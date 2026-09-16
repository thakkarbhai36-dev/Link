"""Configuration models and loading.

Structure lives in config.yaml; secrets live in the environment. The two are
kept apart so the config file is safe to read, diff, and share.
"""

from __future__ import annotations

import os
from datetime import time as dtime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from .errors import ConfigError

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _parse_hhmm(value: str, field: str) -> dtime:
    try:
        hour, minute = value.strip().split(":")
        return dtime(hour=int(hour), minute=int(minute))
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{field} must look like HH:MM, got {value!r}") from exc


class ActiveHours(BaseModel):
    start: str = "08:00"
    end: str = "21:00"

    @property
    def start_time(self) -> dtime:
        return _parse_hhmm(self.start, "safety.active_hours.start")

    @property
    def end_time(self) -> dtime:
        return _parse_hhmm(self.end, "safety.active_hours.end")

    @model_validator(mode="after")
    def _check(self) -> ActiveHours:
        if self.start_time >= self.end_time:
            raise ValueError("safety.active_hours.start must be earlier than end")
        return self


class LLMConfig(BaseModel):
    model: str = "claude-opus-5"
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    max_tokens: int = Field(default=8000, ge=256, le=128_000)
    server_side_fallbacks: bool = True


class HashtagConfig(BaseModel):
    enabled: bool = True
    max: int = Field(default=3, ge=0, le=10)
    pool: list[str] = Field(default_factory=list)

    @field_validator("pool")
    @classmethod
    def _normalise(cls, pool: list[str]) -> list[str]:
        return [tag if tag.startswith("#") else f"#{tag}" for tag in pool]


class PostingConfig(BaseModel):
    enabled: bool = True
    times: list[str] = Field(default_factory=lambda: ["09:15"])
    days: list[str] = Field(default_factory=lambda: ["mon", "tue", "wed", "thu", "fri"])
    require_approval: bool = True
    max_per_day: int = Field(default=1, ge=0, le=5)
    visibility: Literal["PUBLIC", "CONNECTIONS"] = "PUBLIC"
    max_chars: int = Field(default=2600, ge=100, le=3000)
    voice: str = ""
    topics: list[str] = Field(default_factory=list)
    topic_cooldown_days: int = Field(default=14, ge=0)
    hashtags: HashtagConfig = Field(default_factory=HashtagConfig)
    banned_phrases: list[str] = Field(default_factory=list)

    @field_validator("days")
    @classmethod
    def _check_days(cls, days: list[str]) -> list[str]:
        lowered = [d.strip().lower()[:3] for d in days]
        bad = [d for d in lowered if d not in DAYS]
        if bad:
            raise ValueError(f"unknown day(s) {bad}; use {', '.join(DAYS)}")
        return lowered

    @field_validator("times")
    @classmethod
    def _check_times(cls, times: list[str]) -> list[str]:
        for value in times:
            _parse_hhmm(value, "posting.times")
        return times


class LikeLimits(BaseModel):
    enabled: bool = True
    max_per_day: int = Field(default=15, ge=0, le=100)
    max_per_author_per_week: int = Field(default=3, ge=0, le=50)


class CommentLimits(BaseModel):
    enabled: bool = True
    max_per_day: int = Field(default=5, ge=0, le=50)
    max_per_author_per_week: int = Field(default=1, ge=0, le=20)
    min_chars: int = Field(default=120, ge=1)
    max_chars: int = Field(default=600, ge=10, le=1250)
    reject_if_generic: bool = True
    no_links: bool = True
    no_hashtags: bool = True

    @model_validator(mode="after")
    def _lengths(self) -> CommentLimits:
        if self.min_chars >= self.max_chars:
            raise ValueError("comments.min_chars must be below comments.max_chars")
        return self


class EngagementConfig(BaseModel):
    enabled: bool = False
    mode: Literal["off", "suggest", "auto"] = "suggest"
    first_degree_only: bool = True
    interests: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)
    min_relevance: float = Field(default=0.55, ge=0.0, le=1.0)
    llm_scoring: bool = True
    likes: LikeLimits = Field(default_factory=LikeLimits)
    comments: CommentLimits = Field(default_factory=CommentLimits)
    feed_scan_limit: int = Field(default=40, ge=1, le=200)
    max_post_age_hours: int = Field(default=48, ge=1)

    @property
    def writes_allowed(self) -> bool:
        """True only in auto mode. suggest mode never touches anyone's post."""
        return self.enabled and self.mode == "auto"


class SafetyConfig(BaseModel):
    active_hours: ActiveHours = Field(default_factory=ActiveHours)
    min_seconds_between_actions: int = Field(default=45, ge=0)
    max_seconds_between_actions: int = Field(default=180, ge=0)
    schedule_jitter_seconds: int = Field(default=900, ge=0)
    max_actions_per_day: int = Field(default=25, ge=0)

    @model_validator(mode="after")
    def _ordering(self) -> SafetyConfig:
        if self.min_seconds_between_actions > self.max_seconds_between_actions:
            raise ValueError("safety.min_seconds_between_actions exceeds the max")
        return self


