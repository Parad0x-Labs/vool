"""A7 canonical finalization authority — the ONE authority for finalized answer truth.

Extracted from the proven web-lane response-commit mechanism into a
transport-neutral home. Every semantic assistant answer traverses here exactly
once per turn:

    admit (A2) -> presentation completes -> finalize -> persist -> transport framing

Laws enforced by this module:

- A2 identity is INPUT: ``semantic_result_id`` is consumed verbatim from the
  frozen admission record inside the sealing context. It is never parsed,
  reconstructed, or reminted; A7-local identity lives in its own ``fc:``
  namespace and references, never replaces, the admitted id.
- Hash last: ``content_hash`` covers the exact canonical answer bytes.
- Typed terminal split: ANSWER_PRESENT (bytes exist => finalization required)
  vs NO_ANSWER_TERMINAL (typed truth; no manufactured prose).
- Durable immutability: first content wins once per logical truth (unique
  partial index on the admitted semantic id); identical duplicates are accepted
  idempotently; different-content second finalization raises
  :class:`FinalizationRejected`. Correctness lives in SQLite semantics.
"""
from __future__ import annotations

import contextlib
import contextvars
import hashlib
import logging
import os
import sqlite3
from typing import Any

from storage.db import get_connection

_log = logging.getLogger(__name__)

ANSWER_PRESENT = "answer_present"
NO_ANSWER_TERMINAL = "no_answer_terminal"


class NoAnswerContent(ValueError):
    """Refused: empty provider content presented as successful semantic answer.

    Per product law, zero-byte semantic answers are only legal when an explicit
    upstream typed contract positively identifies them as intentional; absent
    that proof, empty content is failure/no-answer truth, never assistant
    content.
    """


class ReplayAmbiguityRefused(RuntimeError):
    """PASS003 (CE13) fail-closed refusal: the requested identity maps to more
    than one immutable finalization row, so NO authoritative truth can be
    selected — replay is refused instead of resolving by SQL sort internals
    (which allowed substituted bytes to be served as owner-committed truth)."""


# Re-exported so callers of this authority have a single import site.
from core.final_response_store import FinalizationRejected


def _sha256_hex(text: str) -> str:
    return "sha256:" + hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _utcnow() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def current_semantic_result_id() -> str:
    """Consume the frozen A2 admission id verbatim (opaque read-only)."""
    try:
        from core.semantic.semantic_result_seam import current_admission

        record = current_admission()
    except Exception:  # pragma: no cover - seam absence must never crash finality
        return ""
    if record is None:
        return ""
    return str(getattr(record, "semantic_result_id", "") or "")


def _authoritative_closure_verdict(caller_closure: dict[str, Any] | None) -> dict[str, Any]:
    """F-07: the durable obligation ledger is authoritative on closure. A
    bound set's verdict is always re-derived from the ledger; a caller-supplied
    dict is advisory and used ONLY when no set is bound (legacy lanes)."""
    try:
        from core.conductor import obligation_ledger as _obligations

        _bound_active = _obligations.active_set()
    except Exception:
        _bound_active = None
    if _bound_active is not None:
        return dict(_obligations.closure_verdict(*_bound_active))
    # The same law, one step further: when the caller's dict IDENTIFIES its set, the
    # ledger is still authoritative over it. The dict is computed inside the turn's
    # execution scope and can only be a snapshot of that moment — the closure sweep
    # runs here, after it, and its dispositions must be what the certificate reports.
    if isinstance(caller_closure, dict):
        set_id = str(caller_closure.get("set_id") or "").strip()
        version = str(caller_closure.get("set_version") or "").strip()
        if set_id and version:
            try:
                from core.conductor import obligation_ledger as _ledger

                return dict(_ledger.closure_verdict(set_id, version))
            except Exception:
                return dict(caller_closure)
    return dict(caller_closure) if caller_closure else _active_closure_verdict()


def _active_closure_verdict() -> dict[str, Any]:
    """K-05: consult the cross-lane obligation ledger for the turn's verdict.

    A lane with NO bound obligation set is vacuously covered (pre-wiring
    bridge, R-0/A-4a). A BOUND set whose verdict cannot be established is a
    refusal: the exception PROPAGATES — fail closed, never silently covered.
    """
    from core.conductor import obligation_ledger

    active = obligation_ledger.active_set()
    if active is None:
        # R-6 end state: an ONBOARDED lane (execution identity bound) must
        # finalize with a REAL verdict — vacuous coverage is refused. Legacy
        # unfenced lanes keep the pre-wiring bridge.
        from core.semantic.semantic_admissions import current_execution_identity

        if current_execution_identity():
            raise RuntimeError(
                "K-05: no closure verdict available on an onboarded lane; "
                "finalization refused (fail closed)"
            )
        return {"covered": True, "open_count": 0, "set_version": ""}
    return obligation_ledger.closure_verdict(*active)


#: The bytes a cancelled turn commits instead of the answer its lanes went on to compose.
CANCELLED_BEFORE_PUBLICATION = (
    "Cancelled before publication: this turn was stopped while it was working, so no answer "
    "was published."
)


def _turn_cancel_marker_fired(source_context: dict[str, Any] | None) -> bool:
    """The router's cancel vocabulary (an Event-like `.is_set()` or a callable token)."""
    marker = (source_context or {}).get("cancel_event") or (source_context or {}).get("cancellation_token")
    if marker is None:
        return False
    try:
        is_set = getattr(marker, "is_set", None)
        if callable(is_set):
            return bool(is_set())
        return bool(marker()) if callable(marker) else False
    except Exception:
        return False


RSS_UNAVAILABLE_HEADER = "Could not be answered:"


#: What the reader is told about a slot whose answer the publication gate removed. Closed
#: vocabulary, beside the ledger's own reasons: it states what happened, not a diagnosis.
RSS_REASON_WITHHELD = (
    "an answer was produced but the sources retrieved for this turn did not support it, "
    "so it was withheld"
)
#: The same statement for a turn that RE-PRESENTED the previous answer: its support was that
#: answer's published support, and no source was retrieved this turn to name.
RSS_REASON_WITHHELD_RE_PRESENTATION = (
    "an answer was produced but the previous answer's published support did not cover it, "
    "so it was withheld"
)
#: A slot whose answer was PUBLISHED IN PART: the gate kept the supported statements and listed
#: the rest above as withheld. Saying such a slot "could not be answered" was false (measured
#: 2026-09-06: a comparison published six supported claims and was listed as unanswered).
RSS_PARTIAL_HEADER = "Answered in part:"
RSS_REASON_ANSWERED_IN_PART = (
    "answered in part — the statements the sources retrieved for this turn did not support "
    "are listed above as withheld"
)


def _render_closure_rows(pending: list[dict[str, Any]], content: str) -> str:
    """The closure section: unanswered slots under one header, partially answered under another."""
    unanswered = [row for row in pending if not row.get("answered_in_part")]
    partial = [row for row in pending if row.get("answered_in_part")]
    blocks: list[str] = []
    if unanswered:
        lines = [] if RSS_UNAVAILABLE_HEADER in content else [RSS_UNAVAILABLE_HEADER]
        lines += [f"* {str(row.get('text') or '').strip()} — {row.get('reason')}" for row in unanswered]
        blocks.append("\n".join(lines))
    if partial:
        lines = [] if RSS_PARTIAL_HEADER in content else [RSS_PARTIAL_HEADER]
        lines += [f"* {str(row.get('text') or '').strip()} — {row.get('reason')}" for row in partial]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _rss_bound_set(caller_closure: dict[str, Any] | None) -> tuple[str, str] | None:
    """Locate the turn's obligation set for the sweep.

    The ContextVar first, then the closure dict the payload carries. The second
    path is the load-bearing one: `apps/vool_agent.py` stashes the verdict on the
    payload precisely BECAUSE the ContextVar scope has exited by the time transport
    finalizes, and five of the six `finalize_answer` call sites pass no closure at
    all. A sweep that could only read the ContextVar would be a sweep that runs on
    one door out of six.
    """
    try:
        from core.conductor import obligation_ledger as _obligations

        bound = _obligations.active_set()
    except Exception:
        bound = None
    if bound is not None:
        return bound
    if not isinstance(caller_closure, dict):
        return None
    set_id = str(caller_closure.get("set_id") or "").strip()
    version = str(caller_closure.get("set_version") or "").strip()
    if set_id and version:
        return (set_id, version)
    return None


def _rss_set_for_request() -> tuple[str, str] | None:
    """Last resort: the set minted under this turn's A0 request id."""
    try:
        from core.conductor import obligation_ledger as _obligations
        from core.semantic.semantic_admissions import current_request_id

        return _obligations.set_for_request(current_request_id())
    except Exception:
        return None


def _under_literal_output_contract(request: str) -> bool:
    """Whether this turn's own instructions make the served bytes the deliverable.

    Read from `core.raw_output_contract`, which is where the contract is expressed and
    enforced (`b148d2cc`, "enforce raw output-only contracts") -- not from a phrase list here.
    A turn that says "Only the list.", "JSON only", "one word", "just the number" has told the
    runtime that anything appended to the answer is a violation of the answer.
    """
    try:
        from core.raw_output_contract import parse_raw_output_contract

        return parse_raw_output_contract(str(request or "")) is not None
    except Exception:
        return False



def _frozen_slot_set(bound: tuple[str, str]) -> tuple[frozenset[str], str]:
    """The turn's frozen SlotId set and its source (graph / legacy_reparse / none). Never raises."""
    try:
        from core.conductor import obligation_ledger as _obligations
        from core.semantic.bridges.ledger import frozen_slot_ids

        return frozen_slot_ids(_obligations.snapshot_request_graph(*bound))
    except Exception:
        return frozenset(), "none"


#: Set to ``enforce`` to make the slot/publication conservation laws fail-closed at finalization.
#: Default is observe-only: every turn RECORDS its verdict as a ledger event; nothing is rejected.
CONSERVATION_ENV = "VOOL_SEMANTIC_CONSERVATION"
CONSERVATION_EVENT = "semantic_conservation_verdict"


def _semantic_conservation_verdict(
    caller_closure: dict[str, Any] | None, content: str, source_context: dict[str, Any] | None
) -> dict[str, Any] | None:
    """The slot-conservation and publication-conservation verdicts for this turn, recorded.

    Slot law: the frozen SlotId set (from the door's graph) equals the set of terminal demand
    dispositions, each terminating exactly once, ANSWERED only with evidence, every result bound to
    THIS turn's graph. Publication law: every terminal slot is VISIBLE in the served bytes.

    EVIDENCE GRADE. The only per-slot evidence this runtime can derive from the final payload today
    is the text ladder (``unit_answer_evidence`` over the FINAL bytes -- after grounding, redaction
    and format passes). A ledger row that says ``satisfied`` is a label; it counts as an answer here
    only if the ladder finds the slot in the bytes that ship, and it is reported as UNVERIFIED (and
    hidden) otherwise. Renderer-emitted slot-bound blocks are the stronger evidence this verdict
    does not yet have, and the grade says so; nobody may read this verdict as block-bound proof.

    STATUS. ``evaluated`` when a graph and terminal rows existed; ``not_evaluated`` with a reason
    when they did not (no bound set, a pre-graph set, an UNREADABLE stored graph, no rows). A missing
    check is reported as missing, never as green. Observe-only unless
    ``VOOL_SEMANTIC_CONSERVATION=enforce``; enforcement acts only on an evaluated verdict.
    """
    bound = _rss_bound_set(caller_closure) or _rss_set_for_request()
    if bound is None:
        return None
    verdict: dict[str, Any] = {
        "schema": "vool.semantic_conservation_verdict.v1",
        "set_id": bound[0],
        "set_version": bound[1],
        "status": "not_evaluated",
        "reason": "",
        "evidence_grade": "text_ladder_over_final_bytes",
        "enforced": os.environ.get(CONSERVATION_ENV, "").strip().lower() == "enforce",
    }
    try:
        from core.agent_runtime.answer_coverage import DEMAND_SATISFIED, unit_answer_evidence, unit_is_disclosed_in
        from core.conductor import obligation_ledger as _obligations
        from core.semantic.bridges.demand import slot_results_from_ledger
        from core.semantic.bridges.ledger import graph_from_snapshot, snapshot_has_graph
        from core.semantic.bridges.publication import published_slots
        from core.semantic.publication_conservation import verify_publication
        from core.semantic.slot_reconciliation import reconcile

        snapshot = _obligations.snapshot_request_graph(*bound)
        graph = graph_from_snapshot(snapshot)
        if graph is None:
            verdict["reason"] = "unreadable_graph" if snapshot_has_graph(snapshot) else "no_graph"
            _emit_conservation_event(source_context, verdict)
            return verdict
        rows = _obligations.demand_obligations(*bound)
        if not rows:
            verdict["reason"] = "no_rows"
            _emit_conservation_event(source_context, verdict)
            return verdict
        request_text = str((snapshot or {}).get("request_text") or "")
        ladder = unit_answer_evidence(request_text, content) if request_text else {}
        answered: dict[str, str] = {}
        rendered: dict[str, str] = {}
        for row in rows:
            unit_id = str(row.get("unit_id") or "")
            state = str(row.get("state") or "")
            if state == DEMAND_SATISFIED:
                if ladder.get(unit_id) == DEMAND_SATISFIED:
                    answered[unit_id] = f"text_ladder:{DEMAND_SATISFIED}"
            elif unit_is_disclosed_in(str(row.get("text") or ""), content):
                rendered[unit_id] = str(row.get("text") or "")
        results = slot_results_from_ledger(rows, answered_content=answered, graph=graph)
        reconciliation = reconcile(graph, results, require_binding=True)
        publication = verify_publication(
            reconciliation, published_slots(results, answered_units=answered, rendered_units=rendered)
        )
        verdict.update({
            "status": "evaluated",
            "graph_digest": _graph_digest_of(graph),
            "frozen_slot_count": len(graph.slot_ids),
            "terminal_slot_count": len(results),
            "slot_conservation": {
                "conserved": reconciliation.conserved,
                "missing": list(reconciliation.missing),
                "invented": list(reconciliation.invented),
                "duplicated": list(reconciliation.duplicated),
                "answered_without_content": list(reconciliation.answered_without_content),
                "foreign": list(reconciliation.foreign),
                "unbound": list(reconciliation.unbound),
            },
            "publication_conservation": {
                "conserved": publication.conserved,
                "omitted": list(publication.omitted),
                "hidden": list(publication.hidden),
                "invented": list(publication.invented),
            },
        })
        _emit_conservation_event(source_context, verdict)
        return verdict
    except Exception as exc:
        _log.warning("semantic conservation verdict failed: %s", exc)
        verdict["reason"] = f"error:{type(exc).__name__}"
        _emit_conservation_event(source_context, verdict)
        return verdict


def _graph_digest_of(graph: Any) -> str:
    try:
        from core.semantic.graph_serialization import graph_digest

        return graph_digest(graph)
    except Exception:
        return ""


def _emit_conservation_event(source_context: dict[str, Any] | None, verdict: dict[str, Any]) -> None:
    """Record the verdict on the runtime ledger. Fail-soft; the verdict dict is the caller's copy."""
    try:
        from core.runtime_task_events import emit_runtime_event

        if verdict.get("status") != "evaluated":
            message = f"Conservation: not evaluated ({verdict.get('reason') or 'unknown'})"
        else:
            slots_ok = bool(verdict["slot_conservation"]["conserved"])
            pub_ok = bool(verdict["publication_conservation"]["conserved"])
            message = (
                f"Conservation: slots {'conserved' if slots_ok else 'NOT conserved'}, "
                f"publication {'conserved' if pub_ok else 'NOT conserved'} "
                f"(evidence: {verdict.get('evidence_grade')})"
            )
        emit_runtime_event(
            source_context,
            event_type=CONSERVATION_EVENT,
            message=message,
            details={"verdict": verdict, "reason": "semantic_conservation"},
        )
    except Exception:
        pass


