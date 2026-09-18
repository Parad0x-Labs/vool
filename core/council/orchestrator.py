"""The council orchestrator: rounds, the report barrier, and the tally law.

The loop the operator never has to drive by hand: dispatch every seat, wait at the
barrier until each one has a TERMINAL report (landed or typed failure — a dead seat is
reported, never faked), then wake the bench with the previous round's reports and go
again, until the run converges or states plainly that it could not.

Round phases:
  * ``investigate`` — round 1 and every rebuild round after a counterexample kills a
    candidate. Seats are BLIND to each other in round 1: each gathers its own evidence.
    The builder's ``DIAGNOSIS:`` line becomes the candidate.
  * ``adjudicate``  — every other round. Seats see the candidate plus all OTHER seats'
    previous reports (labelled by ROLE, never by model — a brand name is not an
    argument) and voting seats must close with the anti-truncation verdict line.

Tally law:
  * a receipt-backed ``COUNTEREXAMPLE:`` from ANY seat — judge or advisor — rejects the
    candidate regardless of votes. Consensus is not proof; one deterministic
    counterexample beats a room full of agreement. A counterexample WITHOUT tool
    receipts is recorded as an unverified claim and trumps nothing.
  * convergence needs every voting seat's parseable verdict (a truncated report is
    EVIDENCE-INCOMPLETE, which never counts as AGREE): unanimity with four or fewer
    voting seats, strict majority with five or more.
  * a voting seat that exhausts its attempt bound ends the run as FAILED — a missing
    vote is never voted away.

Attempt law (``core/council/attempts.py``):
  * every seat turn is classified into one typed outcome — VALID, EMPTY, MALFORMED,
    FAILED, TIMED_OUT, CANCELLED — and EVERY attempt leaves one ``seat_attempt`` ledger
    row, the first-try successes included. A retry that only appears when it fails is a
    retry nobody can count;
  * MALFORMED is a structural verdict, not a transport one: a voting seat that does not
    close an adjudicate round with the anti-truncation verdict line, or a builder whose
    investigate report carries no ``DIAGNOSIS:`` line, is RE-ASKED. Before this, one
    seat's missing verdict line burned a whole round of every other seat;
  * re-asks are bounded by a named :class:`~core.council.attempts.RetryPolicy` that
    cannot be constructed unbounded. A MALFORMED report that survives the bound still
    LANDS with its text and meets the same tally law it always did (EVIDENCE-INCOMPLETE,
    never AGREE) — pausing for the operator is a later slice, not this one.

This module is deliberately runtime-agnostic: seat dispatch, event emission, and the
workspace SHA come in as injected callables, so the whole state machine is testable
without a model, and the live wiring (core/council/dispatch.py) can be replaced without
touching the law.
"""

from __future__ import annotations

import hashlib
import threading
import time
import uuid
from dataclasses import dataclass, field, replace
from typing import Any, Callable

from core.council import containment
from core.council.attempts import AttemptOutcome, RetryPolicy
from core.council.roles import DEFAULT_JUDGE_ROLES, ROLE_REGISTRY, role_brief
from core.council.roles import (
    DIET_CANDIDATE,
    DIET_EXHIBITS,
    DIET_PEER_REPORTS,
    DIET_PROBLEM,
    DIET_WORKSPACE,
)
from core.council.run_store import CouncilRunStore


class CouncilRunError(ValueError):
    """A convene-time contract violation (bad bench, no problem, unknown role)."""


@dataclass(frozen=True)
class Seat:
    seat_id: str
    role_id: str
    model: str
    votes: bool
    #: A DISABLED seat keeps its id, its role, its vote flag and every report it already
    #: produced; it is simply no longer dispatched and no longer counted in quorum.
    #: Disabling is not demotion — a disabled judge is not an advisor, and nothing here
    #: ever rewrites another seat's `votes` to make a tally work out.
    active: bool = True

    @property
    def label(self) -> str:
        spec = ROLE_REGISTRY.get(self.role_id)
        base = spec.label if spec else self.role_id
        return base if self.votes else f"{base} (advisor)"


