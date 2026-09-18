"""What a conductor run observed, in the one shape the canonical evidence authority reads.

The defect this module exists to close
--------------------------------------
Found by human test at e9d15df0, and it is the same invariant as the failed-retrieval repair
approached from the other side::

    U: Search live web for current BTC price and Gold price, then calculate how many
       ounces of Gold equal one BTC.

    conductor plan: market_quote:bitcoin SUCCEEDED
                    market_quote:gold    SUCCEEDED
                    quantitative_reasoning SUCCEEDED      -> 3/3 succeeded
    visible answer: "I didn't run any live lookup on this turn..."

Two live quotes really were fetched and the runtime told the user it had looked nothing up. A
guard can only respect evidence it can SEE, and the conductor put its observations nowhere any
reader consults:

* nothing in the whole `core/conductor` package writes to `runtime_tool_observations` or to any
  retrieval-receipt channel; and
* it could not have, because `apps.vool_agent` hands `NodeContext` a
  ``source_context=dict(source_context or {})`` -- a shallow COPY -- so anything a node wrote would
  land on a detached dict and never reach the turn.

So the evidence is derived HERE, from the typed outcomes the run returns, and published by the
dispatch site where the turn's real context is in scope -- the same shape and the same reason as
`core.live_data_retrieval_receipts.publish_live_data_retrieval_receipts`.

Which nodes count, and why there is no operation list
-----------------------------------------------------
Not every node that succeeds observed the world. An arithmetic result is not evidence; neither is a
model's prose. The discriminator is the **effect the operation already declares** in its own
`OperationCapability` -- `LIVE_OBSERVATION` for the lanes that reach a provider (weather_lookup,
market_quote, fx_quote, place_search, structured_research) and `WORKSPACE_EVIDENCE` for the ones
that read this machine (workspace_investigation, conclusion). Everything else --
`COMPUTED_VALUE`, `DERIVED_ANALYSIS`, `KNOWLEDGE_ANSWER`, `GENERATED_CONTENT`, `ACTION_STATUS`,
`MISSING_INFORMATION` -- is reasoning over facts, not an observation of them.

Reading the declared effect rather than matching operation names is what makes this general: a new
operation registered tomorrow gets the correct evidence behaviour from the capability it must
declare anyway, and no BTC/gold/weather vocabulary appears here at all.

Success is `NodeOutcome.succeeded`, which is already the strict form -- SUCCEEDED *and* every
`required_result_fields` entry present. A node whose adapter returned a partial shape has not
observed anything usable, and this module inherits that judgement rather than re-deriving a looser
one.

A failed observation node is recorded too, as ``ok=False``. That is truthful about what was
attempted and, by `core.observation_evidence`, worth exactly nothing as evidence -- which is the
other half of the same contract and the direction the earlier repair closed.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from core.conductor.capabilities import OperationEffect

#: Effects that mean the node OBSERVED something outside the model. Everything else reasons over
#: facts rather than acquiring them, and reasoning is never evidence for a factual claim.
_OBSERVATION_EFFECTS = frozenset(
    {OperationEffect.LIVE_OBSERVATION, OperationEffect.WORKSPACE_EVIDENCE}
)

#: Bound on the result VALUES rendered into an evidence row. The rendered node line plus
#: these values are what a claim is matched against; the bound keeps one verbose operation
#: from evicting its siblings out of a bounded channel.
_RESULT_VALUES_CHARS = 1600


def observation_event_payload(result: Any) -> dict[str, Any]:
    """The identity a node-completion event needs for its result to be bound as evidence.

    Generic by design -- no operation names, no effect knowledge: the VALUES the node
    observed (bounded), and the source it named, when the result carries one. The
    completion-event binding and the post-plan binding read the SAME fields through the
    SAME projection, so one plan's observations mint ONE evidence-set id -- not one
    while nodes are running and a different one after the plan, which would leave every
    answering generation call referencing a set the record no longer holds.
    """
    if not isinstance(result, dict) or not result:
        return {}
    payload: dict[str, Any] = {"values": _observed_values(result)}
    url = str(result.get("source_url") or "").strip()
    if url:
        payload["source_url"] = url
    domain = _result_source_domain(result)
    if domain:
        payload["origin_domain"] = domain
    return {key: value for key, value in payload.items() if value}


def _effect(operation: str) -> OperationEffect | None:
    """The declared effect for `operation`, or None when nothing serves it."""

    try:
        from core.conductor.registry import operation_spec

        spec = operation_spec(operation)
    except Exception:
        return None
    capability = getattr(spec, "capability", None)
    return getattr(capability, "effect", None)


def operation_is_an_observation(operation: str) -> bool:
    """Whether this operation's own declared effect makes it an observation of the world."""

    return _effect(operation) in _OBSERVATION_EFFECTS