def _rss_closure_sweep(
    content: str,
    caller_closure: dict[str, Any] | None,
    withheld_claims: tuple[str, ...] = (),
    *,
    withheld_reason: str = RSS_REASON_WITHHELD,
    published_in_part: bool = False,
) -> tuple[str, dict[str, Any]]:
    """THE enforcement point: no requested slot leaves this turn unaccounted.

    AUD-20260829-003. The turn's demand set is minted at intake; here, at the one
    function every finalized answer passes through, every demand slot still open is
    driven to a terminal state and EVERY slot no lane answered is RENDERED as an
    unavailable row naming what was asked and why.

    The render used to be gated on a registered family also reading the slot. Measured
    live over HTTP on the operator's own evening turn, that gate produced
    `demand_unanswered: 3, demand_rendered: 0` — truthful counting behind a body that
    was still just the FX line, so the user experience was identical to the defect.
    Counting privately is not disclosure. The gate is gone; what protects the
    single-domain population instead is the EVIDENCE ladder below, which discharges a
    slot the answer addresses and a slot that names no thing to look up.

    Placed inside this module rather than at the call sites deliberately. There are
    six real `finalize_answer` call sites (`channel_gateway.py:188`,
    `service.py:3823/:4146/:4348`, `runtime.py:2444`, `presence.py:256`); only four
    share a helper with a working closure handoff, and none of them catches
    `FinalizationRejected`. A sweep retrofitted per call site would make some turns
    die with a fully computed answer discarded and let others silently keep the
    false-coverage defect — strictly worse than doing nothing. One function, six
    doors, no retrofit.

    Fail-visible, never fail-dead: this runs BEFORE the `covered` check and drives
    every demand obligation terminal, so RSS can never be the reason a turn refuses
    finalization. An internal failure here is caught and recorded in the
    certificate; it never takes a computed answer down with it.
    """
    bound = _rss_bound_set(caller_closure) or _rss_set_for_request()
    if bound is None:
        return content, {}
    try:
        from core.agent_runtime.answer_coverage import (
            DEMAND_SATISFIED,
            DEMAND_UNANSWERED,
            unit_answer_evidence,
            unit_is_disclosed_in,
            units_without_own_object,
        )
        from core.conductor import obligation_ledger as _obligations

        if not _obligations.demand_obligations(*bound):
            return content, {}
        request = _obligations.request_text(*bound)
        # KEYSTONE: the frozen slot set is READ from the graph the door persisted, and the text
        # ladder below (which still re-derives unit ids from the request text) is checked against
        # it. A ladder id outside the frozen set is a drift between two readings of one turn --
        # recorded, never acted on here. A pre-graph snapshot is served by the legacy re-derivation
        # and says so (`legacy_reparse`).
        _frozen_slots, _frozen_source = _frozen_slot_set(bound)
        states: dict[str, str] = {}
        if request:
            # THE EVIDENCE LADDER, applied here because this is the only place that
            # holds the FINALIZED bytes. Receipts are facts; the sweep below is still
            # the only writer of a disposition.
            for unit_id in units_without_own_object(request):
                _obligations.record_slice_consumption(
                    *bound, unit_id=unit_id, evidence="ride_along_no_object"
                )
            states = unit_answer_evidence(request, content)
            if _frozen_source == "graph" and set(states) - _frozen_slots:
                _log.warning(
                    "rss sweep: ladder ids %s outside the frozen slot set %s (set %s)",
                    sorted(set(states) - _frozen_slots), sorted(_frozen_slots), bound[0],
                )
            if _under_literal_output_contract(request) or (
                isinstance(caller_closure, dict)
                and caller_closure.get("literal_answer_lane")
            ):
                # The turn's own instructions — or the stipulated-contract lane that answered it —
            # make the bytes the deliverable, so its demand
                # set is not a set of slots to account against the prose. Nothing is claimed
                # unanswered here: the runtime keeps the record and makes no accusation.
                states = {
                    unit_id: (DEMAND_SATISFIED if verdict == DEMAND_SATISFIED else "indeterminate")
                    for unit_id, verdict in states.items()
                }
            for unit_id, verdict in states.items():
                if verdict == DEMAND_SATISFIED:
                    _obligations.record_slice_consumption(
                        *bound, unit_id=unit_id, evidence="served_answer_evidence"
                    )
        if len(states) < 2:
            # A turn that asked for ONE thing and got bytes back did not drop a slot: there
            # was no second slot to drop. Disclosure exists to catch PARTIAL service, and a
            # single-unit turn has no partial. Measured: `18^2` answered `324` — a correct,
            # terse, value-only answer that echoes none of the question — was denied by its
            # own reply. At worst such a unit is indeterminate; a lane that truly failed says
            # so in its own refusal text, which is its job, not this sweep's.
            states = {
                unit_id: (verdict if verdict == DEMAND_SATISFIED else "indeterminate")
                for unit_id, verdict in states.items()
            }
        # WHEN THE GATE EDITED THE ANSWER, THE BYTES DECIDE.
        #
        # A lane-attested receipt outranks everything below it, and rightly: the lane ran and
        # produced content. Its premise is that the content it produced is in the answer. The
        # grounding publication gate runs AFTER the lane and can remove a statement the sources
        # do not support -- and at that moment the premise is void for this turn, because the
        # bytes being certified are no longer the bytes the receipt was filed against.
        #
        # Measured on a served four-slot turn: the gate withheld the Rome weather statement,
        # the receipt stood, and the certificate reported `covered: true, demand_satisfied: 4`
        # over bytes containing neither "Rome" nor "weather". The text ladder had it right on
        # those same bytes and was overridden.
        #
        # So on a turn that withheld anything, receipts stop outranking the served text and the
        # ladder's reading of the PUBLISHED bytes decides. A slot still present in the answer
        # is still satisfied -- this narrows nothing that survived the gate. It is deliberately
        # a property of the TURN, not a guess about which claim belonged to which slot: a
        # withheld sentence is the answer's own words, and it need not echo the question's.
        rows = _obligations.sweep_demand_obligations(
            *bound, states=states, receipts_outrank_bytes=not withheld_claims
        )
        if not rows:
            return content, {}
        census = dict(_obligations.demand_census(*bound))
        # M3B root cause D: the attempt row's coarse status is reconciled from SUBTASK
        # states (`reconcile_attempt_lifecycle`), so "every dispatched subtask succeeded"
        # finalized SUCCEEDED even while demands the plan never admitted went unanswered --
        # Incident 3 served London weather only and certified full success. This is the one
        # seam where the demand census is terminal, so this is where SUCCEEDED is corrected:
        # a provably-unanswered demand demotes the attempt to PARTIAL_SUCCESS with the census
        # count as its reason. Monotonic CAS (SUCCEEDED -> PARTIAL_SUCCESS only), never a
        # resurrection path, and fail-visible: an internal failure is recorded, never thrown.
        unanswered_rows = [row for row in rows if row.get("state") == DEMAND_UNANSWERED]
        if unanswered_rows:
            attempt_id = _obligations.attempt_id_of_set(*bound)
            if attempt_id:
                try:
                    from core.runtime_continuity import demote_runtime_attempt_if_succeeded

                    demote_runtime_attempt_if_succeeded(
                        attempt_id,
                        terminal_reason=(
                            f"{len(unanswered_rows)} of {census.get('demand_minted', len(unanswered_rows))} "
                            "demand obligations unanswered"
                        ),
                    )
                except Exception as exc:
                    _log.warning(
                        "demand-census attempt demotion failed for %s: %s", attempt_id, exc
                    )
                    census["demotion_error"] = type(exc).__name__
        pending = [
            row
            for row in rows
            if row.get("state") == DEMAND_UNANSWERED
            and not unit_is_disclosed_in(str(row.get("text") or ""), content)
        ]
        if withheld_claims:
            # AND THE READER IS TOLD. Demoting a withheld slot in the census stops the
            # certificate lying, but the certificate is not what the user reads. Measured on
            # the served turn: the gate removed the Rome weather line, the answer went out with
            # no Rome in it, and the only trace was "Withheld from this answer: 1 statement
            # that the sources retrieved for this turn do not support" -- which names a source
            # URL, not the slot. The reader was never told which of the four things they asked
            # for went missing. That is C7's silent drop, arriving through the gate.
            #
            # Only slots the answer does not otherwise carry, and only on a turn that actually
            # withheld something, so a normal turn renders exactly what it did before.
            already = {str(row.get("text") or "") for row in pending}
            pending += [
                {
                    **row,
                    "reason": RSS_REASON_ANSWERED_IN_PART if published_in_part else withheld_reason,
                    **({"answered_in_part": True} if published_in_part else {}),
                }
                for row in rows
                if row.get("state") != DEMAND_SATISFIED
                and str(row.get("text") or "") not in already
                and not unit_is_disclosed_in(str(row.get("text") or ""), content)
            ]
        literal_lane = bool(
            isinstance(caller_closure, dict) and caller_closure.get("literal_answer_lane")
        )
        if literal_lane or (request and _under_literal_output_contract(request)):
            # A turn whose contract is "only the list" gets the list. Appending denial rows
            # to it is a contract violation in the served bytes -- measured live (RED-1
            # NEW-2): a correct `A1, B2, C3` came back with four rows glued underneath it.
            # Only the RENDERING is withheld, because here the bytes ARE the deliverable.
            #
            # The count is taken over rows this turn cannot show were answered, NOT over
            # `pending`. Literal turns flatten every non-satisfied verdict to `indeterminate`
            # above, precisely so the lane makes no accusation -- which also empties
            # `pending`, so the old count reported 0 withheld on a turn that withheld
            # everything, under a comment claiming the certificate told the truth. It did
            # not. Withholding a row and certifying that nothing was withheld is the silent
            # drop this class is about, relocated into the certificate. (L6.)
            census["demand_rendered"] = 0
            census["demand_render_withheld"] = len(
                [
                    row
                    for row in rows
                    if row.get("state") != DEMAND_SATISFIED
                    and not unit_is_disclosed_in(str(row.get("text") or ""), content)
                ]
            )
            return content, census
        census["demand_rendered"] = len(pending)
        if not pending:
            return content, census
        return content.rstrip() + "\n\n" + _render_closure_rows(pending, content), census
    except Exception as exc:  # never let accounting discard a computed answer
        _log.warning("RSS closure sweep failed; turn ships unswept: %s", exc)
        return content, {"rss_sweep_error": type(exc).__name__}


def retry_classification_for_turn(turn_id: str, *, session_id: str = "") -> str:
    """The turn's retry hint, DERIVED from the faults its boundaries filed.

    This is the finalization seam consuming the fault plane: the classification is a
    pure projection of the turn's durable fault records (a cancellation outranks a
    timeout, a policy refusal never reads as retryable), so the terminal truth can no
    longer disagree with what the boundaries recorded. Empty means no fault said
    anything about retrying -- a fact, never a claimed all-clear. Best-effort: the
    commit must not fail because the fault store is unreadable.
    """
    try:
        from core.faults.mapping import retry_classification_from_faults
        from core.faults.recorder import faults_for_turn

        return retry_classification_from_faults(
            faults_for_turn(str(turn_id or "").strip(), session_id=str(session_id or ""))
        )
    except Exception:
        return ""


def _restamp_presentation_selection(
    final_content: str,
    selection_context: dict[str, Any],
    display: dict[str, Any] | None,
    grounding_refused: bool,
    turn_id: str,
) -> dict[str, Any]:
    """C19: re-derive the presentation-selection record from POST-GATE bytes.

    The same pure selector that ran in the router runs once more over the bytes
    that actually commit, so the shipped record can never describe bytes that
    did not ship. Reconciliation with the router's pre-gate record is limited
    to what the re-run cannot know on its own:

    - a grounding REFUSAL re-stamps the whole record (``grounding_gate``);
    - a prior stand-down (explicit contract, exact contract, neutralization)
      stands, because those disables belong to the turn, not the bytes;
    - a prior ``fallback="prose_default"`` (the one bounded repair ran and was
      not accepted) survives onto a still-prose record.
    """
    from core.presentation_selection import (
        SELECTION_SCHEMA,
        select_presentation,
    )

    clean_turn_id = str(turn_id or "").strip()
    if grounding_refused:
        return {
            "turn_id": clean_turn_id,
            "elected": None,
            "trigger": None,
            "disabled_by": "grounding_gate",
            "gap_detected": False,
            "fallback": None,
            "origin": "automatic",
            "schema": SELECTION_SCHEMA,
        }
    prior: dict[str, Any] = {}
    if isinstance(display, dict):
        candidate = display.get("presentation_selection")
        if isinstance(candidate, dict) and candidate.get("schema") == SELECTION_SCHEMA:
            prior = candidate
    if not prior:
        candidate = selection_context.get("presentation_selection")
        if isinstance(candidate, dict) and candidate.get("schema") == SELECTION_SCHEMA:
            prior = candidate
    if prior.get("disabled_by"):
        return {**prior, "turn_id": clean_turn_id or str(prior.get("turn_id") or "")}
    record = select_presentation(final_content, selection_context)
    if prior.get("fallback") == "prose_default" and record.get("gap_detected"):
        record = {**record, "fallback": "prose_default"}
    return record


