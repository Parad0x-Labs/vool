"""Unit, selection and policy proofs for the X editorial studio.

The platform policy is pinned against OFFICIAL X documentation and the official
twitter-text v3 weighted-counting rules (see core/x_platform_policy.py for the exact
sources). The XDraft contract is validated deterministically — these tests pin the
invariants, never exact generated sentences.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture()
def iso_home(tmp_path, monkeypatch):
    """Isolated VOOL_HOME so operator config never touches the real home."""
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.delenv("VOOL_MCP_CONFIG", raising=False)
    from core import native_skill_library

    native_skill_library.reset_contract_caches()
    yield home
    native_skill_library.reset_contract_caches()


# ---------------------------------------------------------------------------
# Platform truth (official sources, pinned in core/x_platform_policy.py)
# ---------------------------------------------------------------------------


def test_policy_pins_official_limits_with_sources() -> None:
    from core import x_platform_policy as pol

    assert pol.PLATFORM_POLICY_VERSION, "the policy must carry a version"
    assert pol.STANDARD_POST_LIMIT == 280
    assert pol.PREMIUM_LONG_POST_LIMIT == 25_000
    assert pol.URL_ACCOUNTED_CHARS == 23
    # Every pinned fact names its official source.
    for key in ("standard_post_limit", "premium_long_post_limit", "url_accounting",
                "long_post_premium_eligibility", "article_limits"):
        assert str(pol.SOURCES.get(key) or "").startswith("http"), key
    # No invented Article limit: officially, none is stated.
    assert pol.length_limit("article") is None
    assert pol.length_limit("post") == 280
    assert pol.length_limit("long_post") == 25_000


def test_weighted_length_matches_twitter_text_rules() -> None:
    from core.x_platform_policy import weighted_length

    assert weighted_length("hello") == 5
    assert weighted_length("a" * 280) == 280
    assert weighted_length("a" * 281) == 281
    # A long URL counts as 23 characters, whatever its true length (t.co accounting).
    long_url = "https://example.com/" + "x" * 120
    assert weighted_length(long_url) == 23
    # CJK code points are double-weighted, as the official config's ranges say.
    assert weighted_length("名前") == 4
    # A single emoji weighs like two characters (emoji ranges in the official config).
    assert weighted_length("👍") == 2
    # Mixed: text plus URL.
    assert weighted_length("look: " + long_url) == 6 + 23


def test_length_limit_unknown_for_articles_is_typed() -> None:
    from core.x_platform_policy import length_limit_with_certainty

    limit, certainty = length_limit_with_certainty("article")
    assert limit is None and certainty == "validation_unknown"
    limit, certainty = length_limit_with_certainty("post")
    assert limit == 280 and certainty == "official"
    limit, certainty = length_limit_with_certainty("long_post")
    assert limit == 25_000 and certainty == "official_premium"


# ---------------------------------------------------------------------------
# XDraft contract
# ---------------------------------------------------------------------------


CLEAN_POST = {
    "mode": "post",
    "audience": "developers",
    "purpose": "the reader should try the flag",
    "thesis": "interrupted builds are no longer lost work",
    "title": "",
    "hook": "",
    "posts": ["Our CLI now resumes builds. 94s to 12s. https://example.com/changelog"],
    "sections": [],
    "media_notes": [],
    "links": [],
    "cta": "",
}

CORPUS = (
    "our CLI now resumes builds from checkpoints. measured: 94s to 12s. "
    "changelog: https://example.com/changelog"
)


def test_compose_fills_engine_owned_fields() -> None:
    from core import x_platform_policy as pol
    from core.x_editorial import compose_xdraft

    xdraft = compose_xdraft(CLEAN_POST, user_text=CORPUS, source_context={})
    assert xdraft.mode == "post"
    assert xdraft.platform_policy_version == pol.PLATFORM_POLICY_VERSION
    assert xdraft.voice_profile_version is None
    assert xdraft.copy_ready_text, "the engine renders copy-ready text"
    kinds = {c.kind for c in xdraft.claim_ledger}
    assert {"number", "url"} <= kinds
    statuses = {c.status for c in xdraft.claim_ledger}
    assert "grounded" in statuses


def test_clean_post_validates_and_renders_exactly() -> None:
    from core.x_editorial import compose_xdraft, copy_ready_text, findings_of

    xdraft = compose_xdraft(CLEAN_POST, user_text=CORPUS, source_context={})
    failures = [f for f in findings_of(xdraft) if f.severity == "failure"]
    assert failures == [], [f.code for f in failures]
    assert copy_ready_text(xdraft) == xdraft.copy_ready_text
    assert xdraft.copy_ready_text == CLEAN_POST["posts"][0]


def test_overlong_post_fails_with_weighted_count() -> None:
    draft = dict(CLEAN_POST)
    draft["posts"] = ["x" * 281]
    from core.x_editorial import compose_xdraft, findings_of

    xdraft = compose_xdraft(draft, user_text="", source_context={})
    codes = {f.code for f in findings_of(xdraft) if f.severity == "failure"}
    assert "over_limit" in codes
    over = next(f for f in findings_of(xdraft) if f.code == "over_limit")
    assert "281" in over.message and "280" in over.message


def test_thread_items_are_validated_individually() -> None:
    draft = {
        "mode": "thread",
        "posts": ["short one", "y" * 300, "also short"],
        "title": "", "hook": "", "sections": [], "media_notes": [], "links": [], "cta": "",
    }
    from core.x_editorial import compose_xdraft, findings_of

    xdraft = compose_xdraft(draft, user_text="", source_context={})
    over = [f for f in findings_of(xdraft) if f.code == "over_limit"]
    assert len(over) == 1 and over[0].where == "post 2"
    rendered = xdraft.copy_ready_text
    assert rendered.startswith("1/ short one")
    assert "2/ " + "y" * 300 in rendered


def test_article_length_is_validation_unknown_never_invented() -> None:
    draft = dict(CLEAN_POST)
    draft["mode"] = "article"
    draft["title"] = "A real title"
    draft["sections"] = ["A section of prose.", "Another section."]
    draft["posts"] = []
    from core.x_editorial import compose_xdraft, findings_of

    xdraft = compose_xdraft(draft, user_text="", source_context={})
    unknown = [f for f in findings_of(xdraft) if f.code == "validation_unknown"]
    assert len(unknown) == 1
    assert unknown[0].severity == "unknown"
    assert xdraft.copy_ready_text.startswith("A real title\n\n")


def test_article_without_title_is_a_failure() -> None:
    draft = dict(CLEAN_POST)
    draft["mode"] = "article"
    draft["title"] = ""
    draft["sections"] = ["Body without a title."]
    draft["posts"] = []
    from core.x_editorial import compose_xdraft, findings_of

    xdraft = compose_xdraft(draft, user_text="", source_context={})
    codes = {f.code for f in findings_of(xdraft) if f.severity == "failure"}
    assert "article_title_missing" in codes


def test_requested_format_wins() -> None:
    from core.x_editorial import compose_xdraft, findings_of, requested_mode

    assert requested_mode("Turn this into a thread: blah") == "thread"
    assert requested_mode("write an X article about cats") == "article"
    assert requested_mode("compress this into a single X post") == "post"
    assert requested_mode("write a Premium long post") == "long_post"
    assert requested_mode("hello world") == ""

    draft = dict(CLEAN_POST)
    xdraft = compose_xdraft(draft, user_text="turn this into a thread", source_context={})
    codes = {f.code for f in findings_of(xdraft) if f.severity == "failure"}
    assert "requested_mode_mismatch" in codes


# ---------------------------------------------------------------------------
# Claim ledger, sludge, privacy
# ---------------------------------------------------------------------------


def test_ungrounded_number_quote_and_url_are_caught() -> None:
    draft = dict(CLEAN_POST)
    draft["posts"] = [
        '94% of teams fail. As someone said: "this changes everything". '
        "See https://invented.example.com/x and 12s to glory."
    ]
    from core.x_editorial import compose_xdraft, findings_of

    xdraft = compose_xdraft(draft, user_text="announcing our release", source_context={})
    codes = {f.code for f in findings_of(xdraft) if f.severity == "failure"}
    assert {"ungrounded_number", "ungrounded_quote", "ungrounded_url"} <= codes
    statuses = {(c.text, c.status) for c in xdraft.claim_ledger}
    assert any(t == "94%" and s == "ungrounded" for t, s in statuses)


def test_supplied_grounds_survive() -> None:
    from core.x_editorial import compose_xdraft, findings_of

    xdraft = compose_xdraft(CLEAN_POST, user_text=CORPUS, source_context={})
    failures = [f for f in findings_of(xdraft) if f.severity == "failure"]
    assert failures == []
    assert "https://example.com/changelog" in xdraft.copy_ready_text
    assert "94s to 12s" in xdraft.copy_ready_text
    grounded = {c.text for c in xdraft.claim_ledger if c.status == "grounded"}
    assert "https://example.com/changelog" in grounded


AI_SLUDGE_SAMPLES = [
    "In today's fast-paced world, developers struggle.",
    "Our tool is a real game changer.",
    "We delve into the details.",
    "Unlock your potential today.",
    "This revolutionary approach wins.",
    "It's not just a tool, it's a companion.",
    "In conclusion, in conclusion, in conclusion is repetitive filler that we cut.",
]


def test_ai_sludge_is_detected_by_name() -> None:
    from core.x_editorial import sludge_hits

    for sample in AI_SLUDGE_SAMPLES:
        hits = sludge_hits(sample)
        assert hits, f"sludge not detected: {sample!r}"


def test_clean_prose_has_no_sludge_hits() -> None:
    from core.x_editorial import sludge_hits

    assert sludge_hits("Our CLI now resumes builds from the last checkpoint.") == []


def test_deliberate_operator_phrasing_is_allowed() -> None:
    """A sludge phrase the OPERATOR supplied verbatim is deliberate, not a finding."""
    from core.x_editorial import compose_xdraft, findings_of

    draft = dict(CLEAN_POST)
    draft["posts"] = ["Our customers call it a game changer. We kept the phrase."]
    xdraft = compose_xdraft(
        draft,
        user_text='Use my line: "our customers call it a game changer"',
        source_context={},
    )
    codes = {f.code for f in findings_of(xdraft)}
    assert "ai_sludge" not in codes


def test_privacy_leaks_are_refused() -> None:
    draft = dict(CLEAN_POST)
    draft["posts"] = [
        "Read my notes at /Users/operator-example/secrets/notes.txt and key AKIAIOSFODNN7EXAMPLE."
    ]
    from core.x_editorial import compose_xdraft, findings_of

    xdraft = compose_xdraft(draft, user_text="read my notes", source_context={})
    codes = {f.code for f in findings_of(xdraft) if f.severity == "failure"}
    assert "privacy_leak" in codes


def test_apply_output_clean_serves_copy_ready_exactly() -> None:
    from core.x_editorial import apply_x_editorial_output, compose_xdraft, copy_ready_text

    provider_text = "===XDRAFT===\n" + json.dumps(CLEAN_POST) + "\n===XDRAFT-END==="
    app = apply_x_editorial_output(provider_text, user_text=CORPUS, state={"scope": "global"})
    assert app.changed is True and app.compliant is True
    xdraft = compose_xdraft(CLEAN_POST, user_text=CORPUS, source_context={})
    assert app.text == copy_ready_text(xdraft)
    assert app.record["clean"] is True
    assert app.record["copy_ready_served"] is True


def test_apply_output_without_envelope_passes_through() -> None:
    from core.x_editorial import apply_x_editorial_output

    app = apply_x_editorial_output(
        "A conversational critique of your draft: tighten the ending.",
        user_text="critique my draft",
        state={"scope": "global"},
    )
    assert app.changed is False and app.compliant is True
    assert app.record.get("envelope_found") is False


def test_apply_output_hostile_withholds_copy_ready() -> None:
    draft = dict(CLEAN_POST)
    draft["posts"] = ["94% of teams agree. https://invented.example.com"]
    provider_text = "===XDRAFT===\n" + json.dumps(draft) + "\n===XDRAFT-END==="
    from core.x_editorial import apply_x_editorial_output

    app = apply_x_editorial_output(provider_text, user_text="announce our release", state={})
    assert app.compliant is False
    assert "copy-ready withheld" in app.text
    assert app.record["copy_ready_served"] is False
    assert "ungrounded_number" in app.text and "ungrounded_url" in app.text


def test_apply_output_malformed_envelope_is_typed() -> None:
    from core.x_editorial import apply_x_editorial_output

    app = apply_x_editorial_output(
        "===XDRAFT===\n{not json}\n===XDRAFT-END===", user_text="hi", state={}
    )
    assert app.compliant is False
    assert "envelope_malformed" in app.text


# ---------------------------------------------------------------------------
# Selection: direct requests select, bare mentions do not
# ---------------------------------------------------------------------------


SELECTS = [
    "Write an X Article from these notes: ...",
    "turn this into a thread",
    "Please format this for X: my notes here",
    "make it sound like me",
    "Write a Premium long post about the launch",
    "write a tweet about cats",
    "edit my tweet draft, it rambles",
    "Turn this article into a thread",
    "compress this into a single X post",
]

DOES_NOT_SELECT = [
    "I saw a post on X about the launch",
    "X is my favorite social platform",
    "What is the character limit on Twitter?",
    "write me a poem about the sea",
    "my blog article needs an editor's eye",
    "the thread on HN was spicy today",
    "",
]


def test_selection_direct_requests_select(iso_home) -> None:
    from core.native_skill_library import load_skill_contracts, select_native_skills

    library = load_skill_contracts()
    ids = {c.id for c in library.contracts}
    assert "x-editorial-studio" in ids, "the shipped skill must load"
    assert library.invalid == (), [e.violations for e in library.invalid]

    for text in SELECTS:
        selection = select_native_skills(task_class="creative_ideation", user_text=text)
        assert "x-editorial-studio" in {c.id for c in selection.selected}, text


def test_selection_bare_mentions_and_other_writing_do_not(iso_home) -> None:
    from core.native_skill_library import load_skill_contracts, select_native_skills

    load_skill_contracts()
    for text in DOES_NOT_SELECT:
        selection = select_native_skills(task_class="creative_ideation", user_text=text)
        assert "x-editorial-studio" not in {c.id for c in selection.selected}, repr(text)


def test_selection_does_not_depend_on_task_class_alone(iso_home) -> None:
    from core.native_skill_library import select_native_skills

    selection = select_native_skills(task_class="creative_ideation", user_text="write me a poem")
    assert "x-editorial-studio" not in {c.id for c in selection.selected}


def test_disabled_skill_does_not_select(iso_home) -> None:
    from core.native_skill_library import select_native_skills, set_skill_enabled

    assert set_skill_enabled("x-editorial-studio", False)["status"] == "ok"
    selection = select_native_skills(
        task_class="creative_ideation", user_text="write an X article"
    )
    assert "x-editorial-studio" not in {c.id for c in selection.selected}
    set_skill_enabled("x-editorial-studio", True)


def test_contract_is_read_only_with_no_tool_grants(iso_home) -> None:
    from core.native_skill_library import inspect_skill

    record = inspect_skill("x-editorial-studio")
    contract = record["contract"]
    assert contract["risk_class"] == "read_only"
    assert contract["permitted_tools"] == []
    assert contract["tool_intents"] == []
    assert contract["expected_outputs"], "typed expected outputs are required"


def test_guidance_is_bounded_and_attributed(iso_home) -> None:
    """Bounded, attributed, and COMPLETE.

    The editorial skill used to be the only row here because it was the only skill
    that could win a slot on this turn. It now shares the turn with the presentation
    doctrine and a lens, which hold their own lane -- so it is asserted by membership
    rather than by being first, and the bound is the per-turn total across both lanes.
    """
    from core.native_skill_library import guidance_for_selection, select_native_skills
    from core.tool_offer_assembly import MAX_TOTAL_SKILL_CHARS

    selection = select_native_skills(
        task_class="creative_ideation", user_text="write an X article"
    )
    text, provenance, permitted = guidance_for_selection(selection.selected)
    assert len(text) <= MAX_TOTAL_SKILL_CHARS
    rows = {row["name"]: row for row in provenance}
    assert "x-editorial-studio" in rows, sorted(rows)
    editorial = rows["x-editorial-studio"]
    assert editorial["origin"] == "native"
    assert editorial["lane"] == "task", editorial
    # Complete or absent -- never a truncated body carrying a full-fidelity claim.
    assert editorial["complete"] is True, editorial
    assert editorial["body_chars"] > 0 and editorial["chars"] >= editorial["body_chars"], editorial
    assert permitted == (), "the editorial skill grants no tools"


def test_model_lane_does_not_change_the_contract(iso_home) -> None:
    """The same turn under different model lanes gets byte-identical guidance and the same
    deterministic validation — the contract is model-independent."""
    from core.native_skill_library import select_native_skills
    from core.x_editorial import compose_xdraft, copy_ready_text

    guidance = []
    for _model in ("custom-byok:probe-model", "ollama-local:qwen2.5:7b", "ollama-local:qwen3:4b"):
        selection = select_native_skills(
            task_class="creative_ideation", user_text="write an X article"
        )
        from core.native_skill_library import guidance_for_selection

        text, provenance, _ = guidance_for_selection(selection.selected)
        guidance.append((text, tuple(sorted(p["name"] for p in provenance))))
    assert len(set(guidance)) == 1, "guidance must be model-invariant"

    drafts = [copy_ready_text(compose_xdraft(CLEAN_POST, user_text=CORPUS, source_context={}))
              for _ in range(3)]
    assert len(set(drafts)) == 1, "validation/rendering must be deterministic"


# ---------------------------------------------------------------------------
# Voice profile: approved evidence, scoped, restart-safe, inspectable
# ---------------------------------------------------------------------------


def test_voice_add_inspect_reset_and_versions(iso_home) -> None:
    from core.x_voice_profile import (
        add_samples,
        inspect,
        remove_sample,
        reset_scope,
        set_paused,
        voice_profile_version,
    )

    assert voice_profile_version("global") is None
    added = add_samples("global", ["Short sentence. Punchy. Numbers over adjectives."],
                        source="operator")
    assert added["status"] == "ok"
    v1 = voice_profile_version("global")
    assert v1 == 1
    record = inspect("global")
    assert len(record["samples"]) == 1
    assert record["version"] == 1

    sample_id = record["samples"][0]["id"]
    assert remove_sample("global", sample_id)["status"] == "ok"
    assert voice_profile_version("global") == 2, "every change is a new version"
    assert inspect("global")["samples"] == []

    add_samples("global", ["Another approved line."], source="operator")
    assert reset_scope("global")["status"] == "ok"
    assert inspect("global")["samples"] == []
    set_paused("global", True)
    assert inspect("global")["paused"] is True


def test_voice_privacy_scan_refuses_secrets(iso_home) -> None:
    from core.x_voice_profile import add_samples, voice_profile_version

    refused = add_samples(
        "global",
        ["my key is AKIAIOSFODNN7EXAMPLE and notes live in /Users/operator-example/secrets"],
        source="operator",
    )
    assert refused["status"] == "refused", refused
    assert refused["refused"], refused
    assert voice_profile_version("global") is None, "a refused sample must not enter the store"


def test_voice_scopes_are_isolated(iso_home) -> None:
    from core.x_voice_profile import add_samples, inspect

    add_samples("global", ["Global operator line."], source="operator")
    add_samples("project:alpha", ["Alpha project line."], source="operator")
    add_samples("project:beta", ["Beta project line."], source="operator")
    assert inspect("global")["samples"][0]["text"] == "Global operator line."
    assert inspect("project:alpha")["samples"][0]["text"] == "Alpha project line."
    assert "Alpha project line." not in [s["text"] for s in inspect("project:beta")["samples"]]


def test_voice_persists_across_fresh_module_state(iso_home) -> None:
    """Restart-preservation by construction: the disk is read on every call, and a fresh
    interpreter sees the same store (no process-local copy to lose)."""
    import subprocess
    import sys

    from core.x_voice_profile import add_samples

    add_samples("global", ["Persistence proof line."], source="operator")
    home = iso_home
    code = (
        "import json,os;"
        f"os.environ['VOOL_HOME']={str(home)!r};"
        "import sys; sys.path.insert(0, %r);"
        "from core.x_voice_profile import voice_profile_version, inspect;"
        "print(json.dumps({'v': voice_profile_version('global'),"
        "'texts': [s['text'] for s in inspect('global')['samples']]}))"
        % str(Path(__file__).resolve().parents[1])
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    payload = json.loads(out.stdout.strip().splitlines()[-1])
    assert payload["v"] == 1
    assert payload["texts"] == ["Persistence proof line."]


def test_voice_segment_only_on_request_and_bounded(iso_home) -> None:
    from core.x_voice_profile import (
        MAX_PROMPT_CHARS,
        add_samples,
        voice_segment_for_turn,
    )

    add_samples("global", ["Short sentence. Punchy."], source="operator")
    # No voice request → no voice evidence enters the prompt.
    assert voice_segment_for_turn("write an X article", source_context={}) == ""
    seg = voice_segment_for_turn("make it sound like me", source_context={})
    assert "voice profile v" in seg
    assert "Short sentence. Punchy." in seg
    assert "never infer biography" in seg
    assert len(seg) <= MAX_PROMPT_CHARS

    # With samples but a paused store → no evidence.
    from core.x_voice_profile import set_paused

    set_paused("global", True)
    assert voice_segment_for_turn("make it sound like me", source_context={}) == ""


def test_voice_segment_excerpts_carry_privacy_scan(iso_home) -> None:
    """A sample that passed the write-time scan is still scanned at render time; and the
    segment never contains machine identity markers."""
    from core.x_voice_profile import add_samples, voice_segment_for_turn

    added = add_samples("global", ["Plain approved sentence with no secrets."],
                        source="operator")
    assert added["status"] == "ok"
    seg = voice_segment_for_turn("make it sound like me", source_context={})
    assert str(iso_home) not in seg, "the segment must never leak local paths"
