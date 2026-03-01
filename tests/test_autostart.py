"""Tests for macOS launchd auto-sync plist generation."""
import pytest
from pathlib import Path
import xml.etree.ElementTree as ET


def test_generate_plist_contains_correct_remote_name(tmp_path):
    from claudesync.autostart import generate_plist

    plist_content = generate_plist(
        remote_name="studio",
        claudesync_path="/usr/local/bin/claudesync",
        interval_seconds=300,
        log_dir=tmp_path,
    )
    # Must be valid XML
    root = ET.fromstring(plist_content)
    assert root.tag == "plist"
    # Must contain the remote name somewhere
    assert "studio" in plist_content
    # Must contain StartInterval
    assert "StartInterval" in plist_content


def test_generate_plist_uses_correct_interval(tmp_path):
    """Interval value must appear in the StartInterval integer node (verified via XML parse)."""
    from claudesync.autostart import generate_plist

    plist_content = generate_plist(
        remote_name="studio",
        claudesync_path="/usr/local/bin/claudesync",
        interval_seconds=600,
        log_dir=tmp_path,
    )
    root = ET.fromstring(plist_content)
    # Walk the dict children: key "StartInterval" followed by integer node
    children = list(root.find("dict"))
    for i, child in enumerate(children):
        if child.tag == "key" and child.text == "StartInterval":
            integer_node = children[i + 1]
            assert integer_node.tag == "integer"
            assert integer_node.text == "600"
            break
    else:
        pytest.fail("StartInterval key not found in plist")


def test_plist_label_is_unique_per_remote(tmp_path):
    from claudesync.autostart import generate_plist

    plist_a = generate_plist("studio", "/usr/bin/claudesync", 300, tmp_path)
    plist_b = generate_plist("work", "/usr/bin/claudesync", 300, tmp_path)
    assert "studio" in plist_a
    assert "work" in plist_b
    assert "studio" not in plist_b


def test_install_path_is_in_launch_agents(tmp_path):
    from claudesync.autostart import plist_install_path
    path = plist_install_path("studio")
    assert "LaunchAgents" in str(path)
    assert "studio" in str(path)


# ---------------------------------------------------------------------------
# remote_name validation tests
# ---------------------------------------------------------------------------

def test_validate_remote_name_rejects_path_traversal():
    """remote_name with '..' must be rejected."""
    from claudesync.autostart import _validate_remote_name
    with pytest.raises(ValueError, match="invalid"):
        _validate_remote_name("../etc")


def test_validate_remote_name_rejects_slashes():
    """remote_name containing '/' must be rejected."""
    from claudesync.autostart import _validate_remote_name
    with pytest.raises(ValueError, match="invalid"):
        _validate_remote_name("foo/bar")


def test_validate_remote_name_rejects_empty():
    """Empty remote_name must be rejected."""
    from claudesync.autostart import _validate_remote_name
    with pytest.raises(ValueError, match="invalid"):
        _validate_remote_name("")


def test_validate_remote_name_accepts_valid():
    """Valid remote names (alphanumeric, hyphens, dots, underscores) must pass."""
    from claudesync.autostart import _validate_remote_name
    # Should not raise
    _validate_remote_name("home")
    _validate_remote_name("my-remote")
    _validate_remote_name("remote.1")
    _validate_remote_name("a1_b2")


def test_generate_plist_escapes_xml_chars(tmp_path):
    """remote_name containing XML special chars must produce valid XML."""
    from claudesync.autostart import generate_plist, _validate_remote_name
    import pytest
    # '&' in remote_name must be rejected by validation (not a valid name char)
    with pytest.raises(ValueError, match="invalid"):
        _validate_remote_name("foo&bar")


def test_generate_plist_rejects_invalid_remote_name(tmp_path):
    """generate_plist must reject an invalid remote_name."""
    from claudesync.autostart import generate_plist
    with pytest.raises(ValueError, match="invalid"):
        generate_plist(
            remote_name="../../etc/passwd",
            claudesync_path="/usr/bin/claudesync",
            interval_seconds=300,
            log_dir=tmp_path,
        )


