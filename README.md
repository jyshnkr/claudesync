# ClaudeSync

Bi-directional Claude Code context sync over SSH. Keep your Claude settings, project instructions, and MCP configuration identical across every machine you work on.

## Features

- **Push / pull** an allowlist of `~/.claude/` files (`settings.json`, `projects/`, `tasks/`, `plans/`, `session-env/`, `plugins/`), per-project `CLAUDE.md` / `.claude/settings.json` / `.mcp.json`, and `~/.claude.json` between local and remote machines via rsync over SSH
- **One-command pairing** — `claudesync pair` tests SSH, auto-detects remote home, saves config, and runs an initial push
- **Autostart** — `claudesync autostart enable/disable` installs/removes a macOS launchd plist to auto-pull on a schedule
- **Conflict detection** — last-write-wins based on file mtime, with automatic backup of the losing side and human-readable conflict output
- **Safe allowlist sanitization** — only explicitly safe fields are synced from `~/.claude.json`; unknown fields, auth tokens, API keys, and sensitive nested keys are stripped by default
- **Manifest tracking** — per-remote SHA-256 + mtime manifest to detect what actually changed
- **Atomic writes** — all config/manifest updates use temp-file + rename to avoid partial writes
- **Dedicated `known_hosts`** — SSH host keys stored in `~/.claudesync/known_hosts`, separate from `~/.ssh/known_hosts`
- **Security hardened** — path-traversal guards on restore, file-permission preservation on atomic replace, XML-escaped plist generation

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

### Option A: `claudesync pair` (recommended)

```bash
# One command — tests SSH, saves config, and pushes
claudesync pair --name home --address alice@192.168.1.100

# Optional: enable auto-pull every 5 minutes
claudesync autostart enable home
```

### Option B: Manual setup

```bash
# 1. Run the interactive setup wizard
claudesync init

# 2. Register a project directory
claudesync project add ~/Projects/MyProject

# 3. Push local context to the remote
claudesync push home

# 4. Pull remote context back to local
claudesync pull home

# Optional: enable auto-pull every 5 minutes
claudesync autostart enable home
```

## CLI Reference

| Command | Description |
|---------|-------------|
| `claudesync init` | Interactive setup wizard |
| `claudesync pair` | One-command two-machine setup |
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
| `claudesync autostart enable <remote>` | Install launchd plist for auto-pull |
| `claudesync autostart disable <remote>` | Remove launchd plist |

Options for `remote add`:

| Option | Default | Description |
|--------|---------|-------------|
| `--key`, `-k` | `~/.ssh/id_ed25519` | SSH private key path |
| `--remote-home`, `-r` | `/home/<user>` | Remote home directory |

Options for `pair`:

| Option | Description |
|--------|-------------|
| `--name`, `-n` | Name for the remote (e.g. `home`) |
| `--address`, `-a` | SSH address (`user@host`) |
| `--key`, `-k` | SSH private key path (optional) |

Options for `autostart enable`:

| Option | Default | Description |
|--------|---------|-------------|
| `--interval` | `300` | Pull interval in seconds |

## Architecture

```text
src/claudesync/
├── cli.py          — Typer CLI commands; orchestrates push/pull/diff/status/pair/autostart
├── engine.py       — Rsync engine: builds commands, runs transfers, counts files
├── remote_agent.py — Versioned sidecar script deployed to remote for file hashing
├── config.py       — Config load/save (TOML); Remote + SyncSettings dataclasses
├── manifest.py     — SHA-256 + mtime manifest; per-remote last-sync tracking
├── conflicts.py    — Conflict detection and last-write-wins resolution
├── backup.py       — Backup creation, listing, and restore (with security guards)
├── sanitize.py     — Allowlist sanitization of ~/.claude.json before sync
├── autostart.py    — macOS launchd plist install/uninstall for scheduled pulls
└── filters.py      — Rsync filter rules and project sync item list
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
strategy         = "last-write-wins"   # only supported strategy
backup_count     = 10                  # number of conflict backups to keep
include_history  = false               # set true to sync history.jsonl (opt-in)
```

## What Gets Synced

ClaudeSync uses an **allowlist** approach — only the items listed below are transferred. Everything else in `~/.claude/` is excluded by default.

| Item | Location |
|------|----------|
| Global Claude settings | `~/.claude/settings.json` |
| Projects directory | `~/.claude/projects/` |
| Tasks directory | `~/.claude/tasks/` |
| Plans directory | `~/.claude/plans/` |
| Session environments | `~/.claude/session-env/` |
| Installed plugins list | `~/.claude/plugins/installed_plugins.json` |
| Plugin blocklist | `~/.claude/plugins/blocklist.json` |
| Conversation history | `~/.claude/history.jsonl` (**opt-in**, disabled by default) |
| Claude global config | `~/.claude.json` (auth fields stripped on push, re-merged on pull) |
| Per-project instructions | `<project>/CLAUDE.md` |
| Per-project settings | `<project>/.claude/settings.json` |
| Per-project MCP config | `<project>/.mcp.json` |

## Security Considerations

- **Allowlist approach.** Only explicitly safe fields from `~/.claude.json` are synced. Unknown fields are stripped by default, preventing silent data exfiltration as the Claude config schema evolves.
- **Auth fields never leave your machine.** `oauthAccount`, `userID`, `primaryApiKey`, and other sensitive fields are stripped from `~/.claude.json` before it is transferred to the remote.
- **Merge overlay sanitization.** Sensitive nested keys (`env`, `apiKey`, `token`, `secret`, `password`, etc.) are recursively stripped from the remote overlay before it is merged into local config.
- **Pulled config is merged, not replaced.** When pulling, remote UI preferences overwrite local ones, but local auth fields are always preserved.
- **Dedicated `known_hosts`.** SSH connections use `~/.claudesync/known_hosts` rather than `~/.ssh/known_hosts`, so host-key changes on a remote are caught immediately rather than silently updating a shared file.
- **Hardened plist generation.** All user-supplied values are XML-escaped before being written to launchd plists; `launchctl` is invoked via its absolute path `/bin/launchctl` to prevent CWD-based PATH hijacking.
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

```text
.
├── src/claudesync/   — Source package
├── tests/            — Pytest test suite (155 tests)
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
