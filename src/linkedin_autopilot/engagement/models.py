"""Data shapes shared across the engagement package."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass(slots=True)
class FeedPost:
    """One post as read from the feed."""

    urn: str
    author_name: str
    text: str
    author_profile_url: str = ""
    posted_age: str = ""  # LinkedIn's own relative label, e.g. "3h"
    age_hours: float | None = None
    is_promoted: bool = False
    is_first_degree: bool = False
    already_liked: bool = False
    scraped_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def excerpt(self) -> str:
        flat = " ".join(self.text.split())
        return flat[:180] + ("…" if len(flat) > 180 else "")

    @property
    def author_key(self) -> str:
        """Stable identifier for per-author caps. The profile URL is more
        reliable than the display name, which is not unique."""
        return self.author_profile_url or self.author_name
