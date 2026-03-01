"""Tests for ~/.claude.json sanitization."""
import json
import pytest
from pathlib import Path

from claudesync.sanitize import (
    sanitize_claude_json,
    write_sanitized_temp,
    merge_pulled_claude_json,
    SAFE_FIELDS,
)


@pytest.fixture
def full_claude_json(tmp_path) -> Path:
    data = {
        "oauthAccount": {"token": "secret-token"},
        "userID": "user-123",
        "cachedGrowthBookFeatures": {"feature": True},
        "passesEligibilityCache": True,
        # SAFE_FIELDS members — these should survive sanitization
        "theme": "dark",
        "numStartups": 42,
        "verbose": True,
        "projects": {"/path/to/project": {"enabled": True}},
        # Non-safe fields — should be stripped by the allowlist
        "showSpinnerTree": True,
        "skillUsage": {"my-skill": 5},
        "lastPlanModeUse": "2026-02-28",
    }
    p = tmp_path / ".claude.json"
    p.write_text(json.dumps(data))
    return p


def test_sanitize_strips_oauth(full_claude_json):
    result = sanitize_claude_json(full_claude_json)
    assert "oauthAccount" not in result


def test_sanitize_strips_user_id(full_claude_json):
    result = sanitize_claude_json(full_claude_json)
    assert "userID" not in result


def test_sanitize_strips_growthbook(full_claude_json):
    result = sanitize_claude_json(full_claude_json)
    assert "cachedGrowthBookFeatures" not in result


def test_sanitize_strips_eligibility(full_claude_json):
    result = sanitize_claude_json(full_claude_json)
    assert "passesEligibilityCache" not in result


def test_sanitize_keeps_safe_fields(full_claude_json):
    result = sanitize_claude_json(full_claude_json)
    assert result["theme"] == "dark"
    assert result["numStartups"] == 42
    assert result["verbose"] is True
    # Non-safe fields must be stripped by the allowlist
    assert "showSpinnerTree" not in result
    assert "skillUsage" not in result
    assert "lastPlanModeUse" not in result


def test_sanitize_keeps_projects(full_claude_json):
    result = sanitize_claude_json(full_claude_json)
    assert "/path/to/project" in result["projects"]


def test_sanitize_returns_empty_when_missing(tmp_path):
    result = sanitize_claude_json(tmp_path / "missing.json")
    assert result == {}


def test_write_sanitized_temp(full_claude_json):
    tmp = write_sanitized_temp(full_claude_json)
    try:
        assert tmp.exists()
        with tmp.open() as f:
            data = json.load(f)
        assert "oauthAccount" not in data
        assert "theme" in data
    finally:
        tmp.unlink(missing_ok=True)


def test_merge_preserves_local_auth(tmp_path):
    local = tmp_path / "local.json"
    local.write_text(json.dumps({
        "oauthAccount": {"token": "local-secret"},
        "userID": "local-user",
        "theme": "light",
    }))

    pulled = tmp_path / "pulled.json"
    pulled.write_text(json.dumps({
        "theme": "dark",
        "numStartups": 42,
    }))

    merge_pulled_claude_json(pulled, local)

    with local.open() as f:
        merged = json.load(f)

    # Safe fields from remote override local
    assert merged["theme"] == "dark"
    assert merged["numStartups"] == 42
    # Local auth preserved (not overwritten by remote)
    assert merged["oauthAccount"] == {"token": "local-secret"}
    assert merged["userID"] == "local-user"


def test_merge_with_no_local_file(tmp_path):
    local = tmp_path / "missing.json"
    pulled = tmp_path / "pulled.json"
    pulled.write_text(json.dumps({"theme": "dark"}))

    merge_pulled_claude_json(pulled, local)

    with local.open() as f:
        merged = json.load(f)
    assert merged["theme"] == "dark"


def test_sanitize_strips_primary_api_key(full_claude_json):
    data = json.loads(full_claude_json.read_text())
    data["primaryApiKey"] = "sk-ant-secret"
    full_claude_json.write_text(json.dumps(data))

    result = sanitize_claude_json(full_claude_json)
    assert "primaryApiKey" not in result


def test_sanitize_strips_has_completed_onboarding(full_claude_json):
    data = json.loads(full_claude_json.read_text())
    data["hasCompletedOnboarding"] = True
    full_claude_json.write_text(json.dumps(data))

    result = sanitize_claude_json(full_claude_json)
    assert "hasCompletedOnboarding" not in result


