"""Council API surface: convene/status/stop/list, owner-local, one thread per run.

The run registry here is process-local liveness only — the durable truth is the run
store (state JSON + evidence ledger), which survives a daemon restart. A run whose
process died mid-round is therefore visible exactly as far as it actually got, and the
ledger states it; nothing resumes silently.

Pin custody: the composer pin is global runtime state and v1 seat dispatch swaps it
per cloud seat. The thread captures the operator's pin before round 1 and restores it
in a ``finally`` — a crashed run still hands the composer back.
"""

from __future__ import annotations

import hashlib
import subprocess
import threading
import uuid
from typing import Any

from core.council import containment, pin_lock
from core.council.chat_record import persist_council_summary
from core.council.dispatch import live_seat_turn_factory, read_current_pin, restore_pin
from core.council.orchestrator import CouncilOrchestrator, CouncilRunError, Seat, default_bench
from core.council.roles import ROLE_REGISTRY
from core.council.run_store import CouncilRunStore, list_runs
from core.council.scorecard import persist_scorecard

_LOCK = threading.Lock()
_RUNS: dict[str, CouncilOrchestrator] = {}
#: Runs paused at NEEDS_ATTENTION, waiting on the operator. They hold no model pin and no
#: thread — the machine is handed back while a human decides — but they keep their bench,
#: their rounds and their chat card, and `resume` re-enters the SAME object.
#:
#: In THIS process only, deliberately, exactly as pin ownership is. A state file that says
#: needs_attention after a daemon restart describes a run whose thread died with the last
#: one; the actions answer `council_run_not_resumable` rather than pretending otherwise.
_PAUSED: dict[str, CouncilOrchestrator] = {}

#: The one state an operator can still act on.
_PAUSED_STATE = "needs_attention"

_ALLOWED_CONVENE_FIELDS = {
    "problem", "exhibits", "seats", "chat_session", "max_rounds", "seat_capabilities",
}

#: HARD STOP (operator rule, 2026-08-28): council seats run on CLOUD models only. A single
#: "auto"/local seat cold-loads a multi-GB local model per round and has OOM-frozen the host —
#: three seats would be three of them. Enforced HERE, server-side, so no client and no future
#: operator can convene a local-model council by accident. The only override is a deliberate
#: environment act, never a request field.
_ALLOW_LOCAL_SEATS_ENV = "VOOL_COUNCIL_ALLOW_LOCAL_SEATS"


def _local_seats_allowed() -> bool:
    import os

    return os.environ.get(_ALLOW_LOCAL_SEATS_ENV, "").strip() == "1"


def _seat_model_violation(model: str) -> str | None:
    """The cloud-only wall: None when the seat model passes, else the typed refusal."""
    cleaned = str(model or "").strip()
    if not cleaned or cleaned.lower() in {"auto", "vool", "local"}:
        return (
            "local_seat_refused: council seats run on cloud models only on this machine — "
            "an auto/local seat cold-loads a multi-GB model per round and can freeze the host. "
            "Name a concrete cloud model (provider/model or a :free id) for every seat."
        )
    if "/" not in cleaned:
        return (
            f"local_seat_refused: '{cleaned}' is a local model id — council seats run on cloud "
            "models only on this machine. Name a concrete cloud model (provider/model)."
        )
    return None


def _workspace_sha(workspace_root: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=workspace_root or None,
            capture_output=True, text=True, timeout=10,
        )
        return completed.stdout.strip()[:40] if completed.returncode == 0 else ""
    except Exception:
        return ""


def _parse_seats(raw: Any) -> list[Seat] | str:
    """Seats from the request body, or an error string. Empty → the default bench
    (which still passes the cloud-only wall below, so a bare convene is refused with
    the typed reason instead of silently seating local models)."""
    if raw in (None, []):
        seats = default_bench("")
    elif not isinstance(raw, list):
        return "seats must be a list"
    else:
        seats = []
        for index, entry in enumerate(raw):
            if not isinstance(entry, dict):
                return f"seat {index} must be an object"
            unknown = set(entry.keys()) - {"role_id", "model", "votes"}
            if unknown:
                return f"seat {index} has unknown fields: {sorted(unknown)}"
            role_id = str(entry.get("role_id") or "").strip()
            if role_id not in ROLE_REGISTRY:
                return f"seat {index}: unknown role '{role_id}'"
            seats.append(
                Seat(
                    seat_id=f"s{index + 1}",
                    role_id=role_id,
                    model=str(entry.get("model") or "").strip(),
                    votes=entry.get("votes") is not False,
                )
            )
    if not _local_seats_allowed():
        for seat in seats:
            violation = _seat_model_violation(seat.model)
            if violation:
                return violation
    return seats


