"""The council's terminal result, written into the chat it was convened from.

A run whose only home is a modal is a run the operator loses the moment they close it,
switch chats, or reload. What survives a reload is the chat transcript — so a terminal
council writes ONE assistant-authored artifact into its originating chat: a compact,
readable summary of what the bench actually decided, closed by a machine-readable
marker line::

    council | run=<run_id> | <terminal-state> | r<round>/<max>

Two readers, one string. A client that knows nothing about councils renders plain text
that still says what happened, who sat, and what was decided. A client that does knows
the run id, the terminal state and the round it stopped on, and can re-fetch the full
record from ``/api/council/status`` and ``/api/council/events`` to rebuild the folded
card. The summary is never the evidence — the ledger is; the summary is what remains
readable when the evidence has been pruned.

Persistence goes through ``append_assistant_artifact_event``, NOT the conversation
writer: a council verdict has no user side, and manufacturing one would feed a machine
adjudication into fact capture, heuristics and the semantic store as if the operator had
said it.
"""

from __future__ import annotations

import re
from typing import Any

from core.council.run_store import CouncilRunStore

#: Run states from which nothing further happens. Anything else is still in flight and
#: has no result to write; persisting one would put a half-finished verdict in the chat.
TERMINAL_STATES = frozenset({"converged", "failed", "no_convergence", "stopped", "crashed"})

#: The ledger row that records the write. It is the evidence that the chat was written
#: AND the idempotency key: the crash path and the normal path can both fire, and the
#: operator must not end up with the same verdict twice.
CHAT_SUMMARY_EVENT = "chat_summary_persisted"

_MARKER_RE = re.compile(
    r"^council \| run=(?P<run>[A-Za-z0-9_-]{1,80}) \| (?P<state>[a-z_]{1,40}) \| "
    r"r(?P<round>\d{1,4})/(?P<max>\d{1,4})$",
    re.MULTILINE,
)

_HEADLINES = {
    "converged": "adjudicated",
    "failed": "failed",
    "no_convergence": "no convergence",
    "stopped": "stopped by the operator",
    "crashed": "crashed",
}


def council_marker(*, run_id: str, state: str, round_no: int, max_rounds: int) -> str:
    """The final line of a persisted summary. One shape, stated once, parsed by one regex."""
    return f"council | run={run_id} | {state} | r{int(round_no)}/{int(max_rounds)}"


def parse_council_marker(text: str) -> dict[str, Any] | None:
    """The marker carried by ``text``, or None.

    Line-anchored on purpose: an operator writing *about* a council in an ordinary
    message must never be mistaken for one. A marker is a whole line in the agreed shape
    or it is not a marker.
    """
    match = _MARKER_RE.search(str(text or ""))
    if match is None:
        return None
    return {
        "run_id": match.group("run"),
        "state": match.group("state"),
        "round_no": int(match.group("round")),
        "max_rounds": int(match.group("max")),
    }


def _seat_line(seat: dict[str, Any], reports: list[dict[str, Any]]) -> str:
    """One seat's row: its ROLE, what it was asked to run on, and what actually answered.

    Requested and actual are never collapsed. A seat whose actual model was never
    established by streamed evidence says "actual model unknown" — echoing the request
    back as though it were the answer is the exact untruth C2 removed from the record,
    and it must not reappear in prose.
    """
    seat_id = str(seat.get("seat_id") or "")
    role = str(seat.get("role_id") or seat_id)
    if seat.get("votes") is False:
        role += " (advisor)"
    mine = [r for r in reports if str(r.get("seat_id") or "") == seat_id]
    last = mine[-1] if mine else None
    requested = str((last or {}).get("model_requested") or seat.get("model") or "") or "no model named"
    actual = str((last or {}).get("model_actual") or "").strip()
    provenance = f"asked for {requested}, " + (
        f"answered by {actual}" if actual else "actual model unknown"
    )
    if last is None:
        return f"  - {role} — no report ({provenance})"
    status = str(last.get("status") or "")
    verdict = str(last.get("verdict") or "")
    parts = [status or "no status"]
    if verdict:
        parts.append(verdict)
    elif status == "landed":
        parts.append("no parseable verdict")
    if last.get("failure"):
        parts.append(str(last.get("failure")))
    receipts = int(last.get("receipt_count") or 0)
    if receipts:
        parts.append(f"{receipts} receipts")
    retries = sum(int(r.get("retries_used") or 0) for r in mine)
    if retries:
        parts.append(f"{retries} retries")
    return f"  - {role} — {' · '.join(parts)} ({provenance})"