def test_plist_install_path_rejects_invalid_remote_name():
    """plist_install_path must reject an invalid remote_name."""
    from claudesync.autostart import plist_install_path
    with pytest.raises(ValueError, match="invalid"):
        plist_install_path("../evil")


# ---------------------------------------------------------------------------
# sanitize nested fields tests
# ---------------------------------------------------------------------------

def test_sanitize_strips_env_from_mcp_servers(tmp_path):
    """env keys nested in mcpServers values must be stripped."""
    import json
    from claudesync.sanitize import sanitize_claude_json

    source = tmp_path / ".claude.json"
    source.write_text(json.dumps({
        "mcpServers": {
            "my-server": {
                "command": "node",
                "args": ["server.js"],
                "env": {"API_KEY": "secret-token", "PORT": "3000"},
            }
        }
    }))

    result = sanitize_claude_json(source)
    assert "mcpServers" in result
    server = result["mcpServers"]["my-server"]
    assert "env" not in server, "env must be stripped from mcpServers values"
    assert server["command"] == "node"
    assert server["args"] == ["server.js"]


def test_sanitize_strips_sensitive_nested_in_projects(tmp_path):
    """Sensitive keys nested inside projects values must be stripped."""
    import json
    from claudesync.sanitize import sanitize_claude_json

    source = tmp_path / ".claude.json"
    source.write_text(json.dumps({
        "projects": {
            "/path/to/project": {
                "enabled": True,
                "apiKey": "sk-ant-secret",
                "token": "bearer-abc",
            }
        }
    }))

    result = sanitize_claude_json(source)
    assert "projects" in result
    proj = result["projects"]["/path/to/project"]
    assert "apiKey" not in proj, "apiKey must be stripped from project values"
    assert "token" not in proj, "token must be stripped from project values"
    assert proj["enabled"] is True


def test_sanitize_preserves_safe_nested_structure(tmp_path):
    """Non-sensitive nested fields in mcpServers and projects must be preserved."""
    import json
    from claudesync.sanitize import sanitize_claude_json

    source = tmp_path / ".claude.json"
    source.write_text(json.dumps({
        "mcpServers": {
            "safe-server": {
                "command": "python",
                "args": ["-m", "myserver"],
            }
        },
        "projects": {
            "/my/proj": {
                "enabled": False,
                "name": "My Project",
            }
        }
    }))

    result = sanitize_claude_json(source)
    assert result["mcpServers"]["safe-server"]["command"] == "python"
    assert result["mcpServers"]["safe-server"]["args"] == ["-m", "myserver"]
    assert result["projects"]["/my/proj"]["enabled"] is False
    assert result["projects"]["/my/proj"]["name"] == "My Project"


# ---------------------------------------------------------------------------
# uninstall_plist resilience tests
# ---------------------------------------------------------------------------

def test_uninstall_plist_handles_missing_launchctl(tmp_path, monkeypatch):
    """uninstall_plist must still unlink the plist and return True if launchctl is absent."""
    import subprocess
    from claudesync.autostart import plist_install_path

    # Monkeypatch plist_install_path to point into tmp_path
    fake_plist = tmp_path / "com.claudesync.autosync.studio.plist"
    fake_plist.write_text("<plist/>")
    monkeypatch.setattr(
        "claudesync.autostart.plist_install_path",
        lambda remote_name: fake_plist,
    )

    # Make subprocess.run raise FileNotFoundError (launchctl not found)
    def raise_fnf(*args, **kwargs):
        raise FileNotFoundError("launchctl not found")

    monkeypatch.setattr(subprocess, "run", raise_fnf)

    from claudesync.autostart import uninstall_plist
    result = uninstall_plist("studio")

    assert result is True
    assert not fake_plist.exists(), "plist file must be removed even when launchctl is absent"
