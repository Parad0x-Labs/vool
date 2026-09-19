"""Law 3: forks hold capabilities — least-authority execution with taint tracking.

Why this module exists: the 2026-08-19 audit found a research turn with no blast-radius
bound — any sub-task could reach any tool, so a poisoned webpage read by a research fork
could in principle reach the disk. This module makes that structurally impossible rather
than behaviourally unlikely:

* every fork carries an EXPLICIT capability set, and a tool call outside it is refused
  with a receipt row naming the fork, the tool and the reason — the denial is auditable,
  never silent (the audited refusal-vs-event-store contradiction started as a silent gate);
* a child fork can never hold a capability its parent lacks, so no chain of spawns widens
  the blast radius;
* text that arrived from an untrusted origin (a webpage, a file) is TAINTED; taint
  survives explicit combination and container nesting, and a tainted value cannot enter
  tool arguments without an explicit countersign.

Two decisions worth defending:

**No prefix implication.** ``fs`` does not grant ``fs.read``. Prefix and wildcard grants
are how capability systems rot: one broad token handed out for convenience quietly
becomes root. :meth:`CapabilitySet.allows` is exact-set membership and nothing else; a
fork that needs two tokens must be granted two tokens.

**``str()`` un-taints by design; ``combine`` is the sanctioned join.** Python offers no
way to make every f-string or ``+`` preserve a wrapper, and pretending otherwise would be
a false guarantee — exactly the kind of scripted safety Section 2 bans. Instead the rule
is enforced at the kernel seam: :func:`check_tool_call` walks argument containers
recursively, and code that concatenates tainted text MUST use
:meth:`TaintedValue.combine`, which keeps the taint and every contributing origin.
Laundering through bare ``str()`` is thereby an explicit act visible in review, not an
accident the runtime hides.

Direction validated by CaMeL (arXiv 2503.18813): capability-scoped execution with taint
flow is a proven prompt-injection defense no shipped agent runtime implements. This is
VOOL's stdlib-only kernel form of it.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

# Lowercase dotted identifiers only: 'net.fetch', 'fs.read', bare 'fs'. fullmatch (not
# $-anchored search) so a trailing newline cannot smuggle a token past validation.
_TOKEN_RE = re.compile(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*")

_SPAWN_TOOL = "kernel.spawn_child"


def _validate_token(token: object) -> str:
    """Return the token if well-formed, else raise loudly.

    Malformed tokens raise instead of failing membership quietly: a typo'd grant that
    silently never matches is indistinguishable from a policy decision, and that
    ambiguity is how a denial gets misreported (the audited refusal-text contradiction).
    """
    if not isinstance(token, str):
        raise TypeError(f"capability token must be str, got {type(token).__name__}")
    if not _TOKEN_RE.fullmatch(token):
        raise ValueError(
            f"invalid capability token {token!r}: expected lowercase dotted identifiers like 'fs.read'"
        )
    return token


def _receipt(fork_id: str, tool: str, required: str, decision: str, reason: str) -> dict[str, str]:
    """One receipt-row shape for every grant and denial, so the ledger is uniform."""
    return {
        "fork_id": fork_id,
        "tool": tool,
        "required": required,
        "decision": decision,
        "reason": reason,
    }


class CapabilityDenied(RuntimeError):
    """A capability check refused — carries the receipt row so the denial is auditable.

    The receipt travels ON the exception: the caller that catches it can persist the row
    without reconstructing it, so the event store and the refusal text cannot diverge.
    """

    def __init__(self, fork_id: str, token: str, reason: str, receipt: dict[str, str]) -> None:
        super().__init__(f"fork {fork_id!r} denied {token!r}: {reason}")
        self.fork_id = fork_id
        self.token = token
        self.reason = reason
        self.receipt = receipt


@dataclass(frozen=True)
class CapabilitySet:
    """An immutable, exact-match set of capability tokens.

    Frozen because a capability set that mutates after a fork starts is not a bound —
    the whole point is that the blast radius is fixed at spawn time.
    """

    tokens: frozenset[str]

    def __init__(self, tokens: Iterable[str] = ()) -> None:
        # A bare string is iterable and every single letter is a valid token ('f', 's'),
        # so CapabilitySet("fs") would silently grant two nonsense capabilities. Refuse.
        if isinstance(tokens, str | bytes):
            raise TypeError("pass an iterable of tokens, not a bare string")
        object.__setattr__(self, "tokens", frozenset(_validate_token(t) for t in tokens))

    def allows(self, token: str) -> bool:
        """Exact membership only — 'fs' never implies 'fs.read', nor the reverse."""
        return _validate_token(token) in self.tokens

    def subset_of(self, other: CapabilitySet) -> bool:
        if not isinstance(other, CapabilitySet):
            raise TypeError(f"subset_of expects CapabilitySet, got {type(other).__name__}")
        return self.tokens <= other.tokens


@dataclass(frozen=True)
class TaintedValue:
    """A string that remembers it came from an untrusted origin.

    ``str(tv)`` returns the bare payload (rendering must work), which is also the one
    documented laundering point — see the module docstring for why that trade is taken
    and why :meth:`combine` is the required join for tainted text.
    """

    payload: str
    origin: str

    def __post_init__(self) -> None:
        if not isinstance(self.payload, str):
            raise TypeError(f"payload must be str, got {type(self.payload).__name__}")
        if not isinstance(self.origin, str) or not self.origin:
            # An origin-less taint cannot be audited; requiring it keeps every tainted
            # value traceable back to the fetch that introduced it.
            raise ValueError("origin must be a non-empty str, e.g. 'web:https://...'")

    def __str__(self) -> str:
        return self.payload

    @property
    def is_tainted(self) -> bool:
        return True

    @staticmethod
    def combine(*parts: str | TaintedValue) -> TaintedValue | str:
        """Join parts, keeping taint (and every contributing origin) if any part carries it.

        Returns a plain str when nothing was tainted: clean text joined with clean text
        is clean, and wrapping it would train callers to ignore the wrapper.
        """
        pieces: list[str] = []
        origins: list[str] = []
        for part in parts:
            if isinstance(part, TaintedValue):
                pieces.append(part.payload)
                if part.origin not in origins:
                    origins.append(part.origin)
            elif isinstance(part, str):
                pieces.append(part)
            else:
                raise TypeError(f"combine accepts str or TaintedValue, got {type(part).__name__}")
        joined = "".join(pieces)
        if not origins:
            return joined
        return TaintedValue(joined, "+".join(origins))


def is_tainted(value: object) -> bool:
    """True if the value is, or anywhere contains, a :class:`TaintedValue`.

    Iterative walk with an id-based seen set: tool arguments are attacker-shaped data,
    so a self-referential container or a 10,000-level nest must yield an answer, not a
    RecursionError that skips the check. Dict KEYS are walked too — a tainted key is
    taint entering the call exactly as a tainted value is.
    """
    seen: set[int] = set()
    stack: list[object] = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, TaintedValue):
            return True
        if isinstance(current, dict):
            if id(current) in seen:
                continue
            seen.add(id(current))
            stack.extend(current.keys())
            stack.extend(current.values())
        elif isinstance(current, list | tuple | set | frozenset):
            if id(current) in seen:
                continue
            seen.add(id(current))
            stack.extend(current)
    return False


_WALKABLE_SCALARS = (str, bytes, int, float, bool, type(None))


def assert_walkable_args(args: dict[str, object]) -> None:
    """Refuse tool args holding values the taint walk cannot see inside.

    ``is_tainted`` walks the container types tool arguments are made of. An arbitrary
    object with a ``TaintedValue`` in an attribute walks as opaque and clean — measured
    in the adversarial pass (finding D3): ``{"path": Wrapper(tainted)}`` passed
    ``check_tool_call`` as ``allowed``. Laundering taint through a wrapper object must
    not be cheaper than a countersign, so an argument value outside the JSON-shaped
    vocabulary (scalars, dict/list/tuple/set/frozenset, TaintedValue) is refused
    outright — fail closed, the same posture as the capability check itself.
    """
    seen: set[int] = set()
    stack: list[object] = [args]
    while stack:
        current = stack.pop()
        if isinstance(current, (TaintedValue, *_WALKABLE_SCALARS)):
            continue
        if isinstance(current, dict):
            if id(current) in seen:
                continue
            seen.add(id(current))
            stack.extend(current.keys())
            stack.extend(current.values())
        elif isinstance(current, list | tuple | set | frozenset):
            if id(current) in seen:
                continue
            seen.add(id(current))
            stack.extend(current)
        else:
            raise TypeError(
                "tool args must be JSON-shaped (scalars, containers, TaintedValue); "
                f"got {type(current).__name__} — an opaque object could smuggle taint"
            )


@dataclass(frozen=True)
class ForkContext:
    """A fork's identity and its fixed capability bound.

    Frozen: a fork's authority is decided when it is spawned and never after. The only
    way to different capabilities is a NEW child fork, which must narrow, never widen.
    """

    fork_id: str
    caps: CapabilitySet
    parent_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.fork_id, str) or not self.fork_id:
            raise ValueError("fork_id must be a non-empty str")
        if not isinstance(self.caps, CapabilitySet):
            raise TypeError(f"caps must be CapabilitySet, got {type(self.caps).__name__}")
        if self.parent_id is not None and (not isinstance(self.parent_id, str) or not self.parent_id):
            raise ValueError("parent_id must be None or a non-empty str")

    def spawn_child(self, fork_id: str, caps: CapabilitySet) -> ForkContext:
        """Create a child fork; refuse any capability the parent does not hold.

        Escalation is checked against THIS fork, not some root: a fork that dropped a
        capability cannot re-mint it for a descendant, so authority only ever narrows
        down a spawn chain.
        """
        if not caps.subset_of(self.caps):
            escalated = sorted(caps.tokens - self.caps.tokens)[0]
            receipt = _receipt(fork_id, _SPAWN_TOOL, escalated, "denied", "capability_escalation")
            raise CapabilityDenied(fork_id, escalated, "capability_escalation", receipt)
        return ForkContext(fork_id=fork_id, caps=caps, parent_id=self.fork_id)


def check_tool_call(
    fork: ForkContext,
    tool_name: str,
    required: str,
    args: dict,
    *,
    countersigned: bool = False,
) -> dict[str, str]:
    """The one gate a tool call passes on its way to execution.

    Order is deliberate: the capability check runs FIRST, so a fork that lacks the token
    is denied for that reason even when its arguments are also tainted — a countersign
    (an arbiter approving tainted input) must never double as a capability grant.
    """
    if not fork.caps.allows(required):
        receipt = _receipt(fork.fork_id, tool_name, required, "denied", "capability_missing")
        raise CapabilityDenied(fork.fork_id, required, "capability_missing", receipt)
    assert_walkable_args(args)
    if is_tainted(args):
        if not countersigned:
            receipt = _receipt(
                fork.fork_id, tool_name, required, "denied", "tainted_argument_uncountersigned"
            )
            raise CapabilityDenied(
                fork.fork_id, required, "tainted_argument_uncountersigned", receipt
            )
        return _receipt(
            fork.fork_id, tool_name, required, "allowed_countersigned", "tainted_argument_countersigned"
        )
    return _receipt(fork.fork_id, tool_name, required, "allowed", "capability_present")
