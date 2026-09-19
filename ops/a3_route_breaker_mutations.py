#!/usr/bin/env python3
"""A3 route-breaker mutation runner (M1–M13) — sabotage proof for the tool-loop
no-eligible-tool gate.

For every load-bearing guard in ``research_tool_loop_facade.py`` this runner:
  1. mutates the REAL production guard in place (line window or targeted string edit),
  2. runs the named liveness fence and expects it to FAIL (RED),
  3. restores the file from the in-memory pristine copy.

This is NOT a pytest module — it exits non-zero at module scope and mutates
production source. It lives in ``ops/`` so pytest never collects it; collecting it
raised ``INTERNALERROR: SystemExit`` and aborted the entire suite.

Usage:  python ops/a3_route_breaker_mutations.py
        (paths resolve from this file's own repo root, so it always sabotages the
        checkout it is run from — never another worktree)
Exit 0 = every mutation was caught by its fence; exit 1 otherwise.
"""

import os
import subprocess
import sys

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON = os.path.join(PROJECT, ".venv", "bin", "python")
FACADE = os.path.join(PROJECT, "core/agent_runtime/research_tool_loop_facade.py")
TEST_FILE = os.path.join(PROJECT, "tests/test_tool_loop_liveness.py")


def _readlines(path):
    with open(path) as f: return f.readlines()
def _invalidate_bytecode(path):
    """Drop the cached .pyc for ``path`` so the next import re-reads the source.

    Every mutation here inserts the same 11 characters (" False and " / a same-
    length comment prefix), so two DIFFERENT mutations routinely produce files of
    IDENTICAL size.  CPython validates a .pyc on (source mtime in whole seconds,
    source size) — so when two mutations land inside the same second, the second
    pytest run silently imports the FIRST mutation's bytecode and scores a verdict
    for code that is not on disk.  Observed live on 2026-08-29: M5 reported PASS
    while actually executing M2's mutated gate.
    """
    cache = os.path.join(os.path.dirname(path), "__pycache__")
    stem = os.path.splitext(os.path.basename(path))[0] + "."
    if os.path.isdir(cache):
        for name in os.listdir(cache):
            if name.startswith(stem):
                os.remove(os.path.join(cache, name))


def _writelines(path, lines):
    with open(path, "w") as f: f.writelines(lines)
    _invalidate_bytecode(path)
def synth(path):
    r = subprocess.run([PYTHON, "-c", f"import ast;ast.parse(open('{path}').read())"],
                       capture_output=True, text=True)
    return r.returncode == 0
def run(test):
    r = subprocess.run([PYTHON, "-m", "pytest", f"{TEST_FILE}::{test}", "-x", "-v"],
                       capture_output=True, text=True, timeout=30)
    return r.returncode


SYNTH_ERROR = -1  # sentinel: the mutated file did not parse, so no fence ran


def mutate(lines, anchor, replacement, tag):
    """Replace the one line containing ``anchor``; abort if it is not there.

    Every mutation anchors on production text rather than a line number.  M1/M2
    previously blanked a hard-coded range (962-993); when the facade shifted that
    range would comment out unrelated code, and the resulting parse error was
    scored as a pass.  A missing anchor is now a hard abort, not a silent no-op.
    """
    for i, ln in enumerate(lines):
        if anchor in ln:
            lines[i] = replacement
            return i
    print(f"  x ABORT ({tag}): anchor not found in facade: {anchor!r}")
    print("    Re-anchor this mutation before trusting the run.")
    sys.exit(2)


def score(tag, code):
    """Record one mutation's verdict.

    A mutation that leaves the facade unparseable is VACUOUS, not RED.  synth()
    short-circuits run(), pytest never launches, and the old scoring counted the
    resulting ``code != 0`` as a pass — which is exactly how M1, M2 and M5
    reported green without ever exercising a fence (CLAUDE.md 6b.4: a mutation on
    a seam the tests never reach is not proof).
    """
    if code == SYNTH_ERROR:
        print("  x VACUOUS (mutated facade does not parse; pytest never ran)")
        fail.append(f"{tag}: VACUOUS — no fence was exercised")
    elif code == 0:
        print("  x NOT RED (exit=0) — the fence did not notice this sabotage")
        fail.append(f"{tag}: NOT RED")
    else:
        print(f"  RED (exit={code})")


GATE_CALL = "if not pending_tool_payload and self._no_eligible_tool_detected("

P = _readlines(FACADE)
fail = []

print("="*60)
print("SABOTAGE TESTS M1–M8")
print("="*60)

# M1 – gate removal → L7 expects no_fit events → RED
print("\n--- M1: remove no-fit termination ---")
L = list(P)
# Short-circuit the gate's call site: `False and ...` never evaluates the probe,
# so the gate cannot fire, and the multi-line call below stays syntactically whole.
mutate(L, GATE_CALL,
       "            if False and not pending_tool_payload and self._no_eligible_tool_detected(\n",
       "M1")
