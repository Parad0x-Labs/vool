"""The one seam that turns REACH observation on for a turn and writes the receipt when it ends.

Kept apart from `apps.vool_agent` on purpose. The agent gains two lines -- an import and a `with` --
and everything about what is observed, whether it is observed, and what is written can change here
without touching the turn body. That is the blast-radius rule applied to instrumentation, which is
the code most likely to be revised repeatedly while the thing it measures stays still.

Three behaviours worth knowing before reading the code:

**Nested turns do not start a second recorder.** The runtime re-enters `run_once` for planned
sub-turns, and a fresh recorder per sub-turn would produce several partial receipts per user
message, each missing the gates the others saw. When a recorder is already active this yields it
unchanged, so a sub-turn's gates land in the outer turn's ordered record and one message produces
one receipt.

**The receipt is written in a `finally`.** A turn that raises has still passed through gates, and
that path is the most interesting one to be able to read afterwards. Losing the record exactly when
something went wrong would be the wrong trade.

**Nothing here can fail a turn.** Every step is wrapped, and the whole thing is a no-op when
`VOOL_SEMANTIC_REACH=0`. The `with` block yields whether or not observation is on, so the turn body
runs the same instruction sequence either way -- which is what makes the OFF/ON replay proof a
property of the code rather than a hope about it.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from core.semantic import reach as semantic_reach


def _turn_identity(source_context: dict[str, Any] | None) -> tuple[str, str]:
    """Session and turn ids, read from the same keys the runtime event funnel reads.

    Read LATE, never captured early. `_run_once_inner` fills the turn's context as it goes -- the
    session id and canonical turn id are not there when the turn starts -- so reading them at entry
    produced an empty session, and a receipt with an empty session is never written to the ledger.
    A hostile review found the CLI hitting exactly that: `run_once(source_context=None)` produced no
    semantic receipt at all.
    """
    context = source_context or {}
    session_id = str(context.get("runtime_session_id") or context.get("session_id") or "")
    turn_id = str(context.get("cancel_turn_id") or context.get("_canonical_user_turn_id") or "")
    return session_id, turn_id


@dataclass
class TurnObservation:
    """The handle a turn uses to report what actually happened to it.

    Exists so the receipt's model-lane facts come from the turn's own result rather than from a
    marker placed part-way down the pipeline. `record_outcome` is called with the finished result;
    a turn that raises never calls it, and the receipt then says so rather than guessing.
    """

    recorder: Any
    #: The context dict the turn actually used. Rebound by `bind_context` once the runtime has one,
    #: because the caller may have passed None and the real context is created inside the turn.
    context: dict[str, Any] | None = None

    def bind_context(self, context: dict[str, Any] | None) -> None:
        if isinstance(context, dict):
            self.context = context

    def record_outcome(self, result: Any) -> None:
        """Take the turn's own report of itself. Never raises."""
        try:
            if self.recorder is None or not isinstance(result, dict):
                return
            self.recorder.record_turn_outcome(
                model_calls=int(result.get("model_calls") or 0),
                route=str(result.get("route") or ""),
                route_reason=str(result.get("route_reason") or ""),
                fast_path_hit=bool(result.get("fast_path_hit")),
            )
        except Exception:
            return


