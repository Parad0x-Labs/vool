"""Live DISPATCH tracer for VOOL's front-door claim points.

TEST INFRASTRUCTURE ONLY. Changes no product behaviour: every wrapper delegates straight through to
the original callable and returns its result unchanged.

WHY THIS EXISTS RATHER THAN `claim_sequence_monitor.attribute()`
-----------------------------------------------------------------
`claim_sequence_monitor.attribute()` is a PREDICATE SWEEP. It calls each registry probe directly
over a bare prompt and prints which ones say yes. That answers "which predicates match this text",
which is NOT the question a misroute investigation asks. Three things it structurally cannot tell
you, all of which decided a set-6 turn:

1. **Order.** It runs probes in REGISTRY order, not in the order the front door consults them. A
   probe that would never have been reached in production still reports CLAIMED.
2. **Authority.** It calls each probe in isolation, so a claimant that consults
   `analyze_retrieval_constraints` and one that never looks at any authority at all are
   indistinguishable in its output.
3. **Who won.** Several probes return truthy on the same turn. Exactly one of them produced the
   text the user received; the others were overridden, consumed as a sub-answer, or discarded.
   `attribute()` reports all of them identically.

This module answers all three by observing REAL execution instead of re-invoking predicates.

HOW EACH FIELD IS OBTAINED -- and where the boundary of "observed" sits
------------------------------------------------------------------------
* **claim_order** -- a per-turn counter incremented on every wrapper EXIT whose return is truthy.
  It is the position in the real consultation sequence, not a registry index.

* **depth / parent / authority_calls** -- the wrappers log on ENTRY as well as exit and keep a
  per-thread frame stack, so any wrapped call made INSIDE a claimant's dynamic extent is recorded
  as nested under it. `authority_calls` on a claimant's exit record is exactly the set of
  `cluster == "authority"` calls that happened between its entry and its exit -- i.e. the
  claimant's own consultation, not another lane's. A claimant with an empty `authority_calls`
  claimed without asking, and that is an observation, not an inference.

* **reason** -- pulled STRUCTURALLY out of the return value, not `repr()`d. Most of these probes
  return an envelope that names its own reading: `currency_fast_path` returns
  `{"kind": "rate_lookup", "grounded": "no_rate_declined", ...}`, `analyze_retrieval_constraints`
  returns a `RetrievalConstraints` with `has_prohibition` / `prohibited_toolsets`,
  `parse_raw_output_contract` returns a contract with `exact_text`, `coverage_for` returns a
  `ClaimCoverage` with `covers_whole_turn` / `consumed`. `_reason_of` reads those fields by name.

* **won_dispatch** -- see `resolve_turn` below. It is resolved from THREE observed facts, never
  from a route-name guess:
    (a) `dispatch_post`'s own return value carries the final answer text and the final `route` the
        HTTP caller reads -- the tracer records the turn's real outcome in the same log;
    (b) `fast_path_result` is the front door's single dispatch emitter (apps/vool_agent.py:2439),
        and its `reason=` kwarg is what `core/response_provenance.py:_route_label` turns into that
        `route`; wrapping it records `(reason, response)` for whichever lane actually emitted;
    (c) a claimant "won" when the response text IT returned is the response text the emitter
        emitted, or (for lanes with no fast-path emitter) is what the turn finally answered.
  Where none of the three resolves it, `won_dispatch` is left as `"unresolved"` and the turn is
  counted in that bucket. It is never filled in by pattern-matching a route name to a module name.

WHAT THIS DOES NOT COVER
--------------------------
The registry is 27 symbols. `handle_turn_frontdoor` alone holds roughly 70 inline claim points and
the whole runtime roughly 89; the remaining ~62 are UNOBSERVED by this tracer. A turn whose winner
is one of those resolves to `won_dispatch="unresolved"` with `actual_claimant=""` rather than being
attributed to the nearest registered probe.
"""
from __future__ import annotations

import importlib
import json
import os
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from typing import Any

try:  # `tests` is a real package, but pytest also puts `tests/` itself on sys.path.
    from tests.claim_sequence_monitor import REGISTRY, ProbeSpec, _extract_prompt
except ImportError:  # pragma: no cover - depends only on which entry point imported us
    from claim_sequence_monitor import REGISTRY, ProbeSpec, _extract_prompt

