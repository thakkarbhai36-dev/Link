"""Browser-backed test of the feed scraper.

Runs the real extraction code against a fixture page that mimics LinkedIn's
markup, in a real Chromium. This is the only test that needs a browser; it
skips cleanly when Playwright or a Chromium build is unavailable, so the rest
of the suite still runs anywhere.

The fixture is not LinkedIn's actual HTML and will drift from it. What this
proves is that the selector tuples, the attribute lookups, the relative-time
parser, and the filter chain fit together — not that the selectors still match
production.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from linkedin_autopilot.config import EngagementConfig
from linkedin_autopilot.engagement import feed
from linkedin_autopilot.engagement.scoring import score_post

pytest.importorskip("playwright", reason="browser extra not installed")

from playwright.sync_api import sync_playwright  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "feed_sample.html"

# Where a prebuilt Chromium lives when it is not in Playwright's default spot.
_CANDIDATE_BINARIES = (
    os.environ.get("CHROMIUM_EXECUTABLE", ""),
    "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
)


class _FakeSession:
    """Stands in for BrowserSession; read_feed only uses these three members."""

    def __init__(self, page, url: str) -> None:
        self.page = page
        self._url = url

    def goto_feed(self) -> None:
        self.page.goto(self._url, wait_until="domcontentloaded")

    def save_debug_snapshot(self, label: str) -> None:
        return None


def _launch(playwright):
    """Launch Chromium however this machine happens to have it."""
    attempts = [lambda: playwright.chromium.launch(headless=True, args=["--no-sandbox"])]
    for binary in _CANDIDATE_BINARIES:
        if binary and Path(binary).exists():
            attempts.append(
                lambda b=binary: playwright.chromium.launch(
                    headless=True, executable_path=b, args=["--no-sandbox"]
                )
            )
    last: Exception | None = None
    for attempt in reversed(attempts):
        try:
            return attempt()
        except Exception as exc:
            last = exc
    pytest.skip(f"no usable Chromium: {last}")


@pytest.fixture(scope="module")
def posts():
    url = FIXTURE.resolve().as_uri()
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        try:
            page = browser.new_page()
            return feed.read_feed(_FakeSession(page, url), limit=10)
        finally:
            browser.close()


@pytest.fixture()
def engagement() -> EngagementConfig:
    return EngagementConfig(
        enabled=True,
        mode="suggest",
        interests=["postgres", "api design", "incident response"],
        exclude_keywords=["hiring", "comment below to receive"],
        min_relevance=0.5,
        max_post_age_hours=48,
        first_degree_only=True,
    )


def by_urn(posts, suffix: str):
    return next(post for post in posts if post.urn.endswith(suffix))


def test_every_post_is_found(posts) -> None:
    assert len(posts) == 4
    assert all(post.urn.startswith("urn:li:activity:") for post in posts)


def test_author_and_text_are_read(posts) -> None:
    post = by_urn(posts, "0001")
    assert post.author_name == "Alex Doe"
    assert "index bloat" in post.text


def test_tracking_parameters_are_stripped_from_profile_urls(posts) -> None:
    assert by_urn(posts, "0001").author_profile_url.endswith("/in/alexdoe")


def test_connection_degree_is_detected(posts) -> None:
    assert by_urn(posts, "0001").is_first_degree is True
    assert by_urn(posts, "0003").is_first_degree is False


def test_promoted_posts_are_flagged(posts) -> None:
    assert by_urn(posts, "0002").is_promoted is True
    assert by_urn(posts, "0001").is_promoted is False


def test_relative_ages_are_parsed(posts) -> None:
    assert by_urn(posts, "0001").age_hours == 3.0
    assert by_urn(posts, "0003").age_hours == 48.0
    assert by_urn(posts, "0004").age_hours == 720.0


def test_existing_reaction_is_detected(posts) -> None:
    assert by_urn(posts, "0003").already_liked is True
    assert by_urn(posts, "0001").already_liked is False


def test_only_the_relevant_post_survives_scoring(posts, engagement) -> None:
    kept = [
        post for post in posts if score_post(post, engagement).score >= engagement.min_relevance
    ]
    assert [post.urn[-4:] for post in kept] == ["0001"]


def test_each_rejection_gives_a_reason(posts, engagement) -> None:
    reasons = {post.urn[-4:]: score_post(post, engagement).reason for post in posts}
    assert "promoted" in reasons["0002"]
    assert "first-degree" in reasons["0003"]
    assert "older than" in reasons["0004"]