def _result_source_domain(result: Any) -> str:
    """The domain of where an observation came from, when the result names it."""

    if not isinstance(result, dict):
        return ""
    for key in ("source_url", "source_domain", "origin_domain"):
        value = str(result.get(key) or "").strip()
        if value:
            domain = value.split("//")[-1].split("/")[0]
            return domain or value
    label = str(result.get("source_label") or "").strip()
    return label


def _observed_values(result: Any) -> str:
    """The result's VALUES, bounded. Never its key names alone.

    The defect this closes, measured served on the isolated daemon: the observation row
    carried ``weather_lookup observed ['condition', 'temperature_c']`` -- the KEYS -- so a
    claim reading "Kaunas 28" had nothing to match against and the publication gate refused
    a turn whose two readings had really been fetched. The values are the observation;
    the keys are its schema.
    """
    if not isinstance(result, dict) or not result:
        return ""
    try:
        text = json.dumps(result, ensure_ascii=False, default=str, sort_keys=True)
    except Exception:
        return ""
    return text[:_RESULT_VALUES_CHARS]


def _row_for(outcome: Any) -> dict[str, Any]:
    """One observation outcome as a note-shaped evidence row with full identity.

    Identity carried per row: the DEMAND it serves (the node's own request text and id --
    what a mixed multi-demand turn's account is kept against), the SOURCE it came from
    (the result's own source_url/source_label, never the operation's name), and the VALUES
    it observed. `summary` carries the node's rendered line because that is the sentence
    the composed answer actually ships -- matching claims against it is matching against
    what the user was told.
    """
    node = getattr(outcome, "node", None)
    operation = str(getattr(node, "operation", "") or "").strip()
    result = getattr(outcome, "result", None)
    rendered = str(getattr(outcome, "rendered", "") or "").strip()
    values = _observed_values(result)
    node_id = str(getattr(node, "node_id", "") or "")
    demand_text = str(getattr(node, "request_text", "") or "").strip()
    ok = bool(getattr(outcome, "succeeded", False))
    domain = _result_source_domain(result) if ok else ""
    return {
        "schema": "tool_observation_v1",
        "intent": f"conductor.{operation}",
        "tool_surface": "web" if _effect(operation) is OperationEffect.LIVE_OBSERVATION else "workspace",
        "ok": ok,
        "status": "executed" if ok else "failed",
        "node_id": node_id,
        "demand_id": node_id,
        "demand_text": demand_text[:200],
        "summary": rendered[:_RESULT_VALUES_CHARS] or values,
        "response_preview": values if ok else (
            f"{operation} failed: {str(getattr(outcome, 'failure_reason', '') or 'no result')[:160]}"
        ),
        "origin_domain": domain,
        "result_url": str(result.get("source_url") or "").strip() if isinstance(result, dict) else "",
    }


def conductor_observations(outcomes: Sequence[Any] | None) -> list[dict[str, Any]]:
    """One same-turn observation entry per conductor node that tried to observe the world.

    `ok` is the node's own strict success. Nodes whose operation does not declare an observing
    effect are absent entirely -- a calculation that succeeded is not evidence that anything was
    looked up, and recording it as one would rebuild the defect this whole contract exists to stop.

    Entries are deduplicated by node id, so a caller that publishes twice cannot make one
    observation count twice. Content is the observed VALUES and the node's own rendered line,
    never the result's key names.
    """

    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for outcome in list(outcomes or []):
        node = getattr(outcome, "node", None)
        operation = str(getattr(node, "operation", "") or "").strip()
        if not operation or not operation_is_an_observation(operation):
            continue
        node_id = str(getattr(node, "node_id", "") or "")
        if node_id and node_id in seen:
            continue
        if node_id:
            seen.add(node_id)
        entries.append(_row_for(outcome))
    return entries


