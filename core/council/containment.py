"""THE council seat capability and workspace authority — one fence, held as state.

The incident (2026-09-02, blocker 7 of
`validation-logs/unified-demo-candidate-20260902/REPORT.md`): a council run kept running
in the background after the browser drive moved on, and its BUILDER seat made a real,
persistent edit to `core/agent_runtime/turn_planner_hook.py` in the operator's checkout
through the ordinary workspace tools. Nothing malfunctioned. There were three things
standing between a seat and the production source tree, and not one of them was a
boundary the runtime HELD:

* the role brief's prose — "You never apply the fix" (`core/council/roles.py`);
* the seat context's prose — "You have read-only tools" (`orchestrator._seat_context`),
  which was not merely unenforced but false;
* `"mode": "plan"` as a FIELD in the dispatch HTTP body (`core/council/dispatch.py`),
  which registers a per-session mode any later turn can re-register, and which only ever
  reached `decide_tool_call` on the call paths that happen to run it — `execute_runtime_tool`
  is reachable directly from the sub-envelope executor, the machine fast paths and the
  research loop, and none of those takes a permission decision.

This module is the replacement, and it is the same repair `core/agent_runtime/audit_policy.py`
made one layer up after the same class of incident: permission stops being prose and
becomes a value. A council run REGISTERS its seats here when it is convened. From that
moment the fence is a property of the seat's session id, held in this process, and it is
consulted at the ONE door every real runtime effect passes through
(`core.runtime_execution_tools.execute_runtime_tool`). A seat cannot present it, drop it,
or re-register it: nothing a model emits and nothing in an HTTP body names a grant here.

The laws, in the order they are enforced:

1. **Read-only by default.** A registered seat may exercise exactly one declared
   side-effect class without a grant: ``read_only``. Investigation is the seat's job and
   is untouched.
2. **Grants are explicit, per run, per seat, per class.** Only the operator's convene
   request (or a direct server-side call) can grant one, only from
   ``GRANTABLE_SIDE_EFFECT_CLASSES`` (which holds exactly ``workspace_write``), and only for
   that run. Shells, interpreters, test runners, formatters, sub-task envelopes, money,
   network sends, publishing, runtime-capability changes and any class this build has never
   heard of are never grantable at any breadth — the same ceiling shape
   `mode_permission_policy._INTERNAL_AUTHORITY_CEILING` uses.
3. **A grant of the class is not a grant of every tool that declares it.** Only the four
   typed mutators in ``SEAT_WORKSPACE_MUTATION_TOOLS`` are invocable, because only those four
   resolve their target against the workspace root this fence redirects.
4. **A granted seat still works only inside a disposable workspace.** Its mutating calls
   are re-rooted onto a per-seat directory outside every checkout, and every path
   argument is resolved (aliases bound, ``~`` expanded, ``..`` collapsed, symlinks followed,
   hard links refused) and required to land inside it. The operator's checkout is not
   reachable from a seat by any tool name.
5. **A cancelled or finished run fences every seat, including one already running.** The
   orchestrator's stop flag is only read between attempts, so a seat inside a 15-minute turn
   never sees it. Fencing is read at the effect door instead — which is inside the turn —
   and it also cancels each in-flight seat turn at the server through
   `core.live_turns.request_cancel`, closes the council's end of the stream, refuses the next
   model pin, and discards any result that arrives late.

   This module contains a seat by never giving it a subprocess, not by confining one. There
   is no namespace, jail or seccomp profile here and nothing in it should be described as a
   sandbox.
6. **Every attempted effect leaves a row** — ``executed``, ``refused``, ``failed`` or
   ``cancelled`` — so "the seat tried" is answerable from a ledger rather than from a
   clean-tree gate noticing afterwards.
7. **A context with no registered seat is not a council context**, and this module returns
   "no opinion" for it. Ordinary tools keep exactly the behaviour they had.

Classification is by the tool's own DECLARED side-effect class, never by its name: a tool
added tomorrow is contained by construction, and an unknown class fails closed.
"""

from __future__ import annotations

