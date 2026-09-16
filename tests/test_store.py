from __future__ import annotations

from datetime import date

from linkedin_autopilot.store import ActionKind, ActionStatus, Store


def test_action_roundtrip(store: Store) -> None:
    action_id = store.add_action(
        ActionKind.COMMENT,
        target_urn="urn:li:activity:99",
        target_author="https://www.linkedin.com/in/someone",
        body="A specific thing about index bloat.",
        context={"score": 0.81},
    )
    action = store.get_action(action_id)
    assert action is not None
    assert action.kind == "comment"
    assert action.status == "pending"
    assert action.context["score"] == 0.81


def test_update_action_changes_status(store: Store) -> None:
    action_id = store.add_action(ActionKind.POST, body="hello")
    store.update_action(action_id, status=ActionStatus.DONE, result_urn="urn:li:share:1")
    action = store.get_action(action_id)
    assert action is not None
    assert action.status == "done"
    assert action.result_urn == "urn:li:share:1"


def test_seen_posts_dedupe(store: Store) -> None:
    assert store.has_seen("urn:li:activity:1") is False
    store.mark_seen("urn:li:activity:1", "author", "scored 0.2")
    store.mark_seen("urn:li:activity:1", "author", "scored again")
    assert store.has_seen("urn:li:activity:1") is True


def test_already_acted_ignores_rejected(store: Store) -> None:
    action_id = store.add_action(ActionKind.LIKE, target_urn="urn:li:activity:7")
    assert store.already_acted("urn:li:activity:7", ActionKind.LIKE) is True
    store.update_action(action_id, status=ActionStatus.REJECTED)
    assert store.already_acted("urn:li:activity:7", ActionKind.LIKE) is False


def test_counters_are_per_day_and_kind(store: Store, today: date) -> None:
    store.bump_counter(ActionKind.LIKE, today)
    store.bump_counter(ActionKind.LIKE, today)
    store.bump_counter(ActionKind.POST, today)
    assert store.counter(ActionKind.LIKE, today) == 2
    assert store.counter(ActionKind.POST, today) == 1
    assert store.total_actions_today(today) == 3


def test_author_action_count_only_counts_live_actions(store: Store) -> None:
    author = "https://www.linkedin.com/in/alexdoe"
    store.add_action(ActionKind.COMMENT, target_urn="a", target_author=author)
    rejected = store.add_action(ActionKind.COMMENT, target_urn="b", target_author=author)
    store.update_action(rejected, status=ActionStatus.REJECTED)
    assert store.author_action_count(author, ActionKind.COMMENT) == 1


def test_recent_topics_and_last_used(store: Store) -> None:
    store.record_topic("Databases")
    assert "Databases" in store.recent_topics(within_days=7)
    assert store.recent_topics(within_days=0) == set()
    assert "Databases" in store.topic_last_used()
