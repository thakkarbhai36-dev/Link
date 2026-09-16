from __future__ import annotations

from linkedin_autopilot.config import Config
from linkedin_autopilot.content.topics import choose_topic
from linkedin_autopilot.store import Store


def test_first_topic_chosen_when_history_is_empty(config: Config, store: Store) -> None:
    assert choose_topic(config.posting, store) == "Databases"


def test_used_topics_are_skipped(config: Config, store: Store) -> None:
    store.record_topic("Databases")
    assert choose_topic(config.posting, store) == "Code review"


def test_falls_back_to_least_recently_used(config: Config, store: Store) -> None:
    for topic in config.posting.topics:
        store.record_topic(topic)
    # Everything is inside the cooldown, so the oldest entry wins.
    assert choose_topic(config.posting, store) == "Databases"


def test_zero_cooldown_always_returns_the_first(config: Config, store: Store) -> None:
    config.posting.topic_cooldown_days = 0
    store.record_topic("Databases")
    assert choose_topic(config.posting, store) == "Databases"
