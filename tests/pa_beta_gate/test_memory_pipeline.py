"""pa_beta_gate — memory capture/store/retrieve pipeline (deterministic).

Covers the preference + heuristic memory pipeline that makes VOOL feel like it
remembers *you*: a free-form answer-style command is captured by the typed
Operator Profile authority, explicit preferences round-trip, and the style
parser never swallows a real question or task.

MIGRATED 2026-09-12: free-form style commands moved off
``user_preferences.style_notes`` into the ONE typed authority
(``core.operator_profile``, reached from the turn front door via
``core.operator_profile_turn``): explicit forms persist and report, stated
forms become a Save/Edit candidate, weak forms stay chat-local. These tests
assert the SAME user-visible contract through the current authority. The old
``maybe_handle_preference_command -> style_notes`` seam deliberately declines
style commands now, so the turn reaches the profile lane.
"""
from __future__ import annotations

import pytest

from core.user_preferences import load_preferences, maybe_handle_preference_command

pytestmark = [pytest.mark.pa_beta]

_PRINCIPAL = "pa-style-test"


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    from core import runtime_paths
    monkeypatch.setattr(runtime_paths, "_VOOL_HOME_OVERRIDE", None, raising=False)
    from core.user_preferences import default_preferences, save_preferences
    save_preferences(default_preferences())
    return tmp_path


def _apply_style_proposal(command: str, *, principal: str, session_id: str = "pa-style-session"):
    """Apply one style command the way the turn front door applies it: interpret, then let
    the authority decide (explicit -> remember, strong -> candidate, weak -> chat-local)."""
    from core import operator_profile as profile
    from core.operator_profile_interpretation import interpret_profile_turn

    proposals = [p for p in interpret_profile_turn(command) if p.category == "response_style"]
    assert proposals, f"style command produced no profile proposal: {command!r}"
    proposal = proposals[0]
    if proposal.strength == "explicit":
        change = profile.remember(
            principal, proposal.category, proposal.value, scope=proposal.scope,
            session_id=session_id, origin="explicit", actor="chat",
            reason=f"explicit: {proposal.clause[:120]}",
        )
    elif proposal.strength == "strong":
        change = profile.propose_candidate(
            principal, proposal.category, proposal.value,
            session_id=session_id, confidence=0.85, reason=f"stated: {proposal.clause[:120]}",
        )
    else:
        change = profile.note_chat_local(
            principal, proposal.category, proposal.value,
            session_id=session_id, confidence=0.5,
        )
    return proposal, change


# ---------------------------------------------------------------------------
# Free-form style/tone preference is captured by the typed profile authority
# ---------------------------------------------------------------------------

STYLE_COMMANDS = [
    "answer in short telegram style",
    "be concise",
    "keep it brief",
    "always be blunt with me",
    "answer me in short telegram dev style with no fluff",
    "respond in a formal tone",
    "keep your answers technical and to the point",
    "from now on be direct",
]


@pytest.mark.parametrize("command", STYLE_COMMANDS)
def test_free_form_style_command_is_captured_and_persisted(isolated_home, command):
    import uuid

    principal = f"pa-style-{uuid.uuid4().hex[:10]}"
    # The preference-command surface no longer claims style commands: it declines so the
    # turn reaches the profile lane instead of silently saving to a legacy field.
    handled, _ = maybe_handle_preference_command(command)
    assert handled is False, f"style command must defer to the profile lane: {command!r}"

    proposal, change = _apply_style_proposal(command, principal=principal)
    # "unchanged" is the authority's idempotent re-save of the same value, not a failure.
    assert change.kind in {"saved", "updated", "unchanged", "candidate"}, change.report

    from core import operator_profile as profile

    if proposal.explicit:
        # A persistence-marked command is durable truth, readable after the fact.
        saved = [i for i in profile.list_items(principal) if i.category == "response_style"]
        assert any(proposal.value == i.value_text for i in saved), (
            f"explicit style not persisted for {command!r}: {[i.value_text for i in saved]}"
        )
    else:
        # A stated command becomes a confirmation candidate, never silent durable truth.
        assert change.kind == "candidate", change.report


