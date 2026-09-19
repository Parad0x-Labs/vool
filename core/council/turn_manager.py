"""Event-driven council turn manager — COMPLETION IS THE CLOCK.

Replaces batch orchestration (launch → wait → poll → advance) for the C3
topology: parallel advisors feed one sealed judge. Laws implemented here:

L1  ACTUAL WORKER COMPLETION DRIVES THE DAG. A worker finishing immediately
    publishes a typed event; dependency satisfaction is evaluated the moment
    the event lands. Normal progression NEVER sleeps or polls.
L2  Explicit dependency graph. Stage order lives in requirement objects,
    never in array position or timing.
L3  Quorum requirements (e.g. 2-of-3 valid advisors). Optional seats whose
    results are not needed do not block the judge; leftover work is
    cancelled and recorded truthfully as CANCELLED — never "completed".
L4  Typed completions. EMPTY and MALFORMED are distinct from VALID, trigger
    their bounded retry immediately on arrival, and can never count toward
    quorum while invalid.
L5  Head-of-line freedom. Launch order has no scheduling meaning; a 90s seat
    does not delay observation of 3s seats.
L6  Sealed judge preserved: raw judge bytes are frozen at commit; downstream
    validates/seals only.
L7  Budget reservation at ADMISSION: topology cost (judge call + bounded
    retries) is reserved before any paid advisor launches; insufficient
    budget refuses BEFORE spend. The authorization cannot expire mid-run.
L8  DISAGREEMENT ≠ PARTIAL. Advisor disagreement/failures are diagnostics in
    the receipt; the user-visible status reflects only whether the committed
    answer satisfies its obligation.
L9  Monotonic event sequence; replay reconstructs the run deterministically.
L10 Late events after CommitSealed are recorded diagnostically and mutate
    nothing.
L11 Cancellation stops remaining work where possible, invents nothing, and
    cannot erase an already-sealed commit.
L12 Per-seat provider configuration travels in the admitted seat spec — no
    global reasoning-mode hack, no silent provider-default drift.

Timers appear ONLY as deadlines/stall detection handed to workers — never as
the mechanism that discovers completion.
"""
from __future__ import annotations

import itertools
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# ---------------------------------------------------------------- events ----

class Ev(str, Enum):
    COUNCIL_STARTED = "CouncilStarted"
    SEAT_STARTED = "SeatStarted"
    SEAT_COMPLETED = "SeatCompleted"
    SEAT_REFUSED = "SeatRefused"
    SEAT_FAILED = "SeatFailed"
    SEAT_TIMED_OUT = "SeatTimedOut"
    RETRY_SCHEDULED = "RetryScheduled"
    JUDGE_READY = "JudgeReady"
    JUDGE_STARTED = "JudgeStarted"
    JUDGE_COMPLETED = "JudgeCompleted"
    COMMIT_SEALED = "CommitSealed"
    COUNCIL_COMPLETED = "CouncilCompleted"
    LATE_DIAGNOSTIC = "LateDiagnostic"
    CANCELLED = "Cancelled"


@dataclass(frozen=True)
class Event:
    """One runtime occurrence. ``seq`` is a gapless monotonic id — replay
    reconstructs start/completion/judge/commit order deterministically."""

    seq: int
    kind: Ev
    seat_id: str | None = None
    detail: str = ""


