"""Rsync engine — builds and runs rsync commands over SSH."""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .config import Remote
from .filters import build_global_filter_args, PROJECT_SYNC_ITEMS


class SyncError(Exception):
    pass


class Engine:
    def __init__(self, remote: Remote) -> None:
        self.remote = remote

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_connection(self) -> bool:
        """Test SSH connectivity to remote. Returns True if reachable."""
        result = subprocess.run(
            self._ssh_cmd() + ["echo", "ok"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0

    def push(self, project_paths: list[Path], sanitized_claude_json: Path | None = None) -> dict[str, Any]:
        """Push local context to remote. Returns summary dict."""
        summary: dict[str, Any] = {"files_transferred": 0, "errors": []}

        # Phase 1: global ~/.claude/
        result = self._rsync_global(direction="push", dry_run=False)
        summary["files_transferred"] += _count_transferred(result.stdout)
        if result.returncode != 0:
            summary["errors"].append(result.stderr)

        # Phase 2: per-project context
        for proj in project_paths:
            res = self._rsync_project(proj, direction="push", dry_run=False)
            summary["files_transferred"] += _count_transferred(res.stdout)
            if res.returncode != 0:
                summary["errors"].append(res.stderr)

        # Phase 3: sanitized .claude.json
        if sanitized_claude_json:
            res = self._rsync_claude_json(sanitized_claude_json, direction="push", dry_run=False)
            if res.returncode != 0:
                summary["errors"].append(res.stderr)

        return summary

    def pull(self, project_paths: list[Path], temp_claude_json_dest: Path | None = None) -> dict[str, Any]:
        """Pull remote context to local. Returns summary dict."""
        summary: dict[str, Any] = {"files_transferred": 0, "errors": []}

        # Phase 1: global ~/.claude/
        result = self._rsync_global(direction="pull", dry_run=False)
        summary["files_transferred"] += _count_transferred(result.stdout)
        if result.returncode != 0:
            summary["errors"].append(result.stderr)

        # Phase 2: per-project context
        for proj in project_paths:
            res = self._rsync_project(proj, direction="pull", dry_run=False)
            summary["files_transferred"] += _count_transferred(res.stdout)
            if res.returncode != 0:
                summary["errors"].append(res.stderr)

        # Phase 3: pull .claude.json to a temp location for merging
        if temp_claude_json_dest:
            res = self._rsync_claude_json(temp_claude_json_dest, direction="pull", dry_run=False)
            if res.returncode != 0:
                summary["errors"].append(res.stderr)

        return summary

    def dry_run(self, project_paths: list[Path], direction: str = "push") -> str:
        """Run rsync --dry-run, return combined output for display."""
        lines: list[str] = []

        result = self._rsync_global(direction=direction, dry_run=True)
        lines.append("=== Global ~/.claude/ ===")
        lines.append(result.stdout)

        for proj in project_paths:
            res = self._rsync_project(proj, direction=direction, dry_run=True)
            lines.append(f"=== Project: {proj} ===")
            lines.append(res.stdout)

        return "\n".join(lines)

    def get_remote_file_hashes(self, file_paths: list[str]) -> dict[str, dict[str, Any]]:
        """
        SSH to remote and compute SHA-256 + mtime for each path.
        Returns { path: { hash, mtime } } for files that exist.
        """
        if not file_paths:
            return {}

        # Build a small Python one-liner to run on remote
        paths_json = json.dumps(file_paths)
        script = (
            "import json, hashlib, os, sys; "
            "paths = json.loads(sys.argv[1]); "
            "result = {}; "
            "[result.update({p: {'hash': hashlib.sha256(open(p,'rb').read()).hexdigest(), 'mtime': os.stat(p).st_mtime}}) "
            " for p in paths if os.path.isfile(p)]; "
            "print(json.dumps(result))"
        )
        cmd = self._ssh_cmd() + ["python3", "-c", script, paths_json]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return {}
        try:
            return json.loads(result.stdout.strip())
        except json.JSONDecodeError:
            return {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ssh_cmd(self) -> list[str]:
        return [
            "ssh",
            "-i", str(self.remote.ssh_key_path),
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "BatchMode=yes",
            self.remote.address,
        ]

    def _ssh_opt(self) -> list[str]:
        """SSH options for rsync -e flag."""
        return [
            "ssh",
            "-i", str(self.remote.ssh_key_path),
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "BatchMode=yes",
        ]

    def _base_rsync(self, dry_run: bool = False) -> list[str]:
        ssh_opt = " ".join(self._ssh_opt())
        cmd = ["rsync", "-avz", "--delete", "-e", ssh_opt]
        if dry_run:
            cmd.append("--dry-run")
        return cmd

    def _rsync_global(self, direction: str, dry_run: bool) -> subprocess.CompletedProcess:
        """Sync ~/.claude/ directory."""
        local = str(Path.home() / ".claude") + "/"
        remote = f"{self.remote.address}:{self.remote.remote_home}/.claude/"

        filter_args = build_global_filter_args()
        cmd = self._base_rsync(dry_run) + filter_args

        if direction == "push":
            cmd += [local, remote]
        else:
            cmd += [remote, local]

        return subprocess.run(cmd, capture_output=True, text=True, timeout=120)

    def _rsync_project(self, project_path: Path, direction: str, dry_run: bool) -> subprocess.CompletedProcess:
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

        # Return a combined result (last one, or first failure)
        failures = [r for r in results if r.returncode != 0]
        return failures[0] if failures else (results[-1] if results else _empty_result())

    def _rsync_claude_json(
        self,
        local_file: Path,
        direction: str,
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
    """Count files transferred from rsync output."""
    count = 0
    for line in rsync_output.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith(("sending", "sent", "total", "receiving", "received", ">f", ".")):
            if "/" in stripped or "." in stripped:
                count += 1
    return count


def _empty_result() -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
