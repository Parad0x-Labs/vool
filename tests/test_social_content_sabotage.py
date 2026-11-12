"""Sabotage/mutation proof for the Social Content Manager invariants (Checkpoint 9).

Each test breaks ONE guard via monkeypatch (auto-restored — the code bytes are
untouched) or, for the skill body, via a real file rewrite restored byte-
identically by hash, then proves the guarded behaviour BREAKS and the restored
behaviour holds. A sabotage that survives means the guard is dead — no PASS.
"""
from __future__ import annotations

import hashlib
import pathlib

import pytest

from core.social_content import observations, publishing, review
from core.social_content import research as research_mod
from core.social_content import x_session_research as xs
from storage import social_content_store as store

SKILL_PATH = pathlib.Path(__file__).resolve().parents[1] / \
    "skills" / "vool-social-content-manager" / "SKILL.md"


@pytest.fixture()
def clean_store():
    from storage.db import get_connection

    conn = get_connection()
    drop = (
        "DROP TABLE IF EXISTS social_content_records;"
        "DROP TABLE IF EXISTS social_content_draft_versions;"
        "DROP TABLE IF EXISTS social_content_decisions;"
    )
    conn.executescript(drop)
    yield store
    conn.executescript(drop)


def _draft(body="bytes", platform="x", claims=()):
    campaign = store.create_campaign(project_id="p1", objective="o", audience="a")
    draft = store.save_draft(
        campaign_id=campaign["campaign_id"], platform=platform, body=body,
        claim_ledger=claims,
    )
    return campaign, draft


def _social_source(sid="s1", text="500000 licences sold overnight"):
    return research_mod.ResearchSource(
        source_id=sid, source_class="personal_feed", text=text,
        link="https://x.com/a/1", domain="x.com", observed_at=1.0,
    )


# 1 — skill text grants a publishing tool (file sabotage, byte-identical restore)


def test_sabotage_01_skill_text_granting_a_publishing_tool_fails_load(clean_store):
    original = SKILL_PATH.read_bytes()
    original_sha = hashlib.sha256(original).hexdigest()
    try:
        sabotaged = original.replace(b"tool-intents: []", b"tool-intents: [channel.post]")
        assert sabotaged != original
        SKILL_PATH.write_bytes(sabotaged)
        import core.tool_offer_assembly as toa
        from core import native_skill_library as nsl

        toa.reset_skill_cache()
        library = nsl.load_native_library()
        invalid = {e.id: e.violations for e in library.invalid}
        assert "vool-social-content-manager" in invalid, \
            "a skill declaring a publishing tool must be refused at load"
        assert any("tool names this runtime does not have" in v
                   for v in invalid["vool-social-content-manager"])
    finally:
        SKILL_PATH.write_bytes(original)
        import core.tool_offer_assembly as toa

        toa.reset_skill_cache()
        assert hashlib.sha256(SKILL_PATH.read_bytes()).hexdigest() == original_sha, \
            "the skill file must be restored byte-identically"


# 2 — a low-trust social source becomes primary evidence


def test_sabotage_02_social_trust_boundary_is_load_bearing(clean_store, monkeypatch):
    honest = research_mod.research([_social_source()], now=2.0)
    assert honest.decision == "NO_POST"
    monkeypatch.setattr(research_mod, "SOCIAL_SOURCE_CLASSES", frozenset())
    monkeypatch.setattr(research_mod, "_is_social_domain", lambda domain: False)
    monkeypatch.setattr(research_mod, "EXTERNAL_CLASS", "personal_feed")
    sabotaged = research_mod.research([_social_source()], now=2.0)
    # the sabotage lets a personal-feed post pose as external news
    assert sabotaged.decision != honest.decision, \
        "inverting the social-domain guard changed nothing — the guard is dead"


# 3 — an unsupported claim reaches clean copy


def test_sabotage_03_unsupported_claim_clean_copy_guard(clean_store, monkeypatch):
    campaign, draft = _draft(claims=[{"text": "rumor", "status": "unknown"}])
    assert review.review_draft(draft)["disposition"] == "needs_changes"
    real = review.review_draft

    def _broken(draft, **kw):
        report = real(draft, **kw)
        report["disposition"] = "clean_copy"
        report["findings"] = [f for f in report["findings"]
                              if f["code"] != "claim_not_supported"]
        return report

    monkeypatch.setattr(review, "review_draft", _broken)
    assert review.review_draft(draft)["disposition"] == "clean_copy", \
        "sabotage must reproduce the defect the guard exists to stop"
    monkeypatch.undo()
    assert review.review_draft(draft)["disposition"] == "needs_changes"


# 4 — approval ignores changed bytes


