"""The dashboard HTTP server.

Deliberately built on the standard library. This is a single-user tool that may
end up running on a small free-tier host, so the install stays light and there
is no build step.

Security posture: the dashboard can publish to your LinkedIn account, so it is
never open. A token is required on every request, it binds to loopback unless
told otherwise, and it refuses to bind to a public interface without an
explicitly supplied token.
"""

from __future__ import annotations

import http.cookies
import ipaddress
import secrets
import threading
import urllib.parse
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from ..config import Config
from ..errors import AutopilotError
from ..logging_setup import get_logger
from ..safety import Guard
from ..store import ActionStatus, Store
from . import templates

log = get_logger(__name__)

COOKIE_NAME = "autopilot_token"
MAX_BODY_BYTES = 64 * 1024
VALID_STATUSES = {"pending", "approved", "done", "failed", "rejected", "skipped"}


def resolve_token(explicit: str | None, host: str) -> tuple[str, bool]:
    """Return the token to use and whether it was generated here.

    Refuses to invent a token for a publicly reachable bind: a generated token
    scrolls past in a log, and an operator who did not choose one probably did
    not realise the dashboard was about to face the internet.
    """
    if explicit:
        return explicit, False
    if not _is_loopback(host):
        raise AutopilotError(
            f"Refusing to serve on {host} without a token. This dashboard can publish "
            "to your LinkedIn account, so a public bind must set one explicitly:\n"
            "  export AUTOPILOT_WEB_TOKEN=$(python -c 'import secrets;print(secrets.token_urlsafe(32))')\n"
            "Or bind to localhost instead and reach it over an SSH tunnel."
        )
    return secrets.token_urlsafe(24), True


