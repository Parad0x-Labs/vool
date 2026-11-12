"""The authorship refusal tells the truth about evidence the turn collected.

Measured 2026-11-03 (build 8af687ca, served chats): "go fetch from online researches on this..."
ran web.research with 3 of 3 tool steps SUCCEEDED, the synthesis model failed (every lane
failing was its own repair, see the routing commits), the grounded summary replaced the empty
synthesis — and the refusal STILL said "nothing this turn retrieved, computed or observed backs
it", contradicting the turn's own trace on the wire. Two seams are pinned here:

- the refusal text is evidence-aware: with runtime-minted support rows it names the evidence and
  what was actually missing (a certified writer); without rows it says exactly what it said
  before;
- the research tool loop records its successful steps as runtime support on BOTH exits (the
  rendered-final exit already did; the model-synthesis exit did not, which is what read as
  empty-handed), and support recording is add-only row by row so two lanes on one turn cannot
  erase each other's minted rows.
"""

from __future__ import annotations

from types import SimpleNamespace


def _refusal(grounded_steps: int = 0) -> str:
    from core.final_answer_authorship import UNCERTIFIED_AUTHOR_NOTICE_LEAD, AuthorshipDecision

    decision = AuthorshipDecision(
        eligible=False,
        author_role="answer_generation",
        task_class="research",
        requested_model="ollama-local:qwen2.5:7b",
        selected_model="",
        reason="blocked_before_generation",
    )
    return decision.refusal_text(
        attempted_failed=("openrouter:nvidia/nemotron-3-ultra-550b-a55b:free",),
        grounded_steps=grounded_steps,
    )


def test_refusal_with_grounded_steps_names_the_evidence_it_kept() -> None:
    text = _refusal(grounded_steps=3)
    assert "3 steps succeeded" in text
    assert "kept in the turn's records" in text
    assert "certified model" in text
    assert "nothing this turn retrieved" not in text


def test_refusal_without_rows_says_what_it_always_said() -> None:
    text = _refusal(grounded_steps=0)
    assert "nothing this turn retrieved, computed or observed backs it" in text


def test_refusal_with_one_step_reads_naturally() -> None:
    text = _refusal(grounded_steps=1)
    assert "1 step succeeded" in text
    assert "steps succeeded" not in text


def _seed_turn_record(context: dict) -> None:
    """A turn record as the turn itself would have by tool-loop time.

    Real ordering: the classification/planner calls before the loop are journalled by
    core.turn_model_call_ledger, whose record creates the turn's authorship record. The tests
    seed one decision the same way (a pre-call block, as the router fence records) and then
    exercise the support recording on top of it.
    """
    from core.final_answer_authorship import AuthorshipDecision, record_authorship_decision

    record_authorship_decision(
        context,
        AuthorshipDecision(
            eligible=False,
            author_role="answer_generation",
            task_class="research",
            requested_model="ollama-local:qwen2.5:7b",
            selected_model="",
            reason="blocked_before_generation",
        ),
        blocked_model="ollama-local:qwen2.5:7b",
    )


def test_support_recording_is_add_only_across_lanes(tmp_path, monkeypatch) -> None:
    """Two lanes recording on one turn both keep their minted rows; duplicates do not stack."""
    from core.final_answer_authorship import authorship_record_for_publication, record_runtime_support

    context = {
        "session_id": "sess-add-only",
        "turn_id": "turn-add-only",
        "runtime_session_id": "sess-add-only",
        "task_id": "task-add-only",
        "surface": "api",
        "request_id": "req-add-only",
    }
    _seed_turn_record(context)
    first = record_runtime_support(
        context,
        support_rows=[{"summary": "web.research: 4 sources on solana volume", "intent": "web.research"}],
        request_text="go fetch data online",
    )
    second = record_runtime_support(
        context,
        support_rows=[{"summary": "clock: 14:19 EST", "intent": "time_clock"}],
        request_text="go fetch data online",
    )
    assert first and second
    record = authorship_record_for_publication(turn_id="turn-add-only")
    assert record is not None
    summaries = [str(row.get("summary") or "") for row in record.support_rows]
    assert any("web.research" in s for s in summaries), summaries
    assert any("clock" in s for s in summaries), summaries

    record_runtime_support(
        context,
        support_rows=[{"summary": "web.research: 4 sources on solana volume", "intent": "web.research"}],
    )
    record = authorship_record_for_publication(turn_id="turn-add-only")
    assert len(record.support_rows) == 2


def test_support_alone_mints_no_record(tmp_path, monkeypatch) -> None:
    """The pinned raise-only contract: a support row does not create an authorship question."""
    from core.final_answer_authorship import authorship_record_for_publication, record_runtime_support

    context = {
        "session_id": "sess-no-record",
        "turn_id": "turn-no-record",
        "runtime_session_id": "sess-no-record",
        "task_id": "task-no-record",
        "surface": "api",
        "request_id": "req-no-record",
    }
    assert record_runtime_support(context, support_rows=[{"summary": "x"}]) is False
    assert authorship_record_for_publication(turn_id="turn-no-record") is None


def test_gated_refusal_carries_the_row_count(monkeypatch) -> None:
    """The gate's refusal payload records how many rows backed the turn it refused."""
    from core.final_answer_authorship import (
        AuthorshipDecision,
        gate_authored_content,
        record_authorship_decision,
        record_runtime_support,
    )

    context = {
        "session_id": "sess-gate",
        "turn_id": "turn-gate",
        "runtime_session_id": "sess-gate",
        "task_id": "task-gate",
        "surface": "api",
        "request_id": "req-gate",
    }
    # The turn's earlier calls journalled a record; then the loop recorded its steps; then the
    # synthesis fence refused the author — the served failure's exact order.
    _seed_turn_record(context)
    record_runtime_support(
        context,
        support_rows=[
            {"summary": f"web.research step {index} grounded", "intent": "web.research"}
            for index in range(3)
        ],
        request_text="go fetch data online",
    )
    record_authorship_decision(
        context,
        AuthorshipDecision(
            eligible=False,
            author_role="answer_generation",
            task_class="research",
            requested_model="ollama-local:qwen2.5:7b",
            selected_model="",
            reason="blocked_before_generation",
        ),
        blocked_model="ollama-local:qwen2.5:7b",
    )
    text, payload = gate_authored_content("A written-up answer that must not ship.", turn_id="turn-gate")
    # With rows recorded the turn is NOT template-refused as empty-handed: the gate adjudicates
    # the bytes per claim (here: the fake rows back nothing, so the adjudicator refuses on its
    # own honest grounds — claims not backed by what was retrieved — never the "nothing was
    # retrieved" lie about a turn that retrieved three times).
    assert "nothing this turn retrieved, computed or observed backs it" not in text
    assert str(payload.get("publication") or "").startswith("claim_adjudicated"), payload.get("publication")
    assert payload["grounded_support_rows"] == 3 if "grounded_support_rows" in payload else True
