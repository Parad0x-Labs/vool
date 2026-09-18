"""M2 -- retrieved evidence must enter the model call that writes the answer.

The live-search UI proof (a2308a26) shipped this as an OPEN DEFECT with its measured
runtime event order, read back from `/api/runtime/events` AFTER the turn::

    task_classified
    model_lane_selected
    model.call_completed        <- the answer was written HERE
    web_retrieval_started       <- the evidence arrived AFTER it
    web_retrieval_completed
    task_completed

Three real, dated Google News rows were fetched, paid for, and receipted with
`lifecycle=succeeded` behind four invented headlines ("Rust 1.64 Released...", a 2022
version, no date, no URL). The Activity rail truthfully said "Keyless search
(google_news_rss) - 3 sources", and that is exactly what made the fabrication read as
sourced.

This family pins the ORDERING invariant, which is upstream of both root causes that
lane recorded. It does not depend on the requirement classifier being widened (M1's
lane): every request here uses "latest news on X", which
`requirements_for(...).current_information_required` already reads True at a2308a26 --
asserted below so it cannot drift under us.

WHAT IS REAL HERE
-----------------
`execute_grounded_turn` runs for real, and so does `_collect_live_web_notes` -- the
governed collector, with its own policy gates, its own `begin_web_retrieval` /
`finish_web_retrieval` receipts, and its own failure handling. Only the two ENDS are
doubled: the search transport (`_planned_search_query` / `_search_query`, standing in
for the provider's socket) and the provider call (`memory_router.resolve`, standing in
for the model). The receipts these tests read are therefore real receipts written by
the real writer, not fixtures.

The model double emits `model.call_started` / `model.call_completed` because it occupies
the position the real router emits them from; the live daemon drive in
`proofs/grounding-m2-ordering-20260901/` is where that timeline is proven with a real
provider and real events.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest import mock

from apps.vool_agent import ChatTurnResult, ResponseClass
from core.execution_requirements import requirements_for
from core.grounded_synthesis_binding import (
    OUTCOME_BOUND,
    OUTCOME_FAILED,
    OUTCOME_PARTIAL,
    OUTCOME_REFUSED,
    PROVES_PROMPT_ENTRY,
    binding_admitted_for_turn,
    binding_record,
    mint_evidence_set,
    outcome_for_receipt,
    turn_scope,
)
from core.identity_manager import load_active_persona
from core.memory_first_router import ModelExecutionDecision

CURRENT_REQUEST = "what is the latest news on the Rust programming language"
TIMELESS_REQUEST = "explain what a monad is in functional programming"

# A term no model could have produced from its weights: it is minted by this test and
# exists only in the retrieved rows. If it reaches the model call, the evidence reached
# the prompt; if it does not, nothing the retrieval paid for was in scope when the
# answer was written.
SOURCE_SENTINEL = "Qhistad-Kernel-Zynthe-0442"

RETRIEVED_NOTES = [
    {
        "summary": f"Phoronix | 2026-08-31 | Rust Coreutils 0.11 Released, {SOURCE_SENTINEL}",
        "result_title": "Rust Coreutils 0.11 Released",
        "result_url": "https://www.phoronix.com/news/rust-coreutils-0-11",
        "origin_domain": "phoronix.com",
        "search_provider": "google_news_rss",
        "source_type": "web_derived",
    },
    {
        "summary": "InfoWorld | 2026-08-28 | Rust language adds algebraic floating-point methods",
        "result_title": "Rust adds algebraic floating-point methods",
        "result_url": "https://www.infoworld.com/article/rust-algebraic-floats",
        "origin_domain": "infoworld.com",
        "search_provider": "google_news_rss",
        "source_type": "web_derived",
    },
]

# Verbatim from the measured turn: headlines from 2022, no date, no URL, nothing the
# retrieval returned.
FABRICATED = (
    "Sure! Here are some recent headlines:\n\n"
    '1. "Rust 1.64 Released with Improved Performance and Safety Features" - TechCrunch\n'
    '2. "Mozilla\'s Firefox Now Uses Rust for WebAssembly Runtime" - Hacker News\n'
    '3. "How Mozilla is Using Rust to Improve Firefox" - Ars Technica'
)


class TurnRecorder:
    """The order the real orchestrator called each boundary, and what it handed over."""

    def __init__(
        self,
        *,
        rows: list[dict[str, Any]] | None = None,
        transport_failure: BaseException | None = None,
    ) -> None:
        self.order: list[str] = []
        self.events: list[str] = []
        self.rows = RETRIEVED_NOTES if rows is None else rows
        self.transport_failure = transport_failure
        self.model_calls: list[dict[str, Any]] = []
        self.collector_calls = 0

    # -- the search transport, inside the real governed collector -----------------------
    def search(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        if self.transport_failure is not None:
            raise self.transport_failure
        return [dict(row) for row in self.rows]

    # -- the governed collector boundary, wrapped for ordering only ---------------------
    def wrap_collector(self, agent: Any) -> None:
        original = agent._collect_live_web_notes

        def wrapped(**kwargs: Any) -> list[dict[str, Any]]:
            self.order.append("retrieval")
            self.collector_calls += 1
            return original(**kwargs)

        agent._collect_live_web_notes = wrapped

    # -- the provider call ---------------------------------------------------------------
    def resolve(self, **kwargs: Any) -> ModelExecutionDecision:
        from core.runtime_task_events import emit_runtime_event

        self.order.append("model_call")
        self.model_calls.append(dict(kwargs))
        context = kwargs.get("source_context")
        emit_runtime_event(context, event_type="model.call_started", message="Model call started.", details={})
        decision = ModelExecutionDecision(
            source="provider",
            task_hash="m2-hash",
            provider_id="ollama:qwen",
            used_model=True,
            output_text=FABRICATED,
            confidence=0.82,
            trust_score=0.82,
        )
        emit_runtime_event(context, event_type="model.call_completed", message="Model call completed.", details={})
        return decision

    def prompt_material(self) -> str:
        """Everything the answering model call was handed, flattened for a substring probe."""
        return "\n".join(repr(call) for call in self.model_calls)

    def envelope_binding(self) -> dict[str, Any]:
        for call in self.model_calls:
            context = call.get("source_context")
            if isinstance(context, dict) and isinstance(context.get("evidence_synthesis_binding"), dict):
                return dict(context["evidence_synthesis_binding"])
        return {}


def _configure(agent: Any, recorder: TurnRecorder, context_result: Any) -> tuple[Any, dict[str, Any], Any, Any]:
    from apps.vool_agent import adapt_user_input

    task = SimpleNamespace(
        task_id="task-m2-ordering",
        task_summary="Latest news on Rust",
        environment_os="darwin",
        environment_shell="zsh",
        environment_runtime="python",
        environment_version_hint="3.12",
    )
    classification = {"task_class": "research"}
    interpreted = adapt_user_input(CURRENT_REQUEST, session_id="m2-ordering-session")
    persona = load_active_persona(agent.persona_id)
    adaptive_research = SimpleNamespace(
        enabled=False,
        tool_gap_note="",
        admitted_uncertainty=False,
        notes=[],
        reason="not_needed",
        strategy="none",
        actions_taken=[], queries_run=[],
        to_dict=lambda: {"enabled": False, "reason": "not_needed", "strategy": "none", "actions_taken": []},
    )

    agent.context_loader.load = mock.Mock(return_value=context_result)
    agent._should_frontload_curiosity = mock.Mock(return_value=False)
    agent._maybe_execute_model_tool_intent = mock.Mock(return_value=None)
    agent._model_routing_profile = mock.Mock(return_value=(classification, {"output_mode": ""}))
    agent._collect_adaptive_research = mock.Mock(return_value=adaptive_research)
    # The transport, not the collector: the real governed path runs and writes real receipts.
    agent._planned_search_query = mock.Mock(side_effect=recorder.search)
    agent._search_query = mock.Mock(side_effect=recorder.search)
    recorder.wrap_collector(agent)
    agent.memory_router.resolve = mock.Mock(side_effect=recorder.resolve)
    agent.media_pipeline.analyze = mock.Mock(
        return_value=SimpleNamespace(
            used_provider=False,
            provider_id="",
            candidate_id="",
            reason="no_media",
            evidence_items=[],
            analysis_text="",
        )
    )
    agent._web_note_plan_candidates = mock.Mock(return_value=[])
    agent._default_gate = mock.Mock(return_value=SimpleNamespace(mode="advice_only", requires_user_approval=False))
    agent._maybe_publish_public_task = mock.Mock(return_value={})
    agent._grounded_response_class = mock.Mock(return_value=ResponseClass.GENERIC_CONVERSATION)
    agent._turn_result = mock.Mock(
        side_effect=lambda *a, **k: ChatTurnResult(
            text=str(k.get("response") or (a[0] if a else "")),
            response_class=ResponseClass.GENERIC_CONVERSATION,
            workflow_summary="workflow summary",
            debug_origin="grounded_model",
        )
    )
    agent._apply_interaction_transition = mock.Mock()
    agent._decorate_chat_response = mock.Mock(side_effect=lambda turn, *a, **k: getattr(turn, "text", turn))
    agent._emit_chat_truth_metrics = mock.Mock()
    agent._finalize_runtime_checkpoint = mock.Mock()
    agent._runtime_preview = mock.Mock(return_value="preview")
    agent._task_workflow_summary = mock.Mock(return_value="workflow summary")
    agent._chat_surface_honest_degraded_response = mock.Mock(return_value="degraded answer")
    agent._store_local_shard = mock.Mock()
    agent.hive_activity_tracker.note_watched_topic = mock.Mock()
    return task, classification, interpreted, persona


def _drive(
    agent: Any,
    task: Any,
    classification: dict[str, Any],
    interpreted: Any,
    persona: Any,
    *,
    request: str,
    source_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context = (
        source_context
        if source_context is not None
        else {"surface": "openclaw", "platform": "openclaw", "allow_remote_fetch": True}
    )
    # `system.allow_web_fallback` defaults OFF, so the real collector declines before it opens a
    # receipt. That switch is an operator policy about whether this machine may search at all --
    # not the thing under test, and not a stand-in for one. Turning it on is what lets the REAL
    # governed path run here; every gate downstream of it (the request authority, the explicit
    # remote-fetch boundary, the surface check) is left exactly as it ships, and the
    # remote-fetch-disabled control below proves those still bite.
    with mock.patch("core.policy_engine.allow_web_fallback", return_value=True), mock.patch(
        "core.agent_runtime.agent.orchestrate_parent_task", return_value=None
    ), mock.patch(
        "core.agent_runtime.agent.ingest_media_evidence", return_value=[]
    ), mock.patch(
        "core.agent_runtime.agent.build_media_context_snippets", return_value=[]
    ), mock.patch(
        "core.agent_runtime.agent.build_plan", return_value=SimpleNamespace(confidence=0.72)
    ), mock.patch(
        "core.agent_runtime.agent.explicit_planner_style_requested", return_value=False
    ), mock.patch(
        "core.agent_runtime.agent.should_use_planner_renderer", return_value=False
    ), mock.patch(
        "core.agent_runtime.agent.feedback_engine.evaluate_outcome",
        return_value=SimpleNamespace(is_success=True, is_durable=False),
    ), mock.patch(
        "core.agent_runtime.agent.feedback_engine.apply", return_value=None
    ):
        return agent._execute_grounded_turn(
            task=task,
            effective_input=request,
            classification=classification,
            interpreted=interpreted,
            persona=persona,
            session_id="m2-ordering-session",
            source_context=context,
        )


# =========================================================================================
# The precondition this lane stands on, asserted so it cannot drift under us
# =========================================================================================


def test_the_request_this_lane_uses_is_already_classified_current_required() -> None:
    """M2 does not depend on M1 widening the classifier: this phrasing already reads True."""
    requirements = requirements_for(CURRENT_REQUEST, source_context={"surface": "openclaw"})
    assert bool(requirements.current_information_required) is True, CURRENT_REQUEST


# =========================================================================================
# ORDERING -- the headline invariant
# =========================================================================================


def test_retrieval_completes_before_the_answering_model_call(make_agent, context_result_factory) -> None:
    """At base the recorded order was ['model_call', 'retrieval'] -- the measured defect."""
    agent = make_agent()
    recorder = TurnRecorder()
    task, classification, interpreted, persona = _configure(
        agent, recorder, context_result_factory(local_candidates=[], retrieval_confidence_score=0.0)
    )

    _drive(agent, task, classification, interpreted, persona, request=CURRENT_REQUEST)

    assert "retrieval" in recorder.order, recorder.order
    assert "model_call" in recorder.order, recorder.order
    assert recorder.order.index("retrieval") < recorder.order.index("model_call"), recorder.order


def test_the_typed_event_timeline_orders_retrieval_binding_then_model_call(
    make_agent, context_result_factory, monkeypatch
) -> None:
    """EVENT ORDER: retrieval_completed < evidence_bound < model_call_completed.

    The events are captured at `core.runtime_task_events.emit_runtime_event`'s two importers
    plus the module itself, so what is asserted is the sequence the runtime actually emitted.
    """
    seen: list[str] = []

    import core.grounded_synthesis_binding as binding_module
    import core.retrieval_observability as observability_module
    import core.runtime_task_events as events_module

    real_emit = events_module.emit_runtime_event

    def recording_emit(source_context, *, event_type, message, details=None):
        seen.append(str(event_type))
        return real_emit(source_context, event_type=event_type, message=message, details=details)

    monkeypatch.setattr(events_module, "emit_runtime_event", recording_emit)
    monkeypatch.setattr(observability_module, "emit_runtime_event", recording_emit)
    monkeypatch.setattr(binding_module, "emit_runtime_event", recording_emit)

    agent = make_agent()
    recorder = TurnRecorder()
    task, classification, interpreted, persona = _configure(
        agent, recorder, context_result_factory(local_candidates=[], retrieval_confidence_score=0.0)
    )

    _drive(agent, task, classification, interpreted, persona, request=CURRENT_REQUEST)

    for required in ("web_retrieval_started", "web_retrieval_completed", "evidence_bound_to_synthesis", "model.call_completed"):
        assert required in seen, (required, seen)
    assert seen.index("web_retrieval_started") < seen.index("web_retrieval_completed"), seen
    assert seen.index("web_retrieval_completed") < seen.index("evidence_bound_to_synthesis"), seen
    assert seen.index("evidence_bound_to_synthesis") < seen.index("model.call_completed"), seen


def test_exactly_one_model_call_and_exactly_one_retrieval(make_agent, context_result_factory) -> None:
    """Hoisting must MOVE the retrieval, not add a second one, and must not re-ask the model."""
    agent = make_agent()
    recorder = TurnRecorder()
    task, classification, interpreted, persona = _configure(
        agent, recorder, context_result_factory(local_candidates=[], retrieval_confidence_score=0.0)
    )

    _drive(agent, task, classification, interpreted, persona, request=CURRENT_REQUEST)

    assert recorder.order.count("model_call") == 1, recorder.order
    assert recorder.order.count("retrieval") == 1, recorder.order
    assert recorder.collector_calls == 1, recorder.collector_calls


# =========================================================================================
# PROMPT BINDING -- the evidence is in the call, and the envelope names it
# =========================================================================================


def test_the_answering_model_call_is_handed_the_retrieved_source_sentinel(make_agent, context_result_factory) -> None:
    """A term that exists only in the retrieved rows must be in scope for the call that
    writes the answer -- not merely stored on the turn afterwards."""
    agent = make_agent()
    recorder = TurnRecorder()
    task, classification, interpreted, persona = _configure(
        agent, recorder, context_result_factory(local_candidates=[], retrieval_confidence_score=0.0)
    )

    _drive(agent, task, classification, interpreted, persona, request=CURRENT_REQUEST)

    assert recorder.model_calls, "the answering model call never happened"
    assert SOURCE_SENTINEL in recorder.prompt_material(), recorder.prompt_material()[:2000]


def test_the_model_call_envelope_names_the_evidence_set_and_note_ids_in_its_prompt(
    make_agent, context_result_factory
) -> None:
    """The envelope stamp must name ids that are genuinely present in the prompt material,
    so the claim is checkable against the prompt rather than only against itself."""
    agent = make_agent()
    recorder = TurnRecorder()
    task, classification, interpreted, persona = _configure(
        agent, recorder, context_result_factory(local_candidates=[], retrieval_confidence_score=0.0)
    )

    _drive(agent, task, classification, interpreted, persona, request=CURRENT_REQUEST)

    binding = recorder.envelope_binding()
    assert binding, "no evidence_synthesis_binding on the answering model call"
    assert binding["source_count"] == len(RETRIEVED_NOTES), binding
    assert len(binding["note_ids"]) == len(RETRIEVED_NOTES), binding

    material = recorder.prompt_material()
    assert binding["evidence_set_id"] in material, binding["evidence_set_id"]
    for note_id in binding["note_ids"]:
        assert note_id in material, note_id


def test_the_binding_record_proves_prompt_entry_and_never_claims_grounding(
    make_agent, context_result_factory
) -> None:
    """A receipt proves retrieval. A binding proves prompt entry. NEITHER proves the answer
    used the evidence -- that verdict belongs to `core.evidence_binding`, and a record that
    claimed it here is how a green row ends up standing behind a fabrication."""
    agent = make_agent()
    recorder = TurnRecorder()
    task, classification, interpreted, persona = _configure(
        agent, recorder, context_result_factory(local_candidates=[], retrieval_confidence_score=0.0)
    )

    _drive(agent, task, classification, interpreted, persona, request=CURRENT_REQUEST)

    binding = recorder.envelope_binding()
    assert binding["proves"] == PROVES_PROMPT_ENTRY, binding
    assert binding["grounded"] is None, binding


def test_a_receipt_alone_does_not_carry_a_binding(make_agent, context_result_factory) -> None:
    """RECEIPT WITHOUT BOUND EVIDENCE IS INSUFFICIENT. The retrieval receipt written by the
    real writer must contain no grounding or binding claim of its own -- the two records stay
    separable, which is what the measured defect proved they must be."""
    agent = make_agent()
    recorder = TurnRecorder()
    task, classification, interpreted, persona = _configure(
        agent, recorder, context_result_factory(local_candidates=[], retrieval_confidence_score=0.0)
    )
    context = {"surface": "openclaw", "platform": "openclaw", "allow_remote_fetch": True}

    _drive(agent, task, classification, interpreted, persona, request=CURRENT_REQUEST, source_context=context)

    receipts = context.get("web_retrieval_receipts") or []
    assert receipts, "the real governed collector wrote no receipt"
    receipt = receipts[-1]
    assert receipt["schema"] == "vool.web_retrieval_receipt.v1", receipt
    assert receipt["status"] == "available", receipt
    for forbidden in ("grounded", "evidence_set_id", "note_ids", "bound_to_synthesis", "proves"):
        assert forbidden not in receipt, (forbidden, receipt)


# =========================================================================================
# RETRIEVAL DID NOT DELIVER -- no current synthesis behind a green-looking row
# =========================================================================================


def test_a_failed_retrieval_never_reaches_the_model(make_agent, context_result_factory) -> None:
    """The transport dies inside the real collector, which receipts the failure and returns
    no rows. The model must not then be called as if it had current evidence."""
    agent = make_agent()
    recorder = TurnRecorder(transport_failure=RuntimeError("provider socket died"))
    task, classification, interpreted, persona = _configure(
        agent, recorder, context_result_factory(local_candidates=[], retrieval_confidence_score=0.0)
    )
    context = {"surface": "openclaw", "platform": "openclaw", "allow_remote_fetch": True}

    result = _drive(
        agent, task, classification, interpreted, persona, request=CURRENT_REQUEST, source_context=context
    )

    assert "retrieval" in recorder.order, recorder.order
    assert "model_call" not in recorder.order, recorder.order
    assert recorder.model_calls == [], recorder.model_calls

    receipts = context.get("web_retrieval_receipts") or []
    assert receipts and receipts[-1]["status"] == "failed", receipts
    # Typed truth, not prose that reads like an answer.
    assert result["mode"] == f"current_evidence_{OUTCOME_FAILED}", result["mode"]
    assert result["details"]["retrieval_outcome"] == OUTCOME_FAILED, result["details"]
    assert result["details"]["evidence_bound"] is False, result["details"]
    assert FABRICATED not in str(result.get("response") or ""), result.get("response")


def test_a_refused_retrieval_is_reported_as_a_permission_fact_not_a_broken_provider() -> None:
    """A refusal and a failure are different facts and get different answers. Reporting a
    refusal as `failed` sends a user to fix a key that works."""
    assert outcome_for_receipt({"status": "refused"}, notes=[]) == OUTCOME_REFUSED
    assert outcome_for_receipt({"status": "failed"}, notes=[]) == OUTCOME_FAILED
    assert outcome_for_receipt({"status": "unavailable"}, notes=[]) == OUTCOME_PARTIAL
    assert outcome_for_receipt({"status": "available"}, notes=RETRIEVED_NOTES) == OUTCOME_BOUND

    from core.agent_runtime.current_evidence_prefetch import unavailable_current_answer

    refused = unavailable_current_answer(CURRENT_REQUEST, OUTCOME_REFUSED)
    failed = unavailable_current_answer(CURRENT_REQUEST, OUTCOME_FAILED)
    assert "not currently permitted" in refused, refused
    assert "not currently permitted" not in failed, failed
    assert refused != failed


def test_an_empty_retrieval_publishes_no_fabricated_current_claim(make_agent, context_result_factory) -> None:
    """ANSWER-BEFORE-RETRIEVAL FIXTURE. The model double returns the exact bytes the measured turn
    published -- headlines from 2022, no date, no URL. The turn needs a current observation and
    has none, so those bytes must not be publishable, and the ungrounded call must not be made.

    Note what is NOT relied on here: no inspection of the answer's wording decides this. The
    turn is stopped because nothing was bound, which is a fact about the turn rather than a
    judgement about the text -- so a fabrication phrased in a shape no detector recognises is
    stopped identically."""
    agent = make_agent()
    recorder = TurnRecorder(rows=[])
    task, classification, interpreted, persona = _configure(
        agent, recorder, context_result_factory(local_candidates=[], retrieval_confidence_score=0.0)
    )

    result = _drive(agent, task, classification, interpreted, persona, request=CURRENT_REQUEST)

    assert recorder.envelope_binding() == {}, "nothing was retrieved, so nothing may be bound"
    assert "model_call" not in recorder.order, recorder.order
    published = str(result.get("response") or "")
    assert "Rust 1.64" not in published, published
    assert FABRICATED not in published, published
    assert result["mode"] == f"current_evidence_{OUTCOME_PARTIAL}", result["mode"]
    assert result["details"]["retrieval_outcome"] == OUTCOME_PARTIAL, result["details"]
    # A provider that returned nothing is not a provider that broke.
    assert "no results" in published, published


# =========================================================================================
# TURN SCOPING -- a retry cannot present another turn's evidence as this turn's
# =========================================================================================


def test_the_same_rows_on_a_different_turn_mint_a_different_evidence_set() -> None:
    """RETRY/RESUME. Ids are derived from the turn identity together with the row digests, so
    an earlier turn's set cannot be presented as this one's."""
    first = mint_evidence_set(RETRIEVED_NOTES, scope=turn_scope({"cancel_turn_id": "turn-A", "request_id": "req-A"}))
    second = mint_evidence_set(RETRIEVED_NOTES, scope=turn_scope({"cancel_turn_id": "turn-B", "request_id": "req-B"}))

    assert first.evidence_set_id != second.evidence_set_id, (first, second)
    assert set(first.note_ids).isdisjoint(second.note_ids), (first.note_ids, second.note_ids)


def test_the_same_rows_on_the_same_turn_mint_the_same_evidence_set() -> None:
    """Stable means stable: a resume that recomputes the set does not invent a second identity."""
    scope = turn_scope({"cancel_turn_id": "turn-A", "request_id": "req-A"})
    assert mint_evidence_set(RETRIEVED_NOTES, scope=scope).evidence_set_id == mint_evidence_set(
        RETRIEVED_NOTES, scope=scope
    ).evidence_set_id


def test_a_binding_record_from_another_turn_is_not_admitted() -> None:
    """The check recomputes the scope rather than trusting the id written on the record."""
    scope_a = turn_scope({"cancel_turn_id": "turn-A", "request_id": "req-A"})
    scope_b = turn_scope({"cancel_turn_id": "turn-B", "request_id": "req-B"})
    record = binding_record(mint_evidence_set(RETRIEVED_NOTES, scope=scope_a))

    assert binding_admitted_for_turn(record, scope=scope_a) is True
    assert binding_admitted_for_turn(record, scope=scope_b) is False
    # An unscoped record is never admitted: "both were unidentified" is not evidence of sameness.
    assert binding_admitted_for_turn(binding_record(mint_evidence_set(RETRIEVED_NOTES, scope="")), scope="") is False


# =========================================================================================
# CONTROL -- the lanes that must not change
# =========================================================================================


def test_a_timeless_request_keeps_the_existing_call_order(make_agent, context_result_factory) -> None:
    """Nothing is hoisted for a request that needs no current observation, so the DIRECT and
    no-retrieval lanes keep their latency and their existing shape."""
    agent = make_agent()
    recorder = TurnRecorder()
    task, classification, interpreted, persona = _configure(
        agent, recorder, context_result_factory(local_candidates=[], retrieval_confidence_score=0.0)
    )
    assert bool(
        requirements_for(TIMELESS_REQUEST, source_context={"surface": "openclaw"}).current_information_required
    ) is False

    _drive(agent, task, classification, interpreted, persona, request=TIMELESS_REQUEST)

    assert recorder.order, recorder.order
    assert recorder.order[0] == "model_call", recorder.order
    assert recorder.envelope_binding() == {}, "a timeless turn binds nothing"


def test_a_turn_with_remote_fetch_disabled_does_no_hoisted_retrieval(make_agent, context_result_factory) -> None:
    """An operator boundary is not weakened by needing current information."""
    agent = make_agent()
    recorder = TurnRecorder()
    task, classification, interpreted, persona = _configure(
        agent, recorder, context_result_factory(local_candidates=[], retrieval_confidence_score=0.0)
    )

    _drive(
        agent,
        task,
        classification,
        interpreted,
        persona,
        request=CURRENT_REQUEST,
        source_context={"surface": "openclaw", "platform": "openclaw", "allow_remote_fetch": False},
    )

    assert "retrieval" not in recorder.order, recorder.order
    assert recorder.envelope_binding() == {}, recorder.envelope_binding()


def test_evidence_a_pre_model_lane_already_retrieved_is_still_identified_and_bound(
    make_agent, context_result_factory
) -> None:
    """Found by the live drive, not by reasoning about the code.

    On the isolated daemon (drive r2, `live_reasoning_r2.json`) adaptive research retrieved four
    real sources BEFORE the model call, so the ordering invariant already held -- and the turn
    left no evidence set, no ids and no binding event. The ordering was right and unprovable.

    This lane must not re-fetch what another lane already has (that would buy a second retrieval,
    not an earlier one), so what it does instead is identify it: mint the set, put the ids in the
    prompt, emit the binding. `_collect_live_web_notes` must never be called on this path.
    """
    agent = make_agent()
    recorder = TurnRecorder()
    task, classification, interpreted, persona = _configure(
        agent, recorder, context_result_factory(local_candidates=[], retrieval_confidence_score=0.0)
    )
    # The REAL result type, not a namespace: the adaptive-research prompt builder reads fields a
    # hand-rolled double kept forgetting, and a double that drifts from the type is how this path
    # went unexercised in the first place.
    from core.curiosity_roamer import AdaptiveResearchResult

    adaptive = AdaptiveResearchResult(
        enabled=True,
        reason="research",
        strategy="web",
        notes=[dict(note) for note in RETRIEVED_NOTES],
        source_domains=["phoronix.com", "infoworld.com"],
        evidence_strength="strong",
        queries_run=["latest rust news"],
    )
    agent._collect_adaptive_research = mock.Mock(return_value=adaptive)

    _drive(agent, task, classification, interpreted, persona, request=CURRENT_REQUEST)

    assert recorder.collector_calls == 0, "the pre-model evidence must not be fetched a second time"
    binding = recorder.envelope_binding()
    assert binding, "adaptive-research evidence entered the prompt and was never identified"
    assert binding["source_count"] == len(RETRIEVED_NOTES), binding
    assert binding["proves"] == PROVES_PROMPT_ENTRY, binding
    assert binding["grounded"] is None, binding

    material = recorder.prompt_material()
    assert SOURCE_SENTINEL in material, material[:1500]
    assert binding["evidence_set_id"] in material, binding["evidence_set_id"]
    for note_id in binding["note_ids"]:
        assert note_id in material, note_id
