"""Sanitize ~/.claude.json before syncing — allowlist only safe fields."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

CLAUDE_JSON = Path.home() / ".claude.json"

# Fields explicitly safe to sync — portable preferences only.
# Everything not in this list stays local (auth, account state, tokens).
# Add fields here only when you are certain they contain no secrets.
SAFE_FIELDS = {
    "customApiKeyConfig",      # display name for custom API key (not the key)
    "numStartups",             # startup counter
    "lastSeenAnnouncement",    # dismissed announcement IDs
    "theme",                   # UI theme
    "prefersReducedMotion",    # accessibility preference
    "verbose",                 # CLI verbosity flag
    "alwaysAllowedTools",      # tool permission preferences
    "projects",                # project-level config (non-auth)
    "mcpServers",              # MCP server names/args (not tokens)
}

# Fields known to hold sensitive data — kept for documentation/audit purposes.
# These are NOT used in code (the allowlist above handles exclusion implicitly).
_KNOWN_SENSITIVE = {
    "oauthAccount",
    "userID",
    "cachedGrowthBookFeatures",
    "passesEligibilityCache",
    "primaryApiKey",
    "hasCompletedOnboarding",
}

# Fields whose values may themselves contain sensitive nested keys.
_NESTED_STRIP_FIELDS = {"projects", "mcpServers", "customApiKeyConfig"}

# Sensitive keys that must be stripped when found nested inside SAFE_FIELDS values
# (e.g. env tokens in mcpServers, API keys in projects).
_NESTED_SENSITIVE_KEYS = {
    "env",
    "apiKey",
    "apiToken",
    "token",
    "secret",
    "password",
    "credentials",
}


def _strip_sensitive_nested(obj: Any) -> Any:
    """Recursively remove _NESTED_SENSITIVE_KEYS from dict values.

    Recurses into both dicts and lists; other values are returned unchanged.
    """
    if isinstance(obj, dict):
        return {
            k: _strip_sensitive_nested(v)
            for k, v in obj.items()
            if k not in _NESTED_SENSITIVE_KEYS
        }
    if isinstance(obj, list):
        return [_strip_sensitive_nested(item) for item in obj]
    return obj


def sanitize_claude_json(source: Path = CLAUDE_JSON) -> dict[str, Any]:
    """Read ~/.claude.json and return only fields in SAFE_FIELDS."""
    if not source.exists():
        return {}

    try:
        with source.open() as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Cannot read {source}: invalid JSON ({e}). Fix or delete the file."
        ) from e

    if not isinstance(data, dict):
        raise ValueError(f"Cannot read {source}: expected a JSON object, got {type(data).__name__}.")

    result = {}
    for k, v in data.items():
        if k not in SAFE_FIELDS:
            continue
        if k in _NESTED_STRIP_FIELDS:
            result[k] = _strip_sensitive_nested(v)
        else:
            result[k] = v
    return result


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

    Strategy: start with local file, overlay remote's SAFE_FIELDS values.
    This ensures local auth/account fields are never touched.
    """
    try:
        with pulled_path.open() as f:
            remote_data: dict[str, Any] = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Pulled {pulled_path} contains invalid JSON ({e}). "
            "The remote file may be corrupted."
        ) from e
    if not isinstance(remote_data, dict):
        raise ValueError(
            f"Pulled {pulled_path} must be a JSON object, got {type(remote_data).__name__}."
        )

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
        if not isinstance(local_data, dict):
            raise ValueError(
                f"Local {local_path} must be a JSON object, got {type(local_data).__name__}."
            )

    # Start with local (preserves all auth + any fields we don't know about),
    # then overlay only the safe fields from remote.
    merged = {**local_data}
    for field in SAFE_FIELDS:
        if field in remote_data:
            if field in _NESTED_STRIP_FIELDS:
                merged[field] = _strip_sensitive_nested(remote_data[field])
            else:
                merged[field] = remote_data[field]

    original_mode = local_path.stat().st_mode if local_path.exists() else None
    tmp = local_path.with_suffix(".tmp")
    try:
        with tmp.open("w") as f:
            json.dump(merged, f, indent=2)
        if original_mode is not None:
            tmp.chmod(original_mode)
        tmp.replace(local_path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
