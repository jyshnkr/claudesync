# ClaudeSync

Bi-directional Claude Code context sync over SSH. Keep your Claude settings, project instructions, and MCP configuration identical across every machine you work on.

## Features

- **Push / pull** `~/.claude/`, per-project `CLAUDE.md` / `.claude/settings.json` / `.mcp.json`, and `~/.claude.json` between local and remote machines via rsync over SSH
- **Conflict detection** — last-write-wins based on file mtime, with automatic backup of the losing side
- **Safe sanitization** — strips OAuth tokens, API keys, and other auth fields from `~/.claude.json` before it ever leaves your machine
- **Manifest tracking** — per-remote SHA-256 + mtime manifest to detect what actually changed
- **Atomic writes** — all config/manifest updates use temp-file + rename to avoid partial writes
- **Security hardened** — path-traversal guards on restore, file-permission preservation on atomic replace

## Prerequisites

- Python 3.9+
- `rsync` (any modern version)
- SSH key access to the remote machine (password auth is not supported)

## Installation

```bash
pip install -e .
```

Or, for development:

```bash
pip install -e ".[dev]"
```

## Quick Start

```bash
# 1. Run the interactive setup wizard
claudesync init

# 2. Register a project directory
claudesync project add ~/Projects/MyProject

# 3. Push local context to the remote
claudesync push home

# 4. Pull remote context back to local
claudesync pull home
```

## CLI Reference

| Command | Description |
|---------|-------------|
| `claudesync init` | Interactive setup wizard |
| `claudesync push <remote>` | Sync local → remote |
| `claudesync pull <remote>` | Sync remote → local |
| `claudesync status <remote>` | Dry-run showing what would change |
| `claudesync diff <remote>` | File-level diff between local and remote |
| `claudesync remote add <name> <user@host>` | Add a remote machine |
| `claudesync remote list` | List configured remotes |
| `claudesync project add <path>` | Register a project directory |
| `claudesync project list` | List registered projects |
| `claudesync backup list` | List conflict backups |
| `claudesync backup restore <id> [path]` | Restore a backed-up file |

Options for `remote add`:

| Option | Default | Description |
|--------|---------|-------------|
| `--key`, `-k` | `~/.ssh/id_ed25519` | SSH private key path |
| `--remote-home`, `-r` | `/home/<user>` | Remote home directory |

## Architecture

```
src/claudesync/
├── cli.py        — Typer CLI commands; orchestrates push/pull/diff/status
├── engine.py     — Rsync engine: builds commands, runs transfers, counts files
├── config.py     — Config load/save (TOML); Remote + SyncSettings dataclasses
├── manifest.py   — SHA-256 + mtime manifest; per-remote last-sync tracking
├── conflicts.py  — Conflict detection and last-write-wins resolution
├── backup.py     — Backup creation, listing, and restore (with security guards)
├── sanitize.py   — Strip auth fields from ~/.claude.json before sync
└── filters.py    — Rsync filter rules and project sync item list
```

## Configuration Reference

Configuration is stored in `~/.claudesync/config.toml`:

```toml
[remotes.home]
host       = "192.168.1.100"
user       = "alice"
ssh_key    = "~/.ssh/id_ed25519"
remote_home = "/home/alice"

[projects]
paths = ["/Users/alice/Projects/MyProject"]

[sync]
strategy     = "last-write-wins"   # only supported strategy
backup_count = 10                  # number of conflict backups to keep
```

## What Gets Synced

| Item | Location |
|------|----------|
| Global Claude settings | `~/.claude/` (excluding `*.jsonl`, `~/.claude/cache/`) |
| Claude global config | `~/.claude.json` (auth fields stripped on push, re-merged on pull) |
| Per-project instructions | `<project>/CLAUDE.md` |
| Per-project settings | `<project>/.claude/settings.json` |
| Per-project MCP config | `<project>/.mcp.json` |

## Security Considerations

- **Auth fields never leave your machine.** `oauthAccount`, `userID`, `primaryApiKey`, and other sensitive fields are stripped from `~/.claude.json` before it is transferred to the remote.
- **Pulled config is merged, not replaced.** When pulling, remote UI preferences overwrite local ones, but local auth fields are always preserved.
- **Path traversal is blocked on restore.** `restore_backup()` verifies that both the source file (inside the backup archive) and the destination path resolve inside the expected directories.
- **File permissions are preserved.** Atomic replace operations capture the original mode and restore it on the temp file before the rename.

## Development

```bash
# Run the full test suite
pytest

# Install with dev dependencies
pip install -e ".[dev]"
```

Project structure:

```
.
├── src/claudesync/   — Source package
├── tests/            — Pytest test suite (97 tests)
├── pyproject.toml    — Build configuration
├── CHANGELOG.md      — Version history
└── SECURITY.md       — Security policy
```

## Contributing

1. Fork the repository and create a feature branch
2. Run `pytest` to verify all tests pass
3. Submit a pull request with a clear description of the change

## License

MIT
