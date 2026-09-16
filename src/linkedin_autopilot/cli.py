"""Command line interface."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Annotated

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from .config import Config, load_config
from .errors import AutopilotError
from .logging_setup import get_logger, setup_logging
from .safety import Guard
from .store import Action, ActionKind, ActionStatus, Store

console = Console()
log = get_logger(__name__)

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Automate your own LinkedIn account, with a human in the loop.",
)
auth_app = typer.Typer(no_args_is_help=True, help="Connect and inspect your LinkedIn account.")
post_app = typer.Typer(no_args_is_help=True, help="Draft and publish posts.")
engage_app = typer.Typer(no_args_is_help=True, help="Scan the feed and queue engagement.")
review_app = typer.Typer(no_args_is_help=True, help="Approve or reject queued actions.")
browser_app = typer.Typer(no_args_is_help=True, help="Manage the browser session.")

app.add_typer(auth_app, name="auth")
app.add_typer(post_app, name="post")
app.add_typer(engage_app, name="engage")
app.add_typer(review_app, name="review")
app.add_typer(browser_app, name="browser")

ConfigOption = Annotated[Path | None, typer.Option("--config", "-c", help="Path to config.yaml.")]


def _bootstrap(config_path: Path | None = None) -> tuple[Config, Store, Guard]:
    """Load everything a command needs, or exit with a readable message."""
    load_dotenv()
    try:
        config = load_config(config_path)
    except AutopilotError as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(code=2) from exc
    setup_logging(log_dir=config.log_dir)
    store = Store(config.db_path)
    return config, store, Guard(config, store)


def _fail(exc: Exception) -> None:
    # Escaped: messages carry things like ".[browser]", which rich would
    # otherwise read as a markup tag and silently drop.
    console.print(f"[red]{escape(str(exc))}[/red]")
    raise typer.Exit(code=1)


def _edit_text(initial: str) -> str | None:
    """Open $EDITOR on a scratch file and return what came back.

    Returns None when no editor is available or the editor exits non-zero, so
    the caller can leave the draft untouched rather than blanking it.
    """
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if not editor:
        for candidate in ("sensible-editor", "nano", "vim", "vi"):
            if shutil.which(candidate):
                editor = candidate
                break
    if not editor:
        console.print(
            "[yellow]No editor found. Set $EDITOR, or edit the draft another way.[/yellow]"
        )
        return None

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8"
    ) as handle:
        handle.write(initial)
        path = Path(handle.name)

    try:
        result = subprocess.run([*shlex.split(editor), str(path)], check=False)
        if result.returncode != 0:
            console.print(f"[yellow]Editor exited with status {result.returncode}.[/yellow]")
            return None
        return path.read_text(encoding="utf-8")
    finally:
        path.unlink(missing_ok=True)


def _action_table(actions: list[Action], title: str) -> Table:
    table = Table(title=title, show_lines=False)
    table.add_column("#", justify="right", style="bold")
    table.add_column("kind")
    table.add_column("status")
    table.add_column("author")
    table.add_column("preview", overflow="fold")

    for action in actions:
        preview = (action.body or action.context.get("excerpt") or "").strip()
        preview = escape(" ".join(preview.split())[:90])
        colour = {
            "pending": "yellow",
            "approved": "cyan",
            "done": "green",
            "failed": "red",
            "rejected": "dim",
            "skipped": "dim",
        }.get(action.status, "white")
        table.add_row(
            str(action.id),
            action.kind,
            f"[{colour}]{action.status}[/{colour}]",
            (action.context.get("author") or "—")[:24],
            preview or "—",
        )
    return table


# ------------------------------------------------------------------ top level


@app.command()
def init(
    force: Annotated[bool, typer.Option("--force", help="Overwrite existing files.")] = False,
) -> None:
    """Create config.yaml and .env from the bundled examples."""
    root = Path.cwd()
    pairs = [
        ("config.example.yaml", "config.example.yaml", "config.yaml"),
        (".env.example", "env.example", ".env"),
    ]
    package_dir = Path(__file__).resolve().parent
    repo_root = package_dir.parents[1]

    for repo_name, packaged_name, target_name in pairs:
        # A clone has these at the repository root; a wheel carries them inside
        # the package. Look in the working directory first either way.
        candidates = [
            root / repo_name,
            package_dir / "examples" / packaged_name,
            repo_root / repo_name,
        ]
        source = next((path for path in candidates if path.exists()), None)
        target = root / target_name

        if source is None:
            console.print(f"[yellow]Skipping {target_name}: {repo_name} not found[/yellow]")
            continue
        if target.exists() and not force:
            console.print(f"[dim]{target_name} already exists; leaving it alone[/dim]")
            continue
        shutil.copy(source, target)
        console.print(f"[green]Wrote {target_name}[/green]")

    console.print(
        Panel(
            "1. Put your keys in [bold].env[/bold]\n"
            "2. Edit [bold]config.yaml[/bold]: timezone, topics, voice\n"
            "3. Run [bold]autopilot auth login[/bold]\n"
            "4. Run [bold]autopilot post draft[/bold] to see a draft\n\n"
            "[dim]dry_run starts on. Nothing reaches LinkedIn until you turn it off.[/dim]",
            title="Next steps",
        )
    )


@app.command()
def status(config: ConfigOption = None) -> None:
    """Show current limits, quotas, and queue depth."""
    cfg, store, guard = _bootstrap(config)

    table = Table(title="Autopilot status", show_header=False)
    table.add_column("", style="bold")
    table.add_column("")
    for label, value in guard.describe():
        table.add_row(label, value)
    table.add_row("config", str(cfg.source_path))
    table.add_row("database", str(cfg.db_path))
    table.add_row("pending review", str(store.pending_count()))
    console.print(table)

    tokens_present = cfg.token_path.exists()
    console.print(
        "LinkedIn token: [green]present[/green]"
        if tokens_present
        else "LinkedIn token: [yellow]not set up — run: autopilot auth login[/yellow]"
    )


@app.command()
def doctor(config: ConfigOption = None) -> None:
    """Check that everything needed to run is actually in place."""
    cfg, _store, _guard = _bootstrap(config)
    problems: list[str] = []
    notes: list[str] = []

    if not cfg.secrets.anthropic_api_key:
        notes.append(
            "ANTHROPIC_API_KEY is not set. The SDK also reads an `ant auth login` "
            "profile, so this may still work."
        )
    if not cfg.secrets.linkedin_client_id or not cfg.secrets.linkedin_client_secret:
        problems.append("LINKEDIN_CLIENT_ID / LINKEDIN_CLIENT_SECRET are not set in .env")
    if not cfg.token_path.exists():
        problems.append("No LinkedIn token yet. Run: autopilot auth login")

    if cfg.engagement.enabled and cfg.engagement.mode != "off":
        try:
            import playwright  # noqa: F401
        except ImportError:
            problems.append(
                'Engagement is enabled but Playwright is missing: pip install -e ".[browser]"'
            )
        profile = Path(cfg.browser.user_data_dir)
        if not profile.exists():
            problems.append("No browser profile yet. Run: autopilot browser login")

    if cfg.dry_run:
        notes.append("dry_run is on: nothing will be published.")
    if cfg.engagement.mode == "auto":
        notes.append("Engagement mode is auto: likes and comments go out without review.")

    for note in notes:
        console.print(f"[yellow]note[/yellow]  {escape(note)}")
    for problem in problems:
        console.print(f"[red]problem[/red]  {escape(problem)}")

    if not problems:
        console.print("\n[green]Ready to run.[/green]")
    else:
        raise typer.Exit(code=1)


@app.command()
def run(config: ConfigOption = None) -> None:
    """Start the scheduler and keep running."""
    cfg, store, guard = _bootstrap(config)
    from .scheduler import run_forever

    try:
        run_forever(cfg, store, guard)
    except AutopilotError as exc:
        _fail(exc)


@app.command()
def stop(config: ConfigOption = None) -> None:
    """Engage the kill switch: halt all activity immediately."""
    cfg, _store, _guard = _bootstrap(config)
    cfg.kill_switch_file.parent.mkdir(parents=True, exist_ok=True)
    cfg.kill_switch_file.touch()
    console.print(
        f"[red]Kill switch engaged[/red] at {cfg.kill_switch_file}.\n"
        "Nothing will run until you delete that file, or run: autopilot resume"
    )


@app.command()
def resume(config: ConfigOption = None) -> None:
    """Clear the kill switch."""
    cfg, _store, _guard = _bootstrap(config)
    if cfg.kill_switch_file.exists():
        cfg.kill_switch_file.unlink()
        console.print("[green]Kill switch cleared.[/green]")
    else:
        console.print("[dim]The kill switch was not engaged.[/dim]")


# ------------------------------------------------------------------------ auth


@auth_app.command("login")
def auth_login(
    config: ConfigOption = None,
    no_browser: Annotated[
        bool, typer.Option("--no-browser", help="Print the URL instead of opening a browser.")
    ] = False,
) -> None:
    """Authorize this tool to post on your behalf."""
    cfg, _store, _guard = _bootstrap(config)
    from .auth import authorize_interactive

    try:
        tokens = authorize_interactive(cfg, open_browser=not no_browser)
    except AutopilotError as exc:
        _fail(exc)
        return

    console.print(
        Panel(
            f"Signed in as [bold]{tokens.member_name or 'unknown'}[/bold]\n"
            f"Member URN: {tokens.member_urn}\n"
            f"Scopes: {tokens.scope}\n"
            f"Token valid for about {tokens.seconds_remaining // 3600} hours",
            title="Connected",
        )
    )


@auth_app.command("status")
def auth_status(config: ConfigOption = None) -> None:
    """Show the stored LinkedIn token."""
    cfg, _store, _guard = _bootstrap(config)
    from .auth import TokenStore

    tokens = TokenStore(cfg.token_path).load_or_none()
    if tokens is None:
        console.print("[yellow]No stored token. Run: autopilot auth login[/yellow]")
        raise typer.Exit(code=1)

    table = Table(show_header=False)
    table.add_column("", style="bold")
    table.add_column("")
    table.add_row("member", tokens.member_name or "—")
    table.add_row("urn", tokens.member_urn or "—")
    table.add_row("scopes", tokens.scope or "—")
    table.add_row(
        "expires",
        "[red]expired[/red]"
        if tokens.expired(margin=0)
        else f"in {tokens.seconds_remaining // 3600}h {tokens.seconds_remaining % 3600 // 60}m",
    )
    table.add_row("refreshable", "yes" if tokens.can_refresh() else "no")
    console.print(table)


@auth_app.command("logout")
def auth_logout(config: ConfigOption = None) -> None:
    """Delete the stored LinkedIn token."""
    cfg, _store, _guard = _bootstrap(config)
    from .auth import TokenStore

    TokenStore(cfg.token_path).clear()
    console.print("[green]Token deleted.[/green]")


# ------------------------------------------------------------------------ post


@post_app.command("draft")
def post_draft(
    config: ConfigOption = None,
    topic: Annotated[
        str | None, typer.Option("--topic", help="Override the rotation for this draft.")
    ] = None,
) -> None:
    """Generate a post and put it in the review queue."""
    cfg, store, guard = _bootstrap(config)
    from .runner import run_post_job

    try:
        report = run_post_job(cfg, store, guard, topic=topic, force_publish=False)
    except AutopilotError as exc:
        _fail(exc)
        return

    latest = store.list_actions(kind=ActionKind.POST, limit=1)
    if latest and latest[0].status == ActionStatus.PENDING:
        action = latest[0]
        console.print(
            Panel(
                escape(action.body or ""),
                title=f"Draft #{action.id} — {action.context.get('topic', 'no topic')}",
                subtitle=f"{len(action.body or '')} characters",
            )
        )
        if action.context.get("rationale"):
            console.print(f"[dim]Angle: {action.context['rationale']}[/dim]")
        console.print(f"\nPublish with: [bold]autopilot review approve {action.id}[/bold]")
    console.print(f"[dim]{report.summary}[/dim]")


@post_app.command("now")
def post_now(
    config: ConfigOption = None,
    topic: Annotated[str | None, typer.Option("--topic")] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation prompt.")] = False,
) -> None:
    """Generate a post and publish it straight away."""
    cfg, store, guard = _bootstrap(config)
    from .runner import run_post_job

    if not yes and not cfg.dry_run:
        typer.confirm("This publishes to your live LinkedIn feed. Continue?", abort=True)

    try:
        report = run_post_job(cfg, store, guard, topic=topic, force_publish=True)
    except AutopilotError as exc:
        _fail(exc)
        return
    for note in report.notes:
        console.print(escape(note))


# ---------------------------------------------------------------------- engage


@engage_app.command("scan")
def engage_scan(
    config: ConfigOption = None,
    limit: Annotated[int | None, typer.Option("--limit", help="Posts to read.")] = None,
    debug: Annotated[
        bool, typer.Option("--debug", help="Save a screenshot when a selector fails.")
    ] = False,
) -> None:
    """Read the feed and queue engagement for review. Never writes."""
    cfg, store, guard = _bootstrap(config)
    from .runner import run_engagement_job

    if cfg.engagement.mode == "auto":
        console.print("[yellow]Mode is auto; this scan will act, not just queue.[/yellow]")

    try:
        report = run_engagement_job(cfg, store, guard, limit=limit, debug=debug)
    except AutopilotError as exc:
        _fail(exc)
        return

    console.print(f"[bold]{report.summary}[/bold]")
    pending = store.list_actions(status=ActionStatus.PENDING, limit=25)
    if pending:
        console.print(_action_table(pending, "Waiting for review"))
        console.print("\nApprove with: [bold]autopilot review approve <id>[/bold]")


# ---------------------------------------------------------------------- review


@review_app.command("list")
def review_list(
    config: ConfigOption = None,
    status_filter: Annotated[
        str | None, typer.Option("--status", help="pending, approved, done, failed…")
    ] = "pending",
    limit: Annotated[int, typer.Option("--limit")] = 30,
) -> None:
    """List queued actions."""
    _cfg, store, _guard = _bootstrap(config)
    actions = store.list_actions(status=status_filter, limit=limit)
    if not actions:
        console.print(f"[dim]Nothing with status '{status_filter}'.[/dim]")
        return
    console.print(_action_table(actions, f"Actions ({status_filter})"))


@review_app.command("show")
def review_show(
    action_id: Annotated[int, typer.Argument(help="The action id from `review list`.")],
    config: ConfigOption = None,
) -> None:
    """Show one queued action in full."""
    _cfg, store, _guard = _bootstrap(config)
    action = store.get_action(action_id)
    if action is None:
        console.print(f"[red]No action with id {action_id}[/red]")
        raise typer.Exit(code=1)

    console.print(
        Panel(
            escape(action.body) if action.body else "[dim](no body — this is a like)[/dim]",
            title=f"#{action.id} · {action.kind} · {action.status}",
        )
    )
    if action.context:
        table = Table(show_header=False, box=None)
        for key, value in action.context.items():
            table.add_row(f"[dim]{key}[/dim]", str(value))
        console.print(table)
    if action.target_urn:
        console.print(f"[dim]https://www.linkedin.com/feed/update/{action.target_urn}/[/dim]")
    if action.error:
        console.print(f"[red]error: {escape(action.error)}[/red]")


@review_app.command("edit")
def review_edit(
    action_id: Annotated[int, typer.Argument()],
    config: ConfigOption = None,
) -> None:
    """Open a queued draft in your editor and save the changes."""
    _cfg, store, _guard = _bootstrap(config)
    action = store.get_action(action_id)
    if action is None:
        console.print(f"[red]No action with id {action_id}[/red]")
        raise typer.Exit(code=1)

    original = action.body or ""
    edited = _edit_text(original)
    if edited is None or edited.strip() == original.strip():
        console.print("[dim]No changes.[/dim]")
        return
    store.update_action(action_id, body=edited.strip())
    console.print(f"[green]Updated #{action_id}.[/green]")


@review_app.command("approve")
def review_approve(
    action_id: Annotated[int, typer.Argument()],
    config: ConfigOption = None,
    later: Annotated[
        bool, typer.Option("--later", help="Mark approved but do not act now.")
    ] = False,
) -> None:
    """Approve a queued action and carry it out."""
    cfg, store, guard = _bootstrap(config)
    action = store.get_action(action_id)
    if action is None:
        console.print(f"[red]No action with id {action_id}[/red]")
        raise typer.Exit(code=1)

    store.update_action(action_id, status=ActionStatus.APPROVED)
    if later:
        console.print(f"[green]#{action_id} approved.[/green] It runs on the next queue pass.")
        return

    from .runner import run_approved_queue

    try:
        report = run_approved_queue(cfg, store, guard)
    except AutopilotError as exc:
        _fail(exc)
        return
    for note in report.notes:
        console.print(escape(note))


@review_app.command("reject")
def review_reject(
    action_id: Annotated[int, typer.Argument()],
    config: ConfigOption = None,
) -> None:
    """Reject a queued action so it is never carried out."""
    _cfg, store, _guard = _bootstrap(config)
    store.update_action(action_id, status=ActionStatus.REJECTED)
    console.print(f"[dim]#{action_id} rejected.[/dim]")


@review_app.command("run")
def review_run(config: ConfigOption = None) -> None:
    """Carry out everything already approved."""
    cfg, store, guard = _bootstrap(config)
    from .runner import run_approved_queue

    try:
        report = run_approved_queue(cfg, store, guard)
    except AutopilotError as exc:
        _fail(exc)
        return
    for note in report.notes:
        console.print(escape(note))
    console.print(f"[dim]{report.summary}[/dim]")


# --------------------------------------------------------------------- browser


@browser_app.command("login")
def browser_login(config: ConfigOption = None) -> None:
    """Open a browser so you can sign in to LinkedIn by hand."""
    cfg, _store, _guard = _bootstrap(config)
    from .engagement.browser import interactive_login

    try:
        interactive_login(cfg)
    except AutopilotError as exc:
        _fail(exc)


@browser_app.command("check")
def browser_check(config: ConfigOption = None) -> None:
    """Confirm the saved browser session is still signed in."""
    cfg, _store, _guard = _bootstrap(config)
    from .engagement.browser import BrowserSession

    try:
        with BrowserSession(cfg) as session:
            session.goto_feed()
            if session.is_logged_in():
                console.print("[green]Signed in.[/green]")
            else:
                console.print("[yellow]Not signed in. Run: autopilot browser login[/yellow]")
                raise typer.Exit(code=1)
    except AutopilotError as exc:
        _fail(exc)


def main() -> None:
    try:
        app()
    except AutopilotError as exc:  # Anything that escaped a command handler.
        console.print(f"[red]{escape(str(exc))}[/red]")
        sys.exit(1)


if __name__ == "__main__":
    main()