def _parse_seat_capabilities(raw: Any, seats: list[Seat]) -> dict[str, tuple[str, ...]] | str:
    """The operator's EXPLICIT per-seat capability grant for this run, or a typed refusal.

    Shape: ``{"s1": ["workspace_write"]}``. Absent — the shape of every convene the product
    sends today — means every seat is read-only, which is the default the fence already holds.

    A class outside `containment.GRANTABLE_SIDE_EFFECT_CLASSES` is REFUSED here rather than
    dropped: an operator who asked to let a seat spend or publish must be told no. A seat id
    the bench does not contain is refused for the same reason — a grant nothing can consume
    reads as authority granted, and is not.
    """
    if raw in (None, "", {}):
        return {}
    if not isinstance(raw, dict):
        return "seat_capabilities must be an object mapping seat_id to a list of capabilities"
    known = {seat.seat_id for seat in seats}
    out: dict[str, tuple[str, ...]] = {}
    for seat_id, classes in raw.items():
        clean = str(seat_id or "").strip()
        if clean not in known:
            return f"seat_capabilities names unknown seat `{clean}` (bench: {sorted(known)})"
        if isinstance(classes, str) or not isinstance(classes, (list, tuple)):
            return f"seat_capabilities[{clean}] must be a list of capability classes"
        wanted = tuple(sorted({str(item or "").strip() for item in classes if str(item or "").strip()}))
        ungrantable = [
            item for item in wanted if item not in containment.GRANTABLE_SIDE_EFFECT_CLASSES
        ]
        if ungrantable:
            return (
                f"a council seat is never granted {ungrantable} at any breadth "
                f"(grantable: {sorted(containment.GRANTABLE_SIDE_EFFECT_CLASSES)})"
            )
        if wanted:
            out[clean] = wanted
    return out


def _publish_terminal_artifacts(run_id: str, state: str) -> None:
    """The finalization contract's publisher: the run's CHAT SUMMARY, written with the
    terminal state passed explicitly (the state file does not carry it yet -- that is the
    point: seeing ``converged`` must imply the artifact exists). The scorecard stays with
    the thread's ``finally``: it derives its terminal fact from the run's events, which the
    ledger only carries once the ending event is appended. The summary writer is
    idempotent, so the finally re-running it is a no-op -- never a second chat artifact."""
    persist_council_summary(run_id, terminal_state=state)


