"""Access-token persistence.

Tokens are written to a single JSON file with owner-only permissions, kept out
of the SQLite database so the database can be copied or inspected without
carrying credentials along with it.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ..errors import AuthError

# Refresh this many seconds before the token actually expires.
EXPIRY_MARGIN_SECONDS = 300


@dataclass(slots=True)
class TokenSet:
    access_token: str
    expires_at: str
    scope: str = ""
    refresh_token: str | None = None
    refresh_expires_at: str | None = None
    member_urn: str | None = None
    member_name: str | None = None
    obtained_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    @classmethod
    def from_response(
        cls,
        payload: dict,
        *,
        member_urn: str | None = None,
        member_name: str | None = None,
    ) -> TokenSet:
        now = datetime.now(UTC)
        expires_in = int(payload.get("expires_in", 0))
        refresh_expires_in = payload.get("refresh_token_expires_in")
        return cls(
            access_token=payload["access_token"],
            expires_at=(now + timedelta(seconds=expires_in)).isoformat(),
            scope=payload.get("scope", ""),
            refresh_token=payload.get("refresh_token"),
            refresh_expires_at=(
                (now + timedelta(seconds=int(refresh_expires_in))).isoformat()
                if refresh_expires_in
                else None
            ),
            member_urn=member_urn,
            member_name=member_name,
        )

    @property
    def expiry(self) -> datetime:
        return datetime.fromisoformat(self.expires_at)

    def expired(self, margin: int = EXPIRY_MARGIN_SECONDS) -> bool:
        return datetime.now(UTC) >= self.expiry - timedelta(seconds=margin)

    @property
    def seconds_remaining(self) -> int:
        return max(0, int((self.expiry - datetime.now(UTC)).total_seconds()))

    def can_refresh(self) -> bool:
        """LinkedIn only issues refresh tokens to approved applications."""
        if not self.refresh_token:
            return False
        if self.refresh_expires_at is None:
            return True
        return datetime.now(UTC) < datetime.fromisoformat(self.refresh_expires_at)


class TokenStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def exists(self) -> bool:
        return self.path.exists()

    def load(self) -> TokenSet:
        if not self.path.exists():
            raise AuthError(f"No stored LinkedIn token at {self.path}. Run: autopilot auth login")
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise AuthError(f"Token file {self.path} is unreadable: {exc}") from exc
        try:
            return TokenSet(**data)
        except TypeError as exc:
            raise AuthError(
                f"Token file {self.path} has an unexpected shape; delete it and log in again"
            ) from exc

    def load_or_none(self) -> TokenSet | None:
        try:
            return self.load()
        except AuthError:
            return None

    def save(self, tokens: TokenSet) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temporary file first so an interrupted write cannot leave a
        # half-written token behind, and create it unreadable to other users.
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(asdict(tokens), handle, indent=2)
        os.replace(tmp, self.path)
        os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)