def test_style_command_sets_a_matching_tone_hint():
    import uuid

    principal = f"pa-style-{uuid.uuid4().hex[:10]}"
    proposal, change = _apply_style_proposal("always be blunt with me", principal=principal)
    assert proposal.explicit and proposal.value == "direct"  # blunt normalizes to direct
    assert change.kind in {"saved", "updated", "unchanged"}


# ---------------------------------------------------------------------------
# The parser never swallows a real question, task, or persona command
# ---------------------------------------------------------------------------

NOT_STYLE = [
    "write a concise summary of the notes",   # a task with a style adjective
    "Write a Python function add_tax(price, rate) returning price plus percentage tax. Keep it short.",
    "what is the capital of france?",         # a question
    "give me a brief overview of the repo",   # a task
    "register alice.null for me",             # unrelated action
    "explain how oauth works",                # a question
]


@pytest.mark.parametrize("text", NOT_STYLE)
def test_question_or_task_is_not_swallowed_as_style(isolated_home, text):
    import uuid

    from core import operator_profile as profile
    from core.operator_profile_interpretation import interpret_profile_turn

    persisting = [
        p for p in interpret_profile_turn(text)
        if p.category == "response_style" and (p.explicit or p.strength == "strong")
    ]
    assert not persisting, f"{text!r} was wrongly captured as a durable style preference"
    # and nothing lands in the store even if some weak chat-local note was taken
    principal = f"pa-style-{uuid.uuid4().hex[:10]}"
    assert not [i for i in profile.list_items(principal) if i.category == "response_style"]


def test_persona_command_still_routes_to_character_not_style(isolated_home):
    handled, response = maybe_handle_preference_command("be a pirate")
    assert handled is True
    assert "character mode" in response.lower()  # persona, not style
    assert not load_preferences().style_notes


# ---------------------------------------------------------------------------
# Explicit preference commands still round-trip (no regression)
# ---------------------------------------------------------------------------

def test_humor_preference_round_trips(isolated_home):
    handled, _ = maybe_handle_preference_command("set humor 70%")
    assert handled is True
    assert load_preferences().humor_percent == 70


def test_show_preferences_reports_state(isolated_home):
    handled, response = maybe_handle_preference_command("/prefs")
    assert handled is True
    assert "humor=" in response and "autonomy=" in response


# ---------------------------------------------------------------------------
# Heuristic capture: append_conversation_event learns how the user likes answers
# ---------------------------------------------------------------------------

_DIRECT = {
    "surface": "desktop",
    "platform": "local",
    "_owner_local": True,
}


def _sid(label):
    import uuid
    return f"openclaw:{label}:{uuid.uuid4().hex}"


def _access_policy(session_id):
    from core.context_scope import ContextAccessPolicy

    return ContextAccessPolicy.for_request(
        session_id=session_id,
        source_context=_DIRECT,
    )


CAPTURE_CASES = [
    ("Keep the answers concise and brutally honest with me.", "response_style"),
    ("Please stay concise, direct, and clear in every reply.", "response_style"),
    ("Use official docs and GitHub repos as sources first.", "source_preference"),
    ("I am building Telegram bots in Python for this project.", "preferred_stack"),
]


@pytest.mark.parametrize("user_input,category", CAPTURE_CASES)
def test_style_and_pref_heuristics_are_captured(isolated_home, user_input, category):
    from core.memory.entries import search_user_heuristics
    from core.persistent_memory import append_conversation_event

    sid = _sid("cap")
    append_conversation_event(session_id=sid, user_input=user_input, assistant_output="Understood.",
                              source_context=_DIRECT)
    rows = search_user_heuristics(
        "help me plan the work",
        access_policy=_access_policy(sid),
        topic_hints=[],
        limit=8,
    )
    assert any(r["category"] == category for r in rows), f"{category} not captured from {user_input!r}"