def _start_run_thread(
    orchestrator: CouncilOrchestrator, base_url: str, dispatch_capability: str
) -> None:
    """Register the run as live and drive it on its own thread until it ends or pauses.

    ONE body for convene and resume, so the pin restore, the fence release, the transcript
    write and the live/paused bookkeeping cannot drift between the first round and the
    fifth. The caller must already hold the pin under ``orchestrator.run_id``.
    """

    def _run() -> None:
        original_pin = read_current_pin(base_url)
        try:
            orchestrator.run()
        except Exception as exc:  # noqa: BLE001 — a crashed run is stated, never hidden
            orchestrator.store.append_event("run_crashed", error=f"{type(exc).__name__}: {exc}")
            orchestrator.store.write_state({
                "state": "crashed",
                "error": f"{type(exc).__name__}: {exc}",
                "problem": orchestrator.problem,
                "seats": [
                    {"seat_id": s.seat_id, "role_id": s.role_id, "model": s.model,
                     "votes": s.votes, "active": s.active}
                    for s in orchestrator.seats
                ],
            })
        finally:
            # Restoration runs while this run still OWNS the fence, so it carries the run's
            # capability — the release below is what ends the ownership, and the order
            # matters: hand the operator's pin back first, then stop being the owner.
            restore_pin(base_url, original_pin, dispatch_capability)
            # The operator's pin is back; hand the fence back too, on EVERY exit —
            # converged, no_convergence, stopped, crashed, AND paused. A council waiting on
            # a human must not hold every chat on the machine hostage: the operator needs
            # the chat to diagnose the seat that just died.
            pin_lock.release(orchestrator.run_id)
            # The verdict belongs in the chat it was convened from, on every TERMINAL exit.
            # A paused run is not finished, so `persist_council_summary` declines it (its
            # state is not terminal) and no half-written verdict reaches the transcript.
            try:
                # The derived contribution record, written once into the run's own ledger.
                # Declines a paused run: nothing has been committed, so nothing has been
                # adopted, and a ranking over that would be a guess with counts attached.
                persist_scorecard(orchestrator.run_id)
            except Exception as exc:  # a scorecard fault is stated, never silent
                orchestrator.store.append_event(
                    "scorecard_failed", error=f"{type(exc).__name__}: {exc}"
                )
            try:
                persist_council_summary(orchestrator.run_id)
            except Exception as exc:  # a transcript fault is stated in the ledger, never silent
                orchestrator.store.append_event(
                    "chat_summary_failed", error=f"{type(exc).__name__}: {exc}"
                )
            with _LOCK:
                _RUNS.pop(orchestrator.run_id, None)
                # Live → paused in ONE step under the lock: there is no instant in which a
                # run needing the operator is in neither registry and looks like it vanished.
                if orchestrator.current_state == _PAUSED_STATE:
                    _PAUSED[orchestrator.run_id] = orchestrator

    with _LOCK:
        # finalization contract: the terminal chat summary publishes before the terminal
        # state becomes visible (see orchestrator._finish)
        orchestrator.publish_terminal_artifacts = lambda state: _publish_terminal_artifacts(
            orchestrator.run_id, state)
        _RUNS[orchestrator.run_id] = orchestrator
        _PAUSED.pop(orchestrator.run_id, None)
    try:
        threading.Thread(
            target=_run, name=f"council-{orchestrator.run_id}", daemon=True
        ).start()
    except Exception:
        # No thread means no `finally` to hand the pin back. Undo the whole start.
        with _LOCK:
            _RUNS.pop(orchestrator.run_id, None)
        pin_lock.release(orchestrator.run_id)
        raise


def convene(body: dict[str, Any], *, base_url: str, workspace_root: str) -> tuple[int, dict[str, Any]]:
    unknown = set(body.keys()) - _ALLOWED_CONVENE_FIELDS
    if unknown:
        return 400, {"ok": False, "error": f"unknown fields: {sorted(unknown)}"}
    # ONE live council at a time: v1 seat dispatch owns the global composer pin, and five
    # silent duplicate runs (measured live, 2026-08-28) all raced it. A second convene while
    # one is live is a typed refusal, never a queue.
    with _LOCK:
        live_ids = sorted(_RUNS.keys())
        paused_ids = sorted(_PAUSED.keys())
    if live_ids:
        return 409, {
            "ok": False,
            "error": (
                f"council_already_live: run {live_ids[0]} is still live — one council at a "
                "time owns the model pin. Stop it or let it finish, then convene."
            ),
            "live_run_id": live_ids[0],
        }
    if paused_ids:
        # A paused run still owns the bench, the chat card and the right to resume. A
        # second convene would strand it with no way back to the work it already paid for.
        return 409, {
            "ok": False,
            "error": "council_needs_attention",
            "run_id": paused_ids[0],
            "detail": (
                f"council {paused_ids[0]} is paused waiting on you — resolve its failed "
                "seat (retry it, replace its model, or disable it) and resume it, or stop "
                "it, before convening another."
            ),
        }
    seats = _parse_seats(body.get("seats"))
    if isinstance(seats, str):
        return 400, {"ok": False, "error": seats}
    seat_grants = _parse_seat_capabilities(body.get("seat_capabilities"), seats)
    if isinstance(seat_grants, str):
        return 400, {"ok": False, "error": seat_grants}
    try:
        max_rounds = int(body.get("max_rounds") or 5)
    except (TypeError, ValueError):
        return 400, {"ok": False, "error": "max_rounds must be an integer"}
    max_rounds = max(2, min(10, max_rounds))
    # Take the machine's model pin BEFORE the run exists, and mint the capability the
    # seats will present to get back through the fence. The run id is minted here rather
    # than by the orchestrator because the pin is owned by a run id, and a pin taken
    # under a name nothing else uses is a pin nothing can release.
    run_id = f"council-{uuid.uuid4().hex[:12]}"
    try:
        dispatch_capability = pin_lock.acquire(run_id)
    except pin_lock.CouncilPinBusyError as exc:
        # Refused, never forced: a chat turn already in flight keeps the pin it was
        # admitted under, and the council says so instead of pinning over it.
        return 409, {"ok": False, "error": exc.code, "detail": exc.detail}
    # From here the pin is HELD, and every path out of this function that does not hand a
    # live thread the responsibility for releasing it must release it itself: a typed 400,
    # a seat factory that raises, a thread that will not start. Without this, one bad
    # convene locks every chat on the machine until the daemon restarts.
    try:
        orchestrator = CouncilOrchestrator(
            problem=str(body.get("problem") or ""),
            exhibits=str(body.get("exhibits") or ""),
            seats=seats,
            seat_turn=live_seat_turn_factory(base_url, dispatch_capability),
            workspace_root=workspace_root,
            chat_session=str(body.get("chat_session") or ""),
            max_rounds=max_rounds,
            run_id=run_id,
            workspace_sha=_workspace_sha(workspace_root),
            seat_grants=seat_grants,
            on_transition=lambda event: pin_lock.note_state(
                run_id, str(event.get("state") or "")
            ),
        )
    except CouncilRunError as exc:
        pin_lock.release(run_id)
        return 400, {"ok": False, "error": str(exc)}
    except BaseException:
        pin_lock.release(run_id)
        raise

    _start_run_thread(orchestrator, base_url, dispatch_capability)
    return 200, {
        "ok": True,
        "run_id": orchestrator.run_id,
        "seats": [
            {"seat_id": s.seat_id, "role_id": s.role_id, "model": s.model or "auto", "votes": s.votes}
            for s in seats
        ],
        "max_rounds": max_rounds,
        # Stated back, always — including the empty default. A run that says nothing about
        # capabilities is a run whose seats are read-only, and the operator should be able to
        # read that off the convene response rather than infer it from silence.
        "seat_capabilities": {seat_id: list(classes) for seat_id, classes in seat_grants.items()},
    }


