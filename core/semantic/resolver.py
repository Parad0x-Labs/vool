"""The model-backed ``SemanticResolver``: interpret a turn into a typed ``RequestGraph``.

The model INTERPRETS meaning; it never gains authority. Its output is a graph of requests, slots,
operands by role, constraints, prohibitions, retractions and dependencies -- "what does the user
appear to mean?" -- and nothing in it decides what may execute. ``core.semantic.admission`` re-enters
the real permission policy for every operation, every time. A model-filled field is untrusted
input, by type.

Two disciplines are load-bearing:

* **Two phases, no transport.** ``prepare`` builds the prompts, the provider schema and the
  versions; ``finish`` parses a reply. Both are pure. The call that actually reaches a model is made
  OUTSIDE ``core/semantic`` (the runtime's shadow seam, an evaluation script), so this package never
  has a frame on a provider call's stack and the resolver carries no per-turn state. The optional
  ``backend`` is a convenience for tests and offline evaluation (``interpret``/``propose``); the
  runtime does not use it.
* **Abstain-and-fallback -- NOT "fail-open".** A reply that is not a graph (empty, not JSON, too
  large) makes ``finish`` return ``None``: the resolver ABSTAINS. Abstention is not a guess and not
  a verdict about the turn; the deterministic reading stays authoritative, UNKNOWN stays UNKNOWN,
  and no tool or action authority is granted -- there is none here to grant. A reply that IS a
  graph but could not interpret part of the text does not abstain: the uninterpreted text is
  preserved as UNRESOLVED obligations (see ``core.semantic.graph_parser``).

Spans are bound by TEXT, never by model offsets: the model copies verbatim substrings, the parser
locates them, and an entity that is not in the message mints no span.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from core.semantic.canonical_text import CanonicalText
from core.semantic.graph_parser import parse_graph_reply
from core.semantic.request_graph import RequestGraph
from core.semantic.types import IntentProposal

#: A model backend is any ``(system_prompt, user_prompt) -> reply_text`` callable -- the same shape
#: as the conductor's ``propose_semantics`` seam. Tests inject a deterministic stub; evaluation
#: scripts inject a bounded HTTP call; the runtime performs the call itself from its shadow seam.
SemanticProposalBackend = Callable[[str, str], str]

PROMPT_VERSION = "vool.semantic_graph_prompt.v1"
REPLY_SCHEMA_VERSION = "vool.semantic_graph_reply.v1"

#: How many requests one reply may interpret. Beyond this the remaining text is CONSERVED as
#: unresolved obligations rather than dropped (the earlier cap truncated silently).
_MAX_REQUESTS = 24

#: A reply this large is refused unparsed -- a defense against a backend that streams an essay.
_MAX_REPLY_CHARS = 200_000


class _Breaker:
    """Open after ``threshold`` consecutive failures; stay open for ``cooloff_s``.

    A cold or unreachable backend must not be re-tried on every single turn. This is backend HEALTH
    state, deliberately shared across turns; it holds no text, no prompt and no reply.
    """

    def __init__(self, *, threshold: int = 3, cooloff_s: float = 30.0) -> None:
        self._threshold = int(threshold)
        self._cooloff_s = float(cooloff_s)
        self._consecutive = 0
        self._open_until = 0.0
        self._lock = threading.Lock()

    def is_open(self, *, now: float | None = None) -> bool:
        moment = time.monotonic() if now is None else now
        with self._lock:
            return moment < self._open_until

    def record_success(self) -> None:
        with self._lock:
            self._consecutive = 0
            self._open_until = 0.0

    def record_failure(self, *, now: float | None = None) -> None:
        moment = time.monotonic() if now is None else now
        with self._lock:
            self._consecutive += 1
            if self._consecutive >= self._threshold:
                self._open_until = moment + self._cooloff_s


@dataclass(frozen=True)
class PreparedInterpretation:
    """Everything one interpretation call needs, built before any transport is touched.

    ``cache_key`` is the turn-safe identity of the question: the turn, the text digest and every
    version that shapes the reply. Two different turns never share it, and a prompt, schema or
    catalog change invalidates every cached answer at once.
    """

    turn_id: str
    canonical: CanonicalText
    operations: tuple[str, ...]
    catalog_version: str
    system_prompt: str
    user_prompt: str
    prompt_version: str
    schema_version: str
    json_schema: dict[str, Any]
    cache_key: str


def catalog_version(operations: Sequence[str], descriptions: Mapping[str, str] | None = None) -> str:
    """A short digest of the operation menu offered (names + descriptions), for telemetry and cache identity."""
    rows = sorted((str(op), str((descriptions or {}).get(op, ""))) for op in operations)
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode("utf-8")).hexdigest()[:12]


def graph_reply_json_schema() -> dict[str, Any]:
    """Provider-native shape for the reply. Every value is a pointer (verbatim text, a key the reply
    minted, an enum) or a quantity; there is no field an operation parameter could be written into."""
    operand = {
        "type": "object",
        "properties": {
            "role": {"type": "string"},
            "text": {"type": "string"},
            "occurrence": {"type": "integer"},
            "entity": {"type": "string"},
            "quantity": {
                "type": "object",
                "properties": {
                    "raw": {"type": "string"}, "exact": {"type": "string"}, "unit": {"type": "string"},
                    "currency": {"type": "string"}, "asset": {"type": "string"},
                },
                "additionalProperties": True,
            },
            "result_of": {"type": "string"},
        },
        "required": ["role"],
        "additionalProperties": True,
    }
    slot = {
        "type": "object",
        "properties": {
            "key": {"type": "string"},
            "expected": {"type": "string"},
            "state": {"type": "string"},
            "reason": {"type": "string"},
            "operands": {"type": "array", "items": operand, "maxItems": 12},
            "ambiguity": {
                "type": "object",
                "properties": {
                    "alternatives": {"type": "array", "items": {"type": "string"}},
                    "reason": {"type": "string"},
                    "needs_clarification": {"type": "boolean"},
                },
                "additionalProperties": True,
            },
        },
        "required": ["expected"],
        "additionalProperties": True,
    }
    request = {
        "type": "object",
        "properties": {
            "key": {"type": "string"},
            "source": {"type": "string"},
            "state": {"type": "string"},
            "reason": {"type": "string"},
            "operation": {"type": ["string", "null"]},
            "slots": {"type": "array", "items": slot, "maxItems": 12},
            "depends_on": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"request": {"type": "string"}, "kind": {"type": "string"}, "slot": {"type": "string"}},
                    "required": ["request"],
                    "additionalProperties": True,
                },
            },
        },
        "required": ["key", "source"],
        "additionalProperties": True,
    }
    scoped = {
        "type": "object",
        "properties": {
            "kind": {"type": "string"}, "text": {"type": "string"}, "detail": {"type": "string"},
            "target": {"type": "string"}, "requests": {"type": "array", "items": {"type": "string"}},
            "supersedes": {"type": "string"},
        },
        "additionalProperties": True,
    }
    return {
        "type": "object",
        "properties": {
            "requests": {"type": "array", "items": request, "maxItems": _MAX_REQUESTS},
            "constraints": {"type": "array", "items": scoped},
            "prohibitions": {"type": "array", "items": scoped},
            "retractions": {"type": "array", "items": scoped},
            "presentation": {
                "type": "object",
                "properties": {
                    "format": {"type": "string"}, "fields": {"type": "array", "items": {"type": "string"}},
                    "literal_output": {"type": "boolean"},
                },
                "additionalProperties": True,
            },
        },
        "required": ["requests"],
        "additionalProperties": True,
    }


class ModelSemanticResolver:
    """Interprets, never guesses, never authorizes. Structurally cannot carry authority: its outputs
    are a ``RequestGraph`` and, as a projection, ``IntentProposal``s -- neither has a field for
    side effects, approvals or permissions."""

    name = "model_semantic_resolver"

    def __init__(
        self,
        backend: SemanticProposalBackend | None = None,
        *,
        catalog_descriptions: Mapping[str, str] | None = None,
        breaker: _Breaker | None = None,
        max_requests: int = _MAX_REQUESTS,
        max_reply_chars: int = _MAX_REPLY_CHARS,
    ) -> None:
        self._backend = backend
        self._descriptions = dict(catalog_descriptions or {})
        self._breaker = breaker or _Breaker()
        self._max_requests = int(max_requests)
        self._max_reply_chars = int(max_reply_chars)

    # -- phase 1: prepare (pure) ------------------------------------------------

    def prepare(
        self, canonical: CanonicalText, *, operations: Sequence[str], turn_id: str = ""
    ) -> PreparedInterpretation | None:
        text = canonical.text.strip() if canonical is not None else ""
        operation_names = tuple(dict.fromkeys(str(op) for op in operations if str(op or "").strip()))
        if not text or not operation_names:
            return None
        if self._breaker.is_open():
            return None
        version = catalog_version(operation_names, self._descriptions)
        identity = "|".join((str(turn_id or ""), canonical.digest, PROMPT_VERSION, REPLY_SCHEMA_VERSION, version))
        return PreparedInterpretation(
            turn_id=str(turn_id or ""),
            canonical=canonical,
            operations=operation_names,
            catalog_version=version,
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=self._build_user_prompt(canonical.text, operation_names),
            prompt_version=PROMPT_VERSION,
            schema_version=REPLY_SCHEMA_VERSION,
            json_schema=graph_reply_json_schema(),
            cache_key=hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24],
        )

    def _build_user_prompt(self, text: str, operations: tuple[str, ...]) -> str:
        lines = ["OPERATIONS (name a request's operation only from this list, or null):"]
        for name in operations:
            description = str(self._descriptions.get(name, "")).strip()
            lines.append(f"  {name}" + (f" - {description}" if description else ""))
        lines.append("")
        lines.append("USER MESSAGE (verbatim, between the markers):")
        lines.append("<<<")
        lines.append(text)
        lines.append(">>>")
        return "\n".join(lines)

    # -- phase 2: finish (pure) -------------------------------------------------

    def finish(self, prepared: PreparedInterpretation, reply: Any) -> RequestGraph | None:
        """The graph for ``reply``, or None when the resolver abstains. Records backend health."""
        parsed = parse_graph_reply(
            reply,
            canonical=prepared.canonical,
            turn_id=prepared.turn_id,
            operations=prepared.operations,
            max_requests=self._max_requests,
            max_reply_chars=self._max_reply_chars,
        )
        if parsed.abstained or parsed.graph is None:
            self._breaker.record_failure()
            return None
        self._breaker.record_success()
        return parsed.graph

    def record_transport_failure(self) -> None:
        """A transport that raised or timed out counts against backend health too."""
        self._breaker.record_failure()

    # -- convenience for tests and offline evaluation (needs the optional backend) -

    def interpret(self, canonical: CanonicalText, *, operations: Sequence[str], turn_id: str = "") -> RequestGraph | None:
        """prepare -> backend -> finish. Any backend failure abstains; nothing propagates."""
        if self._backend is None:
            raise RuntimeError("interpret() needs a backend; the runtime uses prepare()/finish() with its own transport")
        prepared = self.prepare(canonical, operations=operations, turn_id=turn_id)
        if prepared is None:
            return None
        try:
            reply = self._backend(prepared.system_prompt, prepared.user_prompt)
        except Exception:
            self.record_transport_failure()
            return None
        return self.finish(prepared, reply)

    def propose(self, canonical: CanonicalText, *, operations: Sequence[str]) -> tuple[IntentProposal, ...]:
        """The legacy clause projection: ``interpret`` then ``proposals_from_graph``. Empty on abstain."""
        graph = self.interpret(canonical, operations=operations)
        return proposals_from_graph(graph) if graph is not None else ()


# -- projection: graph -> clauses (for the admission path) --------------------


def proposals_from_graph(graph: RequestGraph) -> tuple[IntentProposal, ...]:
    """One ``IntentProposal`` per live request, in a dependency-respecting order.

    Stable ids ride along (``request_id``/``slot_ids``); ``depends_on`` indices are derived from the
    graph's edges so a downstream consumer that still speaks positions gets positions that agree
    with the ids. A request a Retraction superseded (kind ``request``, or every slot withdrawn by
    kind ``slot``) is excluded -- a cancelled obligation, not a clause to run -- and a partially
    withdrawn request keeps only its live slots; unresolved requests are kept with operation
    ``unknown`` so nothing that was asked disappears from the count admission accounts for.
    """
    superseded_requests = {r.supersedes for r in graph.retractions if r.supersedes and r.supersedes_kind == "request"}
    superseded_slots = {r.supersedes for r in graph.retractions if r.supersedes and r.supersedes_kind == "slot"}
    live = []
    for r in graph.requests:
        if r.id in superseded_requests:
            continue
        if r.slot_ids and all(sid in superseded_slots for sid in r.slot_ids):
            continue  # every slot of the request was withdrawn: nothing left to run
        live.append(r)
    by_id = {r.id: r for r in live}
    # Every kind of edge orders the dependent after its prerequisite, CONDITIONAL included.
    edges = [(d.from_request, d.to_request) for d in graph.dependencies
             if d.from_request in by_id and d.to_request in by_id]
    prerequisites: dict[str, set[str]] = {r.id: set() for r in live}
    for frm, to in edges:
        prerequisites[frm].add(to)
    ordered: list[str] = []
    remaining = [r.id for r in live]
    while remaining:
        ready = [rid for rid in remaining if prerequisites[rid] <= set(ordered)]
        if not ready:
            # Unreachable for a valid graph (construction refuses cycles); stated rather than
            # silently ordered so a future law change cannot turn into a dropped edge here.
            raise ValueError("dependency edges do not admit an order; the graph is not acyclic")
        ordered.append(ready[0])
        remaining.remove(ready[0])
    index_of = {rid: i for i, rid in enumerate(ordered)}
    mentions = {m.id: m for m in graph.mentions}
    proposals: list[IntentProposal] = []
    for rid in ordered:
        request = by_id[rid]
        slots = tuple(s for s in graph.slots_for(rid) if s.id not in superseded_slots)
        spans = tuple(
            mentions[op.mention_id].span
            for slot in slots for op in slot.operands
            if op.mention_id is not None and op.mention_id in mentions
        )
        depends_on = tuple(sorted(index_of[p] for p in prerequisites[rid] if index_of[p] < index_of[rid]))
        proposals.append(
            IntentProposal(
                index=index_of[rid],
                request_text=request.source_text,
                operation=request.family or "unknown",
                arguments={},
                spans=spans,
                depends_on=depends_on,
                origin="model",
                request_id=request.id,
                slot_ids=tuple(s.id for s in slots),
            )
        )
    return tuple(proposals)


_SYSTEM_PROMPT = """You interpret a user's message into a structured description of what was asked. You do NOT
answer the message and you do NOT run anything -- you only describe its meaning.

