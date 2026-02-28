"""Manifest tracking — records file hashes + timestamps per remote."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MANIFEST_FILE = Path.home() / ".claudesync" / "manifest.json"


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
    with MANIFEST_FILE.open() as f:
        return json.load(f)


def save_manifest(manifest: dict[str, Any]) -> None:
    """Save manifest to ~/.claudesync/manifest.json."""
    MANIFEST_FILE.parent.mkdir(parents=True, exist_ok=True)
    with MANIFEST_FILE.open("w") as f:
        json.dump(manifest, f, indent=2)


def build_local_manifest(file_paths: list[str]) -> dict[str, dict[str, Any]]:
    """
    Build a manifest dict for the given list of file paths.

    Returns: { "<abs_path>": { "hash": "sha256...", "mtime": 1234567890 } }
    """
    result: dict[str, dict[str, Any]] = {}
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
    local_manifest: dict[str, dict[str, Any]],
) -> None:
    """Update the manifest entries for a remote after a successful sync."""
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


def get_remote_manifest(remote_name: str) -> dict[str, dict[str, Any]]:
    """Get the stored manifest for a specific remote."""
    manifest = load_manifest()
    return manifest.get(remote_name, {})