class BrowserConfig(BaseModel):
    user_data_dir: Path = Path("./state/browser-profile")
    headless: bool = False
    screenshot_dir: Path = Path("./state/screenshots")
    nav_timeout_ms: int = Field(default=30_000, ge=1000)


class Secrets(BaseModel):
    """Read from the environment, never from the config file."""

    anthropic_api_key: str | None = None
    linkedin_client_id: str | None = None
    linkedin_client_secret: str | None = None
    linkedin_redirect_uri: str = "http://localhost:8765/callback"

    @classmethod
    def from_env(cls) -> Secrets:
        return cls(
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
            linkedin_client_id=os.getenv("LINKEDIN_CLIENT_ID") or None,
            linkedin_client_secret=os.getenv("LINKEDIN_CLIENT_SECRET") or None,
            linkedin_redirect_uri=os.getenv("LINKEDIN_REDIRECT_URI")
            or "http://localhost:8765/callback",
        )


class Config(BaseModel):
    timezone: str = "UTC"
    dry_run: bool = True
    kill_switch_file: Path = Path("./state/STOP")
    state_dir: Path = Path("./state")
    llm: LLMConfig = Field(default_factory=LLMConfig)
    posting: PostingConfig = Field(default_factory=PostingConfig)
    engagement: EngagementConfig = Field(default_factory=EngagementConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    browser: BrowserConfig = Field(default_factory=BrowserConfig)

    # Populated after load; excluded from any dump of the config.
    secrets: Secrets = Field(default_factory=Secrets, exclude=True, repr=False)
    source_path: Path | None = Field(default=None, exclude=True, repr=False)

    @field_validator("timezone")
    @classmethod
    def _check_tz(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone {value!r}") from exc
        return value

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def db_path(self) -> Path:
        return self.state_dir / "autopilot.sqlite3"

    @property
    def token_path(self) -> Path:
        return self.state_dir / "tokens.json"

    @property
    def log_dir(self) -> Path:
        return self.state_dir / "logs"

    def runtime_problems(self) -> list[str]:
        """Cross-field requirements that only matter once the tool actually runs.

        Kept out of the field validators so a default Config stays constructible:
        a partial config in a test or a REPL should not have to name topics it
        will never use.
        """
        problems: list[str] = []
        if self.posting.enabled and not self.posting.topics:
            problems.append("posting.enabled is true but posting.topics is empty")
        if (
            self.engagement.enabled
            and self.engagement.mode != "off"
            and not self.engagement.interests
        ):
            problems.append("engagement is enabled but engagement.interests is empty")
        return problems

    def ensure_dirs(self) -> None:
        for path in (self.state_dir, self.log_dir):
            path.mkdir(parents=True, exist_ok=True)


DEFAULT_CONFIG_PATHS = (Path("config.yaml"), Path("config.yml"))


def find_config_path(explicit: Path | str | None = None) -> Path:
    """Resolve which config file to read, in precedence order."""
    if explicit is not None:
        return Path(explicit)
    env_path = os.getenv("AUTOPILOT_CONFIG")
    if env_path:
        return Path(env_path)
    for candidate in DEFAULT_CONFIG_PATHS:
        if candidate.exists():
            return candidate
    return DEFAULT_CONFIG_PATHS[0]


def load_config(path: Path | str | None = None) -> Config:
    """Load, validate, and return the configuration.

    Raises ConfigError with a readable message rather than a pydantic traceback,
    because this is usually the first thing a new user gets wrong.
    """
    config_path = find_config_path(path)
    if not config_path.exists():
        raise ConfigError(
            f"No config file at {config_path}. Copy config.example.yaml to config.yaml, "
            "or run: autopilot init"
        )

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"{config_path} is not valid YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"{config_path} must contain a YAML mapping at the top level")

    raw.pop("secrets", None)  # Defence in depth: secrets never come from the file.

    try:
        config = Config.model_validate(raw)
    except ValidationError as exc:
        details = "\n".join(
            f"  - {'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()
        )
        raise ConfigError(f"{config_path} has invalid settings:\n{details}") from exc

    config.secrets = Secrets.from_env()
    config.source_path = config_path

    state_override = os.getenv("AUTOPILOT_STATE_DIR")
    if state_override:
        config.state_dir = Path(state_override)

    problems = config.runtime_problems()
    if problems:
        details = "\n".join(f"  - {problem}" for problem in problems)
        raise ConfigError(f"{config_path} has invalid settings:\n{details}")

    config.ensure_dirs()
    return config
