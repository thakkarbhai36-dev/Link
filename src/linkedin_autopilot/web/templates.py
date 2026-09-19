"""HTML rendering.

Server-rendered, no build step, no JavaScript framework. Everything the
dashboard does is a link or a form POST, which means it works on a phone with a
flaky connection and degrades to something usable if styles fail to load.
"""

from __future__ import annotations

from html import escape

from ..store import Action

_STYLE = """
:root {
  color-scheme: light dark;
  --bg: #f6f7f9;
  --surface: #ffffff;
  --border: #dfe3e8;
  --text: #16191d;
  --muted: #5f6672;
  --accent: #0a66c2;
  --accent-text: #ffffff;
  --danger: #b42318;
  --ok: #067647;
  --warn: #b54708;
  --radius: 12px;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #14171a;
    --surface: #1d2126;
    --border: #2e343b;
    --text: #e9ecef;
    --muted: #9aa3ae;
    --accent: #5aa7f0;
    --accent-text: #0b1620;
    --danger: #f97066;
    --ok: #47cd89;
    --warn: #f79009;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font: 16px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  padding-bottom: 3rem;
}
header {
  position: sticky; top: 0; z-index: 5;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  padding: 0.75rem 1rem;
  display: flex; align-items: center; gap: 0.75rem; flex-wrap: wrap;
}
header h1 { font-size: 1rem; margin: 0; font-weight: 650; letter-spacing: -0.01em; }
header nav { margin-left: auto; display: flex; gap: 0.5rem; flex-wrap: wrap; }
main { max-width: 46rem; margin: 0 auto; padding: 1rem; }
a { color: var(--accent); }
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 1rem;
  margin-bottom: 0.75rem;
}
.card h2 { margin: 0 0 0.5rem; font-size: 0.95rem; font-weight: 650; }
.row { display: flex; gap: 0.5rem; align-items: center; flex-wrap: wrap; }
.meta { color: var(--muted); font-size: 0.82rem; }
.body {
  white-space: pre-wrap;
  word-break: break-word;
  margin: 0.5rem 0 0.75rem;
}
.tag {
  display: inline-block;
  font-size: 0.72rem;
  font-weight: 650;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  padding: 0.15rem 0.45rem;
  border-radius: 999px;
  border: 1px solid var(--border);
  color: var(--muted);
}
.tag.pending { color: var(--warn); border-color: var(--warn); }
.tag.approved { color: var(--accent); border-color: var(--accent); }
.tag.done { color: var(--ok); border-color: var(--ok); }
.tag.failed { color: var(--danger); border-color: var(--danger); }
button, .btn {
  font: inherit;
  font-weight: 600;
  padding: 0.6rem 0.9rem;
  border-radius: 10px;
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--text);
  cursor: pointer;
  text-decoration: none;
  display: inline-block;
  min-height: 44px;
}
button.primary, .btn.primary { background: var(--accent); border-color: var(--accent); color: var(--accent-text); }
.btn.active { background: var(--text); border-color: var(--text); color: var(--bg); }
button.danger { color: var(--danger); border-color: var(--danger); }
button.small, .btn.small { min-height: 0; padding: 0.35rem 0.6rem; font-size: 0.82rem; }
textarea {
  width: 100%;
  min-height: 14rem;
  font: inherit;
  padding: 0.75rem;
  border-radius: 10px;
  border: 1px solid var(--border);
  background: var(--bg);
  color: var(--text);
  resize: vertical;
}
table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
td { padding: 0.35rem 0; vertical-align: top; }
td:first-child { color: var(--muted); width: 42%; padding-right: 0.75rem; }
.banner {
  border-radius: var(--radius);
  padding: 0.75rem 1rem;
  margin-bottom: 0.75rem;
  border: 1px solid;
  font-size: 0.9rem;
}
.banner.info { border-color: var(--accent); }
.banner.warn { border-color: var(--warn); }
.banner.error { border-color: var(--danger); }
.empty { color: var(--muted); text-align: center; padding: 2rem 1rem; }
.count { font-variant-numeric: tabular-nums; }
form.inline { display: inline; }
"""


