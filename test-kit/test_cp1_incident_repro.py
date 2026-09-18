"""CP1 — the recorded incident, reproduced and attributed at audited HEAD 091ed83b.

Incident (raw evidence: validation-logs/conversation-truth/raw-evidence/
incident_req_67536be2_events_seq25-52.jsonl, request req:http:67536be2ab574acfb57e2de27fcc7a64):
after "what is gold price now?" was answered with a live gold quote, the operator typed the VW
comparison text. The backend received it intact (task_received seq 29), the model call failed
(seq 30-31, model_load_gated_low_memory), and the turn was then planned and answered as ONE
subtask market_quote/Gold (live_data_plan_created seq 34, task_completed seq 44), demoted to
PARTIAL_SUCCESS only after shipment (seq 50, "3 of 4 demand obligations unanswered").

HEAD contains the source lane's repair (a4e22311/6f47e5ac/7865c723). These tests (1) verify the
exact recorded sequence is declined at HEAD and (2) sever the repair's guard
(`_content_the_obligation_does_not_hold`, the repo's own sabotage idiom) to reproduce the
replacement end-to-end in-process and pin the FIRST boundary that diverges: the continuation
decision, whose inherited text then propagates through the frozen requirement into the plan —
the same chain the raw events show in the wild.
"""
from __future__ import annotations

import pytest

import core.live_data_continuation as live_data_continuation
from core.execution_requirements import requirements_for
from core.live_data_continuation import continuation_inherits_live_data
from kit_lib import GOLD_ANSWER, GOLD_QUESTION, VW_QUESTION, context, gold_thread, plan_operations


def test_incident_sequence_at_head_is_declined_everywhere():
    """The exact recorded turns at HEAD: no inheritance, no plan, no LIVE_DATA mode."""
    session_id = gold_thread("head")
    source = context(
        session_id,
        ("user", GOLD_QUESTION), ("assistant", GOLD_ANSWER), ("user", VW_QUESTION),
    )

    inherited = continuation_inherits_live_data(VW_QUESTION, source_context=source)
    assert inherited == "", inherited
    assert plan_operations(VW_QUESTION, source) == []
    assert requirements_for(VW_QUESTION, source_context=source).answer_mode != "LIVE_DATA"


def test_severed_guard_reproduces_the_answer_replacement_and_attributes_the_boundary():
    """Guard severed = the pre-repair logic. The chain, in order:

    1. continuation_inherits_live_data rewrites the car text into the gold request;
    2. requirements_for classifies the turn LIVE_DATA with reason
       live_data_continuation_inherited (derived from the OLD text; M1-frozen afterwards);
    3. build_live_data_plan builds exactly the recorded wrong plan: one market_quote/Gold.

    That is the whole in-the-wild mechanism (raw events seq 29 -> 34 -> 44) with the model
    failure playing no part in the substitution.
    """
    session_id = gold_thread("severed")
    source = context(
        session_id,
        ("user", GOLD_QUESTION), ("assistant", GOLD_ANSWER), ("user", VW_QUESTION),
    )

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            live_data_continuation,
            "_content_the_obligation_does_not_hold",
            lambda text, *, slots: [],
        )
        inherited = continuation_inherits_live_data(VW_QUESTION, source_context=source)
        # The rebound substitutes the recorded slot with its canonical capitalisation — the
        # source lane measured exactly this string for this request. (Incident
        # reproduction only: pins frozen PRE-REPAIR behaviour under sabotage; do not copy
        # exact-string pins for forward-looking law tests.)
        assert inherited == "what is Gold price now?", inherited

    # The classification seam: the CURRENT text classifies non-live, yet the turn is frozen
    # LIVE_DATA because the INHERITED text classifies. requirements_for caches per
    # (text, context); a fresh context keeps this assertion independent of the call above.
    fresh = context(
        session_id,
        ("user", GOLD_QUESTION), ("assistant", GOLD_ANSWER), ("user", VW_QUESTION),
    )
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            live_data_continuation,
            "_content_the_obligation_does_not_hold",
            lambda text, *, slots: [],
        )
        requirement = requirements_for(VW_QUESTION, source_context=fresh)
        assert requirement.answer_mode == "LIVE_DATA"
        assert "live_data_continuation_inherited" in requirement.reason_codes

    # The planning seam: the plan is built from the inherited text, one gold subtask —
    # exactly live_data_plan_created seq 34.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            live_data_continuation,
            "_content_the_obligation_does_not_hold",
            lambda text, *, slots: [],
        )
        assert plan_operations(VW_QUESTION, source) == [("market_quote", "Gold")]


def test_the_incident_is_not_a_browser_history_race():
    """The client transcript and the request both carried the car text (source lane's claim).
    Pin the server-side fact the claim rests on: given the CORRECT history, the substitution
    still happened pre-repair — so the divergence is not upstream of the continuation seam.
    """
    # The history above IS the correct history (gold exchange, then the car text). The
    # severed-guard reproduction already ran on it. What this test adds: a history that does
    # NOT contain the gold exchange at all (only the obligation, as a cross-restart carry)
    # still substitutes pre-repair — the obligation store alone is sufficient.
    session_id = gold_thread("norestart")
    source = context(session_id, ("user", VW_QUESTION))

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            live_data_continuation,
            "_content_the_obligation_does_not_hold",
            lambda text, *, slots: [],
        )
        assert continuation_inherits_live_data(VW_QUESTION, source_context=source) == "what is Gold price now?"

    # ...and at HEAD, the same obligation-only context is declined.
    assert continuation_inherits_live_data(VW_QUESTION, source_context=source) == ""
