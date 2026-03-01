"""Tests for rsync engine — command construction (mock subprocess)."""
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

from claudesync.config import Remote
from claudesync.engine import Engine, SyncError, _count_transferred, _empty_result


@pytest.fixture
def remote():
    return Remote(host="192.168.1.100", user="alice", ssh_key="~/.ssh/id_ed25519", remote_home="/home/alice")


@pytest.fixture
def engine(remote):
    return Engine(remote)


def test_ssh_cmd_includes_key(engine, remote):
    cmd = engine._ssh_cmd()
    assert "-i" in cmd
    key_idx = cmd.index("-i")
    assert "id_ed25519" in cmd[key_idx + 1]


def test_ssh_cmd_includes_address(engine, remote):
    cmd = engine._ssh_cmd()
    assert "alice@192.168.1.100" in cmd


def test_base_rsync_no_dry_run(engine):
    cmd = engine._base_rsync(dry_run=False)
    assert "rsync" in cmd
    assert "-avz" in cmd
    assert "--dry-run" not in cmd


def test_base_rsync_with_dry_run(engine):
    cmd = engine._base_rsync(dry_run=True)
    assert "--dry-run" in cmd


def test_check_connection_success(engine):
    mock_result = MagicMock()
    mock_result.returncode = 0
    with patch("claudesync.engine.subprocess.run", return_value=mock_result):
        assert engine.check_connection() is True


def test_check_connection_failure(engine):
    mock_result = MagicMock()
    mock_result.returncode = 1
    with patch("claudesync.engine.subprocess.run", return_value=mock_result):
        assert engine.check_connection() is False


def test_push_calls_rsync(engine, tmp_path):
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "sending file.txt\n"
    mock_result.stderr = ""

    sanitized = tmp_path / "sanitized.json"
    sanitized.write_text("{}")

    with patch("claudesync.engine.subprocess.run", return_value=mock_result) as mock_run:
        summary = engine.push([], sanitized_claude_json=sanitized)

    assert mock_run.called
    assert summary.errors == []


def test_pull_calls_rsync(engine, tmp_path):
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = ""
    mock_result.stderr = ""

    with patch("claudesync.engine.subprocess.run", return_value=mock_result) as mock_run:
        summary = engine.pull([])

    assert mock_run.called
    assert summary.errors == []


def test_dry_run_returns_output(engine):
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "would transfer file.txt"
    mock_result.stderr = ""

    with patch("claudesync.engine.subprocess.run", return_value=mock_result):
        output = engine.dry_run([])

    assert "Global" in output


def test_get_remote_file_hashes_success(engine):
    expected = {"/home/alice/.claude/settings.json": {"hash": "abc", "mtime": 1000.0}}
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = json.dumps(expected)

    with patch("claudesync.engine.subprocess.run", return_value=mock_result):
        result = engine.get_remote_file_hashes(["/home/alice/.claude/settings.json"])

    assert result == expected


def test_get_remote_file_hashes_raises_on_ssh_failure(engine):
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = ""
    mock_result.stderr = "ssh: connect to host"

    with patch("claudesync.engine.subprocess.run", return_value=mock_result):
        with pytest.raises(SyncError, match="SSH command failed"):
            engine.get_remote_file_hashes(["/some/file"])


def test_get_remote_file_hashes_empty_list(engine):
    # Should not call subprocess for empty list
    with patch("claudesync.engine.subprocess.run") as mock_run:
        result = engine.get_remote_file_hashes([])
    assert result == {}
    mock_run.assert_not_called()


def test_count_transferred_counts_paths():
    # --itemize-changes lines: >f = sent, <f = received
    output = ">f+++++++++ settings.json\n>f+++++++++ history.jsonl\nsent 1234 bytes\n"
    count = _count_transferred(output)
    assert count == 2


def test_count_transferred_empty():
    assert _count_transferred("") == 0


def test_empty_result():
    r = _empty_result()
    assert r.returncode == 0
    assert r.stdout == ""


def test_push_with_project_paths_calls_rsync_per_project(engine, tmp_path):
    """Engine should call rsync once per PROJECT_SYNC_ITEM per project."""
    proj = tmp_path / "MyProject"
    proj.mkdir()
    (proj / "CLAUDE.md").write_text("# project")

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = ""
    mock_result.stderr = ""

    with patch("claudesync.engine.subprocess.run", return_value=mock_result) as mock_run:
        engine.push([proj])

    # At minimum: 1 global call + >=1 project call
    assert mock_run.call_count >= 2


def test_get_remote_file_hashes_raises_on_invalid_json(engine):
    """If SSH stdout is not JSON (e.g. login banner), SyncError is raised."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "Welcome to server!\nLast login: ..."

    with patch("claudesync.engine.subprocess.run", return_value=mock_result):
        with pytest.raises(SyncError, match="parse"):
            engine.get_remote_file_hashes(["/some/file"])


def test_rsync_project_aggregates_all_failures(engine, tmp_path):
    """All per-item rsync failures are aggregated, not just the first."""
    proj = tmp_path / "Proj"
    proj.mkdir()
    # Create all three items so all rsync calls are attempted on push
    (proj / ".claude").mkdir()
    (proj / ".claude" / "settings.json").write_text("{}")
    (proj / "CLAUDE.md").write_text("# x")
    (proj / ".mcp.json").write_text("{}")

    fail_result = MagicMock()
    fail_result.returncode = 1
    fail_result.stdout = ""
    fail_result.stderr = "error"

    with patch("claudesync.engine.subprocess.run", return_value=fail_result):
        combined = engine._rsync_project(proj, direction="push", dry_run=False)

    assert combined.returncode != 0
    # Combined stderr should contain errors from all 3 items
    assert combined.stderr.count("error") >= 2


def test_check_connection_handles_timeout(engine):
    """TimeoutExpired during SSH connection check returns False."""
    import subprocess
    with patch("claudesync.engine.subprocess.run",
               side_effect=subprocess.TimeoutExpired(cmd="ssh", timeout=10)):
        assert engine.check_connection() is False