def finalize_answer(
    *,
    turn_id: str,
    canonical_content: str,
    source_context: dict[str, Any] | None = None,
    display_metadata: dict[str, Any] | None = None,
    status: str = ANSWER_PRESENT,
    closure: dict[str, Any] | None = None,
    root_cause: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Establish finalized answer truth for one admitted turn.

    Returns the immutable typed finalization object (the ``response.commit``
    envelope, extended with A7 identity fields). Durable binding happens here,
    before any caller persists derived representations or serves a byte.

    K-05 CERTIFICATE-AT-FINALITY: the closure verdict
    ``{covered, open_count, set_version}`` is required input. A structurally
    open set (open_count > 0) refuses finalization — incompleteness must never
    become permanently immutable.

    ROOT-CAUSE CONTRACT: when the turn carries a repair diagnosis (the typed
    `core.root_cause_contract` riding the turn context, or its dict projection
    handed by the transport shim), it is VALIDATED here and stamped into the
    commit as derived truth — state plus operator status. A record claiming
    verification/completion its own evidence contradicts is REFUSED
    (fail-closed, exactly like the closure verdict): a symptom patch can never
    finalize as a completed root repair. Turns without a contract are
    untouched — the doctrine is not forced onto ordinary turns.
    """
    content = str(canonical_content or "")
    # The turn's cancel marker, captured before any local rebinding of `source_context` below.
    _cancel_context = source_context if isinstance(source_context, dict) else None
    if not content.strip():
        raise NoAnswerContent(
            "zero-byte semantic answer refused: no explicit intentional-empty contract upstream"
        )
    # ROOT-CAUSE CONTRACT — resolve and validate BEFORE the reserved-key
    # discard below. The explicit wire projection wins (it is what the turn
    # sealed); the typed context object is the in-turn transport.
    root_cause_publication: dict[str, Any] | None = None
    root_cause_state = ""
    root_cause_status = ""
    resolved_root_cause = root_cause
    if resolved_root_cause is None and isinstance(source_context, dict):
        riding = source_context.get("root_cause_contract")
        if isinstance(riding, dict):
            resolved_root_cause = riding
        elif riding is not None and type(riding).__name__ == "RootCauseContract":
            resolved_root_cause = riding.to_dict()
    if resolved_root_cause is not None:
        from core.root_cause_contract import (
            RootCauseContractError,
            publication_for,
            validate_publication,
        )

        try:
            validated_contract = validate_publication(resolved_root_cause)
        except RootCauseContractError as exc:
            raise FinalizationRejected(
                "", f"ROOT_CAUSE_CONTRACT_INCONSISTENT:{exc}", ""
            ) from exc
        root_cause_publication = publication_for(validated_contract)
        root_cause_state = validated_contract.state
        root_cause_status = validated_contract.operator_status()
    # C19 PRESENTATION SELECTION — the selection inputs must be captured before
    # the reserved-key discard below: the post-gate re-stamp re-runs the same
    # pure function on the bytes that actually ship, against the context the
    # turn carried (constraint record, neutralization flags, runtime notices).
    _selection_context: dict[str, Any] = dict(source_context) if isinstance(
        source_context, dict
    ) else {}
    del source_context  # reserved for sealing-context correlation; identity comes from the seam
    # R-4 (H-2): an onboarded lane presents its fence tuple at finality —
    # a stale generation/epoch is refused with zero rows written.
    from core.semantic.semantic_admissions import enforce_fence_if_onboarded

    enforce_fence_if_onboarded()
    # M3 PUBLICATION GATE. The ONE seam that owns PUBLISHED.
    #
    # For a turn `core.execution_requirements` marked current-information, model-generated
    # bytes may commit only when the answering model call named the bound evidence set and
    # every claim in THESE bytes is supported by it. Any other turn — every DIRECT and
    # timeless answer — has no lifecycle row and passes through without a claim matcher
    # running or a byte being read.
    #
    # Here, and above the RSS sweep, for two reasons. Here: `finalize_answer` is the single
    # authority every semantic answer traverses, through all six transport doors, so a gate
    # anywhere else certifies one door and leaves five open. Above the sweep: the sweep's
    # unanswered-slot census is typed runtime text about the REQUEST, not a claim about the
    # world, and gating it would strip a turn's account of what it could not answer.
    #
    # Fail-open on an internal error is deliberate and bounded: this gate can only ever
    # narrow what ships, so a gate that cannot run must not be able to kill a turn that was
    # otherwise going to answer. What it must never do is silently pass a REFUSAL — and it
    # cannot, because a verdict it never produced is a verdict it never applied.
    from core.grounding_publication import gate_publishable_content

    # PER-UNIT SUPPORT FOR THE PLAIN LANE (F48/F11). The conductor feeds the computed and
    # stable-knowledge channels per node; the plain lane fed nothing, so a mixed turn it served
    # was refused WHOLE by the gate below. Each demand unit the requirements authority reads as
    # stable now records its answering line (with the served author's policy verdict) or its
    # runtime-computed value, and the gate's own exemption and support rules decide from there.
    try:
        from core.plain_lane_stable_units import publish_plain_lane_units_for_turn

        publish_plain_lane_units_for_turn(
            _selection_context if isinstance(_selection_context, dict) else None,
            turn_id=turn_id,
            answer_text=str(content or ""),
        )
    except Exception:  # pragma: no cover - the gate runs on the channels it has
        _log.exception("plain-lane unit publication failed; the gate runs on the channels it has")
    try:
        content, _grounding_record = gate_publishable_content(
            content, turn_id=turn_id,
            runtime_notice=_selection_context.get("runtime_notice_not_an_answer") is True,
        )
    except Exception:  # pragma: no cover - the gate must not be able to end a working turn
        _log.exception("grounding publication gate failed; publishing ungated")
        _grounding_record = {}
    if not str(content or "").strip():
        raise NoAnswerContent(
            "grounding publication gate produced no publishable bytes for a current-information turn"
        )
    # AUTHOR-ELIGIBILITY GATE. The grounding gate above asks whether the answer is supported by
    # what the turn retrieved, and it asks it only of turns M1 marked current-information. This
    # one asks a different question, of every turn: was the model that WROTE these bytes
    # certified to take the final-answer role at all? DIRECT turns have no lifecycle row, so
    # they never reached the gate above -- which is exactly how an uncertified local model came
    # to author open-domain answers with nothing behind them.
    #
    # Here, and beside the grounding gate, for the identical reason: `finalize_answer` is the one
    # authority every semantic answer traverses, through all six transport doors, so a check
    # anywhere else certifies one door and leaves five open -- and a retry is just another
    # traversal, which is why a retry cannot walk past it either.
    #
    # Jurisdiction is narrow by construction and the module documents it: only bytes a model
    # actually served, with nothing the runtime minted behind them, decided by an authority that
    # returned INELIGIBLE. A deterministic answer, a tool-backed answer and a certified author
    # all fall outside it and are returned unchanged.
    from core.final_answer_authorship import gate_authored_content

    try:
        content, _authorship_record = gate_authored_content(content, turn_id=turn_id)
    except Exception:  # pragma: no cover - reading the decision must not end a working turn
        _log.exception("final-answer authorship gate failed; publishing ungated")
        _authorship_record = {}
    if not str(content or "").strip():
        raise NoAnswerContent(
            "final-answer authorship gate produced no publishable bytes"
        )
    # EXECUTION CLAIMS. Model-authored bytes that present a tool run this turn's ledgers do not
    # hold are withheld here, at the same boundary, by the same authorship authority: a claim to
    # have executed something is a claim about the runtime, and only the runtime's own records
    # can back it. Fail-open on an internal error, like the two gates above; it can only narrow.
    from core.final_answer_authorship import gate_execution_claims
    try:
        # `source_context` was discarded above (reserved-key law); the retained copy carries the
        # turn's ledger stamp, which is what the ledger lookup keys on.
        content, _execution_record = gate_execution_claims(
            content, turn_id=turn_id, source_context=_selection_context
        )
    except Exception:  # pragma: no cover - the check must not end a working turn
        _log.exception("execution-claim gate failed; publishing ungated")
        _execution_record = {}
    if _execution_record:
        _authorship_record = {**dict(_authorship_record or {}), "execution_claims": _execution_record}
    if not str(content or "").strip():
        raise NoAnswerContent(
            "execution-claim gate produced no publishable bytes: the reply presented a tool run "
            "this turn did not perform and nothing else in it survived"
        )
    # F45 ENFORCEMENT — unresolved entity ambiguity withholds introduced precise numerics.
    # THE CONSUMER: this seam. The probe's replied-without-verdict state was previously only
    # RECORDED on the context (a fact, not a fence) and the ordinary answer published as if
    # the question had been adjudicated unambiguous — the exact silence the contract forbids.
    # Enforced here, at the one authority every served answer traverses: when the turn's
    # adjudication is unresolved and the bytes assert precise numbers the REQUEST did not
    # carry, those claims are withheld with an honest notice naming the incomplete check.
    # Everything else publishes: a non-numeric answer ("Paris") ships unchanged, and turns
    # without the state field (every other path) are untouched.
    try:
        from core.claim_support import _extract_numerics

        _adj = (
            _selection_context.get("ambiguity_adjudication")
            if isinstance(_selection_context, dict)
            else None
        )
        if isinstance(_adj, dict) and str(_adj.get("state") or "") == "unresolved_replied_without_verdict":
            _request_text = ""
            for _key in ("turn_request", "user_input", "effective_input", "request_text"):
                _value = _selection_context.get(_key)
                if isinstance(_value, str) and _value.strip():
                    _request_text = _value
                    break
            _introduced = tuple(
                value
                for value in sorted(_extract_numerics(str(content or "")))
                if value not in _extract_numerics(_request_text)
            )
            if _introduced:
                from core.grounding_publication import _segments_by_line, _trim_unsupported_spans

                _kept: list[str] = []
                _withheld_lines: list[str] = []
                for _line, _segments in _segments_by_line(str(content or "")):
                    _line_introduced = [
                        seg
                        for seg in _segments
                        # separator-insensitive: the extractor normalizes "250,000" to
                        # "250000", so the raw segment never contains the normalized form
                        if any(num in seg.replace(",", "") for num in _introduced)
                    ]
                    if not _line_introduced:
                        _kept.append(_line)
                        continue
                    _trimmed = _trim_unsupported_spans(_line, _line_introduced)
                    if _trimmed and _trimmed.strip():
                        _kept.append(_trimmed)
                    else:
                        _withheld_lines.append(_line.strip())
                _notice = (
                    "Withheld from this answer: exact figures I could not stand behind, because "
                    "my check for whether this question refers to one specific place, person or "
                    "thing did not complete. Confirm which one you mean and I will answer "
                    "precisely."
                )
                _body = "\n".join(_kept).strip()
                if _body:
                    content = f"{_body}\n\n{_notice}"
                else:
                    content = (
                        "I couldn't complete my check for whether this question has a single "
                        "well-known answer, and the reply I drafted rested on exact figures I "
                        "can't stand behind without it. Tell me which place, person or thing you "
                        "mean, and I'll answer precisely."
                    )
    except Exception:  # pragma: no cover — enforcement may not kill a working answer
        _log.exception("ambiguity-adjudication enforcement failed; publishing ungated")
    # RSS: account for every requested slot BEFORE the coverage check, and render
    # what no lane answered. Must precede the hash — the swept bytes are the bytes
    # the commit covers and the bytes transport serves.
    _withheld = tuple(
        str(c)
        for c in (dict(_grounding_record.get("publication") or {}).get("withheld_claims") or ())
        if str(c or "").strip()
    )
    from core.grounding_lifecycle import REASON_RE_PRESENTATION
    _withheld_reason = (
        RSS_REASON_WITHHELD_RE_PRESENTATION
        if REASON_RE_PRESENTATION in tuple(_grounding_record.get("reason_codes") or ())
        else RSS_REASON_WITHHELD
    )
    _publication = dict(_grounding_record.get("publication") or {})
    content, _rss_census = _rss_closure_sweep(
        content,
        closure,
        _withheld,
        withheld_reason=_withheld_reason,
        # Supported statements shipped beside the withheld ones: the slot was answered in part.
        published_in_part=bool(_withheld) and int(_publication.get("supported_claim_count") or 0) > 0,
    )
    # PRESENTATION RENDER LANE — the recovered renderer family (recovery vault
    # family `presentation-render`), live at the one door every answer crosses.
    # A table shape the C19 authority elected from the bytes themselves is
    # re-formed through the vault renderer: cells verbatim, UNKNOWN preserved,
    # round-trip guarded, everything else byte-identical. ABOVE the conservation
    # verdict on purpose: the sweep must judge the RENDERED bytes, so a renderer
    # that dropped a slot cannot ship unnoticed. Fail-open like the gates above —
    # the lane can only normalize, so a failure must not end a working turn.
    from core.presentation.answer_render import normalize_structured_shape

    try:
        content, _render_lane_record = normalize_structured_shape(content, _selection_context)
    except Exception:  # pragma: no cover - the lane must not be able to end a working turn
        _log.exception("presentation render lane failed; publishing unrendered")
        _render_lane_record = {}
    # KEYSTONE: slot + publication conservation over the door's RequestGraph, from the ledger's own
    # terminal rows. Recorded as a ledger event every turn; refuses only under the enforce flag.
    # `source_context` was discarded above (reserved-key law); the retained copy carries the
    # session/turn keys the ledger event funnel reads.
    _conservation = _semantic_conservation_verdict(closure, content, _selection_context)
    if (
        _conservation is not None
        and _conservation.get("enforced")
        and _conservation.get("status") == "evaluated"
    ):
        _slot_ok = bool(_conservation["slot_conservation"]["conserved"])
        _pub_ok = bool(_conservation["publication_conservation"]["conserved"])
        if not (_slot_ok and _pub_ok):
            raise FinalizationRejected(
                "",
                "SLOT_CONSERVATION:" + ("slots" if not _slot_ok else "publication") + f":{_conservation['set_version']}",
                "",
            )
    # C19 PRESENTATION SELECTION — post-gate re-stamp. The router's pre-gate
    # record rides ``display_metadata``/turn context and describes bytes the
    # gates above could still have replaced; the shipped record is re-derived
    # from the bytes that actually commit, so it can never describe bytes that
    # did not ship. On a REFUSED turn the shipped truth is the refusal.
    _grounding_refused = str(
        dict(_grounding_record.get("publication") or {}).get("state") or ""
    ) == "refused"
    _presentation_selection = _restamp_presentation_selection(
        content,
        _selection_context,
        display_metadata,
        _grounding_refused,
        turn_id,
    )
    # F-07: the durable ledger is authoritative; caller dict advisory only.
    verdict = _authoritative_closure_verdict(closure)
    if not isinstance(verdict, dict) or not {
        "covered",
        "open_count",
        "set_version",
    } <= set(verdict):
        raise FinalizationRejected(
            "", "MALFORMED_CLOSURE_VERDICT", ""
        )
    # STRUCTURAL terminality decides whether this turn may finalize at all, and it is
    # read from the LEDGER, before RSS says anything about truth. The sweep above has
    # already driven every demand slot terminal, so RSS can never be why a turn dies
    # holding a computed answer — fail-VISIBLE, never fail-dead.
    if not verdict["covered"]:
        raise FinalizationRejected(
            "",
            f"OBLIGATIONS_OPEN:{int(verdict['open_count'])}:{verdict['set_version']}",
            "",
        )
    if _rss_census:
        # Property 4: minted cardinality rides beside open_count, so a vacuous
        # one-slot closure is distinguishable by inspection from a complete
        # four-slot turn. Added only when the turn actually minted demand, so a
        # turn with no bound set keeps a byte-identical certificate.
        verdict = {**verdict, **_rss_census}
        if (
            int(_rss_census.get("demand_unanswered") or 0) > 0
            or int(_rss_census.get("demand_indeterminate") or 0) > 0
        ):
            # `covered` is a claim about the REQUEST, and it is made only when EVERY
            # minted slot carries positive evidence of an answer. Measured live: a turn
            # served with 1 of 4 slots certified `covered: true, demand_unanswered: 3`
            # (a certificate contradicting its own census), and a wrong-location line
            # for one slot discharged ANOTHER slot through the shared tokens
            # `united`/`states` and restored `covered: true` over a slot nobody answered
            # (RED-1 NEW-4). An indeterminate slot therefore also blocks the claim:
            # coverage is asserted from evidence, never from the absence of a denial.
            # Structural terminality (open_count == 0) is what permits finalization and
            # is unchanged; this field tells the truth about the answer, not the ledger.
            verdict["covered"] = False
    # LAW 2: consume the A2 id verbatim — never derive it from bytes, ids below.
    semantic_result_id = current_semantic_result_id()
    if _turn_cancel_marker_fired(_cancel_context):
        # The operator cancelled this turn and the cancel was acknowledged. Whatever the lanes
        # composed afterwards is not published: the committed bytes are the typed cancel notice --
        # decided AFTER every gate and the closure sweep so nothing rewrites it -- and the typed
        # `task_cancelled` reaches the page. Measured 2026-09-06 on a scratch daemon: a cancel
        # acknowledged mid-retrieval was followed by every remaining facet query, a model call and
        # a published comparison -- an orphan answer under a cancelled turn.
        content = CANCELLED_BEFORE_PUBLICATION
        if isinstance(_cancel_context, dict):
            _cancel_context["_cancelled_before_publication"] = True
        try:
            from core.runtime_task_events import emit_runtime_event

            emit_runtime_event(
                _cancel_context,
                event_type="task_cancelled",
                message="Turn cancelled by the operator before publication; no answer was published.",
                details={"cancelled_at": "finalization"},
            )
        except Exception:
            pass
    content_hash = _sha256_hex(content)
    clean_turn_id = str(turn_id or "").strip()
    # K-09: finalization identity derives from OPAQUE inputs
    # (semantic_result_id, request_id, nonce) — NEVER from the raw content
    # hash. A one-word answer must not become a permanent confirmation oracle,
    # and rekeying must stay possible after scale.
    try:
        from core.semantic.semantic_admissions import current_request_id

        bound_request_id = current_request_id()
    except Exception:
        bound_request_id = ""
    import secrets as _secrets

    finalization_nonce = _secrets.token_hex(8)
    finalization_id = "fc:" + hashlib.sha256(
        "\x1f".join((semantic_result_id, bound_request_id, finalization_nonce)).encode("utf-8")
    ).hexdigest()[:32]
    # M2 SLICE 4 -- the typed terminal truth. The values the commit assembled
    # from scattered locals are computed INTO the TurnResult first; the commit's
    # own result-carried fields are then PROJECTED from it. The buckets follow
    # the ledger census the verdict already carries (no new taxonomy -- M6's).
    # R2 — the turn's effect account travels WITH its terminal truth. The slot
    # existed and no writer ever filled it, so a turn that was denied at the
    # network door committed a terminal record saying nothing about the denial.
    # Read from the turn's own ledger, which is open here: finalization runs
    # inside the turn scope that owns it.
    from core.effect_gateway import effect_outcomes as _turn_effect_outcomes
    from core.effect_gateway import effect_receipts as _turn_effect_receipts
    from core.effect_gateway import effect_receipts_dropped as _turn_effects_dropped
    from core.effect_gateway import effect_receipts_truncated as _turn_effects_truncated
    from core.turn_contract import TurnResult

    turn_result = TurnResult(
        turn_id=clean_turn_id,
        request_id=bound_request_id,
        finalization_id=finalization_id,
        terminal_state=status,
        committed_answer=content,
        final_content_hash=content_hash,
        fulfilled_obligations=int(_rss_census.get("demand_satisfied") or 0),
        unresolved_obligations=int(_rss_census.get("demand_indeterminate") or 0),
        refused_obligations=int(_rss_census.get("demand_unanswered") or 0),
        effect_receipts=_turn_effect_receipts(),
        effect_receipts_truncated=_turn_effects_truncated(),
        effect_receipts_dropped=_turn_effects_dropped(),
        # R2b2b — the terminal lifecycle travels with the turn's truth: the
        # per-effect outcome account, so a commit answers what was attempted,
        # whether transport ran, and how it ended.
        effect_outcomes=_turn_effect_outcomes(),
        # ROOT-CAUSE CONTRACT — derived from the validated typed record above;
        # empty means no diagnosis was open (a fact, never a repair claim).
        root_cause_state=root_cause_state,
        root_cause_status=root_cause_status,
        # FAULT PLANE — the turn's retry hint, derived from the faults its boundaries
        # filed. Empty for a turn with no faults, so ordinary commits stay byte-identical.
        retry_classification=retry_classification_for_turn(clean_turn_id),
    )
    _commit_display_metadata = dict(display_metadata or {})
    if isinstance(_presentation_selection, dict):
        _commit_display_metadata["presentation_selection"] = _presentation_selection
    if isinstance(_render_lane_record, dict) and _render_lane_record:
        # Provenance of the render lane: what it normalized, or why it declined.
        # Same surface law as the selection record — metadata, never answer bytes.
        _commit_display_metadata["render_lane"] = dict(_render_lane_record)
    if _execution_record:
        # Provenance of the execution-claim check: what it withheld, or why it stood down.
        _commit_display_metadata["execution_claims"] = dict(_execution_record)
    commit = {
        "type": "response.commit",
        "version": 2,
        "revision": 1,
        "finalization_id": finalization_id,
        "turn_id": clean_turn_id,
        "semantic_result_id": semantic_result_id,
        "request_id": bound_request_id,
        **turn_result.to_commit_fields(),
        "closure_verdict": dict(verdict),
        # C19: the shipped selection record describes the committed (post-gate)
        # bytes — provenance only, never part of the answer bytes themselves.
        "display_metadata": _commit_display_metadata,
        # Requirement 11: the lifecycle rides the commit, so the API and Activity can show
        # required / retrieved / bound / supported / published — or name the exact stage that
        # failed — without re-deriving any of it. Absent for turns that never needed current
        # information, which is how a reader tells "not applicable" from "not reached".
        **({"grounding_lifecycle": _grounding_record} if _grounding_record else {}),
        # The egress law: the commit envelope is an ingress/egress adapter, so the
        # result rides as its DICT projection (JSON-safe — found live: the typed
        # object broke the serve path's serialization with a 500). Reconstruct with
        # TurnResult(**commit["turn_result"]); the typed object remains the
        # computed-once home inside the seam.
        "turn_result": turn_result.to_dict(),
    }
    if root_cause_publication is not None:
        # Added ONLY for turns that carried a diagnosis contract, so ordinary
        # commits stay byte-identical (frozen tests pin their shape).
        commit["root_cause"] = dict(root_cause_publication)
    outcome = _bind_durably(commit)
    commit["binding_outcome"] = outcome
    # TRANSCRIPT AT THE COMMIT BOUNDARY. The lane that produced this answer staged its
    # conversation row instead of writing a pre-gate draft (see core.persistent_memory); the
    # row is written HERE, once, with the exact bytes this commit certifies -- so the transcript,
    # the wire and response.commit can never disagree, and no downstream memory writer ever
    # mines text the gates refused. Best-effort by construction: a transcript store fault must
    # not unseal an answer that is already durably bound.
    if bound_request_id and status == ANSWER_PRESENT:
        try:
            from core.persistent_memory import persist_staged_conversation_event

            persist_staged_conversation_event(
                request_id=bound_request_id,
                committed_text=content,
                extra_fields={"finalization_id": finalization_id, "content_hash": content_hash},
            )
        except Exception:
            _log.exception("staged transcript row was not persisted at the commit boundary")
    return commit


def no_answer_terminal(
    *,
    turn_id: str,
    reason_code: str,
    detail: str = "",
    closure: dict[str, Any] | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """Typed NO_ANSWER_TERMINAL truth: no semantic answer exists for the turn.

    D11 strict-empty policy (written law, not convention):
    - provider-no-content after a valid request ⇒
      NO_ANSWER_TERMINAL(reason='provider_no_content');
    - a valid EMPTY answer exists ONLY under an explicit strict-output
      obligation positively identifying declared-empty bytes;
    - refusal enters as GATED disposition via SemanticSource.REFUSAL_POLICY.
    A blank done-only stream is therefore never generic SUCCESS merely because
    transport ended cleanly.

    K-05/K-10: the terminal carries the closure verdict and, when `persist`
    (default), is durably bound as an a7_finalizations row — NO_ANSWER_COMMITTED
    is durable typed truth, never ephemeral prose.
    """
    # Pass-002 parity: a persisted NO_ANSWER terminal is an A7 finality write,
    # so it presents the same guards as ANSWER_PRESENT binding — FENCE-OR-
    # REFUSE (stale generation/epoch refused with zero rows) and REFERENT-
    # BEFORE-REFERENCE (an onboarded lane may not mint a durable terminal
    # without an admitted sr citation). A non-persisted terminal writes no
    # rows, so no fence presentation is required.
    semantic_result_id = current_semantic_result_id()
    verdict = _authoritative_closure_verdict(closure)
    if persist:
        from core.semantic.semantic_admissions import (
            current_execution_identity,
            enforce_fence_if_onboarded,
        )

        enforce_fence_if_onboarded()
        cited_sr = semantic_result_id.strip()
        if cited_sr:
            from core.semantic.semantic_admissions import admission_exists

            if not admission_exists(cited_sr):
                raise FinalizationRejected(
                    cited_sr,
                    "NO_ADMISSION_ROW",
                    "",
                )
        if not cited_sr and current_execution_identity():
            raise FinalizationRejected("", "EMPTY_SR_ONBOARDED_LANE", "")
    record = {
        "type": "no_answer.terminal",
        "version": 1,
        "turn_id": str(turn_id or "").strip(),
        "semantic_result_id": semantic_result_id,
        "status": NO_ANSWER_TERMINAL,
        "reason_code": str(reason_code or "unspecified"),
        "detail": str(detail or ""),
        "closure_verdict": verdict,
    }
    # The turn ends with no semantic answer: the staged transcript row (if the lane staged one)
    # is written with an EMPTY assistant text and the typed reason, never with the draft that
    # was refused -- the transcript says what the wire said.
    try:
        from core.persistent_memory import TRANSCRIPT_COMMIT_STATE_NO_ANSWER, flush_staged_conversation_events
        from core.semantic.semantic_admissions import current_request_id as _na_request_id

        _na_bound = str(_na_request_id() or "")
        if _na_bound:
            flush_staged_conversation_events(
                _na_bound,
                commit_state=TRANSCRIPT_COMMIT_STATE_NO_ANSWER,
                assistant_text="",
            )
    except Exception:
        _log.exception("staged transcript row was not flushed at the no-answer terminal")
    if persist:
        conn = get_connection()
        try:
            finalization_id = "fc:" + hashlib.sha256(
                "\x1f".join(
                    (
                        record["semantic_result_id"],
                        str(record.get("closure_verdict", {}).get("set_version") or ""),
                        "no-answer",
                        record["reason_code"],
                        _utcnow(),
                    )
                ).encode("utf-8")
            ).hexdigest()[:32]
            conn.execute(
                """
                INSERT INTO a7_finalizations (
                    finalization_id, semantic_result_id, turn_id, content_hash,
                    canonical_content, status, request_id, payload_ref,
                    availability, terminal_reason
                ) VALUES (?, ?, ?, '', '', ?, ?, NULL, 'AVAILABLE', ?)
                """,
                (
                    finalization_id,
                    record["semantic_result_id"],
                    record["turn_id"],
                    NO_ANSWER_TERMINAL,
                    "",
                    str(record["reason_code"]),
                ),
            )
            conn.commit()
            record["finalization_id"] = finalization_id
        except sqlite3.IntegrityError:
            conn.rollback()
        finally:
            conn.close()
    return record


def get_finalization_by_request_id(
    request_id: str, *, principal: str = ""
) -> dict[str, Any] | None:
    """TRUE_REPLAY read: the committed truth for an already-accepted request.

    Availability-aware: erased/withheld truth replays as UNAVAILABLE_BY_POLICY
    without bytes (never regenerated). Principal-scoped (A8 fail-closed law):
    a replay read carries the authenticated principal; absent or unrecognized
    principals are refused — the principal vocabulary is owned by the
    invocation ledger, never re-invented here."""
    from core.invocation.ledger import validate_principal

    validate_principal(principal)
    clean = str(request_id or "").strip()
    if not clean:
        return None
    # PASS-002 principal binding: A0 request truth owns WHO accepted this
    # request id. If an invocation record exists, only its own frozen
    # principal may replay it — AVAILABLE alone authorizes nothing, a
    # different recognized principal is refused, and an unresolvable
    # required owner fails closed.
    from core.invocation.ledger import get_invocation

    invocation = get_invocation(clean)
    if invocation is not None:
        owner = str(invocation.get("principal") or "").strip()
        requested = str(principal or "").strip()
        if not owner or requested != owner:
            from core.invocation.ledger import PrincipalDenied

            raise PrincipalDenied(
                f"replay denied: request {clean[:64]!r} belongs to a different "
                f"principal; requested={requested[:60]!r}"
            )
    conn = get_connection()
    try:
        # PASS003 (CE13): ambiguity is REFUSED, never resolved by SQL sort
        # internals. More than one immutable finalization row sharing this
        # request id means the identity cannot be resolved to one
        # authoritative truth — fail closed (typed refusal), because any
        # arbitrary newest/oldest pick can serve substituted bytes as owner-
        # committed truth. Canonical uniqueness (migration index) prevents
        # new duplicates; legacy duplicates are quarantined here.
        dup = conn.execute(
            "SELECT COUNT(*) AS n FROM a7_finalizations WHERE request_id = ? AND status = ?",
            (clean, ANSWER_PRESENT),
        ).fetchone()
        if dup is not None and int(dup["n"]) > 1:
            raise ReplayAmbiguityRefused(
                f"replay refused: request {clean[:64]!r} is ambiguous "
                f"({int(dup['n'])} finalization rows); no authoritative truth "
                "can be selected"
            )
        row = conn.execute(
            "SELECT * FROM a7_finalizations WHERE request_id = ? AND status = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (clean, ANSWER_PRESENT),
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        availability = str(result.get("availability") or AVAILABILITY_AVAILABLE)
        if availability in (AVAILABILITY_WITHHELD, AVAILABILITY_ERASED):
            result["canonical_content"] = ""
            result["replay_outcome"] = REPLAY_UNAVAILABLE_BY_POLICY
        return result
    finally:
        conn.close()


def get_finalization_by_semantic_id(semantic_result_id: str) -> dict[str, Any] | None:
    """TRUE_REPLAY read path: existing immutable truth keyed by admitted identity.

    A8 read-gate law: WITHHELD/ERASED rows never expose their bytes through
    this raw reader — canonical_content is suppressed (blanked) whenever the
    payload is unavailable; identity/label fields remain for governance reads.
    """
    clean = str(semantic_result_id or "").strip()
    if not clean:
        return None
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM a7_finalizations WHERE semantic_result_id = ?",
            (clean,),
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        if str(result.get("availability") or "") in _UNSERVEABLE_AVAILABILITY:
            result["canonical_content"] = ""
        return result
    finally:
        conn.close()


def get_finalization_by_content(text: str) -> dict[str, Any] | None:
    """Content-addressed read: does this exact text match committed A7 truth?

    Used by replay/history surfaces to distinguish verified canonical bytes from
    LEGACY_UNVERIFIED rows without fabricating identity for old history. A8
    read-gate law: WITHHELD/ERASED rows never expose their bytes through this
    raw reader (canonical_content suppressed; the label stays available).
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM a7_finalizations WHERE content_hash = ? LIMIT 1",
            (_sha256_hex(text),),
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        if str(result.get("availability") or "") in _UNSERVEABLE_AVAILABILITY:
            result["canonical_content"] = ""
        return result
    finally:
        conn.close()


_AVAILABILITY_READ_SCOPE = contextvars.ContextVar("availability_read_scope", default=None)


@contextlib.contextmanager
def availability_read_scope():
    """Reuse a connection only during one synchronous projection operation.

    Verdicts are never cached and no read transaction is held: each check sees
    current committed governance. The connection closes before the response
    leaves this scope, preserving storage's no-idle-connection contract.
    """
    from storage.db import active_default_db_path

    path = active_default_db_path()
    outer = _AVAILABILITY_READ_SCOPE.get()
    if outer is not None and outer[0] == path:
        # Re-entrant: a listing called from inside a wider projection shares that projection's
        # connection and leaves closing it to the scope that opened it.
        yield
        return
    state = [path, None]
    token = _AVAILABILITY_READ_SCOPE.set(state)
    try:
        yield
    finally:
        _AVAILABILITY_READ_SCOPE.reset(token)
        if state[1] is not None:
            state[1].close()


@contextlib.contextmanager
def _availability_connection():
    from storage.db import active_default_db_path

    state = _AVAILABILITY_READ_SCOPE.get()
    if state is None or state[0] != active_default_db_path():
        with contextlib.closing(get_connection()) as conn:
            yield conn
    else:
        if state[1] is None:
            state[1] = get_connection()
        yield state[1]


def payload_availability_for_hash(content_hash: str) -> str | None:
    """A8 serve-time availability verdict keyed by payload content hash.

    The ONE read-gate helper for surfaces that hold payload-derived text or an
    unsalted digest of it (history, receipts, checkpoints, mirror records):

    - an a7 row still carrying this content hash reports its availability;
    - otherwise, an erasure digest tombstone proves the payload was ERASED
      (the row's own hash is salted at ERASE, and the tombstone reason is a
      KEYED digest, so neither can be used as a plaintext confirmation
      oracle);
    - None means no governed truth is known for these bytes (honest legacy).

    Fail-closed on ambiguity is the caller's law; this helper only reports
    truth. Reads re-verify per call — no read-once-trust.
    """
    clean = str(content_hash or "").strip()
    if not clean:
        return None
    with _availability_connection() as conn:
        _migrate_legacy_digest_tombstones(conn)
        # CONVERGENCE 2026-09-02 (F18 fail-open): `LIMIT 1` picked ONE of the rows carrying
        # these bytes, and with no ORDER BY that is whichever row the store happens to hand
        # back -- usually the oldest. Byte-identical payloads DO get admitted more than once
        # (two turns, two finalizations, the same canonical text), and when one of them was
        # withheld while a sibling stayed AVAILABLE, the arbitrary pick disclosed the withheld
        # bytes. Whether it disclosed them depended on row order, which is why it surfaced as
        # an intermittent leak rather than a stable one.
        #
        # The dominance rule this function already applies to bound derivatives is the same
        # rule these siblings need, and it is the rule the module docstring states: ERASED >
        # WITHHELD > anything else. Resolve across EVERY row holding this hash, most
        # restrictive wins. Ambiguous ownership never resolves into disclosure.
        sibling_verdicts = {
            str(candidate["availability"] or "")
            for candidate in conn.execute(
                "SELECT availability FROM a7_finalizations WHERE content_hash = ?",
                (clean,),
            ).fetchall()
        }
        row: dict[str, str] | None = None
        if AVAILABILITY_ERASED in sibling_verdicts:
            row = {"availability": AVAILABILITY_ERASED}
        elif AVAILABILITY_WITHHELD in sibling_verdicts:
            row = {"availability": AVAILABILITY_WITHHELD}
        elif sibling_verdicts:
            row = {"availability": sorted(sibling_verdicts)[0]}
        # A9 RC-8 pass-003 (F1/F8 dominance): a live AVAILABLE row sharing this
        # key must NOT outvote a governed binding — differently-governed
        # sources can produce byte-identical canonical derivatives, and the
        # fail-closed direction is to withhold. Ambiguous ownership never
        # resolves into disclosure.
        if (
            row is not None
            and str(row["availability"] or "") not in _UNSERVEABLE_AVAILABILITY
        ):
            dominated = conn.execute(
                """
                SELECT f.availability AS availability
                FROM a8_governed_derivatives d
                JOIN a7_finalizations f ON f.finalization_id = d.finalization_id
                WHERE d.value_key = ?
                  AND f.availability IN (?, ?)
                LIMIT 1
                """,
                (clean, *_UNSERVEABLE_AVAILABILITY),
            ).fetchone()
            if dominated is not None:
                return str(dominated["availability"] or "")
        if row is not None:
            return str(row["availability"] or "") or None
        tomb = conn.execute(
            "SELECT 1 FROM a7_governance_events "
            "WHERE event_kind = ? AND reason IN (?, ?) LIMIT 1",
            (
                EVENT_KIND_ERASURE_DIGEST_TOMBSTONE,
                clean,
                _keyed_tombstone_value(clean),
            ),
        ).fetchone()
        if tomb is not None:
            return AVAILABILITY_ERASED
        # A8 PASS004 (NCE-F1/F2): governed derivative lineage. A bound fragment
        # resolves to its governing finalization's LIVE availability — ownership
        # is read from registry identity, never re-derived from text or hash
        # guesses. A registry hit whose governing row cannot resolve fails
        # closed (ERASED), never discloses.
        reg = conn.execute(
            "SELECT finalization_id FROM a8_governed_derivatives "
            "WHERE value_key = ? LIMIT 1",
            (clean,),
        ).fetchone()
        if reg is not None:
            grow = conn.execute(
                "SELECT availability FROM a7_finalizations "
                "WHERE finalization_id = ? LIMIT 1",
                (str(reg["finalization_id"]),),
            ).fetchone()
            resolved = str(grow["availability"] or "").strip() if grow else ""
            return resolved if resolved in _UNSERVEABLE_AVAILABILITY + (
                AVAILABILITY_AVAILABLE,
                AVAILABILITY_LEGACY_UNKNOWN,
            ) else AVAILABILITY_ERASED
        return None


# ---------------------------------------------------------------------------
# A8 pass-002 strict digest law: the erasure tombstone stores a KEYED digest
# (HMAC-SHA256 under a server-local A8 key), never the pre-erase unsalted
# sha256. An attacker with the old ledger could confirm low-entropy guesses
# offline; with the key held outside every read API, guess->confirm is dead
# while deterministic tombstone lookup is preserved.
# ---------------------------------------------------------------------------

_a8_digest_key_cache: bytes | None = None
_a8_store_ready_cache: bool | None = None
# The per-process caches above hold facts about ONE governance database. They are
# keyed to the active default DB path so a runtime-home/test-home switch cannot
# serve a verdict (or key material) proven under a different store: a READY
# verdict replayed against a fresh home queries a missing a7_finalizations table
# and the write fence fail-closes every public surface.
_governance_cache_db_path: str | None = None


def _governance_cache_stale() -> bool:
    """True when the cached verdict/key were proven under a different store."""
    from storage.db import active_default_db_path

    return _governance_cache_db_path != active_default_db_path()

GOVERNANCE_STORE_READY = "READY"
GOVERNANCE_STORE_ABSENT = "ABSENT"
GOVERNANCE_STORE_UNAVAILABLE = "UNAVAILABLE"


def governance_store_state() -> str:
    """Tri-state readiness of the hosted A8/A7 governance store.

    - READY: a completed probe positively proved the a7 governance tables
      exist (this deployment hosts A8 truth).
    - ABSENT: a completed probe positively proved no a7 governance tables
      exist (honest ungoverned legacy reality).
    - UNAVAILABLE: the probe ITSELF failed — readiness cannot currently be
      determined. An outage is never evidence that governance does not apply,
      so privacy callers treat this state fail-CLOSED.

    Only the two PROVEN states are cached, and only while the active default
    database is the one they were proven against (a runtime-home switch re-probes
    rather than replaying another store's verdict). UNAVAILABLE is
    never cached: a transient failure must not poison readiness into a durable
    permissive verdict, and the next call re-probes so recovery stays possible.

    The probe reads sqlite_master instead of the governed table so that
    "table absent" is a successful query result, not an exception — the old
    single-query probe could not tell a missing table from a dead database,
    which is exactly the fail-open the A9 PASS-003 confirmation caught."""
    global _a8_store_ready_cache, _governance_cache_db_path
    if _governance_cache_stale():
        _a8_store_ready_cache = None
        _governance_cache_db_path = None
    if _a8_store_ready_cache is True:
        return GOVERNANCE_STORE_READY
    if _a8_store_ready_cache is False:
        return GOVERNANCE_STORE_ABSENT
    try:
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name = 'a7_finalizations' LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
    except Exception:
        return GOVERNANCE_STORE_UNAVAILABLE
    _a8_store_ready_cache = row is not None
    from storage.db import active_default_db_path as _adp

    _governance_cache_db_path = _adp()
    return GOVERNANCE_STORE_READY if row is not None else GOVERNANCE_STORE_ABSENT


def governance_store_ready() -> bool:
    """Fail-closed boolean view of :func:`governance_store_state` for privacy
    gates: True whenever governed disclosure must be adjudicated (READY) or
    cannot be ruled out (UNAVAILABLE). False ONLY on a positively proven
    ABSENT store — the one honest-legacy case where reads resolve ungoverned."""
    return governance_store_state() != GOVERNANCE_STORE_ABSENT


def reset_governance_readiness_for_tests() -> None:
    global _a8_store_ready_cache, _a8_digest_key_cache, _governance_cache_db_path
    _a8_store_ready_cache = None
    _a8_digest_key_cache = None
    _governance_cache_db_path = None


def verdict_or_unavailable(text: str) -> str | None:
    """Availability verdict with outage discrimination: on a live store,
    exceptions propagate to the caller (fail-closed territory); with a store
    positively proven ABSENT, reads resolve as ungoverned legacy (None). A
    store whose readiness CANNOT be determined is treated like a live one —
    the lookup is attempted and its failure propagates, never resolving an
    outage into an ungoverned verdict."""
    if governance_store_state() == GOVERNANCE_STORE_ABSENT:
        return None
    return payload_availability_for_text(text)


def writer_may_publish_public_text(text: str) -> bool:
    """A8 write fence for PUBLIC content surfaces (hive posts/topics, task
    briefs/results, VoolBook posts): exact-hash governance PLUS the
    fragment-aware derived-copy law against every WITHHELD payload's retained
    plaintext — a NOVEL quotation written after WITHHOLD is refused, not just
    byte-equal copies. ERASED rows retain no plaintext by law; their exact
    and tombstone digests carry them (retaining plaintext to catch novel
    post-erase quotes would violate erasure itself). Store ABSENT → ungoverned
    legacy (publish); hosted-store failure fails closed (refuse)."""
    if not writer_may_persist_text(text):
        return False
    value = str(text or "")
    if not value.strip():
        return True
    try:
        if governance_store_state() == GOVERNANCE_STORE_ABSENT:
            return True
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT content_hash, canonical_content FROM a7_finalizations "
                "WHERE availability = ?",
                (AVAILABILITY_WITHHELD,),
            ).fetchall()
        finally:
            conn.close()
        for row in rows:
            if _text_matches_erasure(
                value,
                str(row["content_hash"] or ""),
                str(row["canonical_content"] or ""),
                min_fragment=DERIVATIVE_FRAGMENT_MIN_RUN,
            ):
                return False
        return True
    except Exception:
        return False


