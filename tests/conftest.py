from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from linkedin_autopilot.config import Config
from linkedin_autopilot.engagement.models import FeedPost
from linkedin_autopilot.safety import Guard
from linkedin_autopilot.store import Store


@pytest.fixture()
def config(tmp_path: Path) -> Config:
    cfg = Config.model_validate(
        {
            "timezone": "UTC",
            "dry_run": True,
            "state_dir": str(tmp_path / "state"),
            "kill_switch_file": str(tmp_path / "state" / "STOP"),
            "posting": {
                "enabled": True,
                "topics": ["Databases", "Code review", "Incidents"],
                "topic_cooldown_days": 7,
                "banned_phrases": ["thrilled to announce"],
                "hashtags": {"enabled": True, "max": 2, "pool": ["#backend"]},
            },
            "engagement": {
                "enabled": True,
                "mode": "suggest",
                "interests": ["postgres", "api design"],
                "exclude_keywords": ["hiring"],
                "min_relevance": 0.5,
            },
            "safety": {
                "active_hours": {"start": "00:00", "end": "23:59"},
                "max_actions_per_day": 10,
                "min_seconds_between_actions": 0,
                "max_seconds_between_actions": 0,
            },
        }
    )
    cfg.ensure_dirs()
    return cfg


@pytest.fixture()
def store(config: Config) -> Store:
    return Store(config.db_path)


@pytest.fixture()
def guard(config: Config, store: Store) -> Guard:
    return Guard(config, store)


@pytest.fixture()
def today() -> date:
    return date.today()


def make_post(**overrides) -> FeedPost:
    defaults = {
        "urn": "urn:li:activity:1",
        "author_name": "Alex Doe",
        "author_profile_url": "https://www.linkedin.com/in/alexdoe",
        "text": "A long post about postgres index bloat and how we measured it in production.",
        "posted_age": "3h",
        "age_hours": 3.0,
        "is_promoted": False,
        "is_first_degree": True,
    }
    defaults.update(overrides)
    return FeedPost(**defaults)
