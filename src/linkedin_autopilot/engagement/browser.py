"""Playwright session management.

The browser runs against a persistent profile directory: you log in once, by
hand, and the session cookie is reused afterwards. There is no credential
handling here and no attempt to look like anything other than what it is —
an automated browser driving your own logged-in session.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..config import Config
from ..errors import BrowserError
from ..logging_setup import get_logger
from . import selectors

if TYPE_CHECKING:  # pragma: no cover - typing only
    from playwright.sync_api import Page

log = get_logger(__name__)


def _require_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise BrowserError(
            "Playwright is not installed. Install the browser extra and the browser itself:\n"
            '  pip install -e ".[browser]"\n'
            "  python -m playwright install chromium"
        ) from exc
    return sync_playwright


class BrowserSession:
    """A logged-in LinkedIn tab, scoped to a `with` block."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.browser_config = config.browser
        # Typed loosely: the concrete Playwright types are only importable when
        # the optional browser extra is installed.
        self._playwright: Any = None
        self._context: Any = None
        self.page: Page | None = None

    def __enter__(self) -> BrowserSession:
        sync_playwright = _require_playwright()
        user_data_dir = Path(self.browser_config.user_data_dir).resolve()
        user_data_dir.mkdir(parents=True, exist_ok=True)

        self._playwright = sync_playwright().start()
        try:
            self._context = self._playwright.chromium.launch_persistent_context(
                str(user_data_dir),
                headless=self.browser_config.headless,
                viewport={"width": 1280, "height": 900},
                args=["--disable-blink-features=AutomationControlled"],
            )
        except Exception as exc:
            self._playwright.stop()
            raise BrowserError(
                f"Could not launch Chromium: {exc}. Run: python -m playwright install chromium"
            ) from exc

        self._context.set_default_timeout(self.browser_config.nav_timeout_ms)
        self.page = self._context.pages[0] if self._context.pages else self._context.new_page()
        return self

    def __exit__(self, *exc_info: object) -> None:
        try:
            if self._context is not None:
                self._context.close()
        finally:
            if self._playwright is not None:
                self._playwright.stop()

    # ------------------------------------------------------------------ state

    def goto_feed(self) -> None:
        if self.page is None:
            raise BrowserError("Browser session is not open")
        self.page.goto(selectors.FEED_URL, wait_until="domcontentloaded")

    def is_logged_in(self) -> bool:
        if self.page is None:
            return False
        if "/login" in self.page.url or "/checkpoint" in self.page.url:
            return False
        for marker in selectors.LOGGED_IN_MARKERS:
            try:
                if self.page.locator(marker).count() > 0:
                    return True
            except Exception:
                continue
        return False

    def require_login(self) -> None:
        self.goto_feed()
        if not self.is_logged_in():
            raise BrowserError(
                "This browser profile is not logged in to LinkedIn. "
                "Run `autopilot browser login`, sign in by hand (including any "
                "two-factor prompt), then close the window."
            )

    def save_debug_snapshot(self, label: str) -> Path | None:
        """Write a screenshot and the page HTML, for fixing broken selectors."""
        if self.page is None:
            return None
        directory = Path(self.browser_config.screenshot_dir)
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        image_path = directory / f"{stamp}-{label}.png"
        html_path = directory / f"{stamp}-{label}.html"
        try:
            self.page.screenshot(path=str(image_path), full_page=False)
            html_path.write_text(self.page.content(), encoding="utf-8")
            log.info("Saved debug snapshot to %s", image_path)
            return image_path
        except Exception:
            log.debug("could not save debug snapshot", exc_info=True)
            return None


@contextmanager
def open_session(config: Config, *, require_login: bool = True) -> Iterator[BrowserSession]:
    with BrowserSession(config) as session:
        if require_login:
            session.require_login()
        yield session


def interactive_login(config: Config) -> None:
    """Open a window and wait for the user to sign in by hand."""
    sync_playwright = _require_playwright()
    user_data_dir = Path(config.browser.user_data_dir).resolve()
    user_data_dir.mkdir(parents=True, exist_ok=True)

    print(
        "\nA Chromium window is opening. Sign in to LinkedIn there, finish any\n"
        "two-factor prompt, then close the window. The session is saved to:\n"
        f"  {user_data_dir}\n"
    )

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(user_data_dir),
            headless=False,  # The whole point is that a person drives this.
            viewport={"width": 1280, "height": 900},
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(selectors.LOGIN_URL, wait_until="domcontentloaded")
        try:
            # Block until the person closes the window.
            page.wait_for_event("close", timeout=0)
        except Exception:
            pass
        finally:
            with contextlib.suppress(Exception):
                context.close()

    print("Window closed. Verify with: autopilot browser check")
