"""Tests for manifest tracking."""
import json
import pytest
from pathlib import Path

from claudesync.manifest import (
    compute_file_hash,
    build_local_manifest,
    load_manifest,
    save_manifest,
    update_manifest_for_remote,
    get_remote_manifest,
)


@pytest.fixture
def manifest_file(tmp_path, monkeypatch):
    mf = tmp_path / "manifest.json"
    monkeypatch.setattr("claudesync.manifest.MANIFEST_FILE", mf)
    return mf


def test_compute_file_hash(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("hello world")
    h = compute_file_hash(f)
    assert len(h) == 64  # SHA-256 hex
    assert h == compute_file_hash(f)  # deterministic


def test_compute_hash_different_content(tmp_path):
    f1 = tmp_path / "a.txt"
    f2 = tmp_path / "b.txt"
    f1.write_text("hello")
    f2.write_text("world")
    assert compute_file_hash(f1) != compute_file_hash(f2)


def test_build_local_manifest(tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("content")
    manifest = build_local_manifest([str(f)])
    assert str(f) in manifest
    assert "hash" in manifest[str(f)]
    assert "mtime" in manifest[str(f)]


def test_build_manifest_skips_missing(tmp_path):
    manifest = build_local_manifest([str(tmp_path / "nonexistent.txt")])
    assert manifest == {}


def test_save_and_load_manifest(manifest_file):
    data = {"home": {"/some/file": {"hash": "abc", "mtime": 123.0, "last_synced": "2026"}}}
    save_manifest(data)
    loaded = load_manifest()
    assert loaded == data


def test_load_manifest_empty_when_missing(manifest_file):
    result = load_manifest()
    assert result == {}


def test_update_manifest_for_remote(manifest_file):
    local_manifest = {"/path/to/file": {"hash": "deadbeef", "mtime": 1000.0}}
    update_manifest_for_remote("home", local_manifest)

    remote_manifest = get_remote_manifest("home")
    assert "/path/to/file" in remote_manifest
    assert remote_manifest["/path/to/file"]["hash"] == "deadbeef"
    assert "last_synced" in remote_manifest["/path/to/file"]


def test_get_remote_manifest_empty_for_unknown(manifest_file):
    result = get_remote_manifest("unknown-remote")
    assert result == {}


def test_load_manifest_rejects_non_dict_json(manifest_file):
    """load_manifest must reject a manifest file that is not a JSON object."""
    manifest_file.write_text(json.dumps([1, 2, 3]))

    with pytest.raises(ValueError, match="corrupted"):
        load_manifest()


def test_manifest_update_is_serialized(tmp_path, monkeypatch):
    """Concurrent manifest updates must not lose data."""
    import threading
    monkeypatch.setattr("claudesync.manifest.MANIFEST_FILE", tmp_path / "manifest.json")
    monkeypatch.setattr("claudesync.manifest.LOCK_FILE", tmp_path / "manifest.lock")

    def update_remote(name):
        manifest_data = {name: {"hash": "abc", "mtime": 1.0}}
        update_manifest_for_remote(name, manifest_data)

    threads = [
        threading.Thread(target=update_remote, args=(f"remote{i}",))
        for i in range(5)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # All 5 remotes must be present — none overwritten
    saved = load_manifest()
    for i in range(5):
        assert f"remote{i}" in saved, f"remote{i} was lost due to race condition"
