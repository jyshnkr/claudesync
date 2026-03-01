"""Tests for backup management."""
import pytest
from pathlib import Path
from unittest.mock import patch

from claudesync.backup import backup_file, list_backups, restore_backup


@pytest.fixture
def backup_dir(tmp_path, monkeypatch):
    """Redirect BACKUP_DIR to an isolated temp directory and return it."""
    bd = tmp_path / "backups"
    monkeypatch.setattr("claudesync.backup.BACKUP_DIR", bd)
    return bd


def _ts_backup(src: Path, ts: str, **kwargs) -> Path:
    """Call backup_file with a fixed mock timestamp."""
    with patch("claudesync.backup.datetime") as mock_dt:
        mock_dt.now.return_value.strftime.return_value = ts
        return backup_file(src, **kwargs)


def _get_backup_id(dest: Path, backup_dir: Path) -> str:
    """Extract the timestamp directory name (backup_id) from a dest path."""
    return dest.relative_to(backup_dir).parts[0]


def test_backup_file_creates_expected_path_structure(tmp_path, backup_dir):
    src = tmp_path / "settings.json"
    src.write_text("content")

    dest = _ts_backup(src, "20260101T000000")

    assert dest.exists()
    assert dest.is_relative_to(backup_dir)
    assert dest.name == "settings.json"


def test_backup_file_rotates_when_over_limit(tmp_path, backup_dir):
    """Verify rotation removes oldest entries when over keep_count."""
    src = tmp_path / "file.txt"
    src.write_text("data")

    timestamps = [f"2026010{i}T000000" for i in range(5)]
    for ts in timestamps:
        _ts_backup(src, ts, keep_count=3)

    ts_dirs = sorted([d for d in backup_dir.iterdir() if d.is_dir()])
    assert len(ts_dirs) == 3
    # Newest 3 kept: indices 2,3,4
    assert ts_dirs[0].name == "20260102T000000"
    assert ts_dirs[-1].name == "20260104T000000"


def test_backup_file_keeps_exactly_keep_count_entries(tmp_path, backup_dir):
    src = tmp_path / "f.txt"
    src.write_text("x")

    for i in range(10):
        _ts_backup(src, f"202601{i:02d}T000000", keep_count=5)

    ts_dirs = [d for d in backup_dir.iterdir() if d.is_dir()]
    assert len(ts_dirs) == 5


def test_list_backups_returns_newest_first(tmp_path, backup_dir):
    src = tmp_path / "f.txt"
    src.write_text("x")

    _ts_backup(src, "20260101T000000")
    _ts_backup(src, "20260102T000000")

    entries = list_backups()
    assert len(entries) >= 2
    ids = [e.backup_id for e in entries]
    assert ids == sorted(ids, reverse=True)


def test_list_backups_empty_when_no_backup_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("claudesync.backup.BACKUP_DIR", tmp_path / "nonexistent")
    assert list_backups() == []


def test_restore_backup_single_file(tmp_path, backup_dir, monkeypatch):
    # Patch Path.home() so the restore guard accepts tmp_path
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    src = tmp_path / "restore_target.txt"
    src.write_text("original")
    dest = _ts_backup(src, "20260101T120000")
    backup_id = _get_backup_id(dest, backup_dir)

    src.write_text("overwritten")
    restored = restore_backup(backup_id, str(src))

    assert len(restored) == 1
    assert src.read_text() == "original"


def test_restore_backup_all_files(tmp_path, backup_dir):
    src = tmp_path / "all_files.txt"
    src.write_text("data")
    dest = _ts_backup(src, "20260101T130000")
    backup_id = _get_backup_id(dest, backup_dir)

    restored = restore_backup(backup_id)
    assert len(restored) >= 1


def test_restore_backup_raises_on_unknown_backup_id(backup_dir):
    with pytest.raises(ValueError, match="not found"):
        restore_backup("nonexistent_id")


def test_restore_backup_rejects_path_traversal(tmp_path, backup_dir):
    """restore_backup must reject paths that escape the backup directory."""
    ts_dir = backup_dir / "20260101T000000"
    ts_dir.mkdir(parents=True)
    (ts_dir / "innocent.txt").write_text("ok")

    with pytest.raises(ValueError, match="traversal"):
        restore_backup("20260101T000000", "/../../../etc/passwd")
