"""Backup management for conflict resolution."""
from __future__ import annotations

import os
import shutil
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

BACKUP_DIR = Path.home() / ".claudesync" / "backups"


@dataclass
class BackupEntry:
    backup_id: str          # timestamp string, e.g. "20260228T143052"
    original_path: str
    backup_path: Path
    created_at: str


def backup_file(original: Path, keep_count: int = 10) -> Path:
    """
    Backup a file to ~/.claudesync/backups/<timestamp>/<original_path_structure>.
    Returns the path to the backup file.
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    # Strip leading / to make a relative path inside the backup dir
    rel = str(original).lstrip("/")
    dest = BACKUP_DIR / ts / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(original, dest)

    _rotate_backups(keep_count)
    return dest


def list_backups() -> list[BackupEntry]:
    """List all backups sorted newest-first."""
    if not BACKUP_DIR.exists():
        return []

    entries: list[BackupEntry] = []
    for ts_dir in sorted(BACKUP_DIR.iterdir(), reverse=True):
        if not ts_dir.is_dir():
            continue
        for file_path in ts_dir.rglob("*"):
            if file_path.is_file():
                # Reconstruct original path
                rel = str(file_path.relative_to(ts_dir))
                original = "/" + rel
                entries.append(BackupEntry(
                    backup_id=ts_dir.name,
                    original_path=original,
                    backup_path=file_path,
                    created_at=_parse_ts(ts_dir.name),
                ))
    return entries


def restore_backup(backup_id: str, original_path: str | None = None) -> list[Path]:
    """
    Restore files from a backup identified by backup_id.

    If original_path is given, restore only that file.
    Otherwise restore all files in the backup.
    Returns list of restored paths.
    """
    # Validate backup_id is a single safe path segment
    parts = Path(backup_id).parts
    if len(parts) != 1 or parts[0] in (".", "..") or os.sep in backup_id:
        raise ValueError(f"Invalid backup_id: '{backup_id}'")

    ts_dir = BACKUP_DIR / backup_id
    # Guard: backup_id must resolve inside BACKUP_DIR
    if not ts_dir.resolve().is_relative_to(BACKUP_DIR.resolve()):
        raise ValueError(f"Invalid backup id outside backup directory: '{backup_id}'")
    if not ts_dir.is_dir():
        raise ValueError(f"Backup '{backup_id}' not found in {BACKUP_DIR}")

    restored: list[Path] = []

    if original_path:
        rel = original_path.lstrip("/")
        backup_file_path = ts_dir / rel
        # Guard against path traversal in backup archive
        if not backup_file_path.resolve().is_relative_to(ts_dir.resolve()):
            raise ValueError(f"Path traversal detected in original_path: '{original_path}'")
        if not backup_file_path.exists():
            raise FileNotFoundError(f"'{original_path}' not found in backup '{backup_id}'")
        dest = Path(original_path)
        # Guard restore destination to home directory
        if not dest.resolve().is_relative_to(Path.home().resolve()):
            raise ValueError(f"Restore destination outside home directory: '{original_path}'")
        dest.parent.mkdir(parents=True, exist_ok=True)
        _atomic_copy(backup_file_path, dest)
        restored.append(dest)
    else:
        backup_root = ts_dir.resolve()  # already validated above
        home_root = Path.home().resolve()
        for src in ts_dir.rglob("*"):
            if not src.is_file():
                continue
            if not src.resolve().is_relative_to(backup_root):
                raise ValueError(f"Path traversal detected in backup archive: '{src}'")
            rel = str(src.relative_to(ts_dir))
            dest = Path("/" + rel)
            if not dest.resolve().is_relative_to(home_root):
                raise ValueError(f"Restore destination outside home directory: '{dest}'")
            dest.parent.mkdir(parents=True, exist_ok=True)
            _atomic_copy(src, dest)
            restored.append(dest)

    return restored


def _atomic_copy(src: Path, dest: Path) -> None:
    """Copy src to dest atomically via a temp file in the same directory.

    Opens dest.parent with O_NOFOLLOW|O_DIRECTORY, verifies its inode hasn't
    changed since the lstat, then creates the temp file with os.openat against
    the held dirfd. This closes the TOCTOU window between the symlink check
    and the write.
    """
    try:
        parent_lstat = os.lstat(dest.parent)
    except FileNotFoundError:
        raise ValueError(f"Restore destination parent does not exist: '{dest.parent}'")
    if stat.S_ISLNK(parent_lstat.st_mode):
        raise ValueError(f"Restore destination parent is a symlink: '{dest.parent}'")
    if dest.is_symlink():
        raise ValueError(f"Restore destination is a symlink: '{dest}'")

    dirfd = os.open(
        str(dest.parent),
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    tmp: Path | None = None
    try:
        dir_fstat = os.fstat(dirfd)
        if (dir_fstat.st_ino, dir_fstat.st_dev) != (parent_lstat.st_ino, parent_lstat.st_dev):
            raise ValueError(f"Restore destination parent was replaced: '{dest.parent}'")
        tmp_name = f".claudesync_tmp_{os.urandom(8).hex()}"
        tmp = dest.parent / tmp_name
        if hasattr(os, "openat"):
            raw_fd = os.openat(dirfd, tmp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        else:
            # openat unavailable (some platforms/builds): fall back to full path.
            # The dirfd inode check above already ensures dest.parent is authentic.
            raw_fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(raw_fd)
        shutil.copy2(src, tmp)
        os.replace(tmp, dest)
    except BaseException:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
        raise
    finally:
        os.close(dirfd)


def _rotate_backups(keep_count: int) -> None:
    """Remove oldest backups, keeping only keep_count entries."""
    if not BACKUP_DIR.exists():
        return
    ts_dirs = sorted(
        [d for d in BACKUP_DIR.iterdir() if d.is_dir()],
        reverse=True,
    )
    for old_dir in ts_dirs[keep_count:]:
        shutil.rmtree(old_dir, ignore_errors=True)


def _parse_ts(ts: str) -> str:
    """Convert '20260228T143052' to ISO-like readable string."""
    try:
        dt = datetime.strptime(ts, "%Y%m%dT%H%M%S")
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except ValueError:
        return ts
