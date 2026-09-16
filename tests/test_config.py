from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from linkedin_autopilot.config import Config, load_config
from linkedin_autopilot.errors import ConfigError


def test_missing_file_names_the_fix(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="autopilot init"):
        load_config(tmp_path / "nope.yaml")


def test_rejects_unknown_timezone(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"timezone": "Mars/Olympus"}))
    with pytest.raises(ConfigError, match="timezone"):
        load_config(path)


def test_rejects_bad_day_names(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump({"posting": {"enabled": True, "topics": ["x"], "days": ["funday"]}})
    )
    with pytest.raises(ConfigError, match="unknown day"):
        load_config(path)


def test_posting_requires_topics(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"posting": {"enabled": True, "topics": []}}))
    with pytest.raises(ConfigError, match="topics"):
        load_config(path)


def test_engagement_requires_interests(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump({"engagement": {"enabled": True, "mode": "suggest", "interests": []}})
    )
    with pytest.raises(ConfigError, match="interests"):
        load_config(path)


def test_secrets_in_file_are_ignored(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("LINKEDIN_CLIENT_ID", raising=False)
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "timezone": "UTC",
                "state_dir": str(tmp_path / "state"),
                "posting": {"topics": ["Databases"]},
                "secrets": {"linkedin_client_id": "leaked-from-yaml"},
            }
        )
    )
    config = load_config(path)
    assert config.secrets.linkedin_client_id is None


def test_active_hours_must_be_ordered() -> None:
    with pytest.raises(ValidationError, match="earlier than"):
        Config.model_validate({"safety": {"active_hours": {"start": "20:00", "end": "08:00"}}})


def test_suggest_mode_never_writes() -> None:
    config = Config.model_validate(
        {"engagement": {"enabled": True, "mode": "suggest", "interests": ["x"]}}
    )
    assert config.engagement.writes_allowed is False


def test_auto_mode_writes() -> None:
    config = Config.model_validate(
        {"engagement": {"enabled": True, "mode": "auto", "interests": ["x"]}}
    )
    assert config.engagement.writes_allowed is True
