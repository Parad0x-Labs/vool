"""Workspace coverage must not recursively ask its caller to build an execution plan."""
import json
import subprocess
import sys

import pytest


@pytest.mark.parametrize("prompt", [
    "read alpha.py and bravo.py",
    "open loader.py and mention config.py as its sibling",
    "Return exactly the second line of alpha.txt. Return exactly the first line of bravo.txt.",
])
def test_workspace_read_capability_does_not_refine_execution_units(monkeypatch, prompt):
    from core.agent_runtime import fast_paths_utility
    from core.agent_runtime.demand_ownership import _workspace_read_covers

    def recursive_refinement(*args, **kwargs):
        pytest.fail("Coverage called execution-dependent read-plan refinement")

    monkeypatch.setattr(fast_paths_utility, "_per_unit_line_windows", recursive_refinement)
    assert _workspace_read_covers(prompt)


@pytest.mark.parametrize("prompt", [
    "Fix math.js so the existing test.js passes. Run the existing tests and explain the change briefly. Keep the tests unchanged.",
    "Please repair parser.py to satisfy the tests in checks.py. Execute the existing checks and summarize the correction.",
])
def test_explicit_repair_is_not_finalized_as_a_file_read(prompt):
    from core.agent_runtime.fast_paths_utility import _direct_workspace_read_request
    from core.tool_demand_signals import is_explicit_code_repair

    assert is_explicit_code_repair(prompt)
    assert _direct_workspace_read_request(prompt) is None


@pytest.mark.parametrize("prompt", [
    "read alpha.py and bravo.py",
    "Return exactly the second line of alpha.txt. Return exactly the first line of bravo.txt.",
    "Fix math.js so the existing test.js passes. Run the existing tests and explain the change briefly. Keep the tests unchanged.",
    "Please repair parser.py to satisfy the tests in checks.py. Execute the existing checks and summarize the correction.",
])
def test_cold_request_interpretation_terminates(prompt):
    # A subprocess gives every case an empty interpretation cache and makes a
    # recursive regression fail in bounded time instead of wedging the whole suite.
    code = """
import json, sys
from core.agent_runtime.answer_coverage import interpret_request
from core.agent_runtime.demand_ownership import execution_units
reading = interpret_request(sys.argv[1])
print(json.dumps({'requests': len(reading.requests), 'execution_units': len(execution_units(sys.argv[1]))}))
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", code, prompt],
        capture_output=True, text=True, timeout=15, check=True,
    )
    counts = json.loads(result.stdout.strip().splitlines()[-1])
    assert counts["requests"] >= 1
    assert counts["execution_units"] >= 1
