"""Reading posts out of the LinkedIn feed."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..errors import BrowserError
from ..logging_setup import get_logger
from . import selectors
from .browser import BrowserSession
from .models import FeedPost

if TYPE_CHECKING:  # pragma: no cover - typing only
    from playwright.sync_api import Locator

log = get_logger(__name__)

# Longer unit names first: "mo" must win over "m", or "2mo" reads as 2 minutes.
_AGE_RE = re.compile(
    r"(\d+)\s*(seconds?|minutes?|hours?|days?|weeks?|months?|years?|mo|s|m|h|d|w|y)\b", re.I
)
_UNIT_HOURS = {
    "second": 1 / 3600,
    "s": 1 / 3600,
    "minute": 1 / 60,
    "m": 1 / 60,
    "hour": 1.0,
    "h": 1.0,
    "day": 24.0,
    "d": 24.0,
    "week": 168.0,
    "w": 168.0,
    "month": 720.0,
    "mo": 720.0,
    "year": 8760.0,
    "y": 8760.0,
}


def parse_age_hours(label: str) -> float | None:
    """Turn LinkedIn's relative timestamp into hours.

    Handles both the compact feed form ("3h", "2w") and the long form
    ("3 hours ago"). Returns None when nothing recognisable is present.
    """
    if not label:
        return None
    match = _AGE_RE.search(label)
    if not match:
        return None
    amount, unit = match.groups()
    # "m" is ambiguous in LinkedIn's compact labels: it means minutes there, and
    # months are written "mo", which the alternation above matches first.
    multiplier = _UNIT_HOURS.get(unit.lower().rstrip("s") or unit.lower())
    if multiplier is None:
        return None
    return float(amount) * multiplier


def _first_text(container: Locator, candidates: tuple[str, ...]) -> str:
    for selector in candidates:
        try:
            locator = container.locator(selector).first
            if locator.count() > 0:
                text = locator.inner_text(timeout=2000).strip()
                if text:
                    return text
        except Exception:
            continue
    return ""


def _first_attribute(container: Locator, candidates: tuple[str, ...], attribute: str) -> str:
    for selector in candidates:
        try:
            locator = container.locator(selector).first
            if locator.count() > 0:
                value = locator.get_attribute(attribute, timeout=2000)
                if value:
                    return value.strip()
        except Exception:
            continue
    return ""


def _container_urn(container: Locator) -> str:
    for attribute in selectors.URN_ATTRIBUTES:
        try:
            value = container.get_attribute(attribute, timeout=2000)
        except Exception:
            continue
        if value and value.startswith("urn:li:"):
            return value
    return ""


def _already_liked(container: Locator) -> bool:
    for selector in selectors.LIKE_BUTTON:
        try:
            button = container.locator(selector).first
            if button.count() == 0:
                continue
            if (button.get_attribute("aria-pressed", timeout=2000) or "").lower() == "true":
                return True
        except Exception:
            continue
    return False


def _expand(container: Locator) -> None:
    """Click "…see more" so the full body is readable."""
    for selector in selectors.SEE_MORE_BUTTON:
        try:
            button = container.locator(selector).first
            if button.count() > 0 and button.is_visible():
                button.click(timeout=2000)
                return
        except Exception:
            continue


def _extract(container: Locator) -> FeedPost | None:
    urn = _container_urn(container)
    if not urn:
        return None

    _expand(container)

    text = _first_text(container, selectors.POST_TEXT)
    author_name = _first_text(container, selectors.AUTHOR_NAME)
    description = _first_text(container, selectors.AUTHOR_DESCRIPTION)
    age_label = _first_text(container, selectors.POST_AGE)
    profile_url = _first_attribute(container, selectors.AUTHOR_LINK, "href").split("?")[0]

    combined = f"{description} {age_label}".lower()

    return FeedPost(
        urn=urn,
        author_name=author_name,
        author_profile_url=profile_url,
        text=text,
        posted_age=age_label,
        age_hours=parse_age_hours(age_label),
        is_promoted="promoted" in combined or "sponsored" in combined,
        # LinkedIn writes the degree into the actor description, e.g. "· 1st".
        is_first_degree="1st" in description,
        already_liked=_already_liked(container),
    )


def read_feed(session: BrowserSession, limit: int = 40, *, debug: bool = False) -> list[FeedPost]:
    """Scroll the main feed and return up to `limit` posts.

    Raises BrowserError when no post container matches any known selector, since
    that means the page layout moved rather than that the feed is empty.
    """
    if session.page is None:
        raise BrowserError("Browser session is not open")

    page = session.page
    session.goto_feed()

    container_selector = ""
    for candidate in selectors.POST_CONTAINERS:
        try:
            page.wait_for_selector(candidate, timeout=10_000)
            container_selector = candidate
            break
        except Exception:
            continue

    if not container_selector:
        if debug:
            session.save_debug_snapshot("feed-no-containers")
        raise BrowserError(
            "No feed posts matched any known selector. LinkedIn's layout has "
            "probably changed. Re-run with --debug to save a screenshot, then "
            "update linkedin_autopilot/engagement/selectors.py."
        )

    posts: dict[str, FeedPost] = {}
    stalls = 0

    while len(posts) < limit and stalls < 4:
        containers = page.locator(container_selector)
        count = containers.count()

        for index in range(count):
            if len(posts) >= limit:
                break
            try:
                post = _extract(containers.nth(index))
            except Exception as exc:
                log.debug("skipped a feed item: %s", exc)
                continue
            if post and post.urn not in posts:
                posts[post.urn] = post

        before = len(posts)
        page.mouse.wheel(0, 2400)
        page.wait_for_timeout(1500)
        stalls = stalls + 1 if len(posts) == before else 0

    log.info("Read %d posts from the feed", len(posts))
    if debug and not posts:
        session.save_debug_snapshot("feed-empty")
    return list(posts.values())
