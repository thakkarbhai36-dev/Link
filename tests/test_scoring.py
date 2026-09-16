from __future__ import annotations

from dataclasses import dataclass

from linkedin_autopilot.config import EngagementConfig
from linkedin_autopilot.engagement.feed import parse_age_hours
from linkedin_autopilot.engagement.scoring import keyword_score, passes_filters, score_post

from .conftest import make_post


@dataclass
class FakeScore:
    score: float
    reason: str = "fake"


def engagement() -> EngagementConfig:
    return EngagementConfig(
        enabled=True,
        mode="suggest",
        interests=["postgres", "api design"],
        exclude_keywords=["hiring"],
        min_relevance=0.5,
        max_post_age_hours=48,
    )


def test_keyword_score_needs_every_word_of_an_interest() -> None:
    post = make_post(text="Some thoughts on design in general.")
    assert keyword_score(post, ["api design"]) == 0.0

    post = make_post(text="Some thoughts on api design in general.")
    assert keyword_score(post, ["api design"]) > 0.0


def test_keyword_score_is_zero_without_interests() -> None:
    assert keyword_score(make_post(), []) == 0.0


def test_keyword_score_is_bounded() -> None:
    post = make_post(text="postgres api design postgres api design")
    assert 0.0 <= keyword_score(post, ["postgres", "api design"]) <= 1.0


def test_promoted_posts_are_filtered() -> None:
    allowed, reason = passes_filters(make_post(is_promoted=True), engagement())
    assert not allowed
    assert "promoted" in reason


def test_non_first_degree_filtered_when_configured() -> None:
    config = engagement()
    config.first_degree_only = True
    allowed, reason = passes_filters(make_post(is_first_degree=False), config)
    assert not allowed
    assert "first-degree" in reason


def test_excluded_keyword_filters_post() -> None:
    allowed, reason = passes_filters(make_post(text="We are hiring engineers"), engagement())
    assert not allowed
    assert "hiring" in reason


def test_old_posts_are_filtered() -> None:
    allowed, reason = passes_filters(make_post(age_hours=200.0), engagement())
    assert not allowed
    assert "older than" in reason


def test_filtered_post_scores_zero() -> None:
    scored = score_post(make_post(is_promoted=True), engagement())
    assert scored.score == 0.0


def test_llm_score_is_blended_with_keywords() -> None:
    post = make_post(text="postgres index bloat and api design tradeoffs")
    keyword_only = score_post(post, engagement()).score
    blended = score_post(post, engagement(), llm_scorer=lambda **_: FakeScore(0.2)).score
    assert blended < keyword_only


def test_scoring_survives_a_failing_scorer() -> None:
    def explode(**_):
        raise RuntimeError("model unavailable")

    scored = score_post(make_post(), engagement(), llm_scorer=explode)
    assert scored.score >= 0.0
    assert "failed" in scored.reason


def test_parse_age_hours() -> None:
    assert parse_age_hours("3h") == 3.0
    assert parse_age_hours("45m") == 45 / 60
    assert parse_age_hours("2w") == 336.0
    assert parse_age_hours("1mo") == 720.0
    assert parse_age_hours("5 hours ago") == 5.0
    assert parse_age_hours("now") is None
    assert parse_age_hours("") is None
