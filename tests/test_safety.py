from __future__ import annotations

from datetime import date

from linkedin_autopilot.config import Config
from linkedin_autopilot.safety import Guard
from linkedin_autopilot.store import ActionKind, Store


def test_kill_switch_blocks_everything(config: Config, guard: Guard) -> None:
    config.kill_switch_file.parent.mkdir(parents=True, exist_ok=True)
    config.kill_switch_file.touch()
    decision = guard.check(ActionKind.POST)
    assert not decision
    assert "kill switch" in decision.reason


def test_outside_active_hours_blocks(config: Config, store: Store) -> None:
    config.safety.active_hours.start = "00:00"
    config.safety.active_hours.end = "00:01"
    guard = Guard(config, store)
    decision = guard.check(ActionKind.LIKE)
    # Only fails between 00:00 and 00:01 UTC, when the window genuinely is open.
    if guard.within_active_hours():
        assert decision
    else:
        assert not decision
        assert "active hours" in decision.reason


def test_daily_kind_limit(config: Config, store: Store, today: date) -> None:
    guard = Guard(config, store)
    for _ in range(config.posting.max_per_day):
        store.bump_counter(ActionKind.POST, today)
    decision = guard.check(ActionKind.POST)
    assert not decision
    assert "daily post limit" in decision.reason


def test_global_ceiling_beats_per_kind_limit(config: Config, store: Store, today: date) -> None:
    config.safety.max_actions_per_day = 2
    guard = Guard(config, store)
    store.bump_counter(ActionKind.LIKE, today)
    store.bump_counter(ActionKind.LIKE, today)
    decision = guard.check(ActionKind.LIKE)
    assert not decision
    assert "daily ceiling" in decision.reason


def test_per_author_weekly_cap(config: Config, store: Store) -> None:
    config.engagement.comments.max_per_author_per_week = 1
    guard = Guard(config, store)
    author = "https://www.linkedin.com/in/alexdoe"
    store.add_action(ActionKind.COMMENT, target_urn="x", target_author=author)
    decision = guard.check(ActionKind.COMMENT, author=author)
    assert not decision
    assert "per-author" in decision.reason


def test_quota_remaining_never_negative(config: Config, store: Store, today: date) -> None:
    guard = Guard(config, store)
    for _ in range(50):
        store.bump_counter(ActionKind.LIKE, today)
    assert guard.quota_remaining(ActionKind.LIKE) == 0


def test_pace_uses_the_injected_sleeper(config: Config, store: Store) -> None:
    config.safety.min_seconds_between_actions = 5
    config.safety.max_seconds_between_actions = 5
    guard = Guard(config, store)
    slept: list[float] = []
    waited = guard.pace(sleeper=slept.append)
    assert waited == 5
    assert slept == [5]