def test_always_include_heuristic_surfaces_without_token_overlap(isolated_home):
    from core.memory.entries import search_user_heuristics
    from core.persistent_memory import append_conversation_event

    sid = _sid("always")
    append_conversation_event(session_id=sid, user_input="Keep the answers concise and brutally honest.",
                              assistant_output="ok", source_context=_DIRECT)
    rows = search_user_heuristics(
        "what is the weather like on mars",
        access_policy=_access_policy(sid),
        topic_hints=[],
        limit=8,
    )
    assert any(r["category"] == "response_style" for r in rows)


# ---------------------------------------------------------------------------
# Injection: the learned style reaches the prompt via the tiered loader
# ---------------------------------------------------------------------------

def _load_context(session_id, query, *, source_context):
    from core.human_input_adapter import HumanInputInterpretation
    from core.identity_manager import load_active_persona
    from core.task_router import classify, create_task_record
    from core.tiered_context_loader import TieredContextLoader

    interp = HumanInputInterpretation(
        raw_text=query, normalized_text=query, reconstructed_text=query, intent_mode="request",
        topic_hints=[], reference_targets=[], understanding_confidence=0.72, quality_flags=[],
        needs_clarification=False, turn_id=None,
    )
    task = create_task_record(query)
    return TieredContextLoader().load(
        task=task, classification=classify(task.task_summary), interpretation=interp,
        persona=load_active_persona("default"), session_id=session_id, source_context=source_context,
    )


def test_learned_style_is_injected_into_context_on_a_private_surface(isolated_home):
    from core.persistent_memory import append_conversation_event

    sid = _sid("inject")
    append_conversation_event(session_id=sid, user_input="Keep the answers concise and brutally honest.",
                              assistant_output="ok", source_context=_DIRECT)
    result = _load_context(sid, "help me sketch a plan", source_context=_DIRECT)
    items = [i for i in result.relevant_items if i.source_type == "user_heuristic"]
    assert items, "learned style heuristic was not injected into context"


@pytest.mark.parametrize("platform", ["discord", "telegram", "slack"])
def test_learned_style_is_not_injected_on_a_group_surface(isolated_home, platform):
    from core.persistent_memory import append_conversation_event

    sid = _sid("grp")
    group_ctx = {"surface": "channel", "platform": platform, "is_group": True}
    append_conversation_event(session_id=sid, user_input="Keep the answers concise and brutally honest.",
                              assistant_output="ok", source_context=_DIRECT)
    result = _load_context(sid, "help me sketch a plan", source_context=group_ctx)
    items = [i for i in result.relevant_items if i.source_type == "user_heuristic"]
    assert not items, f"private heuristic leaked into a {platform} group surface"


def test_explicit_style_preference_is_injected_via_bootstrap(isolated_home):
    from core.bootstrap_context import _conversation_preference_text
    from core.user_preferences import UserPreferences, save_preferences

    save_preferences(UserPreferences(style_notes="short telegram style, no fluff"))
    assert "short telegram style" in _conversation_preference_text()


# ---------------------------------------------------------------------------
# Dense profile durability
# ---------------------------------------------------------------------------

def test_dense_profile_reflects_learned_response_style(isolated_home):
    from core.memory.learning import load_operator_dense_profile, refresh_operator_dense_profile
    from core.persistent_memory import append_conversation_event

    sid = _sid("dense")
    append_conversation_event(
        session_id=sid,
        user_input="Keep the answers concise and brutally honest, and focus on the GOLDEN_LOOP project.",
        assistant_output="ok", source_context=_DIRECT,
    )
    refresh_operator_dense_profile(session_id=sid)
    profile = load_operator_dense_profile()
    styles = " ".join(str(s) for s in (profile.get("response_style") or [])).lower()
    assert "concise" in styles or "direct" in styles or "honest" in styles


# ---------------------------------------------------------------------------
# Private / public memory isolation
# ---------------------------------------------------------------------------

def test_session_scope_defaults_to_local_only(isolated_home):
    from core.memory.policies import session_memory_policy

    policy = session_memory_policy(_sid("scope-default"))
    assert str(policy.get("share_scope") or "local_only") == "local_only"


@pytest.mark.parametrize("scope", ["local_only", "hive_mind", "public_knowledge"])
def test_session_scope_round_trips(isolated_home, scope):
    from core.memory.policies import session_memory_policy, set_session_memory_policy

    sid = _sid("scope")
    set_session_memory_policy(sid, share_scope=scope)
    assert session_memory_policy(sid)["share_scope"] == scope