#: The channel on the turn context where this turn's deterministic computations are published.
#: Deliberately NOT `runtime_tool_observations`: that channel's readers answer "did this turn
#: look something up", and a calculation answers a different question (see `conductor_observations`).
RUNTIME_COMPUTED_VALUES_CHANNEL = "runtime_computed_values"


def conductor_computations(outcomes: Sequence[Any] | None) -> list[dict[str, Any]]:
    """This plan's deterministic computations, as support rows of their own kind.

    A succeeded node whose operation declares `COMPUTED_VALUE` produced its numbers in runtime
    code -- the value came out of `evaluate_grounded_expression` or the direct evaluator, not out
    of a model's imagination. Such a value is not an OBSERVATION (nothing was looked up), but it
    is also not an unsupported claim: when a mixed turn's publication is gated because a SIBLING
    clause needed current information, the computed clause's own rendered line must still match
    support, or one clause's ungrounded model prose takes the whole turn's arithmetic down with it
    (measured 2026-09-08, acceptance turn 18: "5 + 5 = 10" died inside a whole-turn grounding
    refusal). Failed nodes publish nothing -- an attempt is not a value, by the same rule
    observations follow.

    The `summary` is the node's rendered line, which is the exact sentence the composed answer
    ships, so claim matching is against what the user was actually told.
    """

    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for outcome in list(outcomes or []):
        node = getattr(outcome, "node", None)
        operation = str(getattr(node, "operation", "") or "").strip()
        if not operation:
            continue
        if not bool(getattr(outcome, "succeeded", False)):
            continue
        try:
            effect = _effect(operation)
        except Exception:
            continue
        if effect is not OperationEffect.COMPUTED_VALUE:
            continue
        node_id = str(getattr(node, "node_id", "") or "")
        if node_id and node_id in seen:
            continue
        if node_id:
            seen.add(node_id)
        rendered = str(getattr(outcome, "rendered", "") or "").strip()
        if not rendered:
            continue
        entries.append(
            {
                "schema": "computed_value_v1",
                "ok": True,
                "node_id": node_id,
                "demand_id": node_id,
                "demand_text": str(getattr(node, "request_text", "") or "").strip()[:200],
                "summary": rendered[:_RESULT_VALUES_CHARS],
            }
        )
    return entries


def publish_conductor_computations(
    source_context: dict[str, Any] | None, outcomes: Sequence[Any] | None
) -> list[dict[str, Any]]:
    """Record this plan's computations on the turn, exactly once, and return what was recorded.

    Idempotent by node id against whatever the channel already holds, mirroring
    `publish_conductor_observations`.
    """

    entries = conductor_computations(outcomes)
    if not entries or not isinstance(source_context, dict):
        return []
    existing = [
        dict(item)
        for item in list(source_context.get(RUNTIME_COMPUTED_VALUES_CHANNEL) or [])
        if isinstance(item, dict)
    ]
    already = {str(item.get("node_id") or "") for item in existing if item.get("node_id")}
    fresh = [entry for entry in entries if str(entry.get("node_id") or "") not in already]
    if not fresh:
        return []
    source_context[RUNTIME_COMPUTED_VALUES_CHANNEL] = (existing + fresh)[-32:]
    return fresh


#: The channel where this turn's stable-knowledge renders are published. This channel is
#: DELIBERATELY NOT SUPPORT. A computed value's row may back a claim because runtime code
#: produced the number; a knowledge node's render was written by a MODEL, and the model's own
#: output may never certify itself (false absolution — the whole lane exists to refuse that).
#: The publication gate consumes this channel as a per-claim EXEMPTION on the plan's typed
#: authority alone. See FINDINGS F43 for the served defect that introduced it.
RUNTIME_STABLE_KNOWLEDGE_CHANNEL = "runtime_stable_knowledge"


