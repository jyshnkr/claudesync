"""Tests for config loading and saving."""
import pytest
import tomli_w
from pathlib import Path

from claudesync.config import (
    Config,
    Remote,
    SyncSettings,
    load_config,
    save_config,
    _validate_remote,
)


def test_default_config_created_when_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("claudesync.config.CONFIG_DIR", tmp_path / ".claudesync")
    monkeypatch.setattr("claudesync.config.CONFIG_FILE", tmp_path / ".claudesync" / "config.toml")

    from claudesync import config as cfg_mod
    cfg_mod.CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    config = load_config()
    assert config.remotes == {}
    assert config.projects == []
    assert config.sync.strategy == "last-write-wins"
    assert config.sync.backup_count == 10


def test_save_and_load_roundtrip(tmp_path, monkeypatch):
    config_file = tmp_path / "config.toml"
    monkeypatch.setattr("claudesync.config.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("claudesync.config.CONFIG_FILE", config_file)

    config = Config(
        remotes={
            "home": Remote(host="192.168.1.1", user="alice", ssh_key="~/.ssh/id_ed25519", remote_home="/home/alice")
        },
        projects=["/home/alice/Projects"],
        sync=SyncSettings(strategy="last-write-wins", backup_count=5),
    )
    save_config(config)

    loaded = load_config()
    assert "home" in loaded.remotes
    assert loaded.remotes["home"].host == "192.168.1.1"
    assert loaded.remotes["home"].user == "alice"
    assert loaded.projects == ["/home/alice/Projects"]
    assert loaded.sync.backup_count == 5


def test_remote_address():
    r = Remote(host="10.0.0.1", user="bob")
    assert r.address == "bob@10.0.0.1"


def test_remote_default_home():
    r = Remote(host="10.0.0.1", user="carol")
    assert r.remote_home == "/home/carol"


def test_get_remote_raises_on_missing():
    config = Config()
    with pytest.raises(ValueError, match="not found"):
        config.get_remote("nonexistent")


def test_validate_remote_missing_host():
    with pytest.raises(ValueError, match="host"):
        _validate_remote("test", {"user": "alice"})


def test_validate_remote_missing_user():
    with pytest.raises(ValueError, match="user"):
        _validate_remote("test", {"host": "10.0.0.1"})


def test_ssh_key_path_expands_tilde():
    r = Remote(host="h", user="u", ssh_key="~/.ssh/id_rsa")
    assert not str(r.ssh_key_path).startswith("~")
    assert "id_rsa" in str(r.ssh_key_path)


def test_sync_settings_defaults_include_history_false():
    s = SyncSettings()
    assert s.include_history is False


def test_include_history_true_value(tmp_path, monkeypatch):
    """Native TOML boolean true must parse as Python True."""
    config_file = tmp_path / "config.toml"
    monkeypatch.setattr("claudesync.config.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("claudesync.config.CONFIG_FILE", config_file)

    config_file.write_text('[sync]\ninclude_history = true\n')

    loaded = load_config()
    assert loaded.sync.include_history is True


def test_include_history_string_false_treated_as_false(tmp_path, monkeypatch):
    """String 'false' in TOML must parse as Python False, not True.

    bool('false') is True because non-empty strings are truthy — the _parse_bool()
    helper must handle this case correctly.
    """
    config_file = tmp_path / "config.toml"
    monkeypatch.setattr("claudesync.config.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("claudesync.config.CONFIG_FILE", config_file)

    # Write raw TOML with include_history as a string (not a native bool)
    config_file.write_text('[sync]\ninclude_history = "false"\n')

    loaded = load_config()
    assert loaded.sync.include_history is False


def test_include_history_string_true_treated_as_true(tmp_path, monkeypatch):
    """String 'true' in TOML must parse as Python True."""
    config_file = tmp_path / "config.toml"
    monkeypatch.setattr("claudesync.config.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("claudesync.config.CONFIG_FILE", config_file)

    config_file.write_text('[sync]\ninclude_history = "true"\n')

    loaded = load_config()
    assert loaded.sync.include_history is True