Return ONLY one JSON object:
{"requests": [ {"key": "r1", "source": "<this request in the user's own words, copied verbatim>",
                 "state": "resolved" | "ambiguous" | "unknown_meaning" | "answerable_without_tool" |
                          "unsupported_capability" | "needs_input" | "tool_forbidden" | "retrieval_forbidden",
                 "reason": "<why, when the state is not resolved>",
                 "operation": "<ONE operation name from OPERATIONS, or null>",
                 "slots": [ {"key": "s1", "expected": "<what a satisfying answer must contain>",
                             "state": "<same vocabulary as above>", "reason": "",
                             "operands": [ {"role": "subject"|"source"|"target"|"payment"|"destination"|
                                                    "comparison_a"|"comparison_b"|"other",
                                            "text": "<entity copied verbatim from the message>",
                                            "occurrence": <0-based index if the same words appear more than once>,
                                            "entity": "<canonical identity: crypto by ticker (BTC), fiat by ISO code (EUR),
                                                       equities by ticker (TSLA), commodities lower-case (gold), places/products by name>",
                                            "quantity": {"raw": "<as written>", "exact": "<number>", "unit": "", "currency": "", "asset": ""} },
                                          {"role": "payment", "result_of": "<key of an EARLIER slot whose result is used>"} ],
                             "ambiguity": {"alternatives": ["gold", "silver"], "reason": "", "needs_clarification": true} } ],
                 "depends_on": [ {"request": "<key of an earlier request>", "kind": "value"|"ordering"|"conditional", "slot": "<its slot key>"} ] } ],
 "constraints": [ {"kind": "time"|"location"|"freshness"|"retrieval_required"|"retrieval_forbidden"|"tool_forbidden"|"output_format"|"user_restriction",
                   "text": "<the cue copied verbatim, if any>", "detail": "", "requests": ["r1"]} ],
 "prohibitions": [ {"target": "<the forbidden thing, lower-case: web, tools, gold, twitter>", "text": "<the clause verbatim>", "requests": []} ],
 "retractions": [ {"target": "<what was withdrawn, lower-case: search>", "text": "<the clause verbatim>", "supersedes": "r1"} ],
 "presentation": {"format": "prose"|"table"|"json"|"literal"|"list"|"unspecified", "fields": [], "literal_output": false} }