_writelines(FACADE, L)
code = run("test_L7_identifier_canary_fast") if synth(FACADE) else SYNTH_ERROR
_writelines(FACADE, P)
score("M1", code)

# M2 – gate removal → L12 (12 scripts, dependency_resolution) → runs all 12 → RED
print("\n--- M2: ignore identical no-progress state ---")
L = list(P)
# Short-circuit the gate's call site: `False and ...` never evaluates the probe,
# so the gate cannot fire, and the multi-line call below stays syntactically whole.
mutate(L, GATE_CALL,
       "            if False and not pending_tool_payload and self._no_eligible_tool_detected(\n",
       "M2")
_writelines(FACADE, L)
code = run("test_L12_identical_state_terminates") if synth(FACADE) else SYNTH_ERROR
_writelines(FACADE, P)
score("M2", code)

# M3 – remove task_class scope AND lower threshold → any single tool triggers
print("\n--- M3: terminate after ANY single tool failure ---")
L = list(P)
for i, ln in enumerate(L):
    if 'if task_class not in {"dependency_resolution", "config"}:' in ln:
        L[i] = "        if False:  # M3: remove scope, apply to ALL task_classes\n"
        break
for i, ln in enumerate(L):
    if "if len(executed_steps) < 2:" in ln:
        L[i] = "        if len(executed_steps) < 1:\n"
        break
_writelines(FACADE, L)
code = run("test_L4_first_fails_second_continues") if synth(FACADE) else SYNTH_ERROR
_writelines(FACADE, P)
score("M3", code)

# M4 – no_eligible_tool not terminal → L9 checks success=False → RED
print("\n--- M4: no_eligible_tool not terminal failure ---")
L = list(P)
for i, ln in enumerate(L):
    if 'terminal_failure = bool(reject_reason) or loop_stop_reason' in ln:
        L[i] = "        terminal_failure = bool(reject_reason) or loop_stop_reason in {\"step_budget_exhausted\"}  # M4\n"
        break
_writelines(FACADE, L)
code = run("test_L9_no_fit_does_not_claim_success") if synth(FACADE) else SYNTH_ERROR
_writelines(FACADE, P)
score("M4", code)

# M5 – always enter tool loop → L6 zero-dispatch → RED
print("\n--- M5: no-tools policy enters tool loop ---")
L = list(P)
# Same short-circuit shape: the old edit replaced only the `if` header and left
# its argument lines and closing paren dangling -> SyntaxError, so L6 never ran.
mutate(L, "if not self._should_attempt_tool_intent(",
       "        if False and not self._should_attempt_tool_intent(\n",
       "M5")
_writelines(FACADE, L)
code = run("test_L6_explicit_no_tools_zero_dispatch") if synth(FACADE) else SYNTH_ERROR
_writelines(FACADE, P)
score("M5", code)

# M6 – disable the no-fit gate (stub _no_eligible_tool_detected to return False)
# while keeping the round budget intact.  test_M6_structural_termination_not_budget
# asserts <6 rounds with the gate.  Without the gate, the loop runs all 8 scripts
# (≥9 rounds) → RED.
print("\n--- M6: structural termination disabled, budget-only backstop ---")
L = list(P)
for i, ln in enumerate(L):
    if 'if task_class not in {"dependency_resolution", "config"}:' in ln:
        # Replace the scope check with a flat return False (disable entire method)
        L[i] = "        return False  # M6: disable gate\n"
        # Remove the next line (the original return False under the if)
        if i + 1 < len(L) and 'return False' in L[i + 1]:
            L.pop(i + 1)
        break
_writelines(FACADE, L)
code = run("test_M6_structural_termination_not_budget") if synth(FACADE) else SYNTH_ERROR
_writelines(FACADE, P)
score("M6", code)

# M7 – remove scope, bypass intent check, and always return True → breaks L4
print("\n--- M7: stale no-fit blocks reconsideration ---")
L = list(P)
for i, ln in enumerate(L):
    if 'if task_class not in {"dependency_resolution", "config"}:' in ln:
        L[i] = "        if False:  # M7: remove scope\n"
        break
for i, ln in enumerate(L):
    if 'if len(last_intents) != 1:' in ln:
        L[i] = "        if False:  # M7: bypass intent check\n"
        break
for i, ln in enumerate(L):
    if "if not reqs.tools_required:" in ln and "return True" in L[i+1]:
        L[i] = "            if True:  # M7: always True\n"
        break
_writelines(FACADE, L)
code = run("test_L4_first_fails_second_continues") if synth(FACADE) else SYNTH_ERROR
_writelines(FACADE, P)
score("M7", code)