def writer_may_persist_text(text: str) -> bool:
    """ERASE-dominance primitive for derivative writers (mirror publisher,
    checkpoint writer, runtime events, sync): before durable persistence of
    governed payload bytes, consult canonical availability. WITHHELD/ERASED
    verdicts veto the write; absence of the governance store entirely means
    no A8 truth is hosted here (legacy). Store FAILURE on a live store fails
    closed (the write is refused — never resurrect erased bytes)."""
    try:
        verdict = verdict_or_unavailable(text)
    except Exception:
        return False
    return verdict not in (AVAILABILITY_WITHHELD, AVAILABILITY_ERASED)


def _a8_digest_key() -> bytes:
    global _a8_digest_key_cache, _governance_cache_db_path
    if _a8_digest_key_cache and not _governance_cache_stale():
        return _a8_digest_key_cache
    import secrets

    from core.runtime_paths import data_path

    path = data_path("a8_digest_key.hex")
    try:
        raw = path.read_text(encoding="utf-8").strip()
        key = bytes.fromhex(raw)
        if len(key) < 32:
            raise ValueError("stale short key")
    except Exception:
        key = secrets.token_bytes(32)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(key.hex(), encoding="utf-8")
        tmp.replace(path)
    _a8_digest_key_cache = key
    if _governance_cache_stale():
        from storage.db import active_default_db_path as _adp

        _governance_cache_db_path = _adp()
    return key


def _keyed_tombstone_value(pre_erase_hash: str) -> str:
    import hmac as _hmac

    return "keyed-sha256:" + _hmac.new(
        _a8_digest_key(), pre_erase_hash.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def _migrate_legacy_digest_tombstones(conn: sqlite3.Connection) -> None:
    """Rewrite pre-pass-002 tombstones that retained an UNsalted sha256 into
    the keyed form (co-delete of the confirmation oracle). Deterministic —
    lookup equivalence is preserved by construction."""
    rows = conn.execute(
        "SELECT event_id, reason FROM a7_governance_events "
        "WHERE event_kind = ? AND reason LIKE 'sha256:%'",
        (EVENT_KIND_ERASURE_DIGEST_TOMBSTONE,),
    ).fetchall()
    if not rows:
        return
    # THIS RUNS ON A READ PATH, and it WRITES. `payload_availability_for_hash` opens
    # its own connection, so when its caller already holds the WAL write lock -- which
    # `storage.useful_output_store` deliberately does, to make its veto and its write
    # atomic -- this UPDATE contends with that caller. SQLite does not exempt a second
    # connection in the same thread.
    #
    # Measured on an upgraded (pre-pass-002) database: 5.19 s of self-blocking, then
    # `OperationalError`, which `writer_may_persist_text` swallows via
    # `except Exception: return False`. The veto therefore REFUSED a legitimate
    # payload, and in `sync_useful_outputs` a refusal is a DELETE -- so a real
    # useful-output row was destroyed, the tombstone stayed unmigrated, and it
    # happened again on the next sync, forever.
    #
    # Best-effort now, and ONLY that. Correctness never depended on this rewrite: the
    # tombstone lookup matches BOTH the legacy and the keyed form. It closes a
    # confirmation oracle -- the unsalted pre-erase hash -- which is worth doing, and
    # is not worth failing a read for, and is certainly not worth deleting a row for.
    #
    # Deliberately NOT changed: the busy timeout, and where this is called from. A
    # short timeout here was tried and reverted -- it made the rewrite lose its race
    # under any contention and left the oracle open, which the F11 served freeze test
    # catches. Moving the call to the erase path was tried and reverted too: this
    # function commits, and committing inside `set_availability`'s guarded transaction
    # breaks its compare-and-set. What remains is the one change that fixes the data
    # loss without weakening anything: an uncontended read behaves exactly as before,
    # and a contended one leaves the rows alone instead of poisoning the verdict.
    # The residual cost under contention is latency, recorded, not a deleted row.
    try:
        for row in rows:
            conn.execute(
                "UPDATE a7_governance_events SET reason = ? WHERE event_id = ? AND reason = ?",
                (
                    _keyed_tombstone_value(str(row["reason"])),
                    str(row["event_id"]),
                    str(row["reason"]),
                ),
            )
        conn.commit()
    except Exception:
        # Leave the rows exactly as they are. The caller is a READ, and a failed
        # housekeeping rewrite must never become that read's answer.
        with contextlib.suppress(Exception):
            conn.rollback()


#: Single-process coordination between the ERASE traversal and late writers:
#: the sweep holds it across every store step so no in-flight writer can slip
#: a write between its eligibility check and its durable commit once ERASE has
#: won. Writers take it across their check+commit boundary. (Mirror publisher,
#: pass-002 TOCTOU closure.)
import threading as _threading

A8_TRAVERSAL_LOCK = _threading.RLock()


def text_is_unserveable_on(conn: sqlite3.Connection, text: str) -> bool:
    """Is this payload text unserveable, asked THROUGH the caller's own connection?

    The same question ``payload_availability_for_text`` answers, evaluated inside
    the caller's open transaction instead of on a second connection. That is the
    whole point: a writer holding the WAL write lock cannot ask the ordinary
    availability reader without opening a nested connection, and a nested
    connection contends with the very transaction that opened it.

    So the writer's guard reads on ITS OWN connection, and it reads the three
    durable sources whose verdicts are UNSERVEABLE:

    * a sibling finalization carrying these exact bytes that is WITHHELD/ERASED
      (the dominance rule: most restrictive wins across every row with this hash);
    * a governed derivative bound to a finalization that is WITHHELD/ERASED;
    * an erasure digest tombstone for this hash, legacy or keyed.

    CONSERVATIVE BY CONSTRUCTION. It is a guard, never a replacement: it may only
    ever refuse MORE than the full law, never less, so the caller keeps its own
    ordinary veto and this closes the window between that veto and the commit.
    Ambiguous ownership resolves to refusal, exactly as the full law does.
    """
    clean = _sha256_hex(str(text or ""))
    row = conn.execute(
        """
        SELECT 1 FROM a7_finalizations WHERE content_hash = ? AND availability IN (?, ?)
        UNION ALL
        SELECT 1 FROM a8_governed_derivatives d
          JOIN a7_finalizations f ON f.finalization_id = d.finalization_id
          WHERE d.value_key = ? AND f.availability IN (?, ?)
        UNION ALL
        SELECT 1 FROM a7_governance_events WHERE event_kind = ? AND reason IN (?, ?)
        LIMIT 1
        """,
        (
            clean, *_UNSERVEABLE_AVAILABILITY,
            clean, *_UNSERVEABLE_AVAILABILITY,
            EVENT_KIND_ERASURE_DIGEST_TOMBSTONE, clean, _keyed_tombstone_value(clean),
        ),
    ).fetchone()
    return row is not None


def payload_availability_for_text(text: str) -> str | None:
    """Serve-time availability verdict for payload-derived text (see
    payload_availability_for_hash)."""
    return payload_availability_for_hash(_sha256_hex(str(text or "")))


def payload_availability_for_request_id(request_id: str) -> str | None:
    """Serve-time availability verdict by REQUEST lineage: the most restrictive
    availability among every finalization the A0 request id governs
    (ERASED > WITHHELD > anything else). A derivative that carries the turn's
    request id (an operator-profile item learned from that turn) gates on this
    without holding a finalization id of its own. None when no governed row
    carries the id."""
    clean = str(request_id or "").strip()
    if not clean:
        return None
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT availability FROM a7_finalizations WHERE request_id = ?",
            (clean,),
        ).fetchall()
    finally:
        conn.close()
    verdicts = {str(row["availability"] or "") for row in rows}
    if AVAILABILITY_ERASED in verdicts:
        return AVAILABILITY_ERASED
    if AVAILABILITY_WITHHELD in verdicts:
        return AVAILABILITY_WITHHELD
    if verdicts:
        return sorted(verdicts)[0]
    return None


