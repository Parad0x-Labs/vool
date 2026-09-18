"""Served-turn proofs for the X editorial studio — REAL /api/chat, provider-bound.

Every test here drives a REAL daemon (apps/vool_api_server.py subprocess, isolated
VOOL_HOME) through HTTP /v1/chat/completions, with a scripted loopback provider that
records every request body the runtime binds for the wire. The invariants under proof:

- direct X-writing requests select the native x-editorial-studio skill; bare X mentions do not;
- all four output modes (post / Premium long post / thread / X Article) validate and render;
- raw notes become a strong Article; an Article converts to a thread and to a single post;
- the user's requested format wins over the provider's;
- grounded claims and links survive; a hostile provider's fabricated numbers, quotations and
  URLs are CAUGHT and the copy-ready rendering is withheld with typed findings;
- operator-approved voice evidence reaches the wire, survives a daemon restart, and never
  leaks across project scopes;
- the exact copy/paste text is served with no commentary around it;
- no files, no publishing calls, no permission grants and no external effects occur.

Assertions pin INVARIANTS (typed findings, exact copy fidelity, ledger records), never
exact generated sentences.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests._served_skill_rig import (
    ProviderState,
    ServedDaemon,
    make_provider_server,
)

SKILL_ID = "x-editorial-studio"

NOTES = (
    "Notes for the piece: our CLI shipped crash-safe resume. "
    "Measured on our own benchmark: cold builds dropped from 94s to 12s. "
    "The release page is https://example.com/release-notes. "
    "One engineer said the wait felt longer than the build itself."
)

ARTICLE_DRAFT = {
    "mode": "article",
    "audience": "developers who run long builds daily",
    "purpose": "the reader should feel checkpoint resume is worth trying this week",
    "thesis": "resumable builds turn interrupted builds into free progress",
    "title": "The build you interrupted is not lost",
    "hook": "Every developer knows the feeling: kill a build, lose the work.",
    "sections": [
        "Every developer knows the feeling: kill a build, lose the work. Our CLI now "
        "resumes crash-safe, and cold builds on our benchmark dropped from 94s to 12s.",
        "The release page https://example.com/release-notes names the flag. One engineer "
        "said the wait felt longer than the build itself; that sentence is why this "
        "shipped.",
    ],
    "media_notes": [],
    "links": ["https://example.com/release-notes"],
    "cta": "",
}

POST_DRAFT = {
    "mode": "post",
    "audience": "developers",
    "purpose": "the reader should try checkpoint resume",
    "thesis": "interrupted builds are no longer lost work",
    "title": "",
    "hook": "Our CLI now resumes interrupted builds from the last checkpoint.",
    "posts": [
        "Our CLI now resumes interrupted builds from the last checkpoint. Cold builds "
        "on our benchmark dropped from 94s to 12s. Release page: https://example.com/release-notes"
    ],
    "sections": [],
    "media_notes": [],
    "links": ["https://example.com/release-notes"],
    "cta": "",
}

LONG_POST_DRAFT = {
    "mode": "long_post",
    "audience": "developers",
    "purpose": "the reader should feel the pain and try the flag",
    "thesis": "checkpoint resume turns kills into free progress",
    "title": "",
    "hook": "We used to lose interrupted builds. Now we resume them.",
    "posts": [
        "We used to lose interrupted builds. Now we resume them. "
        + (
            "The change is small: persist the checkpoint, verify it on start, resume. "
            "The discipline is not small: the checkpoint had to be crash-safe, or resume "
            "would corrupt more than it saves. We measured cold builds dropping from 94s "
            "to 12s on our own benchmark before we believed it. "
        )
        * 2,
    ],
    "sections": [],
    "media_notes": [],
    "links": [],
    "cta": "",
}

THREAD_DRAFT = {
    "mode": "thread",
    "audience": "developers",
    "purpose": "the reader should try checkpoint resume",
    "thesis": "interrupted builds are no longer lost work",
    "title": "",
    "hook": "Our CLI now resumes interrupted builds from the last checkpoint.",
    "posts": [
        "Our CLI now resumes interrupted builds from the last checkpoint.",
        "Cold builds on our benchmark dropped from 94s to 12s.",
        "Release page: https://example.com/release-notes",
    ],
    "sections": [],
    "media_notes": [],
    "links": ["https://example.com/release-notes"],
    "cta": "",
}

HOSTILE_DRAFT = {
    "mode": "post",
    "audience": "everyone",
    "purpose": "go viral",
    "thesis": "our tool is a game changer",
    "title": "",
    "hook": "",
    "posts": [
        'In today\'s fast-paced world, our CLI is a revolutionary game changer that will '
        "unlock 10x velocity for 94% of teams. As Satya Nadella said: \"This changes "
        "everything.\" Read more: https://totally-invented.example.com/launch"
    ],
    "sections": [],
    "media_notes": [],
    "links": [],
    "cta": "Follow for more!",
}

MISMATCH_DRAFT = {
    "mode": "post",
    "audience": "developers",
    "purpose": "the reader should try checkpoint resume",
    "thesis": "interrupted builds are no longer lost work",
    "title": "",
    "hook": "",
    "posts": ["Our CLI now resumes interrupted builds. 94s to 12s on our benchmark."],
    "sections": [],
    "media_notes": [],
    "links": [],
    "cta": "",
}


def _envelope(draft: dict) -> str:
    return "===XDRAFT===\n" + json.dumps(draft) + "\n===XDRAFT-END==="


def _expected_copy_ready(draft: dict) -> str:
    """The copy-ready text the engine must serve for a clean draft — computed by the SAME
    deterministic engine the daemon runs, in this test process."""
    from core.x_editorial import copy_ready_text

    xdraft = compose_for_test(draft)
    return copy_ready_text(xdraft)


def compose_for_test(draft: dict):
    from core.x_editorial import compose_xdraft

    return compose_xdraft(draft, user_text="", source_context={})


def _skill_body() -> str:
    path = Path(__file__).resolve().parents[1] / "skills" / SKILL_ID / "SKILL.md"
    return path.read_text(encoding="utf-8").split("---", 2)[2].strip()


def _served_text(reply: dict) -> str:
    return str(((reply.get("choices") or [{}])[0].get("message") or {}).get("content") or "")


def _skill_events(rig, session_id: str) -> list[dict]:
    return [
        e
        for e in rig.daemon.session_events(session_id)
        if e.get("event_type") == "tool_offer_skills"
    ]


def _selected_x(rig, session_id: str) -> dict | None:
    """The served selection evidence for a PLAIN-TEXT drafting turn: the response edge's
    validation receipt names the skill, and the skill's guidance header rode the bound
    prompt. (The tool_offer_skills event exists only on the tool_intent lane.)"""
    for event in _x_validation_events(rig, session_id):
        if str(event.get("skill")) == SKILL_ID:
            return event  # the events endpoint flattens the record onto the event
    return None


def _skill_header_in_prompt(rig) -> bool:
    return "x-editorial-studio (skill v" in "\n".join(rig.state.system_prompts())


def _x_validation_events(rig, session_id: str) -> list[dict]:
    return [
        e
        for e in rig.daemon.session_events(session_id)
        if e.get("event_type") == "x_editorial_validation"
    ]


@pytest.fixture(scope="module")
def x_rig(tmp_path_factory):
    """One provider + one certified daemon shared by the fast proofs."""
    tmp = tmp_path_factory.mktemp("x-editorial-served")
    state = ProviderState()
    server, port = make_provider_server(state)
    home = tmp / "home"
    home.mkdir(parents=True)
    daemon = ServedDaemon(home, port).start()
    daemon.pin_provider_model()
    cert = daemon.certify_provider_model()
    result = cert.get("result") or cert
    assert result.get("state") == "verified", f"rig provider must certify: {json.dumps(result)[:400]}"
    # Warmup turn: the daemon lazily materializes its own bootstrap artifacts (caches,
    # routing stores, memory DB) on the first chat. Let them land here so per-test
    # file snapshots measure only what a DRAFTING turn does.
    state.script = [{"final": "Ready."}]
    daemon.chat("Say ready.")
    state.requests.clear()
    yield type("Rig", (), {"state": state, "daemon": daemon, "home": home, "tmp": tmp})
    daemon.stop()
    server.shutdown()


def _snapshot_files(home: Path) -> set[str]:
    return {
        str(p.relative_to(home))
        for p in home.rglob("*")
        if p.is_file() and "daemon.log" not in p.name
    }


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def test_explicit_x_article_request_selects_and_serves_exact_copy(x_rig) -> None:
    """A direct 'write an X Article' request selects the skill and the served answer IS the
    exact copy-ready text — no commentary around it."""
    x_rig.state.requests.clear()
    x_rig.state.script = [{"final": _envelope(ARTICLE_DRAFT)}]
    before = _snapshot_files(x_rig.home)
    reply = x_rig.daemon.chat(f"Write an X Article from these notes: {NOTES}")
    after = _snapshot_files(x_rig.home)

    selected = _selected_x(x_rig, str(reply.get("vool_session_id") or ""))
    assert selected, "an explicit X Article request must select the x-editorial skill"
    assert selected.get("policy_version"), selected
    assert _skill_header_in_prompt(x_rig), (
        "the selected skill's guidance header must ride the provider-bound prompt"
    )

    served = _served_text(reply)
    expected = _expected_copy_ready(ARTICLE_DRAFT)
    assert served == expected, (
        "the served answer must be the exact copy-ready text\n"
        f"--- served ---\n{served[:600]}\n--- expected ---\n{expected[:600]}"
    )
    assert "===XDRAFT===" not in served, "the envelope must never leak into the served answer"
    assert "94s to 12s" in served and "https://example.com/release-notes" in served, (
        "grounded numbers and links must survive into the copy"
    )

    events = _x_validation_events(x_rig, str(reply.get("vool_session_id") or ""))
    assert events and events[-1].get("clean") is True, events[-1:]

    # Drafting creates no new files: anything new is the runtime's own per-turn receipts
    # (honesty receipts, routing capsules, prompt-debug) that EVERY served turn writes.
    # data/cas_chunks/ is the content-addressed store a served turn writes when it
    # admits turn context, and the *-wal/*-shm sidecars are SQLite's own. Neither is
    # a DRAFT -- the claim under test is that drafting produces no document, not that
    # a served turn touches no storage. They were missing from this list, so the test
    # was red for a reason unrelated to editorial drafting.
    per_turn_prefixes = (
        "data/honesty_receipts/",
        "data/model_handoff_capsules/",
        "data/cas_chunks/",
        "logs/",
    )
    stray = [
        f
        for f in (after - before)
        if not f.startswith(per_turn_prefixes) and not f.endswith(("-wal", "-shm"))
    ]
    assert not stray, f"drafting must not create files: {stray}"
    assert x_rig.daemon.executed_tools(str(reply.get("vool_session_id") or "")) == []


def test_automatic_x_formatting_request_selects(x_rig) -> None:
    """A natural 'format this for X' request selects the skill without naming a format."""
    x_rig.state.requests.clear()
    x_rig.state.script = [{"final": _envelope(POST_DRAFT)}]
    reply = x_rig.daemon.chat(f"Format these notes for X please: {NOTES}")
    assert _selected_x(x_rig, str(reply.get("vool_session_id") or "")), (
        "a direct 'format this for X' request must select the skill"
    )
    assert _served_text(reply) == _expected_copy_ready(POST_DRAFT)


def test_bare_x_mention_does_not_select(x_rig) -> None:
    """Merely mentioning X/Twitter must not select the editorial skill."""
    x_rig.state.requests.clear()
    x_rig.state.script = [{"final": "They shipped a solid release; the timing looks good."}]
    reply = x_rig.daemon.chat("I saw a post on X about their launch. What do you think of it?")
    session_id = str(reply.get("vool_session_id") or "")
    assert _selected_x(x_rig, session_id) is None, (
        "a bare X mention must not select the editorial skill"
    )
    assert _x_validation_events(x_rig, session_id) == []
    assert not _skill_header_in_prompt(x_rig), (
        "a bare X mention must not put the editorial skill in the prompt"
    )
    assert x_rig.daemon.executed_tools(session_id) == []


# ---------------------------------------------------------------------------
# The four output modes
# ---------------------------------------------------------------------------


def test_standard_post_mode_served_exact(x_rig) -> None:
    x_rig.state.requests.clear()
    x_rig.state.script = [{"final": _envelope(POST_DRAFT)}]
    reply = x_rig.daemon.chat(f"Write a tweet from these notes: {NOTES}")
    assert _served_text(reply) == _expected_copy_ready(POST_DRAFT)
    events = _x_validation_events(x_rig, str(reply.get("vool_session_id") or ""))
    assert events and events[-1].get("mode") == "post"


def test_premium_long_post_mode_served_with_eligibility_truth(x_rig) -> None:
    x_rig.state.requests.clear()
    x_rig.state.script = [{"final": _envelope(LONG_POST_DRAFT)}]
    reply = x_rig.daemon.chat(
        f"Turn these notes into a Premium long post: {NOTES}"
    )
    served = _served_text(reply)
    assert served == _expected_copy_ready(LONG_POST_DRAFT)
    events = _x_validation_events(x_rig, str(reply.get("vool_session_id") or ""))
    assert events and events[-1].get("mode") == "long_post"
    record = events[-1]
    codes = {f.get("code") for f in record.get("findings", [])}
    assert "premium_eligibility_unverified" in codes, record


def test_thread_mode_numbers_items_and_validates_each(x_rig) -> None:
    x_rig.state.requests.clear()
    x_rig.state.script = [{"final": _envelope(THREAD_DRAFT)}]
    reply = x_rig.daemon.chat(f"Turn this into a thread: {NOTES}")
    served = _served_text(reply)
    assert served == _expected_copy_ready(THREAD_DRAFT)
    assert served.startswith("1/ "), served[:80]
    assert "2/ " in served and "3/ " in served
    events = _x_validation_events(x_rig, str(reply.get("vool_session_id") or ""))
    assert events and events[-1].get("mode") == "thread"


def test_article_mode_reports_validation_unknown_for_length(x_rig) -> None:
    """Official X documentation states no Article character limit; the engine must return a
    typed validation_unknown finding instead of inventing one."""
    x_rig.state.requests.clear()
    x_rig.state.script = [{"final": _envelope(ARTICLE_DRAFT)}]
    reply = x_rig.daemon.chat(f"Write an X Article from these notes: {NOTES}")
    events = _x_validation_events(x_rig, str(reply.get("vool_session_id") or ""))
    assert events, "the article turn must record a validation receipt"
    codes = {f.get("code") for f in events[-1].get("findings") or []}
    assert "validation_unknown" in codes, events[-1]


# ---------------------------------------------------------------------------
# Conversion and format authority
# ---------------------------------------------------------------------------


def test_article_converts_to_thread(x_rig) -> None:
    x_rig.state.requests.clear()
    x_rig.state.script = [{"final": _envelope(THREAD_DRAFT)}]
    article_text = (
        "Every developer knows the feeling: kill a build, lose the work. Our CLI now "
        "resumes crash-safe, and cold builds on our benchmark "
        "dropped from 94s to 12s. Release page: https://example.com/release-notes"
    )
    reply = x_rig.daemon.chat(f"Turn this article into a thread: {article_text}")
    served = _served_text(reply)
    assert served == _expected_copy_ready(THREAD_DRAFT)
    events = _x_validation_events(x_rig, str(reply.get("vool_session_id") or ""))
    assert events and events[-1].get("mode") == "thread"


def test_article_converts_to_single_post(x_rig) -> None:
    x_rig.state.requests.clear()
    x_rig.state.script = [{"final": _envelope(POST_DRAFT)}]
    article_text = (
        "Every developer knows the feeling: kill a build, lose the work. Our CLI now "
        "resumes crash-safe, and cold builds on our benchmark "
        "dropped from 94s to 12s. Release page: https://example.com/release-notes"
    )
    reply = x_rig.daemon.chat(f"Compress this into a single X post: {article_text}")
    assert _served_text(reply) == _expected_copy_ready(POST_DRAFT)
    events = _x_validation_events(x_rig, str(reply.get("vool_session_id") or ""))
    assert events and events[-1].get("mode") == "post"


def test_user_requested_format_wins_over_provider(x_rig) -> None:
    """The user asked for a thread; a provider that returns a single post is caught and the
    copy-ready rendering is withheld with a typed finding."""
    x_rig.state.requests.clear()
    x_rig.state.script = [{"final": _envelope(MISMATCH_DRAFT)}]
    reply = x_rig.daemon.chat(
        "Turn this into a thread: our CLI now resumes interrupted builds. 94s to 12s on our benchmark."
    )
    served = _served_text(reply)
    assert "copy-ready withheld" in served, served
    assert "requested_mode_mismatch" in served, served
    events = _x_validation_events(x_rig, str(reply.get("vool_session_id") or ""))
    assert events and events[-1].get("clean") is False


# ---------------------------------------------------------------------------
# Hostile provider / grounding
# ---------------------------------------------------------------------------


def test_hostile_provider_fabrications_are_caught(x_rig) -> None:
    """A provider that invents numbers, a quotation, a URL and sludge cannot slip them in
    unnoticed: each is named by a typed finding and the clean copy is withheld."""
    x_rig.state.requests.clear()
    x_rig.state.script = [{"final": _envelope(HOSTILE_DRAFT)}]
    reply = x_rig.daemon.chat("Write a tweet announcing our CLI release.")
    served = _served_text(reply)
    for code in ("ungrounded_number", "ungrounded_quote", "ungrounded_url", "ai_sludge"):
        assert code in served, f"{code} must be named in the served answer\n{served}"
    assert "copy-ready withheld" in served
    # The fabricated URL must never be served as clean copy: the withheld marker is present
    # and the exact-copy form is not.
    assert served.strip() != _expected_copy_ready(HOSTILE_DRAFT)
    events = _x_validation_events(x_rig, str(reply.get("vool_session_id") or ""))
    assert events and events[-1].get("clean") is False
    codes = {f.get("code") for f in events[-1].get("findings") or []}
    assert {"ungrounded_number", "ungrounded_quote", "ungrounded_url", "ai_sludge"} <= codes


def test_unsupported_publish_request_returns_typed_guidance(x_rig) -> None:
    """Publishing is not a capability of this lane. The request must produce honest typed
    guidance — never a tool, never an external effect."""
    x_rig.state.requests.clear()
    refusal = "I can draft the thread, but I cannot publish or schedule posts. " \
              "Here is the copy-ready text instead."
    x_rig.state.script = [{"final": refusal}]
    reply = x_rig.daemon.chat("Can you publish this thread for me on X?")
    session_id = str(reply.get("vool_session_id") or "")
    served = _served_text(reply)
    assert "cannot publish" in served, served
    assert x_rig.daemon.executed_tools(session_id) == [], "no tool may execute for a publish ask"
    # The publish door must not exist at all.
    import urllib.error

    with pytest.raises(urllib.error.HTTPError):
        x_rig.daemon.post("/api/x/publish", {"text": "hello"})
    # And no prompt carried any publish-shaped tool intent.
    for body in x_rig.state.turn_requests:
        for tool in body.get("tools") or []:
            assert "publish" not in str(tool.get("function", {}).get("name") or ""), tool


# ---------------------------------------------------------------------------
# Voice
# ---------------------------------------------------------------------------


def _seed_voice(home: Path) -> None:
    """Seed the operator-approved voice store the way the operator's own tools would."""
    from core.x_voice_profile import add_samples

    add_samples(
        "global",
        ["Shipped it. Small diff, big difference. Numbers over adjectives."],
        source="operator",
        home=home,
    )
    add_samples(
        "project:proj-b",
        ["Deep dive: here is how the scheduler actually picks its next victim, with numbers."],
        source="operator",
        home=home,
    )