def conductor_stable_knowledge(outcomes: Sequence[Any] | None) -> list[dict[str, Any]]:
    """This plan's open-authority knowledge renders, as plan-authority exemption records.

    A succeeded KNOWLEDGE_ANSWER node is the runtime's typed decision that this clause is a
    knowledge question — the same family a DIRECT knowledge turn answers with no grounding
    lifecycle at all. When a SIBLING clause needed current information and opened this
    lifecycle, the knowledge clause's own rendered line must not drown with it (measured on
    build 07aaaede: "The Berlin Wall fell in 1989." and "The novel 1984 was written by Orwell"
    generated correctly and then shipped inside the withheld-work notice).

    Failed nodes publish nothing. The node's numeric AUTHORITY (open vs closed) is not a
    filter here: it classifies the REQUEST's numeric shape ("1984" in a title makes a clause
    closed), while every protection the exemption actually needs is enforced per CLAIM at the
    gate — introduced non-year numerics, currency and current-truth markers, freshness cues,
    containment in this render, and the authorship policy's verdict for the call that served
    it. The channel records; the gate refuses.
    """

    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for outcome in list(outcomes or []):
        node = getattr(outcome, "node", None)
        operation = str(getattr(node, "operation", "") or "").strip()
        if not operation:
            continue
        if not bool(getattr(outcome, "succeeded", False)):
            continue
        try:
            effect = _effect(operation)
        except Exception:
            continue
        if effect is not OperationEffect.KNOWLEDGE_ANSWER:
            continue
        node_id = str(getattr(node, "node_id", "") or "")
        if node_id and node_id in seen:
            continue
        if node_id:
            seen.add(node_id)
        rendered = str(getattr(outcome, "rendered", "") or "").strip()
        if not rendered:
            continue
        entries.append(
            {
                "schema": "stable_knowledge_v1",
                "ok": True,
                "node_id": node_id,
                "demand_id": node_id,
                "demand_text": str(getattr(node, "request_text", "") or "").strip()[:200],
                "summary": rendered[:_RESULT_VALUES_CHARS],
            }
        )
    return entries


def publish_conductor_stable_knowledge(
    source_context: dict[str, Any] | None, outcomes: Sequence[Any] | None
) -> list[dict[str, Any]]:
    """Record this plan's stable-knowledge renders on the turn, exactly once.

    Idempotent by node id, mirroring `publish_conductor_computations`. Each entry is then
    enriched with the authorship-policy verdict for the generation call that served its node,
    joined by the clause the call's briefing embeds: the publisher asks the turn's
    `conductor_generation_authorship` log (written by `build_conductor_ask_model`, whose
    verdicts are `core.final_answer_authorship.decide_final_answer_author`'s own) for an
    ELIGIBLE call whose prompt carries this node's demand text. The join fails CLOSED — no
    matching verdict leaves the entry unmarked, and an unmarked entry can never exempt a
    claim. A wrong join cannot grant anything; it can only withhold.
    """

    entries = conductor_stable_knowledge(outcomes)
    if not entries or not isinstance(source_context, dict):
        return []
    verdicts = [
        dict(item)
        for item in list(source_context.get("conductor_generation_authorship") or [])
        if isinstance(item, dict)
    ]
    existing = [
        dict(item)
        for item in list(source_context.get(RUNTIME_STABLE_KNOWLEDGE_CHANNEL) or [])
        if isinstance(item, dict)
    ]
    already = {str(item.get("node_id") or "") for item in existing if item.get("node_id")}
    fresh = []
    for entry in entries:
        if str(entry.get("node_id") or "") in already:
            continue
        demand_text = " ".join(
            str(entry.get("demand_text") or "").split()
        )
        verdict = next(
            (
                item
                for item in verdicts
                if item.get("eligible")
                and demand_text
                and demand_text in " ".join(str(item.get("prompt") or "").split())
            ),
            None,
        )
        if verdict is not None:
            entry["author_eligible"] = True
            entry["author"] = str(verdict.get("provider_id") or "")
        fresh.append(entry)
    if not fresh:
        return []
    source_context[RUNTIME_STABLE_KNOWLEDGE_CHANNEL] = (existing + fresh)[-32:]
    return fresh


