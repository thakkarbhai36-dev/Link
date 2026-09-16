"""Deciding whether a post is worth engaging with.

The keyword pass is deterministic and free, and runs first. The model is only
asked about posts that survive it, which keeps the cost proportional to the
number of genuinely plausible candidates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..config import EngagementConfig
from ..logging_setup import get_logger
from .models import FeedPost

log = get_logger(__name__)

_WORD_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


@dataclass(slots=True)
class ScoredPost:
    post: FeedPost
    score: float
    reason: str

    @property
    def urn(self) -> str:
        return self.post.urn


def keyword_score(post: FeedPost, interests: list[str]) -> float:
    """Fraction of the configured interests the post appears to touch.

    A multi-word interest counts only when every one of its words appears, which
    keeps "api design" from matching a post that merely says "design".
    """
    if not interests:
        return 0.0

    post_tokens = _tokens(post.text)
    if not post_tokens:
        return 0.0

    hits = 0
    for interest in interests:
        needed = _tokens(interest)
        if needed and needed.issubset(post_tokens):
            hits += 1

    # One strong match on a short interest list should already be meaningful,
    # so the curve rewards the first hit more than the fifth.
    raw = hits / len(interests)
    return min(1.0, raw**0.5)


def passes_filters(post: FeedPost, config: EngagementConfig) -> tuple[bool, str]:
    """Cheap disqualifications, checked before anything is scored."""
    if post.is_promoted:
        return False, "promoted post"

    if config.first_degree_only and not post.is_first_degree:
        return False, "author is not a first-degree connection"

    if not post.text.strip():
        return False, "post has no readable text"

    lowered = post.text.lower()
    for keyword in config.exclude_keywords:
        if keyword.lower() in lowered:
            return False, f"matched excluded keyword {keyword!r}"

    if post.age_hours is not None and post.age_hours > config.max_post_age_hours:
        return False, f"older than {config.max_post_age_hours}h"

    return True, ""


def score_post(
    post: FeedPost,
    config: EngagementConfig,
    llm_scorer=None,
) -> ScoredPost:
    """Score one post, optionally asking the model to arbitrate.

    llm_scorer is any callable taking (author, post_text) and returning an object
    with .score and .reason. It is injected rather than imported so this module
    stays testable without network access.
    """
    allowed, why = passes_filters(post, config)
    if not allowed:
        return ScoredPost(post, 0.0, why)

    kw = keyword_score(post, config.interests)

    if llm_scorer is None:
        return ScoredPost(post, kw, f"keyword match {kw:.2f}")

    try:
        judged = llm_scorer(author=post.author_name, post_text=post.text)
    except Exception as exc:  # A scoring failure must not abort the whole scan.
        log.warning("Relevance scoring failed for %s: %s", post.urn, exc)
        return ScoredPost(post, kw, f"keyword match {kw:.2f} (model scoring failed)")

    # Keep the keyword signal in the mix: it is the one that cannot hallucinate.
    blended = 0.35 * kw + 0.65 * float(judged.score)
    return ScoredPost(post, blended, judged.reason)
