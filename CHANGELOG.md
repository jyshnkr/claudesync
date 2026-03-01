# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-02-28

### Security
- Fix bulk restore path traversal bypass in `restore_backup()` — the `else` (restore-all) branch now checks both that source files resolve inside the backup archive and that destinations resolve inside `$HOME`
- Preserve file permissions on atomic replace in `merge_pulled_claude_json()`, `save_manifest()`, and `save_config()` — the original mode is captured before writing the temp file and restored before the rename

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
- Regression test for `detect_conflicts()` `last_sync=None` fallback path
- Security tests: bulk restore destination guard and file permission preservation after merge

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
