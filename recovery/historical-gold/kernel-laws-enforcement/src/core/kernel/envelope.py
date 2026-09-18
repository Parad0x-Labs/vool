"""The Mandate Envelope — bounded autonomy with a conformance proof.

An agent trusted to "just handle it" improvises. The danger is not that it
improvises badly — it is that NOTHING in a normal runtime can afterwards state,
mechanically, what the improvisation was allowed to touch, what it actually
touched, what was refused, and what it deliberately never attempted. Scope creep
is invisible because there is no object that scope could be relative TO.

The envelope makes the mandate a TRANSACTIONAL OBJECT:

- A :class:`Mandate` names the objective, the tools, the resource patterns, and a
  finite effect budget. Sealing mints Envelope v1 — frozen forever.
- Every effect must be ADMITTED before it runs: tool in set, some string argument
  matching a resource pattern, budget not exhausted. Refusals are RECORDED, not
  silent — a denial is evidence of restraint, and restraint you cannot show is
  indistinguishable from luck.
- Amendments are append-only VERSIONS. The user themselves may widen the mandate
  mid-flight ("also wipe the logs") — but the widening applies from version N+1
  onward, and the conformance proof judges EVERY effect under the version in force
  AT ITS POSITION. Retroactive legality does not exist: you cannot narrow or widen
  history after the fact, even though you wrote the mandate.
- Revocation kills the envelope for all further effects, allowed or not.
- :meth:`Envelope.conformance` closes the run with a digest-bound PROOF: every
  admitted effect, every denial with its reason, budget consumed vs granted,
  obligations mapped to the mandate — and NEGATIVE claims: tool kinds never
  attempted, resource patterns never touched. "We did not do X" becomes a computed
  fact about the record, not a promise.

Fail-closed everywhere: exhausted budget refuses allowed tools too; a restored
envelope continues its exact counters (restart cannot reset spent budget); the
proof's digest binds it to one specific effect journal, so a proof cannot be
replayed over a different execution.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from core.kernel.effects import EffectJournal

__all__ = [
    "BudgetExhausted",
    "verify_proof",
    "ConformanceProof",
    "Envelope",
    "Mandate",
    "OutsideEnvelope",
    "Revoked",
]


class OutsideEnvelope(RuntimeError):
    """An effect outside the mandate. Recorded as restraint evidence, then raised."""

    def __init__(self, tool: str, reason: str, version: int) -> None:
        super().__init__(f"outside envelope v{version}: {tool}: {reason}")
        self.tool = tool
        self.reason = reason
        self.version = version


class BudgetExhausted(OutsideEnvelope):
    def __init__(self, tool: str, version: int, used: int, granted: int) -> None:
        super().__init__(tool, f"effect budget exhausted ({used}/{granted})", version)
        self.used, self.granted = used, granted


class Revoked(OutsideEnvelope):
    def __init__(self, tool: str, version: int) -> None:
        super().__init__(tool, "mandate revoked", version)


@dataclass(frozen=True)
class Mandate:
    """What the principal authorized. Everything else is outside by default."""

    objective: str
    tools: frozenset[str]
    resources: tuple[str, ...]          # fnmatch patterns
    #: argument keys whose values are checked against `resources`. Declared, not
    #: guessed: scanning every string would flag file CONTENTS that merely mention
    #: a path -- heuristics are how envelopes rot.
    resource_arg_keys: tuple[str, ...] = ("path",)
    max_effects: int = 0
    mandate_id: str = ""

    def __post_init__(self) -> None:
        if not self.tools:
            raise ValueError("a mandate granting zero tools authorizes nothing — "
                             "say so with an empty run, not an empty grant")
        if self.max_effects <= 0:
            raise ValueError("max_effects must be positive")


@dataclass(frozen=True)
class _Denial:
    index: int
    tool: str
    reason: str
    version: int


def _strings_in(value: object) -> Iterable[str]:
    stack = [value]
    seen: set[int] = set()
    while stack:
        current = stack.pop()
        if isinstance(current, str):
            yield current
        elif isinstance(current, dict):
            if id(current) in seen:
                continue
            seen.add(id(current))
            stack.extend(current.keys())
            stack.extend(current.values())
        elif isinstance(current, (list, tuple, set, frozenset)):
            if id(current) in seen:
                continue
            seen.add(id(current))
            stack.extend(current)


class Envelope:
    """Versioned admission gate + ledger. One envelope per mandate execution."""

    def __init__(self, mandate: Mandate) -> None:
        self._mandate = mandate
        self._tools = [frozenset(mandate.tools)]
        self._resources = [tuple(mandate.resources)]
        self._revoked = False
        self._version = 1
        self._admitted = 0
        self.denials: list[_Denial] = []
        self.admissions: list[dict[str, object]] = []
        self.amendments: list[str] = []

    # -- admission ---------------------------------------------------------------

    @property
    def version(self) -> int:
        return self._version

    @property
    def revoked(self) -> bool:
        return self._revoked

    def admit(self, tool: str, args: Mapping[str, object]) -> None:
        """Gate ONE effect. Raises (and records) if outside the current version."""
        index = self._admitted + len(self.denials)
        if self._revoked:
            self._record(index, tool, "mandate revoked")
            raise Revoked(tool, self._version)
        if tool not in self._tools[-1]:
            self._record(index, tool, f"tool '{tool}' not granted")
            raise OutsideEnvelope(tool, f"tool '{tool}' not granted", self._version)
        if self._admitted >= self._mandate.max_effects:
            self._record(index, tool,
                         f"budget exhausted ({self._admitted}/{self._mandate.max_effects})")
            raise BudgetExhausted(tool, self._version, self._admitted,
                                  self._mandate.max_effects)
        resources = self._resources[-1]
        if resources:
            # CONTAINMENT IS STRICT: every PATH-LIKE string in the arguments must
            # match a granted pattern. One legitimate path does not launder an
            # escaped one hiding beside it ('["core/a.py", "/etc/passwd"]').
            # Non-path words are ignored; a resource grant with no path anywhere
            # still refuses -- admitting an effect whose target we cannot see is
            # how envelopes rot.
            declared = [k for k in self._mandate.resource_arg_keys if k in args]
            if not declared:
                self._record(index, tool,
                             f"no declared resource argument present "
                             f"(expected keys {list(self._mandate.resource_arg_keys)})")
                raise OutsideEnvelope(
                    tool, f"no declared resource argument present "
                    f"(expected keys {list(self._mandate.resource_arg_keys)})",
                    self._version)
            escaped = [
                f"{key}={value!r}"
                for key in declared
                for value in ([args[key]] if isinstance(args[key], str) else list(args[key]))
                if not any(fnmatch.fnmatch(str(value), pat) for pat in resources)
            ]
            if escaped:
                self._record(index, tool, f"path(s) outside mandate: {escaped}")
                raise OutsideEnvelope(
                    tool, f"path(s) outside mandate: {escaped}", self._version)
        self._admitted += 1
        self.admissions.append({"index": index, "tool": tool,
                                "version": self._version})

    def admit_resource(self, tool: str, canonical_resource: str) -> None:
        """Gate an effect by CANONICAL identity (used by the authoritative boundary).
        The canonical resource string is matched against this version's patterns;
        all other laws (revocation, tool set, budget) apply identically."""
        index = self._admitted + len(self.denials)
        if self._revoked:
            self._record(index, tool, "mandate revoked")
            raise Revoked(tool, self._version)
        if tool not in self._tools[-1]:
            self._record(index, tool, f"tool '{tool}' not granted")
            raise OutsideEnvelope(tool, f"tool '{tool}' not granted", self._version)
        if self._admitted >= self._mandate.max_effects:
            self._record(index, tool,
                         f"budget exhausted ({self._admitted}/{self._mandate.max_effects})")
            raise BudgetExhausted(tool, self._version, self._admitted,
                                  self._mandate.max_effects)
        resources = self._resources[-1]
        if resources and not any(fnmatch.fnmatch(canonical_resource, pat) for pat in resources):
            self._record(index, tool,
                         f"resource {canonical_resource!r} outside mandate {list(resources)}")
            raise OutsideEnvelope(
                tool, f"resource {canonical_resource!r} outside mandate "
                f"{list(resources)}", self._version)
        self._admitted += 1
        self.admissions.append({"index": index, "tool": tool,
                                "resource": canonical_resource,
                                "version": self._version})

    def _record(self, index: int, tool: str, reason: str) -> None:
        self.denials.append(_Denial(index=index, tool=tool, reason=reason,
                                    version=self._version))

    # -- evolution -----------------------------------------------------------------

    def amend(self, *, add_tools: Iterable[str] = (), add_resources: Iterable[str] = (),
              note: str = "") -> int:
        """Append-only widening/narrowing. History stays judged under its own version.
        String arguments are coerced to single-element tuples -- a bare "logs/*" must
        never explode into per-character patterns (the '*' char alone matches ALL)."""
        if isinstance(add_tools, str):
            add_tools = (add_tools,)
        if isinstance(add_resources, str):
            add_resources = (add_resources,)
        if self._revoked:
            raise RuntimeError("cannot amend a revoked envelope")
        self._tools.append(self._tools[-1] | set(add_tools))
        self._resources.append(self._resources[-1] + tuple(add_resources))
        self._version += 1
        self.amendments.append(note or f"v{self._version}")
        return self._version

    def revoke(self, note: str = "") -> None:
        self._revoked = True
        self.amendments.append(note or "REVOKED")

    # -- persistence -----------------------------------------------------------------

    def to_json(self) -> str:
        return json.dumps({
            "schema": "vool.envelope.v1",
            "mandate": {"objective": self._mandate.objective,
                        "tools": sorted(self._mandate.tools),
                        "resources": list(self._mandate.resources),
                        "max_effects": self._mandate.max_effects,
                        "id": self._mandate.mandate_id},
            "version": self._version,
            "revoked": self._revoked,
            "admitted": self._admitted,
            "denials": [vars(d) for d in self.denials],
            "admissions": self.admissions,
            "amendments": self.amendments,
            "tools_versions": [sorted(t) for t in self._tools],
            "resource_versions": [list(r) for r in self._resources],
        }, sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, text: str) -> "Envelope":
        raw = json.loads(text)
        if raw.get("schema") != "vool.envelope.v1":
            raise ValueError("not a vool.envelope.v1 document")
        m = raw["mandate"]
        env = cls(Mandate(objective=m["objective"], tools=frozenset(m["tools"]),
                          resources=tuple(m["resources"]),
                          max_effects=m["max_effects"], mandate_id=m["id"]))
        env._version = raw["version"]
        env._revoked = raw["revoked"]
        env._admitted = raw["admitted"]      # restart CANNOT reset spent budget
        env.denials = [_Denial(**d) for d in raw["denials"]]
        env.admissions = raw["admissions"]
        env.amendments = raw["amendments"]
        env._tools = [frozenset(t) for t in raw["tools_versions"]]
        env._resources = [tuple(r) for r in raw["resource_versions"]]
        return env

    # -- the proof -----------------------------------------------------------------

    def conformance(self, journal: EffectJournal, *,
                    obligations: Iterable[tuple[str, str]] = ()) -> "ConformanceProof":
        """Digest-bound proof over THIS journal: admissions, denials, budget,
        negative space. The digest binds proof to execution — a proof replayed
        over a different tape is a forgery and fails verification."""
        entries = journal.entries()
        body = json.dumps({"envelope": self.to_json(),
                           "tape": [e["effect_id"] for e in entries],
                           "obligations": sorted(tuple(o) for o in obligations)},
                          sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        admitted_tools = {a["tool"] for a in self.admissions}
        denied_tools = {d.tool for d in self.denials}
        never_attempted = tuple(sorted(self._tools[-1] - admitted_tools - denied_tools))
        touched_patterns = tuple(sorted({
            pattern for a in self.admissions for pattern in self._resources_for(a["version"])
        })) if self._resources[-1] else ()
        return ConformanceProof(
            mandate_id=self._mandate.mandate_id, objective=self._mandate.objective,
            versions_used=self._version, effects_admitted=self._admitted,
            budget=self._mandate.max_effects, denials=tuple(
                {"index": d.index, "tool": d.tool, "reason": d.reason,
                 "version": d.version} for d in self.denials),
            never_attempted_tools=never_attempted,
            revoked=self._revoked,
            obligations=tuple(sorted(obligations)),
            journal_digest=digest)

    def _resources_for(self, version: int) -> tuple[str, ...]:
        return self._resources[version - 1] if version <= len(self._resources) else ()


@dataclass(frozen=True)
class ConformanceProof:
    mandate_id: str
    objective: str
    versions_used: int
    effects_admitted: int
    budget: int
    denials: tuple[dict[str, object], ...]
    never_attempted_tools: tuple[str, ...]
    revoked: bool
    obligations: tuple[tuple[str, str], ...]
    journal_digest: str

    def to_dict(self) -> dict:
        return {"mandate_id": self.mandate_id, "objective": self.objective,
                "versions_used": self.versions_used,
                "effects_admitted": self.effects_admitted, "budget": self.budget,
                "denials": list(self.denials),
                "never_attempted_tools": list(self.never_attempted_tools),
                "revoked": self.revoked,
                "obligations": [list(o) for o in self.obligations],
                "journal_digest": self.journal_digest}

    @classmethod
    def from_dict(cls, raw: dict) -> "ConformanceProof":
        return cls(mandate_id=raw["mandate_id"], objective=raw["objective"],
                   versions_used=raw["versions_used"],
                   effects_admitted=raw["effects_admitted"], budget=raw["budget"],
                   denials=tuple(dict(d) for d in raw["denials"]),
                   never_attempted_tools=tuple(raw["never_attempted_tools"]),
                   revoked=raw["revoked"],
                   obligations=tuple(tuple(o) for o in raw["obligations"]),
                   journal_digest=raw["journal_digest"])

    def render(self) -> str:  # noqa: E301
        lines = [f"[conformance] mandate {self.mandate_id}: {self.objective!r}",
                 f"  admitted {self.effects_admitted}/{self.budget} effects across "
                 f"{self.versions_used} envelope version(s)"
                 + ("  [REVOKED]" if self.revoked else "")]
        for d in self.denials:
            lines.append(f"  DENIED #{d['index']} {d['tool']}: {d['reason']} (v{d['version']})")
        if self.never_attempted_tools:
            lines.append(f"  never attempted: {', '.join(self.never_attempted_tools)}")
        if self.obligations:
            lines.append("  obligations: " + ", ".join(f"{oid}:{kind}" for oid, kind in
                                                       self.obligations))
        lines.append(f"  journal digest {self.journal_digest[:16]}…")
        return "\n".join(lines)


def verify_proof(envelope: Envelope, proof: ConformanceProof,
                 journal: EffectJournal) -> bool:
    """A proof verifies ONLY against the envelope that minted it and the exact
    journal it was bound to -- a policy object without its execution proves nothing.
    Obligations ride ON the proof, so verifier and minter cannot disagree."""
    if proof.mandate_id != envelope._mandate.mandate_id:
        return False
    entries = journal.entries()
    body = json.dumps({"envelope": envelope.to_json(),
                       "tape": [e["effect_id"] for e in entries],
                       "obligations": sorted(tuple(o) for o in proof.obligations)},
                      sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest() == proof.journal_digest