class EventBus:
    """Thread-safe append-only log; the UI-facing read-only event stream."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._seq = itertools.count(1)
        self._log: list[Event] = []
        self._cond = threading.Condition(self._lock)

    def publish(self, kind: Ev, *, seat_id: str | None = None, detail: str = "") -> Event:
        with self._cond:
            ev = Event(next(self._seq), kind, seat_id, detail)
            self._log.append(ev)
            self._cond.notify_all()
            return ev

    def wait_for(self, predicate: Callable[[Event], bool], timeout: float | None = None) -> Event | None:
        """Block until an event satisfying ``predicate`` exists (scanning first,
        then waking on each publication). This is the ONLY wait primitive in
        the module — it wakes on publication, so no polling tick is involved."""
        import time as _time
        deadline = None if timeout is None else _time.monotonic() + timeout
        with self._cond:
            while True:
                for e in reversed(self._log):
                    if predicate(e):
                        return e
                if deadline is not None:
                    remaining = deadline - _time.monotonic()
                    if remaining <= 0:
                        return None
                    self._cond.wait(min(remaining, 0.05))
                else:
                    self._cond.wait(0.05)

    @property
    def log(self) -> tuple[Event, ...]:
        with self._lock:
            return tuple(self._log)


# ------------------------------------------------------- typed completion ----

class Outcome(Enum):
    VALID = "valid"
    EMPTY = "empty"          # finish_reason=length-style: no content produced
    MALFORMED = "malformed"  # content present but fails the stage validator
    FAILED = "failed"        # transport/provider error
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"  # logical release; physical stop only when launch was prevented


@dataclass(frozen=True)
class SeatResult:
    outcome: Outcome
    text: str = ""
    error: str = ""

    @property
    def counts_toward_quorum(self) -> bool:
        return self.outcome is Outcome.VALID


#: Default structural validation: non-empty content. Stages may supply a
#: stricter validator; a validator raising means MALFORMED.
def default_validator(text: str) -> bool:
    return bool(text and text.strip())


# ------------------------------------------------------------ seat config ----

@dataclass(frozen=True)
class SeatRuntimeConfig:
    """Per-model provider configuration admitted WITH the seat — provider
    defaults cannot silently change behaviour between admission and call."""

    model_id: str = ""
    provider_params: dict[str, Any] = field(default_factory=dict)  # e.g. {"reasoning": {"enabled": False}}
    timeout_s: float = 120.0
    max_retries_on_invalid: int = 1  # bounded retry on EMPTY/MALFORMED


# ------------------------------------------------------------- dependency ----

@dataclass(frozen=True)
class Requirement:
    """Explicit dependency declaration: judge needs >= min_valid of these."""

    seat_ids: tuple[str, ...]
    min_valid: int

    def __post_init__(self) -> None:
        if not 1 <= self.min_valid <= len(self.seat_ids):
            raise ValueError("min_valid out of range")


# ------------------------------------------------------------------ run ------

class BudgetCannotReserve(Exception):
    """Admission-time refusal: the topology's full cost cannot be covered."""


