"""Tests for conflict detection and last-write-wins resolution."""
import pytest
from pathlib import Path
from unittest.mock import patch

from claudesync.conflicts import (
    FileState,
    detect_conflicts,
    apply_conflict_resolutions,
    _resolve_by_mtime,
    ConflictReport,
    FileConflict,
)


REMOTE = "home"


def _make_manifest(path: str, hash_val: str, mtime: float) -> dict:
    return {path: {"hash": hash_val, "mtime": mtime}}


@pytest.fixture(autouse=True)
def no_last_sync(monkeypatch):
    """Default: no previous sync manifest."""
    monkeypatch.setattr("claudesync.conflicts.get_remote_manifest", lambda name: {})


def test_unchanged_file():
    path = "/home/user/.claude/settings.json"
    local = _make_manifest(path, "abc123", 1000.0)
    remote = _make_manifest(path, "abc123", 1000.0)
    report = detect_conflicts(REMOTE, local, remote)
    assert len(report.conflicts) == 1
    assert report.conflicts[0].state == FileState.UNCHANGED


def test_local_only_file():
    path = "/home/user/.claude/settings.json"
    local = _make_manifest(path, "abc123", 1000.0)
    remote = {}
    report = detect_conflicts(REMOTE, local, remote)
    assert report.conflicts[0].state == FileState.LOCAL_ONLY
    assert report.conflicts[0].winner == "local"


def test_remote_only_file():
    path = "/home/user/.claude/settings.json"
    local = {}
    remote = _make_manifest(path, "abc123", 1000.0)
    report = detect_conflicts(REMOTE, local, remote)
    assert report.conflicts[0].state == FileState.REMOTE_ONLY
    assert report.conflicts[0].winner == "remote"


def test_modified_locally(monkeypatch):
    path = "/some/file"
    synced_hash = "synced"
    monkeypatch.setattr(
        "claudesync.conflicts.get_remote_manifest",
        lambda name: {path: {"hash": synced_hash, "mtime": 900.0, "last_synced": "x"}},
    )
    local = _make_manifest(path, "new-local-hash", 1000.0)
    remote = _make_manifest(path, synced_hash, 900.0)
    report = detect_conflicts(REMOTE, local, remote)
    assert report.conflicts[0].state == FileState.MODIFIED_LOCAL


def test_modified_remotely(monkeypatch):
    path = "/some/file"
    synced_hash = "synced"
    monkeypatch.setattr(
        "claudesync.conflicts.get_remote_manifest",
        lambda name: {path: {"hash": synced_hash, "mtime": 900.0, "last_synced": "x"}},
    )
    local = _make_manifest(path, synced_hash, 900.0)
    remote = _make_manifest(path, "new-remote-hash", 1100.0)
    report = detect_conflicts(REMOTE, local, remote)
    assert report.conflicts[0].state == FileState.MODIFIED_REMOTE


def test_conflict_local_wins_newer_mtime(monkeypatch):
    path = "/some/file"
    monkeypatch.setattr(
        "claudesync.conflicts.get_remote_manifest",
        lambda name: {path: {"hash": "old", "mtime": 500.0, "last_synced": "x"}},
    )
    local = _make_manifest(path, "local-new", 2000.0)
    remote = _make_manifest(path, "remote-new", 1500.0)
    report = detect_conflicts(REMOTE, local, remote)
    fc = report.conflicts[0]
    assert fc.state == FileState.CONFLICT
    assert fc.winner == "local"


def test_conflict_remote_wins_newer_mtime(monkeypatch):
    path = "/some/file"
    monkeypatch.setattr(
        "claudesync.conflicts.get_remote_manifest",
        lambda name: {path: {"hash": "old", "mtime": 500.0, "last_synced": "x"}},
    )
    local = _make_manifest(path, "local-new", 1500.0)
    remote = _make_manifest(path, "remote-new", 2000.0)
    report = detect_conflicts(REMOTE, local, remote)
    fc = report.conflicts[0]
    assert fc.state == FileState.CONFLICT
    assert fc.winner == "remote"


def test_apply_conflict_resolutions_backups_loser(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    path.write_text("local content")

    backup_dir = tmp_path / "backups"
    monkeypatch.setattr("claudesync.backup.BACKUP_DIR", backup_dir)

    fc = FileConflict(
        path=str(path),
        state=FileState.CONFLICT,
        local_mtime=1000.0,
        remote_mtime=2000.0,
        winner="remote",
    )
    report = ConflictReport(conflicts=[fc])
    updated = apply_conflict_resolutions(report, backup_count=5)

    assert updated.conflicts[0].backup_path is not None
    assert Path(updated.conflicts[0].backup_path).exists()


def test_resolve_by_mtime_local_newer():
    assert _resolve_by_mtime(2000.0, 1000.0) == "local"


def test_resolve_by_mtime_remote_newer():
    assert _resolve_by_mtime(1000.0, 2000.0) == "remote"


def test_resolve_by_mtime_equal_prefers_local():
    assert _resolve_by_mtime(1000.0, 1000.0) == "local"


def test_resolve_by_mtime_none_local():
    assert _resolve_by_mtime(None, 1000.0) == "remote"


def test_resolve_by_mtime_none_remote():
    assert _resolve_by_mtime(1000.0, None) == "local"


def test_conflict_report_has_conflicts():
    fc = FileConflict("/f", FileState.CONFLICT, 1.0, 2.0, "remote")
    report = ConflictReport(conflicts=[fc])
    assert report.has_conflicts


def test_conflict_report_no_conflicts():
    fc = FileConflict("/f", FileState.UNCHANGED, 1.0, 1.0, None)
    report = ConflictReport(conflicts=[fc])
    assert not report.has_conflicts