@dataclass
class SeatReport:
    seat_id: str
    round_no: int
    status: str  # "landed" | "failed"
    text: str = ""
    verdict: str | None = None  # "AGREE" | "DISAGREE" | None
    counterexample: str | None = None
    counterexample_backed: bool = False
    receipt_count: int = 0
    session_id: str | None = None
    failure: str | None = None
    #: The typed outcome of the LAST attempt — the one this report carries. Earlier
    #: attempts are not overwritten: each has its own `seat_attempt` ledger row.
    outcome: str = AttemptOutcome.VALID.value
    attempts: int = 1
    retries_used: int = 0
    #: What the operator asked this seat to run on, and what the runtime says actually
    #: answered. Separate fields on purpose: `model_actual` is empty unless streamed
    #: evidence established it, and is NEVER a copy of the request.
    model_requested: str = ""
    model_actual: str | None = None
    model_evidence: str = "unknown"
    #: Where the full text lives in the append-only ledger. Set once the `seat_report`
    #: event has been written and its sequence is known.
    text_ref: dict[str, Any] | None = None
    #: True once an operator retry or model replacement produced a LATER report for this
    #: seat in this round. The row stays exactly as it was written — this is the marker
    #: that says "a newer attempt exists", never a licence to edit or drop the old one.
    superseded: bool = False
    #: What the provider reported spending on this seat turn, summed over its calls by
    #: dispatch (`complete: false` names a lower bound). A failed seat turn reports none:
    #: a seat that never answered has no measured usage to show.
    usage: dict[str, Any] | None = None

    @property
    def report_sha256(self) -> str | None:
        """Over the EXACT stored bytes — the same string the ledger row carries."""
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest() if self.text else None

    def _common(self) -> dict[str, Any]:
        return {
            "seat_id": self.seat_id,
            "round_no": self.round_no,
            "status": self.status,
            "outcome": self.outcome,
            "attempts": self.attempts,
            "retries_used": self.retries_used,
            "verdict": self.verdict,
            "counterexample": self.counterexample,
            "counterexample_backed": self.counterexample_backed,
            "receipt_count": self.receipt_count,
            "session_id": self.session_id,
            "model_requested": self.model_requested,
            "model_actual": self.model_actual,
            "model_evidence": self.model_evidence,
            "failure": self.failure,
            "superseded": self.superseded,
            "usage": self.usage,
            "report_sha256": self.report_sha256,
        }

    def as_evidence_row(self) -> dict[str, Any]:
        """For the LEDGER: the record, carrying the report text itself, written once."""
        return {**self._common(), "text": self.text}

    def as_state_row(self) -> dict[str, Any]:
        """For the SNAPSHOT: the view. It carries the hash and a reference, never the body.

        The snapshot is rewritten after every seat and re-fetched by the UI every couple
        of seconds; inlining every report meant the whole transcript was re-serialized and
        re-shipped on each tick, and the same bytes were stored twice under two different
        durability promises. `run_store`'s own docstring already said the state is a VIEW
        and the ledger is the record — this is the code finally agreeing with it.
        """
        return {**self._common(), "text_ref": self.text_ref}


# Seat dispatch: (seat, prompt, round_no, run_id) -> {"text": str, "receipt_count": int,
# "session_id": str | None}. Raises on failure; the orchestrator types the failure.
SeatTurnFn = Callable[[Seat, str, int, str], dict[str, Any]]


def parse_verdict(text: str) -> str | None:
    """Anti-truncation convention shared with core/kernel/consensus.py: only the LAST
    non-empty line counts, and only when it is exactly the verdict sentence."""
    for line in reversed(str(text or "").splitlines()):
        line = line.strip()
        if not line:
            continue
        if line == "VERDICT: AGREE":
            return "AGREE"
        if line == "VERDICT: DISAGREE":
            return "DISAGREE"
        return None
    return None


def parse_counterexample(text: str) -> str | None:
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("COUNTEREXAMPLE:"):
            claim = stripped[len("COUNTEREXAMPLE:"):].strip()
            return claim[:600] if claim else None
    return None


def parse_diagnosis(text: str) -> tuple[str, str]:
    """(candidate, source) — the DIAGNOSIS line, or a flagged unstructured fallback."""
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("DIAGNOSIS:"):
            claim = stripped[len("DIAGNOSIS:"):].strip()
            if claim:
                return claim[:400], "diagnosis_line"
    fallback = " ".join(str(text or "").split())[:240]
    return fallback, "unstructured"


def structural_contract_violation(text: str, *, seat: Seat, phase: str) -> str:
    """The role's STRUCTURAL contract for this phase, or "" when the report satisfies it.

    Two contracts, both already load-bearing elsewhere in the law:

    * a VOTING seat in an ``adjudicate`` round must close with the anti-truncation
      verdict line. ``parse_verdict`` reads only the LAST non-empty line, so a report
      that carries ``VERDICT: AGREE`` and then keeps talking is a violation — exactly
      as it always was. The change is that it is now a RE-ASK instead of a whole
      wasted round;
    * the BUILDER in an ``investigate`` round must produce the ``DIAGNOSIS:`` line the
      candidate is cut from. Prose that merely reads like a diagnosis is not one: the
      unstructured fallback exists so nothing crashes, not so it can become a candidate.

    Advisors hold no vote and non-builder investigators have no candidate to mint, so
    neither carries a structural contract here; their reports land as written.
    """
    if phase == "adjudicate" and seat.votes:
        if parse_verdict(text) is None:
            return (
                "no parseable verdict on the final non-empty line "
                "(anti-truncation law: only the last line counts)"
            )
        return ""
    if phase == "investigate" and seat.role_id == "builder":
        if parse_diagnosis(text)[1] != "diagnosis_line":
            return "no 'DIAGNOSIS: ' line — the candidate cannot be cut from prose"
        return ""
    return ""


def _timed_out(exc: BaseException) -> bool:
    """A wall-clock deadline, not a broken provider. ``socket.timeout`` is ``TimeoutError``
    on every supported Python; urllib wraps its cause in ``.reason``."""
    if isinstance(exc, TimeoutError):
        return True
    return isinstance(getattr(exc, "reason", None), TimeoutError)


