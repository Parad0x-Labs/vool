"""The SHADOW runtime: run the graph resolver beside a turn -- bounded, detached, observe-only.

This is the ONLY place a model transport meets ``core.semantic``'s resolver, and it lives outside
that package on purpose: the resolver's ``prepare``/``finish`` are pure, the transport is called
from here, and no frame of ``core/semantic`` is ever on a provider call's stack.

What the earlier shadow seam got wrong, and what this does instead:

* it WAITED on the turn's thread up to a timeout and abandoned the worker -- here ``submit`` returns
  a ticket immediately; the turn never waits, and the pool is bounded (``max_workers``) with a
  bounded queue (``max_queue``): an overflow is recorded as a ``queue_full`` observation, not a
  growing backlog;
* the worker inherited the caller's ContextVars -- here every job runs in a FRESH
  ``contextvars.Context()``, so the turn's reach recorder, admission state and obligation binding
  are simply absent: a shadow job cannot write into the turn's ledgers by accident;
* the resolver retained turn state -- here the per-turn identity (turn, request digest, prompt /
  schema / catalog versions) lives on the ``ShadowJob``, the resolver is stateless across turns,
  and the job carries neither ``source_context`` nor any prompt/reply text after completion;
* telemetry was a count bit -- here every completion writes a text-free v2 observation (versions,
  latency, typed failure, per-axis graph differential) to the ring and, when the job carried a
  session, to the runtime ledger as ``semantic_shadow_observation``.

Deadline: the transport is bound by the runtime's provider deadline (``bind_provider_deadline``),
and the job carries its own cancel event; a completion after the job's deadline is recorded with
``failure_type=timeout`` (or ``late=True`` when a result still arrived) and never enters any turn.
The turn's own model-call ledger keys are stripped from the transport's call context, so a shadow
call is never counted as the turn's provider call: observation is observational.
"""
from __future__ import annotations

import contextlib
import contextvars
import os
import queue
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from core import runtime_active_clock
from core.semantic.shadow_observation import (
    FAILURE_ABSTAINED,
    FAILURE_BACKEND_ERROR,
    FAILURE_CANCELLED,
    FAILURE_NONE,
    FAILURE_NOT_PREPARED,
    FAILURE_PARSE_ERROR,
    FAILURE_QUEUE_FULL,
    FAILURE_TIMEOUT,
    SHADOW_OBSERVATION_EVENT,
    ShadowObservationV2,
    build_observation,
    record_observation,
)

#: ``(system_prompt, user_prompt, json_schema) -> reply text``. The runtime builds one per turn from
#: its bounded unpaid-manifest seam; tests inject a stub. Raising or returning None = backend error.
ShadowTransport = Callable[[str, str, dict[str, Any] | None], Any]

_DEFAULT_WORKERS = 2
_DEFAULT_QUEUE = 8
_DEFAULT_DEADLINE_S = 25.0
_WORKERS_ENV = "VOOL_SEMANTIC_SHADOW_WORKERS"
_QUEUE_ENV = "VOOL_SEMANTIC_SHADOW_QUEUE"
_DEADLINE_ENV = "VOOL_SEMANTIC_SHADOW_DEADLINE_S"
POLICY_VERSION = "vool.semantic_shadow_policy.v1"


@dataclass(frozen=True)
class ShadowJob:
    """Everything one shadow run needs, frozen at submit. No source_context, no text besides the
    canonical (whose digest is what gets recorded)."""

    ticket_id: str
    turn_id: str
    session_id: str
    canonical: Any
    operations: tuple[str, ...]
    heuristic_graph: Any
    resolver: Any
    transport: ShadowTransport
    mode: str
    deadline_monotonic: float
    submitted_monotonic: float
    provider: str = ""
    model: str = ""
    cancel: threading.Event = field(default_factory=threading.Event)


