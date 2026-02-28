"""Tests for include/exclude filter rules."""
import pytest
from pathlib import Path

from claudesync.filters import (
    build_global_filter_args,
    build_project_rsync_items,
    GLOBAL_SYNC_INCLUDES,
    GLOBAL_SYNC_EXCLUDES,
    PROJECT_SYNC_ITEMS,
)


def test_filter_args_include_settings():
    args = build_global_filter_args()
    flat = " ".join(args)
    assert "settings.json" in flat
    assert "history.jsonl" in flat
    assert "projects/" in flat


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


def test_project_rsync_items(tmp_path):
    items = build_project_rsync_items(tmp_path)
    paths = [Path(i) for i in items]
    names = [p.name for p in paths]
    assert "settings.json" in names
    assert "CLAUDE.md" in names
    assert ".mcp.json" in names


def test_global_sync_excludes_debug():
    assert "debug/" in GLOBAL_SYNC_EXCLUDES


def test_global_sync_excludes_cache():
    assert "cache/" in GLOBAL_SYNC_EXCLUDES


def test_global_sync_includes_plugins_installed():
    assert "plugins/installed_plugins.json" in GLOBAL_SYNC_INCLUDES


def test_global_sync_excludes_plugins_cache():
    assert "plugins/cache/" in GLOBAL_SYNC_EXCLUDES
