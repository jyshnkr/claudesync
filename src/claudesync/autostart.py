"""macOS launchd auto-sync support."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

PLIST_LABEL_PREFIX = "com.claudesync"

_VALID_REMOTE_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")


def _validate_remote_name(name: str) -> None:
    """Raise ValueError if name is not a safe remote identifier.

    Rejects empty strings, names with '/' or '..', and anything that does not
    match the pattern [a-zA-Z0-9][a-zA-Z0-9._-]*.  This prevents path traversal
    in the plist file path and XML injection in the generated plist content.
    """
    if not name or not _VALID_REMOTE_NAME_RE.match(name) or ".." in name:
        raise ValueError(
            f"Remote name {name!r} is invalid. Use only letters, digits, hyphens, "
            "underscores, and dots. Must not be empty or contain '..' or '/'."
        )


def plist_install_path(remote_name: str) -> Path:
    """Return the LaunchAgents path for a given remote."""
    _validate_remote_name(remote_name)
    return Path.home() / "Library" / "LaunchAgents" / f"{PLIST_LABEL_PREFIX}.{remote_name}.plist"


def generate_plist(
    remote_name: str,
    claudesync_path: str,
    interval_seconds: int = 300,
    log_dir: Path | None = None,
) -> str:
    """Generate a launchd plist XML string for auto-syncing a remote."""
    _validate_remote_name(remote_name)
    if log_dir is None:
        log_dir = Path.home() / ".claudesync" / "logs"

    label = xml_escape(f"{PLIST_LABEL_PREFIX}.{remote_name}")
    claudesync_path = xml_escape(claudesync_path)
    remote_name_escaped = xml_escape(remote_name)
    stdout_log = xml_escape(str(log_dir / f"autosync-{remote_name}.log"))
    stderr_log = xml_escape(str(log_dir / f"autosync-{remote_name}-err.log"))

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>

    <key>ProgramArguments</key>
    <array>
        <string>{claudesync_path}</string>
        <string>pull</string>
        <string>{remote_name_escaped}</string>
    </array>

    <key>StartInterval</key>
    <integer>{interval_seconds}</integer>

    <key>RunAtLoad</key>
    <true/>

    <key>StandardOutPath</key>
    <string>{stdout_log}</string>

    <key>StandardErrorPath</key>
    <string>{stderr_log}</string>
</dict>
</plist>
"""


def install_plist(remote_name: str, claudesync_path: str, interval_seconds: int = 300) -> Path:
    """Write plist to ~/Library/LaunchAgents/ and load it with launchctl."""
    _validate_remote_name(remote_name)
    log_dir = Path.home() / ".claudesync" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    plist_path = plist_install_path(remote_name)
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_text(generate_plist(remote_name, claudesync_path, interval_seconds, log_dir))

    subprocess.run(["launchctl", "load", str(plist_path)], check=True)
    return plist_path


def uninstall_plist(remote_name: str) -> bool:
    """Unload and remove the plist for a remote. Returns True if was installed."""
    plist_path = plist_install_path(remote_name)
    if not plist_path.exists():
        return False

    subprocess.run(["launchctl", "unload", str(plist_path)], check=False)
    plist_path.unlink()
    return True