def payload_availability_for_finalization_id(finalization_id: str) -> str | None:
    """Serve-time availability verdict by finalization identity (lineage gate
    for stamped derivatives such as pins)."""
    clean = str(finalization_id or "").strip()
    if not clean:
        return None
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT availability FROM a7_finalizations WHERE finalization_id = ? LIMIT 1",
            (clean,),
        ).fetchone()
        return str(row["availability"] or "") if row is not None else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# A8 PASS004 (NCE-F1/F2): governed DERIVATIVE lineage. A streamed answer is
# stored as per-chunk fragments (denormalized runtime_sessions.last_message,
# event messages); a fragment's own sha256 matches no governed payload hash and
# its text contains no full payload, so neither the erasure blanket nor a
# whole-payload hash lookup can see it — WITHHELD/ERASED fragments served
# verbatim through /api/runtime/sessions. Repair: when a payload transitions
# unavailable, every surviving derivative VALUE in the A8 runtime stores is
# bound BY DIGEST to its governing finalization in the durable
# a8_governed_derivatives registry (canonical privacy lineage, never re-derived
# from fragment text); the serve/write gates then resolve a bound fragment to
# its governing row's LIVE availability.
# ---------------------------------------------------------------------------

#: Derived-copy law shared with CE04/NCE-D legs: any contiguous run of at
#: least this many characters of governed plaintext marks a value as that
#: payload's derivative (distinctive secrets survive paraphrase otherwise).
DERIVATIVE_FRAGMENT_MIN_RUN = 24


def _derivative_value_matches(value: str, content_hash: str, plaintext: str) -> bool:
    """Fragment-aware governed-match law for registered derivative stores.
    Streaming FRAGMENTS have no minimum size: any value that literally occurs
    inside the governed plaintext is that payload's derivative (exact
    substring ownership — no ambiguity, no false positives); the shared
    >=24-run derived-copy law additionally catches paraphrase-bearing copies,
    mirroring CE04/NCE-D."""
    if _text_matches_erasure(
        value, content_hash, plaintext, min_fragment=DERIVATIVE_FRAGMENT_MIN_RUN
    ):
        return True
    stripped = value.strip()
    return bool(stripped) and bool(plaintext) and stripped in plaintext


def _iter_runtime_derivative_strings():
    """Yield every candidate governed-byte string in the A8-owned runtime
    derivative stores: session summary columns, event message column, and every
    string embedded in an event details_json tree."""
    from core.runtime_continuity import _conn

    conn = _conn()
    try:
        srows = conn.execute(
            "SELECT last_message, request_preview FROM runtime_sessions"
        ).fetchall()
        for srow in srows:
            yield "runtime_sessions.last_message", str(srow["last_message"] or "")
            yield "runtime_sessions.request_preview", str(srow["request_preview"] or "")
        erows = conn.execute(
            "SELECT message, details_json FROM runtime_session_events"
        ).fetchall()
        from core.runtime_continuity import _conn, _iter_tree_strings

        for erow in erows:
            yield "runtime_session_events.message", str(erow["message"] or "")
            raw_details = str(erow["details_json"] or "")
            if raw_details.strip():
                try:
                    import json as _sj

                    parsed = _sj.loads(raw_details)
                except ValueError:
                    parsed = None
                if isinstance(parsed, (dict, list)):
                    for item in _iter_tree_strings(parsed):
                        yield "runtime_session_events.details", item
                else:
                    yield "runtime_session_events.details", raw_details
    finally:
        conn.close()


def register_runtime_governed_fragments(
    finalization_id: str, content_hash: str, plaintext: str = ""
) -> int:
    """Bind every surviving runtime derivative of this governed payload into
    the a8_governed_derivatives registry (read-only over the runtime store;
    registration only). Returns the number of newly bound values."""
    keys = governed_runtime_derivative_keys(content_hash, plaintext)
    if not keys:
        return 0
    conn = get_connection()
    try:
        n = _bind_governed_derivative_keys(conn, finalization_id, content_hash, keys)
        conn.commit()
        return n
    finally:
        conn.close()


def governed_runtime_derivative_keys(
    content_hash: str, plaintext: str = ""
) -> list[str]:
    """Collect value-keys of every surviving runtime derivative of this
    governed payload (fragment-aware derived-copy law over the A8 runtime
    stores). Read-only; returns deduplicated sorted sha256 keys."""
    clean_hash = str(content_hash or "").strip()
    if not clean_hash:
        return []
    matched: set[str] = set()
    for _store, value in _iter_runtime_derivative_strings():
        if not value.strip():
            continue
        if _derivative_value_matches(value, clean_hash, plaintext):
            matched.add(_sha256_hex(value))
    return sorted(matched)


def _iter_meet_derivative_strings():
    """Yield every candidate governed-byte string in the MEET network stores
    (hive post bodies, hive topic summaries, task offer briefs, task result
    summaries, VoolBook post content). Tables absent from a deployment are
    skipped honestly — no fabricated lineage."""
    from storage.db import get_connection as _gc

    targets = (
        ("hive_posts", "body"),
        ("hive_topics", "summary"),
        ("task_offers", "summary"),
        ("task_results", "summary"),
        ("voolbook_posts", "content"),
    )
    conn = _gc()
    try:
        for table, column in targets:
            present = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if present is None:
                continue
            for row in conn.execute(f"SELECT {column} AS _val FROM {table}").fetchall():
                yield f"{table}.{column}", str(row["_val"] or "")
    finally:
        conn.close()


def governed_meet_derivative_keys(
    content_hash: str, plaintext: str = ""
) -> list[str]:
    """Collect value-keys of every surviving MEET-network derivative of this
    governed payload (same fragment-aware derived-copy law as the runtime
    collector; hive/task/voolbook bodies quoting governed bytes bind to the
    governing finalization so every serve-time gate resolves them)."""
    clean_hash = str(content_hash or "").strip()
    if not clean_hash:
        return []
    matched: set[str] = set()
    for _store, value in _iter_meet_derivative_strings():
        if not value.strip():
            continue
        if _derivative_value_matches(value, clean_hash, plaintext):
            matched.add(_sha256_hex(value))
    return sorted(matched)


def _bind_governed_derivative_keys(
    conn: sqlite3.Connection,
    finalization_id: str,
    content_hash: str,
    keys: list[str],
) -> int:
    """INSERT OR IGNORE derivative bindings on the GIVEN connection (allows the
    WITHHELD transition to bind inside its own guarded transaction)."""
    clean_fid = str(finalization_id or "").strip()
    if not clean_fid or not keys:
        return 0
    now = _utcnow()
    for key in keys:
        conn.execute(
            """
            INSERT OR IGNORE INTO a8_governed_derivatives (
                value_key, finalization_id, governed_hash, created_at
            ) VALUES (?, ?, ?, ?)
            """,
            (key, clean_fid, str(content_hash or ""), now),
        )
    return len(keys)


def canonical_governed_derivative_key(plaintext: str) -> str:
    """A9 RC-8 pass-003 (F1): digest of the payload's CANONICAL whitespace-
    collapsed representation, bound while the governing identity is still
    intact (pre-transformation lineage attachment).

    Must byte-match the transcript assembler's collapse rule
    (``core.bootstrap_context._normalized_dialogue_text``); a guard test pins
    this equality. Read-side resolution stays identity-based: the key is an
    entry in ``a8_governed_derivatives`` resolving through ``finalization_id``
    to LIVE availability — never text/hash authority re-derived at reads."""
    collapsed = " ".join(str(plaintext or "").split()).strip()
    if not collapsed:
        return ""
    return _sha256_hex(collapsed)


# ---------------------------------------------------------------------------
# A7 W6: delivery truth (distinct from semantic finality and persistence) and
# TRUE_REPLAY as an immutable read.
# ---------------------------------------------------------------------------

DELIVERY_NOT_ATTEMPTED = "NOT_ATTEMPTED"
DELIVERY_ATTEMPTED_UNKNOWN = "ATTEMPTED_UNKNOWN"
DELIVERY_DELIVERED = "DELIVERED"
DELIVERY_FAILED_TRANSPORT = "FAILED_TRANSPORT"

_DELIVERY_TRANSITIONS = {
    DELIVERY_NOT_ATTEMPTED: {DELIVERY_ATTEMPTED_UNKNOWN, DELIVERY_DELIVERED, DELIVERY_FAILED_TRANSPORT},
    DELIVERY_ATTEMPTED_UNKNOWN: {DELIVERY_DELIVERED, DELIVERY_FAILED_TRANSPORT},
    # DELIVERED is absorbing: proven truth never downgrades.
    DELIVERY_DELIVERED: set(),
    DELIVERY_FAILED_TRANSPORT: {DELIVERY_ATTEMPTED_UNKNOWN, DELIVERY_DELIVERED},
}

# F-06 (independent-proof repair): the ONLY classes that may author proven
# delivery truth, enforced mechanically at the durable write (was: any
# non-empty string accepted, so e.g. LEGACY_UNVERIFIED could stamp DELIVERED).
DELIVERY_EVIDENCE_CLASSES = frozenset(
    {"TRANSPORT_HANDOFF", "PLATFORM_ACK", "RECONCILED"}
)


def set_delivery_status(
    finalization_id: str,
    new_status: str,
    *,
    evidence_class: str = "",
    execution_identity: dict[str, Any] | None = None,
) -> bool:
    """Monotone delivery-status transition, decided IN SQL by rowcount.

    NOT_ATTEMPTED -> ATTEMPTED_UNKNOWN | DELIVERED | FAILED_TRANSPORT;
    ATTEMPTED_UNKNOWN -> DELIVERED | FAILED_TRANSPORT; FAILED_TRANSPORT ->
    ATTEMPTED_UNKNOWN | DELIVERED. Downgrades and reopens (DELIVERED ->
    anything) are refused — a delivered answer can never be un-delivered by a
    later writer.

    A-5 DELIVERED=EVIDENCE: a DELIVERED mark REQUIRES an explicit evidence
    class from the canonical vocabulary
    (TRANSPORT_HANDOFF | PLATFORM_ACK | RECONCILED); an empty or OUT-OF-
    VOCABULARY class (e.g. LEGACY_UNVERIFIED — historical evidence only) is
    REFUSED — nothing may upgrade a claim into proven truth.
    """
    clean_fid = str(finalization_id or "").strip()
    if not clean_fid:
        return False
    clean_class = str(evidence_class or "").strip()
    if new_status == DELIVERY_DELIVERED and clean_class not in DELIVERY_EVIDENCE_CLASSES:
        return False
    # Residue-3 (H-2): a delivery write from an onboarded lane PRESENTS its
    # fence tuple — folded into the same transactional unit as the UPDATE.
    if execution_identity:
        from core.invocation.ledger import require_generation

        require_generation(
            str(execution_identity.get("execution_id") or ""),
            int(execution_identity.get("generation") or 0),
            runtime_epoch=str(execution_identity.get("runtime_epoch") or ""),
        )
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT delivery_status FROM a7_finalizations WHERE finalization_id = ?",
            (clean_fid,),
        ).fetchone()
        current = str(row["delivery_status"] or "") if row is not None else ""
        if new_status not in _DELIVERY_TRANSITIONS.get(current, set()):
            return False
        # The writer states its evidence explicitly; nothing is inferred.
        cursor = conn.execute(
            "UPDATE a7_finalizations SET delivery_status = ?, delivery_evidence_class = ? "
            "WHERE finalization_id = ? AND delivery_status = ?",
            (new_status, clean_class, clean_fid, current),
        )
        conn.commit()
        return cursor.rowcount == 1
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# K-09 payload/finality separation: availability machine + tombstones.
# ---------------------------------------------------------------------------

AVAILABILITY_AVAILABLE = "AVAILABLE"
AVAILABILITY_WITHHELD = "WITHHELD"
AVAILABILITY_ERASED = "ERASED"
# A8 law 11 (honest legacy): rows that predate the availability window are
# LEGACY_UNKNOWN — truthful uncertainty, never silently governed-as-AVAILABLE
# by a schema default. LEGACY_UNKNOWN enters the normal machine on first
# governance contact (WITHHELD/ERASED); it is never upgraded to AVAILABLE.
AVAILABILITY_LEGACY_UNKNOWN = "LEGACY_UNKNOWN"
REPLAY_UNAVAILABLE_BY_POLICY = "UNAVAILABLE_BY_POLICY"
# Governance-event kind recording the PRE-ERASE content_hash of a payload.
# Serve-time gates consult these tombstones so legacy (unlineaged) surfaces
# can still deterministically learn that a payload's digest was erased —
# the a7 row's own hash is salted at ERASE and can no longer be matched.
EVENT_KIND_ERASURE_DIGEST_TOMBSTONE = "erasure_digest_tombstone"
EVENT_KIND_ERASURE_SWEEP = "erasure_sweep"
EVENT_KIND_ERASURE_SWEEP_COMPLETE = "erasure_sweep_complete"

_AVAILABILITY_TRANSITIONS = {
    AVAILABILITY_AVAILABLE: {AVAILABILITY_WITHHELD, AVAILABILITY_ERASED},
    AVAILABILITY_LEGACY_UNKNOWN: {AVAILABILITY_WITHHELD, AVAILABILITY_ERASED},
    AVAILABILITY_WITHHELD: {AVAILABILITY_ERASED},
    AVAILABILITY_ERASED: set(),
}

# Availability states under which governed payload bytes must never be served.
_UNSERVEABLE_AVAILABILITY = (AVAILABILITY_WITHHELD, AVAILABILITY_ERASED)


def set_availability(
    finalization_id: str,
    new_state: str,
    *,
    reason: str = "",
    governance_actor: str = "",
) -> bool:
    """Monotone AVAILABLE → WITHHELD → ERASED, decided IN SQL by rowcount.

    A-13.1/.2: the guarded transition, byte-clear, and tombstone event commit
    in ONE transaction, and the event is written ONLY when rowcount == 1 —
    append-only must not mean append-fiction. ERASE clears the inline
    plaintext and payload_ref and replaces content_hash with a keyed/salted
    digest (digest-retention law: no unsalted confirmation oracle survives
    erasure).
    """
    clean_fid = str(finalization_id or "").strip()
    if not clean_fid:
        return False
    # LINEARIZATION POINT (2026-09-02). The transition collects its derivative bindings with a
    # READ-ONLY scan and binds them inside the guarded transaction below. Between those two
    # moments this function used to hold nothing, and a public-surface writer could complete a
    # whole admission in the gap:
    #
    #   scan (the post does not exist yet) -> post committed, quote unbound -> CAS flips WITHHELD
    #
    # After the flip the write fence refuses new writes, so that post's quote was never bound to
    # anything. Its body is not byte-equal to the payload, so the exact-hash gate misses it, and
    # with no binding the serve gate reads it as ungoverned legacy and serves it forever. The
    # window is narrow, which is why it presented as an intermittent leak rather than a constant
    # one — it was never a flake.
    #
    # `A8_TRAVERSAL_LOCK` is the authority the public write fence ALREADY takes
    # (`create_post_record`) and that the ERASE traversal already holds. Taking it here for the
    # whole scan-and-commit makes the transition atomic with respect to those writers: a writer
    # either finishes entirely before the scan (so the scan sees it and binds it) or runs after
    # the commit (so the fence sees WITHHELD and refuses it). There is no third interleaving.
    #
    # Lock ordering is unchanged and uniform — A8_TRAVERSAL_LOCK is always taken BEFORE any
    # sqlite transaction, never after — so this introduces no inversion. The lock is an RLock,
    # so an erase path that re-enters through here on the same thread still proceeds. Both
    # critical sections are short and hold no network or model call.
    with A8_TRAVERSAL_LOCK:
        return _set_availability_locked(
            clean_fid,
            new_state,
            reason=reason,
            governance_actor=governance_actor,
        )


