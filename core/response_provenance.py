"""What produced this answer — one line, on every response, true of the lane that actually served it.

The defect this module exists to close
--------------------------------------
Live, 2026-08-11. An ordinary chat reply carried a footer::

    local | qwen2.5:7b | 930 tok

The very next turn, an exact file write, rendered its result table and nothing else::

    Files — workspace root
    | exact_one_file_6204.txt | 1 | written |
    Scope... Subject... Tests...

Same session, same surface, and the user could not tell from the second answer whether a model had
been involved at all, which one, or what it cost. Not because the runtime lost that fact — it holds
it, on the result — but because the only thing appending a footer was
`core.web.api.runtime._format_usage_footer`, which is a USAGE footer: it reads
`get_turn_usage()` and returns "" whenever the token total is zero. A deterministic action turn runs
no model, so its token total is zero, so it got no footer. Absence was doing double duty as "no
tokens" and as "nothing to say", and the user only ever saw the second reading.

The rule
--------
Every assistant response exposes compact provenance. What varies is the CONTENT of the line, never
whether there is one:

    model-served chat          local | qwen2.5:7b | 930 tok
    model-served, paid         cloud | claude | 2,450 tok | $0.0123
    model + tool actions       local | qwen2.5:7b | 930 tok | 2 files
    deterministic tool read    tool | workspace.read_file | no model | build 0.5.0
    deterministic write        tool | workspace.write_file | 1 file | no model | build 0.5.0
    deterministic, no tool     tool | smalltalk_fast_path | no model | build 0.5.0
    deterministic, calls above tool | workspace.read_file | no model for the answer |
                                 2 local routing calls | build 0.5.0
    model ran, lane unknown    runtime | model id unrecorded | tokens unreported | build 0.5.0
    recorded, lane unnamed     runtime | no model | build 0.5.0
    nothing recorded at all    (no footer -- see `format_provenance_footer`)

`no model` is a load-bearing phrase, not filler. The single thing this module must never do is let a
deterministic answer read as a model-generated one: a footer naming a model on a turn where none ran
is worse than the missing footer it replaces, because it is a false claim rather than a gap.

Round 2 (2026-08-12) added the symmetric rule, after the reverse failure went live: no word in the
line may be a DEFAULT. Three of them were.

* The lane was ``"cloud" if "cloud" in cost_class else "local"``, so an absent cost class printed
  `local` -- and it was absent on every turn whose model call ran on a conductor or planner worker
  thread, because `core.memory_first_router.get_turn_usage` is a ``threading.local()``. A cloud
  Nemotron call, plainly visible in Activity, footered as the free lane.
* An unknown model id printed the literal word `model`, in the position a model id goes.
* Any nonzero `model_calls` promoted a turn to model-served, overruling the same result's
  ``used_model: False`` -- so a deterministic file read, sitting under two routing calls, described
  itself as a model answer.

Together those produced `local | model | tokens unreported`: three unread facts rendered as three
read ones. Both halves of the rule now hold. A model that ran is never described as absent, and a
fact that was not recorded is named as unrecorded rather than filled in.

Two questions the line keeps apart, because conflating them is what broke it:

1. **Who produced the answer?** Decided by what the producer recorded about itself -- metered
   tokens, then `used_model`, then `fast_path_hit`. Never by a call count.
2. **What did the turn invoke?** `model_calls` and the turn ledger. Disclosed on its own terms as
   `N routing calls`, never as authorship, and never silently dropped -- an undisclosed cloud call
   is a charge the user cannot see.

Where the facts come from
-------------------------
All of them are already on the turn's own result, recorded by the lane that produced it. Nothing
here infers, probes, or guesses:

* `usage` (`core.memory_first_router.get_turn_usage`, or `result["usage_summary"]`) — the served
  model's own reported token counts, cost class and model id. Authoritative when a model ran.
* `result["model_execution"]["used_model"]` — set False by `fast_path_result` and
  `action_fast_path_result`, which also emit a `model_lane_proof` runtime event saying the same.
* `result["model_calls"]` — 0 on every deterministic route.
* `result["route"]` / `result["route_reason"]` — `deterministic:<reason>` or `action:<reason>`.
* `result["details"]` — the action receipt, including `files_written` for a builder result.

A fact this module cannot read is reported as absent, never as a default. A result that records
NONE of the above is not described at all -- see `format_provenance_footer` on why silence, and not
`runtime | no model`, is the honest answer there.

What it does NOT do
-------------------
* It does not read or print paths, file names, arguments, or any part of a receipt beyond a COUNT.
  The existing receipt policy owns what may be shown; a footer is not a place to widen it.
* It does not touch the Activity ledger or any runtime event. Those receipts are the durable
  record; this line is a glance at it.
* It does not distinguish generated-by from reviewed-by. The reviewer's identity is not on the turn
  result today (`verifier_model` lives on the autopilot plan in
  `core.local_inference_autopilot`, not on what the API returns), so claiming it here would be an
  invention. Stated rather than silently omitted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# The rendered footer is a single inline-code span so it reads as machine metadata rather than as
# part of the answer, and so a chat client that renders markdown sets it apart without any styling
# of its own. The separator is " | " inside the span.
_FOOTER_RE = re.compile(r"(?m)^`(?:local|cloud|tool|runtime) \| [^`\n]*`\s*$")

_SEPARATOR = " | "


@dataclass(frozen=True)
class TurnProvenance:
    """What actually produced the turn. Every field is read, never inferred."""

    lane: str
    model_label: str
    model_ran: bool
    tokens: int
    usd: float
    actions: int
    route: str
    build: str
    recorded: bool = True
    #: True when the answering lane was READ (a cost class named it), False when the turn recorded a
    #: model but nothing said which lane it was on. The renderer must be able to tell those apart:
    #: the second used to print `local`, which is a claim, on turns that were in fact cloud.
    lane_recorded: bool = False
    #: The tool the turn actually dispatched, when one did (`workspace.read_file`), as against the
    #: front-door family that claimed the message (`workspace_runtime_fast_path`).
    tool: str = ""
    #: Extra tools beyond `tool`, so a multi-tool turn is not described by one of them alone.
    extra_tools: int = 0
    #: Provider calls entered on this turn that did NOT produce the answer -- routing, arbitration,
    #: planning, and any call that failed. Zero is a reading here, not an absence: it means the
    #: ledger was open and counted nothing.
    routing_calls: int = 0
    #: `local` / `cloud` / `mixed` / "" for those calls. `cloud` whenever any of them was cloud,
    #: because an undisclosed paid call is the one omission that costs the user money.
    routing_lane: str = ""
    #: The turn ran under VOOL Auto Local Only, so no cloud lane was reachable for it. Disclosed
    #: because it changes how the answer should be read: a turn that could not consult the cloud is
    #: a different claim from one that chose not to, and only the footer can tell them apart.
    local_only: bool = False
    #: ``model`` / ``tool`` / ``deterministic`` / ``none``. This names authorship, not mere
    #: participation, and is safe for API consumers to render without reinterpreting call counts.
    answer_source: str = "none"
    #: One of the exhaustive participation states derived from recorded call outcomes.
    model_participation: str = "none"
    completed_model_calls: int = 0
    failed_model_calls: int = 0
    pending_model_calls: int = 0
    participating_models: tuple[str, ...] = ()
    #: The adapter's own refusal code when the selected model's dispatch was refused BEFORE any byte
    #: left (a UsePod money/route/price check), read from the router's terminal decision. "" when the
    #: turn's failure is not such a refusal. Distinguishes "not sent" from "attempted, no answer".
    refused_before_send: str = ""
    usage_details: tuple[dict[str, Any], ...] = ()
    verification_receipts: tuple[dict[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "lane": self.lane,
            "model": self.model_label,
            "model_ran": self.model_ran,
            "tokens": self.tokens,
            "usage_details": list(self.usage_details),
            "verification_receipts": list(self.verification_receipts),
            "usd": self.usd,
            "actions": self.actions,
            "route": self.route,
            "build": self.build,
            "recorded": self.recorded,
            "lane_recorded": self.lane_recorded,
            "tool": self.tool,
            "extra_tools": self.extra_tools,
            "routing_calls": self.routing_calls,
            "routing_lane": self.routing_lane,
            "local_only": self.local_only,
            "answer_source": self.answer_source,
            "model_participation": self.model_participation,
            "completed_model_calls": self.completed_model_calls,
            "failed_model_calls": self.failed_model_calls,
            "pending_model_calls": self.pending_model_calls,
            "participating_models": list(self.participating_models),
        }


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _build_stamp() -> str:
    try:
        from core.app_version import VOOL_VERSION

        return str(VOOL_VERSION or "").strip()
    except Exception:
        return ""


def _route_label(result: dict[str, Any]) -> str:
    """The lane label for a deterministic turn: the reason, not the `deterministic:`/`action:` prefix.

    The prefix says which producer built the result; the reason says which lane served the turn,
    and the second is what a person reading a footer wants.
    """
    reason = str(result.get("route_reason") or "").strip()
    if reason:
        return reason
    route = str(result.get("route") or "").strip()
    if ":" in route:
        return route.split(":", 1)[1].strip() or route
    return route


def _recorded_action_count(result: dict[str, Any]) -> int:
    """How many files the turn's own receipt says it wrote. Never a path, only a count.

    Only `files_written` is counted, and only where a producer already records it under that name
    (`core/agent_runtime/builder/controller.py`). A receipt shape this does not recognize reports
    zero actions rather than a guess -- an undercount omits a segment, an overcount would be a
    claim about work that may not have happened.
    """
    details = result.get("details")
    if not isinstance(details, dict):
        return 0
    total = 0
    for value in details.values():
        if not isinstance(value, dict):
            continue
        written = value.get("files_written")
        if isinstance(written, (list, tuple)):
            total += len(written)
    return total


def _lane_from_cost_class(cost_class: Any) -> str:
    """``local`` / ``cloud`` / "" -- and "" is a real answer, not a prompt to pick one.

    This used to be ``"cloud" if "cloud" in cost_class else "local"``, which turned an ABSENT cost
    class into the word `local`. Every turn whose usage never reached the footer therefore claimed
    the free lane, including the cloud Nemotron turn that made this repair necessary.
    """
    lowered = str(cost_class or "").lower()
    if "cloud" in lowered:
        return "cloud"
    if "local" in lowered:
        return "local"
    return ""


#: The UsePod adapter's refusal families that are raised BEFORE any byte leaves: the money law's
#: reservation, the price/route authorization, the credential and envelope checks. A code outside
#: these (an x402 journal state, say) may follow a payment, so it is never read as "not sent".
USEPOD_PRE_SEND_REFUSAL_PREFIXES = (
    "MONEY_", "price_", "route_", "model_disappeared", "usepod_token_not_configured",
    "usepod_credential_pair_unresolved", "lane_origin_differs_from_credential_origin",
    "max_output_tokens_required", "input_bound_unavailable_for_non_text_parts", "protocol_translation:",
    "sealed_payload_differs_from_envelope", "monetary_authority_",
)
_USEPOD_REFUSAL_PREFIX = "usepod_dispatch_refused:"


def usepod_pre_send_refusal_code(block_reason: str) -> str:
    """The adapter's refusal code when ``block_reason`` names a UsePod dispatch refused BEFORE sending
    (``usepod_dispatch_refused:<code>`` with a pre-send family), else "". A bare code is not accepted:
    the prefix is the adapter's own statement that its refusal came from this seam."""
    reason = str(block_reason or "").strip()
    if not reason.startswith(_USEPOD_REFUSAL_PREFIX):
        return ""
    code = reason[len(_USEPOD_REFUSAL_PREFIX):].strip()[:64]
    return code if code.startswith(USEPOD_PRE_SEND_REFUSAL_PREFIXES) else ""