#: Return-value fields that carry a probe's OWN stated reason, in the order they are looked for.
#: Every name here was read off a real return type in this repo, not guessed:
#: `currency_fast_path` -> kind/grounded/codes; `RetrievalConstraints` -> has_prohibition/
#: forbids_all_tools/forbids_external_retrieval/prohibited_toolsets; `RawOutputContract` ->
#: exact_text/no_json/no_markdown; `ClaimCoverage` -> covers_whole_turn/consumed; a `result` dict
#: -> route/route_reason.
_REASON_FIELDS: tuple[str, ...] = (
    "kind",
    "grounded",
    "route_reason",
    "route",
    "family",
    "reason",
    "exact_text",
    "has_prohibition",
    "forbids_all_tools",
    "forbids_external_retrieval",
    "prohibited_toolsets",
    "covers_whole_turn",
    "consumed",
    "no_json",
    "no_markdown",
    "codes",
    "url",
    "actions",
)

#: Kwarg names worth capturing off a CALL (not a return). `reason`/`response` are what makes
#: `fast_path_result` the dispatch emitter; `family` disambiguates `coverage_for`; `turn_id` and
#: `session_id` come off `dispatch_post`'s body.
_ARG_FIELDS: tuple[str, ...] = ("reason", "family", "turn_id", "session_id")

_MAX_TEXT = 4000


def _short(value: Any, limit: int = 240) -> str:
    text = value if isinstance(value, str) else repr(value)
    text = " ".join(str(text).split())
    return text[:limit]


def _reason_of(result: Any) -> str:
    """The probe's OWN stated reason, read field-by-field, or "" when it states none.

    A bare `True`/`False`/`None` genuinely carries no reason; returning "" for those is the point.
    Anything else is rendered as `field=value` pairs so two claims by the same symbol can be split
    by WHY they claimed -- which is the whole basis of the claimant x reason x outcome histogram.
    """
    if result is None or isinstance(result, bool):
        return ""
    parts: list[str] = []
    for name in _REASON_FIELDS:
        if isinstance(result, dict):
            if name not in result:
                continue
            value = result.get(name)
        else:
            if not hasattr(result, name):
                continue
            try:
                value = getattr(result, name)
            except Exception:  # pragma: no cover - a property that raises is not a reason
                continue
        if value is None or value == "" or value == () or value == frozenset():
            continue
        if callable(value):
            continue
        parts.append(f"{name}={_short(value, 60)}")
    if parts:
        return " ".join(parts)
    if isinstance(result, str):
        return ""
    return type(result).__name__


#: Symbols whose return value is the REQUEST text, not an answer. Without this they text-match a
#: turn that echoes its own prompt and get credited as the producer.
_REQUEST_TEXT_SYMBOLS: frozenset[str] = frozenset(
    {"intake_request_text", "live_request_after_retraction"}
)


def _is_claim(symbol: str, cluster: str, result: Any) -> bool:
    """Did this call CLAIM, as opposed to merely returning a truthy object?

    Truthiness alone is wrong for the two pass-through transformers, and both were caught crediting
    themselves on a real calibration turn before this existed:

    * `apply_raw_output_contract` ALWAYS returns a `RawOutputApplication`, and when no contract
      binds it returns the model's text unmodified. Its return therefore contains the served answer
      on nearly every turn and won every text match -- it was credited on C1, C3, C4, C5 and N2.
      It has a `changed: bool` field that says exactly whether it altered the answer, so that is the
      claim test.
    * `parse_raw_output_contract` returns a CONTRACT, never text. Only an `exact_text` binding pins
      the whole reply; the other flags reshape an answer the model still writes. So it claims only
      when `exact_text is not None` -- which is precisely the C6 defect.
    """
    if cluster in _NON_CLAIMANT_CLUSTERS:
        return False
    if symbol == "apply_raw_output_contract":
        return bool(getattr(result, "changed", False))
    if symbol == "parse_raw_output_contract":
        return getattr(result, "exact_text", None) is not None
    return bool(result)


def _response_text_of(result: Any) -> str:
    """The answer text a return value carries, or "" when it carries none.

    This is the half of `won_dispatch` that does not depend on any route label: if the text a
    claimant produced is the text the turn emitted, that claimant won, whatever the route is
    called.
    """
    if isinstance(result, str):
        return result[:_MAX_TEXT]
    if isinstance(result, dict):
        for key in ("response", "text", "answer", "content", "message"):
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                return value[:_MAX_TEXT]
        inner = result.get("result")
        if isinstance(inner, dict):
            return _response_text_of(inner)
        return ""
    for key in ("response", "text", "answer"):
        value = getattr(result, key, None)
        if isinstance(value, str) and value.strip():
            return value[:_MAX_TEXT]
    return ""