import contextlib
import hashlib
import shutil
import tempfile
import threading
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: The status a refused council effect carries out of the runtime tool door. Named, so a
#: containment refusal is never confused with a tool that merely failed.
CONTAINED_STATUS = "blocked_by_council_containment"

#: The one class a registered seat exercises with no grant at all. Everything a council
#: seat legitimately does to investigate — read, list, search, git status, fetch — declares
#: this class in `core/runtime_tool_contracts.py`.
READ_ONLY_CLASS = "read_only"

#: The ONE class an operator may grant to a seat. `sandbox_command` and
#: `validation_command` were here and are now permanently out — see
#: `NEVER_GRANTABLE_SIDE_EFFECT_CLASSES`.
GRANTABLE_SIDE_EFFECT_CLASSES: frozenset[str] = frozenset({"workspace_write"})

#: Never delegable to a seat at any breadth, whatever an operator asks for.
#:
#: `sandbox_command` and `validation_command` lead the list, and the reason is the one this
#: module got wrong the first time. A granted shell used to be "contained" by running with
#: the seat's workspace as its working directory and refusing a command whose TEXT named an
#: absolute path outside it. That is not containment, it is a spell-check. `$HOME/x`,
#: `${VAR}/x`, `$(pwd)/../x`, backticks, `cd ..`, an interpreter that builds the path at
#: runtime (`python -c "open(os.environ['HOME']+'/x','w')"`), a symlink created by the command
#: itself, `ln`, and every one of the thousand ways a shell can name a file without spelling
#: it — all of them walk straight past a reader of the command line. Confining a child
#: process needs the OS (a namespace, a jail, a seccomp/sandbox profile), and this build does
#: not have one. So the honest boundary is that a seat never gets a shell at all.
#:
#: The rest are here for the reason they always were: money, sending, publishing, changing
#: what the runtime can do, and orchestration that spawns further work are not an operator's
#: to hand to a model seat.
NEVER_GRANTABLE_SIDE_EFFECT_CLASSES: frozenset[str] = frozenset(
    {
        "sandbox_command",
        "validation_command",
        "task_orchestration",
        "builder_state",
        "creative_state",
        "media_generation",
        "network_send",
        "network_publish",
        "wallet_spend",
        "credit_spend",
        "runtime_capability_change",
    }
)

#: The ONLY tools a granted seat may actually invoke.
#:
#: A grant of `workspace_write` is necessary and NOT sufficient. Seventeen intents declare
#: that class, and most of them do not resolve their target against the `workspace_root` this
#: fence redirects: `machine.write_file`/`machine.ensure_directory` take no workspace root at
#: all and resolve under the machine home; `media.*` writes into the media store; `skill.*`
#: into the skills directory; `operator.*` through an external lane. Redirecting the workspace
#: does not move any of them, so a class-only grant would have handed a seat four different
#: doors out of its own jail. `workspace.rollback_last_change` is excluded for the opposite
#: reason: it takes no path at all, so there is nothing for the jail check to bite on, and a
#: seat that can create and edit does not need to undo.
#:
#: These four take `workspace_root=workspace_root` from the dispatcher and resolve every path
#: through it (`core/runtime_execution_tools.py`, the `workspace.*` branches), which is what
#: makes the redirect real rather than decorative.
SEAT_WORKSPACE_MUTATION_TOOLS: frozenset[str] = frozenset(
    {
        "workspace.write_file",
        "workspace.replace_in_file",
        "workspace.apply_unified_diff",
        "workspace.ensure_directory",
    }
)

#: Terminal outcomes a ledger row can carry.
OUTCOME_EXECUTED = "executed"
OUTCOME_REFUSED = "refused"
OUTCOME_FAILED = "failed"
OUTCOME_CANCELLED = "cancelled"

#: How many finished runs keep their jail and ledger for inspection before the oldest is
#: swept. The jail holds a builder seat's candidate work, which is the artifact an operator
#: promotes — deleting it the instant the run ends would throw away the only thing the run
#: produced. It is still disposable: outside every checkout, and swept on this bound.
_RETAINED_FINISHED_RUNS = 8

