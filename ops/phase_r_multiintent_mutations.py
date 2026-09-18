"""Mutation matrix for the Phase R multi-intent decomposition-floor repair (M1-M5).

Each mutation edits a REAL production guard, proves a NAMED test goes RED, then restores the file
byte-identically (sha256-verified) — and after all mutations the pristine suite is rerun GREEN.
A mutation that fails to turn its named test red means the guard is not load-bearing or the test
is vacuous; either aborts with a non-zero exit.

Run from the repo root:
    .venv/bin/python ops/phase_r_multiintent_mutations.py
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = str(ROOT / ".venv" / "bin" / "python")
SUITE = "tests/test_multi_intent_served_coverage.py"

PLANNER = ROOT / "core" / "conductor" / "planner.py"
AGENT = ROOT / "apps" / "vool_agent.py"
COMPOSE = ROOT / "core" / "conductor" / "compose.py"


@dataclass
class Mutation:
    key: str
    title: str
    path: Path
    edits: list[tuple[str, str]]
    red_tests: list[str]
    extra_paths: list[tuple[Path, list[tuple[str, str]]]] = field(default_factory=list)


MUTATIONS: list[Mutation] = [
    Mutation(
        key="M1",
        title="restore the unconditional plain-task decline (drop the live-evidence override)",
        path=PLANNER,
        edits=[
            (
                "    plain_shaped = is_ordinary_multi_part_plain_task(original)\n"
                "    if plain_shaped and not _turn_requires_live_evidence(original):\n"
                "        return None\n",
                "    plain_shaped = is_ordinary_multi_part_plain_task(original)\n"
                "    if plain_shaped:\n"
                "        return None\n",
            )
        ],
        red_tests=[
            f"{SUITE}::test_served_run_on_repro_answers_every_clause",
            f"{SUITE}::test_plain_shaped_live_data_turn_passes_the_pre_model_gate",
        ],
    ),
    Mutation(
        key="M2",
        title="restore the membership-only lane decline (ignore the coverage probe)",
        path=PLANNER,
        edits=[
            (
                "    if not plan.requires_conductor():\n"
                "        # Every operation is inside the legacy live-data lane's vocabulary. Hand the turn over\n"
                "        # ONLY on per-entity proof that the lane serves every planned node; a membership-only\n"
                "        # decline dropped clauses in production (the lane's whole-text extraction found one\n"
                "        # subtask where this plan holds three). No probe, or a probe that raises, keeps the plan.\n"
                "        covered = False\n"
                "        if lane_coverage_probe is not None:\n"
                "            try:\n"
                "                covered = bool(lane_coverage_probe(plan))\n"
                "            except Exception:\n"
                "                covered = False\n"
                "        if covered:\n"
                "            return None\n",
                "    if not plan.requires_conductor():\n"
                "        return None\n",
            )
        ],
        red_tests=[
            f"{SUITE}::test_lane_served_plan_is_kept_without_coverage_proof",
            f"{SUITE}::test_raising_coverage_probe_fails_closed_to_the_conductor",
            f"{SUITE}::test_run_on_weather_and_gold_stays_with_the_conductor",
        ],
    ),
    Mutation(
        key="M3",
        title="delete the composed 'Could not be answered' section",
        path=COMPOSE,
        edits=[
            (
                "    if unserved:\n"
                "        # Named plainly rather than buried. A request the runtime could not serve is a fact about\n"
                "        # the answer, and a reader who cannot see it will read the rest as complete.\n"
                '        sections.append("Could not be answered:\\n" + "\\n".join(unserved))\n',
                "    if False:\n"
                '        sections.append("Could not be answered:\\n" + "\\n".join(unserved))\n',
            )
        ],
        red_tests=[
            f"{SUITE}::test_unservable_clause_is_named_in_could_not_be_answered",
        ],
    ),
    Mutation(
        key="M4",
        title="drop the post-plan knowledge-only decline for plain-shaped turns",
        path=PLANNER,
        edits=[
            (
                "    if plain_shaped and not _plan_carries_live_effects(plan):\n"
                "        return None\n",
                "    if False and plain_shaped and not _plan_carries_live_effects(plan):\n"
                "        return None\n",
            )
        ],
        red_tests=[
            f"{SUITE}::test_explanation_prose_is_not_conducted",
        ],
    ),
    Mutation(
        key="M5",
        title="weaken the coverage probe to an unconditional yes",
        path=AGENT,
        edits=[
            (
                "        def probe(plan) -> bool:\n"
                "            from core.execution_requirements import requirements_for\n",
                "        def probe(plan) -> bool:\n"
                "            return True\n"
                "            from core.execution_requirements import requirements_for\n",
            ),
        ],
        red_tests=[
            f"{SUITE}::test_run_on_weather_and_gold_stays_with_the_conductor",
        ],
    ),
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_pytest(args: list[str]) -> int:
    proc = subprocess.run(
        [PY, "-m", "pytest", "-q", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    tail = "\n".join((proc.stdout or "").strip().splitlines()[-3:])
    print(f"    pytest exit={proc.returncode}  {tail.splitlines()[-1] if tail else ''}")
    return proc.returncode


def apply_edits(path: Path, edits: list[tuple[str, str]]) -> None:
    text = path.read_text()
    for old, new in edits:
        if old not in text:
            raise SystemExit(f"ANCHOR MISSING in {path.name}: {old[:80]!r}")
        if text.count(old) != 1:
            raise SystemExit(f"ANCHOR NOT UNIQUE in {path.name}: {old[:80]!r}")
        text = text.replace(old, new, 1)
    path.write_text(text)


def main() -> int:
    failures: list[str] = []
    print("== pristine gate before mutations ==")
    if run_pytest([SUITE]) != 0:
        print("FATAL: suite not green before mutations")
        return 2

    for mutation in MUTATIONS:
        targets = [(mutation.path, mutation.edits), *mutation.extra_paths]
        originals = {path: path.read_bytes() for path, _ in targets}
        hashes = {path: sha256(path) for path in originals}
        print(f"== {mutation.key}: {mutation.title} ==")
        try:
            for path, edits in targets:
                apply_edits(path, edits)
            rc = run_pytest(mutation.red_tests)
            if rc == 0:
                failures.append(f"{mutation.key}: SURVIVED (named tests stayed green)")
                print(f"    {mutation.key} SURVIVED — guard or test is weak")
            else:
                print(f"    {mutation.key} caught (RED as required)")
        finally:
            for path, original in originals.items():
                path.write_bytes(original)
            for path, digest in hashes.items():
                if sha256(path) != digest:
                    failures.append(f"{mutation.key}: RESTORE MISMATCH for {path}")
        print(f"    restored byte-identical: {all(sha256(p) == h for p, h in hashes.items())}")

    print("== pristine gate after all mutations ==")
    if run_pytest([SUITE]) != 0:
        failures.append("post-mutation pristine suite is not green")

    if failures:
        print("\nMUTATION MATRIX: FAIL")
        for line in failures:
            print(f"  - {line}")
        return 1
    print("\nMUTATION MATRIX: PASS (M1-M5 all caught, restores byte-identical, suite green)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
