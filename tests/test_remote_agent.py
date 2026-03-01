"""Tests for remote_agent.py — deployed sidecar script."""
import json
import os
import sys
import pytest
from pathlib import Path
from unittest.mock import patch


# We import the module itself (not via __main__), so we can call hash_files directly.
import importlib.util


def _load_remote_agent():
    worktree = Path(__file__).parent.parent
    agent_path = worktree / "src" / "claudesync" / "remote_agent.py"
    spec = importlib.util.spec_from_file_location("remote_agent", agent_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


remote_agent = _load_remote_agent()


# ---------------------------------------------------------------------------
# hash_files
# ---------------------------------------------------------------------------

def test_hash_files_valid_input(tmp_path):
    """hash_files returns hash and mtime for existing files."""
    f = tmp_path / "test.txt"
    f.write_text("hello world")

    result = remote_agent.hash_files([str(f)])

    assert str(f) in result
    assert "hash" in result[str(f)]
    assert "mtime" in result[str(f)]
    assert len(result[str(f)]["hash"]) == 64  # SHA-256 hex


def test_hash_files_skips_missing_file(tmp_path):
    """hash_files silently skips paths that don't exist."""
    missing = str(tmp_path / "nonexistent.txt")
    result = remote_agent.hash_files([missing])
    assert result == {}


def test_hash_files_skips_unreadable_file(tmp_path):
    """hash_files must skip files that raise OSError/IOError on read."""
    f = tmp_path / "unreadable.txt"
    f.write_text("content")
    f.chmod(0o000)  # Remove all permissions

    try:
        result = remote_agent.hash_files([str(f)])
        # Should skip the file rather than raising
        assert str(f) not in result
    finally:
        f.chmod(0o644)  # Restore so tmp_path cleanup works


def test_hash_files_empty_list():
    """hash_files on an empty list returns an empty dict."""
    result = remote_agent.hash_files([])
    assert result == {}


# ---------------------------------------------------------------------------
# __main__ input validation
# ---------------------------------------------------------------------------

def test_main_rejects_non_list_json(tmp_path, capsys):
    """remote_agent must exit(1) if JSON arg is not a list."""
    with pytest.raises(SystemExit) as exc_info:
        with patch.object(sys, "argv", ["remote_agent.py", json.dumps({"not": "a list"})]):
            # Re-execute the __main__ block
            code = Path(remote_agent.__file__).read_text()
            exec(compile(code, remote_agent.__file__, "exec"), {"__name__": "__main__"})
    assert exc_info.value.code == 1


def test_main_rejects_non_string_list(tmp_path, capsys):
    """remote_agent must exit(1) if JSON arg is a list but contains non-strings."""
    with pytest.raises(SystemExit) as exc_info:
        with patch.object(sys, "argv", ["remote_agent.py", json.dumps([1, 2, 3])]):
            code = Path(remote_agent.__file__).read_text()
            exec(compile(code, remote_agent.__file__, "exec"), {"__name__": "__main__"})
    assert exc_info.value.code == 1


def test_main_accepts_valid_list(tmp_path, capsys):
    """remote_agent must succeed and output JSON for a valid string list."""
    f = tmp_path / "file.txt"
    f.write_text("data")

    with patch.object(sys, "argv", ["remote_agent.py", json.dumps([str(f)])]):
        code = Path(remote_agent.__file__).read_text()
        exec(compile(code, remote_agent.__file__, "exec"), {"__name__": "__main__"})

    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert str(f) in result