class ShadowTicket:
    """The turn's handle: identity and eventual observation. Never something a turn waits on."""

    def __init__(self, job_id: str, turn_id: str) -> None:
        self.ticket_id = job_id
        self.turn_id = turn_id
        self._done = threading.Event()
        self._observation: ShadowObservationV2 | None = None
        self._status = "queued"

    @property
    def status(self) -> str:
        return self._status

    @property
    def observation(self) -> ShadowObservationV2 | None:
        return self._observation

    def _finish(self, observation: ShadowObservationV2, status: str = "done") -> None:
        self._observation = observation
        self._status = status
        self._done.set()

    def wait(self, timeout: float | None = None) -> bool:
        """For TESTS and drains only. The turn never calls this."""
        return self._done.wait(timeout)

    def to_dict(self) -> dict[str, Any]:
        return {"ticket_id": self.ticket_id, "turn_id": self.turn_id, "status": self._status}


class ShadowRuntime:
    def __init__(
        self,
        *,
        max_workers: int = _DEFAULT_WORKERS,
        max_queue: int = _DEFAULT_QUEUE,
        deadline_s: float = _DEFAULT_DEADLINE_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_workers = max(1, int(max_workers))
        self._queue: queue.Queue[tuple[ShadowJob, ShadowTicket] | None] = queue.Queue(maxsize=max(1, int(max_queue)))
        self._deadline_s = float(deadline_s)
        self._clock = clock
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()
        self._closed_turns: set[str] = set()
        self._stats: dict[str, int] = {"submitted": 0, "dropped": 0, "completed": 0, "late": 0}
        self._failures: dict[str, int] = {}
        self._running = 0
        self._peak_running = 0
        self._stopped = False

    # -- lifecycle ---------------------------------------------------------------------------------

    def _ensure_workers(self) -> None:
        with self._lock:
            while len(self._threads) < self._max_workers and not self._stopped:
                thread = threading.Thread(target=self._worker, name=f"semantic-shadow-{len(self._threads)}", daemon=True)
                thread.start()
                self._threads.append(thread)

    def shutdown(self, *, wait: bool = True, timeout: float = 5.0) -> None:
        with self._lock:
            self._stopped = True
            threads = list(self._threads)
        for _ in threads:
            with contextlib.suppress(queue.Full):
                self._queue.put_nowait(None)
        if wait:
            deadline = time.monotonic() + timeout
            for thread in threads:
                thread.join(max(0.0, deadline - time.monotonic()))

    def close_turn(self, turn_id: str) -> None:
        """The turn finished: a result arriving after this is recorded as LATE and can change nothing."""
        with self._lock:
            self._closed_turns.add(str(turn_id))

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {**self._stats, "failures": dict(self._failures), "queued": self._queue.qsize(),
                    "peak_running": self._peak_running, "workers": len(self._threads)}

    # -- submit -----------------------------------------------------------------------------------

    def submit(
        self,
        *,
        turn_id: str,
        session_id: str,
        canonical: Any,
        operations: Sequence[str],
        heuristic_graph: Any,
        resolver: Any,
        transport: ShadowTransport,
        mode: str = "shadow",
        provider: str = "",
        model: str = "",
        deadline_s: float | None = None,
    ) -> ShadowTicket:
        """Queue one shadow run and return at once. Overflow is a recorded ``queue_full`` observation."""
        ticket = ShadowTicket(f"shadow-{uuid.uuid4().hex[:12]}", str(turn_id))
        now = self._clock()
        job = ShadowJob(
            ticket_id=ticket.ticket_id, turn_id=str(turn_id), session_id=str(session_id or ""),
            canonical=canonical, operations=tuple(str(op) for op in operations), heuristic_graph=heuristic_graph,
            resolver=resolver, transport=transport, mode=str(mode),
            deadline_monotonic=now + float(deadline_s if deadline_s is not None else self._deadline_s),
            submitted_monotonic=now, provider=str(provider), model=str(model),
        )
        with self._lock:
            self._stats["submitted"] += 1
        self._ensure_workers()
        try:
            self._queue.put_nowait((job, ticket))
        except queue.Full:
            observation = self._observation_for(job, failure=FAILURE_QUEUE_FULL, latency_ms=0.0)
            self._record(job, ticket, observation, status="dropped")
            with self._lock:
                self._stats["dropped"] += 1
            return ticket
        return ticket

    # -- worker ----------------------------------------------------------------------------------

    def _worker(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            job, ticket = item
            with self._lock:
                self._running += 1
                self._peak_running = max(self._peak_running, self._running)
            try:
                # A FRESH context: nothing of the submitting turn (reach recorder, admission state,
                # obligation binding, request id) is visible here, so nothing can be written there.
                contextvars.Context().run(self._run_job, job, ticket)
            finally:
                with self._lock:
                    self._running -= 1
                self._queue.task_done()

    def _run_job(self, job: ShadowJob, ticket: ShadowTicket) -> None:
        ticket._status = "running"
        started = self._clock()
        if job.cancel.is_set() or started > job.deadline_monotonic:
            self._record(job, ticket, self._observation_for(job, failure=FAILURE_TIMEOUT if not job.cancel.is_set() else FAILURE_CANCELLED, latency_ms=0.0))
            return
        prepared = None
        graph = None
        notes: tuple[str, ...] = ()
        failure = FAILURE_NONE
        versions: dict[str, str] = {}
        try:
            prepared = job.resolver.prepare(job.canonical, operations=job.operations, turn_id=job.turn_id)
            if prepared is None:
                failure = FAILURE_NOT_PREPARED
            else:
                versions = {
                    "prompt_version": str(getattr(prepared, "prompt_version", "") or ""),
                    "schema_version": str(getattr(prepared, "schema_version", "") or ""),
                    "catalog_version": str(getattr(prepared, "catalog_version", "") or ""),
                }
                try:
                    reply = job.transport(prepared.system_prompt, prepared.user_prompt, dict(prepared.json_schema))
                except Exception:
                    reply = None
                    failure = FAILURE_BACKEND_ERROR
                    if hasattr(job.resolver, "record_transport_failure"):
                        job.resolver.record_transport_failure()
                if failure is FAILURE_NONE:
                    if job.cancel.is_set():
                        failure = FAILURE_CANCELLED
                    else:
                        from core.semantic.graph_parser import parse_graph_reply

                        parsed = parse_graph_reply(
                            reply, canonical=job.canonical, turn_id=job.turn_id, operations=job.operations,
                        )
                        notes = parsed.notes
                        if parsed.abstained or parsed.graph is None:
                            failure = FAILURE_PARSE_ERROR if parsed.reason not in {"empty_reply"} else FAILURE_ABSTAINED
                            if hasattr(job.resolver, "record_transport_failure"):
                                job.resolver.record_transport_failure()
                        else:
                            graph = parsed.graph
        except Exception:
            failure = FAILURE_BACKEND_ERROR
        finished = self._clock()
        late = finished > job.deadline_monotonic
        if late and failure is FAILURE_NONE and graph is None:
            failure = FAILURE_TIMEOUT
        with self._lock:
            closed = job.turn_id in self._closed_turns
        observation = self._observation_for(
            job, failure=failure, latency_ms=(finished - started) * 1000.0, graph=graph, notes=notes,
            late=late or closed, **versions,
        )
        self._record(job, ticket, observation)

    def _observation_for(self, job: ShadowJob, *, failure: str, latency_ms: float, graph: Any = None,
                         notes: Sequence[str] = (), late: bool = False, prompt_version: str = "",
                         schema_version: str = "", catalog_version: str = "") -> ShadowObservationV2:
        return build_observation(
            ticket_id=job.ticket_id, turn_id=job.turn_id, session_id=job.session_id,
            request_digest=str(getattr(job.canonical, "digest", "") or ""), mode=job.mode,
            resolver_name=str(getattr(job.resolver, "name", type(job.resolver).__name__)),
            provider=job.provider, model=job.model, prompt_version=prompt_version,
            schema_version=schema_version, catalog_version=catalog_version, policy_version=POLICY_VERSION,
            latency_ms=latency_ms, failure_type=failure, resolver_graph=graph,
            heuristic_graph=job.heuristic_graph, notes=notes, late=late,
        )

    def _record(self, job: ShadowJob, ticket: ShadowTicket, observation: ShadowObservationV2, *, status: str = "done") -> None:
        record_observation(observation)
        with self._lock:
            self._stats["completed"] += 1 if status == "done" else 0
            if observation.late:
                self._stats["late"] += 1
            self._failures[observation.failure_type] = self._failures.get(observation.failure_type, 0) + 1
        _emit_shadow_event(job, observation)
        ticket._finish(observation, status=status)


def _emit_shadow_event(job: ShadowJob, observation: ShadowObservationV2) -> None:
    """The observation onto the runtime ledger, under a MINIMAL context built from the job's ids --
    never the turn's context (it is not held) and never any text."""
    if not job.session_id:
        return
    try:
        from core.runtime_task_events import emit_runtime_event

        context = {"session_id": job.session_id, "runtime_session_id": job.session_id, "cancel_turn_id": job.turn_id}
        emit_runtime_event(
            context,
            event_type=SHADOW_OBSERVATION_EVENT,
            message=(
                f"Semantic shadow: {observation.failure_type}"
                + (f", whole_turn={observation.differential.get('whole_turn_correct')}" if observation.differential else "")
                + f", {observation.latency_ms:.0f} ms"
            ),
            details={"observation": observation.to_dict(), "reason": "semantic_shadow"},
        )
    except Exception:
        return


# -- the transport the runtime builds --------------------------------------------------------------

_STRIPPED_CONTEXT_KEYS = ("runtime_event_stream_id", "_turn_model_call_ledger_id", "_turn_model_call_ledger_turn")


def build_shadow_transport(agent: Any, source_context: Mapping[str, Any] | None, *, timeout_s: float = _DEFAULT_DEADLINE_S) -> ShadowTransport:
    """A bounded, single-attempt, UNPAID transport, modelled on the planner's auxiliary call.

    Differences that make it a shadow: no shared artifact cache (the job owns its identity), the
    turn's model-call ledger keys and event stream id are stripped from the call context (a shadow
    call is never the turn's call), and the provider deadline is bound to ``timeout_s``.
    """
    base = {k: v for k, v in dict(source_context or {}).items() if k not in _STRIPPED_CONTEXT_KEYS}

    def _transport(system_prompt: str, user_prompt: str, json_schema: dict[str, Any] | None) -> Any:
        from adapters.base_adapter import ModelRequest
        from core.agent_runtime.audit_routing import resolve_routing_mode, select_audit_manifests
        from core.agent_runtime.turn_planner_hook import _prioritize_planner_residency, _unpaid_manifests
        from core.provider_call_deadline import bind_provider_deadline

        routing = resolve_routing_mode(base)
        manifests, _reason = select_audit_manifests(agent, base, routing)
        candidates = _unpaid_manifests(manifests)
        if not bool(getattr(routing, "pinned", False)):
            candidates = _prioritize_planner_residency(candidates)
        if not candidates:
            return None
        request = ModelRequest(
            task_kind="normalization_assist",
            prompt=user_prompt,
            system_prompt=system_prompt,
            temperature=0.0,
            max_output_tokens=1600,
            output_mode="json_object",
            reasoning_mode="disabled",
            contract={"json_schema": json_schema} if json_schema else None,
            metadata={"semantic_shadow": True, "auxiliary_call_cap": 1},
            allow_response_control_retry=False,
            allow_provider_retry=False,
        )
        call_context = bind_provider_deadline(
            base, turn_deadline_monotonic=runtime_active_clock.monotonic() + float(timeout_s), cleanup_margin_seconds=0.0,
            reason="semantic shadow budget",
        )
        _adapter, response, error = agent.memory_router._invoke_manifest(
            manifest=candidates[0], request=request, output_mode="json_object", task=None, source_context=call_context,
        )
        if error or response is None:
            return None
        return str(getattr(response, "output_text", "") or "")

    return _transport


# -- transport selection ----------------------------------------------------------------------------

_TRANSPORT_FACTORY: Callable[[Any, Mapping[str, Any] | None], ShadowTransport] | None = None
_TRANSPORT_LOCK = threading.Lock()


def set_shadow_transport_factory(factory: Callable[[Any, Mapping[str, Any] | None], ShadowTransport] | None) -> None:
    """Install (or clear) the transport factory the door uses. Tests install a deterministic stub;
    production leaves it unset and gets ``build_shadow_transport``. A swappable seam, no authority."""
    global _TRANSPORT_FACTORY
    with _TRANSPORT_LOCK:
        _TRANSPORT_FACTORY = factory


def shadow_transport_for(agent: Any, source_context: Mapping[str, Any] | None) -> ShadowTransport:
    with _TRANSPORT_LOCK:
        factory = _TRANSPORT_FACTORY
    if factory is not None:
        return factory(agent, source_context)
    return build_shadow_transport(agent, source_context)


def submit_turn_shadow(
    agent: Any,
    *,
    source_context: Mapping[str, Any] | None,
    turn_id: str,
    session_id: str,
    text: str,
    heuristic_graph: Any,
) -> dict[str, Any] | None:
    """The door's one call: when the authority mode is SHADOW and a graph resolver is registered,
    queue a shadow run and return the text-free ticket record for the receipt. OFF, no resolver,
    or any fault -> None. Never raises, never waits, dispatches nothing."""
    try:
        from core.semantic.authority_mode import SemanticAuthorityMode, resolve_semantic_authority_mode
        from core.semantic.canonical_text import CanonicalText
        from core.semantic.resolver_registry import active_resolver, has_graph_resolver

        mode = resolve_semantic_authority_mode(backend_available=has_graph_resolver())
        if mode is not SemanticAuthorityMode.SHADOW:
            return None
        resolver = active_resolver()
        if resolver is None or not hasattr(resolver, "prepare"):
            return None
        operations, _descriptions = _catalog_operations()
        if not operations:
            return None
        ticket = default_shadow_runtime().submit(
            turn_id=str(turn_id), session_id=str(session_id or ""), canonical=CanonicalText.of(text),
            operations=operations, heuristic_graph=heuristic_graph, resolver=resolver,
            transport=shadow_transport_for(agent, source_context), mode=mode.value,
        )
        return {
            "ticket_id": ticket.ticket_id,
            "resolver": str(getattr(resolver, "name", type(resolver).__name__)),
            "mode": mode.value,
            "detail": "shadow run queued; the observation lands on the ledger when it completes",
        }
    except Exception:
        return None


def _catalog_operations() -> tuple[tuple[str, ...], dict[str, str]]:
    try:
        from core.semantic.operation_catalog import catalog_entries

        rows = catalog_entries(include_unsupported=False)
        names = tuple(str(r["name"]) for r in rows if r.get("name"))
        return names, {str(r["name"]): str(r.get("description") or "") for r in rows if r.get("name")}
    except Exception:
        return (), {}


def ensure_shadow_resolver_registered() -> bool:
    """When the operator asked for SHADOW and nothing is registered, install the model resolver
    (the two-phase, transport-free one). Idempotent; OFF installs nothing."""
    try:
        from core.semantic.authority_mode import SemanticAuthorityMode, resolve_semantic_authority_mode
        from core.semantic.resolver_registry import has_graph_resolver, register_resolver

        if has_graph_resolver():
            return True
        if resolve_semantic_authority_mode(backend_available=True) is not SemanticAuthorityMode.SHADOW:
            return False
        from core.semantic.resolver import ModelSemanticResolver

        _ops, descriptions = _catalog_operations()
        register_resolver(ModelSemanticResolver(catalog_descriptions=descriptions))
        return True
    except Exception:
        return False


# -- process default -------------------------------------------------------------------------------

_DEFAULT: ShadowRuntime | None = None
_DEFAULT_LOCK = threading.Lock()


def default_shadow_runtime() -> ShadowRuntime:
    global _DEFAULT
    with _DEFAULT_LOCK:
        if _DEFAULT is None:
            _DEFAULT = ShadowRuntime(
                max_workers=int(os.environ.get(_WORKERS_ENV) or _DEFAULT_WORKERS),
                max_queue=int(os.environ.get(_QUEUE_ENV) or _DEFAULT_QUEUE),
                deadline_s=float(os.environ.get(_DEADLINE_ENV) or _DEFAULT_DEADLINE_S),
            )
        return _DEFAULT


def reset_default_shadow_runtime_for_tests() -> None:
    global _DEFAULT
    with _DEFAULT_LOCK:
        if _DEFAULT is not None:
            _DEFAULT.shutdown(wait=False)
        _DEFAULT = None


__all__ = [
    "POLICY_VERSION",
    "ShadowJob",
    "ShadowRuntime",
    "ShadowTicket",
    "ShadowTransport",
    "build_shadow_transport",
    "default_shadow_runtime",
    "ensure_shadow_resolver_registered",
    "reset_default_shadow_runtime_for_tests",
    "set_shadow_transport_factory",
    "shadow_transport_for",
    "submit_turn_shadow",
]
