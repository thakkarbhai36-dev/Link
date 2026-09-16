"""Performing a like or a comment in the browser.

Every function here is a no-op when dry_run is set: it locates the control,
confirms it could act, and reports back without clicking. That path is worth
exercising first, because it proves the selectors work before anything becomes
visible on somebody else's post.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..errors import BrowserError
from ..logging_setup import get_logger
from . import selectors
from .browser import BrowserSession

if TYPE_CHECKING:  # pragma: no cover - typing only
    from playwright.sync_api import Locator

log = get_logger(__name__)


@dataclass(slots=True)
class ActionOutcome:
    performed: bool
    detail: str

    @classmethod
    def did(cls, detail: str) -> ActionOutcome:
        return cls(True, detail)

    @classmethod
    def skipped(cls, detail: str) -> ActionOutcome:
        return cls(False, detail)


def _post_url(urn: str) -> str:
    return f"https://www.linkedin.com/feed/update/{urn}/"


def _open_post(session: BrowserSession, urn: str) -> Locator:
    """Navigate to a single post and return its container."""
    if session.page is None:
        raise BrowserError("Browser session is not open")

    page = session.page
    page.goto(_post_url(urn), wait_until="domcontentloaded")
    page.wait_for_timeout(1200)

    for candidate in selectors.POST_CONTAINERS:
        try:
            page.wait_for_selector(candidate, timeout=8000)
            container = page.locator(candidate).first
            if container.count() > 0:
                return container
        except Exception:
            continue

    session.save_debug_snapshot(f"post-not-found-{urn.split(':')[-1]}")
    raise BrowserError(
        f"Could not open post {urn}. It may have been deleted, or the page "
        "layout changed. A snapshot was saved to the screenshot directory."
    )


def _find_control(container: Locator, candidates: tuple[str, ...], label: str) -> Locator:
    for selector in candidates:
        try:
            control = container.locator(selector).first
            if control.count() > 0:
                return control
        except Exception:
            continue
    raise BrowserError(
        f"No {label} control matched any known selector. Update "
        "linkedin_autopilot/engagement/selectors.py."
    )


def like_post(session: BrowserSession, urn: str, *, dry_run: bool = True) -> ActionOutcome:
    container = _open_post(session, urn)
    button = _find_control(container, selectors.LIKE_BUTTON, "like")

    try:
        pressed = (button.get_attribute("aria-pressed", timeout=3000) or "").lower() == "true"
    except Exception:
        pressed = False

    if pressed:
        return ActionOutcome.skipped("already liked")

    if dry_run:
        return ActionOutcome.skipped("dry run: like button found but not clicked")

    button.click(timeout=8000)
    if session.page is not None:
        session.page.wait_for_timeout(1200)
    log.info("Liked %s", urn)
    return ActionOutcome.did("liked")


def comment_on_post(
    session: BrowserSession, urn: str, text: str, *, dry_run: bool = True
) -> ActionOutcome:
    if not text.strip():
        return ActionOutcome.skipped("comment body is empty")

    container = _open_post(session, urn)

    # Opening the comment box is what renders the editor into the DOM.
    try:
        comment_button = _find_control(container, selectors.COMMENT_BUTTON, "comment")
        comment_button.click(timeout=8000)
    except BrowserError:
        log.debug("no comment button; the editor may already be present")

    page = session.page
    if page is None:
        raise BrowserError("Browser session is not open")
    page.wait_for_timeout(1200)

    editor = None
    for selector in selectors.COMMENT_EDITOR:
        candidate = page.locator(selector).first
        if candidate.count() > 0:
            editor = candidate
            break

    if editor is None:
        session.save_debug_snapshot(f"no-comment-editor-{urn.split(':')[-1]}")
        raise BrowserError(
            "Could not find the comment editor. A snapshot was saved; update "
            "COMMENT_EDITOR in linkedin_autopilot/engagement/selectors.py."
        )

    if dry_run:
        return ActionOutcome.skipped("dry run: comment editor found but nothing was typed")

    editor.click(timeout=8000)
    # Typed rather than pasted: LinkedIn's editor is a rich-text widget that
    # ignores a direct value assignment.
    editor.type(text, delay=25)
    page.wait_for_timeout(800)

    submit = None
    for selector in selectors.COMMENT_SUBMIT:
        candidate = page.locator(selector).first
        if candidate.count() > 0 and candidate.is_enabled():
            submit = candidate
            break

    if submit is None:
        session.save_debug_snapshot(f"no-comment-submit-{urn.split(':')[-1]}")
        raise BrowserError(
            "Typed the comment but could not find an enabled submit button. "
            "Nothing was posted. A snapshot was saved."
        )

    submit.click(timeout=8000)
    page.wait_for_timeout(2000)
    log.info("Commented on %s", urn)
    return ActionOutcome.did("commented")