def _set_availability_locked(
    clean_fid: str,
    new_state: str,
    *,
    reason: str = "",
    governance_actor: str = "",
) -> bool:
    """The guarded transition itself. Callers reach it through :func:`set_availability`, which
    owns the linearization against public-surface writers."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT availability, content_hash, canonical_content "
            "FROM a7_finalizations WHERE finalization_id = ?",
            (clean_fid,),
        ).fetchone()
        current = str(row["availability"] or "") if row is not None else ""
        if new_state not in _AVAILABILITY_TRANSITIONS.get(current, set()):
            return False
        # A8 PASS004 (NCE-F2): derivative key collection runs BEFORE this
        # function opens its guarded transaction — re-entering the shared
        # connection pool mid-transaction would roll back the CAS update.
        # Collection is read-only over the runtime store.
        # A9 RC-8 pass-003 (F1): the payload's canonical collapsed representation
        # is bound WHILE identity is intact, before any downstream lossy
        # transformation of copies can orphan it. For ERASED the binding stores
        # governed_hash='' — strict digest law (the unsalted pre-erase hash is
        # never retained); ownership resolves through finalization_id alone.
        canonical_derivative_key = canonical_governed_derivative_key(
            str(row["canonical_content"] or "")
        )
        withholding_keys: list[str] = []
        if new_state == AVAILABILITY_WITHHELD:
            withholding_keys = governed_runtime_derivative_keys(
                str(row["content_hash"] or ""),
                str(row["canonical_content"] or ""),
            )
            # A8 final freeze: the MEET network stores (hive posts/topics,
            # task offers/results, VoolBook posts) are served surfaces too —
            # their quoting bodies bind under the same WITHHOLD law so every
            # serve-time gate resolves them without destructive traversal.
            # Read-only collection BEFORE the guarded transaction (NCE-F2 law);
            # a collection failure aborts the transition (fail-closed).
            withholding_keys = [
                *withholding_keys,
                *governed_meet_derivative_keys(
                    str(row["content_hash"] or ""),
                    str(row["canonical_content"] or ""),
                ),
            ]
        cursor = conn.execute(
            "UPDATE a7_finalizations SET availability = ? "
            "WHERE finalization_id = ? AND availability = ?",
            (new_state, clean_fid, current),
        )
        if cursor.rowcount != 1:
            # Raced refusal: no event for a transition that never happened.
            conn.rollback()
            return False
        if new_state == AVAILABILITY_WITHHELD:
            # A8 PASS004 (NCE-F2): a streamed answer leaves per-chunk FRAGMENTS
            # in denormalized runtime summary columns; withholding must deny
            # them at every served boundary without destructive traversal.
            # Bind each surviving derivative BY DIGEST to this finalization so
            # serve-time gates resolve lineage identity instead of re-deriving
            # ownership from fragment text — inside the same guarded commit.
            _bind_governed_derivative_keys(
                conn,
                clean_fid,
                str(row["content_hash"] or ""),
                [k for k in [*withholding_keys, canonical_derivative_key] if k],
            )
        if new_state == AVAILABILITY_ERASED:
            pre_erase_hash = str(row["content_hash"] or "")
            salted_digest = "salted-sha256:" + hashlib.sha256(
                (clean_fid + "\x1f" + _utcnow()).encode("utf-8")
            ).hexdigest()
            # Byte-clear carries the availability pin (safety-under-refactor:
            # the clear may only ever run on the row the CAS just won).
            conn.execute(
                "UPDATE a7_finalizations SET canonical_content = '', payload_ref = NULL,"
                " content_hash = ? WHERE finalization_id = ? AND availability = ?",
                (salted_digest, clean_fid, new_state),
            )
            # A8 digest-retention law: the PRE-ERASE content_hash is tombstoned
            # in the governance ledger so unlineaged legacy surfaces can still
            # deterministically learn the digest was erased (the row's hash is
            # salted above and can no longer be matched by content).
            if pre_erase_hash and not pre_erase_hash.startswith("salted-sha256:"):
                conn.execute(
                    """
                    INSERT INTO a7_governance_events (
                        event_id, finalization_id, event_kind, previous_state,
                        new_state, reason, actor, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"gov-{clean_fid}-digest-tombstone-{_utcnow()}",
                        clean_fid,
                        EVENT_KIND_ERASURE_DIGEST_TOMBSTONE,
                        "",
                        AVAILABILITY_ERASED,
                        # Strict digest law (pass-002): KEYED digest — the
                        # pre-erase sha256 itself is never retained.
                        _keyed_tombstone_value(pre_erase_hash),
                        str(governance_actor or ""),
                        _utcnow(),
                    ),
                )
            if canonical_derivative_key:
                # A9 RC-8 pass-003 (F1): whitespace-transformed copies of the
                # erased payload collapse to this same key and resolve through
                # finalization lineage to ERASED. governed_hash stays empty per
                # strict digest law.
                _bind_governed_derivative_keys(
                    conn, clean_fid, "", [canonical_derivative_key]
                )
        conn.execute(
            """
            INSERT INTO a7_governance_events (
                event_id, finalization_id, event_kind, previous_state,
                new_state, reason, actor, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"gov-{clean_fid}-{new_state}-{_utcnow()}",
                clean_fid,
                "availability_transition",
                current,
                new_state,
                str(reason or ""),
                str(governance_actor or ""),
                _utcnow(),
            ),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def sweep_attempted_unknown_deliveries(limit: int = 100) -> list[dict[str, Any]]:
    """K-10 startup sweep (pure read): committed bytes whose handoff ended in
    ATTEMPTED_UNKNOWN. Each entry is a DELIVERY_RETRY candidate — resend the
    committed bytes byte-for-byte, ZERO model/tool work. Never upgrades truth:
    reconciliation authors DELIVERED only on proven evidence classes."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT finalization_id, semantic_result_id, content_hash
            FROM a7_finalizations
            WHERE delivery_status = ? AND status = ? AND availability = ?
            ORDER BY created_at ASC LIMIT ?
            """,
            (DELIVERY_ATTEMPTED_UNKNOWN, ANSWER_PRESENT, AVAILABILITY_AVAILABLE, int(limit)),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def replay_finalized_answer(
    *,
    semantic_result_id: str | None = None,
    content_hash: str | None = None,
    principal: str = "",
) -> dict[str, Any] | None:
    """TRUE_REPLAY: serve existing immutable finalized truth as a pure read.

    Looks up committed truth by admitted semantic id (preferred) or content
    identity. Returns the stored canonical bytes and identity — the caller
    serves them WITHOUT any model regeneration or identity remint. Returns None
    when no committed truth exists (a retry execution is then the caller's
    explicitly distinct decision).

    A8 principal-scope law (fail-closed): a replay request carries the
    authenticated principal; an absent or unrecognized principal is refused
    before any availability verdict or byte is considered. The vocabulary is
    the invocation ledger's — A8 does not mint a permission authority.
    """
    from core.invocation.ledger import validate_principal

    validate_principal(principal)
    row = None
    if str(semantic_result_id or "").strip():
        row = get_finalization_by_semantic_id(semantic_result_id)
    if row is None and str(content_hash or "").strip():
        conn = get_connection()
        try:
            found = conn.execute(
                "SELECT * FROM a7_finalizations WHERE content_hash = ? LIMIT 1",
                (str(content_hash).strip(),),
            ).fetchone()
            row = dict(found) if found else None
        finally:
            conn.close()
    if row is None:
        return None
    # K-09 BYTES-AVAILABLE-LAW: erased/withheld payload replays honestly as
    # UNAVAILABLE_BY_POLICY — the bytes are NEVER regenerated.
    availability = str(row.get("availability") or "AVAILABLE")
    if availability in (AVAILABILITY_WITHHELD, AVAILABILITY_ERASED):
        return {
            "semantic_result_id": str(row.get("semantic_result_id") or ""),
            "finalization_id": str(row.get("finalization_id") or ""),
            "turn_id": str(row.get("turn_id") or ""),
            "status": str(row.get("status") or ""),
            "delivery_status": str(row.get("delivery_status") or ""),
            "availability": availability,
            "replay_outcome": REPLAY_UNAVAILABLE_BY_POLICY,
            "canonical_content": "",
            "content_hash": str(row.get("content_hash") or ""),
        }
    return {
        "semantic_result_id": str(row.get("semantic_result_id") or ""),
        "finalization_id": str(row.get("finalization_id") or ""),
        "turn_id": str(row.get("turn_id") or ""),
        "status": str(row.get("status") or ""),
        "delivery_status": str(row.get("delivery_status") or ""),
        "availability": availability,
        "canonical_content": str(row.get("canonical_content") or ""),
        "content_hash": str(row.get("content_hash") or ""),
    }


def _bind_durably(commit: dict[str, Any]) -> str:
    """Engine-enforced durable binding (immutable-first-insert).

    - INSERT claims the row -> ACCEPTED_FIRST;
    - unique-index conflict + equal stored hash -> ACCEPTED_IDENTICAL;
    - unique-index conflict + different hash -> FinalizationRejected (honest;
      v1 stays canonical; no write occurred).
    """
    conn = get_connection()
    try:
        # K-04 REFERENT-BEFORE-REFERENCE: a finalization citing an sr id with no
        # durable admission row is refused — zero rows written.
        cited_sr = str(commit.get("semantic_result_id") or "").strip()
        if cited_sr:
            from core.semantic.semantic_admissions import admission_exists

            if not admission_exists(cited_sr):
                raise FinalizationRejected(
                    cited_sr,
                    "NO_ADMISSION_ROW",
                    commit["content_hash"],
                )
        else:
            # R-5/A-1: an ONBOARDED lane (execution identity bound) may NOT
            # finalize without an admitted sr citation — the empty-sr escape
            # that let commits evade REFERENT-BEFORE-REFERENCE is closed.
            from core.semantic.semantic_admissions import current_execution_identity

            if current_execution_identity():
                raise FinalizationRejected(
                    "",
                    "EMPTY_SR_ONBOARDED_LANE",
                    commit["content_hash"],
                )
        try:
            # A8 PASS003 (CE07/NCE-C): a fresh finalization of bytes whose
            # canonical payload is already WITHHELD/ERASED must BIND to the
            # existing privacy state — it may never mint AVAILABLE and shadow
            # the digest tombstone. The consult + INSERT share one serialized
            # transaction, so ERASE either fully precedes this txn (verdict
            # seen, inherited) or fully follows it (the derivative sweep then
            # hash-matches this row). Monotone AVAILABILITY→WITHHELD/ERASED
            # law is restored at the binding layer; A7 identity/immutability
            # untouched.
            _inherited = ""
            try:
                _inherited = str(
                    payload_availability_for_hash(str(commit.get("content_hash") or "")) or ""
                )
            except Exception:
                _inherited = AVAILABILITY_ERASED  # fail closed on live-store outage
            _inherit_unavailable = _inherited in _UNSERVEABLE_AVAILABILITY
            _availability = (
                _inherited if _inherit_unavailable else AVAILABILITY_AVAILABLE
            )
            _content = "" if _inherit_unavailable else str(commit["canonical_content"])
            # A8 runtime-epoch privacy fence (R5): on an onboarded lane the
            # epoch/generation fence is IN the INSERT predicate itself — the
            # write and the fence share one statement, one transaction, one
            # snapshot. A stale process cannot mint fresh AVAILABLE bytes after
            # a newer runtime bumped the epoch: the EXISTS fails, zero rows are
            # written, and the refusal is typed FenceRefused.
            from core.semantic.semantic_admissions import current_execution_identity

            _identity = current_execution_identity()
            if _identity:
                cursor = conn.execute(
                    """
                    INSERT INTO a7_finalizations (
                        finalization_id, semantic_result_id, turn_id,
                        content_hash, canonical_content, status, request_id,
                        payload_ref, availability
                    )
                    SELECT ?, ?, ?, ?, ?, ?, ?, NULL, ?
                    WHERE EXISTS (
                        SELECT 1 FROM executions
                        WHERE execution_id = ? AND generation = ?
                          AND runtime_epoch = ? AND state = 'ACTIVE'
                    )
                    """,
                    (
                        commit["finalization_id"],
                        commit["semantic_result_id"],
                        commit["turn_id"],
                        commit["content_hash"],
                        _content,
                        commit["status"],
                        str(commit.get("request_id") or ""),
                        _availability,
                        str(_identity.get("execution_id") or ""),
                        int(_identity.get("generation") or 0),
                        str(_identity.get("runtime_epoch") or ""),
                    ),
                )
                conn.commit()
                if cursor.rowcount == 1:
                    return "ACCEPTED_FIRST"
                conn.rollback()
                from core.invocation.ledger import FenceRefused

                raise FenceRefused(
                    "fence mismatch (in-predicate): execution="
                    f"{_identity.get('execution_id')!r} generation="
                    f"{_identity.get('generation')!r} runtime_epoch="
                    f"{_identity.get('runtime_epoch')!r}"
                )
            cursor = conn.execute(
                """
                INSERT INTO a7_finalizations (
                    finalization_id, semantic_result_id, turn_id,
                    content_hash, canonical_content, status, request_id,
                    payload_ref, availability
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)
                """,
                (
                    commit["finalization_id"],
                    commit["semantic_result_id"],
                    commit["turn_id"],
                    commit["content_hash"],
                    _content,
                    commit["status"],
                    str(commit.get("request_id") or ""),
                    _availability,
                ),
            )
            conn.commit()
            if cursor.rowcount == 1:
                return "ACCEPTED_FIRST"
        except sqlite3.IntegrityError:
            conn.rollback()
        # Classify honestly: prefer the logical-truth key (admitted sr id), and
        # fall back to the finalization id for lanes without admission.
        stored = None
        if str(commit["semantic_result_id"] or "").strip():
            stored = get_finalization_by_semantic_id(commit["semantic_result_id"])
        if stored is None:
            conn2 = get_connection()
            try:
                row = conn2.execute(
                    "SELECT * FROM a7_finalizations WHERE finalization_id = ?",
                    (commit["finalization_id"],),
                ).fetchone()
                stored = dict(row) if row else None
            finally:
                conn2.close()
        stored_hash = str((stored or {}).get("content_hash") or "")
        if stored and stored_hash == commit["content_hash"]:
            return "ACCEPTED_IDENTICAL"
        raise FinalizationRejected(
            commit["semantic_result_id"], stored_hash, commit["content_hash"]
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# A8 erasure traversal — lineage-directed, idempotent, crash-safe.
#
# ERASED = canonical-row law (byte-clear + payload_ref NULL + salted digest +
# digest tombstone, above) PLUS a sweep over every governed local derivative
# reachable by lineage (request_id / finalization_id stamps) or, for legacy
# pre-lineage rows, by deterministic content-hash equality. Per-store sweep
# outcomes are recorded in the a7_governance_events ledger so a crash
# mid-traversal is observable and resumable; an erasure is only reported
# complete when every store step actually completed. A8 owns the verdict and
# the traversal ledger — the physical row/file removals are storage-owner
# effects triggered by this verdict, one canonical path, no second manager.
# ---------------------------------------------------------------------------

_SWEEP_STEP_OK = "completed"
_SWEEP_STEP_ERROR_PREFIX = "error:"


def _sweep_event(finalization_id: str, store_name: str, outcome: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO a7_governance_events (
                event_id, finalization_id, event_kind, previous_state,
                new_state, reason, actor, created_at, store_name, sweep_outcome
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"gov-{finalization_id}-sweep-{store_name}-{_utcnow()}",
                finalization_id,
                EVENT_KIND_ERASURE_SWEEP,
                "",
                AVAILABILITY_ERASED,
                "",
                "a8_traversal",
                _utcnow(),
                store_name,
                outcome,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _text_hash_matches(text: str, stored_digest: str) -> bool:
    """Deterministic content-equivalence: raw sha256 equality (legacy plain
    hash argument) OR keyed-tombstone equality so a sweep resumed after the
    byte-clear still recognizes the erased payload via its keyed digest."""
    if not stored_digest:
        return False
    digest = _sha256_hex(str(text or ""))
    return _digest_representations_match(digest, stored_digest)


def _digest_representations_match(canonical_prefixed_digest: str, stored_digest: str) -> bool:
    """PASS003 canonical digest-comparison representation: ANY caller-supplied
    digest form (bare hex, ``sha256:<hex>``, or the keyed tombstone form) is
    matched through THIS one helper — callers never hand-roll prefix surgery
    (CE04/NCE root cause was exactly such a scattered comparison)."""
    if not stored_digest:
        return False
    digest = str(canonical_prefixed_digest or "")
    clean = digest[len("sha256:"):] if digest.startswith("sha256:") else ""
    stored = str(stored_digest)
    if stored == digest or stored == clean:
        return True
    try:
        return bool(clean) and _keyed_tombstone_value("sha256:" + clean) == stored
    except Exception:
        return False


def _stored_digest_matches_erasure(digest_forms: set[str], stored_digest: str) -> bool:
    for form in digest_forms:
        if _digest_representations_match(form, stored_digest):
            return True
    return False


def _pre_erasure_plaintext_for(finalization_id: str) -> str:
    """Read the governed plaintext BEFORE the availability transition clears
    it, so derivative sweeps can also purge text that merely CONTAINS the
    payload (derived facts, block lines, shadow JSON copies). Empty on resume
    after the first pass — removal is idempotent."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT canonical_content FROM a7_finalizations WHERE finalization_id = ?",
            (str(finalization_id or "").strip(),),
        ).fetchone()
        return str(row["canonical_content"] or "") if row is not None else ""
    finally:
        conn.close()


def _iter_json_strings(value: Any, depth: int = 0):
    """Yield (path-is-irrelevant) every plain string embedded in a JSON-style
    tree, used by the checkpoint/details shadow-copy sweeps."""
    if depth > 8:
        return
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_json_strings(item, depth + 1)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_json_strings(item, depth + 1)


def _scrub_governed_json_strings(
    value: Any,
    *,
    finalization_id: str,
    content_hash: str,
    plaintext: str = "",
    depth: int = 0,
) -> tuple[Any, bool]:
    """Return (clean_copy, changed): every embedded STRING carrying governed
    payload bytes (hash-equivalent OR containing the plaintext) is removed
    from the tree (lists shrink; dict scalars blank). Used for checkpoint
    state/source shadows and runtime details_json — canonical representation,
    not one-field specials (CE11/CE09)."""
    if depth > 8:
        return value, False
    if isinstance(value, str):
        governed = bool(value.strip()) and (
            _text_hash_matches(value, content_hash)
            or (bool(plaintext) and plaintext in value)
        )
        return ("", True) if governed else (value, False)
    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        changed = False
        for key, item in value.items():
            cleaned, hit = _scrub_governed_json_strings(
                item,
                finalization_id=finalization_id,
                content_hash=content_hash,
                plaintext=plaintext,
                depth=depth + 1,
            )
            out[key] = cleaned
            changed = changed or hit
        return out, changed
    if isinstance(value, list):
        kept: list[Any] = []
        changed = False
        for item in value:
            cleaned, hit = _scrub_governed_json_strings(
                item,
                finalization_id=finalization_id,
                content_hash=content_hash,
                plaintext=plaintext,
                depth=depth + 1,
            )
            if not hit:
                kept.append(cleaned)
            changed = changed or hit
        return kept, changed
    return value, False


def _sweep_step_conversation_log(request_id: str, content_hash: str) -> str:
    from core.memory.files import conversation_log_path, load_jsonl, rewrite_jsonl

    rows = load_jsonl(conversation_log_path())
    kept = [
        row
        for row in rows
        if str(row.get("request_id") or "") != request_id
        and not _text_hash_matches(str(row.get("assistant") or ""), content_hash)
    ]
    if len(kept) != len(rows):
        rewrite_jsonl(conversation_log_path(), kept)
    return _SWEEP_STEP_OK


