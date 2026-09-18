"""What each tool actually ran against, this turn, in memory.

The grounding data a binder needs already exists — `RuntimeExecutionResult.details["observation"]`
carries the resolved path and the item names — and then it is thrown away. Nothing persists it for
read-only tools, and `finalize_runtime_checkpoint` clears `executed_steps` at the moment the answer
is returned, so the transient copy dies exactly when the check would run.

**Why this is not the receipt table.** `core/tool_intent_executor.py:320-324` loads a stored receipt
by key and returns it *instead of running the tool*. A receipt is a replay cache, and its key is the
idempotency key. Widening that gate to read-only tools would mean the second "what's in this folder"
of a session answers from a cached row while the folder has changed underneath it — turning a
grounding fix into a staleness bug.

**Why memory and not SQLite.** The binder runs in the same process and the same turn as the tools it
checks. Persisting costs ~1.3-1.5 ms and ~7 KB per call behind a module-level RLock that serializes
the whole continuity store, on a hot path, to store something read microseconds later. Records are
bounded per session and dropped when the session ends.

Nothing here ever changes what a tool returns. It is write-only observation.
"""
from __future__ import annotations

import threading
from collections import OrderedDict, deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

# Per session. High enough to cover a long multi-step turn, low enough that a runaway loop cannot
# grow memory without bound.
_MAX_RECORDS_PER_SESSION = 64
# Sessions retained. An LRU so a long-lived process does not accumulate dead sessions.
_MAX_SESSIONS = 32
# A single tool can return thousands of filenames; the binder only needs them for membership.
_MAX_ITEMS_PER_RECORD = 2000


@dataclass(frozen=True)
class ExecutionRecord:
    """One tool call, reduced to what an answer can be checked against.

    ``turn_id`` / ``generation`` / ``sequence`` are the identity a truth binder needs and the
    original shape lacked. Records are kept per SESSION and nothing clears them between turns, so
    without a turn stamp "did you call GitHub on this turn?" was answerable only as "did you call
    GitHub at any point in this session" — which binds last turn's evidence to this turn's claim.
    ``generation`` separates retry attempts within one turn (a failed first attempt and the
    successful re-run are different facts about the same turn), and ``sequence`` is a monotonic
    per-session counter so chronology survives even when neither of the other two is known.

    An empty ``turn_id`` means UNATTRIBUTED, never "the current turn": a record whose turn nobody
    stamped cannot confirm a claim about this turn, and its presence is why a negative about this
    turn stops being provable.
    """

    intent: str
    arguments: Mapping[str, Any] = field(default_factory=dict)
    resolved_target: str = ""
    items: tuple[str, ...] = ()
    citations: tuple[str, ...] = ()
    ok: bool = True
    status: str = ""
    asserts_action: bool = False
    turn_id: str = ""
    generation: int = 0
    sequence: int = 0

    @property
    def has_target(self) -> bool:
        return bool(self.resolved_target)


_lock = threading.RLock()
_sessions: OrderedDict[str, deque[ExecutionRecord]] = OrderedDict()
_sequences: dict[str, int] = {}


def _session_key(session_id: str) -> str:
    return str(session_id or "").strip() or "default"


def _bucket(session_id: str) -> deque[ExecutionRecord]:
    key = _session_key(session_id)
    bucket = _sessions.get(key)
    if bucket is None:
        bucket = deque(maxlen=_MAX_RECORDS_PER_SESSION)
        _sessions[key] = bucket
        while len(_sessions) > _MAX_SESSIONS:
            evicted, _ = _sessions.popitem(last=False)
            _sequences.pop(evicted, None)
    else:
        _sessions.move_to_end(key)
    return bucket


def record(
    *,
    session_id: str,
    intent: str,
    arguments: Mapping[str, Any] | None = None,
    observation: Mapping[str, Any] | None = None,
    details: Mapping[str, Any] | None = None,
    claim: Any = None,
    ok: bool = True,
    status: str = "",
    source_context: Mapping[str, Any] | None = None,
    turn_id: str = "",
    generation: int = 0,
) -> ExecutionRecord:
    """Reduce one tool execution to a record and keep it for this session's turn.

    ``claim`` is the tool's own ``ToolClaim``, so the keys to read are declared by the tool rather
    than guessed here. A tool that declares nothing still gets a record — the intent and the
    arguments alone are worth having — it just cannot be bound against.

    ``source_context`` is the turn's context; the turn id read from it is the SAME one
    `core.runtime_task_events.emit_runtime_event` stamps on every Activity event
    (``cancel_turn_id``), so a record and the durable Activity row for the same call agree on which
    turn they belong to. Explicit ``turn_id``/``generation`` win over the context for callers that
    already resolved them.
    """

    observation = dict(observation or {})
    details = dict(details or {})
    arguments = dict(arguments or {})
    context = dict(source_context or {})

    resolved = _resolved_target(observation, details, arguments, claim)
    items = _items(observation, details, claim)
    citations = _citations(observation, details, claim)
    bound_turn = str(turn_id or context.get("cancel_turn_id") or "").strip()
    bound_generation = int(generation or _generation_from(context, session_id))

    with _lock:
        key = _session_key(session_id)
        sequence = _sequences.get(key, 0) + 1
        _sequences[key] = sequence
        entry = ExecutionRecord(
            intent=str(intent or "").strip(),
            arguments=arguments,
            resolved_target=resolved,
            items=items,
            citations=citations,
            ok=bool(ok),
            status=str(status or ""),
            asserts_action=bool(getattr(claim, "asserts_action", False)),
            turn_id=bound_turn,
            generation=bound_generation,
            sequence=sequence,
        )
        _bucket(session_id).append(entry)
    return entry