#: What an operator may do to a paused seat.
_SEAT_ACTIONS = {"retry", "replace", "disable"}


def _paused_run(run_id: str) -> tuple[CouncilOrchestrator | None, dict[str, Any] | None]:
    """The paused orchestrator for ``run_id``, or the typed refusal to return instead."""
    clean = str(run_id or "").strip()
    if not clean:
        return None, {"ok": False, "error": "missing run_id"}
    with _LOCK:
        live = clean in _RUNS
        orchestrator = _PAUSED.get(clean)
    if orchestrator is not None:
        return orchestrator, None
    if live:
        return None, {
            "ok": False, "error": "council_running",
            "detail": "this council is running; it is not waiting on you",
        }
    try:
        state = CouncilRunStore(clean).read_state()
    except ValueError:
        return None, {"ok": False, "error": "invalid run id"}
    if state is None:
        return None, {"ok": False, "error": "no such council run"}
    settled = str(state.get("state") or "")
    if settled == _PAUSED_STATE:
        # The state file says paused and nothing in this process holds it: the run's thread
        # died with the previous daemon. Say exactly that rather than accepting a click that
        # can never take effect.
        return None, {
            "ok": False, "error": "council_run_not_resumable",
            "detail": (
                "this council was paused by a daemon that is no longer running, so its "
                "bench and its position are gone. Its evidence is intact in the run "
                "ledger; convene a new council to carry on from it."
            ),
        }
    if settled == "stopped":
        # Cancellation wins, and it keeps winning. A resume or a seat action that arrives
        # after a stop — or was already in flight when it landed — is told what actually
        # happened, not merely that the run is no longer paused.
        return None, {
            "ok": False, "error": "run_stopped", "run_id": clean,
            "detail": "this council was stopped; a stop is never overridden by a resume",
        }
    return None, {
        "ok": False, "error": "council_not_paused",
        "detail": f"run {clean} is {settled or 'unknown'}, not waiting on you",
    }


def _refusal_status(refusal: dict[str, Any]) -> int:
    error = str(refusal.get("error") or "")
    if error in {"missing run_id", "invalid run id"}:
        return 400
    return 404 if error == "no such council run" else 409


