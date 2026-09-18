"""C01 sabotage proof — named reds with byte-exact restore.

Sabotage classes, exactly as the assignment names them:

  S1  SESSION PROPAGATION SEVERED (two variants, both at the join the packs guard):
      S1a the join stops scoping events to the named turn (borrows the whole session window);
      S1b the join stops recording the runtime session identity on the row.
      The identity/scoping pack must go red on both.
  S2  OUTCOME ELIGIBILITY LEAK — the selector's sufficiency adjustment drops the task-kind
      identity filter, letting one task class's observations reorder another's ranking. The
      eligibility pack must go red.

Each sabotage: mutate the working tree -> run the NAMED pack -> require FAILURE (the named
red) -> restore by applying the inverse replacement -> require byte-exactness (sha256 of the
mutated file equal to its pre-sabotage value). Any green sabotage or unclean restore exits
non-zero. Restore never touches git: the lane carries its own uncommitted work.
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PY = sys.executable

JOIN = REPO / "core" / "learning_integration.py"
SUFFICIENCY = REPO / "core" / "learning" / "model_sufficiency.py"

S1A_ORIGINAL = """            event_turn_key = str(event.get("turn_key") or details.get("turn_key") or "").strip()
            if event_turn_key != clean_turn_key:
                continue"""
S1A_SABOTAGED = """            event_turn_key = str(event.get("turn_key") or details.get("turn_key") or "").strip()"""

S1B_ORIGINAL = """            record_sufficiency_observation(
                task_kind=identity["task_kind"] or "unknown",
                provider_id=identity["provider_id"],
                model_id=identity["model_id"],
                outcome=outcome,
                stage_state=state,
                turn_key=clean_turn_key,
                session_id=str(session_id or identity["session_id"] or ""),
            )"""
S1B_SABOTAGED = """            record_sufficiency_observation(
                task_kind=identity["task_kind"] or "unknown",
                provider_id=identity["provider_id"],
                model_id=identity["model_id"],
                outcome=outcome,
                stage_state=state,
                turn_key=clean_turn_key,
                session_id="",
            )"""

S2_ORIGINAL = """        fresh = [
            item
            for item in observations
            if str(item.get("task_kind") or "").strip().lower() == clean_kind
            and str(item.get("provider_id") or "").strip() == clean_provider
            and str(item.get("model_id") or "").strip() == clean_model
            and int(item.get("feedback_version") or 0) == LearningPolicy.FEEDBACK_VERSION
            and (_parse_iso(str(item.get("created_at") or "")) or moment) >= cutoff
        ]"""
S2_SABOTAGED = """        fresh = [
            item
            for item in observations
            if str(item.get("provider_id") or "").strip() == clean_provider
            and str(item.get("model_id") or "").strip() == clean_model
            and int(item.get("feedback_version") or 0) == LearningPolicy.FEEDBACK_VERSION
            and (_parse_iso(str(item.get("created_at") or "")) or moment) >= cutoff
        ]"""


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run_pack(node_id: str) -> int:
    completed = subprocess.run(
        [PY, "-m", "pytest", node_id, "-q", "--no-header", "-x"],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=600,
    )
    return completed.returncode


def sabotage(name: str, path: Path, original: str, sabotaged: str, packs: list[str]) -> bool:
    print(f"\n=== SABOTAGE {name} ===")
    before = _sha(path)
    text = path.read_text(encoding="utf-8")
    if original not in text:
        print(f"FAIL: sabotage {name} did not apply (anchor missing)")
        return False
    path.write_text(text.replace(original, sabotaged), encoding="utf-8")
    reds = True
    try:
        for pack in packs:
            code = _run_pack(pack)
            status = "RED (as required)" if code != 0 else "GREEN (SABOTAGE NOT CAUGHT)"
            print(f"  {pack} -> exit {code}: {status}")
            if code == 0:
                reds = False
    finally:
        restored_text = path.read_text(encoding="utf-8")
        if sabotaged in restored_text:
            path.write_text(restored_text.replace(sabotaged, original), encoding="utf-8")
        restored = _sha(path) == before
        print(f"  restore byte-exact: {restored}")
        if not restored:
            reds = False
    return reds


def main() -> int:
    ok = True
    ok &= sabotage(
        "S1a session propagation severed: join borrows the whole session window",
        JOIN,
        S1A_ORIGINAL,
        S1A_SABOTAGED,
        [
            "tests/test_served_sufficiency_identity.py::TestTheJoinIsScopedToOneTurn::test_other_turns_events_in_the_window_write_nothing",
        ],
    )
    ok &= sabotage(
        "S1b session propagation severed: rows lose the runtime session identity",
        JOIN,
        S1B_ORIGINAL,
        S1B_SABOTAGED,
        [
            "tests/test_served_sufficiency_identity.py::TestTheWriterJoinsTheStoredShape::test_completed_call_and_trace_write_one_eligible_row",
        ],
    )
    ok &= sabotage(
        "S2 outcome-eligibility leak: adjustment ignores task-kind identity",
        SUFFICIENCY,
        S2_ORIGINAL,
        S2_SABOTAGED,
        [
            "tests/test_served_sufficiency_identity.py::TestSelectionConsumesOnlyEligibleRows::test_adjustment_isolates_model_task_and_generation",
        ],
    )
    print("\n=== SABOTAGE VERDICT:", "all named reds fired and restore is byte-exact" if ok else "FAILURE", "===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
