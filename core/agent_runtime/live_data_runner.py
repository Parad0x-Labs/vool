"""Execute a `LiveDataPlan`'s subtasks concurrently, isolating one failure from the rest.

Market and weather subtasks are independent read-only network lookups with no dependency on each
other or on a model, so all seven of the benchmark's subtasks run in the same wave -- unlike a
planned sub-turn (which drives a whole local-model generation and can saturate that lane), each of
these is bounded I/O, and several can be in flight at once. One city's or asset's fetch failing
must never cost the others their result, and a fetch's own text output must never be treated as
data for a DIFFERENT subtask -- every result is written into the SAME subtask's own outcome, never
merged across entities.

Concurrency is proven by real timing telemetry (`queued_at`/`started_at`/`completed_at`,
`concurrency_report`), not by wall-clock duration alone: a fast sequential run can look
indistinguishable from a concurrent one by total elapsed time if the calls are cheap, so this
records enough per-subtask interval data to prove at least two RUNNING intervals genuinely
overlapped.
"""

from __future__ import annotations

import contextlib
import time
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from core.agent_runtime.live_data_plan import LiveDataPlan, LiveDataSubtask, SubtaskLifecycle, SubtaskOutcome


def _now() -> tuple[float, str]:
    """(monotonic seconds, wall-clock ISO string) captured together, for the same instant."""
    return time.monotonic(), datetime.now(timezone.utc).isoformat()


def _run_market_subtask(subtask: LiveDataSubtask, *, timeout_s: float) -> SubtaskOutcome:
    from tools.web.web_research import _MARKET_QUOTE_TARGETS, _crypto_price_fallback_multi, _market_quote_fallback_multi

    asset_key = str(subtask.arguments.get("asset_key") or "")
    kind = str(subtask.arguments.get("kind") or "")
    try:
        if kind == "crypto":
            quotes = _crypto_price_fallback_multi([asset_key], timeout_s=timeout_s)
        else:
            target = next((item for item in _MARKET_QUOTE_TARGETS if item.asset_key == asset_key), None)
            if target is None:
                return SubtaskOutcome(
                    subtask=subtask,
                    state=SubtaskLifecycle.FAILED,
                    failure_reason=f"no commodity target registered for {asset_key!r}",
                )
            quotes = _market_quote_fallback_multi("", [target], timeout_s=timeout_s)
    except Exception as exc:
        return SubtaskOutcome(
            subtask=subtask, state=SubtaskLifecycle.FAILED, failure_reason=f"{type(exc).__name__}: {exc}"[:200]
        )
    if not quotes:
        return SubtaskOutcome(subtask=subtask, state=SubtaskLifecycle.FAILED, failure_reason="no quote returned")
    quote = quotes[0]
    return SubtaskOutcome(
        subtask=subtask,
        state=SubtaskLifecycle.SUCCEEDED,
        result={
            "price": quote.value,
            "currency": quote.currency,
            "change_24h_pct": quote.change_percent,
            # The window the change was actually measured over (CoinGecko: a
            # true 24h; Yahoo commodities: the trading session). Without it,
            # a session change and a 24h change are indistinguishable downstream
            # and a "24-hour mover" line can compare unlike windows.
            "change_window": (getattr(quote, "change_window", "") or "24h").strip(),
            "unit_label": quote.unit_label,
            "source": quote.source_label,
            "source_url": quote.source_url,
            "retrieved_at": quote.as_of,
        },
    )


