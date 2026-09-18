"""pa_beta_gate — typed latest-wins active-mission slots (Codex Phase 2 F2/F3).

Proves the typed-slot layer that persists mission constraints (spend cap, active
.null domain, wallet prefix, deadline, target OS, allowed network, repo) with exact
values, latest-wins supersession, and survival across long distraction sessions —
the state the free-text dialogue goal loses.
"""
from __future__ import annotations

import uuid

import pytest

from core.active_mission import (
    capture_active_mission_slots,
    current_active_mission_slots,
    extract_active_mission_slots,
    render_active_slots,
    upsert_active_mission_slots,
)

pytestmark = [pytest.mark.pa_beta]


def _sid(label):
    return f"openclaw:{label}:{uuid.uuid4().hex}"


def _slot(slots, name):
    return next((s["value"] for s in slots if s["slot_name"] == name), None)


# ── extraction (exact values from Codex's mission corpus) ────────────────────

MISSION_EXTRACTION = [
    ("Active mission: keep launch cap exactly 0.037 SOL, domain alice.null, wallet prefix F6Fr2, Windows only.",
     {"spend_cap": "0.037 SOL", "active_domain": "alice.null", "wallet_prefix": "F6Fr2", "target_os": "Windows"}),
    ("Current mission: max 1.2345 USDC, deadline 2026-07-04 at 14:30, repo vool-local.",
     {"spend_cap": "1.2345 USDC", "deadline": "2026-07-04 at 14:30", "active_repo": "vool-local"}),
    ("Mission update: active .null name is dev.team.null, active wallet prefix is 9xQeW, cap is max 0.05 SOL, target OS is Windows 11.",
     {"active_domain": "dev.team.null", "wallet_prefix": "9xQeW", "spend_cap": "0.05 SOL", "target_os": "Windows 11"}),
]


@pytest.mark.parametrize("text,expected", MISSION_EXTRACTION)
def test_mission_slots_extracted_with_exact_values(text, expected):
    got = {s.slot_name: s.value_raw for s in extract_active_mission_slots(text)}
    for name, value in expected.items():
        assert got.get(name) == value, f"{name}: got {got.get(name)!r}, expected {value!r}"


# ── latest-wins supersession ─────────────────────────────────────────────────

LATEST_WINS = [
    ("Spend cap is 0.037 SOL.", "Update spend cap to 0.05 SOL.", "spend_cap", "0.05 SOL", "0.037 SOL"),
    ("Register alice.null for the launch.", "Change active launch domain to parad0x.null.", "active_domain", "parad0x.null", "alice.null"),
    ("Deadline 2026-07-09 at 14:30.", "Deadline moved to 2026-07-15 at 09:00.", "deadline", "2026-07-15 at 09:00", "14:30"),
]


@pytest.mark.parametrize("old,new,slot,current,stale", LATEST_WINS)
def test_latest_instruction_supersedes_the_old_slot(old, new, slot, current, stale):
    sid = _sid("latest")
    capture_active_mission_slots(sid, old)
    capture_active_mission_slots(sid, new)

    active = current_active_mission_slots(sid)
    value = _slot(active, slot)
    assert value == current, f"{slot}: current is {value!r}, expected {current!r}"
    assert stale not in (value or ""), f"stale value {stale!r} still present in {value!r}"
    # exactly one active row per slot
    assert sum(1 for s in active if s["slot_name"] == slot) == 1


def test_unchanged_value_does_not_churn():
    sid = _sid("nochurn")
    capture_active_mission_slots(sid, "Spend cap is 0.037 SOL.")
    capture_active_mission_slots(sid, "Reminder: spend cap is 0.037 SOL.")
    active = [s for s in current_active_mission_slots(sid) if s["slot_name"] == "spend_cap"]
    assert len(active) == 1 and active[0]["value"] == "0.037 SOL"


# ── long-session survival after distraction (F3) ─────────────────────────────

@pytest.mark.parametrize("n_distractions", [30, 60, 100])
def test_mission_survives_distraction_turns(n_distractions):
    sid = _sid("survive")
    capture_active_mission_slots(
        sid, "Active mission: cap 0.05 SOL, domain parad0x.null, wallet prefix F6Fr2, Windows only, deadline 2026-07-09.")
    for i in range(n_distractions):
        capture_active_mission_slots(sid, f"By the way, unrelated chatter number {i} about lunch and the weather.")

    active = current_active_mission_slots(sid)
    assert _slot(active, "spend_cap") == "0.05 SOL"
    assert _slot(active, "active_domain") == "parad0x.null"
    assert _slot(active, "wallet_prefix") == "F6Fr2"
    assert _slot(active, "target_os") == "Windows"
    assert _slot(active, "deadline") == "2026-07-09"