#: Argument keys that name something on disk. Same list as
#: `mode_permission_policy._PATH_ARG_KEYS`; kept here rather than imported so the fence has
#: no dependency on the mode layer it is deliberately independent of.
_PATH_ARG_KEYS: tuple[str, ...] = (
    "path",
    "paths",
    "file_path",
    "target_path",
    "destination",
    "destination_path",
    "source",
    "source_path",
    "directory",
    "cwd",
)

class CouncilContainmentError(RuntimeError):
    """A caller asked this authority for something it will never do."""


@dataclass(frozen=True)
class InflightSeatTurn:
    """One seat turn that has been dispatched and has not come back.

    ``session_id`` and ``turn_id`` are the pair the seat's `/api/chat` request carries, and
    the pair `core.web.api.runtime` registers with `core.live_turns.register_turn` before it
    starts the worker thread. That makes them the handle a cancellation actually needs:
    `live_turns.request_cancel` sets the Event the router's cancel check reads, so the model
    and tool loop stop — not merely the HTTP stream.

    ``closer`` closes the council's own end of that stream, so the dispatching thread stops
    waiting on a turn that has been told to stop.
    """

    seat_id: str
    session_id: str
    turn_id: str
    closer: Any = None


@dataclass(frozen=True)
class SeatBinding:
    """The seat a source context belongs to, and what that seat may do right now."""

    run_id: str
    seat_id: str
    session_id: str
    granted: frozenset[str]
    workspace: Path
    live: bool
    fence_reason: str


@dataclass
class _Run:
    run_id: str
    grants: dict[str, frozenset[str]]
    sessions: dict[str, str]  # session_id -> seat_id
    jail_root: Path
    live: bool = True
    fence_reason: str = ""
    finished_at: float = 0.0
    ledger: list[dict[str, Any]] = field(default_factory=list)
    #: Optional durable publisher for ledger rows, installed by whoever owns the run's
    #: record. A CALLABLE, not a store import: this module must not be able to fail — or to
    #: be made slow — by the persistence layer of something it only reports to.
    sink: Any = None
    #: Seat turns currently executing, by token. Fencing the run cancels every one of them.
    inflight: dict[int, InflightSeatTurn] = field(default_factory=dict)
    next_token: int = 0


_LOCK = threading.RLock()
_RUNS: dict[str, _Run] = {}
_FINISHED: list[str] = []


# --------------------------------------------------------------------------- identity


def seat_session_id_for(run_id: str, seat_id: str) -> str:
    """The chat session id a seat's turns run under.

    Derived, not allocated, so the authority can register a run's seats BEFORE any of them
    has spoken — the fence has to exist before the first turn, not after it. This is the
    one definition; `core.council.dispatch.seat_session_id` delegates to it so a recorded
    id and a registered id are the same value by construction rather than by two places
    agreeing to hash alike.
    """
    run, seat = str(run_id or ""), str(seat_id or "")
    material = f"council-seat:{run}:{seat}"
    return "openclaw:" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]


def _context_session_id(source_context: Mapping[str, Any] | None) -> str:
    context = source_context or {}
    return str(context.get("runtime_session_id") or context.get("session_id") or "").strip()


def _inherited_session_id() -> str:
    """The session id of the turn that OPENED this execution context, if any.

    A seat's turn can spawn work whose own `source_context` no longer carries the session
    id — a sub-task envelope, a worker task, a fast path that rebuilds the context from
    parts. The effect ledger is bound to a `ContextVar` and inherited by that work through
    `copy_context`, and it froze the originating context when the turn opened. Reading the
    session id back out of it means losing the id downstream widens nothing: the fence
    still finds the seat. Failure here is never fatal — it only means this second binding
    had nothing to add.
    """
    try:
        from core.effect_gateway import current_effect_ledger

        ledger = current_effect_ledger()
    except Exception:
        return ""
    seen = 0
    while ledger is not None and seen < 8:
        seen += 1
        candidate = _context_session_id(getattr(ledger, "source_context", None))
        if candidate:
            return candidate
        ledger = getattr(ledger, "parent", None)
    return ""


