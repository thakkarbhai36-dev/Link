from __future__ import annotations

from linkedin_autopilot.config import CommentLimits, PostingConfig
from linkedin_autopilot.content.validators import (
    is_generic,
    validate_comment,
    validate_post,
)


def posting() -> PostingConfig:
    return PostingConfig(
        enabled=True,
        topics=["x"],
        max_chars=300,
        banned_phrases=["thrilled to announce"],
        hashtags={"enabled": True, "max": 2, "pool": []},
    )


def limits() -> CommentLimits:
    return CommentLimits(min_chars=40, max_chars=300)


GOOD_POST = (
    "We lost forty minutes of writes last Tuesday because an index rebuild "
    "took a lock nobody expected. The fix was boring: check pg_locks before "
    "you start, not after the pager goes off."
)


def test_good_post_passes() -> None:
    assert validate_post(GOOD_POST, posting())


def test_banned_phrase_rejected() -> None:
    result = validate_post(f"{GOOD_POST} I am thrilled to announce it.", posting())
    assert not result
    assert "banned phrase" in result.summary


def test_too_long_rejected() -> None:
    result = validate_post("x" * 400, posting())
    assert not result
    assert "over the 300 limit" in result.summary


def test_too_short_rejected() -> None:
    result = validate_post("Short.", posting())
    assert not result
    assert "too short" in result.summary


def test_too_many_hashtags_rejected() -> None:
    result = validate_post(f"{GOOD_POST} #a #b #c", posting())
    assert not result
    assert "hashtags" in result.summary


def test_hashtags_rejected_when_disabled() -> None:
    config = posting()
    config.hashtags.enabled = False
    result = validate_post(f"{GOOD_POST} #a", config)
    assert not result


GOOD_COMMENT = (
    "The pg_locks check is the part people skip. We added it to the runbook "
    "after a similar outage and it caught two bad rebuilds since."
)


def test_good_comment_passes() -> None:
    assert validate_comment(GOOD_COMMENT, limits())


def test_generic_praise_is_detected() -> None:
    assert is_generic("Great post! Couldn't agree more.")
    assert is_generic("So true. Thanks for sharing!")
    assert not is_generic(GOOD_COMMENT)


def test_generic_comment_rejected() -> None:
    result = validate_comment("Great post! Well said, couldn't agree more here.", limits())
    assert not result


def test_links_rejected() -> None:
    result = validate_comment(f"{GOOD_COMMENT} See https://example.com", limits())
    assert not result
    assert "link" in result.summary


def test_hashtags_rejected_in_comments() -> None:
    result = validate_comment(f"{GOOD_COMMENT} #postgres", limits())
    assert not result
    assert "hashtag" in result.summary


def test_mentions_rejected_in_comments() -> None:
    result = validate_comment(f"{GOOD_COMMENT} cc @someone", limits())
    assert not result
    assert "mention" in result.summary


def test_short_comment_rejected() -> None:
    result = validate_comment("Nice.", limits())
    assert not result