# M8 – bypass intent detection → L7 expects no_fit events → RED
print("\n--- M8: bypass intent cycle detection ---")
L = list(P)
for i, ln in enumerate(L):
    if 'if len(last_intents) != 1:' in ln:
        L[i] = "        if True:  # M8: never detect cycle\n"
        break
_writelines(FACADE, L)
code = run("test_L7_identifier_canary_fast") if synth(FACADE) else SYNTH_ERROR
_writelines(FACADE, P)
score("M8", code)


# M9 – always return True from gate (breaks P13: candidate change should continue)
print("\n--- M9: always return True from gate ---")
L = list(P)
for i, ln in enumerate(L):
    if 'if task_class not in {"dependency_resolution", "config"}:' in ln:
        L[i] = "        return True  # M9: always terminate\n"
        if i + 1 < len(L) and 'return False' in L[i + 1]:
            L.pop(i + 1)
        break
if synth(FACADE):
    _writelines(FACADE, L)
    code = run("test_P13_changed_candidates_continue")
    _writelines(FACADE, P)
    score("M9", code)
else:
    _writelines(FACADE, P)
    fail.append("M9: VACUOUS — mutated facade does not parse")

# M10 – always return True from gate (breaks P14: different intents should continue)
print("\n--- M10: always return True from gate ---")
L = list(P)
for i, ln in enumerate(L):
    if 'if task_class not in {"dependency_resolution", "config"}:' in ln:
        L[i] = "        return True  # M10: always terminate\n"
        if i + 1 < len(L) and 'return False' in L[i + 1]:
            L.pop(i + 1)
        break
if synth(FACADE):
    _writelines(FACADE, L)
    code = run("test_P14_changed_intent_continue")
    _writelines(FACADE, P)
    score("M10", code)
else:
    _writelines(FACADE, P)
    fail.append("M10: VACUOUS — mutated facade does not parse")

# M11 – never terminate from gate (breaks P15: identical state should terminate)
print("\n--- M11: never terminate from gate ---")
L = list(P)
for i, ln in enumerate(L):
    if 'if task_class not in {"dependency_resolution", "config"}:' in ln:
        L[i] = "        return False  # M11: never terminate\n"
        if i + 1 < len(L) and 'return False' in L[i + 1]:
            L.pop(i + 1)
        break
# Also need to block the "return True" path via reqs.tools_required
for i, ln in enumerate(L):
    if 'if not reqs.tools_required:' in ln and 'return True' in L[i+1]:
        L[i] = "            if False:  # M11: never return True\n"
        break
if synth(FACADE):
    _writelines(FACADE, L)
    code = run("test_P15_identical_state_terminates")
    _writelines(FACADE, P)
    score("M11", code)
else:
    _writelines(FACADE, P)
    fail.append("M11: VACUOUS — mutated facade does not parse")

# M12 – remove observation fingerprint check → P14A expects changed obs continues → RED
print("\n--- M12: remove observation fingerprint check ---")
L = list(P)
for i, ln in enumerate(L):
    if 'obs_fps = (source_context or {}).get(\"_observation_fingerprints\")' in ln:
        start = i - 1
        end = i
        for j in range(i, min(i+30, len(L))):
            if 'Same intent, same outcome, same candidates, same observation' in L[j]:
                end = j
                break
        for j in range(start, end):
            L[j] = f"# M12: {L[j]}"
        break
if synth(FACADE):
    _writelines(FACADE, L)
    code = run("test_P14A_changed_observation_continue")
    _writelines(FACADE, P)
    score("M12", code)
else:
    _writelines(FACADE, P)
    fail.append("M12: VACUOUS — mutated facade does not parse")

# M13 – same mutation as M12, different test (P14B)
print("\n--- M13: remove observation fingerprint check (P14B) ---")
L = list(P)
for i, ln in enumerate(L):
    if 'obs_fps = (source_context or {}).get(\"_observation_fingerprints\")' in ln:
        start = i - 1
        end = i
        for j in range(i, min(i+30, len(L))):
            if 'Same intent, same outcome, same candidates, same observation' in L[j]:
                end = j
                break
        for j in range(start, end):
            L[j] = f"# M13: {L[j]}"
        break
if synth(FACADE):
    _writelines(FACADE, L)
    code = run("test_P14B_changed_rejection_continue")
    _writelines(FACADE, P)
    score("M13", code)
else:
    _writelines(FACADE, P)
    fail.append("M13: VACUOUS — mutated facade does not parse")

_writelines(FACADE, P)
print("\n"+"="*60)
if fail:
    print(f"FAILURES: {'; '.join(fail)}")
    sys.exit(1)
print("ALL ACTIVE MUTATIONS VERIFIED RED")
print("M9-M13: same-scope progress identity sabotages verified")
sys.exit(0)