def _is_loopback(host: str) -> bool:
    if host in {"localhost", ""}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class DashboardServer(ThreadingHTTPServer):
    """Threaded server carrying the application objects each request needs."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        *,
        config: Config,
        store: Store,
        guard: Guard,
        token: str,
    ) -> None:
        super().__init__(address, handler)
        self.config = config
        self.store = store
        self.guard = guard
        self.token = token
        # Long-running jobs hold this so two taps cannot start two runs.
        self.job_lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    server: DashboardServer
    server_version = "linkedin-autopilot"

    # ----------------------------------------------------------------- plumbing

    def log_message(self, fmt: str, *args: Any) -> None:
        log.debug("%s - %s", self.address_string(), fmt % args)

    @property
    def app(self) -> DashboardServer:
        return self.server

    def _send(self, status: int, body: str, *, headers: dict[str, str] | None = None) -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        # This page renders text from LinkedIn and from a model; no inline
        # scripts are needed, so forbid them outright.
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'",
        )
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def _redirect(self, location: str) -> None:
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _query(self) -> dict[str, list[str]]:
        return urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)

    def _form(self) -> dict[str, list[str]]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return {}
        if length <= 0 or length > MAX_BODY_BYTES:
            return {}
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        return urllib.parse.parse_qs(raw, keep_blank_values=True)

    # --------------------------------------------------------------------- auth

    def _cookie_token(self) -> str:
        header = self.headers.get("Cookie")
        if not header:
            return ""
        jar = http.cookies.SimpleCookie()
        try:
            jar.load(header)
        except http.cookies.CookieError:
            return ""
        morsel = jar.get(COOKIE_NAME)
        return morsel.value if morsel else ""

    def _authenticated(self) -> tuple[bool, bool]:
        """Return (authenticated, came_from_query).

        Compared in constant time so the token cannot be recovered by timing
        repeated guesses.
        """
        expected = self.app.token
        supplied = self._query().get("token", [""])[0]
        if supplied and secrets.compare_digest(supplied, expected):
            return True, True
        cookie = self._cookie_token()
        if cookie and secrets.compare_digest(cookie, expected):
            return True, False
        return False, False

    def _check_csrf(self, form: dict[str, list[str]]) -> bool:
        supplied = form.get("csrf", [""])[0]
        return bool(supplied) and secrets.compare_digest(supplied, self.app.token)

    # ------------------------------------------------------------------ routing

    def do_GET(self) -> None:  # noqa: N802 - name fixed by the base class
        authed, from_query = self._authenticated()
        if not authed:
            self._send(
                401,
                templates.page("Sign in", templates.login_page()),
            )
            return

        if from_query:
            # Move the token out of the URL so it stops appearing in history and
            # in any Referer the browser might send.
            path = urllib.parse.urlparse(self.path)
            remaining = {
                key: value for key, value in urllib.parse.parse_qsl(path.query) if key != "token"
            }
            target = path.path + (f"?{urllib.parse.urlencode(remaining)}" if remaining else "")
            cookie = (
                f"{COOKIE_NAME}={self.app.token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=604800"
            )
            self.send_response(303)
            self.send_header("Location", target)
            self.send_header("Set-Cookie", cookie)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        route = urllib.parse.urlparse(self.path).path.rstrip("/") or "/"

        if route == "/":
            self._view_queue()
        elif route == "/status":
            self._view_status()
        elif route.startswith("/action/"):
            self._view_action(route)
        else:
            self._send(404, templates.page("Not found", templates.banner("No such page.", "error")))

    def do_POST(self) -> None:  # noqa: N802 - name fixed by the base class
        authed, _ = self._authenticated()
        if not authed:
            self._send(401, templates.page("Sign in", templates.login_page()))
            return

        form = self._form()
        if not self._check_csrf(form):
            self._send(
                403,
                templates.page("Rejected", templates.banner("Request token mismatch.", "error")),
            )
            return

        route = urllib.parse.urlparse(self.path).path.rstrip("/") or "/"
        handlers: dict[str, Callable[[dict[str, list[str]]], None]] = {
            "/draft": self._post_draft,
            "/stop": self._post_stop,
            "/resume": self._post_resume,
        }
        if route in handlers:
            handlers[route](form)
            return

        if route.startswith("/action/"):
            self._post_action(route, form)
            return

        self._send(404, templates.page("Not found", templates.banner("No such page.", "error")))

    # ------------------------------------------------------------------- views

    def _view_queue(self) -> None:
        requested = self._query().get("status", ["pending"])[0]
        status = requested if requested in VALID_STATUSES else "pending"
        actions = self.app.store.list_actions(status=status, limit=50)
        body = templates.queue_page(
            actions,
            token=self.app.token,
            status_filter=status,
            kill_switch=self.app.guard.kill_switch_engaged(),
        )
        self._send(200, templates.page("Review queue", body, active="queue"))

    def _view_status(self) -> None:
        body = templates.status_page(
            self.app.guard.describe(),
            token=self.app.token,
            kill_switch=self.app.guard.kill_switch_engaged(),
            config_path=str(self.app.config.source_path or "config.yaml"),
        )
        self._send(200, templates.page("Status", body, active="status"))

    def _action_id(self, route: str) -> int | None:
        parts = route.strip("/").split("/")
        if len(parts) < 2:
            return None
        try:
            return int(parts[1])
        except ValueError:
            return None

    def _view_action(self, route: str) -> None:
        action_id = self._action_id(route)
        action = self.app.store.get_action(action_id) if action_id is not None else None
        if action is None:
            self._send(
                404, templates.page("Not found", templates.banner("No such action.", "error"))
            )
            return
        body = templates.action_page(action, token=self.app.token, dry_run=self.app.config.dry_run)
        self._send(200, templates.page(f"Action #{action.id}", body, active="queue"))

    # ------------------------------------------------------------------ actions

    def _post_action(self, route: str, form: dict[str, list[str]]) -> None:
        action_id = self._action_id(route)
        if action_id is None or self.app.store.get_action(action_id) is None:
            self._send(
                404, templates.page("Not found", templates.banner("No such action.", "error"))
            )
            return

        verb = route.strip("/").split("/")[-1]

        if verb == "save":
            self.app.store.update_action(action_id, body=form.get("body", [""])[0].strip())
            self._redirect(f"/action/{action_id}")
            return

        if verb == "reject":
            self.app.store.update_action(action_id, status=ActionStatus.REJECTED)
            self._redirect("/")
            return

        if verb == "approve":
            self._approve(action_id)
            return

        self._send(404, templates.page("Not found", templates.banner("No such action.", "error")))

    def _approve(self, action_id: int) -> None:
        from ..runner import run_approved_queue

        if not self.app.job_lock.acquire(blocking=False):
            self._send(
                409,
                templates.page("Busy", templates.banner("Another job is already running.", "warn")),
            )
            return
        try:
            self.app.store.update_action(action_id, status=ActionStatus.APPROVED)
            report = run_approved_queue(self.app.config, self.app.store, self.app.guard)
            summary = "; ".join(report.notes) or report.summary
        except AutopilotError as exc:
            summary = str(exc)
            log.warning("approval of #%s failed: %s", action_id, exc)
        except Exception as exc:  # A dashboard must not die on one bad action.
            summary = f"Unexpected failure: {exc}"
            log.exception("approval of #%s crashed", action_id)
        finally:
            self.app.job_lock.release()

        action = self.app.store.get_action(action_id)
        body = templates.banner(summary, "info")
        if action is not None:
            body += templates.action_page(
                action, token=self.app.token, dry_run=self.app.config.dry_run
            )
        self._send(200, templates.page(f"Action #{action_id}", body, active="queue"))

    def _post_draft(self, _form: dict[str, list[str]]) -> None:
        from ..runner import run_post_job

        if not self.app.job_lock.acquire(blocking=False):
            self._send(
                409,
                templates.page("Busy", templates.banner("Another job is already running.", "warn")),
            )
            return
        try:
            report = run_post_job(
                self.app.config, self.app.store, self.app.guard, force_publish=False
            )
            summary = "; ".join(report.notes) or report.summary
        except AutopilotError as exc:
            summary = str(exc)
        except Exception as exc:
            summary = f"Unexpected failure: {exc}"
            log.exception("draft generation crashed")
        finally:
            self.app.job_lock.release()

        body = templates.banner(summary, "info") + templates.queue_page(
            self.app.store.list_actions(status="pending", limit=50),
            token=self.app.token,
            status_filter="pending",
            kill_switch=self.app.guard.kill_switch_engaged(),
        )
        self._send(200, templates.page("Review queue", body, active="queue"))

    def _post_stop(self, _form: dict[str, list[str]]) -> None:
        path = self.app.config.kill_switch_file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        log.warning("kill switch engaged from the dashboard")
        self._redirect("/status")

    def _post_resume(self, _form: dict[str, list[str]]) -> None:
        self.app.config.kill_switch_file.unlink(missing_ok=True)
        log.info("kill switch cleared from the dashboard")
        self._redirect("/status")


def build_server(
    config: Config,
    store: Store,
    guard: Guard,
    *,
    host: str = "127.0.0.1",
    port: int = 8770,
    token: str | None = None,
) -> tuple[DashboardServer, str, bool]:
    """Construct the server. Returns it with the token and whether it was generated."""
    resolved, generated = resolve_token(token, host)
    server = DashboardServer(
        (host, port), Handler, config=config, store=store, guard=guard, token=resolved
    )
    return server, resolved, generated
