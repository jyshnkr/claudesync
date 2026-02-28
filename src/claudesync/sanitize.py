"""Sanitize ~/.claude.json before syncing — strip auth/sensitive fields."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

CLAUDE_JSON = Path.home() / ".claude.json"

# Fields that contain auth/account data — never leave local machine
SENSITIVE_FIELDS = {
    "oauthAccount",
    "userID",
    "cachedGrowthBookFeatures",
    "passesEligibilityCache",
    # session tokens or cached auth state
    "primaryApiKey",
    "hasCompletedOnboarding",  # may contain account info
}


def sanitize_claude_json(source: Path = CLAUDE_JSON) -> dict[str, Any]:
    """Read ~/.claude.json and return sanitized dict (auth fields stripped)."""
    if not source.exists():
        return {}

    try:
        with source.open() as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"Cannot read {source}: invalid JSON ({e}). Fix or delete the file.") from e

    return {k: v for k, v in data.items() if k not in SENSITIVE_FIELDS}


def write_sanitized_temp(source: Path = CLAUDE_JSON) -> Path:
    """Write sanitized ~/.claude.json to a temp file. Returns temp file path."""
    sanitized = sanitize_claude_json(source)

    tmp = tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".json",
        prefix="claude_sanitized_",
        delete=False,
    )
    json.dump(sanitized, tmp, indent=2)
    tmp.close()
    return Path(tmp.name)


def merge_pulled_claude_json(pulled_path: Path, local_path: Path = CLAUDE_JSON) -> None:
    """
    Merge a pulled (sanitized) .claude.json with local auth fields.

    Pulled config has UI prefs + project settings from remote.
    Local config has auth fields that must be preserved.
    Result: remote non-auth fields + local auth fields.
    """
    try:
        with pulled_path.open() as f:
            remote_data: dict[str, Any] = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Pulled {pulled_path} contains invalid JSON ({e}). "
            "The remote file may be corrupted."
        ) from e

    local_data: dict[str, Any] = {}
    if local_path.exists():
        try:
            with local_path.open() as f:
                local_data = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"Local {local_path} contains invalid JSON ({e}). "
                "Fix or delete the file before syncing."
            ) from e

    # Parse both files successfully before writing — then use atomic replace
    merged = {**remote_data}
    for field in SENSITIVE_FIELDS:
        if field in local_data:
            merged[field] = local_data[field]

    tmp = local_path.with_suffix(".tmp")
    try:
        with tmp.open("w") as f:
            json.dump(merged, f, indent=2)
        tmp.replace(local_path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