def page(title: str, body: str, *, token: str = "", active: str = "") -> str:
    """Wrap body content in the shared shell."""
    suffix = f"?token={escape(token)}" if token else ""

    def link(href: str, label: str, key: str) -> str:
        classes = "btn small active" if key == active else "btn small"
        return f'<a class="{classes}" href="{href}{suffix}">{label}</a>'

    nav = "".join(
        [
            link("/", "Queue", "queue"),
            link("/status", "Status", "status"),
        ]
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="light dark">
<title>{escape(title)}</title>
<style>{_STYLE}</style>
</head>
<body>
<header><h1>{escape(title)}</h1><nav>{nav}</nav></header>
<main>{body}</main>
</body>
</html>"""


def banner(message: str, kind: str = "info") -> str:
    return f'<div class="banner {escape(kind)}">{escape(message)}</div>'


def _tag(status: str) -> str:
    return f'<span class="tag {escape(status)}">{escape(status)}</span>'


def _preview(action: Action, limit: int = 160) -> str:
    raw = action.body or str(action.context.get("excerpt") or "")
    flat = " ".join(raw.split())
    if len(flat) > limit:
        flat = flat[:limit].rstrip() + "…"
    return escape(flat)


def queue_page(actions: list[Action], *, token: str, status_filter: str, kill_switch: bool) -> str:
    suffix = f"?token={escape(token)}" if token else ""
    parts: list[str] = []

    if kill_switch:
        parts.append(banner("Kill switch is engaged. Nothing will run until you clear it.", "warn"))

    filters = ["pending", "approved", "done", "failed", "rejected"]
    chip_parts = []
    for name in filters:
        token_query = "&token=" + escape(token) if token else ""
        classes = "btn small active" if name == status_filter else "btn small"
        chip_parts.append(
            f'<a class="{classes}" href="/?status={name}{token_query}"'
            f' aria-current="{"page" if name == status_filter else "false"}">{name}</a>'
        )
    chips = " ".join(chip_parts)
    parts.append(f'<div class="row" style="margin-bottom:0.75rem">{chips}</div>')

    if not actions:
        parts.append(
            f'<div class="card empty">Nothing with status “{escape(status_filter)}”.</div>'
        )
    else:
        for action in actions:
            author = action.context.get("author")
            score = action.context.get("score")
            topic = action.context.get("topic")
            bits = [f"#{action.id}", escape(action.kind)]
            if author:
                bits.append(escape(str(author)))
            if topic:
                bits.append(escape(str(topic)))
            if score is not None:
                bits.append(f"score {escape(str(score))}")
            parts.append(
                f"""<div class="card">
  <div class="row">{_tag(action.status)}<span class="meta">{" · ".join(bits)}</span></div>
  <div class="body">{_preview(action)}</div>
  <div class="row"><a class="btn small primary" href="/action/{action.id}{suffix}">Open</a></div>
</div>"""
            )

    parts.append(
        f"""<div class="card">
  <h2>Draft a post now</h2>
  <div class="meta">Generates one post and adds it to the queue. It is not published.</div>
  <form method="post" action="/draft" style="margin-top:0.75rem">
    <input type="hidden" name="csrf" value="{escape(token)}">
    <button class="primary" type="submit">Generate a draft</button>
  </form>
</div>"""
    )
    return "".join(parts)


def action_page(action: Action, *, token: str, dry_run: bool) -> str:
    rows = "".join(
        f"<tr><td>{escape(str(key))}</td><td>{escape(str(value))}</td></tr>"
        for key, value in action.context.items()
    )
    if action.target_urn:
        url = f"https://www.linkedin.com/feed/update/{escape(action.target_urn)}/"
        rows += (
            f'<tr><td>post</td><td><a href="{url}" rel="noreferrer">open on LinkedIn</a></td></tr>'
        )
    if action.result_urn:
        url = f"https://www.linkedin.com/feed/update/{escape(action.result_urn)}/"
        rows += f'<tr><td>published</td><td><a href="{url}" rel="noreferrer">view it</a></td></tr>'
    rows += f"<tr><td>created</td><td>{escape(action.created_at)}</td></tr>"

    parts: list[str] = []
    if action.error:
        parts.append(banner(action.error, "error"))
    if dry_run:
        parts.append(banner("Dry run is on. Approving will not publish anything.", "info"))

    editable = action.kind != "like"
    csrf = f'<input type="hidden" name="csrf" value="{escape(token)}">'

    if editable:
        body_field = (
            f'<textarea name="body" aria-label="Draft text">{escape(action.body or "")}</textarea>'
        )
        save = f"""<form method="post" action="/action/{action.id}/save">
  {csrf}{body_field}
  <div class="row" style="margin-top:0.5rem">
    <button type="submit">Save changes</button>
    <span class="meta count">{len(action.body or "")} characters</span>
  </div>
</form>"""
    else:
        save = '<div class="meta">A like has no text to edit.</div>'

    actionable = action.status in {"pending", "approved", "failed"}
    controls = ""
    if actionable:
        controls = f"""<div class="row" style="margin-top:0.75rem">
  <form class="inline" method="post" action="/action/{action.id}/approve">{csrf}
    <button class="primary" type="submit">Approve and run</button></form>
  <form class="inline" method="post" action="/action/{action.id}/reject">{csrf}
    <button class="danger" type="submit">Reject</button></form>
</div>"""

    parts.append(
        f"""<div class="card">
  <div class="row">{_tag(action.status)}<span class="meta">#{action.id} · {escape(action.kind)}</span></div>
  <div style="margin-top:0.75rem">{save}</div>
  {controls}
</div>
<div class="card"><h2>Details</h2><table>{rows}</table></div>"""
    )
    return "".join(parts)


def status_page(
    rows: list[tuple[str, str]], *, token: str, kill_switch: bool, config_path: str
) -> str:
    table = "".join(
        f"<tr><td>{escape(label)}</td><td>{escape(value)}</td></tr>" for label, value in rows
    )
    csrf = f'<input type="hidden" name="csrf" value="{escape(token)}">'
    toggle = (
        f"""<form method="post" action="/resume">{csrf}
  <button class="primary" type="submit">Clear the kill switch</button></form>"""
        if kill_switch
        else f"""<form method="post" action="/stop">{csrf}
  <button class="danger" type="submit">Stop everything</button></form>"""
    )
    return f"""<div class="card"><h2>Now</h2><table>{table}</table>
  <div class="meta" style="margin-top:0.5rem">config: {escape(config_path)}</div></div>
<div class="card"><h2>Kill switch</h2>
  <div class="meta">Halts every action immediately, including a run in progress.</div>
  <div style="margin-top:0.75rem">{toggle}</div></div>"""


def login_page(error: str = "") -> str:
    warning = banner(error, "error") if error else ""
    return f"""{warning}<div class="card">
  <h2>Access token</h2>
  <div class="meta">Printed in the terminal when the dashboard started.</div>
  <form method="get" action="/" style="margin-top:0.75rem">
    <input type="password" name="token" placeholder="token" autocomplete="current-password"
      style="width:100%;padding:0.75rem;border-radius:10px;border:1px solid var(--border);
             background:var(--bg);color:var(--text);font:inherit">
    <div class="row" style="margin-top:0.5rem"><button class="primary" type="submit">Open</button></div>
  </form>
</div>"""
