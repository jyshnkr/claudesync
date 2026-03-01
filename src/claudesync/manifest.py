"""Manifest tracking — records file hashes + timestamps per remote."""
from __future__ import annotations

import fcntl
import hashlib
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypedDict


class FileEntry(TypedDict):
    """Local or remote file entry: hash + modification time."""
    hash: str
    mtime: float


class _SyncedFileRequired(TypedDict):
    hash: str
    mtime: float


class SyncedFileEntry(_SyncedFileRequired, total=False):
    """Manifest entry after a sync: adds the optional sync timestamp."""
    last_synced: str


# Type aliases for readability
LocalManifest = dict[str, FileEntry]
RemoteManifest = dict[str, FileEntry]

MANIFEST_FILE = Path.home() / ".claudesync" / "manifest.json"
LOCK_FILE = Path.home() / ".claudesync" / "manifest.lock"


@contextmanager
def _manifest_lock():
    """Exclusive file lock for manifest read-modify-write cycles."""
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_FILE.open("w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def compute_file_hash(path: Path) -> str:
    """Compute SHA-256 hash of a file."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest() -> dict[str, Any]:
    """Load manifest from ~/.claudesync/manifest.json."""
    if not MANIFEST_FILE.exists():
        return {}
    try:
        with MANIFEST_FILE.open() as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Manifest file {MANIFEST_FILE} is corrupted ({e}). "
            "Delete or repair it to continue."
        ) from e
    if not isinstance(data, dict):
        raise ValueError(
            f"Manifest file {MANIFEST_FILE} is corrupted (expected object, got {type(data).__name__}). "
            "Delete or repair it to continue."
        )
    return data


def save_manifest(manifest: dict[str, Any]) -> None:
    """Save manifest to ~/.claudesync/manifest.json."""
    MANIFEST_FILE.parent.mkdir(parents=True, exist_ok=True)
    original_mode = MANIFEST_FILE.stat().st_mode if MANIFEST_FILE.exists() else None
    tmp = MANIFEST_FILE.with_suffix(".tmp")
    try:
        with tmp.open("w") as f:
            json.dump(manifest, f, indent=2)
        if original_mode is not None:
            tmp.chmod(original_mode)
        tmp.replace(MANIFEST_FILE)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def build_local_manifest(file_paths: list[str]) -> LocalManifest:
    """
    Build a manifest dict for the given list of file paths.

    Returns: { "<abs_path>": { "hash": "sha256...", "mtime": 1234567890 } }
    """
    result: LocalManifest = {}
    for path_str in file_paths:
        p = Path(path_str)
        if p.exists() and p.is_file():
            result[path_str] = {
                "hash": compute_file_hash(p),
                "mtime": p.stat().st_mtime,
            }
    return result


def update_manifest_for_remote(
    remote_name: str,
    local_manifest: LocalManifest,
) -> None:
    """Update the manifest entries for a remote after a successful sync. Thread-safe via file lock."""
    with _manifest_lock():
        manifest = load_manifest()
        now = datetime.now(timezone.utc).isoformat()

        if remote_name not in manifest:
            manifest[remote_name] = {}

        for path_str, info in local_manifest.items():
            manifest[remote_name][path_str] = {
                "hash": info["hash"],
                "mtime": info["mtime"],
                "last_synced": now,
            }

        save_manifest(manifest)


def get_remote_manifest(remote_name: str) -> dict[str, SyncedFileEntry]:
    """Get the stored manifest for a specific remote."""
    manifest = load_manifest()
    return manifest.get(remote_name, {})
