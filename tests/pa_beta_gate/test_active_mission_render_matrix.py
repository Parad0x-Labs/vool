"""pa_beta_gate — 20x deterministic active-mission render matrix (Codex round 4).

The rendered mission answer AND the injected active-mission context block for the canonical prompt
must ALWAYS carry the exact current constraints and NEVER leak the forbidden term Web3, run-to-run
across distinct sessions. Offline, model-free, deterministic.
"""
from __future__ import annotations

import uuid

import pytest

from core.active_mission import (
    current_active_mission_slots,
    render_mission_answer,
)
from core.human_input_adapter import adapt_user_input

pytestmark = [pytest.mark.pa_beta]

PROMPT = (
    "Active mission: cap 0.037 SOL, domain alice.null, wallet prefix F6Fr2, Windows only, "
    "never auto-spend, do not mention Web3. Summarize the mission without the forbidden term."
)
MUST_INCLUDE = ["0.037 SOL", "alice.null", "F6Fr2", "Windows", "never auto-spend"]
FORBIDDEN = "Web3"


def _bootstrap_active_mission_blob(session_id, query):
    from core.bootstrap_context import build_bootstrap_context
    from core.identity_manager import load_active_persona
    from core.task_router import classify, create_task_record

    interp = adapt_user_input(query, session_id=session_id)
    task = create_task_record(query)
    items = build_bootstrap_context(
        persona=load_active_persona("default"), task=task,
        classification=classify(task.task_summary), interpretation=interp, session_id=session_id,
    )
    return " ".join(i.content for i in items if i.source_type == "active_mission")


@pytest.mark.parametrize("run_index", range(20))
def test_mission_render_always_exact_and_web3_free(run_index):
    sid = f"openclaw:render-matrix:{uuid.uuid4().hex}"
    adapt_user_input(PROMPT, session_id=sid)
    slots = current_active_mission_slots(sid)

    rendered = render_mission_answer(slots)
    low = rendered.lower()
    for token in MUST_INCLUDE:
        assert token.lower() in low, f"run {run_index}: render missing {token!r}: {rendered!r}"
    assert FORBIDDEN.lower() not in low, f"run {run_index}: render leaked {FORBIDDEN}: {rendered!r}"


@pytest.mark.parametrize("run_index", range(20))
def test_injected_active_mission_block_always_exact_and_web3_free(run_index):
    sid = f"openclaw:inject-matrix:{uuid.uuid4().hex}"
    adapt_user_input(PROMPT, session_id=sid)
    blob = _bootstrap_active_mission_blob(sid, "summarize the current mission")
    assert blob, f"run {run_index}: no active_mission context injected"
    for token in MUST_INCLUDE:
        assert token in blob, f"run {run_index}: injected block missing {token!r}: {blob!r}"
    assert FORBIDDEN not in blob, f"run {run_index}: injected block leaked {FORBIDDEN}: {blob!r}"
