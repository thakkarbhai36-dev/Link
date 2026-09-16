"""Three-legged OAuth against LinkedIn.

Flow: open the authorization URL in a browser, catch the redirect on a local
one-shot HTTP server, exchange the code for an access token, then call the
OpenID Connect userinfo endpoint to learn the member URN we post as.
"""

from __future__ import annotations

import secrets
import threading
import urllib.parse
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx2 as httpx

from ..config import Config
from ..errors import AuthError
from ..logging_setup import get_logger
from .tokens import TokenSet, TokenStore

log = get_logger(__name__)

AUTHORIZE_URL = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
USERINFO_URL = "https://api.linkedin.com/v2/userinfo"

# openid+profile identify the member; w_member_social is what permits posting.
DEFAULT_SCOPES = ("openid", "profile", "w_member_social")

_SUCCESS_PAGE = b"""<!doctype html>
<html><head><meta charset="utf-8"><title>Connected</title></head>
<body style="font-family:system-ui;max-width:32rem;margin:4rem auto;line-height:1.5">
<h1>LinkedIn connected</h1>
<p>You can close this tab and return to the terminal.</p>
</body></html>"""

_FAILURE_PAGE = b"""<!doctype html>
<html><head><meta charset="utf-8"><title>Authorization failed</title></head>
<body style="font-family:system-ui;max-width:32rem;margin:4rem auto;line-height:1.5">
<h1>Authorization failed</h1>
<p>Check the terminal for details.</p>
</body></html>"""


@dataclass(slots=True)
class _CallbackResult:
    code: str | None = None
    state: str | None = None
    error: str | None = None
    error_description: str | None = None


class _CallbackHandler(BaseHTTPRequestHandler):
    result: _CallbackResult
    expected_path: str

    def do_GET(self) -> None:  # noqa: N802 - name fixed by BaseHTTPRequestHandler
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != self.expected_path:
            self.send_response(404)
            self.end_headers()
            return

        params = urllib.parse.parse_qs(parsed.query)
        self.result.code = params.get("code", [None])[0]
        self.result.state = params.get("state", [None])[0]
        self.result.error = params.get("error", [None])[0]
        self.result.error_description = params.get("error_description", [None])[0]

        body = _FAILURE_PAGE if self.result.error else _SUCCESS_PAGE
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # Silence the default stderr logging.
        return