def seat_action(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """One operator decision about one seat: retry it, re-model it, or take it out.

    Every action is idempotent — a second identical click reports ``changed: false`` and
    writes no second ledger row — and none of them dispatches anything: they change what
    the bench WILL be, and `resume` is the separate, deliberate act that runs it.
    """
    unknown = set(body.keys()) - {"run_id", "seat_id", "action", "model"}
    if unknown:
        return 400, {"ok": False, "error": f"unknown fields: {sorted(unknown)}"}
    action = str(body.get("action") or "").strip().lower()
    if action not in _SEAT_ACTIONS:
        return 400, {
            "ok": False,
            "error": f"unknown action {action!r}; expected one of {sorted(_SEAT_ACTIONS)}",
        }
    orchestrator, refusal = _paused_run(body.get("run_id"))
    if orchestrator is None:
        return _refusal_status(refusal), refusal
    seat_id = str(body.get("seat_id") or "").strip()
    if action == "replace":
        model = str(body.get("model") or "").strip()
        # The cloud-only seat wall is the SAME one convene enforces. A replacement is a
        # seat model like any other, and this door does not get its own weaker rule.
        if not _local_seats_allowed():
            violation = _seat_model_violation(model)
            if violation:
                return 400, {"ok": False, "error": violation}
        result = orchestrator.replace_seat_model(seat_id, model)
    elif action == "retry":
        result = orchestrator.retry_seat(seat_id)
    else:
        result = orchestrator.disable_seat(seat_id)
    if not result.get("ok"):
        code = 404 if result.get("error") == "unknown_seat" else 409
        return code, {"ok": False, **result}
    return 200, {"ok": True, "run_id": orchestrator.run_id,
                 "state": orchestrator.current_state, **result}


def resume(run_id: str, *, base_url: str) -> tuple[int, dict[str, Any]]:
    """Carry on the SAME run: same id, same ledger, same chat card, same evidence.

    Takes the model pin back through the existing fence — a resumed run is a pin owner like
    any other — and re-enters `run()` at the round it paused in.
    """
    orchestrator, refusal = _paused_run(run_id)
    if orchestrator is None:
        return _refusal_status(refusal), refusal
    if orchestrator.stop_requested():
        # Cancellation wins. An operator who stopped this run must not have it restarted by
        # a resume that was already in flight when they did.
        return 409, {
            "ok": False, "error": "run_stopped", "run_id": orchestrator.run_id,
            "detail": "this council was stopped; a stop is never overridden by a resume",
        }
    try:
        dispatch_capability = pin_lock.acquire(orchestrator.run_id, state=_PAUSED_STATE)
    except pin_lock.CouncilPinBusyError as exc:
        return 409, {"ok": False, "error": exc.code, "detail": exc.detail}
    # A fresh capability for a fresh ownership: the one minted at convene died with that
    # acquire, and the seats must present the pass matching the pin actually held now.
    orchestrator.seat_turn = live_seat_turn_factory(base_url, dispatch_capability)
    try:
        _start_run_thread(orchestrator, base_url, dispatch_capability)
    except BaseException:
        pin_lock.release(orchestrator.run_id)
        raise
    return 200, {"ok": True, "run_id": orchestrator.run_id, "resumed": True,
                 "round_no": orchestrator.round_no}


def forget_all_runs_for_tests() -> None:
    """Drop every live and paused run — what a daemon restart leaves behind, on demand."""
    with _LOCK:
        _RUNS.clear()
        _PAUSED.clear()


def status(run_id: str) -> tuple[int, dict[str, Any]]:
    try:
        state = CouncilRunStore(run_id).read_state()
    except ValueError:
        return 400, {"ok": False, "error": "invalid run id"}
    if state is None:
        return 404, {"ok": False, "error": "no such council run"}
    with _LOCK:
        live = run_id in _RUNS
    return 200, {
        "ok": True,
        "live": live,
        "run": state,
        # Every effect this run's seats ATTEMPTED, with its terminal outcome. On 2026-09-02 a
        # seat's write was discovered by a clean-tree gate hours after the run; a refusal that
        # is only visible as a missing file is not a record.
        "seat_effects": [dict(row) for row in containment.effect_ledger(run_id)],
    }


#: One page of ledger rows. The ledger is append-only and a caller resumes with
#: ``next_after``, so a cap bounds one response without ever hiding an event.
_EVENTS_PAGE_LIMIT = 500


def events(run_id: str, after: Any = 0, seq: Any = None) -> tuple[int, dict[str, Any]]:
    """Ordered ledger rows for one run, strictly after the ``after`` cursor.

    The state snapshot is a VIEW that gets rewritten; this is the RECORD, and it is the
    only surface through which retries, per-attempt outcomes and round transitions are
    readable at all. Both inputs fail typed rather than being coerced: a cursor that
    silently became 0 would replay the whole run as if it were new.
    """
    cleaned = str(run_id or "").strip()
    if not cleaned:
        return 400, {"ok": False, "error": "missing run parameter"}
    try:
        store = CouncilRunStore(cleaned)
    except ValueError:
        return 400, {"ok": False, "error": "invalid run id"}
    if store.run_id != cleaned:
        # `_safe_run_id` strips path separators and other unsafe characters. A request
        # that had to be sanitized to be usable is refused, never quietly redirected to
        # whichever run the stripped name happens to name.
        return 400, {"ok": False, "error": "invalid run id"}
    raw_after = 0 if after in (None, "") else after
    try:
        cursor = int(str(raw_after).strip())
    except (TypeError, ValueError):
        return 400, {"ok": False, "error": "after must be a non-negative integer"}
    if cursor < 0:
        return 400, {"ok": False, "error": "after must be a non-negative integer"}
    if store.read_state() is None and not store.read_events():
        return 404, {"ok": False, "error": "no such council run"}
    with _LOCK:
        live = store.run_id in _RUNS

    if seq not in (None, ""):
        # Single-event resolution: what a snapshot's `text_ref` points at. It answers
        # about ONE row, so it carries no cursor and no page.
        try:
            wanted = int(str(seq).strip())
        except (TypeError, ValueError):
            return 400, {"ok": False, "error": "seq must be a positive integer"}
        if wanted < 1:
            return 400, {"ok": False, "error": "seq must be a positive integer"}
        row = store.read_event(wanted)
        if row is None:
            return 404, {"ok": False, "error": f"no event at seq {wanted} for this run"}
        text = row.get("text")
        stored_hash = str(row.get("report_sha256") or "")
        # Verified against the EXACT stored bytes, recomputed here — a served report is
        # never asserted to match a hash nobody checked.
        verified: bool | None = None
        if isinstance(text, str) and stored_hash:
            verified = hashlib.sha256(text.encode("utf-8")).hexdigest() == stored_hash
        return 200, {
            "ok": True, "run_id": store.run_id, "live": live,
            "events": [row], "seq": wanted, "text_verified": verified,
        }

    page = store.read_events_ordered(after=cursor)[:_EVENTS_PAGE_LIMIT]
    # The cursor to resume from: the last row served, or the caller's own cursor when
    # this page was empty. It never walks backwards.
    next_after = page[-1]["seq"] if page else cursor
    # Counted, not inferred. `len(page) < LIMIT` was wrong at exactly one full page: with
    # 500 events remaining it reported "incomplete" while nothing was left, and a paging
    # client would fetch an empty tail forever.
    has_more = store.count_events_after(next_after) > 0
    return 200, {
        "ok": True,
        "run_id": store.run_id,
        "live": live,
        "events": page,
        "next_after": next_after,
        "has_more": has_more,
        "complete": not has_more,
    }


def stop(run_id: str) -> tuple[int, dict[str, Any]]:
    clean = str(run_id or "")
    with _LOCK:
        orchestrator = _RUNS.get(clean)
        paused = _PAUSED.get(clean)
    if orchestrator is not None:
        orchestrator.request_stop()
        return 200, {"ok": True, "run_id": orchestrator.run_id, "stopping": True}
    if paused is not None:
        # A paused run has no thread to notice the flag, so stopping it IS the transition:
        # it ends here, terminally, and leaves the paused registry so no resume can find it.
        # The flag is set as well, so a resume already in flight loses the race by seeing it.
        paused.request_stop()
        paused.finish_stopped()
        with _LOCK:
            _PAUSED.pop(clean, None)
        return 200, {"ok": True, "run_id": paused.run_id, "stopping": True, "stopped": True}
    return 404, {"ok": False, "error": "no live council run with that id"}


def runs(limit: int = 20) -> tuple[int, dict[str, Any]]:
    with _LOCK:
        live_ids = set(_RUNS.keys())
    rows = []
    for row in list_runs(limit):
        rows.append({**row, "live": row.get("run_id") in live_ids})
    return 200, {"ok": True, "runs": rows}
