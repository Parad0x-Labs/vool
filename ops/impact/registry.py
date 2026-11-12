"""The invariant registry: behavioral statements, the proofs that hold them, and their surfaces.

The registry is the human-owned half of the governor. It says what must remain true about the
*product*, in language a user would recognise, and names the proofs that would notice if it
stopped being true. It does not describe module structure, and it is not an import graph: two
files that import each other share nothing here unless the same behavior depends on both.

A surface is a file (`core/task_router.py`) or a single unit (`core/task_router.py::route`).
Directory-wide surfaces are not expressible on purpose. "Everything under core/" is how an
impact tool ends up telling you to run the whole suite, which is the same as telling you
nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ops.impact.units import split_unit

SEVERITIES = ("blocker", "major", "minor")
DEFAULT_REGISTRY = Path("ops/impact/invariants.yaml")


def proof_matches(selector: str, nodeid: str) -> bool:
    """True when ``nodeid`` is one of the tests ``selector`` names.

    Proofs are written as selectors, not as exact node ids, because a parametrised proof's node
    ids carry the parameter text: ``test_x[Return only raw text.]``. Pinning those would mean a
    registry that goes stale the moment someone adds a case to a table -- and going stale by
    *adding* coverage is the worst possible failure direction for a governor.
    """
    return nodeid == selector or nodeid.startswith(f"{selector}[") or nodeid.startswith(f"{selector}::")


@dataclass(frozen=True)
class Invariant:
    """One behavioral statement, its proofs, and the code it is implemented by."""

    id: str
    statement: str
    scenario: str
    severity: str
    proofs: tuple[str, ...]
    surfaces: tuple[str, ...]
    assets: tuple[str, ...] = ()
    kill: str = ""

    def claims(self, unit: str) -> bool:
        """True when this invariant declares ``unit`` (or the file holding it) as its surface."""
        path, _ = split_unit(unit)
        return any(surface in (unit, path) for surface in self.surfaces)

    def claims_asset(self, path: str) -> bool:
        return path in self.assets

    def selectors_matching(self, nodeid: str) -> tuple[str, ...]:
        return tuple(selector for selector in self.proofs if proof_matches(selector, nodeid))


@dataclass(frozen=True)
class Exemption:
    """A unit the gate may pass without a proof, and the stated reason it may."""

    unit: str
    reason: str


@dataclass(frozen=True)
class Registry:
    invariants: tuple[Invariant, ...]
    governed_scope: tuple[str, ...]
    exemptions: tuple[Exemption, ...] = ()
    source: Path | None = None
    _by_id: dict[str, Invariant] = field(default_factory=dict, compare=False)

    def get(self, invariant_id: str) -> Invariant | None:
        return self._by_id.get(invariant_id)

    def governs(self, path: str) -> bool:
        """True when ``path`` is inside the declared governed scope."""
        for entry in self.governed_scope:
            if entry.endswith("/"):
                if path.startswith(entry):
                    return True
            elif path == entry:
                return True
        return False

    def exemption_for(self, unit: str) -> Exemption | None:
        for exemption in self.exemptions:
            if exemption.unit == unit:
                return exemption
        return None

    def proof_files(self) -> tuple[str, ...]:
        files = {proof.split("::", 1)[0] for invariant in self.invariants for proof in invariant.proofs}
        return tuple(sorted(files))

    def invariants_for_proof(self, nodeid: str) -> tuple[Invariant, ...]:
        return tuple(inv for inv in self.invariants if inv.selectors_matching(nodeid))

    def invariants_in_proof_file(self, path: str) -> tuple[Invariant, ...]:
        return tuple(inv for inv in self.invariants if any(p.split("::", 1)[0] == path for p in inv.proofs))


def _as_tuple(raw: Any, *, field_name: str, invariant_id: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError(f"{invariant_id}: {field_name} must be a list")
    return tuple(str(item).strip() for item in raw if str(item).strip())


def load_registry(path: Path) -> Registry:
    """Parse and structurally validate the registry. Content validation lives in the audit."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: registry must be a mapping")
    if int(raw.get("version", 0)) != 1:
        raise ValueError(f"{path}: unsupported registry version {raw.get('version')!r}")

    invariants: list[Invariant] = []
    seen: set[str] = set()
    for entry in raw.get("invariants") or []:
        if not isinstance(entry, dict):
            raise ValueError(f"{path}: every invariant must be a mapping")
        invariant_id = str(entry.get("id", "")).strip()
        if not invariant_id:
            raise ValueError(f"{path}: an invariant is missing its id")
        if invariant_id in seen:
            raise ValueError(f"{path}: duplicate invariant id {invariant_id}")
        seen.add(invariant_id)
        severity = str(entry.get("severity", "")).strip()
        if severity not in SEVERITIES:
            raise ValueError(f"{invariant_id}: severity must be one of {SEVERITIES}, got {severity!r}")
        invariants.append(
            Invariant(
                id=invariant_id,
                statement=str(entry.get("statement", "")).strip(),
                scenario=str(entry.get("scenario", "")).strip(),
                severity=severity,
                proofs=_as_tuple(entry.get("proofs"), field_name="proofs", invariant_id=invariant_id),
                surfaces=_as_tuple(entry.get("surfaces"), field_name="surfaces", invariant_id=invariant_id),
                assets=_as_tuple(entry.get("assets"), field_name="assets", invariant_id=invariant_id),
                kill=str(entry.get("kill", "")).strip(),
            )
        )

    exemptions: list[Exemption] = []
    for entry in raw.get("exemptions") or []:
        if not isinstance(entry, dict):
            raise ValueError(f"{path}: every exemption must be a mapping")
        unit = str(entry.get("unit", "")).strip()
        reason = str(entry.get("reason", "")).strip()
        if not unit or not reason:
            raise ValueError(f"{path}: an exemption needs both a unit and a reason")
        exemptions.append(Exemption(unit=unit, reason=reason))

    governed = _as_tuple(raw.get("governed_scope"), field_name="governed_scope", invariant_id=str(path))
    registry = Registry(
        invariants=tuple(invariants),
        governed_scope=governed,
        exemptions=tuple(exemptions),
        source=path,
    )
    registry._by_id.update({inv.id: inv for inv in invariants})
    return registry
