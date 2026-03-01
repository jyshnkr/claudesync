"""Tests for include/exclude filter rules."""
import pytest
from pathlib import Path

from claudesync.filters import (
    build_global_filter_args,
    get_global_sync_includes,
    PROJECT_SYNC_ITEMS,
)


def test_filter_args_include_settings():
    args = build_global_filter_args()
    flat = " ".join(args)
    assert "settings.json" in flat
    assert "projects/" in flat
    # history.jsonl is opt-in — must NOT appear in default filter args
    assert "history.jsonl" not in flat


def test_filter_args_end_with_exclude_all():
    args = build_global_filter_args()
    # Last filter arg should be "- *" to exclude everything else
    assert args[-1] == "- *"
    assert args[-2] == "--filter"


def test_filter_args_alternate_filter_flag():
    args = build_global_filter_args()
    # Every other element starting at 0 should be "--filter"
    flags = args[::2]
    assert all(f == "--filter" for f in flags)


def test_global_sync_includes_plugins_installed():
    assert "plugins/installed_plugins.json" in get_global_sync_includes()


def test_history_excluded_from_global_sync_by_default():
    """history.jsonl must NOT be in sync list when include_history=False."""
    items = get_global_sync_includes(include_history=False)
    assert "history.jsonl" not in items


def test_history_included_when_opted_in():
    """history.jsonl must appear when include_history=True."""
    items = get_global_sync_includes(include_history=True)
    assert "history.jsonl" in items


def test_project_sync_items_contains_expected():
    assert ".claude/settings.json" in PROJECT_SYNC_ITEMS
    assert "CLAUDE.md" in PROJECT_SYNC_ITEMS
    assert ".mcp.json" in PROJECT_SYNC_ITEMS
