"""Conflict detection and last-write-wins resolution."""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Literal

WinnerSide = Literal["local", "remote"]

from .backup import backup_file


class FileState(str, Enum):
    UNCHANGED = "unchanged"
    MODIFIED_LOCAL = "modified-locally"
    MODIFIED_REMOTE = "modified-remotely"
    CONFLICT = "conflict"
    LOCAL_ONLY = "local-only"
    REMOTE_ONLY = "remote-only"


@dataclass
class FileConflict:
    path: str
    state: FileState
    local_mtime: float | None
    remote_mtime: float | None
    winner: WinnerSide | None
    backup_path: str | None = None


@dataclass
class ConflictReport:
    conflicts: list[FileConflict]

    @property
    def has_conflicts(self) -> bool:
        return any(c.state == FileState.CONFLICT for c in self.conflicts)

    @property
    def modified_files(self) -> list[FileConflict]:
        return [c for c in self.conflicts if c.state != FileState.UNCHANGED]

    def summary(self) -> str:
        counts = {s: 0 for s in FileState}
        for c in self.conflicts:
            counts[c.state] += 1
        parts = []
        if counts[FileState.CONFLICT]:
            parts.append(f"{counts[FileState.CONFLICT]} conflict(s) resolved")
        if counts[FileState.MODIFIED_LOCAL]:
            parts.append(f"{counts[FileState.MODIFIED_LOCAL]} local change(s)")
        if counts[FileState.MODIFIED_REMOTE]:
            parts.append(f"{counts[FileState.MODIFIED_REMOTE]} remote change(s)")
        return ", ".join(parts) if parts else "no changes"


def detect_conflicts(
    remote_name: str,
    local_manifest: dict[str, dict[str, Any]],
    remote_manifest: dict[str, dict[str, Any]],
    last_sync: dict[str, dict[str, Any]] | None = None,
) -> ConflictReport:
    """
    Compare local and remote file manifests against the last-sync manifest.

    local_manifest:  { path: { hash, mtime } }
    remote_manifest: { path: { hash, mtime } }
    last_sync:       { path: { hash, mtime, last_synced } } — pass explicitly
                     (callers retrieve via get_remote_manifest); if omitted,
                     remote_name is used to load it (backward-compat shim).

    Returns a ConflictReport describing state of each file.
    """
    if last_sync is None:
        from .manifest import get_remote_manifest
        last_sync = get_remote_manifest(remote_name)
    all_paths = set(local_manifest) | set(remote_manifest)
    file_states: list[FileConflict] = []

    for path in all_paths:
        local_info = local_manifest.get(path)
        remote_info = remote_manifest.get(path)
        sync_info = last_sync.get(path)

        local_hash = local_info["hash"] if local_info else None
        remote_hash = remote_info["hash"] if remote_info else None
        synced_hash = sync_info["hash"] if sync_info else None

        local_mtime = local_info["mtime"] if local_info else None
        remote_mtime = remote_info["mtime"] if remote_info else None

        local_changed = local_hash != synced_hash
        remote_changed = remote_hash != synced_hash

        if path not in remote_manifest:
            state = FileState.LOCAL_ONLY
            winner = "local"
        elif path not in local_manifest:
            state = FileState.REMOTE_ONLY
            winner = "remote"
        elif local_hash == remote_hash:
            # Same content on both sides — no conflict regardless of manifest
            state = FileState.UNCHANGED
            winner = None
        elif not local_changed and not remote_changed:
            state = FileState.UNCHANGED
            winner = None
        elif local_changed and not remote_changed:
            state = FileState.MODIFIED_LOCAL
            winner = "local"
        elif not local_changed and remote_changed:
            state = FileState.MODIFIED_REMOTE
            winner = "remote"
        else:
            # Both changed — conflict: last-write-wins by mtime
            state = FileState.CONFLICT
            winner = _resolve_by_mtime(local_mtime, remote_mtime)

        file_states.append(FileConflict(
            path=path,
            state=state,
            local_mtime=local_mtime,
            remote_mtime=remote_mtime,
            winner=winner,
        ))

    return ConflictReport(conflicts=file_states)


def apply_conflict_resolutions(
    report: ConflictReport,
    backup_count: int = 10,
) -> ConflictReport:
    """
    For conflict files where local loses, backup the local file.
    Returns updated report with backup_path populated.
    """
    updated: list[FileConflict] = []
    for fc in report.conflicts:
        if fc.state == FileState.CONFLICT and fc.winner == "remote":
            # Local is the loser — back it up before rsync overwrites it
            local_path = Path(fc.path)
            if local_path.exists():
                try:
                    backup_path = backup_file(local_path, backup_count)
                except Exception as e:
                    raise RuntimeError(
                        f"Failed to backup {fc.path} before sync: {e}. "
                        "Aborting to prevent data loss."
                    ) from e
                fc = dataclasses.replace(fc, backup_path=str(backup_path))
        updated.append(fc)
    return ConflictReport(conflicts=updated)


def _resolve_by_mtime(local_mtime: float | None, remote_mtime: float | None) -> WinnerSide:
    """Return 'local' or 'remote' based on which mtime is newer."""
    if local_mtime is None:
        return "remote"
    if remote_mtime is None:
        return "local"
    return "local" if local_mtime >= remote_mtime else "remote"
