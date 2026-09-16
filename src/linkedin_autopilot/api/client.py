"""HTTP client for LinkedIn's versioned REST API.

Handles the three headers LinkedIn insists on, retries the failures worth
retrying, and turns error responses into exceptions carrying the server's own
message instead of a bare status code.
"""

from __future__ import annotations

import random
import time
from typing import Any

import httpx2 as httpx

from ..errors import LinkedInAPIError, RateLimited
from ..logging_setup import get_logger

log = get_logger(__name__)

API_BASE = "https://api.linkedin.com"
# LinkedIn pins behaviour to a monthly version string. Bump deliberately: a
# version LinkedIn has retired returns 426.
DEFAULT_API_VERSION = "202506"

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


class LinkedInClient:
    def __init__(
        self,
        access_token: str,
        *,
        api_version: str = DEFAULT_API_VERSION,
        timeout: float = 30.0,
        max_retries: int = 3,
    ) -> None:
        self.access_token = access_token
        self.api_version = api_version
        self.max_retries = max_retries
        self._client = httpx.Client(
            base_url=API_BASE,
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {access_token}",
                "X-Restli-Protocol-Version": "2.0.0",
                "LinkedIn-Version": api_version,
                "Content-Type": "application/json",
            },
        )

    def __enter__(self) -> LinkedInClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # --------------------------------------------------------------- requests

    def request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            try:
                response = self._client.request(
                    method, path, json=json, params=params, headers=headers
                )
            except httpx.RequestError as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    raise LinkedInAPIError(0, f"network error calling {path}: {exc}") from exc
                self._backoff(attempt)
                continue

            if response.status_code in RETRY_STATUSES and attempt < self.max_retries:
                retry_after = self._retry_after(response)
                log.warning(
                    "LinkedIn returned %s for %s; retrying in %.1fs (attempt %d/%d)",
                    response.status_code,
                    path,
                    retry_after,
                    attempt + 1,
                    self.max_retries,
                )
                time.sleep(retry_after)
                continue

            if response.status_code >= 400:
                self._raise_for_response(response, path)

            return response

        raise LinkedInAPIError(0, f"exhausted retries calling {path}: {last_error}")

    def get(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", path, **kwargs)

    # ---------------------------------------------------------------- helpers

    def _backoff(self, attempt: int) -> None:
        delay = min(2**attempt, 30) + random.uniform(0, 1)
        time.sleep(delay)

    def _retry_after(self, response: httpx.Response) -> float:
        header = response.headers.get("Retry-After")
        if header:
            try:
                return float(header)
            except ValueError:
                pass
        return min(2 ** int(response.headers.get("x-attempt", "1")), 30) + random.uniform(0, 1)

    def _raise_for_response(self, response: httpx.Response, path: str) -> None:
        body = response.text[:2000]
        message = body
        try:
            payload = response.json()
            message = payload.get("message") or payload.get("error_description") or body
        except (ValueError, AttributeError):
            pass

        if response.status_code == 429:
            raise RateLimited(message, retry_after=self._retry_after(response), body=body)

        if response.status_code in (401, 403):
            message = (
                f"{message} — the token may be expired or missing a scope. "
                "Run: autopilot auth login"
            )
        elif response.status_code == 426:
            message = (
                f"{message} — LinkedIn has retired API version {self.api_version}. "
                "Raise the version in linkedin_autopilot/api/client.py."
            )

        raise LinkedInAPIError(response.status_code, f"{message} (calling {path})", body=body)

    # ------------------------------------------------------------------- self

    def userinfo(self) -> dict[str, Any]:
        """OpenID Connect profile for the authorized member."""
        return self.get("/v2/userinfo").json()