def _run_weather_subtask(subtask: LiveDataSubtask, *, timeout_s: float) -> SubtaskOutcome:
    from tools.web.web_research import _is_plausible_weather_location, structured_weather_lookup

    location = str(subtask.arguments.get("location") or "")
    # Mnemosyne review repair (2026-08-07): a second, explicit plausibility check at the actual
    # execution boundary -- `structured_weather_lookup` already refuses to fetch an implausible
    # location internally, but that guard lives inside the fetch function, not at the call site a
    # reviewer or test can point at directly. This is defense in depth against a future extraction
    # regression the same way `_reconcile_to_plan` defends the render layer: a malformed candidate
    # (instruction-tail prose, a label fragment) is rejected here, before any network attempt,
    # never handed to wttr.in on the chance it might resolve to something.
    if not _is_plausible_weather_location(location):
        return SubtaskOutcome(
            subtask=subtask, state=SubtaskLifecycle.FAILED,
            failure_reason=f"rejected implausible location before fetch: {location!r}"[:200],
        )
    # Mnemosyne review repair, final narrow round (2026-08-07): round 3 added a second guard here,
    # rejecting any `extraction_confidence == "tail_fallback"` location over 3 words. Proven wrong,
    # live: "Ho Chi Minh City" (4 words) is a real, entirely legitimate `tail_fallback` candidate
    # (nothing follows it in its clause but the clause's own end) that this guard rejected purely
    # for its length -- "word count is not validation" (Mnemosyne, this round). Removed outright,
    # not narrowed further, per the review's explicit instruction not to reintroduce any rule where
    # word count alone determines legitimacy.
    #
    # `extraction_confidence` remains on the subtask as provenance (was this candidate bounded by a
    # real delimiter/qualifier, or only by the clause's own end), but the runner no longer acts on
    # it. Defense against a malformed composite now lives upstream, at extraction --
    # `_split_weather_candidates` and `_WEATHER_LIST_BOUNDARY_RE` (see their docstrings) are what
    # keep something like "Tallinn live-data request in parallel" or "Toronto"/"Ontario" as two
    # invented entities from ever being CONSTRUCTED as a candidate in the first place, rather than
    # this function trying to re-detect it after the fact by how long or how it is extraction-typed.
    # `_is_plausible_weather_location` above remains the syntactic backstop it always was.
    try:
        result = structured_weather_lookup(location, timeout_s=timeout_s)
    except Exception as exc:
        return SubtaskOutcome(
            subtask=subtask, state=SubtaskLifecycle.FAILED, failure_reason=f"{type(exc).__name__}: {exc}"[:200]
        )
    if result is None:
        return SubtaskOutcome(subtask=subtask, state=SubtaskLifecycle.FAILED, failure_reason="no observation returned")
    return SubtaskOutcome(
        subtask=subtask,
        state=SubtaskLifecycle.SUCCEEDED,
        result={
            "condition": result.condition,
            "temperature_c": result.temperature_c,
            "feels_like_c": result.feels_like_c,
            "high_c": result.high_c,
            "low_c": result.low_c,
            "place_label": result.place_label,
            "source": result.source_label,
            "source_url": result.source_url,
            "observed_at": result.observed_at,
        },
    )


def _run_water_subtask(subtask: LiveDataSubtask, *, timeout_s: float) -> SubtaskOutcome:
    """One sea/water-temperature observation. None from the fetcher is an honest
    failure (unresolved place, provider down, no reading for that point) — never
    a fallback number and never prose."""
    from core.fresh_data.water_temperature import water_temperature_lookup

    place = str(subtask.arguments.get("place") or "")
    try:
        result = water_temperature_lookup(place, timeout_s=timeout_s)
    except Exception as exc:  # noqa: BLE001 — typed failure, message truncated like weather's
        return SubtaskOutcome(
            subtask=subtask,
            state=SubtaskLifecycle.FAILED,
            failure_reason=f"{type(exc).__name__}: {exc}"[:200],
        )
    if result is None:
        return SubtaskOutcome(
            subtask=subtask,
            state=SubtaskLifecycle.FAILED,
            failure_reason="no sea-temperature observation returned for this place",
        )
    return SubtaskOutcome(
        subtask=subtask,
        state=SubtaskLifecycle.SUCCEEDED,
        result={
            "label": result.label,
            "temperature_c": result.temperature_c,
            "temperature_f": result.temperature_f,
            "observed": result.observed,
            "source": result.source,
        },
    )


def _run_one(subtask: LiveDataSubtask, *, timeout_s: float) -> SubtaskOutcome:
    if subtask.operation == "market_quote":
        return _run_market_subtask(subtask, timeout_s=timeout_s)
    if subtask.operation == "weather_lookup":
        return _run_weather_subtask(subtask, timeout_s=timeout_s)
    if subtask.operation == "water_temperature":
        return _run_water_subtask(subtask, timeout_s=timeout_s)
    return SubtaskOutcome(
        subtask=subtask, state=SubtaskLifecycle.FAILED, failure_reason=f"unknown operation {subtask.operation!r}"
    )


def _emit_agent_event(emit, event_type: str, detail: dict) -> None:
    """Report subtask lifecycle without ever letting the report affect the work.

    Swallowed on failure, exactly as the conductor's `_emit` is: a subtask marked FAILED because
    its observer threw would look identical to a real fetch failure.
    """

    if emit is None:
        return
    with contextlib.suppress(Exception):
        emit(event_type, detail)


