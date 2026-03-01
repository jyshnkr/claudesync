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
    from claudesync.autostart import generate_plist

    plist_content = generate_plist(
        remote_name="studio",
        claudesync_path="/usr/local/bin/claudesync",
        interval_seconds=600,
        log_dir=tmp_path,
    )
    assert "600" in plist_content


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
