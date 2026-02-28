"""Include/exclude rules for ClaudeSync rsync transfers."""
from __future__ import annotations

from pathlib import Path

# Paths under ~/.claude/ to include (relative to ~/.claude/)
GLOBAL_SYNC_INCLUDES = [
    "settings.json",
    "history.jsonl",
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


def build_global_filter_args() -> list[str]:
    """Build rsync filter args for global ~/.claude/ sync.

    Only GLOBAL_SYNC_INCLUDES is needed: rsync processes rules top-to-bottom,
    so an explicit include list followed by a single '- *' catch-all is both
    sufficient and safer than maintaining a separate exclude list — any new
    ~/.claude/ subdirectory is automatically excluded unless added to INCLUDES.
    """
    args: list[str] = []

    # Protect included directories so rsync traverses into them
    for item in GLOBAL_SYNC_INCLUDES:
        if item.endswith("/"):
            args += ["--filter", f"+ {item}**"]
        else:
            args += ["--filter", f"+ {item}"]

    # Exclude everything not explicitly included above
    args += ["--filter", "- *"]

    return args


def get_global_include_paths() -> list[str]:
    """Return include paths relative to ~/.claude/ for manifest building."""
    paths = []
    claude_dir = Path.home() / ".claude"
    for item in GLOBAL_SYNC_INCLUDES:
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
