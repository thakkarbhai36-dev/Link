"""Tests for the review dashboard.

The server is started on an ephemeral port and driven over real HTTP, so the
auth, redirect, and CSRF paths are exercised as a browser would hit them.
"""

from __future__ import annotations

import http.client
import threading
from collections.abc import Iterator

import pytest

from linkedin_autopilot.config import Config
from linkedin_autopilot.errors import AutopilotError
from linkedin_autopilot.safety import Guard
from linkedin_autopilot.store import ActionKind, Store
from linkedin_autopilot.web.server import build_server, resolve_token

TOKEN = "test-token-value"


@pytest.fixture()
def seeded(store: Store) -> dict[str, int]:
    post_id = store.add_action(
        ActionKind.POST,
        body="A post about index bloat that is long enough to look like a real draft.",
        context={"topic": "Databases"},
    )
    like_id = store.add_action(
        ActionKind.LIKE,
        target_urn="urn:li:activity:5",
        target_author="https://www.linkedin.com/in/alexdoe",
        context={"author": "Alex Doe", "score": 0.77},
    )
    return {"post": post_id, "like": like_id}


@pytest.fixture()
def server(config: Config, store: Store, guard: Guard) -> Iterator[tuple[str, int]]:
    srv, _token, _generated = build_server(
        config, store, guard, host="127.0.0.1", port=0, token=TOKEN
    )
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield srv.server_address[0], srv.server_address[1]
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)


def request(
    server: tuple[str, int],
    method: str,
    path: str,
    *,
    body: str | None = None,
    cookie: str | None = None,
) -> tuple[int, dict[str, str], str]:
    conn = http.client.HTTPConnection(server[0], server[1], timeout=10)
    headers: dict[str, str] = {}
    if body is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if cookie:
        headers["Cookie"] = cookie
    try:
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        payload = response.read().decode("utf-8", errors="replace")
        return response.status, dict(response.getheaders()), payload
    finally:
        conn.close()


def authed_cookie() -> str:
    return f"autopilot_token={TOKEN}"


# ------------------------------------------------------------------------ auth


def test_no_token_is_rejected(server) -> None:
    status, _headers, body = request(server, "GET", "/")
    assert status == 401
    assert "Access token" in body


def test_wrong_token_is_rejected(server) -> None:
    status, _headers, _body = request(server, "GET", "/?token=nope")
    assert status == 401


def test_token_in_query_sets_a_cookie_and_redirects(server) -> None:
    status, headers, _body = request(server, "GET", f"/?token={TOKEN}")
    assert status == 303
    assert headers["Location"] == "/"
    cookie = headers["Set-Cookie"]
    assert TOKEN in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=Strict" in cookie


def test_query_token_redirect_keeps_other_parameters(server) -> None:
    status, headers, _body = request(server, "GET", f"/?status=done&token={TOKEN}")
    assert status == 303
    assert headers["Location"] == "/?status=done"
    assert "token" not in headers["Location"]


def test_cookie_authenticates(server) -> None:
    status, _headers, body = request(server, "GET", "/", cookie=authed_cookie())
    assert status == 200
    assert "Review queue" in body


def test_security_headers_are_set(server) -> None:
    _status, headers, _body = request(server, "GET", "/", cookie=authed_cookie())
    assert "default-src 'none'" in headers["Content-Security-Policy"]
    assert headers["Referrer-Policy"] == "no-referrer"
    assert headers["X-Content-Type-Options"] == "nosniff"


# ------------------------------------------------------------------------ CSRF


def test_post_without_csrf_is_refused(server, seeded) -> None:
    status, _headers, _body = request(
        server,
        "POST",
        f"/action/{seeded['post']}/reject",
        body="",
        cookie=authed_cookie(),
    )
    assert status == 403


def test_post_with_wrong_csrf_is_refused(server, seeded) -> None:
    status, _headers, _body = request(
        server,
        "POST",
        f"/action/{seeded['post']}/reject",
        body="csrf=wrong",
        cookie=authed_cookie(),
    )
    assert status == 403


def test_unauthenticated_post_is_refused(server, seeded) -> None:
    status, _headers, _body = request(
        server, "POST", f"/action/{seeded['post']}/reject", body=f"csrf={TOKEN}"
    )
    assert status == 401