def test_voice_reaches_the_wire_and_survives_restart(tmp_path_factory) -> None:
    """Operator-approved voice evidence enters the served prompt, survives a daemon restart,
    and never leaks across project scopes."""
    tmp = tmp_path_factory.mktemp("x-voice-served")
    state = ProviderState()
    server, port = make_provider_server(state)
    home = tmp / "home"
    home.mkdir(parents=True)
    _seed_voice(home)
    daemon = ServedDaemon(home, port).start()
    try:
        daemon.pin_provider_model()
        cert = daemon.certify_provider_model()
        assert (cert.get("result") or cert).get("state") == "verified"

        state.requests.clear()
        state.script = [{"final": _envelope(POST_DRAFT)}]
        daemon.chat("In my voice, please: our CLI now resumes interrupted builds.")
        prompts = state.system_prompts()
        assert prompts, "no provider-bound prompt captured"
        bound = "\n".join(prompts)
        assert "voice profile v" in bound, "the voice block never reached the wire"
        assert "Numbers over adjectives" in bound, "the approved global sample never reached the wire"
        assert "scheduler actually picks" not in bound, "another scope's sample leaked in"

        # A project-scoped turn sees its own scope, not another project's.
        state.requests.clear()
        state.script = [{"final": _envelope(POST_DRAFT)}]
        daemon.chat(
            "In my voice, please: our CLI now resumes interrupted builds.",
            workspace="proj-b",
        )
        bound_b = "\n".join(state.system_prompts())
        assert "scheduler actually picks" in bound_b
        assert "Numbers over adjectives" not in bound_b, "global leaked into an owned project scope"
    finally:
        daemon.stop()

    # Restart: the approved voice survives because the disk is the store.
    daemon2 = ServedDaemon(home, port).start()
    try:
        daemon2.pin_provider_model()
        state.requests.clear()
        state.script = [{"final": _envelope(POST_DRAFT)}]
        daemon2.chat("In my voice, please: our CLI now resumes interrupted builds.")
        bound_after = "\n".join(state.system_prompts())
        assert "Numbers over adjectives" in bound_after, "voice did not survive the restart"
    finally:
        daemon2.stop()
    server.shutdown()


