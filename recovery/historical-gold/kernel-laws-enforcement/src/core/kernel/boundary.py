"""The Authoritative Execution Boundary — the actor requests an INTENT; only the
boundary NAMES the effect that actually occurs.

The Mandate Envelope's honest weakness was caller-supplied tool identity: a lane
could label a write as ``fs.read`` and the conformance proof would bless it. This
module removes that assumption by making ONE component own the seam between what the
model asked for and what physically happens:

    actor intent  ->  capability resolver  ->  resource canonicalizer
                  ->  MANDATE gate         ->  Law-3 permission gate
                  ->  execute              ->  EffectJournal (+ outcome truth)

Division of authority (nothing duplicated):

- **Mandate** controls MISSION SCOPE (which capabilities, which canonical resources,
  how many effects).
- **Law 3** controls CAPABILITY PERMISSION (does this fork hold the token at all).
- **The resolver** owns NAMING: verbs map to capabilities through a closed registry;
  an unmapped verb refuses rather than guessing. The model cannot choose the
  authoritative identity — it can only choose among registered intents.
- **Canonical resources**: authorization binds to identity, not spelling.
  Paths are realpath'd (symlink/traversal collapses), repos normalize to
  ``repo:owner/name``, network binds to origin ``net:host``. ``./src/app.py``,
  ``../other-repo/src/app.py`` and a symlink all canonicalize before the mandate
  ever sees them.

Outcome truth: a transport dying after dispatch is recorded ATTEMPTED/AUTHORIZED/
OUTCOME-UNKNOWN (through Law 4's EffectOutcomeUnknown channel) — never completed,
never denied, never "not attempted". Blind retry of an unresolved unknown is
refused unless the capability was REGISTERED idempotent.

Negative space: NOT ATTEMPTED derives ONLY from this ledger — a capability no
intent ever reached the boundary with. Requested-but-refused is DENIED, which is
not the same thing, and model logs prove nothing either way.
"""
from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

from core.kernel.capabilities import CapabilitySet
from core.kernel.envelope import BudgetExhausted, Envelope, OutsideEnvelope, Revoked
from core.kernel.effects import (
    EffectJournal,
    EffectOutcomeUnknown,
    EffectRunner,
)

__all__ = [
    "CapabilitySpec",
    "ExecutionLedger",
    "ExecutionGate",
    "IntentNotResolvable",
    "ResourceIdentity",
    "UnknownUnresolved",
]


class IntentNotResolvable(ValueError):
    """The actor named an intent no registration claims. Guessing is naming."""


class UnknownUnresolved(RuntimeError):
    """An earlier UNKNOWN outcome for this capability/resource blocks blind retry."""


@dataclass(frozen=True)
class ResourceIdentity:
    """The canonical form authorization binds to."""

    capability: str
    resource: str

    @property
    def effect_id(self) -> str:
        return f"{self.capability}@{self.resource}"


@dataclass(frozen=True)
class CapabilitySpec:
    """One registration: an actor-speak verb -> THE capability it really is, plus
    the canonicalizer producing the resource identity from raw arguments."""

    intent: str
    capability: str
    canonicalize: Callable[[Mapping[str, object]], str]
    idempotent: bool = False


def _canon_path(args: Mapping[str, object]) -> str:
    raw = str(args.get("path") or "")
    if not raw:
        raise IntentNotResolvable("path argument missing")
    return "file:" + os.path.realpath(raw)


def _canon_repo(args: Mapping[str, object]) -> str:
    raw = str(args.get("repo") or "")
    parts = [p for p in raw.split("/") if p]
    if len(parts) != 2:
        raise IntentNotResolvable(f"repo must be owner/name, got {raw!r}")
    return f"repo:{parts[0].lower()}/{parts[1]}"


def _canon_origin(args: Mapping[str, object]) -> str:
    url = urlsplit(str(args.get("url") or ""))
    if not url.hostname:
        raise IntentNotResolvable("url argument missing")
    scheme = (url.scheme or "https").lower()
    return f"net:{scheme}://{url.hostname.lower()}"


def _canon_ref(args: Mapping[str, object]) -> str:
    repo = _canon_repo(args)
    return f"{repo}@{str(args.get('branch') or 'HEAD')}"


REGISTRY: tuple[CapabilitySpec, ...] = (
    CapabilitySpec("read file", "fs.read", _canon_path),
    CapabilitySpec("write file", "fs.write", _canon_path),
    CapabilitySpec("delete file", "fs.delete", _canon_path),
    CapabilitySpec("read repository", "repo.read", _canon_path),
    CapabilitySpec("clone repository", "repo.clone", _canon_repo),
    CapabilitySpec("delete remote branch", "git.branch.delete", _canon_ref),
    CapabilitySpec("fetch url", "net.fetch", _canon_origin),
)


