"""Rsync engine — builds and runs rsync commands over SSH."""
from __future__ import annotations

import json
import shlex
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from .config import CONFIG_DIR, Remote
from .filters import build_global_filter_args, PROJECT_SYNC_ITEMS

SyncDirection = Literal["push", "pull"]

AGENT_VERSION = "1"
REMOTE_AGENT_PATH = "~/.claudesync/remote_agent.py"


def _get_agent_script_path() -> Path:
    """Return path to the bundled remote_agent.py."""
    return Path(__file__).parent / "remote_agent.py"


class SyncError(Exception):
    pass


@dataclass
class SyncSummary:
    files_transferred: int = 0
    errors: list[str] = field(default_factory=list)


class Engine:
    def __init__(self, remote: Remote) -> None:
        self.remote = remote

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_connection(self) -> bool:
        """Test SSH connectivity to remote. Returns True if reachable."""
        try:
            result = subprocess.run(
                self._ssh_cmd() + ["echo", "ok"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.returncode == 0
        except subprocess.TimeoutExpired:
            return False
        except FileNotFoundError:
            return False

    def push(self, project_paths: list[Path], sanitized_claude_json: Path | None = None, *, include_history: bool = False) -> SyncSummary:
        """Push local context to remote. Returns SyncSummary."""
        return self._sync("push", project_paths, claude_json_path=sanitized_claude_json, include_history=include_history)

    def pull(self, project_paths: list[Path], temp_claude_json_dest: Path | None = None, *, include_history: bool = False) -> SyncSummary:
        """Pull remote context to local. Returns SyncSummary."""
        return self._sync("pull", project_paths, claude_json_path=temp_claude_json_dest, include_history=include_history)

    def _sync(
        self,
        direction: SyncDirection,
        project_paths: list[Path],
        claude_json_path: Path | None = None,
        *,
        include_history: bool = False,
    ) -> SyncSummary:
        """Internal: run rsync for global, per-project, and .claude.json."""
        summary = SyncSummary()

        # Step 1: global ~/.claude/
        result = self._rsync_global(direction=direction, dry_run=False, include_history=include_history)
        summary.files_transferred += _count_transferred(result.stdout)
        if result.returncode != 0:
            summary.errors.append(result.stderr)

        # Step 2: per-project context
        for proj in project_paths:
            res = self._rsync_project(proj, direction=direction, dry_run=False)
            summary.files_transferred += _count_transferred(res.stdout)
            if res.returncode != 0:
                summary.errors.append(res.stderr)

        # Step 3: .claude.json (sanitized on push, temp dest on pull)
        if claude_json_path:
            res = self._rsync_claude_json(claude_json_path, direction=direction, dry_run=False)
            summary.files_transferred += _count_transferred(res.stdout)
            if res.returncode != 0:
                summary.errors.append(res.stderr)

        return summary

    def dry_run(self, project_paths: list[Path], direction: SyncDirection = "push", *, include_history: bool = False) -> str:
        """Run rsync --dry-run, return combined output for display."""
        lines: list[str] = []

        result = self._rsync_global(direction=direction, dry_run=True, include_history=include_history)
        lines.append("=== Global ~/.claude/ ===")
        lines.append(result.stdout)
        if result.returncode != 0:
            lines.append(f"[ERROR] {result.stderr.strip()}")

        for proj in project_paths:
            res = self._rsync_project(proj, direction=direction, dry_run=True)
            lines.append(f"=== Project: {proj} ===")
            lines.append(res.stdout)
            if res.returncode != 0:
                lines.append(f"[ERROR] {res.stderr.strip()}")

        return "\n".join(lines)

    def get_remote_file_hashes(self, file_paths: list[str]) -> dict[str, dict[str, Any]]:
        """
        SSH to remote, compute SHA-256 + mtime for each path via deployed agent.
        Deploys/updates the agent if missing or outdated.
        Returns { path: { hash, mtime } } for files that exist.
        """
        if not file_paths:
            return {}

        try:
            self._ensure_remote_agent()
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            raise SyncError(f"Failed to verify remote agent: {e}") from e

        paths_json = json.dumps(file_paths)
        cmd = self._ssh_cmd() + ["python3", REMOTE_AGENT_PATH, shlex.quote(paths_json)]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired as e:
            raise SyncError(f"SSH timed out getting remote file hashes: {e}") from e
        except FileNotFoundError as e:
            raise SyncError(f"SSH executable not found: {e}") from e
        if result.returncode != 0:
            raise SyncError(f"Remote agent failed: {result.stderr.strip()}")
        try:
            return json.loads(result.stdout.strip())
        except json.JSONDecodeError as e:
            raise SyncError(
                f"Could not parse remote file hashes (SSH banner pollution?): {e}\n"
                f"Raw output: {result.stdout[:200]!r}"
            ) from e

    def _ensure_remote_agent(self) -> None:
        """Deploy remote_agent.py to remote if missing or version-mismatched."""
        version_cmd = self._ssh_cmd() + ["python3", REMOTE_AGENT_PATH, "--version"]
        try:
            result = subprocess.run(version_cmd, capture_output=True, text=True, timeout=10)
            if result.returncode == 0 and result.stdout.strip() == AGENT_VERSION:
                return  # agent present and current
        except subprocess.TimeoutExpired:
            pass  # treat as "agent not present" — fall through to deploy
        except FileNotFoundError as e:
            raise SyncError(f"SSH executable not found: {e}") from e

        # Deploy via rsync
        agent_src = _get_agent_script_path()
        remote_dir = f"{self.remote.address}:{self.remote.remote_home}/.claudesync/"
        deploy_cmd = [
            "rsync", "-az", "-e", " ".join(self._ssh_opt()),
            str(agent_src), remote_dir,
        ]
        try:
            res = subprocess.run(deploy_cmd, capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired as e:
            raise SyncError(
                f"Timed out deploying remote agent to {self.remote.address}: {e}"
            ) from e
        except FileNotFoundError as e:
            raise SyncError(f"SSH executable not found: {e}") from e
        if res.returncode != 0:
            raise SyncError(
                f"Failed to deploy remote agent to {self.remote.address}: {res.stderr.strip()}"
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @property
    def _ssh_base_args(self) -> list[str]:
        """Common SSH options shared by _ssh_cmd and _ssh_opt."""
        known_hosts = str(CONFIG_DIR / "known_hosts")
        return [
            "-i", str(self.remote.ssh_key_path),
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", f"UserKnownHostsFile={known_hosts}",
            "-o", "BatchMode=yes",
        ]

    def _ssh_cmd(self) -> list[str]:
        """SSH command for running remote commands (appends address)."""
        return ["ssh"] + self._ssh_base_args + [self.remote.address]

    def _ssh_opt(self) -> list[str]:
        """SSH options for rsync -e flag (prepends 'ssh')."""
        return ["ssh"] + self._ssh_base_args

    def _base_rsync(self, dry_run: bool = False) -> list[str]:
        ssh_opt = " ".join(self._ssh_opt())
        # --itemize-changes outputs one line per transferred file (>f... or <f...)
        # enabling _count_transferred to give an accurate count
        cmd = ["rsync", "-avz", "--itemize-changes", "-e", ssh_opt]
        if dry_run:
            cmd.append("--dry-run")
        return cmd

    def _rsync_global(self, *, direction: SyncDirection, dry_run: bool, include_history: bool = False) -> subprocess.CompletedProcess:
        """Sync ~/.claude/ directory."""
        local = str(Path.home() / ".claude") + "/"
        remote = f"{self.remote.address}:{self.remote.remote_home}/.claude/"

        filter_args = build_global_filter_args(include_history=include_history)
        cmd = self._base_rsync(dry_run) + filter_args

        if direction == "push":
            # --delete removes files on remote that no longer exist locally
            cmd += ["--delete", local, remote]
        else:
            cmd += [remote, local]

        return subprocess.run(cmd, capture_output=True, text=True, timeout=120)

    def _rsync_project(self, project_path: Path, *, direction: SyncDirection, dry_run: bool) -> subprocess.CompletedProcess:
        """Sync per-project files (.claude/settings.json, CLAUDE.md, .mcp.json)."""
        results: list[subprocess.CompletedProcess] = []
        remote_proj = f"{self.remote.address}:{self.remote.remote_home}/{project_path.name}/"

        for item in PROJECT_SYNC_ITEMS:
            local_item = project_path / item
            remote_item = f"{remote_proj}{item}"

            if direction == "push":
                if not local_item.exists():
                    continue
                cmd = self._base_rsync(dry_run) + [str(local_item), remote_item]
            else:
                cmd = self._base_rsync(dry_run) + [remote_item, str(local_item)]

            res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            results.append(res)

        if not results:
            return _empty_result()

        # Aggregate stdout from all results; collect all failure stderr messages
        combined_stdout = "\n".join(r.stdout for r in results if r.stdout)
        failures = [r for r in results if r.returncode != 0]
        if failures:
            combined_stderr = "\n".join(r.stderr for r in failures if r.stderr)
            return subprocess.CompletedProcess(
                args=[], returncode=failures[0].returncode,
                stdout=combined_stdout, stderr=combined_stderr,
            )
        return subprocess.CompletedProcess(
            args=[], returncode=0, stdout=combined_stdout, stderr="",
        )

    def _rsync_claude_json(
        self,
        local_file: Path,
        direction: SyncDirection,
        dry_run: bool,
    ) -> subprocess.CompletedProcess:
        """Sync .claude.json (sanitized version on push, pulled version on pull)."""
        remote_path = f"{self.remote.address}:{self.remote.remote_home}/.claude.json"

        if direction == "push":
            cmd = self._base_rsync(dry_run) + [str(local_file), remote_path]
        else:
            cmd = self._base_rsync(dry_run) + [remote_path, str(local_file)]

        return subprocess.run(cmd, capture_output=True, text=True, timeout=30)


def _count_transferred(rsync_output: str) -> int:
    """Count files transferred from rsync --itemize-changes output.

    Lines starting with '>f' (sent) or '<f' (received) represent transferred files.
    """
    return sum(
        1 for line in rsync_output.splitlines()
        if line.startswith((">f", "<f"))
    )


def _empty_result() -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
