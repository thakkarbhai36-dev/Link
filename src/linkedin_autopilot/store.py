"""SQLite-backed state: the action queue, counters, and what we have already seen.

Everything that must survive a restart lives here. The database is small and
local; it is opened per-operation rather than held, so a long-running scheduler
and a one-off CLI command can share it safely.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- One row per proposed or completed action.
CREATE TABLE IF NOT EXISTS actions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kind          TEXT NOT NULL,          -- post | like | comment
    status        TEXT NOT NULL,          -- pending | approved | rejected | done | failed | skipped
    target_urn    TEXT,                   -- the post being acted on (null for our own posts)
    target_author TEXT,                   -- author identifier, for per-author caps
    body          TEXT,                   -- post text or comment text
    context       TEXT,                   -- JSON blob: score, topic, source post excerpt
    result_urn    TEXT,                   -- URN returned by LinkedIn on success
    error         TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_actions_status ON actions(status, kind);
CREATE INDEX IF NOT EXISTS idx_actions_target ON actions(target_urn);
CREATE INDEX IF NOT EXISTS idx_actions_author ON actions(target_author, kind, updated_at);

-- Posts we have already considered, so a feed rescan does not re-propose them.
CREATE TABLE IF NOT EXISTS seen_posts (
    urn           TEXT PRIMARY KEY,
    author        TEXT,
    first_seen_at TEXT NOT NULL,
    reason        TEXT
);

-- Daily counters, keyed by local date so caps line up with the user's day.
CREATE TABLE IF NOT EXISTS counters (
    day   TEXT NOT NULL,
    kind  TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, kind)
);

-- Topics used, to enforce the cooldown.
CREATE TABLE IF NOT EXISTS topic_history (
    topic    TEXT NOT NULL,
    used_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_topic_used ON topic_history(used_at);
"""


class ActionKind(StrEnum):
    POST = "post"
    LIKE = "like"
    COMMENT = "comment"


class ActionStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(slots=True)
class Action:
    id: int
    kind: str
    status: str
    target_urn: str | None
    target_author: str | None
    body: str | None
    context: dict[str, Any]
    result_urn: str | None
    error: str | None
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Action:
        raw_context = row["context"]
        try:
            context = json.loads(raw_context) if raw_context else {}
        except json.JSONDecodeError:
            context = {"_unparsed": raw_context}
        return cls(
            id=row["id"],
            kind=row["kind"],
            status=row["status"],
            target_urn=row["target_urn"],
            target_author=row["target_author"],
            body=row["body"],
            context=context,
            result_urn=row["result_urn"],
            error=row["error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


def _utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Store:
    """Thin, explicit data layer. No ORM, no lazy objects."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=15.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )

    # ---------------------------------------------------------------- actions

    def add_action(
        self,
        kind: ActionKind | str,
        *,
        status: ActionStatus | str = ActionStatus.PENDING,
        target_urn: str | None = None,
        target_author: str | None = None,
        body: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> int:
        now = _utcnow()
        with self._connect() as conn:
            cursor = conn.execute(
                """INSERT INTO actions
                   (kind, status, target_urn, target_author, body, context, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(kind),
                    str(status),
                    target_urn,
                    target_author,
                    body,
                    json.dumps(context or {}, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            return int(cursor.lastrowid or 0)

    def get_action(self, action_id: int) -> Action | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM actions WHERE id = ?", (action_id,)).fetchone()
        return Action.from_row(row) if row else None

    def list_actions(
        self,
        *,
        status: ActionStatus | str | None = None,
        kind: ActionKind | str | None = None,
        limit: int = 50,
    ) -> list[Action]:
        query = "SELECT * FROM actions"
        clauses: list[str] = []
        params: list[Any] = []
        if status is not None:
            clauses.append("status = ?")
            params.append(str(status))
        if kind is not None:
            clauses.append("kind = ?")
            params.append(str(kind))
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [Action.from_row(row) for row in rows]

    def update_action(
        self,
        action_id: int,
        *,
        status: ActionStatus | str | None = None,
        body: str | None = None,
        result_urn: str | None = None,
        error: str | None = None,
    ) -> None:
        sets: list[str] = ["updated_at = ?"]
        params: list[Any] = [_utcnow()]
        if status is not None:
            sets.append("status = ?")
            params.append(str(status))
        if body is not None:
            sets.append("body = ?")
            params.append(body)
        if result_urn is not None:
            sets.append("result_urn = ?")
            params.append(result_urn)
        if error is not None:
            sets.append("error = ?")
            params.append(error)
        params.append(action_id)
        with self._connect() as conn:
            conn.execute(f"UPDATE actions SET {', '.join(sets)} WHERE id = ?", params)

    def pending_count(self, kind: ActionKind | str | None = None) -> int:
        query = "SELECT COUNT(*) AS n FROM actions WHERE status = 'pending'"
        params: list[Any] = []
        if kind is not None:
            query += " AND kind = ?"
            params.append(str(kind))
        with self._connect() as conn:
            return int(conn.execute(query, params).fetchone()["n"])

    # ------------------------------------------------------------ dedupe/seen

    def has_seen(self, urn: str) -> bool:
        with self._connect() as conn:
            row = conn.execute("SELECT 1 FROM seen_posts WHERE urn = ?", (urn,)).fetchone()
        return row is not None

    def mark_seen(self, urn: str, author: str | None = None, reason: str = "") -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO seen_posts (urn, author, first_seen_at, reason)
                   VALUES (?, ?, ?, ?)""",
                (urn, author, _utcnow(), reason),
            )

    def already_acted(self, urn: str, kind: ActionKind | str) -> bool:
        """True if we have queued or completed this action on this post before."""
        with self._connect() as conn:
            row = conn.execute(
                """SELECT 1 FROM actions
                   WHERE target_urn = ? AND kind = ?
                     AND status IN ('pending', 'approved', 'done')""",
                (urn, str(kind)),
            ).fetchone()
        return row is not None

    # -------------------------------------------------------------- counters

    def bump_counter(self, kind: ActionKind | str, local_day: date, amount: int = 1) -> int:
        day = local_day.isoformat()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO counters (day, kind, count) VALUES (?, ?, ?)
                   ON CONFLICT(day, kind) DO UPDATE SET count = count + excluded.count""",
                (day, str(kind), amount),
            )
            row = conn.execute(
                "SELECT count FROM counters WHERE day = ? AND kind = ?", (day, str(kind))
            ).fetchone()
        return int(row["count"])

    def counter(self, kind: ActionKind | str, local_day: date) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT count FROM counters WHERE day = ? AND kind = ?",
                (local_day.isoformat(), str(kind)),
            ).fetchone()
        return int(row["count"]) if row else 0

    def total_actions_today(self, local_day: date) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(count), 0) AS n FROM counters WHERE day = ?",
                (local_day.isoformat(),),
            ).fetchone()
        return int(row["n"])

    def author_action_count(self, author: str, kind: ActionKind | str, days: int = 7) -> int:
        """How many times we have acted on one author recently, for per-author caps."""
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")
        with self._connect() as conn:
            row = conn.execute(
                """SELECT COUNT(*) AS n FROM actions
                   WHERE target_author = ? AND kind = ?
                     AND status IN ('pending', 'approved', 'done')
                     AND updated_at >= ?""",
                (author, str(kind), cutoff),
            ).fetchone()
        return int(row["n"])

    # --------------------------------------------------------------- topics

    def record_topic(self, topic: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO topic_history (topic, used_at) VALUES (?, ?)", (topic, _utcnow())
            )

    def topic_last_used(self) -> dict[str, str]:
        """Map of topic to the ISO timestamp it was last used, for rotation."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT topic, MAX(used_at) AS last_used FROM topic_history GROUP BY topic"
            ).fetchall()
        return {row["topic"]: row["last_used"] for row in rows}

    def recent_topics(self, within_days: int) -> set[str]:
        if within_days <= 0:
            return set()
        cutoff = (datetime.now(UTC) - timedelta(days=within_days)).isoformat(timespec="seconds")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT topic FROM topic_history WHERE used_at >= ?", (cutoff,)
            ).fetchall()
        return {row["topic"] for row in rows}

    # ------------------------------------------------------------------ meta

    def set_meta(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default