def test_render_active_slots_shows_current_exact_values():
    sid = _sid("render")
    capture_active_mission_slots(sid, "cap 0.037 SOL, domain alice.null")
    capture_active_mission_slots(sid, "Update spend cap to 0.088 SOL.")
    rendered = render_active_slots(current_active_mission_slots(sid))
    assert "0.088 SOL" in rendered
    assert "0.037" not in rendered           # stale value not rendered
    assert "alice.null" in rendered


def test_no_slots_for_plain_chat():
    assert extract_active_mission_slots("how are you today?") == []
    assert upsert_active_mission_slots(_sid("empty"), []) == 0


# ── end-to-end through the real chat path (F3, not a direct-slot shortcut) ────

def _bootstrap_items(session_id, query):
    from core.bootstrap_context import build_bootstrap_context
    from core.human_input_adapter import adapt_user_input
    from core.identity_manager import load_active_persona
    from core.task_router import classify, create_task_record

    interp = adapt_user_input(query, session_id=session_id)
    task = create_task_record(query)
    return build_bootstrap_context(
        persona=load_active_persona("default"), task=task, classification=classify(task.task_summary),
        interpretation=interp, session_id=session_id,
    )


def test_real_chat_path_captures_and_injects_active_mission():
    sid = _sid("e2e")
    # a real turn sets the mission, through adapt_user_input (normalizer + capture)
    from core.human_input_adapter import adapt_user_input
    adapt_user_input(
        "Active mission: cap 0.05 SOL, domain parad0x.null, wallet prefix F6Fr2, Windows only.", session_id=sid)
    # 40 distraction turns that carry no mission slots
    for i in range(40):
        adapt_user_input(f"random aside {i} about lunch and the weather today", session_id=sid)

    # the typed slots survive the distraction
    active = current_active_mission_slots(sid)
    assert _slot(active, "spend_cap") == "0.05 SOL"
    assert _slot(active, "active_domain") == "parad0x.null"

    # and are injected into the next turn's context with exact values
    items = _bootstrap_items(sid, "summarize the current mission")
    mission_items = [i for i in items if i.source_type == "active_mission"]
    assert mission_items, "active mission not injected into bootstrap context"
    blob = mission_items[0].content
    assert "0.05 SOL" in blob and "parad0x.null" in blob and "F6Fr2" in blob


def test_real_chat_path_latest_wins_updates_injected_mission():
    sid = _sid("e2e-latest")
    from core.human_input_adapter import adapt_user_input
    adapt_user_input("Set spend cap to 0.037 SOL.", session_id=sid)
    adapt_user_input("Actually, update the spend cap to 0.088 SOL.", session_id=sid)

    items = _bootstrap_items(sid, "what is the cap")
    blob = " ".join(i.content for i in items if i.source_type == "active_mission")
    assert "0.088 SOL" in blob      # current value injected
    assert "0.037" not in blob       # stale value not injected


# ── Phase 3 long-session constraint types (free-text rules, paths, copy terms) ──


def test_action_directives_extracted_and_accumulate():
    got = {s.value_raw for s in extract_active_mission_slots(
        "Active mission: cap 0.05 SOL, never auto-spend, ask before publishing.")}
    assert "never auto-spend" in got
    assert "ask before publishing" in got


def test_term_avoidance_is_captured_typed_but_never_rendered():
    # "do not mention Web3" is captured as a TYPED forbidden_term slot (so the renderer can scrub it)
    # but must NEVER appear in the rendered context block, and carries no directive slot.
    slots = extract_active_mission_slots("Rules: do not mention Web3, never say Web3 hosting or ordinary DNS.")
    forbidden = [s for s in slots if s.slot_name.startswith("forbidden_term:")]
    assert forbidden, "forbidden term should be captured as a typed slot"
    assert not [s for s in slots if s.slot_name.startswith("directive:")]
    # The safety guarantee: the forbidden term never leaks into the rendered context block.
    rendered = render_active_slots([{"slot_name": s.slot_name, "value": s.value_raw} for s in slots])
    assert "Web3" not in rendered
    assert "ordinary DNS" not in rendered


def test_active_path_slot_latest_wins():
    sid = _sid("path")
    capture_active_mission_slots(sid, r"path C:\Users\test\Documents\vool, ask before publishing")
    capture_active_mission_slots(sid, "Update path to /mnt/data/project/file.txt.")
    path = _slot(current_active_mission_slots(sid), "active_path")
    assert path == "/mnt/data/project/file.txt"


