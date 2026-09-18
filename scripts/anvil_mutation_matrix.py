#!/usr/bin/env python3
"""Real source mutation matrix for the live-data weather entity guards.

Earlier rounds shipped `TestMutation*` classes that re-implemented an old algorithm inside the test
file and asserted the local copy behaved badly. That detects nothing about production -- the real
function could be deleted and those tests would still pass. They were removed.

This harness does the real thing. For each mutation it:
  1. edits the actual production source file on disk,
  2. runs the tests that are supposed to catch that mutation,
  3. classifies STRICTLY by pytest's exit code (see below),
  4. restores every touched file byte-for-byte.

EXIT-CODE SEMANTICS (ANVIL closure, 2026-08-07). The previous version treated any non-zero return
as "caught", which silently awarded a kill for pytest exit 4 (usage error / bad selector) and 5
(no tests collected) -- a typo in a selector scored as a passing mutation. Now:

    0            -> mutation SURVIVED   (tests stayed green; the guard has no real coverage)
    1            -> mutation CAUGHT     (an assertion actually failed)
    anything else-> HARNESS ERROR       (bad selector, collection error, interrupt) -> BLOCKED

A harness error is never a kill. It fails the run.

RESTORE SET. The snapshot set is DERIVED from the mutation definitions (`_paths_to_snapshot`),
not hardcoded, so a new mutation against a third production file is snapshotted and restored
automatically instead of being left dirty while the summary claims success.

Both properties are covered by `--self-test`, which runs automatically before the matrix.

Usage:  .venv/bin/python scripts/anvil_mutation_matrix.py
        .venv/bin/python scripts/anvil_mutation_matrix.py --self-test   (self-tests only)
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WEB_RESEARCH = REPO / "tools" / "web" / "web_research.py"
RENDER = REPO / "core" / "agent_runtime" / "fast_live_info_weather_rendering.py"
RUNNER = REPO / "core" / "agent_runtime" / "live_data_runner.py"
PYTHON = sys.executable

ANVIL = "tests/test_live_data_anvil_closure.py"
FINAL = "tests/test_live_data_anvil_final_closure.py"
LIVE_REG = "tests/test_live_data_live_release_regressions.py"

SURVIVED, CAUGHT = "SURVIVED (green)", "caught (RED)"


@dataclass(frozen=True)
class Mutation:
    """One production edit, and the tests that must go RED because of it."""

    name: str
    path: Path
    old: str
    new: str
    tests: list[str] = field(default_factory=list)


MUTATIONS: list[Mutation] = [
    # --- B1: place-name collisions --------------------------------------------------------
    Mutation(
        name="1. reintroduce the My Tho collision (add 'my' back to the token scan)",
        path=WEB_RESEARCH,
        old='    "it", "they",',
        new='    "it", "they", "my",',
        tests=[f"{FINAL}::TestPlaceNamesThatCollideWithPronouns",
               f"{ANVIL}::TestEveryPronounAndPossessiveIsGuarded"],
    ),
    Mutation(
        name="2. reintroduce the She/He place collision",
        path=WEB_RESEARCH,
        old='    "it", "they",',
        new='    "it", "they", "she", "he",',
        tests=[f"{FINAL}::TestPlaceNamesThatCollideWithPronouns",
               f"{ANVIL}::TestEveryPronounAndPossessiveIsGuarded"],
    ),
    # --- B2: each half of the render gate --------------------------------------------------
    Mutation(
        name="3. plausibility gate removed, marker gate intact",
        path=RENDER,
        old="        not _is_plausible_weather_location(location) or _has_clause_structure_marker(location)",
        new="        _has_clause_structure_marker(location)",
        tests=[f"{FINAL}::TestRenderGateHalvesAreSeparatelyLoadBearing"],
    ),
    Mutation(
        name="4. marker gate removed, plausibility gate intact",
        path=RENDER,
        old="        not _is_plausible_weather_location(location) or _has_clause_structure_marker(location)",
        new="        not _is_plausible_weather_location(location)",
        tests=[f"{FINAL}::TestRenderGateHalvesAreSeparatelyLoadBearing"],
    ),
    # --- B3: time-qualified locations ------------------------------------------------------
    Mutation(
        name="5. colon/time handling broken (time qualifier never stripped)",
        path=WEB_RESEARCH,
        old='    stripped = _TIME_QUALIFIER_SUFFIX_RE.sub("", str(text or "")).strip()',
        new='    stripped = str(text or "").strip()',
        tests=[f"{FINAL}::TestTimeQualifiedLocations"],
    ),
    # --- B4: long legitimate places --------------------------------------------------------
    Mutation(
        name="6. long legitimate place handling broken (flat cap restored)",
        path=WEB_RESEARCH,
        old="    max_words, max_chars = (10, 90) if earns_extra_room else (6, 60)",
        new="    max_words, max_chars = (6, 60)",
        tests=[f"{FINAL}::TestLongLegitimateVersusLongInstruction"],
    ),
    Mutation(
        name="6b. long-place allowance granted without the structure test",
        path=WEB_RESEARCH,
        old="    max_words, max_chars = (10, 90) if earns_extra_room else (6, 60)",
        new="    max_words, max_chars = (10, 90)",
        tests=[f"{FINAL}::TestLongLegitimateVersusLongInstruction"],
    ),
    # --- before-fetch guard ----------------------------------------------------------------
    Mutation(
        name="7. before-fetch validation bypassed in the runner",
        path=RUNNER,
        old="    if not _is_plausible_weather_location(location):",
        new="    if False:",
        tests=[f"{FINAL}::TestProviderIsNotCalledForAnUntrustedEntity"],
    ),
    # --- abbreviation handling -------------------------------------------------------------
    Mutation(
        name="8. restore the <=3-letter abbreviation heuristic",
        path=WEB_RESEARCH,
        old="            word.lower() in _PLACE_NAME_ABBREVIATIONS\n            or (word.isalpha() and len(word) == 1)\n            or word.isdigit()",
        new="            (word.isalpha() and len(word) <= 3)\n            or word.isdigit()",
        tests=[f"{LIVE_REG}::TestExactLiveRegressions",
               f"{LIVE_REG}::TestAbbreviationRuleAsksTheRightQuestion"],
    ),
    Mutation(
        name="9. remove 'sta' from the abbreviation set",
        path=WEB_RESEARCH,
        old='    "sta",   # Santa          -- "Sta. Barbara"\n',
        new="",
        tests=[f"{ANVIL}::TestStaSteRegression", f"{ANVIL}::TestEveryAbbreviationIsGuarded"],
    ),
    Mutation(
        name="10. remove 'ste' from the abbreviation set",
        path=WEB_RESEARCH,
        old='    "ste",   # Sainte         -- "Ste. Genevieve"\n',
        new="",
        tests=[f"{ANVIL}::TestStaSteRegression", f"{ANVIL}::TestEveryAbbreviationIsGuarded"],
    ),
    Mutation(
        name="10b. remove a previously untested abbreviation (blvd)",
        path=WEB_RESEARCH,
        old='    "blvd",  # Boulevard\n',
        new="",
        tests=[f"{ANVIL}::TestEveryAbbreviationIsGuarded"],
    ),
    Mutation(
        name="10c. remove a previously untested abbreviation (hwy)",
        path=WEB_RESEARCH,
        old='    "hwy",   # Highway\n',
        new="",
        tests=[f"{ANVIL}::TestEveryAbbreviationIsGuarded"],
    ),
    # --- marker-scan mechanics -------------------------------------------------------------
    Mutation(
        name="11. punctuation stripping removed from the marker scan",
        path=WEB_RESEARCH,
        old='        if raw_token.strip(".,;:!?\'\\"()").lower() in _CLAUSE_STRUCTURE_MARKERS:',
        new="        if raw_token.lower() in _CLAUSE_STRUCTURE_MARKERS:",
        tests=[f"{LIVE_REG}::TestExactLiveRegressions", f"{FINAL}::TestContinuationsStayContained"],
    ),
    Mutation(
        name="12. confident-only marker rejection",
        path=WEB_RESEARCH,
        old='        if _has_clause_structure_marker(" ".join(tokens)):',
        new='        if not confident and _has_clause_structure_marker(" ".join(tokens)):',
        tests=[f"{ANVIL}::TestStructureMarkersApplyRegardlessOfConfidence"],
    ),
    Mutation(
        name="13. remove 'it' from the closed class",
        path=WEB_RESEARCH,
        old='    "it", "they",',
        new='    "they",',
        tests=[f"{LIVE_REG}::TestExactLiveRegressions", f"{FINAL}::TestContinuationsStayContained"],
    ),
    Mutation(
        name="14. remove the demonstratives (these/those)",
        path=WEB_RESEARCH,
        old='    "these", "those",\n',
        new="",
        tests=[f"{ANVIL}::TestShortInstructionsDoNotRenderAsLocations",
               f"{FINAL}::TestRenderGateHalvesAreSeparatelyLoadBearing"],
    ),
    Mutation(
        name="15. bare-pronoun rejection removed",
        path=WEB_RESEARCH,
        old="    if lowered in _BARE_PRONOUN_TOKENS:\n        return False",
        new="    if False:\n        return False",
        tests=[f"{ANVIL}::TestEveryPronounAndPossessiveIsGuarded",
               f"{ANVIL}::TestShortInstructionsDoNotRenderAsLocations"],
    ),
    # --- plausibility predicate, one mutation per remaining arm ----------------------------
    # NOTE: the predicate's former `if not text: return False` arm is deliberately absent -- it no
    # longer exists. This harness proved it was not load-bearing (bypassing it left every empty
    # input still rejected by the "must contain a letter" arm), so rather than ship an arm no
    # regression could defend, it was removed from production.
    Mutation(
        name="16. plausibility arm: structural characters",
        path=WEB_RESEARCH,
        old='    if any(ch in text for ch in "[]{}\\n\\r\\t|"):\n        return False',
        new="    if False:\n        return False",
        tests=[f"{ANVIL}::TestEveryPlausibilityArmHasARegression::test_arm_structural_characters"],
    ),
    Mutation(
        name="17. plausibility arm: colon",
        path=WEB_RESEARCH,
        old='    if ":" in text:\n        return False',
        new="    if False:\n        return False",
        tests=[f"{ANVIL}::TestEveryPlausibilityArmHasARegression::test_arm_colon"],
    ),
    Mutation(
        name="18. plausibility arm: gmt",
        path=WEB_RESEARCH,
        old='    if "gmt" in lowered or "queued user message" in lowered:',
        new='    if "queued user message" in lowered:',
        tests=[f"{ANVIL}::TestEveryPlausibilityArmHasARegression::test_arm_gmt"],
    ),
    Mutation(
        name="19. plausibility arm: queued user message",
        path=WEB_RESEARCH,
        old='    if "gmt" in lowered or "queued user message" in lowered:',
        new='    if "gmt" in lowered:',
        tests=[f"{ANVIL}::TestEveryPlausibilityArmHasARegression::test_arm_queued_user_message"],
    ),
    Mutation(
        name="20. plausibility arm: max length",
        path=WEB_RESEARCH,
        old="    if len(text) > max_chars or word_count > max_words:",
        new="    if word_count > max_words:",
        tests=[f"{ANVIL}::TestEveryPlausibilityArmHasARegression::test_arm_max_length"],
    ),
    Mutation(
        name="21. plausibility arm: max words",
        path=WEB_RESEARCH,
        old="    if len(text) > max_chars or word_count > max_words:",
        new="    if len(text) > max_chars:",
        tests=[f"{ANVIL}::TestEveryPlausibilityArmHasARegression::test_arm_max_words"],
    ),
    Mutation(
        name="22. plausibility arm: requires a letter",
        path=WEB_RESEARCH,
        old="    return any(ch.isalpha() for ch in text)",
        new="    return True",
        tests=[f"{ANVIL}::TestEveryPlausibilityArmHasARegression::test_arm_requires_a_letter"],
    ),
]


def _paths_to_snapshot(mutations: list[Mutation]) -> set[Path]:
    """Every production file the given mutations touch. Derived, never hardcoded -- a mutation
    against a new file must be snapshotted (and therefore restored) automatically."""
    return {mutation.path for mutation in mutations}


def run_pytest(selectors: list[str]) -> int:
    return subprocess.run(
        [PYTHON, "-m", "pytest", "-x", "-q", "-p", "no:randomly", *selectors],
        cwd=REPO, capture_output=True, text=True,
    ).returncode


def classify(returncode: int) -> str:
    """0 -> survived, 1 -> caught, anything else -> harness error. Never award a kill for a
    selector/collection failure."""
    if returncode == 0:
        return SURVIVED
    if returncode == 1:
        return CAUGHT
    return f"HARNESS ERROR (pytest exit {returncode})"


def self_test() -> list[str]:
    """Prove the two properties ANVIL required. Returns a list of failure messages."""
    failures: list[str] = []

    # 1. A broken selector must NOT be scored as a mutation kill.
    bogus = run_pytest([f"{ANVIL}::TestThisClassDoesNotExist"])
    verdict = classify(bogus)
    if verdict == CAUGHT:
        failures.append(f"a bad selector scored as CAUGHT (pytest exit {bogus}) -- exit semantics broken")
    elif not verdict.startswith("HARNESS ERROR"):
        failures.append(f"a bad selector produced {verdict!r} (pytest exit {bogus}), expected HARNESS ERROR")

    # 2. A real assertion failure must be scored CAUGHT (exit 1), so the check above is not
    #    passing merely because everything is classified as an error.
    genuine = classify(run_pytest([f"{FINAL}::TestPlaceNamesThatCollideWithPronouns"]))
    if genuine != SURVIVED:
        failures.append(f"a passing selection scored {genuine!r}, expected {SURVIVED!r}")

    # 3. The restore set must follow the mutations, including to a file not otherwise touched.
    third_file = REPO / "core" / "agent_runtime" / "live_data_plan.py"
    synthetic = [*MUTATIONS, Mutation(name="synthetic", path=third_file, old="x", new="y")]
    if third_file not in _paths_to_snapshot(synthetic):
        failures.append("restore set does not follow mutation definitions -- a new target would be left dirty")
    if _paths_to_snapshot(MUTATIONS) != {m.path for m in MUTATIONS}:
        failures.append("restore set is not derived from the live mutation list")

    return failures


def main() -> int:
    if failures := self_test():
        print("HARNESS SELF-TEST FAILED:")
        for failure in failures:
            print(f"  - {failure}")
        return 3
    print("harness self-test: OK (bad selector -> error not kill; restore set follows mutations)")

    originals = {path: path.read_text() for path in _paths_to_snapshot(MUTATIONS)}
    results: list[tuple[str, str]] = []
    survivors: list[str] = []
    errors: list[str] = []
    try:
        for mutation in MUTATIONS:
            source = originals[mutation.path]
            if mutation.old not in source:
                results.append((mutation.name, "HARNESS ERROR (target not found)"))
                errors.append(mutation.name)
                continue
            mutation.path.write_text(source.replace(mutation.old, mutation.new, 1))
            try:
                verdict = classify(run_pytest(mutation.tests))
            finally:
                mutation.path.write_text(source)
            results.append((mutation.name, verdict))
            if verdict == SURVIVED:
                survivors.append(mutation.name)
            elif verdict != CAUGHT:
                errors.append(mutation.name)
    finally:
        for path, text in originals.items():
            path.write_text(text)

    width = max(len(name) for name, _ in results)
    print("\nANVIL MUTATION MATRIX")
    print("=" * (width + 26))
    for name, verdict in results:
        print(f"{name.ljust(width)}  {verdict}")
    print("=" * (width + 26))
    print(f"{len(results)} mutations, {len(survivors)} survivor(s), {len(errors)} harness error(s)")
    print(f"files snapshotted: {', '.join(sorted(str(p.relative_to(REPO)) for p in originals))}")

    unrestored = [str(p.relative_to(REPO)) for p, text in originals.items() if p.read_text() != text]
    print(f"sources restored: {'YES' if not unrestored else 'NO -- ' + ', '.join(unrestored)}")
    if unrestored:
        return 2
    if errors:
        print("\nHARNESS ERRORS (not kills):")
        for name in errors:
            print(f"  - {name}")
        return 3
    if survivors:
        print("\nSURVIVORS (no test detects these):")
        for name in survivors:
            print(f"  - {name}")
        return 1
    return 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        problems = self_test()
        for problem in problems:
            print(f"FAIL: {problem}")
        print("self-test: " + ("FAILED" if problems else "OK"))
        raise SystemExit(3 if problems else 0)
    raise SystemExit(main())