Rules:
- One request per thing asked; one slot per answer the request needs (a price for each of three metals is three slots).
- Every "source", "text" value is copied VERBATIM from the message so it can be located. Never paraphrase them.
- Roles are by MEANING, never by word order: in "how much silver can I buy with 1 BTC" silver is the target and BTC the payment.
- "gold or silver" as one target is ONE slot with "ambiguity" listing both; "if I have 1 btc or 1 eth" is two slots.
- A request without a stated amount or asset it needs is "needs_input" with the reason.
- A "do not ..." / "no web" is a prohibition (and a retrieval_forbidden or tool_forbidden constraint), NOT a request.
- "cancel that" / "ignore the second question" is a retraction naming the request it withdraws; keep that request listed.
- "if X then Y" is two requests with Y depending on X with kind "conditional"; "with it"/"that" uses "result_of" and a "value" dependency.
- Mark "retrieval_required" for anything needing live or looked-up data, "freshness" only for a stated recency cue (now, latest, today).
- Choose an operation name EXACTLY as spelled in OPERATIONS, or null. Never guess an operation.
- If nothing is being asked, return {"requests": []} together with any prohibitions/retractions present.
- Output the JSON object and nothing else."""


__all__ = [
    "PROMPT_VERSION",
    "REPLY_SCHEMA_VERSION",
    "ModelSemanticResolver",
    "PreparedInterpretation",
    "SemanticProposalBackend",
    "catalog_version",
    "graph_reply_json_schema",
    "proposals_from_graph",
]
