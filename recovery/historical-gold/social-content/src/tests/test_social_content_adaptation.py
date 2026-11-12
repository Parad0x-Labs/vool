"""Platform adaptation — no policy fiction (Checkpoint 4)."""
from __future__ import annotations

import pytest

from core.social_content import adaptation, platform_policy


# ---------------------------------------------------------------- policy truth


def test_policy_facts_are_versioned_and_sourced():
    assert platform_policy.PLATFORM_POLICY_VERSION == "social-platform-2026-09"
    for platform, facts in platform_policy.PLATFORM_FACTS.items():
        for f in facts:
            assert f.retrieved and f.source_url
            assert f.status in ("documented", "validation_unknown", "delegated")
            if f.status == "validation_unknown":
                assert f.value is None, "unknown facts must not carry invented values"


def test_unknown_limits_return_typed_unknown():
    for platform, key in (
        ("linkedin", "article_character_limit"),
        ("threads", "link_handling"),
        ("telegram", "core_app_character_limit"),
    ):
        f = platform_policy.fact(platform, key)
        assert f.value is None and f.status == "validation_unknown"
    assert platform_policy.length_limit_with_certainty("mastodon") == (
        None, "validation_unknown"
    )


def test_x_is_delegated_not_duplicated():
    with pytest.raises(KeyError):
        platform_policy.fact("x", "post_character_limit")
    limit, certainty = platform_policy.length_limit_with_certainty("x")
    # comes from the X authority (280 standard post), not from this module
    assert limit == 280 and certainty != "validation_unknown"
    assert "x_editorial" in adaptation.x_delegation_note()


def test_hostile_invented_limit_is_not_encodable():
    """Unknown limits produce validation_unknown findings, never constants."""
    result = adaptation.adapt(platform="linkedin", body="x" * 4000, format="post")
    codes = [f.code for f in result.findings]
    assert "over_limit" in codes  # 3000 documented
    result = adaptation.adapt(platform="mastodon", body="x" * 4000)
    assert any(f.severity == "unknown" for f in result.findings)
    assert result.clean  # unknown is not a failure — it is honesty


# ---------------------------------------------------------------- adaptation


def test_article_to_thread_split():
    body = ("Receipts are hash chained. " * 12).strip()
    result = adaptation.adapt(platform="threads", body=body, format="thread")
    assert result.thread_parts
    for part in result.thread_parts:
        assert len(part) <= 500


def test_linkedin_and_bluesky_adaptation_preserves_links_and_figures():
    source = "We measured 23ms latency. Details: https://example.com/bench"
    linked = adaptation.adapt(
        platform="linkedin", body="Measured 23ms latency. Details: https://example.com/bench",
        source_body=source,
    )
    assert linked.clean
    broken = adaptation.adapt(
        platform="linkedin", body="Measured 23ms latency. Details omitted.",
        source_body=source,
    )
    assert not broken.clean
    assert any(f.code == "missing_links" for f in broken.findings)


def test_unicode_and_url_accounting():
    # X weighted accounting delegates to the X authority: emoji weigh 2 units
    result = adaptation.adapt(platform="x", body="🎧" * 500)
    assert any(f.code == "over_limit" for f in result.findings)
    # Mastodon links count 23 regardless of length
    long_url = "https://example.com/" + "a" * 200
    result = adaptation.adapt(platform="mastodon", body=f"See {long_url}")
    # default limit unknown per-instance -> unknown finding, not failure
    assert any(f.severity == "unknown" for f in result.findings)


def test_line_breaks_and_multiline_survive():
    body = "line one\n\nline two\n- bullet"
    result = adaptation.adapt(platform="linkedin", body=body)
    assert result.body == body  # byte-identical passthrough, hash-bound


def test_format_precedence_thread_over_post():
    body = "Sentence one. " * 60
    post = adaptation.adapt(platform="threads", body=body, format="post")
    thread = adaptation.adapt(platform="threads", body=body, format="thread")
    assert any(f.code == "over_limit" for f in post.findings)
    assert not any(f.code == "over_limit" for f in thread.findings)


def test_repetitive_cross_platform_copy_detected_and_intentional_reuse_allowed():
    body = "The same copy everywhere. Receipts: https://example.com/r"
    flagged = adaptation.adapt(
        platform="bluesky", body=body,
        other_platform_bodies={"linkedin": body},
    )
    assert any(f.code == "repetitive_cross_platform_copy" for f in flagged.findings)
    reused = adaptation.adapt(
        platform="bluesky", body=body,
        other_platform_bodies={"linkedin": body}, intentional_reuse=True,
    )
    assert not any(f.code == "repetitive_cross_platform_copy" for f in reused.findings)


def test_per_platform_hashes_are_independent():
    body = "Shared bytes. https://example.com/x"
    a = adaptation.adapt(platform="linkedin", body=body)
    b = adaptation.adapt(platform="bluesky", body=body)
    assert a.content_hash == b.content_hash  # same bytes, same hash...
    b2 = adaptation.adapt(platform="bluesky", body=body + " ")
    assert b2.content_hash != a.content_hash  # ...and any byte change re-hashes


def test_media_brief_only_from_evidence():
    assert adaptation.media_brief_from_evidence(()) is None
    assert adaptation.media_brief_from_evidence(("no visuals here",)) is None
    brief = adaptation.media_brief_from_evidence(
        ("the screenshot shows the receipt chain verifying",)
    )
    assert brief and brief["basis"].startswith("operator-supplied")