@dataclass
class CouncilOrchestrator:
    problem: str
    seats: list[Seat]
    seat_turn: SeatTurnFn
    exhibits: str = ""
    workspace_root: str = ""
    chat_session: str = ""
    max_rounds: int = 5
    run_id: str = field(default_factory=lambda: f"council-{uuid.uuid4().hex[:12]}")
    workspace_sha: str = ""
    on_transition: Callable[[dict[str, Any]], None] | None = None
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    #: The operator's EXPLICIT per-seat capability grant for THIS run, `{seat_id: (class, ...)}`.
    #: Empty — the default, and what every convene without an explicit grant produces — means
    #: every seat is read-only. Classes outside `containment.GRANTABLE_SIDE_EFFECT_CLASSES` are
    #: dropped here rather than honoured: nothing a run declares can widen the ceiling.
    seat_grants: dict[str, tuple[str, ...]] = field(default_factory=dict)
    #: TASK CONTRACT + GATE 0 (optional, both-or-neither). When set, `run()` evaluates
    #: the gate BEFORE the first seat is dispatched: a refused contract returns a typed
    #: `gate0_refused` outcome with ZERO model calls. The gate is not re-evaluated on a
    #: resume (`round_no > 0`) — its lease and task state are what the run runs under.
    task_contract: Any | None = None
    task_gate: Any | None = None
    _stop: threading.Event = field(default_factory=threading.Event)

    def __post_init__(self) -> None:
        problem = str(self.problem or "").strip()
        if not problem:
            raise CouncilRunError("a council needs a problem statement")
        if not self.seats:
            self.seats = default_bench("")
        unknown = [seat.role_id for seat in self.seats if seat.role_id not in ROLE_REGISTRY]
        if unknown:
            raise CouncilRunError(f"unknown council roles: {sorted(set(unknown))}")
        if not any(seat.votes for seat in self.seats):
            raise CouncilRunError("a council needs at least one voting seat — advisors alone cannot adjudicate")
        ids = [seat.seat_id for seat in self.seats]
        if len(ids) != len(set(ids)):
            raise CouncilRunError("seat ids must be unique")
        if (self.task_gate is None) != (self.task_contract is None):
            raise CouncilRunError(
                "task_gate and task_contract arrive together — a gate without a "
                "contract has nothing to evaluate, and a contract without a gate "
                "is a task nobody checks"
            )
        self.store = CouncilRunStore(self.run_id)
        # The containment fence exists before the first seat turn does. A council run that is
        # not registered here is not possible: this is the only constructor, and it registers
        # unconditionally. Seats are read-only unless `seat_grants` names a capability.
        self._arm_containment()
        # When the bench was seated. Every surface that shows "elapsed" reads THIS, so a
        # reload, a second browser and the persisted summary all measure from one instant
        # instead of from whenever each of them happened to start looking.
        self.started_at: float = time.time()
        self.rounds: list[list[SeatReport]] = []
        self.candidate: str | None = None
        self.candidate_source: str = ""
        self.outcome: dict[str, Any] = {}
        # RESUME POSITION. `run()` is re-enterable: a run paused at NEEDS_ATTENTION comes
        # back into the same loop at the same round, with the reports it already has, and
        # dispatches only the seats that still owe one. These fields are the whole of
        # "where was I" — everything else is already in `rounds`/`candidate`.
        self.phase: str = "investigate"
        self.round_no: int = 0
        #: True while the current round has been opened but not yet evaluated.
        self.round_open: bool = False
        #: The state the snapshot currently carries. Operator actions re-persist under it,
        #: so a click never changes what state the run is IN — only what it will do next.
        self.current_state: str = "convened"

    # ------------------------------------------------------------ containment
    def _arm_containment(self) -> None:
        """Register (or RE-register, on resume) this run's seats with the one fence.

        Re-entering `run()` re-arms, because `run()` is re-enterable: a run paused at
        NEEDS_ATTENTION is fenced when it pauses and has to get its authority back when the
        operator resumes it. Re-arming is a server-side call with the run's own seat list —
        nothing reachable from a model or an HTTP body can reach it.
        """
        containment.register_run(
            self.run_id,
            seat_ids=[seat.seat_id for seat in self.seats],
            grants=self.seat_grants,
            sink=self._record_seat_effect,
        )

    def _record_seat_effect(self, row: dict[str, Any]) -> None:
        """Publish one attempted-effect row into THIS run's own durable ledger.

        The fence's in-memory account dies with the daemon, and the 2026-09-02 write was
        found by a clean-tree gate hours after the run ended. A row in the run's event stream
        is readable from `/api/council/events` afterwards, by anyone, without the process that
        refused it still being alive.
        """
        self.store.append_event(
            "seat_effect",
            seat_id=row.get("seat_id"),
            intent=row.get("intent"),
            side_effect_class=row.get("side_effect_class"),
            outcome=row.get("outcome"),
            detail=row.get("detail") or None,
            target=row.get("target") or None,
        )

    def _fence_containment(self, reason: str) -> None:
        """Take every seat's authority away, effective INSIDE a turn already running.

        `self._stop` is read by `_dispatch_seat` between attempts, which is the only place
        it can be read from here — a seat already inside its turn (15-minute read timeout,
        real tool loop) never reaches that check, and on 2026-09-02 one of them used the
        window to edit the operator's checkout after the drive had moved on. The fence is
        read at the effect door instead, which is inside the turn.
        """
        containment.fence_run(self.run_id, reason=reason)

    # ---------------------------------------------------------------- control
    def request_stop(self) -> None:
        self._stop.set()
        self._fence_containment(
            "the operator stopped this council run, so its seats hold no authority"
        )

    def stop_requested(self) -> bool:
        return self._stop.is_set()

    def finish_stopped(self) -> dict[str, Any]:
        """End a PAUSED run terminally. A paused run has no thread to notice the stop flag,
        so the transition has to be made by whoever set it."""
        return self._finish("stopped", {
            "result": "stopped", "detail": "stopped by the operator while paused"})

    # ------------------------------------------------------------------ state
    def _persist(self, state_name: str, **extra: Any) -> None:
        self.current_state = state_name
        state = {
            "state": state_name,
            "problem": self.problem,
            "exhibits_present": bool(str(self.exhibits or "").strip()),
            "chat_session": self.chat_session,
            "started_at": self.started_at,
            "workspace_sha": self.workspace_sha,
            "max_rounds": self.max_rounds,
            "seats": [
                {"seat_id": s.seat_id, "role_id": s.role_id, "model": s.model,
                 "votes": s.votes, "active": s.active}
                for s in self.seats
            ],
            "candidate": self.candidate,
            "candidate_source": self.candidate_source,
            "rounds": [
                {"round_no": index + 1, "reports": [r.as_state_row() for r in reports]}
                for index, reports in enumerate(self.rounds)
            ],
            "outcome": self.outcome,
        }
        state.update(extra)
        self.store.write_state(state)
        if self.on_transition is not None:
            try:
                self.on_transition({"run_id": self.run_id, "state": state_name, **extra})
            except Exception:
                pass  # a progress listener must never kill the run

    # ------------------------------------------------------------------ seats
    def seat_by_id(self, seat_id: str) -> Seat | None:
        return next((s for s in self.seats if s.seat_id == str(seat_id or "")), None)

    def active_seats(self) -> list[Seat]:
        return [s for s in self.seats if s.active]

    def voting_seats(self) -> list[Seat]:
        """The seats a tally is computed over: active AND holding a vote."""
        return [s for s in self.seats if s.active and s.votes]

    def _replace_seat(self, seat: Seat, **changes: Any) -> Seat:
        updated = replace(seat, **changes)
        self.seats = [updated if s.seat_id == seat.seat_id else s for s in self.seats]
        return updated

    def quorum_refusal(self, *, without: str = "") -> str:
        """Why the bench could not adjudicate without ``without``, or "".

        Checked BEFORE a disable takes effect, because a disable that leaves no one who can
        vote turns a run the operator was rescuing into one that can only ever reach
        no_convergence — with the reason buried in a tally instead of stated at the click.
        """
        remaining = [s for s in self.voting_seats() if s.seat_id != str(without or "")]
        if not remaining:
            return (
                "no voting seat would be left. Advisors report but never vote, so the "
                "council could not adjudicate at all — replace the seat's model or retry "
                "it instead of disabling it."
            )
        return ""

    def current_reports(self) -> list[SeatReport]:
        return self.rounds[self.round_no - 1] if 0 < self.round_no <= len(self.rounds) else []

    @staticmethod
    def effective_reports(reports: list[SeatReport]) -> list[SeatReport]:
        """The report that COUNTS for each seat: its latest, superseded ones excluded.

        Retry and replacement append; they never edit. So a round can hold two rows for one
        seat — the dead attempt and the one that replaced it — and every law below reads
        this list rather than the raw one.
        """
        return [r for r in reports if not r.superseded]

    def blocking_failures(self) -> list[SeatReport]:
        """Reports that stop this round from being adjudicated at all.

        A voting seat with no report is a missing vote, and a missing vote is never voted
        away. An advisor's failure is recorded and does not block: it holds no vote.
        """
        blocking: list[SeatReport] = []
        for report in self.effective_reports(self.current_reports()):
            if report.status != "failed":
                continue
            seat = self.seat_by_id(report.seat_id)
            if seat is not None and seat.active and seat.votes:
                blocking.append(report)
        return blocking

    def _blocking_rows(self, blocking: list[SeatReport]) -> list[dict[str, Any]]:
        rows = []
        for report in blocking:
            seat = self.seat_by_id(report.seat_id)
            rows.append({
                "seat_id": report.seat_id,
                "role_id": seat.role_id if seat else "",
                "model": seat.model if seat else report.model_requested,
                "round_no": report.round_no,
                "attempts": report.attempts,
                "retries_used": report.retries_used,
                "outcome": report.outcome,
                "failure": report.failure or "",
            })
        return rows

    # -------------------------------------------------------- operator actions
    def _supersede_latest(self, seat_id: str) -> bool:
        """Mark this seat's current report in the open round superseded. True if one was.

        The row is not removed and not edited beyond this flag: the ledger already holds it
        verbatim, and the snapshot keeps showing it so the operator can see what the seat
        did before they intervened.
        """
        for report in reversed(self.current_reports()):
            if report.seat_id == str(seat_id) and not report.superseded:
                report.superseded = True
                return True
        return False

    def retry_seat(self, seat_id: str) -> dict[str, Any]:
        """Queue one more bounded set of attempts for a seat, on the same model."""
        seat = self.seat_by_id(seat_id)
        if seat is None:
            return {"ok": False, "error": "unknown_seat", "detail": f"no seat {seat_id!r}"}
        if not seat.active:
            return {"ok": False, "error": "seat_disabled",
                    "detail": "a disabled seat is not dispatched"}
        if not self._supersede_latest(seat.seat_id):
            return {"ok": True, "changed": False, "seat_id": seat.seat_id, "action": "retry"}
        self.store.append_event("seat_retry_requested", seat_id=seat.seat_id,
                                round_no=self.round_no, model=seat.model)
        self._persist(self.current_state)
        return {"ok": True, "changed": True, "seat_id": seat.seat_id, "action": "retry"}

    def replace_seat_model(self, seat_id: str, model: str) -> dict[str, Any]:
        """Point a seat at a different model. Its ROLE, its vote and its history are
        untouched — the seat IS the role, and the model behind it is the replaceable part."""
        seat = self.seat_by_id(seat_id)
        if seat is None:
            return {"ok": False, "error": "unknown_seat", "detail": f"no seat {seat_id!r}"}
        if not seat.active:
            return {"ok": False, "error": "seat_disabled",
                    "detail": "a disabled seat is not dispatched"}
        wanted = str(model or "").strip()
        if wanted == seat.model:
            return {"ok": True, "changed": False, "seat_id": seat.seat_id,
                    "action": "replace", "model": seat.model}
        previous = seat.model
        self._replace_seat(seat, model=wanted)
        self._supersede_latest(seat.seat_id)
        self.store.append_event("seat_model_replaced", seat_id=seat.seat_id,
                                round_no=self.round_no, model_from=previous, model_to=wanted)
        self._persist(self.current_state)
        return {"ok": True, "changed": True, "seat_id": seat.seat_id, "action": "replace",
                "model": wanted, "model_from": previous}

    def disable_seat(self, seat_id: str) -> dict[str, Any]:
        """Take a seat out of the bench. Refused when the rest could not adjudicate."""
        seat = self.seat_by_id(seat_id)
        if seat is None:
            return {"ok": False, "error": "unknown_seat", "detail": f"no seat {seat_id!r}"}
        if not seat.active:
            return {"ok": True, "changed": False, "seat_id": seat.seat_id, "action": "disable"}
        refusal = self.quorum_refusal(without=seat.seat_id)
        if refusal:
            return {"ok": False, "error": "quorum_impossible", "detail": refusal}
        self._replace_seat(seat, active=False)
        self.store.append_event("seat_disabled", seat_id=seat.seat_id, role_id=seat.role_id,
                                round_no=self.round_no, model=seat.model)
        self._persist(self.current_state)
        return {"ok": True, "changed": True, "seat_id": seat.seat_id, "action": "disable"}

    # ---------------------------------------------------------------- context
    def _peer_reports_block(self, seat: Seat, previous: list[SeatReport]) -> str:
        """Cross-visibility comes ONLY from the snapshot of the PREVIOUS landed round —
        never from the round in flight, or a later seat would see an earlier seat's
        same-round report and the barrier (and round-1 blindness) would silently die."""
        blocks: list[str] = []
        for report in previous:
            if report.seat_id == seat.seat_id:
                continue
            peer = next((s for s in self.seats if s.seat_id == report.seat_id), None)
            label = (peer.label if peer else report.seat_id).upper()
            if report.status != "landed":
                blocks.append(f"REPORT — {label}: (seat failed this round: {report.failure})")
                continue
            blocks.append(f"REPORT — {label}:\n{report.text.strip()}")
        return "\n\n".join(blocks)

    def _seat_context(self, seat: Seat, round_no: int, phase: str, previous: list[SeatReport]) -> str:
        spec = ROLE_REGISTRY[seat.role_id]
        parts: list[str] = [
            f"COUNCIL RUN {self.run_id} — ROUND {round_no} ({phase.upper()})",
            f"SEAT: {seat.label}",
            role_brief(seat.role_id, votes=seat.votes, round_no=2 if phase == "adjudicate" else 1),
        ]
        if DIET_PROBLEM in spec.diet:
            parts.append(f"PROBLEM (from the operator):\n{self.problem.strip()}")
        exhibits = str(self.exhibits or "").strip()
        if DIET_EXHIBITS in spec.diet and exhibits:
            parts.append(f"EXHIBIT A (operator-supplied evidence — start here):\n{exhibits}")
        if DIET_WORKSPACE in spec.diet and spec.investigates:
            root = self.workspace_root or "(the current workspace)"
            note = (
                f"WORKSPACE: {root}. Your tools are READ-ONLY here, and that is enforced by "
                "the runtime rather than asked of you: this seat holds no authority to write, "
                "patch, rename, delete or run a mutating command anywhere, and any attempt is "
                "refused and recorded. Investigate directly and report — the council produces "
                "candidates, the operator promotes them."
            )
            granted = containment.granted_classes(self.run_id, seat.seat_id)
            if granted:
                note += (
                    " The operator granted this seat "
                    f"{', '.join(sorted(granted))} for this run. It is exercisable through "
                    f"{', '.join(sorted(containment.SEAT_WORKSPACE_MUTATION_TOOLS))} and nothing "
                    "else, and only inside this seat's own disposable workspace — never in the "
                    "workspace above. You cannot run a shell, a test command, a formatter or any "
                    "other subprocess: that is not a capability this runtime will delegate to a "
                    "seat, so do not plan around one."
                )
            if round_no == 1 and not exhibits:
                note += (
                    " No evidence was supplied. Gather your own: you are deliberately blind to "
                    "the other seats this round, so pick the evidence YOU believe explains the problem."
                )
            parts.append(note)
        if DIET_CANDIDATE in spec.diet and self.candidate and phase == "adjudicate":
            parts.append(f"CURRENT CANDIDATE (diagnosis under adjudication):\n{self.candidate}")
        if DIET_PEER_REPORTS in spec.diet and round_no >= 2:
            peers = self._peer_reports_block(seat, previous)
            if peers:
                parts.append(f"REPORTS FROM THE PREVIOUS ROUND:\n\n{peers}")
        return "\n\n".join(parts)

    # --------------------------------------------------------------- dispatch
    def _dispatch_seat(
        self, seat: Seat, round_no: int, phase: str, previous: list[SeatReport]
    ) -> SeatReport:
        prompt = self._seat_context(seat, round_no, phase, previous)
        attempt = 0
        outcome = AttemptOutcome.FAILED
        error = "seat failed"
        result: dict[str, Any] = {}
        text = ""
        while attempt < self.retry_policy.max_attempts_per_seat:
            attempt += 1
            if self._stop.is_set():
                # An attempt the operator's stop prevented. Recorded as CANCELLED, never
                # retried: re-asking past a stop would be the runtime overruling them.
                outcome, error, text, result = (
                    AttemptOutcome.CANCELLED, "stopped_by_operator", "", {}
                )
                self._record_attempt(seat, round_no, attempt, outcome, error)
                break
            outcome, error, text, result = self._one_attempt(seat, prompt, round_no, phase)
            if self._stop.is_set():
                # A LATE RESULT. The stop landed while this turn was in flight, so the seat
                # answered a question the operator had already withdrawn. Keeping the text
                # would let a stopped run adjudicate on work it cancelled, and would show the
                # operator a report from after they pressed stop. It is dropped, and the
                # attempt is recorded as what it was.
                outcome, error, text, result = (
                    AttemptOutcome.CANCELLED, "stopped_by_operator", "", {}
                )
            self._record_attempt(seat, round_no, attempt, outcome, error)
            if not self.retry_policy.should_retry(outcome, attempt):
                break
        return self._report_from_attempt(
            seat, round_no, phase,
            outcome=outcome, error=error, text=text, result=result,
            attempts=attempt,
        )

    def _one_attempt(
        self, seat: Seat, prompt: str, round_no: int, phase: str
    ) -> tuple[AttemptOutcome, str, str, dict[str, Any]]:
        """One seat turn, classified. Never raises: every fault becomes a typed outcome."""
        try:
            result = self.seat_turn(seat, prompt, round_no, self.run_id) or {}
        except Exception as exc:  # noqa: BLE001 — every fault becomes a typed outcome
            declared = str(getattr(exc, "attempt_outcome", "") or "")
            if declared in AttemptOutcome.__members__:
                outcome = AttemptOutcome[declared]
            elif _timed_out(exc):
                outcome = AttemptOutcome.TIMED_OUT
            else:
                outcome = AttemptOutcome.FAILED
            return outcome, f"{type(exc).__name__}: {exc}", "", {}
        text = str(result.get("text") or "").strip()
        if not text:
            return AttemptOutcome.EMPTY, "seat returned an empty report", "", result
        violation = structural_contract_violation(text, seat=seat, phase=phase)
        if violation:
            return AttemptOutcome.MALFORMED, violation, text, result
        return AttemptOutcome.VALID, "", text, result

    def _record_attempt(
        self, seat: Seat, round_no: int, attempt: int, outcome: AttemptOutcome, error: str
    ) -> None:
        """EVERY attempt leaves exactly one ledger row — including the ones that worked
        first time. A retry that only shows up when it fails is a retry nobody can count."""
        self.store.append_event(
            "seat_attempt",
            seat_id=seat.seat_id,
            round_no=round_no,
            attempt=attempt,
            outcome=outcome.value,
            error=error or None,
        )

    def _report_from_attempt(
        self, seat: Seat, round_no: int, phase: str, *,
        outcome: AttemptOutcome, error: str, text: str, result: dict[str, Any], attempts: int,
    ) -> SeatReport:
        """The seat's terminal report for this round, built from its LAST attempt.

        A MALFORMED report that survived the bound still LANDS with its text: the seat
        answered, it just could not be parsed, and the downstream law already knows what
        to do with an unparseable verdict (EVIDENCE-INCOMPLETE, never AGREE). Suppressing
        the text would delete evidence to make a status field tidier.
        """
        retries_used = max(0, attempts - 1)
        # `model_requested` falls back to the SEAT's own declared model — that is a
        # request either way, so restating it is not a provenance claim. `model_actual`
        # and its evidence label come only from dispatch: absent, they stay unknown.
        provenance = {
            "model_requested": str(result.get("model_requested") or seat.model or ""),
            "model_actual": result.get("model_actual") or None,
            "model_evidence": str(result.get("model_evidence") or "unknown"),
        }
        if outcome in (AttemptOutcome.VALID, AttemptOutcome.MALFORMED) and text:
            receipt_count = int(result.get("receipt_count") or 0)
            counterexample = parse_counterexample(text)
            raw_usage = result.get("usage")
            return SeatReport(
                seat_id=seat.seat_id,
                round_no=round_no,
                status="landed",
                text=text,
                verdict=parse_verdict(text) if (seat.votes and phase == "adjudicate") else None,
                counterexample=counterexample,
                counterexample_backed=bool(counterexample) and receipt_count > 0,
                receipt_count=receipt_count,
                session_id=result.get("session_id"),
                outcome=outcome.value,
                attempts=attempts,
                retries_used=retries_used,
                usage=dict(raw_usage) if isinstance(raw_usage, dict) else None,
                **provenance,
                failure=error or None if outcome is AttemptOutcome.MALFORMED else None,
            )
        return SeatReport(
            seat.seat_id, round_no, "failed",
            failure=error or "seat failed",
            outcome=outcome.value,
            attempts=attempts,
            retries_used=retries_used,
            **provenance,
        )

    # -------------------------------------------------------------------- run
    def run(self) -> dict[str, Any]:
        """Drive the council to a terminal state — or to a pause the operator owns.

        RE-ENTERABLE. A run paused at NEEDS_ATTENTION comes back in here with the same
        object, the same run id and the same ledger, at the same round, and dispatches only
        the seats that still owe a report. Nothing is replayed and nothing is reset.
        """
        # GATE 0 — before the pin, before the convene event, before ANY seat. A task
        # contract the gate refuses returns a typed outcome having spent ZERO model
        # calls: not one seat turn is dispatched, not one reservation is taken. On a
        # resume (round_no > 0) the gate is not re-evaluated — the run already holds
        # the lease and task state the gate admitted it under.
        if self.task_gate is not None and self.round_no == 0:
            decision = self.task_gate.evaluate(self.task_contract)
            self.store.append_event("gate0_evaluated", **decision.as_row())
            if not decision.allowed:
                return self._finish("gate0_refused", {
                    "result": "gate0_refused",
                    "gate0_reason": decision.reason.value if decision.reason else None,
                    "detail": decision.detail,
                    "task_id": getattr(self.task_contract, "task_id", ""),
                }, event="gate0_refused")
        # RE-ARM. A stop that has not been cleared must not be re-armed away: the flag is
        # checked first thing in the loop below, and a stopped run returns without
        # dispatching. Anything else — a fresh run, or a resume after the operator resolved
        # a failed seat — gets its seats' authority back exactly as `seat_grants` declares.
        if not self._stop.is_set():
            self._arm_containment()
        if self.round_no == 0:
            self.store.append_event(
                "convened",
                problem=self.problem,
                exhibits_present=bool(str(self.exhibits or "").strip()),
                workspace_sha=self.workspace_sha,
                chat_session=self.chat_session,
                seats=[{"seat_id": s.seat_id, "role_id": s.role_id, "model": s.model,
                        "votes": s.votes} for s in self.seats],
            )
            self._persist("convened")
        else:
            self.store.append_event("run_resumed", round_no=self.round_no, phase=self.phase)

        while True:
            if self._stop.is_set():
                return self._finish("stopped", {
                    "result": "stopped", "detail": "stopped by the operator"})
            if not self.round_open:
                if self.round_no >= self.max_rounds:
                    return self._finish("no_convergence", {
                        "result": "no_convergence",
                        "detail": (
                            f"no convergence within the configured cap of {self.max_rounds} "
                            "rounds — the cap protects wall-clock and spend, and reaching it "
                            "is stated, never papered over"
                        ),
                        "candidate": self.candidate,
                    }, event="no_convergence", max_rounds=self.max_rounds)
                self.round_no += 1
                self.rounds = self.rounds[: self.round_no - 1] + [[]]
                self.round_open = True
                self.store.append_event("round_opened", round_no=self.round_no, phase=self.phase)
                self._persist("round_open", round_no=self.round_no, phase=self.phase)

            reports = self.rounds[self.round_no - 1]
            # Snapshot of the PREVIOUS landed round — the only cross-visibility source.
            previous: list[SeatReport] = (
                self.effective_reports(self.rounds[self.round_no - 2])
                if self.round_no >= 2 else []
            )
            # Only seats that still OWE a report this round: on a resume that is the one the
            # operator retried or re-modelled, and nobody else is asked — or charged — twice.
            for seat in self.active_seats():
                if self._stop.is_set():
                    break
                if any(r.seat_id == seat.seat_id and not r.superseded for r in reports):
                    continue
                report = self._dispatch_seat(seat, self.round_no, self.phase, previous)
                reports.append(report)
                # Record FIRST, then point at it. The ledger row's own sequence becomes the
                # reference the snapshot carries, so the pointer can never name a row that
                # was not written.
                recorded = self.store.append_event(
                    "seat_report", phase=self.phase, **report.as_evidence_row()
                )
                report.text_ref = {"kind": "ledger", "seq": recorded["seq"]}
                # Keep the polled state fresh seat-by-seat so the UI shows live progress.
                self._persist("round_open", round_no=self.round_no, phase=self.phase)
            if self._stop.is_set():
                return self._finish("stopped", {
                    "result": "stopped", "detail": "stopped by the operator"})

            # BARRIER: every active seat now has a terminal report. Reports landed.
            self.store.append_event("round_landed", round_no=self.round_no, phase=self.phase)

            blocking = self.blocking_failures()
            if blocking:
                # NOT a verdict. The bench cannot adjudicate without these votes, and
                # inventing either outcome would be a lie about work that did not happen:
                # a convergence the seat never agreed to, or a no-convergence that blames
                # the round cap for a transport fault. The run pauses HERE with everything
                # it has, and the operator decides. `round_open` stays true, so a resume
                # comes back to THIS round rather than opening a fresh one.
                rows = self._blocking_rows(blocking)
                self.store.append_event("needs_attention", round_no=self.round_no,
                                        blocking_seats=rows)
                return self._finish("needs_attention", {
                    "result": "needs_attention",
                    "detail": (
                        "a voting seat exhausted its bounded attempts and produced no "
                        "report. A missing vote is never voted away, so the council is "
                        "paused rather than resolved — retry the seat, point it at another "
                        "model, or disable it and adjudicate with the seats that remain."
                    ),
                    "blocking_seats": rows,
                    "round_no": self.round_no,
                }, persist_only=True)

            effective = self.effective_reports(reports)
            self.round_open = False

            if self.phase == "investigate":
                voting = self.voting_seats()
                builder = next((s for s in voting if s.role_id == "builder"), voting[0])
                builder_report = next(
                    (r for r in effective if r.seat_id == builder.seat_id), None)
                self.candidate, self.candidate_source = parse_diagnosis(
                    builder_report.text if builder_report else "")
                self.store.append_event(
                    "candidate_set", round_no=self.round_no, candidate=self.candidate,
                    source=self.candidate_source, from_seat=builder.seat_id,
                )
                self.phase = "adjudicate"
                self._persist("candidate_set", round_no=self.round_no)
                continue

            # ---- adjudicate round: counterexample trump first, then the vote tally.
            backed = [r for r in effective if r.counterexample_backed]
            if backed:
                self.store.append_event(
                    "candidate_rejected", round_no=self.round_no, candidate=self.candidate,
                    counterexamples=[{"seat_id": r.seat_id, "claim": r.counterexample}
                                     for r in backed],
                )
                self.candidate, self.candidate_source = None, ""
                self.phase = "investigate"  # rebuild: the bench re-investigates
                self._persist("candidate_rejected", round_no=self.round_no)
                continue

            voting_ids = {s.seat_id for s in self.voting_seats()}
            voters = [r for r in effective if r.seat_id in voting_ids]
            if any(r.verdict is None for r in voters):
                # EVIDENCE-INCOMPLETE ≠ AGREE: an unparseable/truncated verdict blocks
                # convergence; the next round re-asks with the same reports visible.
                self.store.append_event(
                    "round_inconclusive", round_no=self.round_no,
                    missing=[r.seat_id for r in voters if r.verdict is None],
                )
                self._persist("round_inconclusive", round_no=self.round_no)
                continue
            agree = sum(1 for r in voters if r.verdict == "AGREE")
            disagree = len(voters) - agree
            converged = (disagree == 0) if len(voters) <= 4 else (agree > len(voters) / 2)
            self.store.append_event(
                "tally", round_no=self.round_no, agree=agree, disagree=disagree,
                voting_seats=len(voters), converged=converged,
            )
            if converged:
                return self._finish("converged", {
                    "result": "adjudicated",
                    "candidate": self.candidate,
                    "candidate_source": self.candidate_source,
                    "agree": agree,
                    "disagree": disagree,
                    "rounds": self.round_no,
                    "authority_note": (
                        "adjudication only — promotion, merge, and spend remain with the operator"
                    ),
                }, event="converged")
            self._persist("round_disputed", round_no=self.round_no,
                          agree=agree, disagree=disagree)

    def _finish(self, state: str, outcome: dict[str, Any], *, event: str = "",
                persist_only: bool = False, **event_fields: Any) -> dict[str, Any]:
        """One exit for every ending. `needs_attention` is an ending of this CALL, not of
        the run: the state persists, the thread returns, and `run()` can be entered again.

        Finalization contract (Goal 2, 2026-09-18): for a TERMINAL ending, the run's
        terminal artifacts (scorecard, chat summary) publish BEFORE the terminal state
        becomes visible, through ``publish_terminal_artifacts`` (registered by the api).
        An observer who sees the state ``converged`` is therefore guaranteed the run's
        final chat artifact already exists -- completion and its publication are one fact.
        An artifact fault is stated in the ledger (the same events the api's own finally
        emits) and NEVER blocks the verdict itself from persisting. The crashed path
        (an exception inside run(), handled by the api) keeps its own best-effort
        ordering; pauses publish nothing, unchanged."""
        self.outcome = outcome
        # Every ending is a fence, including `needs_attention`: a paused run has no thread,
        # so no seat of it should hold authority until the operator resumes it and `run()`
        # re-arms. A terminal ending never re-arms.
        self._fence_containment(f"this council run ended: {state}")
        if state in ("converged", "no_convergence", "stopped", "failed"):
            publisher = getattr(self, "publish_terminal_artifacts", None)
            if publisher is not None:
                try:
                    publisher(state)
                except Exception as exc:  # stated, never silent; the verdict still persists
                    self.store.append_event(
                        "terminal_artifacts_failed", error=f"{type(exc).__name__}: {exc}", state=state)
        self._persist(state)
        if event:
            self.store.append_event(
                event, **{k: v for k, v in outcome.items() if k != "result"}, **event_fields)
        elif not persist_only:
            self.store.append_event(state)
        return self.outcome


def default_bench(model: str) -> list[Seat]:
    """The Convene-with-no-choices bench: three voting seats on one model."""
    return [
        Seat(seat_id=f"s{index + 1}", role_id=role_id, model=model, votes=True)
        for index, role_id in enumerate(DEFAULT_JUDGE_ROLES)
    ]


def elapsed_label(started: float) -> str:
    seconds = max(0, int(time.time() - started))
    return f"{seconds // 60}m{seconds % 60:02d}s"