def _write_receipt(recorder: semantic_reach.ReachRecorder, source_context: dict[str, Any] | None) -> None:
    """Build and store the resolution receipt for a finished turn. Never raises."""
    try:
        from core.semantic.receipt import build_resolution_receipt, emit_resolution_receipt
        from core.semantic.types import RequestShape

        session_id, turn_id = _turn_identity(source_context)
        preempted = recorder.preempted_by
        # The authoritative execution ledger for this turn, read once and handed to the (pure)
        # receipt builder so the receipt reports what ran instead of leaving every reader to infer
        # it from the reach events. Best-effort: a receipt without it is still a true receipt.
        execution: dict[str, Any] = {}
        try:
            from core.execution_truth import resolve_turn_key, turn_execution_summary

            turn_key = resolve_turn_key(source_context, None) or turn_id
            if turn_key:
                execution = turn_execution_summary(turn_key, session_id=session_id)
        except Exception:
            execution = {}
        # The turn's RequestGraph projection (written by the turn door; text-free). Its shape is
        # DERIVED from the graph's own records -- a CONDITIONAL edge, a Retraction, the request
        # count -- never from phrase rules, which is why this slot stayed UNKNOWN until a producer
        # existed. A turn no door served (or whose producer faulted) still says so.
        shape: RequestShape | str = RequestShape.UNKNOWN
        semantic: dict[str, Any] | None = None
        admission: dict[str, Any] | None = None
        try:
            from core.semantic.receipt import (
                SLOT_ATTEMPTED,
                SLOT_NOT_ATTEMPTED,
                admission_slot,
                semantic_shadow_slot,
            )
            from core.semantic.turn_graph import current_graph_projection

            projection = current_graph_projection(source_context, turn_id=turn_id or None)
            if projection is not None:
                shape = str(projection.get("shape") or RequestShape.UNKNOWN.value)
                shadow = source_context.get("_semantic_shadow") if isinstance(source_context, dict) else None
                semantic = semantic_shadow_slot(
                    state=SLOT_ATTEMPTED if isinstance(shadow, dict) and shadow else SLOT_NOT_ATTEMPTED,
                    resolver=str((shadow or {}).get("resolver") or "") if isinstance(shadow, dict) else "",
                    detail=(
                        str((shadow or {}).get("detail") or "") if isinstance(shadow, dict) and shadow
                        else ("graph producer failed: " + str(projection.get("failed")) if projection.get("failed")
                              else "no semantic resolver was consulted for this turn")
                    ),
                    graph=projection,
                    shadow=shadow if isinstance(shadow, dict) else None,
                )
            from core.semantic.semantic_result_seam import current_admission

            record = current_admission()
            if record is not None:
                admission = admission_slot(
                    state=SLOT_ATTEMPTED,
                    detail="semantic result admitted at the A2 seam",
                    admitted_count=1 if bool(getattr(record, "accepted", False)) else 0,
                    rejected_count=0 if bool(getattr(record, "accepted", False)) else 1,
                    results=[{
                        "semantic_result_id": str(getattr(record, "semantic_result_id", "") or ""),
                        "source": str(getattr(getattr(record, "source", None), "value", getattr(record, "source", "")) or ""),
                        "route_id": str(getattr(record, "route_id", "") or ""),
                        "accepted": bool(getattr(record, "accepted", False)),
                    }],
                )
        except Exception:
            semantic = None
            admission = None
        receipt = build_resolution_receipt(
            recorder,
            execution=execution,
            turn_id=turn_id or recorder.turn_id,
            session_id=session_id or recorder.session_id,
            # The lane that actually answered. `model_lane` when nothing claimed, which matches the
            # family `core.routing_decision_log` already records for that case.
            routing_family=preempted or semantic_reach.GATE_MODEL_LANE,
            routing_handled=bool(preempted),
            routing_detail="claimed by a fast-path lane" if preempted else "no fast path claimed this turn",
            shape=shape,
            semantic=semantic,
            admission=admission,
        )
        emit_resolution_receipt(source_context, receipt)
    except Exception:
        return


def _close_turn_shadow(source_context: dict[str, Any] | None) -> None:
    """Tell the shadow runtime this turn is over: a result arriving later is LATE and inert."""
    try:
        if not isinstance(source_context, dict) or not source_context.get("_semantic_shadow"):
            return
        from core.agent_runtime.semantic_shadow import default_shadow_runtime

        _session_id, turn_id = _turn_identity(source_context)
        if turn_id:
            default_shadow_runtime().close_turn(turn_id)
    except Exception:
        return


@contextmanager
def observe_turn(source_context: dict[str, Any] | None = None) -> Iterator[TurnObservation]:
    """Observe one user turn end-to-end. Always yields a handle, even when observation is off.

    The handle rather than the raw recorder is what lets the caller report the finished result
    (`record_outcome`) and, crucially, hand back the context the runtime actually built
    (`bind_context`) -- which is how a `source_context=None` turn still gets a receipt.
    """
    existing = semantic_reach.current()
    if existing is not None:
        # A nested sub-turn. Its gates belong to the message already being observed, and the outer
        # turn owns the outcome -- so this handle deliberately carries no recorder to report with.
        yield TurnObservation(recorder=None, context=source_context)
        return

    session_id, turn_id = _turn_identity(source_context)
    with semantic_reach.observing_turn(session_id=session_id, turn_id=turn_id) as recorder:
        observation = TurnObservation(recorder=recorder, context=source_context)
        try:
            yield observation
        finally:
            if recorder is not None:
                # The context is re-read here, not at entry: the session id only exists once the
                # turn has run. `observation.context` is whatever the runtime bound, falling back to
                # what the caller passed.
                _write_receipt(recorder, observation.context)
                _close_turn_shadow(observation.context)


__all__ = ["TurnObservation", "observe_turn"]