def conductor_evidence_rows(outcomes: Sequence[Any] | None) -> list[dict[str, Any]]:
    """The SUCCEEDED observation rows, projected into the note shape M2's binder takes.

    Failed observations bind nothing -- an attempt is not an observation, by the same rule
    `core.observation_evidence` applies everywhere else. Each row carries the demand it
    serves and the source it came from, so the bound set answers "which demand did which
    source support" instead of only "did this turn retrieve anything".
    """

    return [row for row in conductor_observations(outcomes) if row.get("ok")]


def _bind_rows(
    source_context: dict[str, Any] | None,
    rows: list[dict[str, Any]],
    *,
    query: str = "",
) -> dict[str, Any]:
    """Mint and bind already-projected rows onto the turn's grounding lifecycle.

    The conductor's producer→synthesis boundary. M2's own contract is used verbatim --
    `mint_evidence_set`, `binding_record`, `emit_evidence_bound`, `record_bound` -- so the
    ids, the turn scope and the `proves="prompt_entry"` claim are identical to the grounded
    lane's. Returns the binding record, or {} when there is nothing to bind or no lifecycle
    to bind onto (a turn M1 never marked current-information has no row, which is the
    DIRECT/timeless case and must stay untouched).
    """
    if not rows or not isinstance(source_context, dict):
        return {}
    try:
        from core.grounded_synthesis_binding import (
            binding_record,
            emit_evidence_bound,
            mint_evidence_set,
            turn_scope,
        )
        from core.grounding_lifecycle import record_bound

        scope = turn_scope(source_context)
        evidence_set = mint_evidence_set(
            rows,
            scope=scope,
            query=str(query or ""),
            demand_id=",".join(
                sorted({str(row.get("demand_id") or "") for row in rows if row.get("demand_id")})
            )[:120],
            demand_text=str(rows[0].get("demand_text") or "") if len(rows) == 1 else "",
        )
        record = binding_record(evidence_set, model_call_stage="conductor_observation")
        emit_evidence_bound(source_context, record)
        accepted = record_bound(source_context, binding=record, notes=rows, scope=scope)
        return dict(record) if accepted else {}
    except Exception:
        return {}


def bind_conductor_evidence(
    source_context: dict[str, Any] | None,
    outcomes: Sequence[Any] | None,
    *,
    query: str = "",
) -> dict[str, Any]:
    """Bind this plan's SUCCEEDED observation rows as the turn's evidence set."""
    return _bind_rows(source_context, conductor_evidence_rows(outcomes), query=query)


def bind_evidence_on_node_completion(
    emit: Any,
    *,
    source_context: dict[str, Any] | None,
    plan: Any,
    query: str = "",
):
    """Wrap a node-event emitter so evidence binds INCREMENTALLY, as observations land.

    Why incremental: a conductor plan's generation nodes run in dependency waves, and a
    node that reasons over what an earlier node observed is ANSWERING with that evidence.
    A binding minted only after the whole plan finishes would post-date every generation
    call, so no answering call could ever have carried it -- the record would assert
    prompt entry no prompt ever saw. Binding as each observation completes means the
    envelope is already on the turn context (the same dict `build_conductor_ask_model`
    copies at call time) when a later wave's generation node invokes its model.

    The wrapper never lets binding affect the work: every exception is swallowed, the
    underlying emitter always runs, and its return value is passed through untouched --
    the same discipline `core.conductor.scheduler._emit` already enforces.
    """
    rows: list[dict[str, Any]] = []
    seen_nodes: set[str] = set()
    demand_by_node = {
        str(getattr(node, "node_id", "") or ""): str(getattr(node, "request_text", "") or "")[:200]
        for node in list(getattr(plan, "nodes", ()) or [])
    }

    def _wrapped(event_type: str, detail: dict[str, Any]):
        try:
            if (
                str(event_type or "") == "agent_node_completed"
                and isinstance(detail, dict)
                and bool(detail.get("ok"))
                and operation_is_an_observation(str(detail.get("operation") or ""))
            ):
                node_id = str(detail.get("node_id") or "")
                if node_id and node_id not in seen_nodes:
                    seen_nodes.add(node_id)
                    row = _completion_event_row(detail, demand_by_node)
                    if row:
                        rows.append(row)
                        _carry_lifecycle_id(source_context)
                        record = _bind_rows(source_context, rows, query=query)
                        if record and isinstance(source_context, dict):
                            source_context["evidence_synthesis_binding"] = record
        except Exception:
            pass
        return emit(event_type, detail)

    return _wrapped