# --------------------------------------------------------------------- registration


def _jail_parent() -> Path:
    """Where disposable seat workspaces live: outside every checkout, always."""
    return Path(tempfile.gettempdir()) / "vool-council-workspaces"


def _normalize_grants(grants: Mapping[str, Iterable[str]] | None) -> dict[str, frozenset[str]]:
    """Operator grants, narrowed to what is grantable at all. Never raises on junk."""
    out: dict[str, frozenset[str]] = {}
    for seat_id, classes in dict(grants or {}).items():
        clean_seat = str(seat_id or "").strip()
        if not clean_seat:
            continue
        wanted = {str(item or "").strip() for item in (classes or ())}
        allowed = frozenset(wanted & GRANTABLE_SIDE_EFFECT_CLASSES)
        if allowed:
            out[clean_seat] = allowed
    return out


def register_run(
    run_id: str,
    *,
    seat_ids: Iterable[str],
    grants: Mapping[str, Iterable[str]] | None = None,
    sink: Any = None,
) -> None:
    """Register a council run's seats and their (usually empty) capability grants.

    Called by `CouncilOrchestrator.__post_init__`, so containment exists from the moment a
    run does — including for a run constructed directly in server code or a test, which is
    the point: there is no way to have a council run that is not registered.

    Re-registering the same run id replaces its grants and RE-ARMS it. That is the resume
    path (`core.council.api.resume`), and it is safe because only server code holds this
    function; nothing reachable from a model or an HTTP body can call it.
    """
    clean_run = str(run_id or "").strip()
    if not clean_run:
        raise CouncilContainmentError("a council run must have an id before it has seats")
    seats = [str(seat_id or "").strip() for seat_id in seat_ids]
    seats = [seat_id for seat_id in seats if seat_id]
    normalized = _normalize_grants(grants)
    with _LOCK:
        existing = _RUNS.get(clean_run)
        jail_root = existing.jail_root if existing is not None else _jail_parent() / clean_run
        run = _Run(
            run_id=clean_run,
            grants=normalized,
            sessions={seat_session_id_for(clean_run, seat_id): seat_id for seat_id in seats},
            jail_root=jail_root,
            live=True,
            fence_reason="",
            ledger=existing.ledger if existing is not None else [],
            sink=sink if sink is not None else (existing.sink if existing is not None else None),
        )
        _RUNS[clean_run] = run
        if clean_run in _FINISHED:
            _FINISHED.remove(clean_run)


def grant(run_id: str, seat_id: str, side_effect_class: str) -> frozenset[str]:
    """Grant ONE seat ONE capability class for ONE run. Returns the seat's full grant.

    A class outside `GRANTABLE_SIDE_EFFECT_CLASSES` raises rather than being silently
    dropped: an operator who asked to grant spending should be told no, not told yes and
    then quietly refused at every call.
    """
    wanted = str(side_effect_class or "").strip()
    if wanted not in GRANTABLE_SIDE_EFFECT_CLASSES:
        raise CouncilContainmentError(
            f"`{wanted or 'unknown'}` is never grantable to a council seat "
            f"(grantable: {', '.join(sorted(GRANTABLE_SIDE_EFFECT_CLASSES))})"
        )
    clean_run, clean_seat = str(run_id or "").strip(), str(seat_id or "").strip()
    with _LOCK:
        run = _RUNS.get(clean_run)
        if run is None:
            raise CouncilContainmentError(f"no registered council run `{clean_run}`")
        run.grants[clean_seat] = frozenset(run.grants.get(clean_seat, frozenset())) | {wanted}
        return run.grants[clean_seat]


