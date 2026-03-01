# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