@dataclass
class TraceEvent:
    """One observed entry or exit. `enter` events exist so nesting is visible; the analysis reads
    `exit` events, which carry the return-derived fields."""

    seq: int
    kind: str  # "enter" | "exit" | "raise" | "turn"
    symbol: str
    module: str
    patched_module: str
    cluster: str
    depth: int
    parent: str
    turn_id: str
    thread: str
    t: float
    prompt_prefix: str = ""
    call_args: dict[str, str] = field(default_factory=dict)
    returned_truthy: bool | None = None
    #: `returned_truthy` narrowed by `_is_claim` -- see there for why truthiness is not enough.
    is_claim: bool | None = None
    return_repr: str = ""
    reason: str = ""
    response_text: str = ""
    authority_calls: list[str] = field(default_factory=list)
    claim_order: int | None = None
    note: str = ""


class _Frame:
    __slots__ = ("authority_calls", "cluster", "seq", "symbol")

    def __init__(self, symbol: str, cluster: str, seq: int) -> None:
        self.symbol = symbol
        self.cluster = cluster
        self.seq = seq
        self.authority_calls: list[str] = []


class DispatchTracer:
    """Installed entry+exit wrappers plus the ordered event log they fill in.

    Thread-safe by construction: the frame stack is thread-local (the daemon serves each request on
    its own thread) while the sequence counter and the JSONL sink are lock-guarded, so a trace read
    from a served daemon is not interleaved garbage.
    """

    def __init__(
        self,
        registry: Sequence[ProbeSpec] = REGISTRY,
        *,
        jsonl_path: str | None = None,
    ) -> None:
        self.registry = tuple(registry)
        self.events: list[TraceEvent] = []
        self.install_failures: list[tuple[str, str, str]] = []
        self._restores: list[Callable[[], None]] = []
        self._lock = threading.Lock()
        self._seq = 0
        self._local = threading.local()
        self._jsonl_path = jsonl_path
        self._sink = None
        if jsonl_path:
            os.makedirs(os.path.dirname(jsonl_path) or ".", exist_ok=True)
            # Deliberately not a context manager: this sink outlives the constructor by design --
            # it stays open for the lifetime of a daemon process so every turn appends to one log,
            # and `uninstall()` closes it. Append mode + flush-per-record means a trace survives
            # even if the daemon is killed rather than shut down.
            self._sink = open(jsonl_path, "a", encoding="utf-8")  # noqa: SIM115

    # ---------------------------------------------------------------- bookkeeping

    @property
    def _stack(self) -> list[_Frame]:
        stack = getattr(self._local, "stack", None)
        if stack is None:
            stack = []
            self._local.stack = stack
        return stack

    @property
    def turn_id(self) -> str:
        return getattr(self._local, "turn_id", "") or ""

    @turn_id.setter
    def turn_id(self, value: str) -> None:
        self._local.turn_id = value

    def _next_claim_order(self) -> int:
        current = getattr(self._local, "claim_order", 0) + 1
        self._local.claim_order = current
        return current

    def reset_turn(self, turn_id: str) -> None:
        self._local.claim_order = 0
        self._local.turn_id = turn_id

    def _emit(self, event: TraceEvent) -> None:
        with self._lock:
            self.events.append(event)
            if self._sink is not None:
                self._sink.write(json.dumps(asdict(event), default=str) + "\n")
                self._sink.flush()

    def _take_seq(self) -> int:
        with self._lock:
            self._seq += 1
            return self._seq

    def record_turn_marker(self, turn_id: str, note: str, payload: dict[str, Any]) -> None:
        """A synthetic `turn` event -- the turn's own outcome, in the same log as its claims."""
        self._emit(
            TraceEvent(
                seq=self._take_seq(),
                kind="turn",
                symbol="__turn__",
                module="",
                patched_module="",
                cluster="turn_boundary",
                depth=0,
                parent="",
                turn_id=turn_id,
                thread=threading.current_thread().name,
                t=time.time(),
                note=note,
                call_args={k: _short(v, 600) for k, v in payload.items()},
            )
        )

    # ---------------------------------------------------------------- installation

    def _wrap_one(self, module_path: str, symbol: str, spec: ProbeSpec) -> bool:
        try:
            mod = importlib.import_module(module_path)
        except Exception as exc:
            self.install_failures.append((module_path, symbol, f"import failed: {exc!r}"))
            return False
        original = getattr(mod, symbol, None)
        if original is None or not callable(original):
            self.install_failures.append(
                (module_path, symbol, f"no callable attribute {symbol!r} on {module_path}")
            )
            return False

        tracer = self
        is_authority = spec.cluster == "authority"
        is_boundary = spec.cluster == "turn_boundary"

        def wrapper(*args: Any, **kwargs: Any) -> Any:
            stack = tracer._stack
            parent = stack[-1].symbol if stack else ""
            depth = len(stack)

            call_args = {
                name: _short(kwargs[name], 200)
                for name in _ARG_FIELDS
                if name in kwargs and kwargs[name] is not None
            }
            if is_boundary:
                body = kwargs.get("body")
                if isinstance(body, dict):
                    for name in ("turn_id", "session_id", "model", "model_selection"):
                        if body.get(name):
                            call_args[name] = _short(body[name], 120)
                    tracer.reset_turn(str(body.get("turn_id") or ""))
                path = kwargs.get("path")
                if path:
                    call_args["path"] = _short(path, 80)

            enter_seq = tracer._take_seq()
            prompt_prefix = _short(_extract_prompt(args, kwargs), 160)
            tracer._emit(
                TraceEvent(
                    seq=enter_seq,
                    kind="enter",
                    symbol=symbol,
                    module=spec.module,
                    patched_module=module_path,
                    cluster=spec.cluster,
                    depth=depth,
                    parent=parent,
                    turn_id=tracer.turn_id,
                    thread=threading.current_thread().name,
                    t=time.time(),
                    prompt_prefix=prompt_prefix,
                    call_args=call_args,
                )
            )

            frame = _Frame(symbol, spec.cluster, enter_seq)
            stack.append(frame)
            try:
                result = original(*args, **kwargs)
            except BaseException as exc:
                stack.pop()
                tracer._emit(
                    TraceEvent(
                        seq=tracer._take_seq(),
                        kind="raise",
                        symbol=symbol,
                        module=spec.module,
                        patched_module=module_path,
                        cluster=spec.cluster,
                        depth=depth,
                        parent=parent,
                        turn_id=tracer.turn_id,
                        thread=threading.current_thread().name,
                        t=time.time(),
                        prompt_prefix=prompt_prefix,
                        call_args=call_args,
                        note=f"{type(exc).__name__}: {exc}"[:300],
                    )
                )
                raise
            stack.pop()

            truthy = bool(result)
            is_claim = _is_claim(symbol, spec.cluster, result)
            reason = _reason_of(result)
            if symbol == "apply_raw_output_contract":
                # The winner here is a CONSUMER; its reason has to name the contract it applied or
                # the histogram cannot tell "bound a quoted literal" (correct) from "bound a format
                # description" (the C6 mis-parse). The contract is this call's second argument.
                contract = args[1] if len(args) > 1 else kwargs.get("contract")
                contract_reason = _reason_of(contract) if contract is not None else ""
                if contract_reason:
                    reason = f"{reason} <- {contract_reason}".strip()
            # An authority's verdict is registered with whichever claimant was mid-flight, so its
            # consultation is attributable to that claimant rather than to the turn at large.
            if is_authority and stack:
                stack[-1].authority_calls.append(f"{symbol}({reason or 'no_constraint'})")

            claim_order = tracer._next_claim_order() if truthy else None
            response_text = "" if symbol in _REQUEST_TEXT_SYMBOLS else _response_text_of(result)
            if is_boundary:
                # dispatch_post's ApiResponse body is the turn's authoritative outcome.
                tracer._record_boundary_outcome(result, call_args)

            tracer._emit(
                TraceEvent(
                    seq=tracer._take_seq(),
                    kind="exit",
                    symbol=symbol,
                    module=spec.module,
                    patched_module=module_path,
                    cluster=spec.cluster,
                    depth=depth,
                    parent=parent,
                    turn_id=tracer.turn_id,
                    thread=threading.current_thread().name,
                    t=time.time(),
                    prompt_prefix=prompt_prefix,
                    call_args=call_args,
                    returned_truthy=truthy,
                    is_claim=is_claim,
                    return_repr=_short(result, 400),
                    reason=reason,
                    response_text=response_text,
                    authority_calls=list(frame.authority_calls),
                    claim_order=claim_order,
                )
            )
            return result

        setattr(mod, symbol, wrapper)
        self._restores.append(lambda m=mod, s=symbol, o=original: setattr(m, s, o))
        return True

    def _record_boundary_outcome(self, response: Any, call_args: dict[str, str]) -> None:
        """Extract the served answer + route off `dispatch_post`'s ApiResponse, defensively.

        Read through `getattr`/`json.loads` rather than importing the ApiResponse type: this module
        must never be the reason a daemon fails to start.
        """
        payload: Any = None
        for attr in ("body", "payload", "content", "data"):
            raw = getattr(response, attr, None)
            if raw is None:
                continue
            if isinstance(raw, (bytes, bytearray)):
                try:
                    payload = json.loads(raw.decode("utf-8", "replace"))
                except Exception:
                    continue
            elif isinstance(raw, str):
                try:
                    payload = json.loads(raw)
                except Exception:
                    continue
            elif isinstance(raw, dict):
                payload = raw
            if payload is not None:
                break
        if not isinstance(payload, dict):
            return
        answer = (payload.get("message") or {}).get("content") if isinstance(payload.get("message"), dict) else None
        if answer is None:
            answer = payload.get("response") or ""
        commit = payload.get("vool_response_commit") or {}
        prov = ((commit.get("display_metadata") or {}).get("provenance")) or {}
        self.record_turn_marker(
            call_args.get("turn_id", "") or self.turn_id,
            "served",
            {
                "answer": str(answer)[:_MAX_TEXT],
                "route": prov.get("route", ""),
                "model_ran": prov.get("model_ran", ""),
                "tool": prov.get("tool", ""),
                "answer_source": prov.get("answer_source", ""),
            },
        )

    def install(self) -> None:
        for spec in self.registry:
            self._wrap_one(spec.module, spec.symbol, spec)
            for extra_module in spec.also_patch:
                self._wrap_one(extra_module, spec.symbol, spec)

    def uninstall(self) -> None:
        for restore in reversed(self._restores):
            restore()
        self._restores.clear()
        if self._sink is not None:
            try:
                self._sink.flush()
                self._sink.close()
            finally:
                self._sink = None


