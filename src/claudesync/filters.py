"""Include/exclude rules for ClaudeSync rsync transfers."""
from __future__ import annotations

from pathlib import Path

# Base items synced regardless of settings
_GLOBAL_SYNC_BASE = [
    "settings.json",
    "projects/",
    "tasks/",
    "plans/",
    "session-env/",
    "plugins/installed_plugins.json",
    "plugins/blocklist.json",
]

# Per-project files/dirs to sync (relative to project root)
PROJECT_SYNC_ITEMS = [
    ".claude/settings.json",
    "CLAUDE.md",
    ".mcp.json",
]


def get_global_sync_includes(include_history: bool = False) -> list[str]:
    """Return the list of items to sync under ~/.claude/.

    history.jsonl is opt-in: it contains full conversation history
    including pasted code, API keys, and internal project data.
    """
    items = list(_GLOBAL_SYNC_BASE)
    if include_history:
        items.insert(1, "history.jsonl")  # keep ordering sensible
    return items


def build_global_filter_args(include_history: bool = False) -> list[str]:
    """Build rsync filter args for global ~/.claude/ sync."""
    args: list[str] = []
    for item in get_global_sync_includes(include_history=include_history):
        if item.endswith("/"):
            args += ["--filter", f"+ {item}**"]
        else:
            args += ["--filter", f"+ {item}"]
    # Exclude everything not explicitly included above
    args += ["--filter", "- *"]
    return args


def get_global_include_paths(include_history: bool = False) -> list[str]:
    """Return include paths relative to ~/.claude/ for manifest building."""
    paths = []
    claude_dir = Path.home() / ".claude"
    for item in get_global_sync_includes(include_history=include_history):
        full = claude_dir / item
        if full.exists():
            if full.is_dir():
                for p in full.rglob("*"):
                    if p.is_file():
                        paths.append(str(p))
            else:
                paths.append(str(full))
    # Also include ~/.claude.json (handled separately via sanitize)
    claude_json = Path.home() / ".claude.json"
    if claude_json.exists():
        paths.append(str(claude_json))
    return paths
