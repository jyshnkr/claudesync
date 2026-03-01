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


def test_conflict_report_shows_human_readable_winner(capsys):
    from claudesync.cli import _print_conflict_report
    from claudesync.conflicts import ConflictReport, FileConflict, FileState
    import time

    now = time.time()
    three_days_ago = now - (3 * 86400)
    two_hours_ago = now - (2 * 3600)

    report = ConflictReport(conflicts=[
        FileConflict(
            path="/Users/jay/.claude/settings.json",
            state=FileState.CONFLICT,
            local_mtime=three_days_ago,
            remote_mtime=two_hours_ago,
            winner="remote",
            backup_path="/Users/jay/.claudesync/backups/20260228T143052/settings.json",
        )
    ])
    _print_conflict_report(report)
    captured = capsys.readouterr()
    assert "LOST" in captured.out or "lost" in captured.out.lower()
    assert "WON" in captured.out or "won" in captured.out.lower()
    # Should show relative time, not raw epoch
    assert "days ago" in captured.out or "hours ago" in captured.out


@pytest.fixture
def mock_engine_class():
    with patch("claudesync.cli.Engine") as mock:
        yield mock


def test_pair_command_adds_remote_tests_connection_and_pushes(mock_config, mock_engine_class, tmp_path):
    """pair must add remote, verify SSH, and run initial push."""
    sanitized = tmp_path / "sanitized.json"
    sanitized.write_text("{}")

    mock_engine_class.return_value.check_connection.return_value = True
    mock_engine_class.return_value._ssh_cmd.return_value = ["ssh", "alice@192.168.1.10"]
    mock_engine_class.return_value.get_remote_file_hashes.return_value = {}
    mock_engine_class.return_value.push.return_value = SyncSummary(files_transferred=3)

    with patch("claudesync.cli.load_config", return_value=mock_config), \
         patch("claudesync.cli.save_config"), \
         patch("claudesync.cli.get_remote_manifest", return_value={}), \
         patch("claudesync.cli.build_local_manifest", return_value={}), \
         patch("claudesync.cli.get_global_include_paths", return_value=[]), \
         patch("claudesync.cli.update_manifest_for_remote"), \
         patch("claudesync.cli.write_sanitized_temp", return_value=sanitized), \
         patch("claudesync.cli.subprocess") as mock_sub:
        mock_sub.run.return_value = MagicMock(returncode=0, stdout="/home/alice\n")
        result = runner.invoke(app, [
            "pair",
            "--name", "studio",
            "--address", "alice@192.168.1.10",
            "--key", "~/.ssh/id_ed25519",
        ])

    assert result.exit_code == 0
    assert "studio" in result.output
    assert "paired" in result.output.lower() or "push" in result.output.lower()


def test_pair_does_not_celebrate_on_push_errors(mock_config, mock_engine_class, tmp_path):
    """pair must NOT print 'Paired!' when the push returned errors."""
    sanitized = tmp_path / "sanitized.json"
    sanitized.write_text("{}")

    mock_engine_class.return_value.check_connection.return_value = True
    mock_engine_class.return_value._ssh_cmd.return_value = ["ssh", "alice@192.168.1.10"]
    mock_engine_class.return_value.get_remote_file_hashes.return_value = {}
    # Push returns errors
    mock_engine_class.return_value.push.return_value = SyncSummary(
        files_transferred=0, errors=["rsync: connection reset"]
    )

    with patch("claudesync.cli.load_config", return_value=mock_config), \
         patch("claudesync.cli.save_config"), \
         patch("claudesync.cli.get_remote_manifest", return_value={}), \
         patch("claudesync.cli.build_local_manifest", return_value={}), \
         patch("claudesync.cli.get_global_include_paths", return_value=[]), \
         patch("claudesync.cli.update_manifest_for_remote"), \
         patch("claudesync.cli.write_sanitized_temp", return_value=sanitized), \
         patch("claudesync.cli.subprocess") as mock_sub:
        mock_sub.run.return_value = MagicMock(returncode=0, stdout="/home/alice\n")
        result = runner.invoke(app, [
            "pair",
            "--name", "studio",
            "--address", "alice@192.168.1.10",
        ])

    # "Paired!" celebration must not appear when there were errors
    assert "Paired" not in result.output or "warning" in result.output.lower() or \
        "error" in result.output.lower() or "rsync" in result.output.lower(), (
        "pair must not show celebration message when push had errors"
    )
    # The specific green celebration string must not appear
    assert "✓ Paired" not in result.output


def test_autostart_enable_rejects_zero_interval(mock_config):
    """autostart enable must reject interval <= 0."""
    with patch("claudesync.cli.load_config", return_value=mock_config), \
         patch("claudesync.cli.platform") as mock_platform:
        mock_platform.system.return_value = "Darwin"
        result = runner.invoke(app, ["autostart", "enable", REMOTE_NAME, "--interval", "0"])

    assert result.exit_code != 0
    assert "interval" in result.output.lower() or result.exit_code == 1


def test_autostart_enable_rejects_negative_interval(mock_config):
    """autostart enable must reject interval < 0."""
    with patch("claudesync.cli.load_config", return_value=mock_config), \
         patch("claudesync.cli.platform") as mock_platform:
        mock_platform.system.return_value = "Darwin"
        result = runner.invoke(app, ["autostart", "enable", REMOTE_NAME, "--interval", "-5"])

    assert result.exit_code != 0


def test_autostart_disable_rejects_non_darwin():
    """autostart disable must exit with error on non-macOS platforms."""
    with patch("claudesync.cli.platform") as mock_platform:
        mock_platform.system.return_value = "Linux"
        result = runner.invoke(app, ["autostart", "disable", REMOTE_NAME])

    assert result.exit_code != 0
    assert "macos" in result.output.lower() or "darwin" in result.output.lower() or \
        "launchd" in result.output.lower()


def test_pull_manifest_rebuild_passes_include_history(mock_config, connected_engine, tmp_path):
    """Post-pull manifest rebuild must pass include_history from config to _collect_local_files.

    The pull command calls _collect_local_files twice: once for _build_manifests and once
    for the post-sync manifest rebuild. Both calls must pass include_history=True when
    config.sync.include_history is True. We patch _collect_local_files directly to capture
    every call signature.
    """
    mock_config.sync.include_history = True

    with patch("claudesync.cli.load_config", return_value=mock_config), \
         patch("claudesync.cli.Engine", return_value=connected_engine), \
         patch("claudesync.cli._collect_local_files", return_value=[]) as mock_collect, \
         patch("claudesync.cli.build_local_manifest", return_value={}), \
         patch("claudesync.cli.get_remote_manifest", return_value={}), \
         patch("claudesync.cli.update_manifest_for_remote"), \
         patch("claudesync.cli.merge_pulled_claude_json"), \
         patch("tempfile.NamedTemporaryFile") as mock_ntf:
        tmp_file = tmp_path / "empty.json"
        tmp_file.write_text("")
        mock_ntf.return_value.__enter__.return_value.name = str(tmp_file)

        result = runner.invoke(app, ["pull", REMOTE_NAME])

    assert result.exit_code == 0
    # _collect_local_files is called once in _build_manifests and once for post-sync rebuild.
    # Both calls must include include_history=True.
    assert mock_collect.call_count >= 2, "Expected at least 2 calls to _collect_local_files"
    for c in mock_collect.call_args_list:
        assert c.kwargs.get("include_history") is True, (
            f"_collect_local_files called without include_history=True: {c}"
        )


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