def read_turn_provenance(
    result: dict[str, Any] | None,
    usage: dict[str, Any] | None = None,
    accounting: dict[str, Any] | None = None,
) -> TurnProvenance:
    """The turn's provenance, read off its own result, the served model's usage, and the turn ledger.

    `accounting` is `core.turn_model_call_ledger.turn_call_accounting` -- what the turn actually
    invoked, recorded at the invocation seams and keyed by an id stamped into the context dict, so
    it survives the thread hop that loses the thread-local usage summary. It is taken from
    ``result["model_call_accounting"]`` when not passed explicitly.

    **The two questions, kept apart.** "What produced this answer" and "what did this turn invoke"
    are different, and the old reader answered the first with evidence for the second: any nonzero
    `model_calls` promoted the turn to model-served, after which the lane and the model id were
    filled from a usage summary that -- on exactly those turns -- was empty. `model_calls` now
    proves only that calls were entered; authorship is decided by what the producer itself recorded.
    """
    payload = dict(result or {})
    ledger = accounting if isinstance(accounting, dict) else payload.get("model_call_accounting")
    ledger = dict(ledger) if isinstance(ledger, dict) else {}

    metered = usage if isinstance(usage, dict) else payload.get("usage_summary")
    metered = dict(metered) if isinstance(metered, dict) else {}
    if not metered:
        # The turn's own ledger holds the same summary the thread-local holds, and holds it across
        # the conductor's worker threads. Consulted only when the caller had nothing.
        served = ledger.get("served_usage")
        metered = dict(served) if isinstance(served, dict) else {}

    tokens = _int(metered.get("prompt_tokens")) + _int(metered.get("output_tokens"))
    execution = payload.get("model_execution")
    execution = dict(execution) if isinstance(execution, dict) else {}
    # A pin the UsePod adapter refused before sending is a reading off the router's terminal
    # decision (source selected_model_blocked, reason usepod_dispatch_refused:<code>). The ledger
    # counts that attempt as failed -- true -- but the footer must not add "model attempted" over a
    # request that never left: observed live 2026-09-16, a MONEY_LIQUIDITY_UNVERIFIED refusal
    # footered as an attempted model with no usable answer.
    refused_before_send = ""
    if str(execution.get("source") or "").strip().lower() == "selected_model_blocked":
        _details = execution.get("details") if isinstance(execution.get("details"), dict) else {}
        refused_before_send = usepod_pre_send_refusal_code(str(_details.get("block_reason") or _details.get("reason") or ""))
    # Provider calls entered this turn: the ledger's own count where it has one, otherwise the
    # number the result reports. Not the same thing as authorship -- see below.
    calls = _int(ledger.get("calls")) if "calls" in ledger else _int(payload.get("model_calls"))

    # Authorship, in order of how directly the source witnessed it:
    #   1. metered tokens -- the usage meter records SERVED responses only, so tokens are proof;
    #   2. `used_model` -- the producer's own statement, believed in both directions. Every fast
    #      path sets it False, which is why a routing call above the lane can no longer overturn it;
    #   3. `fast_path_hit` -- a deterministic lane answered, same conclusion from the other side;
    #   4. calls with none of the above -- something was invoked and nothing claims to have answered
    #      without it. Counted as model-served, but with the lane and the id left UNREAD rather than
    #      defaulted, because a `no model` here would be the false claim in the other direction.
    if tokens > 0:
        model_ran = True
    elif "used_model" in execution:
        model_ran = bool(execution.get("used_model"))
    elif payload.get("fast_path_hit"):
        model_ran = False
    else:
        model_ran = calls > 0

    ledger_lanes = [str(item) for item in (ledger.get("lanes") or []) if str(item or "").strip()]
    ledger_models = [str(item) for item in (ledger.get("models") or []) if str(item or "").strip()]
    tools = [str(item) for item in (ledger.get("tools") or []) if str(item or "").strip()]
    outcomes_recorded = any(
        key in ledger for key in ("completed_calls", "failed_calls", "pending_calls")
    )
    completed_calls = _int(ledger.get("completed_calls")) if outcomes_recorded else 0
    failed_calls = _int(ledger.get("failed_calls")) if outcomes_recorded else 0
    pending_calls = _int(ledger.get("pending_calls")) if outcomes_recorded else calls

    if model_ran:
        answer_source = "model"
        model_participation = "generated_final_answer"
    else:
        execution_source = str(execution.get("source") or "").strip()
        if tools:
            answer_source = "tool"
        elif payload.get("fast_path_hit") or execution_source in {
            "fast_path",
            "channel_action",
            "credit_ledger",
        }:
            answer_source = "deterministic"
        else:
            answer_source = "none"
        deterministic_answer = answer_source in {"tool", "deterministic"}
        if failed_calls > 0:
            model_participation = (
                "attempted_failed_then_tool_answer"
                if deterministic_answer
                else "attempted_no_usable_answer"
            )
        elif completed_calls > 0:
            model_participation = (
                "routed_only" if deterministic_answer else "attempted_no_usable_answer"
            )
        elif calls > 0:
            model_participation = "attempted_outcome_unrecorded"
        else:
            model_participation = "none"

    model_label = str(metered.get("model_id") or "").split("/")[-1].strip()
    if not model_label:
        model_label = str(payload.get("model_selected") or "").split("/")[-1].strip()
    if not model_label and len(ledger_models) == 1:
        # One model was invoked all turn, so naming it is a reading rather than a pick. Two or more
        # and the footer says how many instead of choosing a winner.
        model_label = ledger_models[0].split("/")[-1].strip()

    lane = _lane_from_cost_class(metered.get("cost_class"))
    if not lane and ledger_lanes:
        # `cloud` wins a mixed turn: a paid call the footer failed to mention is the omission with a
        # bill attached, and the local one it would be traded for is free.
        lane = "cloud" if "cloud" in ledger_lanes else ledger_lanes[0]
    lane_recorded = bool(lane)

    usd = metered.get("usd_actual")
    usd = float(usd) if isinstance(usd, (int, float)) and usd > 0 else 0.0

    if model_ran:
        lane_word = lane if lane_recorded else "runtime"
    elif answer_source in {"tool", "deterministic"}:
        lane_word = "tool"
    else:
        lane_word = "runtime"

    routing_calls = 0 if model_ran else (
        max(0, completed_calls) if outcomes_recorded else max(0, calls)
    )
    routing_lane = ""
    if routing_calls or failed_calls or pending_calls:
        if "cloud" in ledger_lanes:
            routing_lane = "cloud" if len(ledger_lanes) == 1 else "mixed"
        elif ledger_lanes:
            routing_lane = ledger_lanes[0] if len(ledger_lanes) == 1 else "mixed"

    usage_details = tuple(ledger.get("usage_details") or ([metered["usage_details"]] if metered.get("usage_details") else []))
    if model_ran and usage_details and all(d.get("input_tokens") is not None and d.get("output_tokens") is not None for d in usage_details):
        tokens = sum(_int(d["input_tokens"]) + _int(d["output_tokens"]) for d in usage_details)

    return TurnProvenance(
        lane=lane_word,
        model_label=model_label if model_ran else "",
        model_ran=model_ran,
        tokens=tokens,
        usd=usd,
        actions=_recorded_action_count(payload),
        route=_route_label(payload),
        build=_build_stamp(),
        recorded=bool(
            metered
            or execution
            # An OPEN ledger is not a recording. `begin_turn` now runs on every turn, so an empty
            # accounting block rides on results that record nothing else -- and treating its mere
            # presence as evidence resurrected the exact false claim round 1 removed: a model's
            # streamed prose, carrying no usage and no `model_execution`, footered
            # `runtime | no model`. Only what the ledger actually OBSERVED counts.
            or calls > 0
            or tools
            or payload.get("model_calls") is not None
            or _route_label(payload)
        ),
        lane_recorded=lane_recorded,
        tool=tools[-1] if tools else "",
        extra_tools=max(0, len(tools) - 1),
        routing_calls=routing_calls,
        routing_lane=routing_lane,
        local_only=bool(payload.get("local_only")),
        answer_source=answer_source,
        model_participation=model_participation,
        completed_model_calls=completed_calls,
        failed_model_calls=failed_calls,
        pending_model_calls=pending_calls,
        participating_models=tuple(ledger_models),
        refused_before_send=refused_before_send,
        usage_details=usage_details,
        verification_receipts=tuple(ledger.get("verification_receipts") or []),
    )