def _run_one_timed(
    subtask: LiveDataSubtask,
    *,
    timeout_s: float,
    queued_at: float,
    queued_at_iso: str,
    emit_node_event=None,
) -> SubtaskOutcome:
    """`_run_one`, stamped with real started_at/completed_at captured around the actual fetch."""
    started_at, started_at_iso = _now()
    # Emitted from inside the worker, after the started stamp, so a panel reporting "running for
    # 12s" is not counting the time this subtask waited for a pool slot.
    _emit_agent_event(
        emit_node_event,
        "agent_node_started",
        {
            "schema": "agent_node_started_v1",
            "lane": "live_data",
            "node_id": str(getattr(subtask, "subtask_id", "") or ""),
            "operation": str(getattr(subtask, "operation", "") or ""),
            "depends_on": [],
            "started_at_iso": started_at_iso,
        },
    )
    outcome = _run_one(subtask, timeout_s=timeout_s)
    completed_at, completed_at_iso = _now()
    outcome.queued_at = queued_at
    outcome.queued_at_iso = queued_at_iso
    outcome.started_at = started_at
    outcome.started_at_iso = started_at_iso
    outcome.completed_at = completed_at
    outcome.completed_at_iso = completed_at_iso
    result = dict(outcome.result or {})
    # The observation identity (bounded values + the source the result named), through the SAME
    # projection the conductor lane uses, so a consumer that joins completions as evidence or
    # counts unique sources reads both lanes identically. Generic over results; never affects the
    # outcome.
    observed: dict[str, Any] = {}
    if outcome.ok:
        try:
            from core.conductor.evidence import observation_event_payload

            observed = observation_event_payload(result)
        except Exception:
            observed = {}
    _emit_agent_event(
        emit_node_event,
        "agent_node_completed",
        {
            **({"observed": observed} if observed else {}),
            "schema": "agent_node_completed_v1",
            "lane": "live_data",
            "node_id": str(getattr(subtask, "subtask_id", "") or ""),
            "operation": str(getattr(subtask, "operation", "") or ""),
            "state": str(getattr(outcome.state, "name", outcome.state)).lower(),
            "ok": bool(outcome.ok),
            "failure_reason": str(outcome.failure_reason or ""),
            # This lane has no per-subtask renderer -- rendering happens once over the whole plan --
            # so the field names are reported and `rendered` is deliberately left empty rather than
            # filled with a summary the runtime did not actually produce.
            "result_fields": sorted(result.keys()),
            "rendered": "",
            "rendered_truncated": False,
            "started_at_iso": started_at_iso,
            "completed_at_iso": completed_at_iso,
            "duration_s": outcome.duration_s,
        },
    )
    return outcome


def run_live_data_plan(
    plan: LiveDataPlan,
    *,
    approval_decisions: dict[str, Any] | None = None,
    timeout_s: float = 8.0,
    max_workers: int = 7,
    emit_node_event=None,
) -> list[SubtaskOutcome]:
    """Every subtask's outcome, in plan order, run concurrently within the approved set.

    A subtask whose approval decision is not `allowed` is never executed -- it is recorded as
    WAITING_APPROVAL, preserved in the returned list (never dropped), so the caller can render an
    accurate approval card for exactly that subtask while the rest proceed. `approval_decisions`
    keyed by `subtask_id`; omitted entirely (None) means every subtask runs (the caller has
    already gated access some other way, e.g. a non-interactive test).

    Every runnable subtask is stamped with the SAME `queued_at` (the instant they are all handed
    to the pool together), and its own real `started_at`/`completed_at` captured inside the worker
    thread around the actual fetch -- this is what lets `concurrency_report` prove genuine overlap
    rather than infer it from total wall-clock time.
    """
    decisions = approval_decisions or {}
    runnable: list[LiveDataSubtask] = []
    outcomes: dict[str, SubtaskOutcome] = {}
    for subtask in plan.subtasks:
        if subtask.operation == "unsupported_market_entity":
            # Nothing to fetch -- this was already known at plan time, not discovered by a failed
            # network call. Never enters the pool; resolved immediately, same as WAITING_APPROVAL.
            outcomes[subtask.subtask_id] = SubtaskOutcome(
                subtask=subtask,
                state=SubtaskLifecycle.UNSUPPORTED_ENTITY,
                # A ticker the plan could not resolve carries its identifying question in
                # `arguments["reason"]` (FINDINGS F15); anything else keeps the fixed phrase.
                failure_reason=str(dict(subtask.arguments or {}).get("reason") or "").strip()
                or "not a recognized market entity",
            )
            continue
        decision = decisions.get(subtask.subtask_id)
        if decision is not None and not getattr(decision, "allowed", True):
            outcomes[subtask.subtask_id] = SubtaskOutcome(
                subtask=subtask,
                state=SubtaskLifecycle.WAITING_APPROVAL,
                failure_reason=str(getattr(decision, "reason", "") or "requires approval"),
                approval_decision=str(getattr(decision, "effect", "") or "require_approval"),
            )
            continue
        runnable.append(subtask)

    if runnable:
        queued_at, queued_at_iso = _now()
        try:
            with ThreadPoolExecutor(max_workers=min(max_workers, len(runnable))) as pool:
                # A pool thread starts with an EMPTY context, so without this the turn's
                # remote-fetch ledger, its forbidden flag and its scope marker are all invisible
                # to the code that actually fetches -- the turn would report zero remote calls
                # while its subtasks hit the network. Each task gets its own copy because a
                # Context cannot be entered twice concurrently; the copies share the ledger
                # OBJECT, which is what carries the tally back to the owning turn.
                futures = {
                    pool.submit(
                        copy_context().run,
                        _run_one_timed,
                        task,
                        timeout_s=timeout_s,
                        queued_at=queued_at,
                        queued_at_iso=queued_at_iso,
                        emit_node_event=emit_node_event,
                    ): task
                    for task in runnable
                }
                for future, task in futures.items():
                    outcomes[task.subtask_id] = future.result()
        except Exception:
            for subtask in runnable:
                if subtask.subtask_id not in outcomes:
                    outcomes[subtask.subtask_id] = _run_one_timed(
                        subtask, timeout_s=timeout_s, queued_at=queued_at,
                        queued_at_iso=queued_at_iso, emit_node_event=emit_node_event,
                    )

    return [outcomes[subtask.subtask_id] for subtask in plan.subtasks]