@contextmanager
def trace(
    registry: Sequence[ProbeSpec] = REGISTRY, *, jsonl_path: str | None = None
) -> Iterator[DispatchTracer]:
    tracer = DispatchTracer(registry, jsonl_path=jsonl_path)
    tracer.install()
    try:
        yield tracer
    finally:
        tracer.uninstall()


# --------------------------------------------------------------------- post-hoc resolution


def _norm(text: str) -> str:
    return " ".join(str(text or "").split()).strip()


#: Route labels the runtime emits when the MODEL produced the answer. Not a guess: each is either
#: written by `core/agent_runtime/turn_reasoning.py` (`ordinary_plain_text_*`, `grounded_model_route`)
#: or is a `model_tool_intent_*` label, and every set-6 turn carrying one also reports
#: `model_ran=True` in its own provenance, which is what the resolver actually checks.
_MODEL_ROUTE_PREFIXES: tuple[str, ...] = (
    "ordinary_plain_text",
    "model_tool_intent",
    "grounded_model_route",
)

#: The `honesty` cluster is a PASS-THROUGH TRANSFORMER, not a producer. `enforce_url_grounding` /
#: `enforce_final_action_honesty` are handed the finished result dict and usually return it
#: unchanged -- which means their return value contains the final answer text and would win any
#: naive text match, stealing attribution from the lane that actually produced the answer. That is
#: not hypothetical: the first run of this resolver credited `enforce_url_grounding` on calibration
#: C4 (L9.3) even though the trace plainly showed `currency_fast_path` claiming with
#: `kind=rate_lookup` and the emitter stamping `currency_rate_lookup_fast_path`.
#:
#: A validator only CLAIMS when it overwrites the route with one of its own. This is the complete
#: set it writes, read off `core/agent_runtime/action_honesty_validator.py` rather than guessed
#: (lines 378, 511, 583, 632, 688, 761 x2, 881, 911, 970).
_HONESTY_STAMPS: frozenset[str] = frozenset(
    {
        "wallet_secret_solicitation_blocked",
        "unsupported_inspection_claim",
        "forbidden_term_blocked",
        "leaked_tool_call_suppressed",
        "answer_binding_regrounded",
        "evidence_contradiction_repaired",
        "unprovable_runtime_claim_repaired",
        "fabricated_progress_blocked",
        "false_action_claim_blocked",
        "unfetched_url_claim_blocked",
    }
)

