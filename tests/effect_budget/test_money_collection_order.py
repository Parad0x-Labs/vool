"""Package isolation must not depend on the order a run collects files in.

pytest applies a conftest's autouse fixtures by the identity of its directory's collection node. A run
whose arguments left tests/effect_budget for a file of the parent directory and then returned collected
the package again as a new node, and the package's autouse fixtures (the per-test budget store and the
real-home residue guard) silently stopped applying: the money tests shared the session database and 35
of them failed on another test's rows. Each case re-creates such an order in a child pytest and requires
every selected money module's isolation guard to pass.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.effect_budget.conftest import *  # noqa: F403 — fixtures

REPO_ROOT = Path(__file__).resolve().parents[2]

ORDERS = {
    "package-parent-authority": (
        "tests/effect_budget/test_scopes_and_precedence.py",
        "tests/test_cp2_full_chain_interaction.py",
        "tests/effect_budget/test_money_authority.py",
    ),
    "package-parent-gateway-served-crossprocess": (
        "tests/effect_budget/test_authority.py",
        "tests/test_spend_authorization.py",
        "tests/effect_budget/test_money_gateway_and_provider.py",
        "tests/effect_budget/test_money_served_api.py",
        "tests/effect_budget/test_money_cross_process.py",
    ),
}


@pytest.mark.parametrize("order", sorted(ORDERS))
def test_money_isolation_survives_a_collection_order_that_re_enters_the_package(order, tmp_path):
    files = ORDERS[order]
    env = {key: value for key, value in os.environ.items() if not key.startswith("PYTEST_")}
    env.update({"PYTEST_ADDOPTS": "", "PYTHONDONTWRITEBYTECODE": "1", "VOOL_HOME": str(tmp_path / "child-vool-home")})
    command = [
        sys.executable, "-B", "-m", "pytest", "-p", "no:cacheprovider", "-q",
        "-m", "not pa_beta_live and not live", *files, "-k", "runs_on_its_own_isolated_store",
    ]
    completed = subprocess.run(command, cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=600)
    expected = sum(1 for name in files if Path(name).name.startswith("test_money_"))
    tail = (completed.stdout + completed.stderr)[-3000:]
    assert completed.returncode == 0, tail
    assert f"{expected} passed" in completed.stdout, tail