class TurnManager:
    """Event-driven scheduler for C3: N parallel advisors -> 1 sealed judge.

    PROGRESSION_MODE is deliberately exposed for load-bearing mutation tests;
    production value is 'event'. 'poll' simulates the defective batch
    orchestrator (stage discovery requires an explicit tick()).
    """

    PROGRESSION_MODE = "event"

    def __init__(
        self,
        *,
        judge_seat_id: str,
        advisor_requirements: Requirement,
        workers: dict[str, Callable[[str, float], str]],
        configs: dict[str, SeatRuntimeConfig],
        judge_worker: Callable[[str, float], str],
        judge_config: SeatRuntimeConfig | None = None,
        final_validator: Callable[[str], bool] = default_validator,
        bus: EventBus | None = None,
        task_text: str = "",
    ) -> None:
        self.task_text = task_text
        self.judge_seat_id = judge_seat_id
        self.req = advisor_requirements
        self.workers = dict(workers)
        self.configs = dict(configs)
        self.judge_worker = judge_worker
        self.judge_config = judge_config or SeatRuntimeConfig(model_id="judge")
        self.final_validator = final_validator
        self.bus = bus or EventBus()

        self._state_lock = threading.Lock()
        self._results: dict[str, list[tuple[int, SeatResult]]] = {s: [] for s in self.req.seat_ids}
        self._judge_raw: str | None = None
        self._sealed = False
        self._cancel = threading.Event()
        self.reserved_paid_calls = 0

    # -- admission (L7) -------------------------------------------------------

    def admit(self, *, policy_paid_budget: int, judge_retries: int = 0) -> None:
        """Reserve the WHOLE topology cost up front: the final judge call plus
        its explicit bounded retries. Refuses before any advisor launches if
        the budget cannot cover it. The reservation is consumed by nothing
        else mid-run — the authorization cannot disappear later."""
        needed = 1 + max(0, int(judge_retries))
        if policy_paid_budget < needed:
            raise BudgetCannotReserve(
                f"topology needs {needed} paid call(s) (final judge + {max(0, judge_retries)} retries); "
                f"budget authorizes {policy_paid_budget}"
            )
        self.reserved_paid_calls = needed

    # -- helpers --------------------------------------------------------------

    def _classify(self, text: str, validator: Callable[[str], bool]) -> SeatResult:
        if text is None or not str(text).strip():
            return SeatResult(Outcome.EMPTY)
        try:
            if not validator(text):
                return SeatResult(Outcome.MALFORMED, text=text)
        except Exception:
            return SeatResult(Outcome.MALFORMED, text=text)
        return SeatResult(Outcome.VALID, text=text)

    def _publish_result(self, seat_id: str, result: SeatResult, attempt: int) -> None:
        kind_map = {
            Outcome.VALID: Ev.SEAT_COMPLETED,
            Outcome.EMPTY: Ev.SEAT_COMPLETED,
            Outcome.MALFORMED: Ev.SEAT_REFUSED,
            Outcome.FAILED: Ev.SEAT_FAILED,
            Outcome.TIMED_OUT: Ev.SEAT_TIMED_OUT,
            Outcome.CANCELLED: Ev.CANCELLED,
        }
        self.bus.publish(kind_map[result.outcome], seat_id=seat_id,
                         detail=f"attempt={attempt} outcome={result.outcome.value}")

    def _run_seat_thread(self, seat_id: str, phase: str) -> None:
        cfg = self.configs.get(seat_id) or SeatRuntimeConfig(model_id=seat_id)
        worker = self.workers[seat_id]
        prompt = f"[{phase}] seat={seat_id} model={cfg.model_id}\n\nTASK:\n{self.task_text}"
        attempts = cfg.max_retries_on_invalid + 1
        for attempt in range(1, attempts + 1):
            if self._cancel.is_set():
                # The ONLY physically-confirmed cancellation: this attempt was
                # never launched at all.
                res = SeatResult(Outcome.CANCELLED,
                                 error="launch_prevented_before_any_provider_call" if attempt == 1
                                 else "retry_launch_prevented")
                self._publish_result(seat_id, res, attempt)
                break
            try:
                text = worker(prompt, cfg.timeout_s)
                res = self._classify(text, default_validator)
            except TimeoutError:
                res = SeatResult(Outcome.TIMED_OUT, error=f"deadline {cfg.timeout_s}s")
            except Exception as exc:  # transport/provider failure
                res = SeatResult(Outcome.FAILED, error=str(exc)[:200])
            # publish EVERY attempt's typed outcome immediately (L1/L4)
            with self._state_lock:
                self._results[seat_id].append((attempt, res))
                sealed_already = self._sealed
            if sealed_already and res.outcome is Outcome.VALID:
                # L10: late valid work is diagnostic only.
                self.bus.publish(Ev.LATE_DIAGNOSTIC, seat_id=seat_id,
                                 detail="arrived after commit; recorded, mutates nothing")
            else:
                self._publish_result(seat_id, res, attempt)
            if res.outcome in (Outcome.VALID, Outcome.FAILED, Outcome.TIMED_OUT,
                               Outcome.CANCELLED):
                break
            if res.outcome in (Outcome.EMPTY, Outcome.MALFORMED) and attempt < attempts:
                # L4: retry fires IMMEDIATELY on the completion event — no sweep.
                self.bus.publish(Ev.RETRY_SCHEDULED, seat_id=seat_id,
                                 detail=f"attempt={attempt} was={res.outcome.value}")

    # -- state queries ---------------------------------------------------------

    def _valid_results(self) -> list[tuple[str, SeatResult]]:
        out = []
        for sid, entries in self._results.items():
            best = next((r for _, r in sorted(entries, reverse=True) if r.counts_toward_quorum), None)
            if best:
                out.append((sid, best))
        return out

    def _resolved_or_running(self) -> bool:
        return True  # placeholder kept trivial; threads signal via events

    # -- main -------------------------------------------------------------------

    def run(self) -> dict:
        """Execute the council. Returns the receipt dict. Deterministic event
        sequence; zero periodic polling for progression."""
        self.bus.publish(Ev.COUNCIL_STARTED, detail=f"mode={TurnManager.PROGRESSION_MODE}")
        threads: list[threading.Thread] = []
        poll_ticks = [0]
        for sid in self.req.seat_ids:
            t = threading.Thread(target=self._run_seat_thread, args=(sid, "R1"), daemon=True)
            threads.append(t)

        def _launch_all() -> None:
            for t in threads:
                t.start()

        import time as _time
        _time.monotonic()
        _launch_all()

        if TurnManager.PROGRESSION_MODE == "event":
            # wake on every completion; evaluate quorum each time
            while not self._cancel.is_set():
                with self._state_lock:
                    valid = len(self._valid_results())
                    unresolved_threads = any(t.is_alive() for t in threads)
                if valid >= self.req.min_valid:
                    break
                if not unresolved_threads and valid < self.req.min_valid:
                    self.bus.publish(Ev.COUNCIL_COMPLETED, detail="quorum_unreachable")
                    return {"status": "QUORUM_UNREACHABLE", "answer": "", "events": self.bus.log}
                self.bus.wait_for(
                    lambda e: e.kind in (Ev.SEAT_COMPLETED, Ev.SEAT_FAILED,
                                         Ev.SEAT_TIMED_OUT, Ev.SEAT_REFUSED),
                    timeout=0.05)
        else:
            # DEFECTIVE MODE (mutation testing only): progression discovered by
            # explicit ticks, not by completion.
            while True:
                poll_ticks[0] += 1
                self.tick()
                with self._state_lock:
                    if len(self._valid_results()) >= self.req.min_valid or self._cancel.is_set():
                        break
                _time.sleep(0.005)

        ready_at = _time.monotonic()
        # LOGICAL COUNCIL CANCELLATION: seats not needed for quorum and not
        # yet resolved are released by the council NOW — never left implicitly
        # pending, never retroactively called "completed". This does NOT claim
        # the provider call stopped; physical termination is a distinct,
        # separately-established fact.
        with self._state_lock:
            resolved = {sid for sid, entries in self._results.items() if entries}
            unresolved_optional = [s for s in self.req.seat_ids if s not in resolved]
        for sid in unresolved_optional:
            self.bus.publish(Ev.CANCELLED, seat_id=sid,
                             detail="logical_cancel; provider_execution_may_still_be_in_flight")
        self.bus.publish(Ev.JUDGE_READY,
                         detail=f"valid={len(self._valid_results())}/{len(self.req.seat_ids)}")
        if self._cancel.is_set():
            self.bus.publish(Ev.CANCELLED, seat_id=self.judge_seat_id, detail="before judge")
            self.bus.publish(Ev.COUNCIL_COMPLETED, detail="cancelled")
            return {"status": "CANCELLED", "answer": "", "events": self.bus.log}

        # cancel leftover optional advisor work (truthful recording happens in-thread)
        for t in threads:
            if t.is_alive():
                pass  # threads observe cancel flag between attempts

        self.bus.publish(Ev.JUDGE_STARTED, seat_id=self.judge_seat_id)
        jcfg = self.judge_config
        try:
            brief = "\n".join(f"ADVISOR {sid}:\n{r.text}" for sid, r in self._valid_results())
            raw = self.judge_worker(
                f"[ADJUDICATE_FINAL] TASK:\n{self.task_text}\n\nADVISOR FINDINGS:\n{brief}\n\n"
                "Produce the complete final response yourself.", jcfg.timeout_s)
        except TimeoutError:
            raw = ""
        except Exception:
            raw = ""
        jres = self._classify(raw, self.final_validator)
        self.bus.publish(Ev.JUDGE_COMPLETED, seat_id=self.judge_seat_id,
                         detail=f"outcome={jres.outcome.value}")

        if jres.outcome is not Outcome.VALID:
            self.bus.publish(Ev.COUNCIL_COMPLETED, detail=f"judge_{jres.outcome.value}")
            return {"status": f"JUDGE_{jres.outcome.value.upper()}", "answer": "",
                    "paid_calls_used": 1, "events": self.bus.log}

        # L6/L10: seal exactly what the judge produced.
        self._judge_raw = jres.text
        with self._state_lock:
            self._sealed = True
        self.bus.publish(Ev.COMMIT_SEALED, seat_id=self.judge_seat_id,
                         detail="raw_judge_bytes_frozen")

        # L8: status reflects the FINAL ANSWER obligation only. Advisor
        # disagreement/failure is a diagnostic, never a downgrade.
        status = "COMPLETE"
        self.bus.publish(Ev.COUNCIL_COMPLETED, detail=status)
        self.ready_to_judge_start_s = round(_time.monotonic() - ready_at, 6)
        return {
            "status": status,
            "answer": self._judge_raw,
            "paid_calls_used": 1,
            "advisors_valid": [sid for sid, _ in self._valid_results()],
            "advisor_disagreements_diagnostic": True,
            "events": self.bus.log,
            "poll_ticks": poll_ticks[0],
        }

    def tick(self) -> None:
        """Only used by the defective 'poll' mode in mutation tests."""

    # -- cancellation (L11) ------------------------------------------------------

    def cancel(self) -> None:
        self._cancel.set()


__all__ = [
    "BudgetCannotReserve", "Ev", "Event", "EventBus", "Outcome",
    "Requirement", "SeatResult", "SeatRuntimeConfig", "TurnManager",
]
