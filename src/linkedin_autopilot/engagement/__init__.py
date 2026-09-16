"""Reading the feed and acting on other people's posts.

Everything in this package drives a real browser session, because LinkedIn's
public API does not expose feed reading, likes, or comments on other members'
posts to individual developers. See docs/ENGAGEMENT.md before enabling it.
"""

from .models import FeedPost
from .scoring import keyword_score, passes_filters, score_post

__all__ = ["FeedPost", "keyword_score", "passes_filters", "score_post"]
