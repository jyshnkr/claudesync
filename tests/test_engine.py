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
    with patch("claudesync.engine.subprocess.run", side_effect=[
        MagicMock(returncode=0, stdout="1\n", stderr=""),           # version check
        MagicMock(returncode=0, stdout=json.dumps(expected), stderr=""),  # hash fetch
    ]):
        result = engine.get_remote_file_hashes(["/home/alice/.claude/settings.json"])

    assert result == expected


def test_get_remote_file_hashes_raises_on_ssh_failure(engine):
    with patch("claudesync.engine.subprocess.run", side_effect=[
        MagicMock(returncode=0, stdout="1\n", stderr=""),   # version check OK
        MagicMock(returncode=1, stdout="", stderr="ssh: connect to host"),  # hash fetch fails
    ]):
        with pytest.raises(SyncError, match="Remote agent failed"):
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
    with patch("claudesync.engine.subprocess.run", side_effect=[
        MagicMock(returncode=0, stdout="1\n", stderr=""),   # version check OK
        MagicMock(returncode=0, stdout="Welcome to server!\nLast login: ...", stderr=""),
    ]):
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
    assert combined.stderr.count("error") == 3


def test_check_connection_handles_timeout(engine):
    """TimeoutExpired during SSH connection check returns False."""
    import subprocess
    with patch("claudesync.engine.subprocess.run",
               side_effect=subprocess.TimeoutExpired(cmd="ssh", timeout=10)):
        assert engine.check_connection() is False


def test_get_remote_file_hashes_raises_on_missing_ssh(engine):
    """FileNotFoundError (missing ssh binary) is wrapped as SyncError."""
    with patch("claudesync.engine.subprocess.run",
               side_effect=FileNotFoundError("No such file: ssh")):
        with pytest.raises(SyncError):
            engine.get_remote_file_hashes(["/some/file"])


@pytest.fixture
def mock_run():
    with patch("claudesync.engine.subprocess.run") as m:
        yield m


def test_ensure_remote_agent_deploys_on_first_use(engine, mock_run):
    """Engine must deploy remote_agent.py if not present on remote."""
    mock_run.side_effect = [
        MagicMock(returncode=2, stdout="", stderr=""),   # version check fails
        MagicMock(returncode=0, stdout="", stderr=""),   # rsync deploy
        MagicMock(returncode=0, stdout="{}", stderr=""), # hash fetch (3 calls total)
    ]
    result = engine.get_remote_file_hashes(["/some/file"])
    assert result == {}
    # Verify rsync was called (deploy step)
    calls = [str(c) for c in mock_run.call_args_list]
    assert any("rsync" in c for c in calls)


def test_ensure_remote_agent_not_redeployed_if_current(engine, mock_run):
    """Engine must not redeploy agent if version matches."""
    mock_run.side_effect = [
        MagicMock(returncode=0, stdout="1\n", stderr=""),  # version check OK
        MagicMock(returncode=0, stdout="{}", stderr=""),    # hash fetch
    ]
    result = engine.get_remote_file_hashes([])
    # Only 2 calls would happen if not empty; but empty list returns early
    # Test with a non-empty list:
    mock_run.reset_mock()
    mock_run.side_effect = [
        MagicMock(returncode=0, stdout="1\n", stderr=""),  # version check OK
        MagicMock(returncode=0, stdout="{}", stderr=""),    # hash fetch
    ]
    result = engine.get_remote_file_hashes(["/some/file"])
    assert mock_run.call_count == 2


def test_ensure_remote_agent_handles_version_check_timeout(engine, mock_run):
    """TimeoutExpired during version check must be treated as 'agent not present' and trigger deploy."""
    import subprocess
    mock_run.side_effect = [
        subprocess.TimeoutExpired(cmd="ssh", timeout=10),  # version check times out
        MagicMock(returncode=0, stdout="", stderr=""),      # rsync deploy succeeds
        MagicMock(returncode=0, stdout="{}", stderr=""),    # hash fetch
    ]
    result = engine.get_remote_file_hashes(["/some/file"])
    assert result == {}
    # Verify that rsync (deploy) was called after the timeout
    calls = [str(c) for c in mock_run.call_args_list]
    assert any("rsync" in c for c in calls), "Agent must be deployed after version check timeout"


def test_ensure_remote_agent_handles_deploy_timeout(engine, mock_run):
    """TimeoutExpired during rsync deploy must raise SyncError."""
    import subprocess
    mock_run.side_effect = [
        MagicMock(returncode=2, stdout="", stderr=""),          # version check fails → needs deploy
        subprocess.TimeoutExpired(cmd="rsync", timeout=30),    # deploy times out
    ]
    with pytest.raises(SyncError):
        engine.get_remote_file_hashes(["/some/file"])


def test_ssh_uses_dedicated_known_hosts_file(engine):
    """SSH must use a project-local known_hosts, not pollute ~/.ssh/known_hosts."""
    cmd = engine._ssh_cmd()
    assert any("UserKnownHostsFile" in arg for arg in cmd)
    # StrictHostKeyChecking must NOT be 'no' (insecure — accepts any key forever)
    for i, arg in enumerate(cmd):
        if arg == "-o" and i + 1 < len(cmd):
            if cmd[i + 1].startswith("StrictHostKeyChecking="):
                assert cmd[i + 1] != "StrictHostKeyChecking=no"