def _sweep_step_memory_jsonl(request_id: str) -> str:
    from core.memory.entries import sweep_governed_memory_rows
    from core.memory.files import (
        load_jsonl,
        rewrite_jsonl,
        session_summaries_path,
        user_heuristics_path,
    )

    # The canonical memory store goes through the memory authority: the entry
    # lock serializes the rewrite against concurrent record/forget mutations
    # and the MEMORY.md projection is refreshed, so no bound mirror line
    # outlives a swept row.
    sweep_governed_memory_rows(request_id)
    for path in (session_summaries_path(), user_heuristics_path()):
        rows = load_jsonl(path)
        kept = [row for row in rows if str(row.get("request_id") or "") != request_id]
        if len(kept) != len(rows):
            rewrite_jsonl(path, kept)
    return _SWEEP_STEP_OK


def _sweep_step_dialogue_tables(request_id: str, content_hash: str) -> str:
    from storage.db import get_connection as _gc

    conn = _gc()
    try:
        # Legacy verbatim columns that may hold governed payload bytes even
        # when their lineage column is empty (CE-A / T16): a successful sweep
        # removes them by deterministic content equivalence — no lineage
        # fabrication.
        hash_targets = {
            "dialogue_topic_archives": ("summary", "closing_user_input", "closing_assistant_output"),
            "response_feedback": ("context_snapshot", "user_correction"),
            "dialogue_turns": (),
        }
        for table, hash_columns in (
            {"dialogue_turns": (), **hash_targets}.items()
        ):
            present = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if present is None:
                # Table not present on this store (legacy install) — nothing
                # governed can live there.
                continue
            if request_id:
                rid_column = conn.execute(f"PRAGMA table_info({table})").fetchall()
                has_rid = any(str(row["name"]) == "request_id" for row in rid_column)
                if has_rid:
                    conn.execute(f"DELETE FROM {table} WHERE request_id = ?", (request_id,))
            for column in hash_columns:
                rows = conn.execute(
                    f"SELECT rowid AS _rid, {column} AS _val FROM {table}"
                ).fetchall()
                doomed = [
                    str(row["_rid"])
                    for row in rows
                    if str((row and row["_val"]) or "") != ""
                    and _text_hash_matches(str(row["_val"]), content_hash)
                ]
                for rid in doomed:
                    conn.execute(
                        f"DELETE FROM {table} WHERE rowid = ?", (int(rid),)
                    )
        conn.commit()
    finally:
        conn.close()
    return _SWEEP_STEP_OK


def _text_matches_erasure(
    text: str, content_hash: str, plaintext: str = "", *, min_fragment: int = 0
) -> bool:
    """Hash-equivalence in ANY retained representation, or exact plaintext
    containment when the pre-erase bytes are known. ``min_fragment`` extends
    the containment law to DERIVED text: any contiguous run of at least
    min_fragment characters of the erased payload marks derived copies
    (fact lines such as 'my recovery words are <secret>') as governed too —
    distinctive secrets survive paraphrase otherwise (PASS003 CE04/NCE-D)."""
    value = str(text or "")
    if not value.strip():
        return False
    bare = content_hash[len("sha256:"):] if content_hash.startswith("sha256:") else content_hash
    import hashlib as _hl

    text_hex = _hl.sha256(value.encode("utf-8")).hexdigest()
    if text_hex == bare or _sha256_hex(value) == content_hash:
        return True
    try:
        # A8 final freeze: keyed-tombstone equivalence resolves through the
        # ONE canonical helper — tombstones are minted over the prefixed
        # "sha256:<hex>" form, and a bare-hex HMAC here never matched a
        # resumed sweep's digest (pre-existing latent defect this freeze's
        # F12 detector exposed).
        if bool(bare) and _digest_representations_match(_sha256_hex(value), content_hash):
            return True
    except Exception:
        pass
    if not plaintext:
        return False
    if plaintext in value:
        return True
    if min_fragment > 0 and len(plaintext) > min_fragment and len(value) <= 100000:
        # Deterministic derived-copy containment: a contiguous payload run of
        # >= min_fragment chars inside this text.
        import difflib as _difflib

        matcher = _difflib.SequenceMatcher(None, value, plaintext, autojunk=False)
        _a, _b, size = matcher.find_longest_match(0, len(value), 0, len(plaintext))
        return size >= min_fragment
    return False


def _sweep_step_semantic_nodes(request_id: str, content_hash: str, plaintext: str = "") -> str:
    from core.vool_memory import VoolMemory

    removed = 0
    with VoolMemory() as mem:
        if request_id:
            removed += int(mem.node_delete_by_lineage(request_id) or 0)
        # Legacy verbatim fallback (CE-A): unlineaged fact/memory nodes whose
        # content equals OR CONTAINS the erased payload cannot survive strict
        # erasure. Only rows with NO lineage stamp are touched — lineage truth
        # is never fabricated. The digest comparison runs through the canonical
        # representation matcher (PASS003 CE04 root-cause repair).
        try:
            removed += int(
                mem.node_delete_unlineaged_by_content(
                    content_hash, plaintext=plaintext
                )
                or 0
            )
        except AttributeError:
            # Store adapter without the legacy leg: honest non-completion.
            return f"{_SWEEP_STEP_ERROR_PREFIX}legacy_node_leg_unavailable"
    return _SWEEP_STEP_OK


def _sweep_step_memory_blocks(finalization_id: str, content_hash: str, plaintext: str = "") -> str:
    """PASS003 (NCE-D): memory_blocks fact lines re-fed into later prompts via
    blocks_for_prompt are part of the erasure graph. Governed LINES are
    removed; a block whose content is wholly governed is deleted. Honest
    error outcome when the store cannot be opened/inspected."""
    from core.vool_memory import VoolMemory

    with VoolMemory() as mem:
        mem.purge_governed_lines(content_hash=content_hash, plaintext=plaintext)
    return _SWEEP_STEP_OK


def _sweep_step_runtime_events(
    content_hash: str, plaintext: str = "", finalization_id: str = ""
) -> str:
    """Runtime event log (model_output / model_output_chunk / details legs):
    governed bytes that reconstruct an erased answer are removed (T13). The
    PASS003 leg additionally inspects details_json shadow copies (CE09) and
    the denormalized runtime_sessions summary columns (NCE-A) which no
    earlier step ever touched. PASS004 extends this leg to FRAGMENT forms:
    per-chunk derivatives of a streamed answer are matched with the shared
    derived-copy law AND bound into the a8_governed_derivatives lineage
    registry before scrubbing, so any surviving copy elsewhere stays denied."""
    from core.runtime_continuity import _conn

    bound_keys: set[str] = set()

    def _note(value: str) -> None:
        if value.strip():
            bound_keys.add(_sha256_hex(value))

    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT rowid AS _rid, session_id, seq, event_type, message, details_json "
            "FROM runtime_session_events"
        ).fetchall()
        doomed_ids: list[int] = []
        # Chunk reconstruction: consecutive model_output_chunk messages that
        # concatenate into the erased payload must not survive either.
        from collections import defaultdict

        chunks = defaultdict(list)
        for row in rows:
            message = str(row["message"] or "")
            raw_details = str(row["details_json"] or "")
            # PASS004 (NCE-F1): the message column is a governed-derivative
            # surface in its own right. Matching uses the shared derived-copy
            # law — a strict superset of the historical whole-payload forms —
            # so streamed FRAGMENTS die at rest even when concatenation can
            # never reconstruct the payload verbatim (whitespace-stripped
            # chunks).
            message_hit = _text_matches_erasure(
                message, content_hash, plaintext
            ) or _derivative_value_matches(message, content_hash, plaintext)
            details_hit = False
            if raw_details and not message_hit:
                try:
                    import json as _sj

                    parsed = _sj.loads(raw_details)
                except ValueError:
                    parsed = None
                if isinstance(parsed, (dict, list)):
                    _, details_hit = _scrub_governed_json_strings(
                        parsed,
                        finalization_id="",
                        content_hash=content_hash,
                        plaintext=plaintext,
                    )
                else:
                    details_hit = _text_matches_erasure(raw_details, content_hash, plaintext)
            if message_hit or details_hit:
                # PASS004: register EVERY governed derivative form (incl. short
                # chunk fragments that only the substring law can see), not
                # just whole-payload-hash messages, so the lineage verdicts
                # stay uniform after the rows themselves are deleted.
                if _derivative_value_matches(message, content_hash, plaintext):
                    _note(message)
                doomed_ids.append(int(row["_rid"]))
                continue
            if str(row["event_type"]) == "model_output_chunk":
                chunks[str(row["session_id"])].append((int(row["seq"]), int(row["_rid"]), message))
        for session_key, chunk_list in chunks.items():
            # Reconstruction law: the erased payload may be spread across
            # consecutive chunk events; hash any contiguous window (bounded)
            # so a split copy is as dead as a verbatim one.
            ordered = sorted(chunk_list)
            n = len(ordered)
            for start in range(n):
                joined = ""
                for end in range(start, min(n, start + 256)):
                    joined += ordered[end][2]
                    if _text_matches_erasure(joined, content_hash, plaintext):
                        # PASS004: each chunk in the doomed window is a
                        # governed derivative; register its lineage before the
                        # rows are removed so fragment verdicts stay uniform.
                        for _rid, _seq, chunk_msg in ordered[start : end + 1]:
                            if _derivative_value_matches(chunk_msg, content_hash, plaintext):
                                _note(chunk_msg)
                        doomed_ids.extend(rid for _, rid, _ in ordered[start : end + 1])
                        break
        for rid in set(doomed_ids):
            conn.execute("DELETE FROM runtime_session_events WHERE rowid = ?", (int(rid),))
        # NCE-A at-rest leg: the denormalized session summary columns are
        # erased-payload derivatives; blank them wherever they still carry
        # governed bytes. Never fabricated-complete: blanked rows verify
        # clean on re-run. PASS004: matching uses the shared derived-copy law
        # (NCE-F1) so per-chunk FRAGMENTS are caught too, and each matched
        # value is first bound into the lineage registry as defense-in-depth.
        srows = conn.execute(
            "SELECT rowid AS _rid, last_message, request_preview FROM runtime_sessions"
        ).fetchall()
        for srow in srows:
            updates: dict[str, str] = {}
            for column in ("last_message", "request_preview"):
                value = str(srow[column] or "")
                if _derivative_value_matches(value, content_hash, plaintext):
                    _note(value)
                    updates[column] = ""
                elif bool(plaintext) and plaintext and plaintext in value:
                    updates[column] = value.replace(plaintext, "")
            if updates:
                sets = ", ".join(f"{name} = ?" for name in updates)
                conn.execute(
                    f"UPDATE runtime_sessions SET {sets} WHERE rowid = ?",
                    (*updates.values(), int(srow["_rid"])),
                )
        conn.commit()
    finally:
        conn.close()
    # PASS004: registry binding runs on its own committed transaction AFTER
    # the runtime-store scrub commits — never from a second open connection
    # to the same database file mid-transaction.
    if bound_keys:
        reg_conn = get_connection()
        try:
            _bind_governed_derivative_keys(
                reg_conn,
                finalization_id,
                content_hash,
                sorted(bound_keys),
            )
            reg_conn.commit()
        finally:
            reg_conn.close()
    return _SWEEP_STEP_OK


def _sweep_step_checkpoint_evidence(content_hash: str, plaintext: str = "") -> str:
    """Checkpoint governed-byte copies (T15 + PASS003 CE11): source_context,
    state_json, pending_intent_json, request_text, failure_text and
    outcome_json — EVERY canonical checkpoint representation is scrubbed, not
    one field."""
    import json as _json

    from core.runtime_continuity import _conn

    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT rowid AS _rid, checkpoint_id, request_text, failure_text, "
            "source_context_json, state_json, pending_intent_json, outcome_json, "
            "final_response FROM runtime_checkpoints"
        ).fetchall()

        def _scrub_text(value: str) -> str | None:
            """Plain column scrub: governed bytes → '' (or redact occurrence)."""
            text = str(value or "")
            if not text:
                return None
            if _text_matches_erasure(text, content_hash, plaintext):
                return ""
            if plaintext and plaintext in text:
                return text.replace(plaintext, "")
            return None

        for row in rows:
            updates: dict[str, Any] = {}

            def _scrub_json_column(column: str) -> None:
                raw = str(row[column] or "")
                if not raw or raw == "{}":
                    return
                try:
                    parsed = _json.loads(raw)
                except ValueError:
                    cleaned = _scrub_text(raw.strip('"'))
                    if cleaned is not None:
                        updates[column] = cleaned
                    return
                cleaned, changed = _scrub_governed_json_strings(
                    parsed,
                    finalization_id="",
                    content_hash=content_hash,
                    plaintext=plaintext,
                )
                if changed:
                    updates[column] = _json.dumps(cleaned, sort_keys=True)

            for plain_column in (
                "request_text",
                "failure_text",
                "final_response",
            ):
                cleaned = _scrub_text(str(row[plain_column] or ""))
                if cleaned is not None:
                    updates[plain_column] = cleaned
            for json_column in (
                "source_context_json",
                "state_json",
                "pending_intent_json",
                "outcome_json",
            ):
                _scrub_json_column(json_column)
            if updates:
                sets = ", ".join(f"{name} = ?" for name in updates)
                conn.execute(
                    f"UPDATE runtime_checkpoints SET {sets} WHERE rowid = ?",
                    (*updates.values(), int(row["_rid"])),
                )
        conn.commit()
    finally:
        conn.close()
    return _SWEEP_STEP_OK


def _sweep_step_task_result_bodies(content_hash: str, plaintext: str = "") -> str:
    """Verbatim AND derivative copies of erased bytes across the MEET network
    stores (task result summaries, hive post bodies, hive topic summaries,
    task offer briefs, VoolBook post content). The fragment-aware
    derived-copy law (>=24-run containment / exact-substring ownership)
    matches quoting bodies the exact-hash sweep could never see."""
    from storage.db import get_connection as _gc

    conn = _gc()
    try:
        for table, column in (
            ("task_results", "summary"),
            ("hive_posts", "body"),
            ("hive_topics", "summary"),
            ("task_offers", "summary"),
            ("voolbook_posts", "content"),
        ):
            present = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if present is None:
                continue
            rows = conn.execute(f"SELECT {column} AS _val FROM {table}").fetchall()
            for row in rows:
                value = str(row["_val"] or "")
                if _derivative_value_matches(value, content_hash, plaintext):
                    conn.execute(
                        f"DELETE FROM {table} WHERE {column} = ?", (value,)
                    )
        conn.commit()
    finally:
        conn.close()
    return _SWEEP_STEP_OK


def _sweep_step_checkpoints(content_hash: str, plaintext: str = "") -> str:
    from core.runtime_continuity import _conn

    conn = _conn()
    try:
        conn.execute(
            "UPDATE runtime_checkpoints SET final_response = '', final_response_hash = '' "
            "WHERE final_response_hash = ?",
            (content_hash,),
        )
        # PASS003 CE11 (unstamped recoverable checkpoints): a checkpoint whose
        # row never received a terminal transition may carry the payload
        # WITHOUT its hash stamp; the equality above can never see it. Match
        # by digest equivalence or plaintext containment instead of trusting
        # the stamp.
        rows = conn.execute(
            "SELECT rowid AS _rid, final_response FROM runtime_checkpoints"
        ).fetchall()
        for row in rows:
            value = str(row["final_response"] or "")
            if not value:
                continue
            if _text_matches_erasure(value, content_hash, plaintext) or (
                plaintext and plaintext in value
            ):
                conn.execute(
                    "UPDATE runtime_checkpoints SET final_response = '', "
                    "final_response_hash = '' WHERE rowid = ?",
                    (int(row["_rid"]),),
                )
        conn.commit()
    finally:
        conn.close()
    return _SWEEP_STEP_OK


def _sweep_step_finalized_responses(finalization_id: str, content_hash: str) -> str:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT parent_task_id, raw_synthesized_text, rendered_persona_text, "
            "finalization_id FROM finalized_responses"
        ).fetchall()
        doomed = [
            str(row["parent_task_id"] or "")
            for row in rows
            if str(row["finalization_id"] or "") == finalization_id
            or _text_hash_matches(str(row["raw_synthesized_text"] or ""), content_hash)
            or _text_hash_matches(str(row["rendered_persona_text"] or ""), content_hash)
        ]
        for fid in doomed:
            conn.execute(
                "DELETE FROM finalized_responses WHERE parent_task_id = ?", (fid,)
            )
        conn.commit()
    finally:
        conn.close()
    return _SWEEP_STEP_OK


def _sweep_step_useful_outputs(finalization_id: str, content_hash: str) -> str:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT useful_output_id, output_text, finalization_id FROM useful_outputs"
        ).fetchall()
        doomed = [
            str(row["useful_output_id"] or "")
            for row in rows
            if str(row["finalization_id"] or "") == finalization_id
            or _text_hash_matches(str(row["output_text"] or ""), content_hash)
        ]
        for uid in doomed:
            conn.execute("DELETE FROM useful_outputs WHERE useful_output_id = ?", (uid,))
        conn.commit()
    finally:
        conn.close()
    return _SWEEP_STEP_OK


def _sweep_step_pins(finalization_id: str, content_hash: str) -> str:
    import json as _json

    from core.message_pins import pins_path

    path = pins_path()
    paths = [path, path.with_name(path.name + ".bak")] if path.exists() else []
    changed = False
    for target in paths:
        try:
            store = _json.loads(target.read_text(encoding="utf-8", errors="replace") or "{}")
        except (ValueError, OSError):
            # Governed artifact that cannot be safely inspected: honest
            # non-completion (never a fabricated COMPLETED).
            return f"{_SWEEP_STEP_ERROR_PREFIX}pins_store_unreadable"
        if not isinstance(store, dict):
            return f"{_SWEEP_STEP_ERROR_PREFIX}pins_store_unreadable"
        for sid in list(store.keys()):
            pins = store.get(sid)
            if not isinstance(pins, list):
                continue
            kept = [
                p
                for p in pins
                if not (
                    str((p or {}).get("finalization_id") or "") == finalization_id
                    or (
                        str((p or {}).get("role") or "") == "assistant"
                        and _text_hash_matches(str((p or {}).get("text") or ""), content_hash)
                    )
                )
            ]
            if len(kept) != len(pins):
                store[sid] = kept
                changed = True
        if changed:
            tmp = target.with_name(target.name + ".tmp")
            tmp.write_text(_json.dumps(store, ensure_ascii=False), encoding="utf-8")
            import os as _os

            _os.replace(tmp, target)
    return _SWEEP_STEP_OK


