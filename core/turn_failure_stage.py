"""Which stage a turn actually failed at, as a machine-readable verdict rather than one sentence.

Step 1 of the micro-step debugger. Every user-visible failure in this runtime currently collapses
into a handful of interchangeable sentences:

    "I couldn't get a usable model response in this run"
    "I couldn't produce a normal chat response for that request"
    "model synthesis failed (empty_synthesis)"

Those read identically whether retrieval returned nothing, retrieval returned the wrong domain
entirely, the provider errored, the provider answered and the runtime read the wrong field, a
validator rejected a good answer, or a post-processor deleted it. On 2026-08-05 that ambiguity cost
three separate investigations: an aviation question answered from Apple developer docs, a
"remember these requirements" turn that fell back to a provider-status dump, and a quote whose price
and source were deleted after rendering. In each case the visible text named no stage, so the only
way forward was guessing.

Attribution is not diagnosis, and this module deliberately does not diagnose. It records WHICH
stage ended the turn, from evidence the runtime already has. Whether the model was wrong is a
separate question that a terminal state cannot answer -- and per the review, "the model failed" must
not be claimed until the raw provider envelope proves it. `PROVIDER_ERROR` and
`RESPONSE_EXTRACTION_FAILED` are deliberately distinct for exactly that reason: one says the
provider gave us nothing, the other says it gave us something we failed to read.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TerminalState(str, Enum):
    """The stage a turn ended at. Exactly one is true of any completed turn."""

    SUCCESS = "success"
    OPERATOR_STOPPED = "operator_stopped"              # the operator cancelled this turn; no stage failed
    REQUIRED_TOOLS_NOT_OFFERED = "required_tools_not_offered"  # needed evidence, never got a tool
    RETRIEVAL_EMPTY = "retrieval_empty"                    # searched, nothing came back
    RETRIEVAL_IRRELEVANT = "retrieval_irrelevant"          # results returned, none on topic
    RETRIEVAL_CONTAMINATED = "retrieval_contaminated"      # evidence from another turn/chat/lane
    SYNTHESIS_EMPTY = "synthesis_empty"                    # provider answered, content empty
    RESPONSE_EXTRACTION_FAILED = "response_extraction_failed"  # content existed in a field we skipped
    VALIDATOR_REJECTED = "validator_rejected"              # a real answer refused by a guard
    POSTPROCESSOR_DESTROYED = "postprocessor_destroyed"    # answer arrived, decoration removed it
    PROVIDER_ERROR = "provider_error"                      # transport/HTTP/timeout, no completion
    UNCLASSIFIED = "unclassified"                          # nothing matched -- a gap, and it shows


@dataclass
class StageObservation:
    """What the runtime saw, per stage. Every field is something it already knows."""

    retrieval_attempted: bool = False
    retrieval_result_count: int = 0
    retrieval_on_topic_count: int = 0
    foreign_evidence_ids: list[str] = field(default_factory=list)
    # The operator cancelled the turn (the runtime's own turn_cancelled refusal on a lane
    # attempt). A stop is not a failure of any stage, and text in the terminal payload was
    # never delivered as an answer.
    operator_stop: bool = False
    # What the turn's contract required, versus what the selected lane actually handed it. Set from
    # `ExecutionRequirements`; both default False so an untouched observation cannot trip the check.
    tools_required: bool = False
    tools_offered_count: int = 0
    provider_called: bool = False
    provider_error: str = ""
    raw_content: str = ""
    raw_alternate_fields: dict[str, str] = field(default_factory=dict)
    validator_rejection: str = ""
    text_before_postprocessing: str = ""
    final_text: str = ""


def classify_turn(observation: StageObservation) -> TerminalState:
    """The stage this turn ended at.

    Ordered by how early the stage runs, because the FIRST thing that went wrong is the thing worth
    reporting -- a turn whose evidence was contaminated will also produce a poor answer, and naming
    the poor answer would send the next person to the wrong layer.
    """
    obs = observation

    # Contamination outranks everything: evidence belonging to another turn invalidates the run
    # regardless of how good the output looks. It is the failure most likely to go unnoticed,
    # because a contaminated run can still produce fluent, confident prose.
    if obs.foreign_evidence_ids:
        return TerminalState.RETRIEVAL_CONTAMINATED
    # The operator's stop ended the turn: nothing downstream ran to completion, and any text in
    # the terminal payload was never delivered as an answer. Reporting a stage failure (or
    # success) for a turn the operator stopped sends the next person to a layer that did not
    # fail -- and downstream, a stopped turn must never become evidence about a provider.
    if obs.operator_stop:
        return TerminalState.OPERATOR_STOPPED
    # Ranked directly under contamination and above every retrieval state, because it happens
    # EARLIER than all of them and is invisible in the output: a turn that was never offered a tool
    # produces fluent prose indistinguishable from a researched answer. Reported as
    # `retrieval_empty` it would send the next person to the search layer, which never ran.
    if obs.tools_required and obs.tools_offered_count <= 0:
        return TerminalState.REQUIRED_TOOLS_NOT_OFFERED
    if obs.retrieval_attempted:
        if obs.retrieval_result_count == 0:
            return TerminalState.RETRIEVAL_EMPTY
        if obs.retrieval_on_topic_count == 0:
            return TerminalState.RETRIEVAL_IRRELEVANT
    if obs.provider_error:
        return TerminalState.PROVIDER_ERROR
    if obs.provider_called and not obs.raw_content.strip():
        # The distinction the review insisted on: content elsewhere in the envelope means WE failed
        # to read it, not that the model returned nothing. Never report the model as empty while an
        # unread field holds text.
        if any(str(value or "").strip() for value in obs.raw_alternate_fields.values()):
            return TerminalState.RESPONSE_EXTRACTION_FAILED
        return TerminalState.SYNTHESIS_EMPTY
    if obs.validator_rejection:
        return TerminalState.VALIDATOR_REJECTED
    before = obs.text_before_postprocessing.strip()
    after = obs.final_text.strip()
    if before and not after:
        return TerminalState.POSTPROCESSOR_DESTROYED
    if before and after and len(after) < len(before) * 0.4:
        # The HBAR case: "Hedera Hashgraph is $0.0699 USD ... Source: [CoinGecko](...)" arrived and
        # "24h change: -1.41%." was displayed. Not empty, so nothing flagged it -- yet most of the
        # answer, including the price and the source, was gone.
        return TerminalState.POSTPROCESSOR_DESTROYED
    if after:
        return TerminalState.SUCCESS
    return TerminalState.UNCLASSIFIED


def explain(state: TerminalState) -> str:
    """One line naming the layer to look at. For the trace and the ledger, not for the user."""
    return {
        TerminalState.SUCCESS: "answer delivered",
        TerminalState.OPERATOR_STOPPED: (
            "the operator stopped this turn -- no stage failed; nothing here is evidence about a provider"
        ),
        TerminalState.REQUIRED_TOOLS_NOT_OFFERED: (
            "the request required evidence and the selected lane offered no tool -- check routing, "
            "not retrieval: nothing was searched"
        ),
        TerminalState.RETRIEVAL_EMPTY: "search ran and returned nothing -- check query generation and providers",
        TerminalState.RETRIEVAL_IRRELEVANT: "results returned but none on topic -- check query generation and the relevance gate",
        TerminalState.RETRIEVAL_CONTAMINATED: "evidence from another turn/chat/lane entered this turn -- check evidence ownership",
        TerminalState.SYNTHESIS_EMPTY: "provider answered with no content in any known field",
        TerminalState.RESPONSE_EXTRACTION_FAILED: "the provider returned text the runtime did not read -- check adapter field coverage",
        TerminalState.VALIDATOR_REJECTED: "a real answer was refused by a guard -- check the guard's reason",
        TerminalState.POSTPROCESSOR_DESTROYED: "an answer arrived and decoration removed it -- check the decorator chain",
        TerminalState.PROVIDER_ERROR: "transport or provider fault before any completion",
        TerminalState.UNCLASSIFIED: "no stage matched -- the classifier has a gap, not the runtime",
    }[state]


def observation_from_trace(events: list[dict[str, Any]] | None) -> StageObservation:
    """Best-effort observation from the runtime events a turn already emits.

    Deliberately conservative: an unknown event contributes nothing rather than being guessed at.
    A wrong terminal state is worse than UNCLASSIFIED, because it sends the next person confidently
    to the wrong layer -- which is the failure this whole module exists to end.
    """
    obs = StageObservation()
    for event in list(events or []):
        if not isinstance(event, dict):
            continue
        kind = str(event.get("event_type") or event.get("type") or "").strip().lower()
        message = str(event.get("message") or "")
        details = event.get("details") if isinstance(event.get("details"), dict) else {}
        if kind.startswith("model_lane_failed") or kind.startswith("model_routing_failed"):
            # The runtime's own cancellation refusal on a lane attempt: the operator stopped the
            # turn mid-flight. The error rides top-level, in nested details, or inside the
            # attempt timings the lane failure records.
            texts = [message, str(event.get("error") or ""), str(details.get("error") or "")]
            timings = event.get("attempt_timings") or details.get("attempt_timings")
            if isinstance(timings, list):
                texts.extend(str((item or {}).get("error") or "") for item in timings if isinstance(item, dict))
            if any("turn_cancelled" in text for text in texts):
                obs.operator_stop = True
        if kind.startswith("model.call_started"):
            obs.provider_called = True
        elif kind.startswith("model.call_failed"):
            obs.provider_called = True
            obs.provider_error = message or "model call failed"
        elif kind.startswith("model.call_completed"):
            obs.provider_called = True
            obs.provider_error = ""
        elif "search" in kind or "retrieval" in kind:
            obs.retrieval_attempted = True
            # What the retrieval RETURNED, from the receipts the events already carry. Without this
            # every retrieving turn read `retrieval_empty` ("search ran and returned nothing") --
            # measured 2026-09-06 on a turn whose six bound notes the model had just read.
            if kind == "web_retrieval_completed":
                obs.retrieval_result_count += _receipt_source_count(event)
            elif kind == "evidence_bound_to_synthesis":
                note_ids = event.get("note_ids")
                bound = len(note_ids) if isinstance(note_ids, list) else _as_int(event.get("source_count"))
                obs.retrieval_result_count = max(obs.retrieval_result_count, bound)
    return obs


def _as_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _receipt_source_count(event: dict[str, Any]) -> int:
    """Sources a `web_retrieval_completed` event reports: a flat typed receipt's `source_count`, or
    the sum over the live-data plan's nested `receipts`."""
    if str(event.get("schema") or "") == "vool.web_retrieval_receipt.v1" or ("source_count" in event and not isinstance(event.get("receipts"), list)):
        return _as_int(event.get("source_count"))
    nested = event.get("receipts")
    if isinstance(nested, list):
        return sum(_as_int(item.get("source_count")) for item in nested if isinstance(item, dict))
    return 0
