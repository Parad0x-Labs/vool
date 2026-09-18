"""Prove the execution-truth gate is load-bearing by really breaking the runtime.

A gate is only worth its name if removing the thing it guards makes it fail. This deliberately
drops the production call that turns a real execution into an authoritative fact, runs the suite,
and requires it to go RED.

Two things are checked that a naive sabotage skips, both of which produce a GREEN run that reads as
a surviving guard:

* that the mutation actually LANDED -- a string replace matching nothing leaves the file untouched
  and the suite passes for the most reassuring possible wrong reason;
* that the file is byte-identical again afterwards, so a later run is measuring production code.

    python tools/truth/sabotage_execution_truth.py
"""
from __future__ import annotations

import hashlib
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]

# Each sabotage: the file, the exact production text to break, what to replace it with, and the
# invariant it should destroy.
SABOTAGES = [
    (
        "core/runtime_task_events.py",
        "            _execution_truth().note_runtime_event(",
        "            _DROPPED_BY_SABOTAGE = lambda **_: None; _DROPPED_BY_SABOTAGE(",
        "an executed tool never becomes an authoritative fact",
    ),
    (
        "core/agent_runtime/action_honesty_validator.py",
        "        for entry in executed_tools(turn_key, session_id=clean):",
        "        for entry in []:",
        "the signed honesty ledger stops deriving from the execution ledger",
    ),
    (
        "core/execution_truth.py",
        '        if resolve_turn_key(None, dict(event)) != turn:',
        '        if False:',
        "the gate stops scoping the witness to the turn under test",
    ),
    # The two defects the live product drives found, which the deterministic tests had not modelled.
    (
        "core/execution_truth.py",
        '    ("turn_key", "both"),\n',
        "",
        "a reader re-derives identity instead of taking the stamped turn key",
    ),
    (
        "core/execution_truth.py",
        '    ("turn_id", "context"),',
        '    ("turn_id", "details"),',
        "a lane-local turn id outranks the ambient one and splits the turn",
    ),
    (
        "core/runtime_task_events.py",
        '        payload.setdefault("turn_key", turn_key)',
        "        pass",
        "events stop carrying the canonical turn identity",
    ),
]

TESTS = "tests/test_execution_truth_is_single_sourced.py"


def _sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run_tests() -> tuple[int, str]:
    proc = subprocess.run(
        [".venv/bin/python", "-m", "pytest", "-q", "-p", "no:cacheprovider", TESTS],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={"PYTHONPATH": ".", "PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(pathlib.Path.home())},
    )
    return proc.returncode, (proc.stdout + proc.stderr)[-2500:]


def main() -> int:
    print(f"BASELINE — production code, {TESTS}")
    code, output = _run_tests()
    print(output.strip().splitlines()[-1] if output.strip() else "(no output)")
    if code != 0:
        print("\nFAIL: the suite is not green BEFORE sabotage. Nothing below would mean anything.")
        return 1

    failures = 0
    for rel, needle, replacement, invariant in SABOTAGES:
        path = ROOT / rel
        original = path.read_text(encoding="utf-8")
        before = _sha(path)
        print(f"\n{'=' * 78}\nSABOTAGE  {rel}\n  breaks: {invariant}")

        if needle not in original:
            print(f"  ABORT: anchor not found -- the sabotage would be vacuous.\n    {needle!r}")
            failures += 1
            continue

        path.write_text(original.replace(needle, replacement, 1), encoding="utf-8")
        after = _sha(path)
        if after == before:
            print("  ABORT: file unchanged after write -- mutation did not land.")
            path.write_text(original, encoding="utf-8")
            failures += 1
            continue
        print(f"  mutation landed: {before[:12]} -> {after[:12]}")

        try:
            code, output = _run_tests()
            summary = output.strip().splitlines()[-1] if output.strip() else "(no output)"
            if code == 0:
                print(f"  RESULT: SURVIVED — tests still pass. THE GUARD IS NOT LOAD-BEARING.\n    {summary}")
                failures += 1
            else:
                named = [
                    line.split("::")[-1].split()[0]
                    for line in output.splitlines()
                    if line.startswith("FAILED")
                ]
                print(f"  RESULT: CAUGHT — {summary}")
                for name in named[:6]:
                    print(f"    failed: {name}")
        finally:
            path.write_text(original, encoding="utf-8")
            restored = _sha(path)
            if restored != before:
                print(f"  WARNING: restore mismatch {before[:12]} != {restored[:12]}")
                failures += 1
            else:
                print(f"  restored: {restored[:12]} (byte-identical)")

    print(f"\n{'=' * 78}")
    if failures:
        print(f"SABOTAGE PROOF FAILED: {failures} sabotage(s) survived or could not be applied.")
        return 1
    print("SABOTAGE PROOF PASSED: every sabotage was caught, every file restored byte-identical.")

    code, _ = _run_tests()
    print(f"post-restore suite: {'green' if code == 0 else 'RED — production code did not come back'}")
    return 0 if code == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