def _model_lane_segments(provenance: TurnProvenance) -> list[str]:
    # `model` as a stand-in for an unknown id read as a model NAMED "model" and, worse, sat beside a
    # lane word that was equally unread -- the whole line `local | model | tokens unreported` was
    # three blanks wearing the costume of three facts. An unread id now says so.
    segments = [provenance.lane, provenance.model_label or "model id unrecorded"]
    # A model that ran but reported no token counts is a real state (a provider that omits usage),
    # and saying "tokens unreported" is the truthful rendering of it. Printing "0 tok" would assert
    # a measurement nobody made.
    segments.append(f"{provenance.tokens:,} tok" if provenance.tokens > 0 else "tokens unreported")
    if provenance.usage_details:
        from core.response_usage_details import usage_display_segments
        segments.extend(usage_display_segments(list(provenance.usage_details)))
    elif provenance.usd > 0:
        segments.append(f"${provenance.usd:.4f}")
    if provenance.verification_receipts:
        statuses = sorted({str((r.get("verification") or {}).get("status") or "CLAIMED") for r in provenance.verification_receipts})
        segments.append("identity: " + "/".join(statuses))
    return segments


def _routing_call_segment(provenance: TurnProvenance) -> str:
    """``2 local routing calls`` -- provider calls the turn entered that did not write the answer.

    Not decoration and not optional. A deterministic lane can sit under a routing gate that spends
    real provider calls (the intent arbiter posts to Ollama; a cloud preflight can too), and a
    footer that printed a bare `no model` over them would hide a call the user may have paid for --
    the same absence-as-assertion this module exists to remove, one layer up.
    """
    if provenance.routing_calls <= 0:
        return ""
    plural = "" if provenance.routing_calls == 1 else "s"
    lane = f"{provenance.routing_lane} " if provenance.routing_lane else ""
    return f"{provenance.routing_calls} {lane}routing call{plural}"