@dataclass(frozen=True)
class ConcurrencyReport:
    max_concurrent: int
    overlapping_pairs: tuple[tuple[str, str], ...]
    timed_subtask_count: int

    @property
    def proves_concurrency(self) -> bool:
        """True only when at least two subtasks genuinely overlapped in time, not merely that
        several tasks existed -- a sequential run with 7 subtasks still has `timed_subtask_count
        == 7` but `max_concurrent == 1` and no overlapping pairs."""
        return self.max_concurrent >= 2 and bool(self.overlapping_pairs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_concurrent": self.max_concurrent,
            "overlapping_pairs": list(self.overlapping_pairs),
            "timed_subtask_count": self.timed_subtask_count,
            "proves_concurrency": self.proves_concurrency,
        }


def concurrency_report(outcomes: list[SubtaskOutcome]) -> ConcurrencyReport:
    """The real overlap evidence for a run: max simultaneous RUNNING count + every overlapping pair.

    A sweep-line over each subtask's [started_at, completed_at) interval -- not a duration
    comparison, not an inference from total elapsed time. Subtasks with no timing (never run,
    e.g. WAITING_APPROVAL) are excluded, not treated as zero-duration.
    """
    timed = [o for o in outcomes if o.started_at is not None and o.completed_at is not None]
    events: list[tuple[float, int, str]] = []
    for outcome in timed:
        events.append((outcome.started_at, 1, outcome.subtask.subtask_id))
        events.append((outcome.completed_at, -1, outcome.subtask.subtask_id))
    events.sort(key=lambda item: (item[0], -item[1]))  # a start at the same instant as an end counts as overlap

    running = 0
    max_concurrent = 0
    for _time, delta, _subtask_id in events:
        running += delta
        max_concurrent = max(max_concurrent, running)

    pairs: list[tuple[str, str]] = []
    for i in range(len(timed)):
        for j in range(i + 1, len(timed)):
            if timed[i].overlaps(timed[j]):
                pairs.append((timed[i].subtask.subtask_id, timed[j].subtask.subtask_id))

    return ConcurrencyReport(
        max_concurrent=max_concurrent,
        overlapping_pairs=tuple(pairs),
        timed_subtask_count=len(timed),
    )


def run_live_data_plan_sequential(
    plan: LiveDataPlan,
    *,
    approval_decisions: dict[str, Any] | None = None,
    timeout_s: float = 8.0,
    emit_node_event=None,
) -> list[SubtaskOutcome]:
    """Same contract as `run_live_data_plan`, but one subtask at a time -- exists ONLY as the
    sabotage target for the concurrency proof (see tests/test_live_data_concurrency.py). Never
    called from production code."""
    decisions = approval_decisions or {}
    outcomes: dict[str, SubtaskOutcome] = {}
    for subtask in plan.subtasks:
        decision = decisions.get(subtask.subtask_id)
        if decision is not None and not getattr(decision, "allowed", True):
            outcomes[subtask.subtask_id] = SubtaskOutcome(
                subtask=subtask,
                state=SubtaskLifecycle.WAITING_APPROVAL,
                failure_reason=str(getattr(decision, "reason", "") or "requires approval"),
                approval_decision=str(getattr(decision, "effect", "") or "require_approval"),
            )
            continue
        queued_at, queued_at_iso = _now()
        outcomes[subtask.subtask_id] = _run_one_timed(
            subtask, timeout_s=timeout_s, queued_at=queued_at,
            queued_at_iso=queued_at_iso, emit_node_event=emit_node_event,
        )
    return [outcomes[subtask.subtask_id] for subtask in plan.subtasks]
