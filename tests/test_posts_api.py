from __future__ import annotations

from linkedin_autopilot.api.posts import build_post_payload, escape_commentary


def test_reserved_characters_are_escaped() -> None:
    escaped = escape_commentary("Costs (roughly) 50% #less @scale")
    assert r"\(" in escaped
    assert r"\)" in escaped
    assert r"\#" in escaped
    assert r"\@" in escaped


def test_plain_text_is_untouched() -> None:
    assert escape_commentary("A plain sentence.") == "A plain sentence."


def test_payload_shape() -> None:
    payload = build_post_payload("urn:li:person:abc", "Hello", visibility="CONNECTIONS")
    assert payload["author"] == "urn:li:person:abc"
    assert payload["lifecycleState"] == "PUBLISHED"
    assert payload["visibility"] == "CONNECTIONS"
    assert payload["distribution"]["feedDistribution"] == "MAIN_FEED"