def test_required_terms_captures_copy_list_but_not_ordinary_use():
    got = {s.slot_name: s.value_raw for s in extract_active_mission_slots(
        "Active copy rule: use Web0, .null, VOOL Local, DNA x402; never say Web3 hosting.")}
    terms = got.get("required_terms", "")
    for term in ("Web0", ".null", "VOOL Local", "DNA x402"):
        assert term in terms
    # an ordinary "use X, Y" without a .null term does not become a copy rule
    assert "required_terms" not in {s.slot_name for s in extract_active_mission_slots("Please use vim, not emacs.")}


def test_maturity_label_prefers_affirmed_over_negated():
    got = {s.slot_name: s.value_raw for s in extract_active_mission_slots(
        "Keep the terminology rule and add maturity label alpha only, not beta.")}
    assert got.get("maturity_label", "").lower() == "alpha"


# ── the three Codex Phase 3 long-session scenarios, through the real input path ──

LONG_SESSION_CASES = [
    (
        30,
        "Active mission: cap 0.037 SOL, domain alice.null, wallet prefix F6Fr2, Windows only, never auto-spend, do not mention Web3.",
        "Update active cap to 0.05 SOL and active domain to parad0x.null. Keep wallet prefix F6Fr2, Windows only, never auto-spend, do not mention Web3.",
        "Summarize the current mission in short Telegram style.",
        ["0.05 SOL", "parad0x.null", "F6Fr2", "Windows", "never auto-spend"],
        ["0.037 SOL", "alice.null", "Web3"],
    ),
    (
        60,
        r"Active release task: repo vool-local, deadline July 9, path C:\Users\test\Documents\vool, ask before publishing.",
        "Update deadline to 2026-07-15 at 09:00 and path to /mnt/data/project/file.txt. Ask before publishing remains active.",
        "What is the current release task?",
        ["vool-local", "2026-07-15", "09:00", "/mnt/data/project/file.txt", "ask before publishing"],
        ["July 9", r"C:\Users\test\Documents\vool"],
    ),
    (
        100,
        "Active copy rule: use Web0, .null, VOOL Local, DNA x402; never say Web3 hosting or ordinary DNS; output short Telegram dev style.",
        "Keep the same terminology rule and add maturity label alpha only, not beta.",
        "Write the current copy rule.",
        ["Web0", ".null", "VOOL Local", "DNA x402", "alpha", "Telegram"],
        ["Web3 hosting", "ordinary DNS", "beta"],
    ),
]


@pytest.mark.parametrize("turns,plant,update,final_query,must_include,must_exclude", LONG_SESSION_CASES)
def test_long_session_constraints_survive_through_real_path(turns, plant, update, final_query, must_include, must_exclude):
    from core.human_input_adapter import adapt_user_input

    sid = _sid(f"long-{turns}")
    adapt_user_input(plant, session_id=sid)
    midpoint = max(1, turns // 2)
    for i in range(midpoint):
        adapt_user_input(f"Unrelated turn {i}: scratch note {i}.", session_id=sid)
    adapt_user_input(update, session_id=sid)
    for i in range(midpoint, turns):
        adapt_user_input(f"Unrelated turn {i}: scratch note {i}.", session_id=sid)

    # the injected active-mission context (the real path) carries the current exact constraints
    items = _bootstrap_items(sid, final_query)
    blob = " ".join(i.content for i in items if i.source_type == "active_mission")
    for term in must_include:
        assert term in blob, f"missing {term!r} after {turns} turns"
    for term in must_exclude:
        assert term not in blob, f"forbidden {term!r} present after {turns} turns"


# Codex Phase 3: a mission naming a .null domain must round-trip its exact fields (the .null
# responder must not hijack the mission-recall intent).

def test_active_mission_with_null_domain_injects_exact_fields():
    from core.human_input_adapter import adapt_user_input
    from core.web0_project_grounding import web0_null_project_response

    sid = _sid("null-mission")
    prompt = (
        "Active mission: cap 0.05 SOL, domain alice.null, wallet prefix F6Fr2, Windows only. "
        "What is the current mission?"
    )
    # the .null responder defers on this mission-recall query (mission recall wins)
    assert web0_null_project_response(prompt) is None
    adapt_user_input(prompt, session_id=sid)
    items = _bootstrap_items(sid, "what is the current mission?")
    blob = " ".join(i.content for i in items if i.source_type == "active_mission")
    for field in ("0.05 SOL", "alice.null", "F6Fr2", "Windows"):
        assert field in blob, f"missing {field!r} in injected mission context"
