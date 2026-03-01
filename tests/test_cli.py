"""Tests for ClaudeSync CLI commands."""
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from claudesync.cli import app, _local_to_remote_path
from claudesync.config import Config, Remote, SyncSettings
from claudesync.engine import SyncError, SyncSummary


runner = CliRunner()

REMOTE_NAME = "home"


@pytest.fixture
def mock_config():
    """A Config with one remote and no projects."""
    remote = Remote(host="192.168.1.1", user="alice", ssh_key="~/.ssh/id_ed25519",
                    remote_home="/home/alice")
    config = Config(remotes={REMOTE_NAME: remote}, projects=[], sync=SyncSettings())
    return config


@pytest.fixture
def connected_engine():
    """An Engine mock that always reports connection success."""
    engine = MagicMock()
    engine.check_connection.return_value = True
    engine.get_remote_file_hashes.return_value = {}
    engine.push.return_value = SyncSummary(files_transferred=1, errors=[])
    engine.pull.return_value = SyncSummary(files_transferred=0, errors=[])
    return engine


# ---------------------------------------------------------------------------
# push
# ---------------------------------------------------------------------------

def test_push_updates_manifest_after_successful_sync(mock_config, connected_engine, tmp_path):
    sanitized = tmp_path / "sanitized.json"
    sanitized.write_text("{}")

    with patch("claudesync.cli.load_config", return_value=mock_config), \
         patch("claudesync.cli.Engine", return_value=connected_engine), \
         patch("claudesync.cli.get_global_include_paths", return_value=[]), \
         patch("claudesync.cli.build_local_manifest", return_value={}), \
         patch("claudesync.cli.get_remote_manifest", return_value={}), \
         patch("claudesync.cli.update_manifest_for_remote") as mock_update, \
         patch("claudesync.cli.write_sanitized_temp", return_value=sanitized):
        result = runner.invoke(app, ["push", REMOTE_NAME])

    assert result.exit_code == 0
    mock_update.assert_called_once()


def test_push_skips_manifest_update_on_errors(mock_config, connected_engine, tmp_path):
    connected_engine.push.return_value = SyncSummary(files_transferred=0, errors=["rsync failed"])

    sanitized = tmp_path / "sanitized.json"
    sanitized.write_text("{}")

    with patch("claudesync.cli.load_config", return_value=mock_config), \
         patch("claudesync.cli.Engine", return_value=connected_engine), \
         patch("claudesync.cli.get_global_include_paths", return_value=[]), \
         patch("claudesync.cli.build_local_manifest", return_value={}), \
         patch("claudesync.cli.get_remote_manifest", return_value={}), \
         patch("claudesync.cli.update_manifest_for_remote") as mock_update, \
         patch("claudesync.cli.write_sanitized_temp", return_value=sanitized):
        result = runner.invoke(app, ["push", REMOTE_NAME])

    assert result.exit_code == 0
    mock_update.assert_not_called()


def test_push_cleans_up_sanitized_temp_on_engine_exception(mock_config, connected_engine):
    tmp_file = MagicMock(spec=Path)
    connected_engine.push.side_effect = RuntimeError("rsync crashed")

    with patch("claudesync.cli.load_config", return_value=mock_config), \
         patch("claudesync.cli.Engine", return_value=connected_engine), \
         patch("claudesync.cli.get_global_include_paths", return_value=[]), \
         patch("claudesync.cli.build_local_manifest", return_value={}), \
         patch("claudesync.cli.get_remote_manifest", return_value={}), \
         patch("claudesync.cli.write_sanitized_temp", return_value=tmp_file):
        result = runner.invoke(app, ["push", REMOTE_NAME])

    assert result.exit_code != 0
    # Even on exception, unlink should have been called
    tmp_file.unlink.assert_called_once()


# ---------------------------------------------------------------------------
# pull
# ---------------------------------------------------------------------------

def test_pull_skips_merge_on_empty_remote_claude_json(mock_config, connected_engine, tmp_path):
    empty_tmp = tmp_path / "empty.json"
    empty_tmp.write_text("")  # zero bytes — merge should be skipped

    with patch("claudesync.cli.load_config", return_value=mock_config), \
         patch("claudesync.cli.Engine", return_value=connected_engine), \
         patch("claudesync.cli.get_global_include_paths", return_value=[]), \
         patch("claudesync.cli.build_local_manifest", return_value={}), \
         patch("claudesync.cli.get_remote_manifest", return_value={}), \
         patch("claudesync.cli.update_manifest_for_remote"), \
         patch("claudesync.cli.merge_pulled_claude_json") as mock_merge, \
         patch("tempfile.NamedTemporaryFile") as mock_ntf:
        mock_ntf.return_value.__enter__.return_value.name = str(empty_tmp)
        result = runner.invoke(app, ["pull", REMOTE_NAME])

    assert result.exit_code == 0
    mock_merge.assert_not_called()


