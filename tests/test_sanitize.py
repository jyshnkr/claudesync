"""Tests for ~/.claude.json sanitization."""
import json
import pytest
from pathlib import Path

from claudesync.sanitize import (
    sanitize_claude_json,
    write_sanitized_temp,
    merge_pulled_claude_json,
    SENSITIVE_FIELDS,
)


@pytest.fixture
def full_claude_json(tmp_path) -> Path:
    data = {
        "oauthAccount": {"token": "secret-token"},
        "userID": "user-123",
        "cachedGrowthBookFeatures": {"feature": True},
        "passesEligibilityCache": True,
        "showSpinnerTree": True,
        "skillUsage": {"my-skill": 5},
        "lastPlanModeUse": "2026-02-28",
        "projects": {"/path/to/project": {"enabled": True}},
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


def test_sanitize_keeps_ui_prefs(full_claude_json):
    result = sanitize_claude_json(full_claude_json)
    assert result["showSpinnerTree"] is True
    assert result["skillUsage"] == {"my-skill": 5}
    assert result["lastPlanModeUse"] == "2026-02-28"


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
        assert "showSpinnerTree" in data
    finally:
        tmp.unlink(missing_ok=True)


def test_merge_preserves_local_auth(tmp_path):
    local = tmp_path / "local.json"
    local.write_text(json.dumps({
        "oauthAccount": {"token": "local-secret"},
        "userID": "local-user",
        "showSpinnerTree": False,
    }))

    pulled = tmp_path / "pulled.json"
    pulled.write_text(json.dumps({
        "showSpinnerTree": True,
        "skillUsage": {"new-skill": 3},
    }))

    merge_pulled_claude_json(pulled, local)

    with local.open() as f:
        merged = json.load(f)

    # Remote prefs should win
    assert merged["showSpinnerTree"] is True
    assert merged["skillUsage"] == {"new-skill": 3}
    # Local auth preserved
    assert merged["oauthAccount"] == {"token": "local-secret"}
    assert merged["userID"] == "local-user"


def test_merge_with_no_local_file(tmp_path):
    local = tmp_path / "missing.json"
    pulled = tmp_path / "pulled.json"
    pulled.write_text(json.dumps({"showSpinnerTree": True}))

    merge_pulled_claude_json(pulled, local)

    with local.open() as f:
        merged = json.load(f)
    assert merged["showSpinnerTree"] is True


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
