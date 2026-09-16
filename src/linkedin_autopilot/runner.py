"""The two jobs this tool actually runs: draft-and-post, and scan-and-engage.

Both are written so that the queueing path and the publishing path share the
same guardrails. Nothing here decides policy — it reads the decision out of the
config and the Guard, then does exactly that.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .api import LinkedInClient, publish_text_post
from .auth import get_valid_tokens
from .config import Config
from .content import ContentGenerator
from .errors import AuthError, AutopilotError, ContentRejected
from .logging_setup import get_logger
from .safety import Guard
from .store import ActionKind, ActionStatus, Store

log = get_logger(__name__)


@dataclass(slots=True)
class RunReport:
    """What a job did, in a form the CLI can print and a test can assert on."""

    queued: int = 0
    performed: int = 0
    skipped: int = 0
    failed: int = 0
    notes: list[str] = field(default_factory=list)

    def note(self, message: str) -> None:
        self.notes.append(message)
        log.info(message)

    @property
    def summary(self) -> str:
        return (
            f"{self.performed} performed, {self.queued} queued, "
            f"{self.skipped} skipped, {self.failed} failed"
        )


# ---------------------------------------------------------------------- posts


def publish_action(config: Config, store: Store, guard: Guard, action_id: int) -> str:
    """Publish one queued post. Returns the post URL.

    Raises rather than returning a report: this is called for a specific action
    the user asked to publish, so a failure needs to be visible.
    """
    action = store.get_action(action_id)
    if action is None:
        raise AutopilotError(f"No action with id {action_id}")
    if action.kind != ActionKind.POST:
        raise AutopilotError(f"Action {action_id} is a {action.kind}, not a post")
    if action.status == ActionStatus.DONE:
        raise AutopilotError(f"Action {action_id} was already published ({action.result_urn})")
    if not action.body:
        raise AutopilotError(f"Action {action_id} has no body to publish")

    guard.require(ActionKind.POST)

    if config.dry_run:
        store.update_action(action_id, status=ActionStatus.SKIPPED, error="dry run")
        return "(dry run — nothing was published)"

    tokens = get_valid_tokens(config)
    if not tokens.member_urn:
        raise AuthError("Stored token has no member URN. Run: autopilot auth login")

    try:
        with LinkedInClient(tokens.access_token) as client:
            result = publish_text_post(
                client,
                tokens.member_urn,
                action.body,
                visibility=config.posting.visibility,
            )
    except Exception as exc:
        store.update_action(action_id, status=ActionStatus.FAILED, error=str(exc))
        raise

    store.update_action(action_id, status=ActionStatus.DONE, result_urn=result.urn)
    guard.record(ActionKind.POST)
    topic = action.context.get("topic")
    if topic:
        store.record_topic(topic)
    return result.url


def run_post_job(
    config: Config,
    store: Store,
    guard: Guard,
    *,
    generator: ContentGenerator | None = None,
    topic: str | None = None,
    force_publish: bool = False,
) -> RunReport:
    """Draft a post, then either queue it for review or publish it."""
    report = RunReport()

    if not config.posting.enabled:
        report.note("Posting is disabled in the config; nothing to do.")
        report.skipped += 1
        return report

    decision = guard.check(ActionKind.POST)
    if not decision:
        report.note(f"Not posting: {decision.reason}")
        report.skipped += 1
        return report

    generator = generator or ContentGenerator(config)

    try:
        draft, chosen_topic = generator.generate_post(store, topic=topic)
    except ContentRejected as exc:
        report.note(f"Could not produce a usable draft: {exc}")
        report.failed += 1
        return report

    body = draft.rendered(
        include_hashtags=config.posting.hashtags.enabled,
        max_hashtags=config.posting.hashtags.max,
    )

    action_id = store.add_action(
        ActionKind.POST,
        status=ActionStatus.PENDING,
        body=body,
        context={"topic": chosen_topic, "rationale": draft.rationale},
    )
    report.queued += 1
    report.note(f"Queued post #{action_id} on '{chosen_topic}'")

    should_publish = force_publish or not config.posting.require_approval
    if not should_publish:
        report.note(f"Waiting for approval. Review with: autopilot review show {action_id}")
        return report

    try:
        url = publish_action(config, store, guard, action_id)
    except Exception as exc:
        report.failed += 1
        report.note(f"Publishing post #{action_id} failed: {exc}")
        return report

    if config.dry_run:
        report.note(f"Draft #{action_id} held: {url}")
        return report

    report.queued -= 1
    report.performed += 1
    report.note(f"Published: {url}")
    return report


# ----------------------------------------------------------------- engagement


def run_engagement_job(
    config: Config,
    store: Store,
    guard: Guard,
    *,
    generator: ContentGenerator | None = None,
    limit: int | None = None,
    debug: bool = False,
) -> RunReport:
    """Scan the feed, score what is there, and queue or perform engagement.

    In suggest mode this never writes to LinkedIn: it fills the review queue and
    stops. In auto mode it performs the actions that clear every guardrail.
    """
    report = RunReport()
    engagement = config.engagement

    if not engagement.enabled or engagement.mode == "off":
        report.note("Engagement is disabled in the config; nothing to do.")
        report.skipped += 1
        return report

    # Checked before the browser is launched, not just per action.
    if guard.kill_switch_engaged():
        report.note(f"Kill switch is present at {config.kill_switch_file}; not starting.")
        report.skipped += 1
        return report

    # Imported here so the package works without the browser extra installed.
    from .engagement.browser import open_session
    from .engagement.feed import read_feed
    from .engagement.scoring import score_post

    generator = generator or ContentGenerator(config)
    scorer = generator.score_relevance if engagement.llm_scoring else None
    writes_allowed = engagement.writes_allowed and not config.dry_run

    if engagement.mode == "auto" and config.dry_run:
        report.note("Mode is auto but dry_run is on, so nothing will actually be posted.")

    with open_session(config) as session:
        posts = read_feed(session, limit or engagement.feed_scan_limit, debug=debug)

        candidates = []
        for post in posts:
            if store.has_seen(post.urn):
                continue
            scored = score_post(post, engagement, llm_scorer=scorer)
            store.mark_seen(post.urn, post.author_key, scored.reason)
            if scored.score < engagement.min_relevance:
                report.skipped += 1
                continue
            candidates.append(scored)

        candidates.sort(key=lambda item: item.score, reverse=True)
        report.note(f"{len(candidates)} of {len(posts)} posts cleared the relevance bar")

        for scored in candidates:
            post = scored.post

            if engagement.likes.enabled and not post.already_liked:
                _handle_like(config, store, guard, session, scored, report, writes_allowed)

            if engagement.comments.enabled:
                _handle_comment(
                    config, store, guard, session, scored, report, writes_allowed, generator
                )

            if writes_allowed and (report.performed or report.queued):
                guard.pace()

    return report


def _handle_like(config, store, guard, session, scored, report, writes_allowed) -> None:
    from .engagement.actions import like_post

    post = scored.post
    if store.already_acted(post.urn, ActionKind.LIKE):
        return

    decision = guard.check(ActionKind.LIKE, author=post.author_key)
    if not decision:
        report.skipped += 1
        log.debug("skipping like on %s: %s", post.urn, decision.reason)
        return

    action_id = store.add_action(
        ActionKind.LIKE,
        status=ActionStatus.PENDING,
        target_urn=post.urn,
        target_author=post.author_key,
        context={
            "score": round(scored.score, 3),
            "reason": scored.reason,
            "author": post.author_name,
            "excerpt": post.excerpt,
        },
    )

    if not writes_allowed:
        report.queued += 1
        return

    try:
        outcome = like_post(session, post.urn, dry_run=config.dry_run)
    except Exception as exc:
        store.update_action(action_id, status=ActionStatus.FAILED, error=str(exc))
        report.failed += 1
        report.note(f"Like on {post.urn} failed: {exc}")
        return

    if outcome.performed:
        store.update_action(action_id, status=ActionStatus.DONE)
        guard.record(ActionKind.LIKE)
        report.performed += 1
    else:
        store.update_action(action_id, status=ActionStatus.SKIPPED, error=outcome.detail)
        report.skipped += 1


def _handle_comment(
    config, store, guard, session, scored, report, writes_allowed, generator
) -> None:
    from .engagement.actions import comment_on_post

    post = scored.post
    if store.already_acted(post.urn, ActionKind.COMMENT):
        return

    decision = guard.check(ActionKind.COMMENT, author=post.author_key)
    if not decision:
        report.skipped += 1
        log.debug("skipping comment on %s: %s", post.urn, decision.reason)
        return

    try:
        draft, validation = generator.generate_comment(author=post.author_name, post_text=post.text)
    except AutopilotError as exc:
        report.failed += 1
        report.note(f"Could not draft a comment for {post.urn}: {exc}")
        return

    if not draft.should_comment or not validation:
        report.skipped += 1
        log.info("No comment on %s: %s", post.urn, validation.summary or draft.reason)
        return

    action_id = store.add_action(
        ActionKind.COMMENT,
        status=ActionStatus.PENDING,
        target_urn=post.urn,
        target_author=post.author_key,
        body=draft.text,
        context={
            "score": round(scored.score, 3),
            "reason": draft.reason,
            "author": post.author_name,
            "excerpt": post.excerpt,
        },
    )

    if not writes_allowed:
        report.queued += 1
        return

    try:
        outcome = comment_on_post(session, post.urn, draft.text, dry_run=config.dry_run)
    except Exception as exc:
        store.update_action(action_id, status=ActionStatus.FAILED, error=str(exc))
        report.failed += 1
        report.note(f"Comment on {post.urn} failed: {exc}")
        return

    if outcome.performed:
        store.update_action(action_id, status=ActionStatus.DONE)
        guard.record(ActionKind.COMMENT)
        report.performed += 1
    else:
        store.update_action(action_id, status=ActionStatus.SKIPPED, error=outcome.detail)
        report.skipped += 1


# -------------------------------------------------------------- approved queue


def run_approved_queue(config: Config, store: Store, guard: Guard) -> RunReport:
    """Carry out everything a human has explicitly approved."""
    report = RunReport()

    if guard.kill_switch_engaged():
        report.note(f"Kill switch is present at {config.kill_switch_file}; nothing will run.")
        return report

    approved = store.list_actions(status=ActionStatus.APPROVED, limit=100)

    if not approved:
        report.note("Nothing in the queue is approved.")
        return report

    posts = [a for a in approved if a.kind == ActionKind.POST]
    engagements = [a for a in approved if a.kind != ActionKind.POST]

    for action in posts:
        try:
            url = publish_action(config, store, guard, action.id)
            if config.dry_run:
                report.skipped += 1
                report.note(f"#{action.id} held: {url}")
            else:
                report.performed += 1
                report.note(f"Published #{action.id}: {url}")
        except Exception as exc:
            store.update_action(action.id, status=ActionStatus.FAILED, error=str(exc))
            report.failed += 1
            report.note(f"Post #{action.id} failed: {exc}")

    if not engagements:
        return report

    from .engagement.actions import comment_on_post, like_post
    from .engagement.browser import open_session

    with open_session(config) as session:
        for action in engagements:
            decision = guard.check(ActionKind(action.kind), author=action.target_author)
            if not decision:
                report.skipped += 1
                report.note(f"#{action.id} held back: {decision.reason}")
                continue

            try:
                if action.kind == ActionKind.LIKE:
                    outcome = like_post(session, action.target_urn or "", dry_run=config.dry_run)
                else:
                    outcome = comment_on_post(
                        session,
                        action.target_urn or "",
                        action.body or "",
                        dry_run=config.dry_run,
                    )
            except Exception as exc:
                store.update_action(action.id, status=ActionStatus.FAILED, error=str(exc))
                report.failed += 1
                report.note(f"#{action.id} failed: {exc}")
                continue

            if outcome.performed:
                store.update_action(action.id, status=ActionStatus.DONE)
                guard.record(ActionKind(action.kind))
                report.performed += 1
                report.note(f"#{action.id} {outcome.detail}")
            else:
                store.update_action(action.id, status=ActionStatus.SKIPPED, error=outcome.detail)
                report.skipped += 1

            guard.pace()

    return report