def council_summary_text(state: dict[str, Any]) -> str:
    """The readable artifact for one terminal run. Plain text, marker last.

    Everything here is read off the run state. Nothing is inferred, softened or
    completed: a crashed run with no rounds and no outcome still produces a summary,
    and that summary says it crashed and why.
    """
    run_id = str(state.get("run_id") or "")
    run_state = str(state.get("state") or "")
    rounds = [r for r in (state.get("rounds") or []) if isinstance(r, dict)]
    max_rounds = int(state.get("max_rounds") or 0)
    outcome = state.get("outcome") if isinstance(state.get("outcome"), dict) else {}
    reports = [r for round_row in rounds for r in (round_row.get("reports") or []) if isinstance(r, dict)]

    head = _HEADLINES.get(run_state, run_state or "unknown state")
    lines = [f"COUNCIL — {head} after {len(rounds)} round{'' if len(rounds) == 1 else 's'}."]

    problem = " ".join(str(state.get("problem") or "").split())
    if problem:
        lines.append(f"Problem: {problem[:300]}")

    candidate = str(outcome.get("candidate") or state.get("candidate") or "").strip()
    if candidate:
        lines.append(f"Candidate: {candidate}")
    elif run_state == "converged":
        lines.append("Candidate: none recorded")

    if run_state == "converged":
        lines.append(
            f"Vote: {int(outcome.get('agree') or 0)} agree / "
            f"{int(outcome.get('disagree') or 0)} disagree."
        )
    detail = str(outcome.get("detail") or "").strip()
    if detail:
        lines.append(f"Detail: {detail}")
    error = str(state.get("error") or "").strip()
    if error:
        lines.append(f"Error: {error}")
    failed_seats = [str(s) for s in (outcome.get("failed_seats") or [])]
    if failed_seats:
        lines.append("Failed seats: " + ", ".join(failed_seats))

    seats = [s for s in (state.get("seats") or []) if isinstance(s, dict)]
    if seats:
        lines.append("Seats:")
        lines.extend(_seat_line(seat, reports) for seat in seats)

    lines.append(
        "Adjudication only — promotion, merge and spend remain with the operator."
    )
    lines.append("")
    lines.append(
        council_marker(
            run_id=run_id, state=run_state, round_no=len(rounds), max_rounds=max_rounds
        )
    )
    return "\n".join(lines)


def persist_council_summary(run_id: str, *, terminal_state: str = "") -> bool:
    """Write one terminal run's summary into its originating chat. True when it wrote.

    False — never an exception — for every reason not to: a run still in flight, a run
    convened with no chat to belong to, a missing state file, or a run whose summary is
    already in the chat. The caller is a ``finally`` block on a council thread; a
    persistence problem must not be able to take the run's own reporting down with it.

    ``terminal_state`` is the finalization contract's parameter (Goal 2, 2026-09-18): the
    orchestrator publishes the terminal artifacts BEFORE the terminal state becomes
    visible, so an observer who sees ``converged`` is guaranteed the chat summary exists.
    The override only WIDENS the gate for that pre-publish call; it never lets a paused
    run publish (the orchestrator passes it only on terminal exits)."""
    try:
        store = CouncilRunStore(run_id)
    except ValueError:
        return False
    state = store.read_state()
    if not isinstance(state, dict):
        return False
    effective = str(terminal_state or "") or str(state.get("state") or "")
    if effective not in TERMINAL_STATES:
        return False
    if terminal_state:
        state = {**state, "state": effective}
    chat_session = str(state.get("chat_session") or "").strip()
    if not chat_session:
        return False
    if any(row.get("type") == CHAT_SUMMARY_EVENT for row in store.read_events()):
        return False
    from core.persistent_memory import (
        ARTIFACT_KIND_COUNCIL_SUMMARY,
        append_assistant_artifact_event,
    )

    text = council_summary_text({**state, "run_id": store.run_id})
    written = append_assistant_artifact_event(
        session_id=chat_session,
        text=text,
        artifact_kind=ARTIFACT_KIND_COUNCIL_SUMMARY,
        artifact={
            "run_id": store.run_id,
            "state": str(state.get("state") or ""),
            "rounds": len([r for r in (state.get("rounds") or []) if isinstance(r, dict)]),
            "max_rounds": int(state.get("max_rounds") or 0),
        },
    )
    if not written:
        return False
    store.append_event(
        CHAT_SUMMARY_EVENT,
        chat_session=chat_session,
        terminal_state=str(state.get("state") or ""),
        characters=len(text),
    )
    return True