def register_inflight(
    run_id: str, *, seat_id: str, session_id: str, turn_id: str, closer: Any = None
) -> int:
    """Record that a seat turn is executing. Returns the token that releases it."""
    clean = str(run_id or "").strip()
    with _LOCK:
        run = _RUNS.get(clean)
        if run is None:
            return 0
        run.next_token += 1
        token = run.next_token
        run.inflight[token] = InflightSeatTurn(
            seat_id=str(seat_id or ""),
            session_id=str(session_id or ""),
            turn_id=str(turn_id or ""),
            closer=closer,
        )
        return token


def clear_inflight(run_id: str, token: int) -> None:
    """Release one seat turn. Idempotent, so a double release is a no-op."""
    if not token:
        return
    with _LOCK:
        run = _RUNS.get(str(run_id or "").strip())
        if run is not None:
            run.inflight.pop(int(token), None)


def inflight_seat_turns(run_id: str) -> tuple[InflightSeatTurn, ...]:
    with _LOCK:
        run = _RUNS.get(str(run_id or "").strip())
        return tuple(run.inflight.values()) if run is not None else ()


def run_is_fenced(run_id: str) -> bool:
    """Whether this run's seats have had their authority taken away.

    A run id this authority has never seen is NOT fenced: there is no council run behind it,
    so there is nothing to fence and nothing to protect. Every real run is registered by
    `CouncilOrchestrator.__post_init__`, which is the only constructor, so the gap is not
    reachable from a council run — and a caller passing an unknown id gets the behaviour it
    had before this fence existed rather than a mysterious refusal.
    """
    with _LOCK:
        run = _RUNS.get(str(run_id or "").strip())
        return run is not None and not run.live


def _cancel_inflight(turns: Iterable[InflightSeatTurn]) -> None:
    """Stop each dispatched seat turn at the server, then stop waiting on it.

    Order matters. `request_cancel` is what actually ends the work — it sets the Event the
    turn's own router checks, so the model call and the tool loop wind down and the worker
    releases. Closing the stream afterwards only ends the COUNCIL's wait; doing it first
    would leave a cancelled-looking council attached to a turn still burning tokens.

    Both are best-effort by construction: a turn that has already finished is `not_found`,
    and a stream that has already closed raises. Neither is a reason for a stop to fail.
    """
    for turn in turns:
        if turn.session_id and turn.turn_id:
            with contextlib.suppress(Exception):
                from core.live_turns import request_cancel

                request_cancel(turn.session_id, turn.turn_id)
        if turn.closer is not None:
            with contextlib.suppress(Exception):
                turn.closer()


def fence_run(run_id: str, *, reason: str) -> bool:
    """Stop a run's seats from having any authority, immediately and from inside a turn.

    This is the half of "stop" the orchestrator's flag cannot do. `request_stop` sets an
    `Event` that `_dispatch_seat` reads BETWEEN attempts; a seat already inside its turn —
    the incident's seat, mid-investigation, with a 15-minute read timeout — never reaches
    that check. Fencing here lands on the effect door, which is inside the turn.
    """
    clean = str(run_id or "").strip()
    with _LOCK:
        run = _RUNS.get(clean)
        if run is None:
            return False
        run.live = False
        run.fence_reason = str(reason or "the council run is no longer live")
        run.finished_at = time.time()
        # Taken under the lock and cancelled outside it: `request_cancel` and a stream close
        # both run other people's code, and holding this module's lock across them is how a
        # stop deadlocks against the very turn it is trying to end.
        turns = tuple(run.inflight.values())
        run.inflight.clear()
        if clean not in _FINISHED:
            _FINISHED.append(clean)
        stale = _FINISHED[:-_RETAINED_FINISHED_RUNS] if len(_FINISHED) > _RETAINED_FINISHED_RUNS else []
    _cancel_inflight(turns)
    for old in stale:
        release_run(old)
    return True


def release_run(run_id: str) -> None:
    """Forget a run and delete its disposable workspaces. Never raises."""
    clean = str(run_id or "").strip()
    with _LOCK:
        run = _RUNS.pop(clean, None)
        if clean in _FINISHED:
            _FINISHED.remove(clean)
    if run is None:
        return
    with contextlib.suppress(Exception):
        shutil.rmtree(run.jail_root, ignore_errors=True)