class OAuthFlow:
    def __init__(self, config: Config, scopes: tuple[str, ...] = DEFAULT_SCOPES) -> None:
        self.config = config
        self.scopes = scopes
        secrets_ = config.secrets
        if not secrets_.linkedin_client_id or not secrets_.linkedin_client_secret:
            raise AuthError(
                "LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET must be set. "
                "Create an app at https://www.linkedin.com/developers/apps and copy them into .env"
            )
        self.client_id = secrets_.linkedin_client_id
        self.client_secret = secrets_.linkedin_client_secret
        self.redirect_uri = secrets_.linkedin_redirect_uri

    def authorization_url(self, state: str) -> str:
        query = urllib.parse.urlencode(
            {
                "response_type": "code",
                "client_id": self.client_id,
                "redirect_uri": self.redirect_uri,
                "state": state,
                "scope": " ".join(self.scopes),
            }
        )
        return f"{AUTHORIZE_URL}?{query}"

    def _serve_once(self, state: str, timeout: float) -> _CallbackResult:
        parsed = urllib.parse.urlparse(self.redirect_uri)
        host = parsed.hostname or "localhost"
        port = parsed.port or 80
        path = parsed.path or "/"

        result = _CallbackResult()
        handler = type(
            "BoundCallbackHandler",
            (_CallbackHandler,),
            {"result": result, "expected_path": path},
        )

        try:
            server = HTTPServer((host, port), handler)
        except OSError as exc:
            raise AuthError(
                f"Cannot listen on {host}:{port} for the OAuth redirect: {exc}. "
                "Free the port, or point LINKEDIN_REDIRECT_URI at another one "
                "(it must also be registered on the LinkedIn app)."
            ) from exc

        server.timeout = timeout
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()
        thread.join(timeout)
        server.server_close()

        if result.error:
            raise AuthError(
                f"LinkedIn refused authorization: {result.error} "
                f"({result.error_description or 'no description'})"
            )
        if not result.code:
            raise AuthError(
                f"Timed out after {timeout:.0f}s waiting for the LinkedIn redirect. "
                "Confirm the redirect URL on your app matches LINKEDIN_REDIRECT_URI exactly."
            )
        if result.state != state:
            raise AuthError(
                "OAuth state mismatch — the redirect did not come from the request we started. "
                "Nothing was saved; try again."
            )
        return result

    def exchange_code(self, code: str) -> dict:
        response = httpx.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "redirect_uri": self.redirect_uri,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30.0,
        )
        if response.status_code != 200:
            raise AuthError(f"Token exchange failed ({response.status_code}): {response.text}")
        return response.json()

    def refresh(self, refresh_token: str) -> dict:
        response = httpx.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30.0,
        )
        if response.status_code != 200:
            raise AuthError(
                f"Token refresh failed ({response.status_code}): {response.text}. "
                "Run: autopilot auth login"
            )
        return response.json()

    def fetch_userinfo(self, access_token: str) -> dict:
        response = httpx.get(
            USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=30.0,
        )
        if response.status_code != 200:
            raise AuthError(
                f"Could not read your profile ({response.status_code}): {response.text}. "
                "The app likely lacks the 'Sign In with OpenID Connect' product."
            )
        return response.json()

    def run(self, *, timeout: float = 300.0, open_browser: bool = True) -> TokenSet:
        state = secrets.token_urlsafe(24)
        url = self.authorization_url(state)

        log.info("Opening LinkedIn authorization page")
        print(f"\nIf a browser does not open, visit this URL:\n\n{url}\n")
        if open_browser:
            try:
                webbrowser.open(url)
            except Exception:  # A headless box has no browser; the printed URL still works.
                log.debug("could not launch a browser", exc_info=True)

        result = self._serve_once(state, timeout)
        payload = self.exchange_code(result.code or "")

        userinfo = self.fetch_userinfo(payload["access_token"])
        member_id = userinfo.get("sub")
        if not member_id:
            raise AuthError("LinkedIn did not return a member id in the userinfo response")

        tokens = TokenSet.from_response(
            payload,
            member_urn=f"urn:li:person:{member_id}",
            member_name=userinfo.get("name"),
        )
        log.info("Authorized as %s", tokens.member_name or tokens.member_urn)
        return tokens


def authorize_interactive(config: Config, *, open_browser: bool = True) -> TokenSet:
    """Run the full flow and persist the result."""
    flow = OAuthFlow(config)
    tokens = flow.run(open_browser=open_browser)
    TokenStore(config.token_path).save(tokens)
    return tokens


def get_valid_tokens(config: Config) -> TokenSet:
    """Return a usable token, refreshing it first if that is possible.

    LinkedIn issues refresh tokens only to approved apps, so for most developer
    apps an expired token means logging in again by hand. Say so plainly rather
    than failing with a bare 401 later.
    """
    store = TokenStore(config.token_path)
    tokens = store.load()

    if not tokens.expired():
        return tokens

    if tokens.can_refresh():
        log.info("Access token expired; refreshing")
        flow = OAuthFlow(config)
        payload = flow.refresh(tokens.refresh_token or "")
        refreshed = TokenSet.from_response(
            payload, member_urn=tokens.member_urn, member_name=tokens.member_name
        )
        if not refreshed.refresh_token:
            refreshed.refresh_token = tokens.refresh_token
            refreshed.refresh_expires_at = tokens.refresh_expires_at
        store.save(refreshed)
        return refreshed

    raise AuthError(
        "Your LinkedIn access token has expired and no refresh token is available. "
        "Run: autopilot auth login"
    )
