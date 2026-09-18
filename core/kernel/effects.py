"""Law 4 — everything replays: one recording interface for every nondeterministic effect.

Why this exists (2026-08-19 audit): a currency fast path answered one of four requests and
reported success; refusal text contradicted the event store it claimed to describe. Both
defects share a root: the runtime's account of what happened was reconstructed from prose,
not from a record of the effects that actually ran. Law 4 closes that gap structurally —
every nondeterministic effect (tool result, clock read, RNG draw, model output) passes
through ``EffectRunner.run``, which in record mode writes an append-only journal and in
replay mode serves results FROM that journal without executing anything. A production turn
then replays bit-for-bit on a dev machine, every field bug becomes a permanent regression
fixture, and any mismatch between "what the code now does" and "what the tape says
happened" is a *named* error (`DivergenceError`), never a silent wrong answer.

Three decisions here are load-bearing:

**The tape is strict order, not a lookup table.** Replay consumes journal entries in the
exact sequence they were recorded. A lookup table keyed by effect id would happily serve a
recorded result to code whose control flow has changed — which is precisely the divergence
Law 4 exists to surface. Order sensitivity makes a reordered, added, or removed effect an
immediate, attributable failure instead of a plausible-looking wrong replay.

**A failed effect must not poison the tape.** In record mode, an exception from the effect
function propagates and NOTHING is appended. The journal is a log of results the world
actually returned; recording a half-effect would leave a tape that replays a world state
that never existed. The turn fails loudly at record time, and the tape stays a faithful,
replayable prefix of everything that succeeded before the failure.

**One validated door into the journal.** ``EffectJournal.record`` is the only append path
— public and validating, used by ``EffectRunner`` and external writers alike. There is no
privileged internal bypass: an entry that skipped validation would be exactly the unmarked,
unverifiable claim Law 2 bans, smuggled in at the storage layer instead of the prose layer.
"""
from __future__ import annotations

import hashlib
import json
import random
import time
from collections.abc import Callable, Iterable, Mapping
from typing import Literal

__all__ = ["DivergenceError", "EffectJournal", "EffectRunner"]

#: The closed set of ways a replay can diverge from its tape. Closed on purpose: a caller
#: that catches DivergenceError can switch on ``reason`` exhaustively, and a new failure
#: mode must be added here — visibly — rather than smuggled in as a free-text string.
DIVERGENCE_REASONS: tuple[str, ...] = (
    "args_hash_mismatch",
    "journal_exhausted",
    "effect_id_mismatch",
)



class DivergenceError(RuntimeError):
    """Replay asked the tape for something the tape does not hold next.

    Carries the machine-readable facts (``effect_id`` is the id the CALLER requested,
    ``reason`` is one of ``DIVERGENCE_REASONS``) so a replay harness can attribute the
    divergence without parsing the message. The message stays human-first: it is what
    lands in a failing regression fixture's output.
    """

    def __init__(self, effect_id: str, reason: str, detail: str = "") -> None:
        if reason not in DIVERGENCE_REASONS:
            raise ValueError(f"unknown divergence reason {reason!r}; expected one of {DIVERGENCE_REASONS}")
        message = f"replay diverged at effect {effect_id!r}: {reason}"
        if detail:
            message = f"{message} ({detail})"
        super().__init__(message)
        self.effect_id = effect_id
        self.reason = reason


class ReplayedEffectFailure(RuntimeError):  # noqa: N818 — a replayed OUTCOME, not a new error; suffix would misname it
    """The recorded run FAILED at this effect; replay re-raises that outcome faithfully.

    Failures are outcomes. The first design ("a failed effect must not poison the tape")
    kept the tape clean by recording nothing — and live use falsified it on 2026-08-19:
    a turn whose arithmetic tool refused mid-run could not be replayed, because replay
    found the NEXT effect on the tape and reported effect_id_mismatch, a divergence that
    misdirects the investigation toward control flow when the truth is simply "this
    effect failed here, exactly as recorded". A tape that cannot reproduce failure can
    only replay the turns that never needed debugging.
    """

    def __init__(self, effect_id: str, error_type: str, message: str) -> None:
        super().__init__(f"replayed failure of {effect_id!r}: {error_type}: {message}")
        self.effect_id = effect_id
        self.error_type = error_type
        self.error_message = message


