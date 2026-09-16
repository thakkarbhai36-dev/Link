"""Guardrails. Every write to LinkedIn passes through Guard.check() first.

The rules here are deliberately conservative and all of them are local: nothing
in this module asks LinkedIn for permission, it just refuses to act when the
configured limits say no. Order matters — the kill switch is checked first so a
stuck run can always be stopped by touching a file.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from datetime import date, datetime

from .config import Config
from .errors import SafetyStop
from .logging_setup import get_logger
from .store import ActionKind, Store

log = get_logger(__name__)


@dataclass(slots=True)
class Decision:
    """The outcome of a guardrail check. Falsy when the action must not proceed."""

    allowed: bool
    reason: str = ""

    def __bool__(self) -> bool:
        return self.allowed

    @classmethod
    def ok(cls) -> Decision:
        return cls(True)

    @classmethod
    def no(cls, reason: str) -> Decision:
        return cls(False, reason)


class Guard:
    """Holds the config and store needed to answer 'may I do this right now?'."""

    def __init__(self, config: Config, store: Store) -> None:
        self.config = config
        self.store = store

    # ------------------------------------------------------------- primitives

    def kill_switch_engaged(self) -> bool:
        return self.config.kill_switch_file.exists()

    def now_local(self) -> datetime:
        return datetime.now(self.config.tz)

    def today_local(self) -> date:
        return self.now_local().date()

    def within_active_hours(self, at: datetime | None = None) -> bool:
        moment = (at or self.now_local()).time()
        hours = self.config.safety.active_hours
        return hours.start_time <= moment <= hours.end_time

    # ----------------------------------------------------------------- quotas

    def _per_kind_cap(self, kind: ActionKind) -> int:
        match kind:
            case ActionKind.POST:
                return self.config.posting.max_per_day
            case ActionKind.LIKE:
                return self.config.engagement.likes.max_per_day
            case ActionKind.COMMENT:
                return self.config.engagement.comments.max_per_day
        return 0

    def _per_author_cap(self, kind: ActionKind) -> int | None:
        match kind:
            case ActionKind.LIKE:
                return self.config.engagement.likes.max_per_author_per_week
            case ActionKind.COMMENT:
                return self.config.engagement.comments.max_per_author_per_week
        return None

    def quota_remaining(self, kind: ActionKind) -> int:
        """How many more of this action type today, accounting for the global cap."""
        today = self.today_local()
        per_kind = self._per_kind_cap(kind) - self.store.counter(kind, today)
        overall = self.config.safety.max_actions_per_day - self.store.total_actions_today(today)
        return max(0, min(per_kind, overall))

    # ------------------------------------------------------------- main check

    def check(self, kind: ActionKind, *, author: str | None = None) -> Decision:
        """The single gate. Returns a Decision rather than raising, so callers
        can log and move on to the next candidate instead of aborting the run."""
        if self.kill_switch_engaged():
            return Decision.no(f"kill switch present at {self.config.kill_switch_file}")

        now = self.now_local()
        if not self.within_active_hours(now):
            hours = self.config.safety.active_hours
            return Decision.no(
                f"outside active hours {hours.start}-{hours.end} (local time {now:%H:%M})"
            )

        today = now.date()
        total_today = self.store.total_actions_today(today)
        if total_today >= self.config.safety.max_actions_per_day:
            return Decision.no(
                f"daily ceiling reached ({total_today}/{self.config.safety.max_actions_per_day} "
                "actions of all kinds)"
            )

        used = self.store.counter(kind, today)
        cap = self._per_kind_cap(kind)
        if used >= cap:
            return Decision.no(f"daily {kind} limit reached ({used}/{cap})")

        author_cap = self._per_author_cap(kind)
        if author_cap is not None and author:
            seen = self.store.author_action_count(author, kind, days=7)
            if seen >= author_cap:
                return Decision.no(
                    f"weekly per-author {kind} limit reached for {author} ({seen}/{author_cap})"
                )

        return Decision.ok()

    def require(self, kind: ActionKind, *, author: str | None = None) -> None:
        """Same as check(), but raises. Use where refusal should abort the run."""
        decision = self.check(kind, author=author)
        if not decision:
            raise SafetyStop(decision.reason)

    # ------------------------------------------------------------ bookkeeping

    def record(self, kind: ActionKind) -> None:
        """Count a completed action against today's quotas."""
        self.store.bump_counter(kind, self.today_local())

    def pace(self, sleeper=time.sleep) -> float:
        """Sleep a random interval between actions. Returns the seconds waited.

        A fixed delay is its own signature; the jitter is the point.
        """
        low = self.config.safety.min_seconds_between_actions
        high = self.config.safety.max_seconds_between_actions
        if high <= 0:
            return 0.0
        delay = random.uniform(low, high)
        log.debug("pacing for %.1fs", delay)
        sleeper(delay)
        return delay

    def schedule_jitter(self) -> int:
        """A random offset in seconds to shift a scheduled job by."""
        span = self.config.safety.schedule_jitter_seconds
        return random.randint(0, span) if span > 0 else 0

    def describe(self) -> list[tuple[str, str]]:
        """Human-readable current state, for `autopilot status`."""
        today = self.today_local()
        rows = [
            ("dry run", "on — nothing will be published" if self.config.dry_run else "off"),
            (
                "kill switch",
                "ENGAGED"
                if self.kill_switch_engaged()
                else f"clear ({self.config.kill_switch_file})",
            ),
            (
                "active hours",
                f"{self.config.safety.active_hours.start}-{self.config.safety.active_hours.end} "
                f"({'inside' if self.within_active_hours() else 'outside'} right now)",
            ),
            (
                "actions today",
                f"{self.store.total_actions_today(today)}/{self.config.safety.max_actions_per_day}",
            ),
        ]
        for kind in (ActionKind.POST, ActionKind.LIKE, ActionKind.COMMENT):
            rows.append(
                (
                    f"{kind} today",
                    f"{self.store.counter(kind, today)}/{self._per_kind_cap(kind)}",
                )
            )
        return rows