def resolve(intent: str) -> CapabilitySpec:
    """Closed registry lookup. The ACTOR chose words; THIS table chooses meaning."""
    wanted = intent.strip().lower()
    for spec in REGISTRY:
        if spec.intent == wanted:
            return spec
    raise IntentNotResolvable(
        f"intent {intent!r} is not registered — the boundary will not guess "
        "what operation the actor meant"
    )


@dataclass(frozen=True)
class _LedgerEntry:
    index: int
    status: str                 # allowed | denied | attempted_unknown
    capability: str
    resource: str
    reason: str
    version: int


class ExecutionLedger:
    """The ONLY source of negative space. Reached the boundary = attempted or
    denied; never reached = not attempted. Model logs prove nothing here."""

    def __init__(self) -> None:
        self.entries: list[_LedgerEntry] = []

    def record(self, status: str, spec_res: ResourceIdentity, reason: str,
               version: int) -> None:
        self.entries.append(_LedgerEntry(
            index=len(self.entries), status=status, capability=spec_res.capability,
            resource=spec_res.resource, reason=reason, version=version))

    def attempted(self) -> tuple[str, ...]:
        return tuple(sorted({f"{e.capability}@{e.resource}" for e in self.entries}))

    def not_attempted(self, granted_tools: frozenset[str]) -> tuple[str, ...]:
        reached = {e.capability for e in self.entries}
        return tuple(sorted(granted_tools - reached))


class ExecutionGate:
    """The single seam. Owns naming, order of law, and outcome truth."""

    def __init__(self, *, envelope: Envelope, permissions: CapabilitySet,
                 runner: EffectRunner, registry: tuple[CapabilitySpec, ...] = REGISTRY) -> None:
        self._envelope = envelope
        self._permissions = permissions
        self._runner = runner
        self._registry = {s.intent: s for s in registry}
        self.ledger = ExecutionLedger()
        self._unresolved: set[str] = set()

    def execute(self, intent: str, args: Mapping[str, object], *,
                fn: Callable[..., object]) -> object:
        try:
            spec = self._registry[intent.strip().lower()]
        except KeyError:
            # Unresolvable intents still count as restraint evidence at the boundary.
            self.ledger.record("denied", ResourceIdentity("<unresolvable>", intent),
                               f"intent {intent!r} not registered",
                               self._envelope.version)
            raise IntentNotResolvable(f"intent {intent!r} not registered") from None

        identity = ResourceIdentity(spec.capability, spec.canonicalize(dict(args)))

        if identity.effect_id in self._unresolved and not spec.idempotent:
            self.ledger.record("denied", identity,
                               "prior UNKNOWN outcome unresolved; blind retry forbidden",
                               self._envelope.version)
            raise UnknownUnresolved(
                f"{identity.effect_id} has an unresolved UNKNOWN outcome — retry only "
                "with evidence the first dispatch did not land")

        if not self._permissions.allows(identity.capability):
            self.ledger.record("denied", identity, "permission not held (Law 3)",
                               self._envelope.version)
            raise OutsideEnvelope(identity.capability,
                                  "permission not held (Law 3)", self._envelope.version)

        try:
            self._envelope.admit_resource(identity.capability, identity.resource)
        except (OutsideEnvelope, Revoked, BudgetExhausted) as exc:
            self.ledger.record("denied", identity, str(exc.reason), exc.version)
            raise

        try:
            result = self._runner.run(identity.effect_id, fn, *args.values())
        except EffectOutcomeUnknown as exc:
            self.ledger.record("attempted_unknown", identity,
                               f"dispatched, outcome unknown: {exc.reason}",
                               self._envelope.version)
            self._unresolved.add(identity.effect_id)
            raise
        self.ledger.record("allowed", identity, "executed", self._envelope.version)
        return result


def build_gate(*, envelope: Envelope, permissions: CapabilitySet,
               registry: tuple[CapabilitySpec, ...] = REGISTRY) -> ExecutionGate:
    """Convenience: a gate over a fresh recording runner owned by the same journal
    the conformance proof will bind to."""
    return ExecutionGate(envelope=envelope, permissions=permissions,
                         runner=EffectRunner(mode="record"), registry=registry)


def conformance_v2(gate: ExecutionGate, envelope: Envelope,
                   journal: EffectJournal | None = None) -> dict:
    """Proof shape v2: every boundary event carries canonical identity + version +
    status; negative space derives from the LEDGER alone."""
    journal = journal or gate._runner.journal
    base = envelope.conformance(journal)
    return {
        "mandate_id": base.mandate_id,
        "journal_digest": base.journal_digest,
        "events": [
            {"index": e.index, "status": e.status, "capability": e.capability,
             "resource": e.resource, "reason": e.reason, "version": e.version}
            for e in gate.ledger.entries
        ],
        "attempted": list(gate.ledger.attempted()),
        "not_attempted": list(gate.ledger.not_attempted(envelope_granted_tools(envelope))),
        "revoked": base.revoked,
    }


def envelope_granted_tools(envelope: Envelope) -> frozenset[str]:
    return frozenset(envelope._tools[-1])