def reset_for_tests() -> None:
    """Drop every registration and every disposable workspace."""
    with _LOCK:
        run_ids = list(_RUNS)
    for run_id in run_ids:
        release_run(run_id)
    with _LOCK:
        _RUNS.clear()
        _FINISHED.clear()


# ------------------------------------------------------------------------- lookup


def seat_workspace(run_id: str, seat_id: str) -> Path:
    """The disposable workspace for one seat of one run, created on first use."""
    with _LOCK:
        run = _RUNS.get(str(run_id or "").strip())
        if run is None:
            raise CouncilContainmentError(f"no registered council run `{run_id}`")
        target = run.jail_root / str(seat_id or "seat").strip()
    target.mkdir(parents=True, exist_ok=True)
    return target.resolve()


def binding_for_context(source_context: Mapping[str, Any] | None) -> SeatBinding | None:
    """The seat this context belongs to, or ``None`` for every ordinary caller.

    TWO independent bindings, and EITHER one contains: the session id on the call's own
    context, and the session id frozen onto the turn's inherited effect ledger. Both are
    tried, and the order between them is not a precedence — it is just an order.

    That distinction is the whole point, and getting it wrong is how this leaked. The first
    version read `context_id or inherited_id`, which is not "either one" — it is "the first
    one that is non-empty". An ABSENT session id fell through to the ledger, but a session id
    that was present and belonged to something else did not, and the fence stood aside for a
    stranger. `core/orchestration/executor.py:160-170` produces exactly that shape: it sets
    `session_id` to the ENVELOPE's task id and overwrites `runtime_session_id` with an event
    context's (empty for a step that has none), so a seat's sub-step arrives carrying a real,
    non-empty id that names no seat. Driven at
    `test_a_context_rebuilt_the_way_the_envelope_executor_rebuilds_one_is_still_fenced`,
    which wrote into the operator's checkout until this read both.
    """
    candidates = [_context_session_id(source_context), _inherited_session_id()]
    with _LOCK:
        for session_id in candidates:
            if not session_id:
                continue
            for run in _RUNS.values():
                seat_id = run.sessions.get(session_id)
                if seat_id is None:
                    continue
                return SeatBinding(
                    run_id=run.run_id,
                    seat_id=seat_id,
                    session_id=session_id,
                    granted=frozenset(run.grants.get(seat_id, frozenset())),
                    workspace=run.jail_root / seat_id,
                    live=run.live,
                    fence_reason=run.fence_reason,
                )
    return None


def granted_classes(run_id: str, seat_id: str) -> frozenset[str]:
    """What this seat actually holds — after narrowing, not what the run asked for."""
    with _LOCK:
        run = _RUNS.get(str(run_id or "").strip())
        if run is None:
            return frozenset()
        return frozenset(run.grants.get(str(seat_id or "").strip(), frozenset()))


def effect_ledger(run_id: str) -> tuple[dict[str, Any], ...]:
    """Every effect this run's seats attempted, in order, with its terminal outcome."""
    with _LOCK:
        run = _RUNS.get(str(run_id or "").strip())
        return tuple(dict(row) for row in run.ledger) if run is not None else ()


def record_effect(
    run_id: str,
    seat_id: str,
    *,
    intent: str,
    side_effect_class: str,
    outcome: str,
    detail: str = "",
    target: str = "",
) -> None:
    """Append one terminal row. Every attempted effect leaves exactly one."""
    row = {
        "run_id": str(run_id or "").strip(),
        "seat_id": str(seat_id or ""),
        "intent": str(intent or ""),
        "side_effect_class": str(side_effect_class or ""),
        "outcome": str(outcome or ""),
        "detail": str(detail or ""),
        "target": str(target or ""),
        "at": time.time(),
    }
    with _LOCK:
        run = _RUNS.get(row["run_id"])
        if run is None:
            return
        run.ledger.append(dict(row))
        sink = run.sink
    if sink is None:
        return
    # The in-memory row is already written and is the authority the fence reports from. A
    # durable publisher that fails must not turn a recorded refusal into a crash inside the
    # tool door — the refusal already happened either way.
    with contextlib.suppress(Exception):
        sink(dict(row))