def _failed_call_segment(provenance: TurnProvenance) -> str:
    if provenance.failed_model_calls <= 0:
        return ""
    plural = "" if provenance.failed_model_calls == 1 else "s"
    lane = f"{provenance.routing_lane} " if provenance.routing_lane else ""
    return f"{provenance.failed_model_calls} {lane}model attempt{plural} failed"


def format_model_usage_footer(usage: dict[str, Any] | None) -> str:
    """The model lane's own line: ``local | qwen2.5:7b | 1,203 tok``. "" when no tokens were metered.

    This is the pre-existing `core.web.api.runtime._format_usage_footer` contract, unchanged and
    still empty-when-unmetered, kept as its own function so the model line has exactly ONE
    rendering. `format_provenance_footer` below reuses it rather than re-deriving the same string,
    which is what stopped the two from drifting apart the moment either was edited.
    """
    if not isinstance(usage, dict):
        return ""
    provenance = read_turn_provenance({}, usage)
    if provenance.tokens <= 0:
        return ""
    return "`" + _SEPARATOR.join(_model_lane_segments(provenance)) + "`"


def format_provenance_footer(
    result: dict[str, Any] | None,
    usage: dict[str, Any] | None = None,
    accounting: dict[str, Any] | None = None,
) -> str:
    """The one-line footer for this turn. Empty ONLY when the runtime recorded nothing about it.

    That one exception is not a softening of the rule, it is the rule's own honesty clause, and it
    was found by running the suite rather than by reasoning: a result carrying no usage, no
    `model_execution`, no `model_calls` and no route is a result the runtime cannot describe, and
    `runtime | no model` on it is not a cautious default — it is an assertion that no model ran.
    `tests/test_task_event_stream.py::test_clean_prose_streams_through_unchanged` streams a MODEL's
    prose through exactly that shape, so the catch-all was printing a false claim on a model-served
    turn — the precise thing this module exists to prevent, arrived at from the other direction.

    Every lane that records what it did — the model lanes, every fast path, every action, the
    builder — is described. Silence is now reserved for "nothing was recorded", which is a far
    narrower absence than the defect this replaced (which withheld the footer from every
    deterministic turn, all of which record plenty).
    """
    provenance = read_turn_provenance(result, usage, accounting)
    if not provenance.recorded:
        return ""

    # Bound before the branches. A turn that is RECORDED, ran no model, and whose answer_source is
    # neither "tool" nor "deterministic" (e.g. "none") matches neither arm below, and the file-count
    # append that follows then raised UnboundLocalError. That call is made generator-side
    # (core/web/api/runtime.py `_response_commit` and the stream tail), which is after the response
    # headers are already committed -- so the exception truncated the body mid-stream instead of
    # becoming an error the client could render. Reproduced directly at c6eed761.
    parts: list[str] = []

    if provenance.model_ran:
        parts = _model_lane_segments(provenance)
        if not provenance.lane_recorded and provenance.build:
            # A model ran and the turn cannot say where. That is a line someone will paste into a
            # bug report, so it carries the build the way the deterministic lines do; a footer that
            # merely says "unrecorded" without saying unrecorded BY WHAT is half a report.
            parts.append(f"build {provenance.build}")
    elif provenance.answer_source in {"tool", "deterministic"}:
        parts = [provenance.lane]
        # The TOOL that ran outranks the front-door family that dispatched it. `workspace.read_file`
        # is the operation the user asked for and the name Activity already shows;
        # `workspace_runtime_fast_path` names the lane that claimed the message, and
        # `builder_model_build` names a model build on a write where the receipt records no
        # generation at all -- a route label asserting a model over a turn that ran none.
        operation = provenance.tool or provenance.route
        if operation:
            parts.append(operation)
        if provenance.extra_tools > 0:
            parts.append(f"+{provenance.extra_tools} more tool" + ("" if provenance.extra_tools == 1 else "s"))

    # Kept as its own value as well as appended, because the two branches below REPLACE `parts`
    # wholesale -- so a turn that wrote files and then took the no-model arm silently lost the
    # count it had just recorded. Files written are the least droppable thing on a footer.
    file_segment: list[str] = []
    if provenance.actions > 0:
        file_segment = [f"{provenance.actions} file" + ("" if provenance.actions == 1 else "s")]
        parts.extend(file_segment)

    if not provenance.model_ran and provenance.answer_source in {"tool", "deterministic"}:
        # `no model` is the whole-turn claim and stays exactly that: nothing was invoked at all.
        # When calls WERE entered, the claim narrows to the answer and the calls are named beside
        # it, so the phrase never covers for a call it is not describing.
        routing = _routing_call_segment(provenance)
        failed = _failed_call_segment(provenance)
        if provenance.model_participation == "routed_only":
            parts.append("tool-generated answer")
            parts.append(routing)
        elif provenance.model_participation == "attempted_failed_then_tool_answer":
            parts.append("tool-generated answer")
            parts.append(failed)
        else:
            parts.append("no model for the answer" if routing else "no model")
            if routing:
                parts.append(routing)
        if provenance.build:
            parts.append(f"build {provenance.build}")
    elif not provenance.model_ran:
        if provenance.model_participation == "none":
            parts = ["runtime", "no model", *file_segment]
            if provenance.build:
                parts.append(f"build {provenance.build}")
            if provenance.local_only:
                # This arm returns early, so it skipped the whole-turn disclosure appended at
                # the bottom -- a Local Only turn that invoked nothing told the user nothing
                # about the mode it ran under. The docstring calls this the disclosure the
                # mode OWES the user; an early return is not a reason to stop owing it.
                parts.append("local only · cloud blocked")
            return "`" + _SEPARATOR.join(parts) + "`"
        # No producer claimed a usable answer. Name the attempted model only from the call ledger;
        # a selected-but-never-called model is deliberately absent.
        attempted_model = (
            provenance.participating_models[0].split("/")[-1].strip()
            if len(provenance.participating_models) == 1
            else "model id unrecorded"
        )
        if provenance.refused_before_send:
            # The request never left: say so, and name the check that refused it.
            parts = ["runtime", attempted_model, "not sent", f"refused before send: {provenance.refused_before_send}", *file_segment]
        else:
            parts = ["runtime", attempted_model, "model attempted", "no usable answer", *file_segment]
        failed = _failed_call_segment(provenance)
        if failed:
            parts.append(failed)
        if provenance.build:
            parts.append(f"build {provenance.build}")

    if not provenance.model_ran and provenance.usage_details and not provenance.refused_before_send:
        from core.response_usage_details import usage_display_segments
        parts.extend(usage_display_segments(list(provenance.usage_details)))

    if provenance.local_only:
        # Placed last so it reads as a statement about the whole turn rather than about the lane
        # segment next to it. It is the disclosure the mode owes the user: everything above was
        # produced without any cloud provider being reachable.
        parts.append("local only · cloud blocked")

    return "`" + _SEPARATOR.join(parts) + "`"