#: Clusters that never produce an answer of their own and so can never be the claimant.
_NON_CLAIMANT_CLUSTERS: frozenset[str] = frozenset(
    {"turn_boundary", "authority", "dispatch_emitter", "answer_coverage"}
)

#: Symbols that return a bool/str VERDICT about the turn and never an answer. They are real parts
#: of the consultation sequence and keep their `claim_order`, but they cannot be the dispatch winner,
#: so `emitter_adjacency` must not name one.
#:
#: Why this is needed: adjacency is positional, and when the lane that actually produced an answer is
#: one of the ~62 UNREGISTERED claim points, the nearest preceding claim is whatever turn-shape
#: predicate happened to run last. Measured on set-6 L6.2 (`route=live_info_fast_path`) and L19.1
#: (`route=workspace_runtime_fast_path`): both named `turn_may_hold_several_requests`, a planner
#: predicate that returns a bool and cannot have written either answer. Excluding these makes those
#: turns resolve to `unresolved` -- which is the truthful outcome -- instead of naming a neighbour.
_NEVER_PRODUCER_SYMBOLS: frozenset[str] = frozenset(
    {
        "turn_may_hold_several_requests",
        "is_build_instruction",
        "is_opted_out",
        "looks_like_builder_request",
        "looks_like_grounded_price_lookup",
        "looks_like_personalized_plan_request",
        "asks_for_dynamic_currency_value",
        "static_currency_identity_admitted",
        "_recent_price_subject",
        "slice_binding_is_unsafe",
        "intake_request_text",
        "live_request_after_retraction",
    }
)


