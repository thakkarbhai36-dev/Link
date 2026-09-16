"""Publishing to your own feed through the official Posts API.

This is the sanctioned path: the `w_member_social` scope exists precisely so an
application can post on behalf of the member who authorized it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from ..errors import LinkedInAPIError
from ..logging_setup import get_logger
from .client import LinkedInClient

log = get_logger(__name__)

POSTS_PATH = "/rest/posts"

# LinkedIn's "little text" format treats these as markup, so a literal one in
# the body has to be escaped or the post is rejected or silently mangled.
_RESERVED = r"\\|\{|\}|\@|\[|\]|\(|\)|\<|\>|\#|\*|\_|\~"
_RESERVED_RE = re.compile(f"({_RESERVED})")


def escape_commentary(text: str) -> str:
    """Backslash-escape the characters LinkedIn reserves in post commentary."""
    return _RESERVED_RE.sub(r"\\\1", text)


@dataclass(slots=True)
class PostResult:
    urn: str
    url: str

    @classmethod
    def from_urn(cls, urn: str) -> PostResult:
        # Activity URNs map onto a stable public permalink; share URNs do not,
        # so fall back to the feed update URL which works for both.
        return cls(urn=urn, url=f"https://www.linkedin.com/feed/update/{urn}/")


def build_post_payload(
    author_urn: str,
    text: str,
    *,
    visibility: Literal["PUBLIC", "CONNECTIONS"] = "PUBLIC",
) -> dict:
    return {
        "author": author_urn,
        "commentary": escape_commentary(text),
        "visibility": visibility,
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }


def publish_text_post(
    client: LinkedInClient,
    author_urn: str,
    text: str,
    *,
    visibility: Literal["PUBLIC", "CONNECTIONS"] = "PUBLIC",
) -> PostResult:
    """Publish a text post and return its URN and permalink."""
    payload = build_post_payload(author_urn, text, visibility=visibility)
    response = client.post(POSTS_PATH, json=payload)

    # The created URN comes back in a header, not the body.
    urn = response.headers.get("x-restli-id") or response.headers.get("X-RestLi-Id")
    if not urn:
        try:
            urn = response.json().get("id", "")
        except ValueError:
            urn = ""
    if not urn:
        raise LinkedInAPIError(
            response.status_code,
            "post appears to have been created but LinkedIn returned no id; "
            "check your feed before retrying",
            body=response.text[:500],
        )

    log.info("Published post %s", urn)
    return PostResult.from_urn(urn)
