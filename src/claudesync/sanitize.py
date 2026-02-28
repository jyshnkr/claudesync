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

    with source.open() as f:
        data = json.load(f)

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
    with pulled_path.open() as f:
        remote_data: dict[str, Any] = json.load(f)

    local_data: dict[str, Any] = {}
    if local_path.exists():
        with local_path.open() as f:
            local_data = json.load(f)

    # Start with remote (has updated prefs), overlay local sensitive fields
    merged = {**remote_data}
    for field in SENSITIVE_FIELDS:
        if field in local_data:
            merged[field] = local_data[field]

    with local_path.open("w") as f:
        json.dump(merged, f, indent=2)
