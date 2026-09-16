"""Exception hierarchy. Everything the CLI catches descends from AutopilotError."""

from __future__ import annotations


class AutopilotError(Exception):
    """Base class for every error this package raises deliberately."""


class ConfigError(AutopilotError):
    """The configuration file or environment is missing or contradictory."""


class AuthError(AutopilotError):
    """No usable LinkedIn credentials, or the stored token cannot be refreshed."""


class LinkedInAPIError(AutopilotError):
    """The LinkedIn REST API returned an error response."""

    def __init__(self, status: int, message: str, body: str = "") -> None:
        super().__init__(f"LinkedIn API {status}: {message}")
        self.status = status
        self.body = body


class RateLimited(LinkedInAPIError):
    """LinkedIn asked us to slow down (HTTP 429)."""

    def __init__(self, message: str, retry_after: float | None = None, body: str = "") -> None:
        super().__init__(429, message, body)
        self.retry_after = retry_after


class QuotaExceeded(AutopilotError):
    """A locally configured daily or weekly cap has been reached."""


class SafetyStop(AutopilotError):
    """A guardrail refused to let an action proceed."""


class ContentRejected(AutopilotError):
    """Generated content failed validation and could not be salvaged."""


class BrowserError(AutopilotError):
    """The browser session is unusable, or a page selector no longer matches."""
