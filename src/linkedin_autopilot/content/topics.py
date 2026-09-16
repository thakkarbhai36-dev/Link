"""Topic rotation.

Picks the next topic that has not been used inside the cooldown window, so a
week of posts does not circle one subject.
"""

from __future__ import annotations

from ..config import PostingConfig
from ..logging_setup import get_logger
from ..store import Store

log = get_logger(__name__)


def choose_topic(config: PostingConfig, store: Store) -> str:
    """Return the next topic to write about.

    Falls back to the least recently used topic when every topic is inside the
    cooldown, which is what happens once the list is shorter than the window.
    """
    if not config.topics:
        raise ValueError("posting.topics is empty; nothing to write about")

    recent = store.recent_topics(config.topic_cooldown_days)
    available = [topic for topic in config.topics if topic not in recent]

    if available:
        return available[0]

    log.info(
        "Every topic was used within the last %d days; reusing the oldest",
        config.topic_cooldown_days,
    )
    last_used = store.topic_last_used()
    # A topic absent from history sorts first: never used beats long ago.
    return min(config.topics, key=lambda topic: last_used.get(topic, ""))