# -------------------------------------------------------------------------- the fence


def _path_arguments(arguments: Mapping[str, Any] | None) -> list[str]:
    values: list[str] = []
    for key in _PATH_ARG_KEYS:
        value = (arguments or {}).get(key)
        if isinstance(value, (list, tuple)):
            values.extend(str(item) for item in list(value)[:24] if str(item).strip())
        elif value not in (None, "") and not isinstance(value, (dict, bool)):
            values.append(str(value))
    return [item for item in values if item.strip()]


def _resolved(raw: str, *, jail: Path) -> Path | None:
    """Where this argument WOULD land: `~` expanded, `..` collapsed, symlinks followed."""
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        candidate = Path(text).expanduser()
        if not candidate.is_absolute():
            candidate = jail / candidate
        # strict=False so a file that does not exist yet still resolves to its real
        # location, with every symlink on the existing prefix followed — a symlinked
        # parent inside the jail cannot smuggle a write out of it.
        return candidate.resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return None


def _hard_linked_target(arguments: Mapping[str, Any] | None, *, jail: Path) -> str:
    """The first path argument naming a file with more than one hard link, or ``""``.

    `Path.resolve()` follows symlinks and cannot see hard links at all: a second name for an
    inode IS the file, with nothing in the path to give it away. `st_nlink` is the only thing
    that tells them apart, and it is an OS fact rather than a guess about a string.

    No tool a seat can reach creates a link of either kind — `os.link` has no typed tool and
    a shell is not grantable — and the jail is a directory this runtime creates empty, so
    this defends a door that is currently shut rather than one standing open. It is here
    because it costs one `stat` and it is the check that stays correct if that ever changes.
    """
    for raw in _path_arguments(arguments):
        resolved = _resolved(raw, jail=jail)
        if resolved is None:
            continue
        try:
            if resolved.is_file() and resolved.stat().st_nlink > 1:
                return raw
        except OSError:
            continue
    return ""


def _inside(root: Path, target: Path) -> bool:
    try:
        target.relative_to(root)
        return True
    except ValueError:
        return False


def _escaping_path(arguments: Mapping[str, Any] | None, *, jail: Path) -> str:
    """The first path argument that lands outside the jail, or ``""`` when none does."""
    for raw in _path_arguments(arguments):
        resolved = _resolved(raw, jail=jail)
        if resolved is None or not _inside(jail, resolved):
            return raw
    return ""


@dataclass(frozen=True)
class FenceVerdict:
    """What the fence decided about one attempted effect."""

    binding: SeatBinding | None
    refusal: str
    side_effect_class: str
    workspace: Path | None
    target: str = ""

    @property
    def contained(self) -> bool:
        return self.binding is not None

    @property
    def mutating(self) -> bool:
        return bool(self.side_effect_class) and self.side_effect_class != READ_ONLY_CLASS


def _declared_side_effect_class(intent: str) -> str:
    try:
        from core.tool_argument_aliases import side_effect_class_for_intent

        return str(side_effect_class_for_intent(intent) or "").strip()
    except Exception:
        return ""


def _bound_arguments(intent: str, arguments: Mapping[str, Any] | None) -> dict[str, Any]:
    """Arguments with known aliases bound, so `file_path` is judged as `path`.

    The fence runs BEFORE `execute_runtime_tool` binds aliases, exactly as
    `mode_permission_policy.decide_tool_call` does and for the same reason: an alias is a
    second name for the same effect, and a fence that reads only the canonical name is a
    fence with one door per synonym.
    """
    payload = dict(arguments or {})
    try:
        from core.tool_argument_aliases import bind_known_argument_aliases, input_schema_for_intent

        return dict(
            bind_known_argument_aliases(payload, input_schema=input_schema_for_intent(intent))
        )
    except Exception:
        return payload


