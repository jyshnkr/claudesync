# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] - 2026-03-01

### Security

- Use a dedicated `~/.claudesync/known_hosts` file for SSH host key storage (separate from `~/.ssh/known_hosts`) so rogue re-keys are caught on subsequent connections
- Flip `.claude.json` sanitizer from blocklist to **allowlist**: only explicitly safe fields (`theme`, `numStartups`, `projects`, etc.) are synced — unknown future fields are stripped by default, preventing silent data exfiltration
- Validate `autostart` remote_name with strict regex (`^[a-zA-Z0-9][a-zA-Z0-9._-]*$`); reject empty, `..`, or `/` — prevents path traversal in plist path and XML injection
- Recursively strip sensitive nested keys (`env`, `apiKey`, `token`, `secret`, `password`, etc.) from `mcpServers` and `projects` values in `.claude.json` sanitization

### Fixed

- Add `fcntl.LOCK_EX` file locking to `update_manifest_for_remote()` to prevent lost updates when two concurrent syncs race on the manifest file
- Replace the SSH Python one-liner for remote file hashing with a versioned sidecar script (`remote_agent.py`) deployed to `~/.claudesync/` on the remote — eliminates shell quoting edge cases and SSH banner pollution
- Pass `include_history` to post-pull manifest rebuild (`_collect_local_files` call in `cli.py`)
- Replace `bool(value)` with `_parse_bool()` for `sync.include_history` config parsing — `bool("false")` was `True` (non-empty string)
- Harden `engine._ensure_remote_agent`: catch `TimeoutExpired` on version check (treat as "not present"), and on deploy (raise `SyncError`); catch `FileNotFoundError` on both
- `remote_agent.hash_files`: skip unreadable files with `try/except (OSError, IOError): continue`
- `remote_agent.__main__`: validate JSON arg is `list[str]`; `sys.exit(1)` for dict, non-string list, etc.
- `claudesync pair`: show "✓ Paired!" celebration only when push has no errors
- `claudesync autostart enable`: reject `--interval <= 0`
- `claudesync autostart disable`: add macOS platform check; wrap `uninstall_plist` in try/except

### Changed

- `history.jsonl` is now **opt-in**: set `sync.include_history = true` in `~/.claudesync/config.toml` to sync conversation history. Default is `false` since the file contains full conversation history, pasted code, and internal project data
- `Engine.push`, `Engine.pull`, `Engine._sync`, `Engine.dry_run`: `include_history` is now keyword-only (add `*` separator) to prevent positional misuse

### Added

- `claudesync pair --name <n> --address user@host` — one-command two-machine setup: tests SSH, auto-detects remote home, saves config, and runs an initial push
- `claudesync autostart enable <remote>` / `disable <remote>` — installs/removes a macOS launchd plist (`~/Library/LaunchAgents/com.claudesync.<remote>.plist`) to auto-pull every N seconds (default 5 min)
- `_human_age()` helper: conflict output now shows `← LOST / ← WON` labels and human-readable relative timestamps (`3 days ago`, `2 hours ago`) so users understand why a conflict was resolved the way it was
- ~20 new tests addressing PR #8 review comments (total 140+)

## [0.2.0] - 2026-03-01

### Security

- Fix bulk restore path traversal bypass in `restore_backup()` — the restore-all branch now checks both that source files resolve inside the backup archive and that destinations resolve inside `$HOME`
- Preserve file permissions on atomic replace in `merge_pulled_claude_json()`, `save_manifest()`, and `save_config()` — the original mode is captured before writing the temp file and restored before the rename
- Validate `backup_id` as a single safe path segment before directory lookup; rejects `..`, `.`, multi-segment values like `../../etc` that could escape `BACKUP_DIR`
- Atomic restore writes via `_atomic_copy` helper: writes to a temp file in the same directory then `os.replace()` (POSIX-atomic rename), blocking symlink write races on the destination
- `is_dir()` guard on backup entry lookup — rejects a regular file at `BACKUP_DIR/<backup_id>` that would silently pass `exists()`
- Close TOCTOU window in `_atomic_copy` via `dirfd` inode verification: opens `dest.parent` with `O_NOFOLLOW|O_DIRECTORY`, compares `(st_ino, st_dev)` to the preceding `lstat`, then creates the temp file against the held fd via `os.openat` (with `os.open` fallback on platforms where `openat` is unavailable)

### Fixed

- Rebuild local manifest after pull to record post-sync file state rather than pre-sync state
- Translate local absolute paths to remote equivalents in `_build_manifests()` before querying remote file hashes, so hash lookup actually finds matching files
- Handle `FileNotFoundError` in `get_remote_file_hashes()` when the SSH binary is missing, wrapping it as `SyncError`
- Validate that JSON root is a dict in `sanitize_claude_json()`, `merge_pulled_claude_json()` (both remote and local sides), and `load_manifest()` — raises `ValueError` for non-object JSON
- Count `.claude.json` file transfers in `SyncSummary.files_transferred`

### Changed

- `SyncedFileEntry.last_synced` is now optional for legacy manifest compatibility (TypedDict inheritance pattern)
- `dry_run` parameter on `_rsync_global` and `_rsync_project` is now keyword-only to prevent positional misuse
- Validate `sync.strategy` config field at load time against the set of supported values

### Added

- `README.md` with installation, usage, architecture, and contributing documentation
- `CHANGELOG.md` following Keep a Changelog format
- 20 new tests (88 → 108 total), including 18 covering backup creation, listing, restore, path-traversal guards, `backup_id` validation, `is_dir` guard, and symlink rejection in `_atomic_copy`

## [0.1.0] - 2026-02-28

### Added

- Initial release: bi-directional Claude Code context sync over SSH
- CLI commands: `init`, `push`, `pull`, `status`, `diff`
- Remote management: `remote add`, `remote list`
- Project registration: `project add`, `project list`
- Conflict detection with last-write-wins resolution
- Automatic backup with rotation for conflict losers
- Manifest tracking per remote for change detection
- `.claude.json` sanitization (strips auth fields before sync)
- Rsync-based file transfer with SSH key authentication
- Security hardening: path traversal guards, atomic file writes
- 88 tests