class EffectOutcomeUnknown(RuntimeError):  # noqa: N818 — an OUTCOME (attempted, unknown), not a new error
    """GOBLIN inv 11 (EXTERNAL EFFECT HONESTY). An effect fn raises this to declare that it
    was ATTEMPTED but its outcome is genuinely unknowable — e.g. an external POST was
    dispatched and no acknowledgement came back. This is neither a success (we must not
    claim the effect happened) nor a failure (we must not claim it did not); it is
    reconciliation-required. Recording it keeps the tape honest instead of forcing an
    external ambiguity into a false result/error binary."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"effect outcome unknown: {reason}: {detail}")
        self.reason = reason
        self.detail = detail


class ReplayedEffectUnknown(RuntimeError):  # noqa: N818 — a replayed OUTCOME, not a new error
    """Replay re-raises a recorded EFFECT_UNKNOWN faithfully — replay must never resolve an
    ambiguity the live run could not, so it reproduces the unknown at the same position."""

    def __init__(self, effect_id: str, reason: str, detail: str) -> None:
        super().__init__(f"replayed unknown outcome of {effect_id!r}: {reason}: {detail}")
        self.effect_id = effect_id
        self.reason = reason
        self.detail = detail


def _reject_non_str_dict_keys(value: object) -> None:
    """Refuse dicts keyed by anything but str, anywhere in *value*.

    ``json.dumps`` silently coerces int/float/bool/None keys to strings, so
    ``f({1: "a"})`` and ``f({"1": "a"})`` would hash identically and replay would serve
    one call's recorded result for the other with no DivergenceError — measured in the
    adversarial pass (finding D1). Two calls that differ must never share a hash; a key
    the canonicalizer would have to rewrite is refused instead, matching allow_nan=False:
    fail closed, never normalize a difference away.

    Note what is NOT policed: ``(1, 2)`` vs ``[1, 2]`` still canonicalize identically,
    because a JSON-typed tape has one sequence type. That is a documented property of
    the tape's type system, not a silent coercion of a key's identity.
    """
    seen: set[int] = set()
    stack: list[object] = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            if id(current) in seen:
                continue
            seen.add(id(current))
            for key in current:
                if not isinstance(key, str):
                    raise ValueError(
                        f"dict keys in effect args must be str, got {type(key).__name__}: {key!r}"
                    )
            stack.extend(current.values())
        elif isinstance(current, (list, tuple)):
            if id(current) in seen:
                continue
            seen.add(id(current))
            stack.extend(current)


def _canonical_json(value: object) -> str:
    """Canonicalize to JSON: sorted keys, no whitespace, no NaN/Infinity.

    ``allow_nan=False`` because NaN/Infinity are not JSON — a tape other tooling cannot
    parse is not a tape — and because NaN would also defeat the equality that replay
    verification rests on. ``sort_keys=True`` is what makes ``f(a=1, b=2)`` and
    ``f(b=2, a=1)`` the same effect: Python kwargs order is an artifact of the call site,
    not part of the effect's identity. Non-str dict keys are refused before dumping —
    see :func:`_reject_non_str_dict_keys` for the collision this prevents.
    """
    _reject_non_str_dict_keys(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _serializable_copy(label: str, value: object) -> object:
    """Return a JSON round-trip copy of *value*, or raise ValueError naming *label*.

    The round trip is the validation AND the defensive copy in one step: what comes back
    holds no aliases into caller-owned objects, so later mutation by the caller cannot
    rewrite the tape.
    """
    try:
        return json.loads(_canonical_json(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} is not JSON-serializable ({type(value).__name__}): {exc}") from None


def _args_hash(effect_id: str, args: tuple[object, ...], kwargs: dict[str, object]) -> str:
    """SHA-256 over the canonical JSON of args+kwargs; names the offending arg on failure.

    A bare "not serializable" from three layers down is undebuggable in a live turn, so on
    failure each argument is re-tried individually to report WHICH one broke — the extra
    passes cost nothing on the happy path.
    """
    try:
        canonical = _canonical_json({"args": list(args), "kwargs": kwargs})
    except (TypeError, ValueError):
        for index, arg in enumerate(args):
            try:
                _canonical_json(arg)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"effect {effect_id!r}: positional argument {index} is not JSON-serializable"
                    f" ({type(arg).__name__}): {exc}"
                ) from None
        for name in sorted(kwargs):
            try:
                _canonical_json(kwargs[name])
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"effect {effect_id!r}: keyword argument {name!r} is not JSON-serializable"
                    f" ({type(kwargs[name]).__name__}): {exc}"
                ) from None
        raise  # container itself unserializable with every element fine: not reachable via run()
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class EffectJournal:
    """Append-only tape of effect results; each entry is a plain dict, JSON all the way.

    Entries are plain dicts (keys ``effect_id``, ``args_hash``, ``result``) rather than a
    dataclass because the journal's whole value is that it round-trips through JSON with
    zero interpretation — what gets persisted, diffed, and attached to a bug report is
    exactly what ``entries()`` shows.

    Append-only means the PAST is immutable, so ``entries()`` returns fresh copies: handing
    out the live dicts would let any holder rewrite history through aliasing, which is a
    silent-tape-corruption bug this class exists to make impossible.
    """

    def __init__(self, entries: Iterable[Mapping[str, object]] = ()) -> None:
        self._entries: list[dict[str, object]] = []
        for entry in entries:
            self.record(entry)

    def __len__(self) -> int:
        return len(self._entries)

    def record(self, entry: Mapping[str, object]) -> None:
        """Validate and append one entry — the single door into the tape (see module docstring).

        Validation happens BEFORE anything is appended, so a rejected entry leaves the
        journal exactly as it was.
        """
        if not isinstance(entry, Mapping):
            raise ValueError(f"journal entry must be a mapping, got {type(entry).__name__}")
        keys = set(entry)
        if keys not in ({"effect_id", "args_hash", "result"}, {"effect_id", "args_hash", "error"},
                        {"effect_id", "args_hash", "unknown"}):
            raise ValueError(
                "journal entry keys must be exactly {effect_id, args_hash, result} for a"
                " success, {effect_id, args_hash, error} for a failure, or"
                f" {{effect_id, args_hash, unknown}} for an ambiguous outcome; got {sorted(keys)}"
            )
        effect_id = entry["effect_id"]
        args_hash = entry["args_hash"]
        if not isinstance(effect_id, str) or not effect_id:
            raise ValueError(f"journal entry effect_id must be a non-empty str, got {effect_id!r}")
        if not isinstance(args_hash, str) or not args_hash:
            raise ValueError(f"journal entry args_hash must be a non-empty str, got {args_hash!r}")
        if "error" in entry:
            error = entry["error"]
            if (
                not isinstance(error, Mapping)
                or set(error) != {"type", "message"}
                or not isinstance(error.get("type"), str)
                or not error["type"]
                or not isinstance(error.get("message"), str)
            ):
                raise ValueError(
                    f"journal entry error must be {{'type': non-empty str, 'message': str}}, got {error!r}"
                )
            self._entries.append(
                {"effect_id": effect_id, "args_hash": args_hash,
                 "error": {"type": error["type"], "message": error["message"]}}
            )
            return
        if "unknown" in entry:
            unknown = entry["unknown"]
            if (
                not isinstance(unknown, Mapping)
                or set(unknown) != {"reason", "detail"}
                or not isinstance(unknown.get("reason"), str)
                or not unknown["reason"]
                or not isinstance(unknown.get("detail"), str)
            ):
                raise ValueError(
                    f"journal entry unknown must be {{'reason': non-empty str, 'detail': str}}, got {unknown!r}"
                )
            self._entries.append(
                {"effect_id": effect_id, "args_hash": args_hash,
                 "unknown": {"reason": unknown["reason"], "detail": unknown["detail"]}}
            )
            return
        result = _serializable_copy(f"effect {effect_id!r}: result", entry["result"])
        self._entries.append({"effect_id": effect_id, "args_hash": args_hash, "result": result})

    def entries(self) -> tuple[dict[str, object], ...]:
        """Tuple view of the tape, each entry a fresh copy (aliasing = tape corruption)."""
        out: list[dict[str, object]] = []
        for e in self._entries:
            if "error" in e:
                err = e["error"]
                out.append({"effect_id": e["effect_id"], "args_hash": e["args_hash"],
                            "error": {"type": err["type"], "message": err["message"]}})
            elif "unknown" in e:
                unk = e["unknown"]
                out.append({"effect_id": e["effect_id"], "args_hash": e["args_hash"],
                            "unknown": {"reason": unk["reason"], "detail": unk["detail"]}})
            else:
                out.append({"effect_id": e["effect_id"], "args_hash": e["args_hash"],
                            "result": _serializable_copy("result", e["result"])})
        return tuple(out)

    def to_json(self) -> str:
        return json.dumps(self._entries, sort_keys=True, allow_nan=False)

    @classmethod
    def from_json(cls, text: str) -> EffectJournal:
        """Rebuild a journal from ``to_json`` output; anything malformed is a ValueError.

        Strict on purpose: every entry re-enters through ``record``, so a hand-edited or
        truncated tape is rejected at load time, not discovered mid-replay as a confusing
        divergence.
        """
        try:
            raw = json.loads(text)
        except ValueError as exc:
            raise ValueError(f"journal JSON does not parse: {exc}") from None
        if not isinstance(raw, list):
            raise ValueError(f"journal JSON must be a list of entries, got {type(raw).__name__}")
        journal = cls()
        for index, entry in enumerate(raw):
            try:
                journal.record(entry)
            except ValueError as exc:
                raise ValueError(f"journal entry {index}: {exc}") from None
        return journal


class EffectRunner:
    """Runs effects (record mode) or serves them from a tape (replay mode) — never both.

    Replay mode NEVER calls the effect function. That is the whole point: replay must be
    able to run on a machine with no network, no clock agreement, and no side effects, and
    a replay that "helpfully" re-executed a missing effect would be a live run wearing a
    replay's name — the exact unverifiable-claim pattern the kernel laws ban.
    """

    def __init__(self, mode: Literal["record", "replay"] = "record", journal: EffectJournal | None = None) -> None:
        if mode not in ("record", "replay"):
            raise ValueError(f"mode must be 'record' or 'replay', got {mode!r}")
        if mode == "replay" and journal is None:
            raise ValueError("replay mode requires a journal (there is no tape to replay from)")
        self._mode: Literal["record", "replay"] = mode
        self._journal = journal if journal is not None else EffectJournal()
        self._cursor = 0

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def journal(self) -> EffectJournal:
        return self._journal

    def run(self, effect_id: str, fn: Callable[..., object], /, *args: object, **kwargs: object) -> object:
        """Execute (record) or serve from tape (replay) the effect named *effect_id*.

        The args hash is computed BEFORE the effect runs, so an effect whose arguments
        cannot be recorded is refused up front rather than executed and then lost — an
        effect that ran but left no tape entry is unattributable by construction.

        Replay checks in a fixed priority — exhaustion, then effect id, then args hash —
        because each earlier check makes the later comparison meaningless: comparing arg
        hashes across two DIFFERENT effects would report a mismatch that misdirects the
        investigation toward arguments when the divergence is control flow.
        """
        if not isinstance(effect_id, str) or not effect_id:
            raise ValueError(f"effect_id must be a non-empty str, got {effect_id!r}")
        args_hash = _args_hash(effect_id, args, dict(kwargs))
        if self._mode == "replay":
            if self._cursor >= len(self._journal):
                raise DivergenceError(
                    effect_id,
                    "journal_exhausted",
                    f"requested effect #{self._cursor + 1} but the tape holds {len(self._journal)}",
                )
            entry = self._journal.entries()[self._cursor]
            if entry["effect_id"] != effect_id:
                raise DivergenceError(
                    effect_id,
                    "effect_id_mismatch",
                    f"tape position {self._cursor} holds {entry['effect_id']!r}",
                )
            if entry["args_hash"] != args_hash:
                raise DivergenceError(
                    effect_id,
                    "args_hash_mismatch",
                    f"tape position {self._cursor}: recorded {entry['args_hash'][:12]}…, got {args_hash[:12]}…",
                )
            self._cursor += 1
            if "error" in entry:
                error = entry["error"]
                raise ReplayedEffectFailure(effect_id, str(error["type"]), str(error["message"]))
            if "unknown" in entry:
                unknown = entry["unknown"]
                raise ReplayedEffectUnknown(effect_id, str(unknown["reason"]), str(unknown["detail"]))
            return entry["result"]  # already a fresh copy: entries() never aliases the tape
        # Record mode. A raising effect is an OUTCOME: its type and message go on the tape
        # so replay reproduces the failure at the same position, then the original
        # exception propagates unchanged. (Only outcomes whose args could not even be
        # hashed stay off the tape — that refusal happens above, before fn runs.)
        try:
            result = fn(*args, **kwargs)
        except EffectOutcomeUnknown as exc:
            # inv 11: an ambiguous external outcome is recorded as UNKNOWN (not forced into
            # a false result/error), then the outcome propagates unchanged for the caller to
            # reconcile. Checked before the generic handler so it is not miscategorized.
            self._journal.record(
                {"effect_id": effect_id, "args_hash": args_hash,
                 "unknown": {"reason": exc.reason, "detail": exc.detail}}
            )
            raise
        except Exception as exc:
            self._journal.record(
                {"effect_id": effect_id, "args_hash": args_hash,
                 "error": {"type": type(exc).__name__, "message": str(exc)}}
            )
            raise
        self._journal.record({"effect_id": effect_id, "args_hash": args_hash, "result": result})
        return result

    def clock(self) -> float:
        """Current time through the tape, so replayed runs see the recorded instant."""
        return self.run("kernel.clock", time.time)  # type: ignore[return-value]

    def rand(self) -> float:
        """Uniform [0, 1) through the tape, so replayed runs see the recorded draw."""
        return self.run("kernel.rand", random.random)  # type: ignore[return-value]
