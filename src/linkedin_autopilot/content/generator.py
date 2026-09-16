"""Draft generation through the Anthropic API.

One class wraps every model call the project makes: post drafts, comment
drafts, and relevance scores. Structured output is used throughout so callers
get validated objects rather than prose they have to parse.
"""

from __future__ import annotations

from typing import Any

import anthropic
from pydantic import BaseModel, Field

from ..config import Config
from ..errors import ContentRejected
from ..logging_setup import get_logger
from ..store import Store
from . import prompts
from .topics import choose_topic
from .validators import ValidationResult, validate_comment, validate_post

log = get_logger(__name__)

# Lets the API re-route a declined request instead of failing the run outright.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class PostDraft(BaseModel):
    """A generated post, before validation."""

    text: str = Field(description="The full post body, ready to publish, without hashtags.")
    hashtags: list[str] = Field(
        default_factory=list, description="Hashtags to append, each starting with #."
    )
    rationale: str = Field(description="One sentence on the angle taken, for the reviewer.")

    def rendered(self, *, include_hashtags: bool = True, max_hashtags: int = 3) -> str:
        body = self.text.strip()
        if include_hashtags and self.hashtags:
            tags = [t if t.startswith("#") else f"#{t}" for t in self.hashtags[:max_hashtags]]
            body = f"{body}\n\n{' '.join(tags)}"
        return body


class CommentDraft(BaseModel):
    """A generated comment, or an explicit refusal to comment."""

    should_comment: bool = Field(
        description="False when the post gives nothing specific and true to say."
    )
    text: str = Field(
        default="", description="The comment body. Empty when should_comment is false."
    )
    reason: str = Field(description="Why this reply adds something, or why it was declined.")


class RelevanceScore(BaseModel):
    score: float = Field(ge=0.0, le=1.0, description="0 to 1, per the scoring guide.")
    reason: str = Field(description="One sentence justifying the score.")


class ContentGenerator:
    def __init__(self, config: Config, client: anthropic.Anthropic | None = None) -> None:
        self.config = config
        if client is not None:
            self.client = client
        else:
            api_key = config.secrets.anthropic_api_key
            # A bare constructor also picks up an `ant auth login` profile, so an
            # unset ANTHROPIC_API_KEY is not necessarily an error.
            self.client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()

    # ------------------------------------------------------------------ core

    def _parse(self, *, system: str, user: str, output_model: type[BaseModel]) -> Any:
        """One structured-output call, with refusal handled explicitly."""
        llm = self.config.llm
        kwargs: dict[str, Any] = {
            "model": llm.model,
            "max_tokens": llm.max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "output_format": output_model,
            "output_config": {"effort": llm.effort},
        }

        # The beta and stable endpoints return different response classes, so the
        # name is left untyped and read through getattr below.
        response: Any
        if llm.server_side_fallbacks:
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
            response = self.client.beta.messages.parse(**kwargs)
        else:
            response = self.client.messages.parse(**kwargs)

        if getattr(response, "stop_reason", None) == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None) or "unspecified"
            raise ContentRejected(
                f"The model declined this request (category: {category}). "
                "Adjust the topic or voice and try again."
            )

        parsed = getattr(response, "parsed_output", None)
        if parsed is None:
            raise ContentRejected("The model returned no parsable output.")
        return parsed

    # ------------------------------------------------------------------ posts

    def generate_post(self, store: Store, topic: str | None = None) -> tuple[PostDraft, str]:
        """Return a validated post draft and the topic it was written for.

        One retry on validation failure: the second attempt is told exactly what
        was wrong with the first. Past that, the caller should look at it.
        """
        posting = self.config.posting
        chosen = topic or choose_topic(posting, store)
        recent = sorted(store.recent_topics(posting.topic_cooldown_days))

        if posting.hashtags.enabled and posting.hashtags.pool:
            hashtag_instruction = (
                f"Choose at most {posting.hashtags.max} hashtags from this list only: "
                f"{', '.join(posting.hashtags.pool)}."
            )
        elif posting.hashtags.enabled:
            hashtag_instruction = f"Choose at most {posting.hashtags.max} relevant hashtags."
        else:
            hashtag_instruction = "Do not include any hashtags. Return an empty hashtags list."

        user = prompts.POST_USER_TEMPLATE.format(
            topic=chosen,
            voice=posting.voice.strip() or "Plain, direct, first person.",
            target_chars=min(1200, posting.max_chars),
            max_chars=posting.max_chars,
            hashtag_instruction=hashtag_instruction,
            banned=", ".join(posting.banned_phrases) or "none",
            recent=", ".join(recent) or "none",
        )

        draft: PostDraft = self._parse(
            system=prompts.POST_SYSTEM, user=user, output_model=PostDraft
        )
        rendered = draft.rendered(
            include_hashtags=posting.hashtags.enabled, max_hashtags=posting.hashtags.max
        )
        result = validate_post(rendered, posting)

        if not result:
            log.warning("First draft failed validation (%s); regenerating", result.summary)
            retry_user = (
                f"{user}\n\nYour previous attempt was rejected because it "
                f"{result.summary}. Fix that and write it again."
            )
            draft = self._parse(system=prompts.POST_SYSTEM, user=retry_user, output_model=PostDraft)
            rendered = draft.rendered(
                include_hashtags=posting.hashtags.enabled, max_hashtags=posting.hashtags.max
            )
            result = validate_post(rendered, posting)
            if not result:
                raise ContentRejected(f"Two drafts in a row failed validation: {result.summary}")

        return draft, chosen

    # --------------------------------------------------------------- comments

    def generate_comment(
        self, *, author: str, post_text: str
    ) -> tuple[CommentDraft, ValidationResult]:
        """Draft a comment. The draft may legitimately decline to say anything."""
        engagement = self.config.engagement
        limits = engagement.comments

        user = prompts.COMMENT_USER_TEMPLATE.format(
            author=author or "unknown",
            post_text=post_text.strip()[:4000],
            interests="\n".join(f"- {item}" for item in engagement.interests),
            voice=self.config.posting.voice.strip() or "Plain, direct, first person.",
            min_chars=limits.min_chars,
            max_chars=limits.max_chars,
        )

        draft: CommentDraft = self._parse(
            system=prompts.COMMENT_SYSTEM, user=user, output_model=CommentDraft
        )

        if not draft.should_comment:
            return draft, ValidationResult.failed([f"declined: {draft.reason}"])

        return draft, validate_comment(draft.text, limits)

    # -------------------------------------------------------------- relevance

    def score_relevance(self, *, author: str, post_text: str) -> RelevanceScore:
        user = prompts.RELEVANCE_USER_TEMPLATE.format(
            interests="\n".join(f"- {item}" for item in self.config.engagement.interests),
            author=author or "unknown",
            post_text=post_text.strip()[:4000],
        )
        return self._parse(system=prompts.RELEVANCE_SYSTEM, user=user, output_model=RelevanceScore)