def test_pull_merges_when_remote_claude_json_nonempty(mock_config, connected_engine, tmp_path):
    nonempty_tmp = tmp_path / "nonempty.json"
    nonempty_tmp.write_text('{"key": "value"}')

    with patch("claudesync.cli.load_config", return_value=mock_config), \
         patch("claudesync.cli.Engine", return_value=connected_engine), \
         patch("claudesync.cli.get_global_include_paths", return_value=[]), \
         patch("claudesync.cli.build_local_manifest", return_value={}), \
         patch("claudesync.cli.get_remote_manifest", return_value={}), \
         patch("claudesync.cli.update_manifest_for_remote"), \
         patch("claudesync.cli.merge_pulled_claude_json") as mock_merge, \
         patch("tempfile.NamedTemporaryFile") as mock_ntf:
        mock_ntf.return_value.__enter__.return_value.name = str(nonempty_tmp)
        result = runner.invoke(app, ["pull", REMOTE_NAME])

    assert result.exit_code == 0
    mock_merge.assert_called_once()


# ---------------------------------------------------------------------------
# remote add
# ---------------------------------------------------------------------------

def test_remote_add_rejects_missing_at_symbol(mock_config):
    with patch("claudesync.cli.load_config", return_value=mock_config):
        result = runner.invoke(app, ["remote", "add", "work", "nousernamehost"])

    assert result.exit_code != 0
    assert "user@host" in result.output


# ---------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------

def test_diff_includes_project_files(mock_config, connected_engine, tmp_path):
    """diff should discover per-project files, not just global ones."""
    proj = tmp_path / "MyProject"
    proj.mkdir()
    claude_md = proj / "CLAUDE.md"
    claude_md.write_text("# My project")

    mock_config.projects = [str(proj)]

    with patch("claudesync.cli.load_config", return_value=mock_config), \
         patch("claudesync.cli.Engine", return_value=connected_engine), \
         patch("claudesync.cli.get_global_include_paths", return_value=[]), \
         patch("claudesync.cli.get_remote_manifest", return_value={}), \
         patch("claudesync.cli.build_local_manifest", return_value={}) as mock_build:
        result = runner.invoke(app, ["diff", REMOTE_NAME])

    assert result.exit_code == 0
    # build_local_manifest should have received CLAUDE.md in the file list
    call_args = mock_build.call_args[0][0]
    assert str(claude_md) in call_args


# ---------------------------------------------------------------------------
# _local_to_remote_path
# ---------------------------------------------------------------------------

def test_local_to_remote_path_project_file(tmp_path):
    """A path inside a registered project is mapped under remote_home/proj_name/."""
    remote = Remote(host="h", user="u", ssh_key="~/.ssh/id_ed25519", remote_home="/home/u")
    proj = tmp_path / "MyProject"
    local_path = str(proj / "CLAUDE.md")

    result = _local_to_remote_path(local_path, [proj], remote)

    assert result == f"/home/u/MyProject/CLAUDE.md"


def test_local_to_remote_path_home_relative(tmp_path, monkeypatch):
    """A path under home (but not inside a project) is mapped under remote_home/."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    remote = Remote(host="h", user="u", ssh_key="~/.ssh/id_ed25519", remote_home="/home/u")
    local_path = str(tmp_path / ".claude" / "settings.json")

    result = _local_to_remote_path(local_path, [], remote)

    assert result == "/home/u/.claude/settings.json"


def test_local_to_remote_path_unrelated_returned_unchanged(tmp_path, monkeypatch):
    """A path not under any project or home is returned unchanged."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    remote = Remote(host="h", user="u", ssh_key="~/.ssh/id_ed25519", remote_home="/home/u")
    local_path = "/etc/passwd"

    result = _local_to_remote_path(local_path, [], remote)

    assert result == "/etc/passwd"


def test_pull_rebuilds_manifest_after_sync(mock_config, connected_engine, tmp_path):
    """After a successful pull, manifest should be rebuilt from post-sync local state."""
    with patch("claudesync.cli.load_config", return_value=mock_config), \
         patch("claudesync.cli.Engine", return_value=connected_engine), \
         patch("claudesync.cli.get_global_include_paths", return_value=[]), \
         patch("claudesync.cli.build_local_manifest", return_value={}) as mock_build, \
         patch("claudesync.cli.get_remote_manifest", return_value={}), \
         patch("claudesync.cli.update_manifest_for_remote") as mock_update, \
         patch("claudesync.cli.merge_pulled_claude_json"), \
         patch("tempfile.NamedTemporaryFile") as mock_ntf:
        tmp_file = tmp_path / "empty.json"
        tmp_file.write_text("")
        mock_ntf.return_value.__enter__.return_value.name = str(tmp_file)

        result = runner.invoke(app, ["pull", REMOTE_NAME])

    assert result.exit_code == 0
    # build_local_manifest is called twice: once for _build_manifests, once for rebuild
    assert mock_build.call_count == 2
    mock_update.assert_called_once()