def test_restricted_terms_are_persisted_per_session(isolated_home):
    from core.memory.policies import session_memory_policy, set_session_memory_policy

    sid = _sid("restricted")
    set_session_memory_policy(sid, share_scope="hive_mind", restricted_terms=["wallet", "seed phrase"])
    assert "wallet" in list(session_memory_policy(sid).get("restricted_terms") or [])


# ---------------------------------------------------------------------------
# L1 / L2 consistency across the same session
# ---------------------------------------------------------------------------

def test_l1_recent_turns_and_l2_summary_reflect_the_session(isolated_home):
    from core.memory.entries import search_session_summaries
    from core.persistent_memory import append_conversation_event, recent_conversation_events

    sid = _sid("l1l2")
    append_conversation_event(
        session_id=sid,
        user_input="Help me build a Telegram bot in Python and remember the launch is on 2026-07-15.",
        assistant_output="I will help with the Telegram bot.", source_context=_DIRECT,
    )
    l1 = recent_conversation_events(sid, limit=4)
    assert l1 and "telegram bot" in str(l1[0].get("user") or "").lower()

    l2 = search_session_summaries(
        "telegram bot python",
        access_policy=_access_policy(sid),
        topic_hints=["telegram"],
        limit=3,
    )
    assert l2 and "telegram bot" in l2[0]["summary"].lower()


# ---------------------------------------------------------------------------
# Correction-authored rows are reachable by a subject-shaped forget (no cached-fact leak)
# ---------------------------------------------------------------------------

def test_forget_by_subject_removes_the_corrected_value_too(isolated_home):
    """remember X -> correct to Y -> 'forget the <subject>' must leave NOTHING retrievable:
    the correction stored 'Current corrected <label>: Y' under the subject's fact_key, and a
    text-only forget matcher used to leave that row alive after the user forgot the subject."""
    import uuid

    from core.memory.entries import search_relevant_memory
    from core.persistent_memory import maybe_handle_memory_command

    sid = _sid("forget-corr")
    policy = _access_policy(sid)
    maybe_handle_memory_command("remember the backup code is XQ-771-NEGRO", session_id=sid, access_policy=policy)
    handled, reply = maybe_handle_memory_command("correction: the backup code is XQ-991-VERDE", session_id=sid, access_policy=policy)
    assert handled and "XQ-991-VERDE" in reply

    handled, reply = maybe_handle_memory_command("forget the backup code", session_id=sid, access_policy=policy)
    assert handled and "Removed" in reply

    for query in ("backup code", "XQ-991", "XQ-771"):
        rows = search_relevant_memory(query, access_policy=policy, topic_hints=[], limit=5)
        leaks = [r for r in rows if "XQ-991" in str(r.get("text") or "") or "XQ-771" in str(r.get("text") or "")]
        assert not leaks, f"stale/corrected value still retrievable via {query!r}: {[r.get('text') for r in leaks]}"


def test_forget_by_value_still_works_and_does_not_over_erase(isolated_home):
    import uuid

    from core.memory.entries import search_relevant_memory
    from core.persistent_memory import maybe_handle_memory_command

    sid = _sid("forget-val")
    policy = _access_policy(sid)
    maybe_handle_memory_command("remember the night key is ZULU-9", session_id=sid, access_policy=policy)
    maybe_handle_memory_command("remember the day key is ALFA-2", session_id=sid, access_policy=policy)
    handled, reply = maybe_handle_memory_command("forget ZULU-9", session_id=sid, access_policy=policy)
    assert handled and "Removed 1" in reply
    rows = search_relevant_memory("day key", access_policy=policy, topic_hints=[], limit=5)
    assert any("ALFA-2" in str(r.get("text") or "") for r in rows), "value-scoped forget over-erased a sibling fact"
    rows2 = search_relevant_memory("night key", access_policy=policy, topic_hints=[], limit=5)
    assert not any("ZULU-9" in str(r.get("text") or "") for r in rows2)