def _sibling_authority(
    events: Sequence[dict[str, Any]], claim: dict[str, Any]
) -> list[str]:
    """Authority verdicts consulted FOR this claim by its CALLER, not inside the claimant itself.

    Nesting alone under-reports, and the currency lane is the proof: `turn_frontdoor._currency_reply`
    calls `analyze_retrieval_constraints(text)` and only afterwards calls `currency_fast_path(text,
    **extra)`. The authority is a SIBLING of the claim, not a child of it, so a purely nesting-based
    reading calls that claim `none_consulted` -- true of the function, false of the lane, and the
    difference is exactly the "did anyone ask the authority" question this field exists to answer.

    So: walk backwards from the claim for authority exits over the SAME text (same prompt prefix) at
    the same or shallower depth, and report them as `sibling:`. Same-text is what keeps another
    lane's verdict on a different clause from being borrowed.
    """
    prefix = str(claim.get("prompt_prefix") or "")
    if not prefix:
        return []
    depth = int(claim.get("depth") or 0)
    seq = int(claim.get("seq") or 0)
    found: list[str] = []
    for event in reversed(events):
        if int(event.get("seq") or 0) >= seq:
            continue
        if event.get("kind") != "exit" or event.get("cluster") != "authority":
            continue
        if int(event.get("depth") or 0) > depth:
            continue
        if str(event.get("prompt_prefix") or "") != prefix:
            continue
        found.append(f"sibling:{event.get('symbol')}({event.get('reason') or 'no_constraint'})")
        break
    return found


@dataclass
class TurnResolution:
    """One turn's resolved dispatch picture. Every field is either observed or explicitly blank."""

    turn_id: str
    route: str = ""
    model_ran: Any = ""
    tool: str = ""
    answer: str = ""
    actual_claimant: str = ""
    claim_reason: str = ""
    authority_basis: str = ""
    claim_order: int | None = None
    won_dispatch: str = "unresolved"
    won_basis: str = ""
    emitter_reason: str = ""
    #: A raw-output transformer that CHANGED the answer after its producer made it. Recorded
    #: separately so a postprocessor is never mistaken for the producer -- and so a format/contract
    #: miss stays visible on a turn whose content came from the model.
    post_transform: str = ""
    claimants: list[str] = field(default_factory=list)
    events: int = 0


