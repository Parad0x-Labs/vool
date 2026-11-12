"""The change-impact governor: which behaviors did this change put at risk, and what is unproven.

The question this answers is not "which tests import the file you touched". Import reachability
in a runtime this size closes over nearly everything, which is why an impact tool built on it
ends up advising "run the whole suite" for most changes -- advice that is free to produce,
impossible to act on, and identical to what CI already does.

The question here is narrower and more useful:

    For each unit this change touched, which *behavioral invariant* depends on it, which proof
    would notice if that behavior broke, and did that proof actually execute the changed code?

The last clause is the part a full-suite run cannot answer. A suite can be entirely green while
a changed function was never entered by anything -- green then means "nothing I ran contradicts
you", not "this change is proven". The governor separates those two states and fails on the
second.

Commands
--------

``audit``   registry integrity: proofs exist, declared surfaces exist, and every declared
            surface is confirmed by an observed edge. A declared edge no proof reaches is a
            false claim and is reported as one.
``record``  run the registry's proofs under the tracer and write the observed edge map.
``map``     report impact for a diff, from the stored map. Reads nothing but git and JSON.
``gate``    the enforcing form: resolve required proofs, RUN them, and require both that they
            pass and that they actually executed every changed unit.
``verify``  offline form of the same judgement against a trace produced by some other run,
            for a CI layout that runs the suite in one job and judges it in another.

Exit codes: 0 clean, 1 gate/audit failure, 2 usage or environment error.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ops.impact.registry import (
    DEFAULT_REGISTRY,
    Invariant,
    Registry,
    load_registry,
    proof_matches,
)
from ops.impact.units import MODULE_SCOPE, changed_units, index_units, split_unit, unit_key

DEFAULT_PROOF_MAP = Path("ops/impact/proof_map.json")
_PROOF_CHUNK = 200


# --------------------------------------------------------------------------------------
# observed edges
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ProofMap:
    """Observed edges: which units each proof was seen to execute, and how it ended."""

    units_by_proof: dict[str, frozenset[str]]
    outcomes: dict[str, str]
    setup_units_by_proof: dict[str, frozenset[str]] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: dict) -> ProofMap:
        """Read either form: the tracer's verbose per-run trace, or the compact stored map.

        The stored map repeats the same few thousand unit keys across hundreds of proofs, so it
        is written with a shared string table. Same information, roughly a fifth of the bytes.
        """
        proofs = payload.get("proofs") or {}
        table: list[str] = list(payload.get("unit_table") or ())
        units_by_proof: dict[str, frozenset[str]] = {}
        setup_units: dict[str, frozenset[str]] = {}
        outcomes: dict[str, str] = {}

        def expand(entry: dict, verbose_key: str, packed_key: str) -> frozenset[str]:
            if verbose_key in entry:
                return frozenset(entry.get(verbose_key) or ())
            return frozenset(table[index] for index in entry.get(packed_key) or () if index < len(table))

        for nodeid, entry in proofs.items():
            units_by_proof[nodeid] = expand(entry, "units", "u")
            setup_units[nodeid] = expand(entry, "setup_units", "s")
            outcomes[nodeid] = str(entry.get("outcome", "unknown"))
        return cls(units_by_proof=units_by_proof, outcomes=outcomes, setup_units_by_proof=setup_units)

    def to_payload(self) -> dict:
        table = sorted({unit for units in self.units_by_proof.values() for unit in units}
                       | {unit for units in self.setup_units_by_proof.values() for unit in units})
        index = {unit: position for position, unit in enumerate(table)}
        return {
            "schema": 3,
            "unit_table": table,
            "proofs": {
                nodeid: {
                    "outcome": self.outcomes.get(nodeid, "unknown"),
                    "u": sorted(index[unit] for unit in units),
                    "s": sorted(index[unit] for unit in self.setup_units_by_proof.get(nodeid, frozenset())),
                }
                for nodeid, units in sorted(self.units_by_proof.items())
            },
        }

    @classmethod
    def load(cls, path: Path) -> ProofMap:
        if not path.exists():
            return cls(units_by_proof={}, outcomes={})
        return cls.from_payload(json.loads(path.read_text(encoding="utf-8")))

    def scaffolding_only(self, unit: str) -> tuple[str, ...]:
        """Proofs that touched ``unit`` only while building their fixtures.

        Reported, never counted as an edge. "Your database helper ran during my setup" is not
        a proof that the behavior survives a change to it.
        """
        return tuple(
            sorted(
                nodeid
                for nodeid, units in self.setup_units_by_proof.items()
                if unit in units and unit not in self.units_by_proof.get(nodeid, frozenset())
            )
        )

    def proofs_executing(self, unit: str) -> tuple[str, ...]:
        return tuple(sorted(nodeid for nodeid, units in self.units_by_proof.items() if unit in units))

    def nodeids_for(self, selector: str) -> tuple[str, ...]:
        return tuple(sorted(nodeid for nodeid in self.units_by_proof if proof_matches(selector, nodeid)))

    def selector_executed(self, selector: str, unit: str, *, include_setup: bool = False) -> tuple[str, ...]:
        """Node ids under ``selector`` that were observed entering ``unit``.

        ``include_setup`` is for units that live inside the proof's own file. A fixture is not
        scaffolding when the thing being changed is the fixture: the reason setup is excluded
        elsewhere -- every proof touches the same storage helpers while building a database --
        simply does not arise for a file that belongs to this proof and nothing else.
        """
        found: list[str] = []
        for nodeid in self.nodeids_for(selector):
            if unit in self.units_by_proof[nodeid] or (
                include_setup and unit in self.setup_units_by_proof.get(nodeid, frozenset())
            ):
                found.append(nodeid)
        return tuple(found)


# --------------------------------------------------------------------------------------
# unit existence
# --------------------------------------------------------------------------------------


def unit_exists(repo_root: Path, unit: str) -> bool:
    """True when ``unit`` still exists in the working tree.

    A change that deletes a function produces a unit that cannot be executed by anything ever
    again. Requiring a proof to enter it would be a demand no change could satisfy, so the
    execution gate skips units that are gone while the proof-coverage gate still names them.
    """
    path, qualname = split_unit(unit)
    target = repo_root / path
    if not target.exists():
        return False
    if qualname is None or qualname == MODULE_SCOPE:
        return True
    try:
        source = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    return any(span.qualname == qualname for span in index_units(source))


# --------------------------------------------------------------------------------------
# impact resolution
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class UnitImpact:
    unit: str
    invariants: tuple[str, ...]
    proofs: tuple[str, ...]
    governed: bool
    exists: bool
    exempt_reason: str | None = None
    observable: bool = True

    @property
    def unproven(self) -> bool:
        return self.governed and not self.proofs and self.exempt_reason is None


@dataclass
class ImpactReport:
    base: str
    changed_units: tuple[str, ...] = ()
    changed_assets: tuple[str, ...] = ()
    impacts: tuple[UnitImpact, ...] = ()
    required_proofs: tuple[str, ...] = ()
    at_risk: tuple[str, ...] = ()
    unproven: tuple[str, ...] = ()
    ungoverned: tuple[str, ...] = ()
    newly_ungoverned: tuple[str, ...] = ()
    audit_errors: tuple[str, ...] = ()
    failed_proofs: tuple[str, ...] = ()
    missing_proofs: tuple[str, ...] = ()
    unexecuted: tuple[str, ...] = ()
    executed_by: dict[str, list[str]] = field(default_factory=dict)

    def blocking(self) -> tuple[str, ...]:
        problems: list[str] = []
        for label, items in (
            ("registry defect", self.audit_errors),
            ("no required proof covers the change", self.unproven),
            ("new file beside governed code, unclaimed", self.newly_ungoverned),
            ("required proof did not run", self.missing_proofs),
            ("required proof failed", self.failed_proofs),
            ("changed code was never executed by its required proofs", self.unexecuted),
        ):
            problems.extend(f"{label}: {item}" for item in items)
        return tuple(problems)

    def to_dict(self) -> dict:
        return {
            "base": self.base,
            "changed_units": list(self.changed_units),
            "changed_assets": list(self.changed_assets),
            "at_risk_invariants": list(self.at_risk),
            "required_proofs": list(self.required_proofs),
            "unproven_units": list(self.unproven),
            "ungoverned_units": list(self.ungoverned),
            "newly_ungoverned_files": list(self.newly_ungoverned),
            "audit_errors": list(self.audit_errors),
            "missing_proofs": list(self.missing_proofs),
            "failed_proofs": list(self.failed_proofs),
            "unexecuted_units": list(self.unexecuted),
            "executed_by": self.executed_by,
            "impacts": [
                {
                    "unit": impact.unit,
                    "invariants": list(impact.invariants),
                    "proofs": list(impact.proofs),
                    "governed": impact.governed,
                    "exists": impact.exists,
                    "exempt_reason": impact.exempt_reason,
                }
                for impact in self.impacts
            ],
            "blocking": list(self.blocking()),
        }


def _invariants_for_unit(registry: Registry, proof_map: ProofMap, unit: str) -> tuple[set[str], set[str]]:
    """Return ``(invariant ids, required proofs)`` for one changed unit.

    Two independent sources of edge, unioned:

    * **declared** -- an invariant names this unit or its file as a surface. Survives dynamic
      dispatch, subprocess boundaries and config-driven behavior, which no tracer can see.
    * **observed** -- some proof was recorded executing this unit. Survives an author who
      forgot to declare, which is the common case.

    A changed *proof* is also an edge: editing the test that holds an invariant puts that
    invariant at risk, so the invariants whose proofs live in that file are pulled in.
    """
    path, _ = split_unit(unit)
    invariant_ids: set[str] = set()
    proofs: set[str] = set()

    for invariant in registry.invariants:
        # `claims_asset` covers a source file declared unobservable -- code that only runs in a
        # subprocess, or inside the tracer itself. Its units still belong to the invariant and
        # still require its proofs; what they cannot require is an execution witness.
        if invariant.claims(unit) or invariant.claims_asset(path):
            invariant_ids.add(invariant.id)
            proofs.update(invariant.proofs)

    for nodeid in proof_map.proofs_executing(unit):
        for holder in registry.invariants_for_proof(nodeid):
            invariant_ids.add(holder.id)
            proofs.update(holder.selectors_matching(nodeid))

    for invariant in registry.invariants_in_proof_file(path):
        invariant_ids.add(invariant.id)
        proofs.update(invariant.proofs)

    return invariant_ids, proofs


def _governed_dirs(registry: Registry) -> set[str]:
    """Directories holding governed implementation code.

    Surfaces only. Proof directories are excluded on purpose: adding a test file is the thing
    this repository wants people doing, and a gate that objects to new tests would train the
    exact habit it exists to encourage.
    """
    dirs: set[str] = set()
    for invariant in registry.invariants:
        for surface in invariant.surfaces:
            path, _ = split_unit(surface)
            parent = str(Path(path).parent.as_posix())
            if parent not in {"", "."}:
                dirs.add(parent + "/")
    return dirs


def _claimed_files(registry: Registry) -> set[str]:
    """Files some invariant is responsible for: its surfaces, its assets, and its proofs.

    A proof file is claimed by the invariant it holds. Editing the test that pins a behavior is
    a change to that behavior's guarantee, so it belongs to the invariant as surely as the
    implementation does.
    """
    claimed: set[str] = set()
    for invariant in registry.invariants:
        for surface in invariant.surfaces:
            path, _ = split_unit(surface)
            claimed.add(path)
        claimed.update(invariant.assets)
        claimed.update(selector.split("::", 1)[0] for selector in invariant.proofs)
    return claimed


def detect_newly_ungoverned(repo_root: Path, registry: Registry, base: str, changed_paths: tuple[str, ...]) -> list[str]:
    """New Python files dropped into a directory that already holds governed code.

    This closes the obvious way around the gate: move the logic one file sideways, into a
    module no invariant claims, and the change stops being anyone's responsibility. A new file
    next to governed code has to be claimed, or exempted with a stated reason.
    """
    governed_dirs = _governed_dirs(registry)
    claimed = _claimed_files(registry)
    offenders: list[str] = []
    for path in changed_paths:
        if not path.endswith(".py") or path in claimed:
            continue
        if registry.exemption_for(path) is not None or registry.exemption_for(unit_key(path, MODULE_SCOPE)):
            continue
        parent = str(Path(path).parent.as_posix()) + "/"
        if parent not in governed_dirs:
            continue
        if not (repo_root / path).exists():
            continue
        existed = subprocess.run(
            ["git", "cat-file", "-e", f"{base}:{path}"],
            cwd=str(repo_root),
            capture_output=True,
            check=False,
        )
        if existed.returncode != 0:
            offenders.append(path)
    return sorted(offenders)


def build_report(
    repo_root: Path,
    registry: Registry,
    proof_map: ProofMap,
    base: str,
    *,
    head: str | None = None,
) -> ImpactReport:
    changed = changed_units(repo_root, base, head_ref=head)
    impacts: list[UnitImpact] = []
    required: set[str] = set()
    at_risk: set[str] = set()

    for unit in changed.units:
        path, _ = split_unit(unit)
        invariant_ids, proofs = _invariants_for_unit(registry, proof_map, unit)
        exemption = registry.exemption_for(unit) or registry.exemption_for(path)
        impacts.append(
            UnitImpact(
                unit=unit,
                invariants=tuple(sorted(invariant_ids)),
                proofs=tuple(sorted(proofs)),
                governed=registry.governs(path),
                exists=unit_exists(repo_root, unit),
                exempt_reason=exemption.reason if exemption else None,
                observable=not any(inv.claims_asset(path) for inv in registry.invariants),
            )
        )
        required.update(proofs)
        at_risk.update(invariant_ids)

    for asset in changed.assets:
        holders = [inv for inv in registry.invariants if inv.claims_asset(asset)]
        exemption = registry.exemption_for(asset)
        impacts.append(
            UnitImpact(
                unit=asset,
                invariants=tuple(sorted(inv.id for inv in holders)),
                proofs=tuple(sorted({p for inv in holders for p in inv.proofs})),
                governed=registry.governs(asset),
                exists=(repo_root / asset).exists(),
                exempt_reason=exemption.reason if exemption else None,
                observable=False,
            )
        )
        for invariant in holders:
            at_risk.add(invariant.id)
            required.update(invariant.proofs)

    return ImpactReport(
        base=base,
        changed_units=changed.units,
        changed_assets=changed.assets,
        impacts=tuple(impacts),
        required_proofs=tuple(sorted(required)),
        at_risk=tuple(sorted(at_risk)),
        unproven=tuple(sorted(impact.unit for impact in impacts if impact.unproven)),
        ungoverned=tuple(sorted(impact.unit for impact in impacts if not impact.governed)),
        newly_ungoverned=tuple(detect_newly_ungoverned(repo_root, registry, base, changed.paths)),
    )


# --------------------------------------------------------------------------------------
# registry audit
# --------------------------------------------------------------------------------------


def _surface_exists(repo_root: Path, surface: str) -> bool:
    path, qualname = split_unit(surface)
    if not (repo_root / path).exists():
        return False
    if qualname is None:
        return True
    return unit_exists(repo_root, surface)


def _surface_confirmed(proof_map: ProofMap, invariant: Invariant, surface: str) -> bool:
    surface_path, qualname = split_unit(surface)
    for selector in invariant.proofs:
        for nodeid in proof_map.nodeids_for(selector):
            for observed in proof_map.units_by_proof[nodeid]:
                observed_path, observed_qual = split_unit(observed)
                if observed_path != surface_path:
                    continue
                if qualname is None or qualname == observed_qual:
                    return True
    return False


def audit_registry(repo_root: Path, registry: Registry, proof_map: ProofMap) -> list[str]:
    """Every way the registry can be lying, checked. Returns the list of lies found."""
    errors: list[str] = []

    for invariant in registry.invariants:
        if not invariant.statement:
            errors.append(f"{invariant.id}: has no statement, so nothing is actually being asserted")
        if not invariant.proofs:
            errors.append(f"{invariant.id}: has no proofs, so nothing would notice if it broke")
        if not invariant.surfaces and not invariant.assets:
            errors.append(f"{invariant.id}: has no surfaces, so no change can ever be mapped to it")
        if invariant.severity == "blocker" and not invariant.kill:
            errors.append(f"{invariant.id}: is a blocker but does not state the sabotage that would kill it")

        for selector in invariant.proofs:
            proof_path = selector.split("::", 1)[0]
            if not (repo_root / proof_path).exists():
                errors.append(f"{invariant.id}: proof file does not exist: {proof_path}")
                continue
            nodeids = proof_map.nodeids_for(selector)
            if not nodeids:
                errors.append(
                    f"{invariant.id}: proof selector matches nothing in the observed map, "
                    f"so its edges are unrecorded: {selector}"
                )
                continue
            for nodeid in nodeids:
                outcome = proof_map.outcomes.get(nodeid, "unknown")
                # `skipped` is not a defect here. A skipped proof simply executes nothing, and
                # the surface-confirmation check below is what makes that cost something: an
                # invariant whose proofs all skip cannot evidence a single surface.
                if outcome not in ("passed", "skipped"):
                    errors.append(f"{invariant.id}: proof was recorded as {outcome}, not passed: {nodeid}")

        for surface in invariant.surfaces:
            if not _surface_exists(repo_root, surface):
                errors.append(f"{invariant.id}: declared surface does not exist: {surface}")
                continue
            if not _surface_confirmed(proof_map, invariant, surface):
                errors.append(
                    f"{invariant.id}: declares surface {surface} but none of its own proofs was observed "
                    "executing it -- the edge is claimed, not evidenced"
                )

        for asset in invariant.assets:
            if not (repo_root / asset).exists():
                errors.append(f"{invariant.id}: declared asset does not exist: {asset}")
                continue
            observers = [
                nodeid
                for nodeid, units in proof_map.units_by_proof.items()
                if any(split_unit(unit)[0] == asset for unit in units)
            ]
            if observers:
                # `assets` exempts a file from needing an execution witness. A file that IS
                # observed executing does not need the exemption, and taking it anyway would
                # quietly downgrade a checkable edge into an unchecked assertion.
                errors.append(
                    f"{invariant.id}: {asset} is declared unobservable but {observers[0]} executes it; "
                    "declare it under surfaces instead"
                )

    claimed = _claimed_files(registry)
    for entry in registry.governed_scope:
        if entry.endswith("/"):
            if not (repo_root / entry.rstrip("/")).is_dir():
                errors.append(f"governed_scope: directory does not exist: {entry}")
            continue
        if not (repo_root / entry).exists():
            errors.append(f"governed_scope: file does not exist: {entry}")
        elif entry not in claimed and registry.exemption_for(entry) is None:
            errors.append(f"governed_scope: {entry} is governed but no invariant claims it")

    for exemption in registry.exemptions:
        path, _ = split_unit(exemption.unit)
        if not (repo_root / path).exists():
            errors.append(f"exemption: target does not exist: {exemption.unit}")

    return errors


# --------------------------------------------------------------------------------------
# running proofs
# --------------------------------------------------------------------------------------


def run_proofs(repo_root: Path, proofs: list[str], *, out_dir: Path, extra_args: list[str] | None = None) -> ProofMap:
    """Run ``proofs`` under the tracer and return the observed map from THIS run.

    The stored map is a hint used to pick proofs. This result is the authority: it is the only
    thing that can answer "did the proof I required actually enter the code you changed", and
    it is produced after the change, not before it.
    """
    if not proofs:
        return ProofMap(units_by_proof={}, outcomes={})

    out_dir.mkdir(parents=True, exist_ok=True)
    merged_units: dict[str, frozenset[str]] = {}
    merged_setup: dict[str, frozenset[str]] = {}
    merged_outcomes: dict[str, str] = {}
    env = dict(os.environ)
    # The tree under test comes first; the tree holding this plugin comes next so
    # `-p ops.impact.tracer` resolves even when the two are different trees, which is exactly
    # the case when the governor's own meta-proof drives a synthetic repository.
    search_path = [str(repo_root), str(REPO_ROOT), env.get("PYTHONPATH", "")]
    env["PYTHONPATH"] = os.pathsep.join(part for part in search_path if part)

    for index in range(0, len(proofs), _PROOF_CHUNK):
        chunk = proofs[index : index + _PROOF_CHUNK]
        trace_path = out_dir / f"trace-{index // _PROOF_CHUNK}.json"
        command = [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-p",
            "ops.impact.tracer",
            f"--impact-trace-out={trace_path}",
            *(extra_args or []),
            *chunk,
        ]
        completed = subprocess.run(command, cwd=str(repo_root), env=env, capture_output=True, text=True, check=False)
        if not trace_path.exists():
            raise RuntimeError(
                "proof run produced no trace; pytest said:\n"
                + (completed.stdout or "")[-4000:]
                + (completed.stderr or "")[-2000:]
            )
        chunk_map = ProofMap.load(trace_path)
        merged_units.update(chunk_map.units_by_proof)
        merged_setup.update(chunk_map.setup_units_by_proof)
        merged_outcomes.update(chunk_map.outcomes)

    return ProofMap(units_by_proof=merged_units, outcomes=merged_outcomes, setup_units_by_proof=merged_setup)


def judge_execution(report: ImpactReport, observed: ProofMap) -> None:
    """Fill in the run-dependent verdicts on ``report`` from a fresh observation."""
    required = set(report.required_proofs)
    missing = sorted(selector for selector in required if not observed.nodeids_for(selector))
    # `skipped` is not `failed`. Several proofs here are gated on a live local model, and calling
    # an environment-skipped test a failure would make the gate cry wolf on the first change that
    # touched one. The consequence of a skip is picked up precisely by the execution check below:
    # a proof that never ran cannot be a witness for anything.
    failed = sorted(
        {
            nodeid
            for selector in required
            for nodeid in observed.nodeids_for(selector)
            if observed.outcomes.get(nodeid, "unknown") not in ("passed", "skipped")
        }
    )

    unexecuted: list[str] = []
    executed_by: dict[str, list[str]] = {}
    for impact in report.impacts:
        if not impact.governed or not impact.exists or impact.exempt_reason is not None:
            continue
        if not impact.proofs or not impact.observable:
            # Data files, and source declared unobservable, cannot produce an execution
            # witness. Demanding one would be a demand no change could satisfy. Their proofs
            # still have to pass.
            continue
        unit_path = split_unit(impact.unit)[0]
        witnesses = sorted(
            {
                nodeid
                for selector in impact.proofs
                for nodeid in observed.selector_executed(
                    selector,
                    impact.unit,
                    include_setup=unit_path == selector.split("::", 1)[0],
                )
            }
        )
        if witnesses:
            executed_by[impact.unit] = sorted(witnesses)
        else:
            unexecuted.append(impact.unit)

    report.missing_proofs = tuple(missing)
    report.failed_proofs = tuple(failed)
    report.unexecuted = tuple(sorted(unexecuted))
    report.executed_by = executed_by


# --------------------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------------------


def render_text(registry: Registry, report: ImpactReport) -> str:
    lines: list[str] = []
    lines.append(f"change-impact report against {report.base}")
    lines.append(f"  changed units : {len(report.changed_units)}")
    lines.append(f"  changed assets: {len(report.changed_assets)}")
    lines.append("")

    if report.at_risk:
        lines.append("BEHAVIORS AT RISK")
        for invariant_id in report.at_risk:
            invariant = registry.get(invariant_id)
            if invariant is None:
                continue
            lines.append(f"  [{invariant.severity}] {invariant.id}")
            lines.append(f"      {invariant.statement.strip().splitlines()[0]}")
            if invariant.scenario:
                lines.append(f"      scenario: {invariant.scenario}")
        lines.append("")

    if report.required_proofs:
        lines.append("REQUIRED PROOFS")
        for proof in report.required_proofs:
            marker = " "
            if proof in report.missing_proofs:
                marker = "!"
            elif proof in report.failed_proofs:
                marker = "x"
            lines.append(f"  {marker} {proof}")
        lines.append("")

    if report.unproven:
        lines.append("UNPROVEN CHANGES (governed code no required proof covers)")
        lines.extend(f"  - {unit}" for unit in report.unproven)
        lines.append("")

    if report.unexecuted:
        lines.append("UNEXECUTED CHANGES (required proofs ran but never entered this code)")
        lines.extend(f"  - {unit}" for unit in report.unexecuted)
        lines.append("")

    if report.newly_ungoverned:
        lines.append("NEW FILES BESIDE GOVERNED CODE, UNCLAIMED")
        lines.extend(f"  - {path}" for path in report.newly_ungoverned)
        lines.append("  name each as a surface in ops/impact/invariants.yaml, or exempt it with a reason")
        lines.append("")

    if report.ungoverned:
        lines.append(f"UNGOVERNED CHANGES ({len(report.ungoverned)}) -- outside the registry's scope, not judged")
        lines.extend(f"  - {unit}" for unit in report.ungoverned[:20])
        if len(report.ungoverned) > 20:
            lines.append(f"  ... and {len(report.ungoverned) - 20} more")
        lines.append("")

    if report.audit_errors:
        lines.append("REGISTRY DEFECTS")
        lines.extend(f"  - {error}" for error in report.audit_errors)
        lines.append("")

    blocking = report.blocking()
    lines.append(f"VERDICT: {'BLOCKED' if blocking else 'CLEAR'} ({len(blocking)} blocking finding(s))")
    return "\n".join(lines)


def render_github(report: ImpactReport) -> str:
    lines: list[str] = []
    for unit in report.unproven:
        path = split_unit(unit)[0]
        lines.append(f"::error file={path}::no required proof covers {unit}; claim it in ops/impact/invariants.yaml")
    for unit in report.unexecuted:
        path = split_unit(unit)[0]
        lines.append(f"::error file={path}::required proofs ran but never executed {unit}")
    for path in report.newly_ungoverned:
        lines.append(
            f"::error file={path}::new file beside governed code and no invariant claims it; "
            "name it as a surface in ops/impact/invariants.yaml, or add an exemption with a reason"
        )
    for proof in report.failed_proofs:
        lines.append(f"::error::required proof failed: {proof}")
    for proof in report.missing_proofs:
        lines.append(f"::error::required proof did not run: {proof}")
    for error in report.audit_errors:
        lines.append(f"::error::registry defect: {error}")
    return "\n".join(lines)


def emit(registry: Registry, report: ImpactReport, fmt: str) -> None:
    if fmt == "json":
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    elif fmt == "github":
        rendered = render_github(report)
        if rendered:
            print(rendered)
        print(render_text(registry, report))
    else:
        print(render_text(registry, report))


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def _fmt(args: argparse.Namespace) -> str:
    """Output format, accepted on either side of the subcommand.

    `governor map --base X --format json` is what a person types first. Rejecting it because
    the flag has to precede the subcommand is the kind of paper cut that gets a gate wrapped in
    a shell alias, and then quietly dropped.
    """
    return getattr(args, "format_override", None) or args.format


def _resolve(repo_root: Path, value: str | None, default: Path) -> Path:
    candidate = Path(value) if value else default
    return candidate if candidate.is_absolute() else repo_root / candidate


def _load(args: argparse.Namespace) -> tuple[Path, Registry, ProofMap]:
    repo_root = Path(args.repo_root).resolve() if args.repo_root else REPO_ROOT
    registry = load_registry(_resolve(repo_root, args.registry, DEFAULT_REGISTRY))
    proof_map = ProofMap.load(_resolve(repo_root, args.proof_map, DEFAULT_PROOF_MAP))
    return repo_root, registry, proof_map


def _work_dir(repo_root: Path, requested: str | None, label: str) -> Path:
    """Where trace artifacts land. Never inside the repository unless asked.

    A scratch directory in the tree under test shows up as an untracked change, and the governor
    would be right to report it -- a tool that dirties the thing it is judging.
    """
    del repo_root
    if requested:
        return Path(requested)
    return Path(tempfile.mkdtemp(prefix=f"impact-{label}-"))


def cmd_audit(args: argparse.Namespace) -> int:
    repo_root, registry, proof_map = _load(args)
    if args.fresh:
        # The stored map is evidence from whenever `record` last ran. A surface can quietly stop
        # being reached because of a change somewhere else entirely, and the stored map would go
        # on asserting the edge. `--fresh` re-earns every edge from a run happening right now.
        selectors = sorted({selector for invariant in registry.invariants for selector in invariant.proofs})
        proof_map = run_proofs(repo_root, selectors, out_dir=_work_dir(repo_root, args.work_dir, "audit"))
    errors = audit_registry(repo_root, registry, proof_map)
    if _fmt(args) == "json":
        print(json.dumps({"errors": errors, "invariants": len(registry.invariants)}, indent=2))
    else:
        print(f"registry: {len(registry.invariants)} invariant(s), {len(registry.proof_files())} proof file(s)")
        for error in errors:
            print(f"  DEFECT: {error}")
        print(f"VERDICT: {'BLOCKED' if errors else 'CLEAR'} ({len(errors)} defect(s))")
    return 1 if errors else 0


def cmd_record(args: argparse.Namespace) -> int:
    repo_root, registry, _ = _load(args)
    proofs = sorted({proof for invariant in registry.invariants for proof in invariant.proofs})
    observed = run_proofs(repo_root, proofs, out_dir=_work_dir(repo_root, args.work_dir, "record"))
    target = _resolve(repo_root, args.proof_map, DEFAULT_PROOF_MAP)
    payload = observed.to_payload()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    unmatched = [selector for selector in proofs if not observed.nodeids_for(selector)]
    print(f"recorded {len(payload['proofs'])} node id(s) from {len(proofs)} selector(s) -> {target}")
    for selector in unmatched:
        print(f"  SELECTOR MATCHED NOTHING: {selector}")
    return 1 if unmatched else 0


def cmd_map(args: argparse.Namespace) -> int:
    repo_root, registry, proof_map = _load(args)
    report = build_report(repo_root, registry, proof_map, args.base, head=args.head)
    if args.audit:
        report.audit_errors = tuple(audit_registry(repo_root, registry, proof_map))
    emit(registry, report, _fmt(args))
    return 1 if report.blocking() else 0


def cmd_gate(args: argparse.Namespace) -> int:
    repo_root, registry, proof_map = _load(args)
    report = build_report(repo_root, registry, proof_map, args.base, head=args.head)
    report.audit_errors = tuple(audit_registry(repo_root, registry, proof_map))
    observed = run_proofs(
        repo_root, list(report.required_proofs), out_dir=_work_dir(repo_root, args.work_dir, "gate")
    )
    judge_execution(report, observed)
    emit(registry, report, _fmt(args))
    return 1 if report.blocking() else 0


def cmd_verify(args: argparse.Namespace) -> int:
    repo_root, registry, proof_map = _load(args)
    report = build_report(repo_root, registry, proof_map, args.base, head=args.head)
    report.audit_errors = tuple(audit_registry(repo_root, registry, proof_map))
    observed = ProofMap.load(Path(args.trace))
    judge_execution(report, observed)
    emit(registry, report, _fmt(args))
    return 1 if report.blocking() else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="governor", description=__doc__.splitlines()[0])
    parser.add_argument("--repo-root", default=None)
    parser.add_argument("--registry", default=None)
    parser.add_argument("--proof-map", default=None)
    parser.add_argument("--format", choices=("text", "json", "github"), default="text")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add(name: str, help_text: str) -> argparse.ArgumentParser:
        sub = subparsers.add_parser(name, help=help_text)
        sub.add_argument("--format", dest="format_override", choices=("text", "json", "github"), default=None)
        return sub

    audit = add("audit", "check the registry against the repository and the observed map")
    audit.add_argument(
        "--fresh",
        action="store_true",
        help="re-run the registry's proofs and audit against that run instead of the stored map",
    )
    audit.add_argument("--work-dir", default=None)
    audit.set_defaults(func=cmd_audit)

    record = add("record", "run the registry's proofs and write the observed edge map")
    record.add_argument("--work-dir", default=None)
    record.set_defaults(func=cmd_record)

    mapper = add("map", "report impact for a diff without running anything")
    mapper.add_argument("--base", required=True)
    mapper.add_argument("--head", default=None)
    mapper.add_argument("--audit", action="store_true", help="fold registry defects into the verdict")
    mapper.set_defaults(func=cmd_map)

    gate = add("gate", "resolve, run and judge the required proofs for a diff")
    gate.add_argument("--base", required=True)
    gate.add_argument("--head", default=None)
    gate.add_argument("--work-dir", default=None)
    gate.set_defaults(func=cmd_gate)

    verify = add("verify", "judge a diff against a trace produced by some other run")
    verify.add_argument("--base", required=True)
    verify.add_argument("--head", default=None)
    verify.add_argument("--trace", required=True)
    verify.set_defaults(func=cmd_verify)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (RuntimeError, ValueError, OSError) as error:
        print(f"governor: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