def test_voice_is_absent_when_not_requested_or_absent(x_rig) -> None:
    """Without the voice request, no voice evidence enters the prompt (bounded cost, no leak)."""
    state = x_rig.state
    state.requests.clear()
    state.script = [{"final": _envelope(POST_DRAFT)}]
    x_rig.daemon.chat(f"Write a tweet from these notes: {NOTES}")
    bound = "\n".join(state.system_prompts())
    # The shared rig's home has no voice store at all.
    assert not (x_rig.home / "config" / "x_voice_profiles.json").exists(), (
        "the shared rig home must have no voice store"
    )
    assert "voice profile v" not in bound


# ---------------------------------------------------------------------------
# No-effects law
# ---------------------------------------------------------------------------


def test_no_permissions_or_external_effects_anywhere(x_rig) -> None:
    """Across every drafting turn in this pack: no executed tools, and the skill inventory
    carries no tool grants for the editorial skill."""
    inventory = x_rig.daemon.get("/api/skills")
    entries = inventory.get("skills") if isinstance(inventory.get("skills"), list) else None
    rows = entries or []
    mine = [
        row
        for row in rows
        if str(row.get("id") or row.get("name") or "") == SKILL_ID
    ]
    assert mine, f"the skill must appear in the inventory: {json.dumps(rows)[:400]}"
    row = mine[0]
    assert not row.get("permitted_tools"), row
    assert not row.get("tool_intents"), row
    assert row.get("risk_class") == "read_only", row