def strip_provenance_footer(text: str) -> str:
    """Remove any provenance line already on the text.

    The append path is called more than once on some routes (a buffered finalize after a streamed
    one, a post-stream rewrite of `result["response"]`), and a plain "is it already there?" test
    only catches an IDENTICAL line -- so a second, differently-shaped footer used to stack under
    the first. Removing before appending makes the operation idempotent regardless of shape.
    """
    return _FOOTER_RE.sub("", str(text or "")).rstrip()


def append_provenance_footer(
    text: str,
    result: dict[str, Any] | None,
    usage: dict[str, Any] | None = None,
    accounting: dict[str, Any] | None = None,
) -> str:
    """`text` with exactly one provenance line at the end. Empty text is left alone."""
    body = strip_provenance_footer(text)
    if not body.strip():
        return str(text or "")
    footer = format_provenance_footer(result, usage, accounting)
    if not footer:
        # An unrecorded turn is left exactly as it arrived -- not as the body plus a blank tail,
        # which is what a caller asserting an exact response string would see as a changed answer.
        return body
    return body + "\n\n" + footer


def slot_receipts(
    source_context: dict[str, Any] | None,
    result_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One receipt per demand slot: which tool, source, model and state handled it.

    C11 -- "receipts show which tools/sources/model handled each slot" -- is the one criterion
    that never moved at any commit, and it never moved because provenance in this module is
    TURN-GRAIN by construction. `TurnProvenance` collapses a multi-tool turn to the last tool
    plus a count (`tool`, `extra_tools`), which cannot answer a per-slot question however it is
    read. A turn that served a file read and an arithmetic result reported one tool and
    `extra_tools: 0`.

    The per-slot facts already exist and had ZERO production readers: `turn_demand_ledger` --
    one `DemandRecord` per minted unit, carrying the demand id, the request text, the selected
    capability, the owning lane, whether execution was attempted and the typed terminal state
    it reached -- and its companion `turn_demand_executors`, the route each sub-turn stamped on
    itself. This is the ledger's first production reader; it projects, and derives nothing.

    Two rules keep it a receipt rather than a decoration:

    * a FAILED or REFUSED slot gets a row like any other. "Which tool handled this" is exactly
      the question a failed slot raises, and counting only the served ones is how a turn that
      dropped everything passes vacuously;
    * no field is invented. A slot the runtime cannot attribute says so in `source`/`tool`
      rather than borrowing the turn's own route -- turn-grain attribution presented per slot
      is the defect, not the fix.

    Returns {} when the turn minted no demand, so a turn with no slots keeps a byte-identical
    commit.
    """
    context = source_context if isinstance(source_context, dict) else {}
    try:
        from core.turn_contract import TURN_DEMAND_LEDGER_KEY
    except Exception:
        return {}
    ledger = context.get(TURN_DEMAND_LEDGER_KEY)
    if not isinstance(ledger, (list, tuple)) or not ledger:
        # The transport door hands `run_once` a COPY of its context, so the ledger this turn
        # wrote is not on the dict the door still holds. It rides the result payload instead --
        # the same crossing the closure verdict already makes, and for the same reason.
        stashed = (result_payload or {}).get("_turn_demand_ledger")
        ledger = stashed if isinstance(stashed, (list, tuple)) else []
    ledger = list(ledger)

    payload = result_payload if isinstance(result_payload, dict) else {}
    usage = payload.get("usage_summary") if isinstance(payload.get("usage_summary"), dict) else {}
    provenance = (
        payload.get("answer_provenance")
        if isinstance(payload.get("answer_provenance"), dict)
        else {}
    )
    # The model that actually ran this turn, read from what the turn recorded -- never assumed.
    turn_model = str(
        provenance.get("model_label")
        or usage.get("model")
        or (list(provenance.get("participating_models") or []) or [""])[0]
        or ""
    )

    receipts: dict[str, Any] = {}
    for row in ledger:
        if not isinstance(row, dict):
            continue
        demand_id = str(row.get("demand_id") or "")
        if not demand_id:
            continue
        lane_id = str(row.get("lane_id") or "")
        capability = str(row.get("capability") or "")
        state = str(row.get("terminal_state") or "")
        refusals = [str(reason) for reason in (row.get("refusal_reasons") or [])]
        receipts[demand_id] = {
            "slot": demand_id,
            "request": str(row.get("request") or ""),
            "tool": capability or "unattributed",
            "source": lane_id or "unattributed",
            # A model is named only where a model lane served the slot; a deterministic lane
            # naming the turn's model would be the turn-grain answer wearing a per-slot label.
            "model": turn_model if lane_id and "model" in lane_id else "",
            "attempted": bool(row.get("attempted")),
            "state": state,
            "refusal": "; ".join(refusals),
        }
    # ONE RECEIPT PER MINTED SLOT, not per ledger row.
    #
    # `turn_demand_ledger` is written by ONE route. Measured on the gauntlet's evening prompt:
    # a four-slot turn produced two rows, so two slots had receipts and two had none, and the
    # criterion -- "receipts show which tools/sources/model handled EACH slot" -- was half met
    # while reporting nothing about the half it missed. A slot with no receipt is exactly the
    # slot a reader most needs one for.
    #
    # Every slot the turn MINTED gets a row. Where no lane claimed it, the row says so instead
    # of being absent: "which tool handled this" has an honest answer, and that answer is
    # sometimes "none did".
    for unit_id, text in _minted_demand_slots(context):
        if unit_id in receipts:
            continue
        receipts[unit_id] = {
            "slot": unit_id,
            "request": text,
            "tool": "unattributed",
            "source": "unattributed",
            "model": "",
            "attempted": False,
            "state": "not_attempted",
            "refusal": "",
        }
    return receipts


def _minted_demand_slots(context: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    """(unit_id, text) for every demand this turn minted, read from the obligation ledger.

    The ledger is the turn's own record of what was asked. Reading it here is a projection,
    not a second authority: nothing is minted, dispositioned or derived.
    """
    try:
        from core.conductor import obligation_ledger as _ol
        from core.turn_contract import TURN_REQUEST_KEY
    except Exception:
        return ()
    bound = None
    try:
        bound = _ol.active_set()
        if bound is None:
            request = context.get(TURN_REQUEST_KEY)
            request_id = str(getattr(request, "request_id", "") or "")
            if request_id:
                bound = _ol.set_for_request(request_id)
    except Exception:
        return ()
    if not bound:
        return ()
    try:
        return tuple(
            (str(item.get("unit_id") or ""), str(item.get("text") or ""))
            for item in _ol.demand_obligations(*bound)
            if str(item.get("unit_id") or "")
        )
    except Exception:
        return ()