def test_merge_raises_on_corrupted_pulled_json(tmp_path):
    local = tmp_path / "local.json"
    local.write_text(json.dumps({"key": "value"}))
    pulled = tmp_path / "pulled.json"
    pulled.write_text("this is not json{{{")

    with pytest.raises(ValueError, match="invalid JSON"):
        merge_pulled_claude_json(pulled, local)


def test_merge_raises_on_corrupted_local_json(tmp_path):
    local = tmp_path / "local.json"
    local.write_text("not json at all")
    pulled = tmp_path / "pulled.json"
    pulled.write_text(json.dumps({"key": "value"}))

    with pytest.raises(ValueError, match="invalid JSON"):
        merge_pulled_claude_json(pulled, local)


def test_merge_preserves_file_permissions(tmp_path):
    """merge_pulled_claude_json must preserve original file permissions."""
    local = tmp_path / "local.json"
    local.write_text(json.dumps({"showSpinnerTree": False}))
    local.chmod(0o600)

    pulled = tmp_path / "pulled.json"
    pulled.write_text(json.dumps({"showSpinnerTree": True}))

    merge_pulled_claude_json(pulled, local)

    import stat
    mode = local.stat().st_mode & 0o777
    assert mode == 0o600


def test_sanitize_rejects_non_dict_json(tmp_path):
    """sanitize_claude_json must reject JSON that is not an object."""
    source = tmp_path / ".claude.json"
    source.write_text(json.dumps([1, 2, 3]))

    with pytest.raises(ValueError, match="expected a JSON object"):
        sanitize_claude_json(source)


def test_merge_rejects_non_dict_remote(tmp_path):
    """merge_pulled_claude_json must reject a pulled file that is not a JSON object."""
    local = tmp_path / "local.json"
    local.write_text(json.dumps({"key": "val"}))
    pulled = tmp_path / "pulled.json"
    pulled.write_text(json.dumps([1, 2, 3]))

    with pytest.raises(ValueError, match="must be a JSON object"):
        merge_pulled_claude_json(pulled, local)


def test_merge_rejects_non_dict_local(tmp_path):
    """merge_pulled_claude_json must reject a local file that is not a JSON object."""
    local = tmp_path / "local.json"
    local.write_text(json.dumps([1, 2, 3]))
    pulled = tmp_path / "pulled.json"
    pulled.write_text(json.dumps({"key": "val"}))

    with pytest.raises(ValueError, match="must be a JSON object"):
        merge_pulled_claude_json(pulled, local)


def test_sanitizer_uses_allowlist_not_blocklist(tmp_path):
    """Unknown fields must be stripped, not passed through."""
    source = tmp_path / ".claude.json"
    source.write_text(json.dumps({
        "theme": "dark",
        "numStartups": 42,
        "unknownFutureField": "should-not-sync",
        "anotherNewField": {"nested": "data"},
    }))
    result = sanitize_claude_json(source)
    assert "theme" in result
    assert "numStartups" in result
    assert "unknownFutureField" not in result, (
        "Sanitizer must use allowlist — unknown fields must be stripped"
    )
    assert "anotherNewField" not in result


def test_sanitizer_strips_known_sensitive_fields(tmp_path):
    """All previously sensitive fields must still be absent."""
    source = tmp_path / ".claude.json"
    source.write_text(json.dumps({
        "oauthAccount": "token",
        "primaryApiKey": "sk-ant-xxxx",
        "userID": "user_123",
        "theme": "dark",
    }))
    result = sanitize_claude_json(source)
    assert "oauthAccount" not in result
    assert "primaryApiKey" not in result
    assert "userID" not in result
    assert "theme" in result


def test_sanitize_strips_sensitive_inside_list(tmp_path):
    """_strip_sensitive_nested must recurse into lists, not skip dicts inside them."""
    source = tmp_path / ".claude.json"
    source.write_text(json.dumps({
        "mcpServers": {
            "s": {
                "commands": [
                    {"env": {"SECRET": "x"}, "name": "cmd"}
                ]
            }
        }
    }))
    result = sanitize_claude_json(source)
    cmd = result["mcpServers"]["s"]["commands"][0]
    assert "env" not in cmd, "env inside a list element must be stripped"
    assert cmd["name"] == "cmd"
