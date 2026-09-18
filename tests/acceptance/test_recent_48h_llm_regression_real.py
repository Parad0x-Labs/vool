from __future__ import annotations

from pathlib import Path

from core.llm_eval.pack import collect_recent_llm_inventory, run_pytest_pack


def test_recent_inventory_reports_llm_sections() -> None:
    repo_root = Path(__file__).resolve().parents[2]

    inventory = collect_recent_llm_inventory(repo_root, since_hours=48)

    assert "tests" in inventory
    assert "scripts" in inventory
    assert "workflows" in inventory
    assert isinstance(inventory["relevant_paths"], list)


def test_recent_regression_pack_runner_executes_real_pytest_target() -> None:
    repo_root = Path(__file__).resolve().parents[2]

    result = run_pytest_pack(
        name="acceptance_recent_smoke",
        repo_root=repo_root,
        targets=["tests/test_run_local_acceptance.py::test_load_profile_reads_locked_local_bundle_profile"],
    )

    assert result["exit_code"] == 0
    assert result["summary"]["passed"] >= 1
