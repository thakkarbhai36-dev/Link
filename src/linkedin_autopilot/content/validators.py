"""Checks that run on every generated draft before a human ever sees it.

These are cheap, deterministic, and unforgiving. A model that drifts into
LinkedIn-voice boilerplate gets caught here rather than on your feed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..config import CommentLimits, PostingConfig

_URL_RE = re.compile(r"https?://|www\.", re.IGNORECASE)
_HASHTAG_RE = re.compile(r"#\w+")
_MENTION_RE = re.compile(r"@\w+")

# Comments that say nothing. If a draft is only this, it is noise, and posting
# noise under your name is worse than posting nothing.
_GENERIC_OPENERS = (
    "great post",
    "great share",
    "well said",
    "couldn't agree more",
    "could not agree more",
    "so true",
    "thanks for sharing",
    "this is gold",
    "love this",
    "spot on",
    "absolutely",
    "100%",
    "very insightful",
    "great insights",
    "amazing",
    "congratulations",
)


@dataclass(slots=True)
class ValidationResult:
    ok: bool
    problems: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.ok

    @classmethod
    def passed(cls) -> ValidationResult:
        return cls(True)

    @classmethod
    def failed(cls, problems: list[str]) -> ValidationResult:
        return cls(False, problems)

    @property
    def summary(self) -> str:
        return "; ".join(self.problems)


def _contains_banned(text: str, banned: list[str]) -> list[str]:
    lowered = text.lower()
    return [phrase for phrase in banned if phrase.lower() in lowered]


def validate_post(text: str, config: PostingConfig) -> ValidationResult:
    problems: list[str] = []
    stripped = text.strip()

    if not stripped:
        problems.append("the draft is empty")
    if len(stripped) > config.max_chars:
        problems.append(f"{len(stripped)} characters, over the {config.max_chars} limit")
    if len(stripped) < 80:
        problems.append(f"only {len(stripped)} characters, too short to be worth posting")

    for phrase in _contains_banned(stripped, config.banned_phrases):
        problems.append(f"contains the banned phrase {phrase!r}")

    hashtags = _HASHTAG_RE.findall(stripped)
    if not config.hashtags.enabled and hashtags:
        problems.append("hashtags are disabled but the draft contains some")
    elif len(hashtags) > config.hashtags.max:
        problems.append(f"{len(hashtags)} hashtags, over the limit of {config.hashtags.max}")

    return ValidationResult.passed() if not problems else ValidationResult.failed(problems)


def is_generic(text: str) -> bool:
    """True when a comment is flattery with no substance.

    A draft that merely opens with a pleasantry is fine; one that is nothing but
    pleasantry is not. The test strips known openers and asks what is left.
    """
    lowered = text.strip().lower()
    remainder = lowered
    for opener in _GENERIC_OPENERS:
        remainder = remainder.replace(opener, " ")
    remainder = re.sub(r"[^a-z0-9]+", " ", remainder).strip()
    return len(remainder.split()) < 12


def validate_comment(text: str, limits: CommentLimits) -> ValidationResult:
    problems: list[str] = []
    stripped = text.strip()

    if not stripped:
        problems.append("the draft is empty")
    if len(stripped) < limits.min_chars:
        problems.append(f"{len(stripped)} characters, under the {limits.min_chars} minimum")
    if len(stripped) > limits.max_chars:
        problems.append(f"{len(stripped)} characters, over the {limits.max_chars} maximum")
    if limits.no_links and _URL_RE.search(stripped):
        problems.append("contains a link")
    if limits.no_hashtags and _HASHTAG_RE.search(stripped):
        problems.append("contains a hashtag")
    if _MENTION_RE.search(stripped):
        problems.append("contains an @mention, which this tool will not resolve correctly")
    if limits.reject_if_generic and is_generic(stripped):
        problems.append("is generic praise with no specific content")

    return ValidationResult.passed() if not problems else ValidationResult.failed(problems)