def _sweep_step_liquefy_vault(finalization_id: str, content_hash: str, plaintext: str = "") -> str:
    import shutil

    from core.liquefy_bridge import _vault_dir

    bundles = _vault_dir("bundles")
    if not bundles.exists():
        return _SWEEP_STEP_OK
    import json as _json

    for bundle_dir in sorted(bundles.glob("*")):
        if not bundle_dir.is_dir():
            # PASS003 (CE10): packed FILE bundles (task-*.gz / task-*.zst)
            # produced by the product's own export fallback are governed
            # derivatives in their actual supported form. Open them with the
            # product loader semantics and delete any bundle that is
            # lineage/hash-matched OR carries the plaintext at all.
            if _liquefy_packed_file_governed(
                bundle_dir, finalization_id, content_hash, plaintext
            ):
                try:
                    bundle_dir.unlink()
                except OSError:
                    return f"{_SWEEP_STEP_ERROR_PREFIX}packed_bundle_delete_failed"
                continue
            # A packed file we cannot even classify is an unknown derivative:
            # honest non-completion beats a fictional COMPLETED.
            return f"{_SWEEP_STEP_ERROR_PREFIX}packed_bundle_unclassifiable"
        plain_bundle = bundle_dir / "task_bundle.json"
        if not plain_bundle.exists():
            # Packed/compressed fallback artifact (CE, pass-002 T14): it is a
            # governed derivative that cannot be inspected as-is. It cannot be
            # matched by lineage or hash, so a complete report would be
            # fiction — delete the governed artifact and report honestly.
            shutil.rmtree(bundle_dir, ignore_errors=True)
            continue
        try:
            bundle = _json.loads(plain_bundle.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        final = bundle.get("final_response") if isinstance(bundle, dict) else None
        final_hash = str((final or {}).get("content_hash") or "")
        if (
            str((bundle or {}).get("source_finalization_id") or "") == finalization_id
            or (final_hash and final_hash == content_hash)
            or (plaintext and plaintext in plain_bundle.read_text(encoding="utf-8", errors="replace"))
        ):
            shutil.rmtree(bundle_dir, ignore_errors=True)
    return _SWEEP_STEP_OK


def _liquefy_packed_file_governed(
    path: Any, finalization_id: str, content_hash: str, plaintext: str
) -> bool:
    """True when a packed bundle FILE carries the governed payload — decided
    via the PRODUCT loader semantics, never raw-grep."""
    from core.liquefy_bridge import load_packed_bytes

    suffix = path.suffix.lower()
    backend = {".zst": "liquefy", ".zstd": "liquefy", ".gz": "gzip", ".gzip": "gzip"}.get(suffix)
    if backend is None:
        return False
    try:
        decompressed = load_packed_bytes(payload=path.read_bytes(), storage_backend=backend)
    except Exception:
        raise  # unsupported/corrupt: caller reports non-complete honestly
    text = decompressed.decode("utf-8", errors="replace")
    if finalization_id and f'"{finalization_id}"' in text:
        return True
    if plaintext and plaintext in text:
        return True
    bare = content_hash[len("sha256:"):] if content_hash.startswith("sha256:") else content_hash
    return bool(bare) and bare in text


def _sweep_step_adaptation_corpora(request_id: str, content_hash: str) -> str:
    import json as _json

    from core.runtime_paths import data_path

    corpora_dir = data_path("adaptation", "corpora")
    if not corpora_dir.exists():
        return _SWEEP_STEP_OK
    for path in sorted(corpora_dir.glob("*.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        kept_lines: list[str] = []
        changed = False
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                item = _json.loads(stripped)
            except ValueError:
                kept_lines.append(line)
                continue
            metadata = (item or {}).get("metadata") or {}
            if str(metadata.get("request_id") or "") == request_id or _text_hash_matches(
                str((item or {}).get("output") or ""), content_hash
            ):
                changed = True
                continue
            kept_lines.append(line)
        if changed:
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(
                "".join(line + "\n" for line in kept_lines), encoding="utf-8"
            )
            import os as _os

            _os.replace(tmp, path)
    return _SWEEP_STEP_OK


def _local_mirror_dirs() -> list:
    import os as _os
    from pathlib import Path

    candidates: list[Path] = []
    env_dir = _os.environ.get("VOOL_MIRROR_DATA_DIR")
    if env_dir:
        candidates.append(Path(env_dir))
    default = Path("relay_mirror")
    if default.exists():
        candidates.append(default)
    out: list[Path] = []
    for item in candidates:
        if item.is_dir() and item not in out:
            out.append(item)
    return out


def _sweep_step_mirror_snapshots(finalization_id: str, content_hash: str) -> str:
    import json as _json
    import os as _os

    outcome = _SWEEP_STEP_OK
    mirror_url = _os.environ.get("VOOL_MIRROR_URL") or ""
    if mirror_url and "127.0.0.1" not in mirror_url and "localhost" not in mirror_url:
        # Off-host mirror retention is honestly UNKNOWN: no deletion may be
        # claimed for a third party (EXTERNAL_ERASURE_UNKNOWN lane).
        outcome = "external_mirror_erasure_unknown"
    for base in _local_mirror_dirs():
        for path in sorted(base.glob("*.json")):
            try:
                snapshot = _json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            records = snapshot.get("records") if isinstance(snapshot, dict) else None
            if not isinstance(records, list):
                continue
            kept = [
                r
                for r in records
                if not (
                    str((r or {}).get("finalization_id") or "") == finalization_id
                    or _text_hash_matches(str((r or {}).get("content") or ""), content_hash)
                )
            ]
            if len(kept) != len(records):
                if kept:
                    snapshot["records"] = kept
                    snapshot["record_count"] = len(kept)
                    tmp = path.with_name(path.name + ".tmp")
                    tmp.write_text(
                        _json.dumps(snapshot, sort_keys=True, indent=2), encoding="utf-8"
                    )
                    _os.replace(tmp, path)
                else:
                    path.unlink(missing_ok=True)
    return outcome


def availability_for_receipt_digest(digest: str) -> str | None:
    """A8 serve-time verdict for a receipt's stored prompt/response hash
    (PASS003 CE08). The at-rest value is KEYED (HMAC under the A8 key), so:
    - legacy unsalted ``sha256:<hex>`` values consult rows/tombstones exactly
      like payload_availability_for_hash;
    - keyed values match erasure tombstones directly (same keying scheme);
    - None means no governed truth for this digest."""
    clean = str(digest or "").strip()
    if not clean:
        return None
    conn = get_connection()
    try:
        _migrate_legacy_digest_tombstones(conn)
        if clean == "erasure_digest_suppressed":
            return AVAILABILITY_ERASED
        if not clean.startswith("keyed-sha256:"):
            return payload_availability_for_hash(clean)
        row = conn.execute(
            "SELECT 1 FROM a7_governance_events "
            "WHERE event_kind = ? AND reason = ? LIMIT 1",
            (EVENT_KIND_ERASURE_DIGEST_TOMBSTONE, clean),
        ).fetchone()
        if row is not None:
            return AVAILABILITY_ERASED
        return None
    finally:
        conn.close()


def _sweep_step_honesty_receipts(content_hash: str, plaintext: str = "") -> str:
    """PASS003 (CE08): the receipt ledger must not retain an UNsalted
    confirmation oracle for erased payload bytes (offline dictionary attack
    proven by independent reproof). Suppression rewrites ONLY receipts whose
    stored digest identifies the erased payload — replacing the oracle with
    an explicit suppression marker; integrity of rewritten rows is honestly
    broken (the chain verifier will flag them) rather than faked, because
    strict erasure dominates retained confirmation metadata. Receipts whose
    digests are already keyed match nothing and stay intact.
    Returns error outcome on unreadable governed artifacts (never COMPLETED
    fiction)."""
    import json as _json

    from core.runtime_paths import data_path

    ledger_dir = data_path("honesty_receipts")
    if not ledger_dir.is_dir():
        return _SWEEP_STEP_OK
    bare = content_hash[len("sha256:"):] if content_hash.startswith("sha256:") else content_hash
    oracle_forms = {f"sha256:{bare}", bare} if bare else set()
    if plaintext:
        try:
            oracle_forms.add(_keyed_tombstone_value(_sha256_hex(plaintext)))
        except Exception:
            pass
    changed_any = False
    for path in sorted(ledger_dir.glob("*.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return f"{_SWEEP_STEP_ERROR_PREFIX}receipt_ledger_unreadable"
        kept: list[str] = []
        changed = False
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            hit = any(form and form in stripped for form in oracle_forms)
            # A keying of the erased payload text itself also identifies it.
            if not hit and plaintext:
                try:
                    item = _json.loads(stripped)
                    hit = item.get("response_hash") == _keyed_tombstone_value(
                        _sha256_hex(plaintext)
                    ) or item.get("prompt_hash") == _keyed_tombstone_value(
                        _sha256_hex(plaintext)
                    )
                except ValueError:
                    hit = False
            if hit:
                try:
                    item = _json.loads(stripped)
                    # The oracle digests AND their payload-derived free text
                    # are both governed: suppressing one while serving the
                    # other would keep the erasure fiction alive.
                    item["response_hash"] = "erasure_digest_suppressed"
                    item["prompt_hash"] = "erasure_digest_suppressed"
                    item["verdict_detail"] = ""
                    item["claimed_actions"] = []
                    item["digest_suppressed_by_erasure"] = True
                    kept.append(_json.dumps(item, sort_keys=True, separators=(",", ":")))
                except ValueError:
                    kept.append(stripped)
                changed = True
                changed_any = True
                continue
            kept.append(line)
        if changed:
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(
                "".join(line + "\n" for line in kept), encoding="utf-8"
            )
            import os as _os

            _os.replace(tmp, path)
    return _SWEEP_STEP_OK


def _sweep_step_capsules() -> str:
    # Capsule versions are immutable by trigger law and are shadow-mode (no
    # production prompt/serve reader); they carry write-time lineage stamps
    # going forward. Honest outcome — no untruthful "removed" claim.
    return "shadow_capsule_immutable_not_served"


def _sweep_step_operator_profile(request_id: str, content_hash: str, plaintext: str = "") -> str:
    from core.operator_profile import erase_governed_items

    outcome = erase_governed_items(request_id, content_hash, plaintext)
    return _SWEEP_STEP_OK if str(outcome or "").startswith("ok") else f"{_SWEEP_STEP_ERROR_PREFIX}{outcome}"


def _run_derivative_sweep(
    finalization_id: str,
    request_id: str,
    content_hash: str,
    plaintext: str = "",
) -> dict[str, str]:
    steps: list[tuple[str, Any]] = [
        ("conversation_log", lambda: _sweep_step_conversation_log(request_id, content_hash)),
        ("memory_jsonl", lambda: _sweep_step_memory_jsonl(request_id)),
        ("dialogue_tables", lambda: _sweep_step_dialogue_tables(request_id, content_hash)),
        (
            "semantic_nodes",
            lambda: _sweep_step_semantic_nodes(request_id, content_hash, plaintext),
        ),
        (
            "memory_blocks",
            lambda: _sweep_step_memory_blocks(finalization_id, content_hash, plaintext),
        ),
        ("runtime_checkpoints", lambda: _sweep_step_checkpoints(content_hash, plaintext)),
        (
            "checkpoint_evidence",
            lambda: _sweep_step_checkpoint_evidence(content_hash, plaintext),
        ),
        ("finalized_responses", lambda: _sweep_step_finalized_responses(finalization_id, content_hash)),
        ("useful_outputs", lambda: _sweep_step_useful_outputs(finalization_id, content_hash)),
        ("message_pins", lambda: _sweep_step_pins(finalization_id, content_hash)),
        ("liquefy_vault", lambda: _sweep_step_liquefy_vault(finalization_id, content_hash, plaintext)),
        ("adaptation_corpora", lambda: _sweep_step_adaptation_corpora(request_id, content_hash)),
        ("mirror_snapshots", lambda: _sweep_step_mirror_snapshots(finalization_id, content_hash)),
        ("task_hive_bodies", lambda: _sweep_step_task_result_bodies(content_hash, plaintext)),
        (
            "runtime_events",
            lambda: _sweep_step_runtime_events(
                content_hash, plaintext, finalization_id
            ),
        ),
        ("honesty_receipts", lambda: _sweep_step_honesty_receipts(content_hash, plaintext)),
        ("context_capsules", _sweep_step_capsules),
        # Operator Profile (P1): items lineaged to the erased request, or whose
        # value is/contains the erased plaintext, are tombstoned and their
        # history snapshots blanked (erasure keeps no plaintext anywhere).
        ("operator_profile", lambda: _sweep_step_operator_profile(request_id, content_hash, plaintext)),
    ]
    outcomes: dict[str, str] = {}
    failed = False
    # ERASE dominates late writers: no in-flight writer that consults this
    # lock can slip a durable write between its eligibility check and commit
    # while the traversal runs (mirror publish TOCTOU closure).
    with A8_TRAVERSAL_LOCK:
        for store_name, step in steps:
            try:
                outcome = str(step() or _SWEEP_STEP_OK)
            except Exception as exc:  # honest partial state; resumable, never fiction
                outcome = f"{_SWEEP_STEP_ERROR_PREFIX}{type(exc).__name__}"
                failed = True
            outcomes[store_name] = outcome
            _sweep_event(finalization_id, store_name, outcome)
    if not failed:
        # The complete marker exists ONLY when every store step actually
        # completed — an erasure event without traversal is not erasure.
        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO a7_governance_events (
                    event_id, finalization_id, event_kind, previous_state,
                    new_state, reason, actor, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"gov-{finalization_id}-sweep-complete-{_utcnow()}",
                    finalization_id,
                    EVENT_KIND_ERASURE_SWEEP_COMPLETE,
                    "",
                    AVAILABILITY_ERASED,
                    "",
                    "a8_traversal",
                    _utcnow(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
    return outcomes


def _pre_erasure_hash_for(finalization_id: str) -> str:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT content_hash FROM a7_finalizations WHERE finalization_id = ?",
            (finalization_id,),
        ).fetchone()
        stored = str(row["content_hash"] or "") if row is not None else ""
        if stored and not stored.startswith("salted-sha256:"):
            return stored
        tomb = conn.execute(
            "SELECT reason FROM a7_governance_events "
            "WHERE event_kind = ? AND finalization_id = ? ORDER BY created_at DESC LIMIT 1",
            (EVENT_KIND_ERASURE_DIGEST_TOMBSTONE, finalization_id),
        ).fetchone()
        return str(tomb["reason"] or "") if tomb is not None else ""
    finally:
        conn.close()


def erase_finalization_payload(
    finalization_id: str,
    *,
    reason: str = "",
    governance_actor: str = "",
) -> dict[str, Any]:
    """A8 canonical erasure: availability transition + derivative traversal.

    Idempotent and crash-safe: re-running after a partial traversal re-runs the
    (individually idempotent) store steps and only then records completion.
    Returns the transition + per-store sweep outcomes.
    """
    clean_fid = str(finalization_id or "").strip()
    if not clean_fid:
        return {"finalization_id": "", "transitioned": False, "outcome": "invalid_id"}
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT availability, request_id FROM a7_finalizations WHERE finalization_id = ?",
            (clean_fid,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return {
            "finalization_id": clean_fid,
            "transitioned": False,
            "outcome": "unknown_finalization",
        }
    request_id = str(row["request_id"] or "")
    current = str(row["availability"] or "")
    content_hash = _pre_erasure_hash_for(clean_fid)
    # PASS003: capture the governed plaintext BEFORE the transition clears it
    # so derivative sweeps can also purge text that merely CONTAINS these
    # bytes (derived facts, block lines, shadow JSON copies). Empty after the
    # first pass — removal is idempotent, resume does not need it.
    plaintext = _pre_erasure_plaintext_for(clean_fid)
    if current == AVAILABILITY_ERASED:
        # Already erased: resume/complete the traversal honestly (idempotent).
        outcomes = _run_derivative_sweep(clean_fid, request_id, content_hash, plaintext)
        return {
            "finalization_id": clean_fid,
            "transitioned": True,
            "already_erased": True,
            "sweep": outcomes,
            "sweep_complete": all(
                not v.startswith(_SWEEP_STEP_ERROR_PREFIX) for v in outcomes.values()
            ),
        }
    # PASS-002 duplicate-ID law: request_id is not a unique key across every
    # writer; if additional governed rows share this identifier, "newest row
    # wins" would leave an older secret-bearing row AVAILABLE. The privacy
    # operation applies to the COMPLETE governed set sharing the identifier.
    sibling_ids: list[str] = []
    if request_id:
        conn_sib = get_connection()
        try:
            sib_rows = conn_sib.execute(
                "SELECT finalization_id FROM a7_finalizations "
                "WHERE request_id = ? AND finalization_id <> ? "
                "AND availability IN (?, ?, ?)",
                (
                    request_id,
                    clean_fid,
                    AVAILABILITY_AVAILABLE,
                    AVAILABILITY_WITHHELD,
                    AVAILABILITY_LEGACY_UNKNOWN,
                ),
            ).fetchall()
            sibling_ids = [str(r["finalization_id"]) for r in sib_rows]
        finally:
            conn_sib.close()
    transitioned = set_availability(
        clean_fid,
        AVAILABILITY_ERASED,
        reason=reason,
        governance_actor=governance_actor,
    )
    if not transitioned:
        return {
            "finalization_id": clean_fid,
            "transitioned": False,
            "outcome": "refused_transition",
            "availability": current,
        }
    outcomes = _run_derivative_sweep(clean_fid, request_id, content_hash, plaintext)
    result = {
        "finalization_id": clean_fid,
        "transitioned": True,
        "availability": AVAILABILITY_ERASED,
        "sweep": outcomes,
        "sweep_complete": all(
            not v.startswith(_SWEEP_STEP_ERROR_PREFIX) for v in outcomes.values()
        ),
    }
    for sibling_fid in sibling_ids:
        sibling_result = erase_finalization_payload(
            sibling_fid, reason=f"{reason or 'erase'} (duplicate-request-id set)", governance_actor=governance_actor
        )
        for store_name, value in dict(sibling_result.get("sweep") or {}).items():
            outcomes.setdefault(f"{store_name}[dup:{sibling_fid[:8]}]", value)
            if str(value).startswith(_SWEEP_STEP_ERROR_PREFIX):
                result["sweep_complete"] = False
        if not sibling_result.get("transitioned"):
            result["sweep_complete"] = False
    return result


def erasure_sweep_status(finalization_id: str) -> dict[str, Any]:
    """Observable traversal state for one finalization (no new authority —
    a read over the governance ledger)."""
    clean_fid = str(finalization_id or "").strip()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT availability FROM a7_finalizations WHERE finalization_id = ?",
            (clean_fid,),
        ).fetchone()
        availability = str(row["availability"] or "") if row is not None else ""
        events = conn.execute(
            "SELECT event_kind, store_name, sweep_outcome, created_at "
            "FROM a7_governance_events WHERE finalization_id = ? "
            "ORDER BY created_at ASC",
            (clean_fid,),
        ).fetchall()
    finally:
        conn.close()
    stores: dict[str, str] = {}
    complete = False
    for event in events:
        kind = str(event["event_kind"] or "")
        if kind == EVENT_KIND_ERASURE_SWEEP and event["store_name"]:
            stores[str(event["store_name"])] = str(event["sweep_outcome"] or "")
        elif kind == EVENT_KIND_ERASURE_SWEEP_COMPLETE:
            complete = True
    return {
        "finalization_id": clean_fid,
        "availability": availability,
        "erased": availability == AVAILABILITY_ERASED,
        "sweep_complete": complete,
        "stores": stores,
    }


def resume_incomplete_erasure_sweeps() -> list[str]:
    """Re-run traversal for every ERASED finalization lacking a complete sweep.

    Called after a crash mid-traversal (and safe to call at any time): each
    store step is idempotent, so resumption converges without double-erasing.
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT finalization_id FROM a7_finalizations WHERE availability = ?",
            (AVAILABILITY_ERASED,),
        ).fetchall()
        incomplete = [
            str(row["finalization_id"])
            for row in rows
            if not erasure_sweep_status(str(row["finalization_id"]))["sweep_complete"]
        ]
    finally:
        conn.close()
    for fid in incomplete:
        erase_finalization_payload(fid, reason="a8_resume_incomplete_sweep")
    return incomplete