def _completion_event_row(
    detail: dict[str, Any], demand_by_node: dict[str, str]
) -> dict[str, Any] | None:
    """One evidence row from a node-completion event, or None when it carries no content.

    Field-for-field the shape `conductor_evidence_rows` builds from the outcome, read from
    the event's own payload -- same projection, same digests, one evidence-set id for one
    plan whether the row arrived as a completion event or as a final outcome.
    """
    rendered = str(detail.get("rendered") or "").strip()
    observed = detail.get("observed") if isinstance(detail.get("observed"), dict) else {}
    values = str(observed.get("values") or "").strip()
    if not rendered and not values:
        return None
    node_id = str(detail.get("node_id") or "")
    operation = str(detail.get("operation") or "")
    return {
        "schema": "tool_observation_v1",
        "intent": f"conductor.{operation}",
        "tool_surface": "web" if _effect(operation) is OperationEffect.LIVE_OBSERVATION else "workspace",
        "ok": True,
        "status": "executed",
        "node_id": node_id,
        "demand_id": node_id,
        "demand_text": demand_by_node.get(node_id, ""),
        "summary": rendered[:_RESULT_VALUES_CHARS] or values,
        "response_preview": values,
        "origin_domain": str(observed.get("origin_domain") or ""),
        "result_url": str(observed.get("source_url") or ""),
    }


def _carry_lifecycle_id(source_context: dict[str, Any]) -> None:
    """Find this turn's lifecycle row and stamp its id onto this working copy.

    `record_bound` and `record_synthesis_call` both resolve the row by the id stamped IN
    the context dict. The conductor's dispatch seam works on a deadline-bound COPY that
    may predate M1's first consultation, so the id can be missing here even though the
    turn has a row. Without this, every binding write on the copy silently drops.
    """
    try:
        from core.grounding_lifecycle import adopt_lifecycle_id

        adopt_lifecycle_id(source_context)
    except Exception:
        return


def publish_conductor_observations(
    source_context: dict[str, Any] | None, outcomes: Sequence[Any] | None
) -> list[dict[str, Any]]:
    """Record this plan's observations on the turn, exactly once, and return what was recorded.

    Idempotent by node id against whatever the channel already holds, so a retry or a second call
    for the same plan cannot inflate the turn's evidence.
    """

    entries = conductor_observations(outcomes)
    if not entries or not isinstance(source_context, dict):
        return []
    existing = [
        dict(item)
        for item in list(source_context.get("runtime_tool_observations") or [])
        if isinstance(item, dict)
    ]
    already = {str(item.get("node_id") or "") for item in existing if item.get("node_id")}
    fresh = [entry for entry in entries if str(entry.get("node_id") or "") not in already]
    if not fresh:
        return []
    source_context["runtime_tool_observations"] = (existing + fresh)[-32:]
    return fresh


__all__ = [
    "RUNTIME_COMPUTED_VALUES_CHANNEL",
    "RUNTIME_STABLE_KNOWLEDGE_CHANNEL",
    "bind_conductor_evidence",
    "bind_evidence_on_node_completion",
    "conductor_computations",
    "conductor_evidence_rows",
    "conductor_observations",
    "conductor_stable_knowledge",
    "operation_is_an_observation",
    "publish_conductor_computations",
    "publish_conductor_observations",
    "publish_conductor_stable_knowledge",
]