def _generation_from(context: Mapping[str, Any], session_id: str) -> int:
    """Which retry generation this call belongs to, or 0 when the runtime does not know.

    The context wins when it carries the value. Otherwise the live attempt row is consulted:
    `core.agent_runtime.attempt_retry` bumps ``execution_generation`` on every re-run, so a first
    attempt that tried GitHub and a second that stayed local are distinguishable after the fact —
    which is what lets a repair say "an earlier attempt" instead of flattening the two.
    """

    raw = context.get("execution_generation")
    if isinstance(raw, int) and raw > 0:
        return raw
    if isinstance(raw, str) and raw.strip().isdigit():
        return int(raw.strip())
    clean_session = str(session_id or "").strip()
    if not clean_session:
        return 0
    try:
        from core.runtime_continuity import latest_runtime_attempt

        attempt = latest_runtime_attempt(clean_session) or {}
        return int(attempt.get("execution_generation") or 0)
    except Exception:
        # Observation must never break a turn, and an unknown generation is a legitimate state:
        # it degrades a repair's wording, it does not invent a generation that never ran.
        return 0


def _resolved_target(
    observation: Mapping[str, Any],
    details: Mapping[str, Any],
    arguments: Mapping[str, Any],
    claim: Any,
) -> str:
    """What the tool actually acted on, preferring the tool's own report over the user's spelling.

    The distinction is the point: a user types `~/Desktop/x` and the tool resolves it to an absolute
    path. Binding an answer against the raw argument would accept a claim about a folder that was
    never opened, which is the failure this exists to catch.
    """

    key = str(getattr(claim, "resolved_target_key", "") or "")
    for source in (details, observation):
        # `resolved_target` is the explicit, tool-agnostic channel; a tool that sets it wins.
        value = source.get("resolved_target")
        if isinstance(value, str) and value.strip():
            return value.strip()
    if key:
        for source in (observation, details):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    argument_key = str(getattr(claim, "target_argument", "") or "")
    if argument_key:
        value = arguments.get(argument_key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _items(observation: Mapping[str, Any], details: Mapping[str, Any], claim: Any) -> tuple[str, ...]:
    """The names a tool returned, so an answer naming something else can be caught.

    A missing key means the tool returned nothing, not that nothing is known: the observation
    builder drops any value equal to None/""/[]/{}, so an empty listing has no `entries` key at all.
    Callers distinguish the two with `has_target`, never by the emptiness of this tuple.
    """

    key = str(getattr(claim, "result_items_key", "") or "")
    if not key:
        return ()
    raw: Any = None
    for source in (observation, details):
        if key in source:
            raw = source[key]
            break
    return _names_from(raw)


def _names_from(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, (list, tuple)):
        return ()
    names: list[str] = []
    for item in raw[:_MAX_ITEMS_PER_RECORD]:
        if isinstance(item, str):
            text = item.strip()
        elif isinstance(item, Mapping):
            # Measured shapes: machine.list_directory entries are {name, path, type};
            # find_folder matches are bare path strings; web hits carry url/title.
            text = str(
                item.get("name") or item.get("path") or item.get("url") or item.get("key") or ""
            ).strip()
        else:
            text = ""
        if text:
            names.append(text)
    return tuple(names)


def _citations(observation: Mapping[str, Any], details: Mapping[str, Any], claim: Any) -> tuple[str, ...]:
    values: list[str] = []
    for key in tuple(getattr(claim, "cites", ()) or ()):
        name = str(key).split("[", 1)[0].strip()
        if not name:
            continue
        for source in (observation, details):
            value = source.get(name)
            if isinstance(value, str) and value.strip():
                values.append(value.strip())
                break
    return tuple(values)


def records_for(session_id: str) -> tuple[ExecutionRecord, ...]:
    with _lock:
        bucket = _sessions.get(_session_key(session_id))
        return tuple(bucket) if bucket else ()


def records_for_turn(session_id: str, turn_id: str) -> tuple[ExecutionRecord, ...]:
    """Only the records this turn is known to own.

    A record with no turn stamp is excluded rather than assumed current. That direction is the
    whole point: including it would let a GitHub fetch from an earlier turn answer a question
    explicitly about this one. The caller learns that unattributed records exist from
    `unattributed_count`, and treats their presence as a reason it cannot prove a negative.
    """

    wanted = str(turn_id or "").strip()
    if not wanted:
        return ()
    return tuple(entry for entry in records_for(session_id) if entry.turn_id == wanted)


def unattributed_count(session_id: str) -> int:
    """How many of this session's records carry no turn stamp."""

    return sum(1 for entry in records_for(session_id) if not entry.turn_id)


def clear(session_id: str | None = None) -> None:
    with _lock:
        if session_id is None:
            _sessions.clear()
            _sequences.clear()
            return
        key = _session_key(session_id)
        _sessions.pop(key, None)
        _sequences.pop(key, None)


def executed_targets(session_id: str) -> tuple[str, ...]:
    return tuple(r.resolved_target for r in records_for(session_id) if r.has_target)


def executed_items(session_id: str) -> tuple[str, ...]:
    names: list[str] = []
    for entry in records_for(session_id):
        names.extend(entry.items)
    return tuple(names)


def has_action_receipt(session_id: str, *, target: str = "") -> bool:
    """Whether a tool that can back a mutation claim actually ran — optionally on ``target``.

    The existing check accepts any receipt from any tool on any target, so an answer claiming a file
    was written is backed by an unrelated read. Matching the target is what makes it mean something.
    """

    wanted = str(target or "").strip()
    for entry in records_for(session_id):
        if not (entry.asserts_action and entry.ok):
            continue
        if not wanted or entry.resolved_target == wanted:
            return True
    return False


def _target_matches(claimed: str, actual: str) -> bool:
    """Whether an executed target satisfies a claim about ``claimed``.

    Compared on trailing path segments so the spelling a user typed
    (``app-landing/index.html``) matches what a tool resolved it to
    (``/Users/me/Desktop/VOOL WEBSITE/app-landing/index.html``). A bare filename matches the file
    it names anywhere, which is deliberate: the failure being caught is a file never opened at all,
    not a file opened via an unexpected route.
    """

    left = [p for p in str(claimed or "").strip().strip("`'\"").replace("\\", "/").split("/") if p]
    right = [p for p in str(actual or "").strip().replace("\\", "/").split("/") if p]
    if not left or not right:
        return False
    depth = min(len(left), len(right))
    return right[-depth:] == left[-depth:]


def verify_claim(session_id: str, *, target: str = "") -> dict[str, Any]:
    """Did this session actually do work, and specifically on ``target``?

    The check the runtime was missing. `_current_session_has_executed_receipt` asks whether
    *anything* ran; a reply claiming "I audited app-landing/index.html" passes that as long as some
    unrelated read happened — which is exactly what occurred: the audit read `README.md` and
    `site/assets/site.js`, never opened the named file, and claimed the work anyway.

    ``supported`` answers the referential question instead: is there a record whose resolved target
    IS the thing the sentence claims. Absent a named target it degrades to the existential answer,
    because "I had a look" with no object is not a checkable claim.
    """

    entries = [entry for entry in records_for(session_id) if entry.ok]
    executed = [entry.intent for entry in entries]
    wanted = str(target or "").strip()
    if not wanted:
        return {
            "any_execution": bool(entries),
            "supported": bool(entries),
            "target": "",
            "matched_intent": "",
            "executed": executed,
        }

    for entry in entries:
        if entry.has_target and _target_matches(wanted, entry.resolved_target):
            return {
                "any_execution": True,
                "supported": True,
                "target": wanted,
                "matched_intent": entry.intent,
                "executed": executed,
            }
        # A listing that returned the file counts as having seen it, but not as having read it.
        if any(_target_matches(wanted, name) for name in entry.items):
            return {
                "any_execution": True,
                "supported": False,
                "target": wanted,
                "matched_intent": "",
                "listed_only": entry.intent,
                "executed": executed,
            }
    return {
        "any_execution": bool(entries),
        "supported": False,
        "target": wanted,
        "matched_intent": "",
        "executed": executed,
    }


def bind_summary(session_id: str) -> dict[str, Any]:
    """Compact view for logs and for a judge prompt."""

    entries = records_for(session_id)
    return {
        "call_count": len(entries),
        "intents": [e.intent for e in entries],
        "targets": [e.resolved_target for e in entries if e.has_target],
        "item_count": sum(len(e.items) for e in entries),
        "any_action": any(e.asserts_action and e.ok for e in entries),
    }


__all__ = [
    "ExecutionRecord",
    "bind_summary",
    "clear",
    "executed_items",
    "executed_targets",
    "has_action_receipt",
    "record",
    "records_for",
    "records_for_turn",
    "unattributed_count",
    "verify_claim",
]