def load_events(jsonl_path: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    with open(jsonl_path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def group_by_turn(events: Sequence[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Segment a JSONL trace per turn using the request's OWN `turn_id`.

    Timestamps would also work for a strictly serial driver, but turn_id is exact and survives a
    driver that ever stops being serial. Events emitted before the first boundary (module import,
    prewarm) land under "" and are reported rather than silently attached to turn 1.
    """
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        grouped.setdefault(str(event.get("turn_id") or ""), []).append(event)
    for bucket in grouped.values():
        bucket.sort(key=lambda e: e.get("seq", 0))
    return grouped


def resolve_turn(turn_id: str, events: Sequence[dict[str, Any]]) -> TurnResolution:
    """Resolve one turn's actual claimant + won_dispatch from observed facts only.

    Resolution order:

      1. **Model.** The turn's own provenance says `model_ran=True` AND the route is a model route.
         This is FIRST, not last, and the ordering is load-bearing: `apply_raw_output_contract` runs
         after the model and rewrites its text (stripping a fence, binding a literal), so on a
         model turn it text-matches the served answer and would be credited as the producer.
         It is a POSTPROCESSOR of the model's answer, and is recorded as `post_transform` instead.
         Basis `provenance_model_ran`.
      2. **Emitter text match.** `fast_path_result` ran; find the claimant whose own `response_text`
         equals the text the emitter emitted. Basis `emitter_text_match`.
      3. **Served text match**, then **served text containment** (>= 40 chars, so a one-word
         coincidence cannot trip it) against the answer `dispatch_post` actually served.
      4. **Emitter adjacency.** The emitter stamped the winning route but no claimant's text matches
         because that lane returns a PLAN rather than prose (`build_live_data_plan` ->
         `route=live_data_typed_plan` is the real case). The claimant is then the last claim made
         before that emitter at the same or shallower depth. Basis `emitter_adjacency` -- weaker
         than the others, POSITIONAL rather than text-identical, and labelled so in every report.

    Text matches scan claimants in FORWARD order. Producers run before the transformers and
    validators that reshape their output, so the earliest text-identical claim is the producer; the
    later ones are handling text they were given.

    Nothing else resolves it. A turn whose winner is one of the ~62 unregistered claim points stays
    `unresolved` with an empty claimant, and the caller counts it rather than attributing it to the
    nearest registered probe.
    """
    resolution = TurnResolution(turn_id=turn_id, events=len(events))

    served = [e for e in events if e.get("kind") == "turn" and e.get("note") == "served"]
    if served:
        args = served[-1].get("call_args") or {}
        resolution.route = str(args.get("route") or "")
        resolution.model_ran = args.get("model_ran", "")
        resolution.tool = str(args.get("tool") or "")
        resolution.answer = str(args.get("answer") or "")

    exits = [e for e in events if e.get("kind") == "exit"]
    claimants = [
        e
        for e in exits
        # `is_claim` is absent from traces written before it existed; fall back to truthiness there.
        if (e.get("is_claim") if e.get("is_claim") is not None else e.get("returned_truthy"))
        and e.get("cluster") not in _NON_CLAIMANT_CLUSTERS
        # An honesty validator counts only when the turn ended on a route IT stamps; otherwise it
        # merely handed back the dict it was given (see _HONESTY_STAMPS).
        and not (e.get("cluster") == "honesty" and resolution.route not in _HONESTY_STAMPS)
    ]
    resolution.claimants = sorted({str(e.get("symbol")) for e in claimants})

    emitters = [e for e in exits if e.get("cluster") == "dispatch_emitter"]
    if emitters:
        resolution.emitter_reason = str((emitters[-1].get("call_args") or {}).get("reason") or "")

    transforms = [
        e
        for e in exits
        if e.get("symbol") == "apply_raw_output_contract" and e.get("is_claim")
    ]
    if transforms:
        resolution.post_transform = str(transforms[-1].get("reason") or "")

    def _adopt(event: dict[str, Any], basis: str) -> None:
        resolution.actual_claimant = str(event.get("symbol") or "")
        resolution.claim_reason = str(event.get("reason") or "")
        nested = [f"nested:{call}" for call in (event.get("authority_calls") or [])]
        basis_calls = nested + _sibling_authority(events, event)
        resolution.authority_basis = "; ".join(basis_calls) or "none_consulted"
        resolution.claim_order = event.get("claim_order")
        resolution.won_dispatch = "yes"
        resolution.won_basis = basis

    # 1. the model produced the text (see the docstring for why this outranks the text matches)
    model_ran = str(resolution.model_ran).lower() in {"true", "1"}
    if model_ran and resolution.route.startswith(_MODEL_ROUTE_PREFIXES):
        resolution.actual_claimant = "(model)"
        resolution.claim_reason = f"route={resolution.route}"
        consulted = [
            call
            for event in claimants
            for call in (event.get("authority_calls") or [])
        ]
        resolution.authority_basis = "; ".join(sorted(set(consulted))) or "none_consulted"
        resolution.won_dispatch = "yes"
        resolution.won_basis = "provenance_model_ran"
        return resolution

    # 2. emitter text match
    if emitters:
        emitted = _norm(emitters[-1].get("response_text") or "")
        if not emitted:
            # fast_path_result returns a result dict; its `response` kwarg is not in _ARG_FIELDS
            # because it can be long, so fall back to the served answer for the comparison.
            emitted = _norm(resolution.answer)
        if emitted:
            emitter_seq = int(emitters[-1].get("seq") or 0)
            matches = [
                e
                for e in claimants
                if _norm(e.get("response_text") or "") == emitted
                and int(e.get("seq") or 0) < emitter_seq
            ]
            if matches:
                # The LAST match before the emitter, not the first. A symbol is often consulted
                # more than once per turn -- an admission PROBE first, then the dispatching call --
                # and when both return the same text a first-match scan credits the probe.
                # Measured on set-6 L9.3: `currency_fast_path` claims at order 7 from
                # `closed_semantic_contract_covers_turn` (which consults no authority) and again at
                # order 14 from `_currency_reply` (which consults `analyze_retrieval_constraints`
                # at order 13, immediately before). Order 14 is what reaches `fast_path_result`.
                # First-match reported the L9.3 local lane as `none_consulted` while the cloud
                # lane -- where the two calls happened to return DIFFERENT text -- correctly
                # reported the authority. Same defect, two different stories: a resolver bug, not a
                # lane difference.
                _adopt(matches[-1], "emitter_text_match")
                return resolution

    # 3. served text match / containment
    answer = _norm(resolution.answer)
    if answer:
        for event in claimants:
            candidate = _norm(event.get("response_text") or "")
            if candidate and candidate == answer:
                _adopt(event, "served_text_match")
                return resolution
        for event in claimants:
            candidate = _norm(event.get("response_text") or "")
            if len(candidate) >= 40 and candidate in answer:
                _adopt(event, "served_text_contains")
                return resolution
        # And the reverse containment: the claimant produced MORE than what was served, because the
        # runtime appends a provenance footer inside the result dict and strips it on the way out
        # (`core/response_provenance.py:strip_provenance_footer`). Measured on L13.1: the validator's
        # own text is the served answer plus "`cloud | nemotron-3.5-lightning:free | 15,565 tok`".
        # Without this the honesty-rewrite turns resolve to `unresolved` even though the trace shows
        # exactly which validator wrote the text.
        for event in claimants:
            candidate = _norm(event.get("response_text") or "")
            if len(answer) >= 40 and answer in candidate:
                _adopt(event, "claim_text_contains_served")
                return resolution

    # 4. emitter adjacency -- positional, weaker, and labelled as such
    if emitters and resolution.emitter_reason and resolution.emitter_reason == resolution.route:
        emitter = emitters[-1]
        emitter_seq = int(emitter.get("seq") or 0)
        emitter_depth = int(emitter.get("depth") or 0)
        preceding = [
            e
            for e in claimants
            if int(e.get("seq") or 0) < emitter_seq
            and int(e.get("depth") or 0) <= emitter_depth + 1
            and str(e.get("symbol")) not in _NEVER_PRODUCER_SYMBOLS
        ]
        if preceding:
            _adopt(preceding[-1], "emitter_adjacency")
            return resolution

    return resolution


def report_turn(turn_id: str, events: Sequence[dict[str, Any]]) -> str:
    """Readable ordered trace for one turn -- the debugger surface."""
    resolution = resolve_turn(turn_id, events)
    lines = [f"TURN {turn_id}  route={resolution.route!r} model_ran={resolution.model_ran}", "=" * 92]
    for event in events:
        if event.get("kind") == "turn":
            lines.append(f"    [served] route={event.get('call_args', {}).get('route')!r}")
            continue
        if event.get("kind") != "exit":
            continue
        indent = "  " * int(event.get("depth") or 0)
        verdict = "CLAIMED " if event.get("returned_truthy") else "declined"
        order = event.get("claim_order")
        head = f"[{order:>3}]" if order else "[   ]"
        lines.append(
            f"{head} {indent}{verdict} {event.get('symbol')} ({event.get('cluster')})"
            f" reason={event.get('reason')!r}"
        )
        for call in event.get("authority_calls") or []:
            lines.append(f"        {indent}^ authority: {call}")
    lines.append("-" * 92)
    lines.append(
        f"actual_claimant={resolution.actual_claimant!r} won_dispatch={resolution.won_dispatch}"
        f" basis={resolution.won_basis!r} reason={resolution.claim_reason!r}"
    )
    lines.append(f"authority_basis={resolution.authority_basis!r}")
    return "\n".join(lines)