def evaluate(
    intent: str,
    arguments: Mapping[str, Any] | None,
    source_context: Mapping[str, Any] | None,
) -> FenceVerdict:
    """The one decision. ``refusal`` empty means this call may proceed.

    Order matters and is the order of the module docstring's laws: identity, then the
    liveness fence, then the declared class, then the grant, then the jail. A call that
    never reaches the jail check was already refused on authority, and a call that reaches
    it has a grant — so the jail is the LAST thing standing, never the first.
    """
    binding = binding_for_context(source_context)
    if binding is None:
        return FenceVerdict(None, "", "", None)

    effect_class = _declared_side_effect_class(intent)
    bound = _bound_arguments(intent, arguments)

    if effect_class == READ_ONLY_CLASS and binding.live:
        # Investigation, from a live seat. The seat's whole job.
        return FenceVerdict(binding, "", effect_class, None)

    if not binding.live:
        return FenceVerdict(
            binding,
            binding.fence_reason
            or "the council run is no longer live, so its seats hold no authority",
            effect_class,
            None,
        )

    if effect_class not in GRANTABLE_SIDE_EFFECT_CLASSES:
        return FenceVerdict(
            binding,
            (
                f"a council seat may never exercise `{effect_class or 'an undeclared'}` side "
                "effects — that class is outside what this runtime will delegate to a seat "
                "at any breadth"
            ),
            effect_class,
            None,
        )

    if intent not in SEAT_WORKSPACE_MUTATION_TOOLS:
        # The grant is for a class; the invocation is of a TOOL. Most tools declaring
        # `workspace_write` resolve their target somewhere the workspace redirect cannot
        # reach, so the class alone would authorize a door out of the jail.
        return FenceVerdict(
            binding,
            (
                f"`{intent}` is not one of the typed workspace mutators a council seat may be "
                f"granted ({', '.join(sorted(SEAT_WORKSPACE_MUTATION_TOOLS))}) — it does not "
                "resolve its target inside the seat's own workspace"
            ),
            effect_class,
            None,
        )

    if effect_class not in binding.granted:
        return FenceVerdict(
            binding,
            (
                f"council seat {binding.seat_id} was not granted `{effect_class}` for run "
                f"{binding.run_id} — seats are read-only unless the operator grants that "
                "exact capability for that run"
            ),
            effect_class,
            None,
        )

    jail = seat_workspace(binding.run_id, binding.seat_id)
    escaping = _escaping_path(bound, jail=jail)
    if escaping:
        return FenceVerdict(
            binding,
            (
                f"`{escaping}` is outside council seat {binding.seat_id}'s workspace — a "
                "granted seat works only inside its own disposable workspace, never in the "
                "operator's checkout"
            ),
            effect_class,
            jail,
            target=escaping,
        )
    linked = _hard_linked_target(bound, jail=jail)
    if linked:
        return FenceVerdict(
            binding,
            (
                f"`{linked}` has more than one name on this filesystem — a hard link is "
                "indistinguishable from the file it points at, so a write through it is a "
                "write to somewhere this fence cannot see"
            ),
            effect_class,
            jail,
            target=linked,
        )
    return FenceVerdict(binding, "", effect_class, jail)


__all__ = [
    "CONTAINED_STATUS",
    "GRANTABLE_SIDE_EFFECT_CLASSES",
    "NEVER_GRANTABLE_SIDE_EFFECT_CLASSES",
    "OUTCOME_CANCELLED",
    "OUTCOME_EXECUTED",
    "OUTCOME_FAILED",
    "OUTCOME_REFUSED",
    "READ_ONLY_CLASS",
    "SEAT_WORKSPACE_MUTATION_TOOLS",
    "CouncilContainmentError",
    "FenceVerdict",
    "InflightSeatTurn",
    "SeatBinding",
    "binding_for_context",
    "clear_inflight",
    "effect_ledger",
    "evaluate",
    "fence_run",
    "grant",
    "inflight_seat_turns",
    "record_effect",
    "register_inflight",
    "register_run",
    "release_run",
    "reset_for_tests",
    "run_is_fenced",
    "seat_session_id_for",
    "seat_workspace",
]
