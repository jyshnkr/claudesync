"""Config management for ClaudeSync."""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

SyncStrategy = Literal["last-write-wins"]
"""Conflict resolution strategy. Only 'last-write-wins' is implemented; the
field is persisted to allow future strategies without a schema change."""

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

import tomli_w

CONFIG_DIR = Path.home() / ".claudesync"
CONFIG_FILE = CONFIG_DIR / "config.toml"


@dataclass
class Remote:
    host: str
    user: str
    ssh_key: str = "~/.ssh/id_ed25519"
    remote_home: str = ""

    def __post_init__(self) -> None:
        if not self.remote_home:
            self.remote_home = f"/home/{self.user}"

    @property
    def ssh_key_path(self) -> Path:
        return Path(self.ssh_key).expanduser()

    @property
    def address(self) -> str:
        return f"{self.user}@{self.host}"


@dataclass
class SyncSettings:
    strategy: SyncStrategy = "last-write-wins"
    backup_count: int = 10

    def __post_init__(self) -> None:
        if self.backup_count < 1:
            raise ValueError(f"backup_count must be >= 1, got {self.backup_count}")


@dataclass
class Config:
    remotes: dict[str, Remote] = field(default_factory=dict)
    projects: list[str] = field(default_factory=list)
    sync: SyncSettings = field(default_factory=SyncSettings)

    def get_remote(self, name: str) -> Remote:
        if name not in self.remotes:
            raise ValueError(f"Remote '{name}' not found. Run: claudesync remote add {name} <user@host>")
        return self.remotes[name]

    def project_paths(self) -> list[Path]:
        return [Path(p).expanduser() for p in self.projects]


def load_config() -> Config:
    """Load config from ~/.claudesync/config.toml, creating defaults if missing."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    if not CONFIG_FILE.exists():
        return Config()

    try:
        with CONFIG_FILE.open("rb") as f:
            raw = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ValueError(
            f"Config file {CONFIG_FILE} is corrupted ({e}). "
            "Fix or delete it to continue."
        ) from e

    remotes: dict[str, Remote] = {}
    for name, data in raw.get("remotes", {}).items():
        _validate_remote(name, data)
        remotes[name] = Remote(
            host=data["host"],
            user=data["user"],
            ssh_key=data.get("ssh_key", "~/.ssh/id_ed25519"),
            remote_home=data.get("remote_home", f"/home/{data['user']}"),
        )

    sync_data = raw.get("sync", {})
    try:
        backup_count = int(sync_data.get("backup_count", 10))
    except (ValueError, TypeError) as e:
        raise ValueError(
            f"Config sync.backup_count must be an integer, got: "
            f"{sync_data.get('backup_count')!r}"
        ) from e
    sync = SyncSettings(
        strategy=sync_data.get("strategy", "last-write-wins"),
        backup_count=backup_count,
    )

    projects = raw.get("projects", {}).get("paths", [])

    return Config(remotes=remotes, projects=projects, sync=sync)


def save_config(config: Config) -> None:
    """Save config to ~/.claudesync/config.toml."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    data: dict = {}

    if config.remotes:
        data["remotes"] = {}
        for name, remote in config.remotes.items():
            data["remotes"][name] = {
                "host": remote.host,
                "user": remote.user,
                "ssh_key": remote.ssh_key,
                "remote_home": remote.remote_home,
            }

    if config.projects:
        data["projects"] = {"paths": config.projects}

    data["sync"] = {
        "strategy": config.sync.strategy,
        "backup_count": config.sync.backup_count,
    }

    original_mode = CONFIG_FILE.stat().st_mode if CONFIG_FILE.exists() else None
    tmp = CONFIG_FILE.with_suffix(".tmp")
    try:
        with tmp.open("wb") as f:
            tomli_w.dump(data, f)
        if original_mode is not None:
            tmp.chmod(original_mode)
        tmp.replace(CONFIG_FILE)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def _validate_remote(name: str, data: dict) -> None:
    for required in ("host", "user"):
        if required not in data:
            raise ValueError(f"Remote '{name}' missing required field: '{required}'")