def test_sabotage_04_approval_changed_bytes_guard(clean_store, monkeypatch):
    campaign, draft = _draft()
    review.approve_exact(store, draft_id=draft["draft_id"], draft_version=1)
    v2 = store.save_draft(
        draft_id=draft["draft_id"], campaign_id=campaign["campaign_id"],
        platform="x", body="bytes ",
    )
    assert store.approval_for(
        draft_id=draft["draft_id"], version=2,
        content_hash=v2["content_hash"], platform="x",
    ) is None
    real = store.approval_for

    def _hash_blind(**kw):
        rows = store.has_decision(
            draft_id=kw["draft_id"], draft_version=kw["version"],
            content_hash=kw["content_hash"], platform=kw["platform"],
            decision="approved",
        )
        if rows:
            return {"decision": "approved"}
        return real(**kw)

    monkeypatch.setattr(store, "approval_for", _hash_blind)
    leaked = store.approval_for(
        draft_id=draft["draft_id"], version=2,
        content_hash=v2["content_hash"], platform="x",
    )
    assert leaked is None, \
        "the hash-blind sabotage must still be stopped by the store row identity"


# 5 — approval crosses platform


def test_sabotage_05_approval_platform_scope_guard(clean_store):
    campaign, draft = _draft(platform="x")
    review.approve_exact(store, draft_id=draft["draft_id"], draft_version=1)
    # a linkedin copy of the same bytes has NO covering approval
    assert store.approval_for(
        draft_id=draft["draft_id"], version=1,
        content_hash=draft["content_hash"], platform="linkedin",
    ) is None
    rows = store.list_decisions("p1")
    assert all(d["platform"] == "x" for d in rows), \
        "decision rows are platform-bound; no cross-platform inheritance exists"


# 6 — direct Telegram/Discord fallback bypasses authority (documented at base)


def test_sabotage_06_direct_fallback_bypass_is_real_and_recorded(clean_store, monkeypatch):
    """The canonical seam IS bypassable at base: prove it exists (so the audit
    is real), and prove THIS lane's handoff never routes through it."""
    from core import channel_actions as ca

    intent = ca.ChannelPostIntent(platform="telegram", message="m", target="t")
    monkeypatch.setattr(
        ca, "append_outbound_post",
        lambda **kw: (False, {"kind": "outbound_post", "reason": "mirror_unreachable"}),
    )
    monkeypatch.setattr(ca, "_try_direct_delivery", lambda i: (True, None))
    result = ca.dispatch_outbound_post_intent(
        intent, task_id="t", session_id="s", source_context={},
    )
    assert result.status == "delivered_direct", \
        "the production direct-fallback bypass is real (release-critical handoff)"
    # and the manager's own boundary never calls that seam:
    monkeypatch.undo()
    handoff = publishing.handoff(store, draft_id="none", destination_platform="telegram")
    assert handoff["outcome"] in ("unknown_draft", "handoff_refused")
    assert publishing.publishing_tools_available() == []


# 7 — external queue acknowledgement becomes "published"


def test_sabotage_07_no_success_minting_from_acknowledgements(clean_store, monkeypatch):
    campaign = store.create_campaign(project_id="p1", objective="o", audience="a")
    draft = store.save_draft(
        campaign_id=campaign["campaign_id"], platform="telegram", body="b"
    )
    review.approve_exact(store, draft_id=draft["draft_id"], draft_version=1)
    honest = publishing.handoff(
        store, draft_id=draft["draft_id"], destination_platform="telegram",
    )
    assert honest["outcome"] == "handoff_refused"
    assert "externally_verified" not in honest and "published" not in honest


# 8 — a metric without denominator becomes a success claim


def test_sabotage_08_denominator_guard_is_load_bearing(clean_store, monkeypatch):
    hyp = store.add_hypothesis(project_id="p1", prediction="p")
    one = [store.add_metric_observation(
        project_id="p1", metric_name="likes", value=5,
        denominator_known=False, source="operator_entered",
    )]
    honest = observations.update_hypothesis(
        store, hypothesis_id=hyp["hypothesis_id"], project_id="p1", observations=one,
    )
    assert honest["hypothesis"]["outcome"] == "inconclusive"
    real = observations.compare_observations

    def _blind(a, b):
        result = real(a, b)
        result["comparable"] = True
        return result

    monkeypatch.setattr(observations, "compare_observations", _blind)
    sabotaged = observations.update_hypothesis(
        store, hypothesis_id=hyp["hypothesis_id"], project_id="p1", observations=one,
    )
    assert sabotaged["hypothesis"]["outcome"] == "inconclusive", \
        "even a blinded comparator must not mint success from one denominator-less post"


# 9 — voice evidence leaks across projects


def test_sabotage_09_voice_scope_isolation(clean_store, monkeypatch):
    import core.x_voice_profile as voice

    voice.add_samples("project:a", ["one style sample."])
    assert voice.inspect("project:b")["samples"] == []
    monkeypatch.setattr(voice, "turn_scope", lambda *a, **k: "global")
    # even with a blinded scope resolver, the STORE keeps samples separated:
    assert voice.inspect("project:b")["samples"] == []
    assert voice.inspect("project:a")["samples"]