# ----------------------------------------------------------------------- views


def test_queue_lists_pending_actions(server, seeded) -> None:
    _status, _headers, body = request(server, "GET", "/", cookie=authed_cookie())
    assert "index bloat" in body
    assert "Alex Doe" in body


def test_unknown_status_falls_back_to_pending(server, seeded) -> None:
    status, _headers, body = request(
        server, "GET", "/?status=../etc/passwd", cookie=authed_cookie()
    )
    assert status == 200
    assert "index bloat" in body


def test_action_page_renders_the_body(server, seeded) -> None:
    status, _headers, body = request(
        server, "GET", f"/action/{seeded['post']}", cookie=authed_cookie()
    )
    assert status == 200
    assert "index bloat" in body
    assert "Approve and run" in body


def test_like_has_no_editor(server, seeded) -> None:
    _status, _headers, body = request(
        server, "GET", f"/action/{seeded['like']}", cookie=authed_cookie()
    )
    assert "A like has no text to edit." in body


def test_missing_action_is_a_404(server) -> None:
    status, _headers, _body = request(server, "GET", "/action/9999", cookie=authed_cookie())
    assert status == 404


def test_non_numeric_action_id_is_a_404(server) -> None:
    status, _headers, _body = request(server, "GET", "/action/abc", cookie=authed_cookie())
    assert status == 404


def test_html_in_a_draft_is_escaped(server, store: Store) -> None:
    store.add_action(ActionKind.POST, body="<script>alert('xss')</script> and more text here.")
    _status, _headers, body = request(server, "GET", "/", cookie=authed_cookie())
    assert "<script>alert" not in body
    assert "&lt;script&gt;" in body


# --------------------------------------------------------------------- mutation


def test_save_updates_the_body(server, seeded, store: Store) -> None:
    status, headers, _body = request(
        server,
        "POST",
        f"/action/{seeded['post']}/save",
        body=f"csrf={TOKEN}&body=Rewritten+by+hand",
        cookie=authed_cookie(),
    )
    assert status == 303
    assert headers["Location"] == f"/action/{seeded['post']}"
    action = store.get_action(seeded["post"])
    assert action is not None
    assert action.body == "Rewritten by hand"


def test_reject_marks_the_action(server, seeded, store: Store) -> None:
    status, _headers, _body = request(
        server,
        "POST",
        f"/action/{seeded['post']}/reject",
        body=f"csrf={TOKEN}",
        cookie=authed_cookie(),
    )
    assert status == 303
    action = store.get_action(seeded["post"])
    assert action is not None
    assert action.status == "rejected"


def test_stop_and_resume_toggle_the_kill_switch(server, config: Config) -> None:
    request(server, "POST", "/stop", body=f"csrf={TOKEN}", cookie=authed_cookie())
    assert config.kill_switch_file.exists()
    request(server, "POST", "/resume", body=f"csrf={TOKEN}", cookie=authed_cookie())
    assert not config.kill_switch_file.exists()


def test_status_page_shows_quotas(server) -> None:
    status, _headers, body = request(server, "GET", "/status", cookie=authed_cookie())
    assert status == 200
    assert "actions today" in body


def test_approve_in_dry_run_publishes_nothing(server, seeded, store: Store) -> None:
    status, _headers, body = request(
        server,
        "POST",
        f"/action/{seeded['post']}/approve",
        body=f"csrf={TOKEN}",
        cookie=authed_cookie(),
    )
    assert status == 200
    assert "dry run" in body.lower()
    action = store.get_action(seeded["post"])
    assert action is not None
    assert action.result_urn is None


# ----------------------------------------------------------------------- tokens


def test_loopback_bind_generates_a_token() -> None:
    token, generated = resolve_token(None, "127.0.0.1")
    assert generated is True
    assert len(token) > 20


def test_explicit_token_is_kept() -> None:
    token, generated = resolve_token("chosen", "0.0.0.0")
    assert (token, generated) == ("chosen", False)


def test_public_bind_without_a_token_is_refused() -> None:
    with pytest.raises(AutopilotError, match="Refusing to serve"):
        resolve_token(None, "0.0.0.0")