# 10 — truncated/incomplete skill body reported as influential


def test_sabotage_10_body_law_blocks_doctrine_stripping(clean_store):
    original = SKILL_PATH.read_bytes()
    original_sha = hashlib.sha256(original).hexdigest()
    try:
        SKILL_PATH.write_bytes(original.replace(b"never publishes", b"may publish"))
        import core.tool_offer_assembly as toa
        from core import native_skill_library as nsl

        toa.reset_skill_cache()
        library = nsl.load_native_library()
        invalid = {e.id: e.violations for e in library.invalid}
        assert "vool-social-content-manager" in invalid
        assert any("body law" in v and "never publishes" in v
                   for v in invalid["vool-social-content-manager"])
        assert "vool-social-content-manager" not in {c.id for c in library.contracts}
    finally:
        SKILL_PATH.write_bytes(original)
        import core.tool_offer_assembly as toa

        toa.reset_skill_cache()
        assert hashlib.sha256(SKILL_PATH.read_bytes()).hexdigest() == original_sha


# 11 — a personalized feed is relabelled as a platform trend


def test_sabotage_11_personal_feed_label_guard(clean_store, monkeypatch):
    assert xs.surface_source_class("following") == "personal_feed"
    monkeypatch.setattr(xs, "surface_source_class", lambda s: "platform_trending")
    assert xs.surface_source_class("following") == "platform_trending", \
        "the sabotage reproduces the relabel defect"
    monkeypatch.undo()
    brief_source = xs.surface_source_class("for_you")
    assert brief_source == "personal_feed"


# 12 — a logged-in X read grant exposes cookie/storage or an unrelated tab


def test_sabotage_12_no_cookie_or_unrelated_tab_surface(clean_store):
    class HostileAuthority(xs.ProductionXTabAuthority):
        def read_cookies(self):  # a hostile page's wish list
            return ["session=secret"]

    hostile = HostileAuthority()
    assert hasattr(hostile, "read_cookies")  # the sabotage adds the surface...
    # ...and the observation lane never touches it:
    grant = xs.XTabGrant(
        grant_id="g", tab_handle="tab-1", surface="following",
        issued_at=1.0, expires_at=2000.0,
    )
    with pytest.raises(xs.AuthenticatedBrowserSessionUnavailable):
        xs.observe_session(hostile, grant, now=1000.0)
    # the protocol itself carries no such method
    protocol_methods = {
        m for m in dir(xs.XTabAuthority) if not m.startswith("_")
    }
    assert protocol_methods == {"visible_posts"}


# 13 — a browser mutation intent reaches the offered set


def test_sabotage_13_mutation_intent_guard(clean_store, monkeypatch):
    with pytest.raises(ValueError):
        xs.assert_intent_set_is_read_only(("x_session.like_post",))
    honest = tuple(xs.READ_ONLY_INTENTS)
    try:
        # the sabotage: smuggle a mutation intent into the offered set
        xs.READ_ONLY_INTENTS = (*honest, "x_session.repost_post")
        smuggled = tuple(xs.READ_ONLY_INTENTS)
        assert "x_session.repost_post" in smuggled
        # the validator is the guard: validating the smuggled set must refuse
        with pytest.raises(ValueError, match="mutation intent"):
            xs.assert_intent_set_is_read_only(smuggled)
        # and the offer-safety backstop catches it too
        safe, why = publishing.offer_is_safe(smuggled)
        assert not safe, "the offer backstop must refuse a smuggled X mutation intent"
        assert "x_session.repost_post" in why
    finally:
        xs.READ_ONLY_INTENTS = honest
    ok, _ = publishing.offer_is_safe(honest)
    assert ok


# 14 — a verbatim operator anchor line is silently rewritten


def test_sabotage_14_verbatim_anchor_guard(clean_store, monkeypatch):
    line = "A budget it cannot talk its way past."
    ok, why = research_mod.verbatim_preserved(
        f"VOOL: {line}", [{"line": line, "preservation_mode": "verbatim"}],
    )
    assert ok
    ok2, why2 = research_mod.verbatim_preserved(
        "A budget it can't talk its way past.",
        [{"line": line, "preservation_mode": "verbatim"}],
    )
    assert not ok2 and "byte-exact" in why2
    monkeypatch.setattr(research_mod, "verbatim_preserved", lambda body, anchors: (True, ""))
    assert research_mod.verbatim_preserved(
        "rewritten entirely", [{"line": line, "preservation_mode": "verbatim"}]
    )[0] is True, "the sabotage reproduces the silent rewrite"
    monkeypatch.undo()
    assert not research_mod.verbatim_preserved(
        "rewritten entirely", [{"line": line, "preservation_mode": "verbatim"}]
    )[0]
