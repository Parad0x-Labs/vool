from __future__ import annotations

import json
import subprocess
from argparse import Namespace
from pathlib import Path

import ops.llm_eval as llm_eval
from core.llm_eval.pack import collect_recent_llm_inventory

PROFILE_ID = "local-bundle-ollama-v1"
PROFILE_NAME = "VOOL local acceptance for the hardware-aware local Ollama bundle"
PRIMARY_MODEL = "qwen3:8b"
BUNDLE_MODELS = ("qwen3:8b", "deepseek-r1:8b")


def test_default_profile_path_tracks_local_acceptance_canonical_profile() -> None:
    assert llm_eval.DEFAULT_PROFILE_PATH == llm_eval.local_acceptance.DEFAULT_PROFILE_PATH
    assert llm_eval.DEFAULT_PROFILE_PATH.name == "local_ollama_bundle_profile.json"


def _fake_online_payload(*, failing: bool) -> dict[str, object]:
    p0_pass = not failing
    consistency_runs = [
        {"latency_seconds": 0.5, "pass": True, "assistant_text": "", "raw_response_text": ""}
        for _ in range(3)
    ]
    return {
        "captured_at_utc": "2026-03-27T00:00:00Z",
        "model": PRIMARY_MODEL,
        "selected_models": list(BUNDLE_MODELS),
        "profile": {
            "id": PROFILE_ID,
            "display_name": PROFILE_NAME,
            "benchmark_model": PRIMARY_MODEL,
            "benchmark_bundle_models": list(BUNDLE_MODELS),
            "runtime_selected_models": list(BUNDLE_MODELS),
        },
        "runtime_version": {"commit": "abc123", "build_id": "test-build"},
        "machine": {"platform": "macOS", "cpu": "Apple M4", "ram_gb": 24.0, "gpu": "Apple M4"},
        "results": {
            "P0.1a_boot_hello": {"latency_seconds": 4.0, "pass": p0_pass, "assistant_text": "hello", "raw_response_text": ""},
            "P0.1b_capabilities": {"latency_seconds": 4.0, "pass": True, "assistant_text": "workspace", "raw_response_text": ""},
            "P0.2_local_file_create": {"latency_seconds": 0.6, "pass": True, "assistant_text": "", "raw_response_text": ""},
            "P0.3_append": {"latency_seconds": 0.6, "pass": True, "assistant_text": "", "raw_response_text": ""},
            "P0.3b_readback": {"latency_seconds": 0.6, "pass": True, "assistant_text": "", "raw_response_text": ""},
            "P0.5_tool_chain": {"latency_seconds": 0.8, "pass": True, "assistant_text": "", "raw_response_text": ""},
            "P0.6_logic": {"latency_seconds": 4.0, "pass": True, "assistant_text": "58 30", "raw_response_text": ""},
            "P0.4_live_lookup": {"latency_seconds": 0.2, "pass": True, "assistant_text": "Bitcoin is $70,576.00 USD. Source: CoinGecko.", "raw_response_text": ""},
            "P0.7_honesty_online": {"latency_seconds": 0.2, "pass": True, "assistant_text": "insufficient evidence", "raw_response_text": ""},
            "P0.8_routing_reliability": {
                "pass": True,
                "cases": [
                    {"case_id": "math-03", "pass": True, "assistant_text": "50*3 = 150."},
                    {"case_id": "math-05", "pass": True, "assistant_text": "100*50 = 5000."},
                ],
            },
            "P1.3_instruction_fidelity": {"latency_seconds": 0.6, "pass": True, "assistant_text": "", "raw_response_text": ""},
            "P1.4_recovery": {"latency_seconds": 0.6, "pass": True, "assistant_text": "", "raw_response_text": ""},
            "P1.1_consistency": consistency_runs,
        },
    }


def test_preserve_previous_output_bundle_copies_non_green_summary(monkeypatch, tmp_path: Path) -> None:
    output_root = tmp_path / "latest"
    output_root.mkdir()
    (output_root / "summary.json").write_text(
        json.dumps({"overall_full_green": False, "live_acceptance": {"status": "fail"}}),
        encoding="utf-8",
    )
    (output_root / "summary.md").write_text("old summary\n", encoding="utf-8")
    monkeypatch.setattr(llm_eval.time, "strftime", lambda fmt, now=None: "20260327T070000Z")

    preserved = llm_eval._preserve_previous_output_bundle(output_root)

    assert preserved == tmp_path / "latest_preserved_fail_20260327T070000Z"
    assert (preserved / "summary.json").exists()
    assert (preserved / "summary.md").read_text(encoding="utf-8") == "old summary\n"


def test_preserve_previous_live_run_artifacts_copies_non_green_bundle(monkeypatch, tmp_path: Path) -> None:
    run_root = tmp_path / "llm_eval_live"
    evidence_dir = run_root / "evidence"
    evidence_dir.mkdir(parents=True)
    (evidence_dir / "online_acceptance.json").write_text(
        json.dumps(_fake_online_payload(failing=True)),
        encoding="utf-8",
    )
    (evidence_dir / "offline_honesty.json").write_text(
        json.dumps({"result": {"latency_seconds": 0.05, "pass": True}}),
        encoding="utf-8",
    )
    (evidence_dir / "manual_btc_verification.json").write_text(
        json.dumps({"pass": True}),
        encoding="utf-8",
    )
    monkeypatch.setattr(llm_eval.time, "strftime", lambda fmt, now=None: "20260327T070500Z")

    preserved = llm_eval._preserve_previous_live_run_artifacts(
        run_root=run_root,
        profile_path=llm_eval.DEFAULT_PROFILE_PATH,
    )

    assert preserved == tmp_path / "llm_eval_live_preserved_fail_20260327T070500Z"
    assert (preserved / "evidence" / "online_acceptance.json").exists()


def test_git_metadata_falls_back_to_build_source_json_when_git_checkout_is_missing(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "build-source.json").write_text(
        json.dumps(
            {
                "ref": "main",
                "branch": "main",
                "commit": "15b496e4992038cbd40a582c0e5aed9688d1d70e",
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(llm_eval, "REPO_ROOT", tmp_path)

    seen_kwargs: list[dict[str, object]] = []

    def _raise_git_failure(*args, **kwargs):
        seen_kwargs.append(dict(kwargs))
        raise subprocess.CalledProcessError(128, args[0])

    monkeypatch.setattr(llm_eval.subprocess, "check_output", _raise_git_failure)

    assert llm_eval._git_branch() == "main"
    assert llm_eval._git_commit() == "15b496e4992038cbd40a582c0e5aed9688d1d70e"
    assert seen_kwargs
    assert all(item.get("stderr") == subprocess.DEVNULL for item in seen_kwargs)


def test_collect_recent_llm_inventory_returns_empty_inventory_when_git_history_is_unavailable(monkeypatch, tmp_path: Path) -> None:
    seen_kwargs: list[dict[str, object]] = []

    def _raise_git_failure(*args, **kwargs):
        seen_kwargs.append(dict(kwargs))
        raise subprocess.CalledProcessError(128, args[0])

    monkeypatch.setattr(subprocess, "check_output", _raise_git_failure)

    inventory = collect_recent_llm_inventory(tmp_path, since_hours=48)

    assert inventory == {
        "since_hours": 48,
        "changed_paths": [],
        "relevant_paths": [],
        "tests": [],
        "scripts": [],
        "docs": [],
        "workflows": [],
    }
    assert seen_kwargs
    assert all(item.get("stderr") == subprocess.DEVNULL for item in seen_kwargs)


def _passing_group_result(name: str) -> dict[str, object]:
    return {
        "category": name,
        "status": "pass",
        "scenarios": [],
        "totals": {"total": 0, "passed": 0, "failed": 0},
    }


def _passing_regression_payload(baseline_root: Path, inventory: dict[str, object]) -> dict[str, object]:
    return {
        "status": "pass",
        "baseline_path": "",
        "inventory": inventory,
        "current": {
            "status": "pass",
            "targets": [],
            "summary": {"passed": 0, "failed": 0, "skipped": 0, "xfailed": 0, "xpassed": 0},
            "duration_seconds": 0.0,
        },
        "comparison": {
            "status": "equal",
            "baseline_available": False,
            "summary_delta": {},
            "duration_delta_seconds": 0.0,
            "pass_regressed": False,
            "duration_regressed": False,
        },
    }


def test_regression_payload_sanitizes_baseline_command_paths(monkeypatch, tmp_path: Path) -> None:
    baseline_root = tmp_path / "baselines"
    baseline_root.mkdir(parents=True)
    monkeypatch.setattr(llm_eval, "REPO_ROOT", Path("/home/vool-user/vool"))
    monkeypatch.setattr(
        llm_eval,
        "run_pytest_pack",
        lambda **kwargs: {
            "name": "recent_48h_llm_regression",
            "command": ["/home/vool-user/vool/.venv/bin/python", "-m", "pytest"],
            "targets": ["tests/test_run_local_acceptance.py"],
            "exit_code": 0,
            "duration_seconds": 1.23,
            "summary": {"passed": 1, "failed": 0, "skipped": 0, "xfailed": 0, "xpassed": 0},
            "status": "pass",
            "stdout": "/home/vool-user/vool/.venv/bin/python -m pytest\n",
            "stderr": "",
        },
    )
    monkeypatch.setattr(
        llm_eval,
        "compare_pytest_results",
        lambda current, baseline: {
            "status": "equal",
            "baseline_available": False,
            "summary_delta": {},
            "duration_delta_seconds": 0.0,
            "pass_regressed": False,
            "duration_regressed": False,
        },
    )

    payload = llm_eval._regression_payload(
        baseline_root=baseline_root,
        inventory={"since_hours": 48, "changed_paths": [], "relevant_paths": [], "tests": [], "scripts": [], "docs": [], "workflows": []},
    )

    baseline_text = (baseline_root / "recent_48h_regression.json").read_text(encoding="utf-8")
    assert "/home/vool-user" not in baseline_text
    assert "<repo>/.venv/bin/python" in baseline_text
    assert "/home/vool-user" not in json.dumps(payload)
    assert "<repo>/.venv/bin/python" in json.dumps(payload)


def test_run_live_acceptance_uses_explicit_runtime_and_workspace_roots(monkeypatch, tmp_path: Path) -> None:
    profile = llm_eval.local_acceptance.AcceptanceProfile(
        profile_id=PROFILE_ID,
        display_name=PROFILE_NAME,
        model=PRIMARY_MODEL,
        cold_start_max_seconds=120.0,
        simple_prompt_median_max_seconds=8.0,
        simple_prompt_hard_max_seconds=20.0,
        file_task_median_max_seconds=15.0,
        live_lookup_median_max_seconds=45.0,
        chained_task_median_max_seconds=60.0,
        consistency_min_passes=2,
        manual_btc_source_label="CoinGecko",
        manual_btc_source_url="https://example.invalid",
        bundle_models=BUNDLE_MODELS,
    )
    captured: dict[str, Path] = {}

    monkeypatch.setattr(llm_eval.local_acceptance, "load_profile", lambda path: profile)

    def _fake_run_full_acceptance(**kwargs):
        captured["runtime_home"] = kwargs["runtime_home"]
        captured["workspace_root"] = kwargs["workspace_root"]
        evidence_dir = Path(kwargs["run_root"]) / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        (evidence_dir / "online_acceptance.json").write_text(
            json.dumps(_fake_online_payload(failing=False)),
            encoding="utf-8",
        )
        (evidence_dir / "offline_honesty.json").write_text(
            json.dumps({"result": {"latency_seconds": 0.05, "pass": True}}),
            encoding="utf-8",
        )
        (evidence_dir / "manual_btc_verification.json").write_text(
            json.dumps({"pass": True}),
            encoding="utf-8",
        )
        return 0

    monkeypatch.setattr(llm_eval.local_acceptance, "run_full_acceptance", _fake_run_full_acceptance)
    monkeypatch.setattr(
        llm_eval.local_acceptance,
        "build_acceptance_summary",
        lambda **kwargs: {"overall_green": True, "p0_ids": []},
    )

    runtime_home = (tmp_path / "installed_runtime_home").resolve()
    workspace_root = (tmp_path / "installed_workspace").resolve()
    result = llm_eval._run_live_acceptance(
        base_url="http://127.0.0.1:11435",
        profile_path=tmp_path / "profile.json",
        run_root=tmp_path / "live_run",
        runtime_home=runtime_home,
        workspace_root=workspace_root,
    )

    assert captured["runtime_home"] == runtime_home
    assert captured["workspace_root"] == workspace_root
    assert result["status"] == "pass"


def test_run_live_acceptance_discovers_active_runtime_roots_when_not_provided(monkeypatch, tmp_path: Path) -> None:
    profile = llm_eval.local_acceptance.AcceptanceProfile(
        profile_id=PROFILE_ID,
        display_name=PROFILE_NAME,
        model=PRIMARY_MODEL,
        cold_start_max_seconds=120.0,
        simple_prompt_median_max_seconds=8.0,
        simple_prompt_hard_max_seconds=20.0,
        file_task_median_max_seconds=15.0,
        live_lookup_median_max_seconds=45.0,
        chained_task_median_max_seconds=60.0,
        consistency_min_passes=2,
        manual_btc_source_label="CoinGecko",
        manual_btc_source_url="https://example.invalid",
        bundle_models=BUNDLE_MODELS,
    )
    captured: dict[str, Path] = {}
    discovered_runtime_home = (tmp_path / "discovered_runtime").resolve()
    discovered_workspace_root = (tmp_path / "discovered_workspace").resolve()

    monkeypatch.setattr(llm_eval.local_acceptance, "load_profile", lambda path: profile)
    monkeypatch.setattr(
        llm_eval.local_acceptance,
        "_discover_active_runtime_roots",
        lambda base_url: (discovered_runtime_home, discovered_workspace_root),
    )

    def _fake_run_full_acceptance(**kwargs):
        captured["runtime_home"] = kwargs["runtime_home"]
        captured["workspace_root"] = kwargs["workspace_root"]
        evidence_dir = Path(kwargs["run_root"]) / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        (evidence_dir / "online_acceptance.json").write_text(
            json.dumps(_fake_online_payload(failing=False)),
            encoding="utf-8",
        )
        (evidence_dir / "offline_honesty.json").write_text(
            json.dumps({"result": {"latency_seconds": 0.05, "pass": True}}),
            encoding="utf-8",
        )
        (evidence_dir / "manual_btc_verification.json").write_text(
            json.dumps({"pass": True}),
            encoding="utf-8",
        )
        return 0

    monkeypatch.setattr(llm_eval.local_acceptance, "run_full_acceptance", _fake_run_full_acceptance)
    monkeypatch.setattr(
        llm_eval.local_acceptance,
        "build_acceptance_summary",
        lambda **kwargs: {"overall_green": True, "p0_ids": []},
    )

    result = llm_eval._run_live_acceptance(
        base_url="http://127.0.0.1:11435",
        profile_path=tmp_path / "profile.json",
        run_root=tmp_path / "live_run",
    )

    assert captured["runtime_home"] == discovered_runtime_home
    assert captured["workspace_root"] == discovered_workspace_root
    assert result["status"] == "pass"


def test_main_requires_runtime_home_and_workspace_root_together(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(llm_eval, "run", lambda args: 0)

    try:
        llm_eval.main(
            [
                "--skip-live-runtime",
                "--runtime-home",
                str(tmp_path / "runtime_home"),
            ]
        )
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("expected argparse failure when only one llm_eval runtime root is provided")


def test_main_accepts_runtime_home_and_workspace_root_args(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def _fake_run(args: Namespace) -> int:
        captured["runtime_home"] = args.runtime_home
        captured["workspace_root"] = args.workspace_root
        captured["skip_live_runtime"] = args.skip_live_runtime
        return 0

    monkeypatch.setattr(llm_eval, "run", _fake_run)

    assert (
        llm_eval.main(
            [
                "--skip-live-runtime",
                "--runtime-home",
                str(tmp_path / "runtime_home"),
                "--workspace-root",
                str(tmp_path / "workspace"),
            ]
        )
        == 0
    )
    assert captured == {
        "runtime_home": str(tmp_path / "runtime_home"),
        "workspace_root": str(tmp_path / "workspace"),
        "skip_live_runtime": True,
    }


def test_compare_pytest_results_ignores_duration_regression_when_target_set_changes() -> None:
    comparison = llm_eval.compare_pytest_results(
        current={
            "targets": ["tests/test_runtime_backbone.py", "tests/test_run_local_acceptance.py"],
            "summary": {"passed": 2, "failed": 0, "skipped": 0, "xfailed": 0, "xpassed": 0},
            "exit_code": 0,
            "duration_seconds": 30.0,
        },
        baseline={
            "targets": ["tests/test_runtime_backbone.py"],
            "summary": {"passed": 1, "failed": 0, "skipped": 0, "xfailed": 0, "xpassed": 0},
            "exit_code": 0,
            "duration_seconds": 10.0,
        },
    )

    assert comparison["status"] == "unchanged"
    assert comparison["duration_comparable"] is False
    assert comparison["duration_regressed"] is False
    assert comparison["pass_regressed"] is False


def test_compare_pytest_results_marks_duration_only_regression_as_warning() -> None:
    comparison = llm_eval.compare_pytest_results(
        current={
            "targets": ["tests/test_runtime_backbone.py"],
            "summary": {"passed": 1, "failed": 0, "skipped": 0, "xfailed": 0, "xpassed": 0},
            "exit_code": 0,
            "duration_seconds": 13.0,
        },
        baseline={
            "targets": ["tests/test_runtime_backbone.py"],
            "summary": {"passed": 1, "failed": 0, "skipped": 0, "xfailed": 0, "xpassed": 0},
            "exit_code": 0,
            "duration_seconds": 10.0,
        },
    )

    # Slower but functionally green is a WARNING, not "degraded" (which stays fatal).
    assert comparison["status"] == "duration_warning"
    assert comparison["duration_comparable"] is True
    assert comparison["duration_regressed"] is True
    assert comparison["pass_regressed"] is False
    assert comparison["duration_threshold_ratio"] == 1.2
    assert comparison["duration_regression_pct"] == 30.0


def test_compare_pytest_results_pass_regression_stays_degraded_even_if_faster() -> None:
    comparison = llm_eval.compare_pytest_results(
        current={
            "targets": ["tests/test_runtime_backbone.py"],
            "summary": {"passed": 0, "failed": 1, "skipped": 0, "xfailed": 0, "xpassed": 0},
            "exit_code": 1,
            "duration_seconds": 5.0,
        },
        baseline={
            "targets": ["tests/test_runtime_backbone.py"],
            "summary": {"passed": 1, "failed": 0, "skipped": 0, "xfailed": 0, "xpassed": 0},
            "exit_code": 0,
            "duration_seconds": 10.0,
        },
    )

    assert comparison["status"] == "degraded"
    assert comparison["pass_regressed"] is True


def test_compare_pytest_results_guards_zero_baseline_duration() -> None:
    comparison = llm_eval.compare_pytest_results(
        current={
            "targets": ["tests/test_runtime_backbone.py"],
            "summary": {"passed": 1, "failed": 0, "skipped": 0, "xfailed": 0, "xpassed": 0},
            "exit_code": 0,
            "duration_seconds": 13.0,
        },
        baseline={
            "targets": ["tests/test_runtime_backbone.py"],
            "summary": {"passed": 1, "failed": 0, "skipped": 0, "xfailed": 0, "xpassed": 0},
            "exit_code": 0,
            "duration_seconds": 0.0,
        },
    )

    assert comparison["duration_comparable"] is False
    assert comparison["duration_regressed"] is False
    assert comparison["duration_ratio"] is None
    assert comparison["status"] == "unchanged"


def _fake_pack(*, exit_code: int, passed: int, failed: int, duration: float, errors: int = 0) -> dict:
    return {
        "name": "recent_48h_llm_regression",
        "command": ["python", "-m", "pytest"],
        "targets": ["tests/test_runtime_backbone.py"],
        "exit_code": exit_code,
        "duration_seconds": duration,
        "summary": {"passed": passed, "failed": failed, "errors": errors, "skipped": 0, "xfailed": 0, "xpassed": 0},
        "status": "pass" if exit_code == 0 else "fail",
        "stdout": "",
        "stderr": "",
    }


def _write_baseline(baseline_root: Path, *, duration: float) -> None:
    baseline_root.mkdir(parents=True, exist_ok=True)
    (baseline_root / "recent_48h_regression.json").write_text(
        json.dumps(
            {
                "targets": ["tests/test_runtime_backbone.py"],
                "summary": {"passed": 1, "failed": 0, "errors": 0, "skipped": 0, "xfailed": 0, "xpassed": 0},
                "exit_code": 0,
                "duration_seconds": duration,
            }
        ),
        encoding="utf-8",
    )


_EMPTY_INVENTORY = {
    "since_hours": 48,
    "changed_paths": [],
    "relevant_paths": [],
    "tests": [],
    "scripts": [],
    "docs": [],
    "workflows": [],
}


def test_duration_only_regression_is_warning_not_fail(monkeypatch, tmp_path: Path) -> None:
    baseline_root = tmp_path / "baselines"
    _write_baseline(baseline_root, duration=10.0)
    monkeypatch.setattr(llm_eval, "_git_commit", lambda: "abc1234")
    monkeypatch.setattr(
        llm_eval, "run_pytest_pack", lambda **kwargs: _fake_pack(exit_code=0, passed=1, failed=0, duration=20.0)
    )

    payload = llm_eval._regression_payload(baseline_root=baseline_root, inventory=dict(_EMPTY_INVENTORY))

    assert payload["status"] == "pass"
    assert payload["duration_warning"] is True
    assert payload["comparison"]["status"] == "duration_warning"
    trend_lines = (baseline_root / "duration_trend.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(trend_lines) == 1
    record = json.loads(trend_lines[0])
    assert record["warned"] is True
    assert record["functional_status"] == "pass"
    assert record["commit"] == "abc1234"
    assert record["baseline_duration_seconds"] == 10.0
    assert record["current_duration_seconds"] == 20.0
    assert record["duration_threshold_ratio"] == 1.2
    assert record["duration_regression_pct"] == 100.0


def test_duration_trend_append_is_idempotent_per_run(monkeypatch, tmp_path: Path) -> None:
    baseline_root = tmp_path / "baselines"
    monkeypatch.setattr(llm_eval, "_git_commit", lambda: "abc1234")

    for _ in range(2):
        _write_baseline(baseline_root, duration=10.0)  # re-pin: a green run overwrites the baseline
        monkeypatch.setattr(
            llm_eval, "run_pytest_pack", lambda **kwargs: _fake_pack(exit_code=0, passed=1, failed=0, duration=20.0)
        )
        llm_eval._regression_payload(baseline_root=baseline_root, inventory=dict(_EMPTY_INVENTORY))

    trend_lines = (baseline_root / "duration_trend.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(trend_lines) == 1


def test_failed_tests_remain_fatal_even_when_fast(monkeypatch, tmp_path: Path) -> None:
    baseline_root = tmp_path / "baselines"
    _write_baseline(baseline_root, duration=10.0)
    monkeypatch.setattr(llm_eval, "_git_commit", lambda: "abc1234")
    monkeypatch.setattr(
        llm_eval, "run_pytest_pack", lambda **kwargs: _fake_pack(exit_code=1, passed=0, failed=1, duration=5.0)
    )

    payload = llm_eval._regression_payload(baseline_root=baseline_root, inventory=dict(_EMPTY_INVENTORY))

    assert payload["status"] == "fail"
    assert payload["duration_warning"] is False


def test_malformed_pack_output_remains_fatal(monkeypatch, tmp_path: Path) -> None:
    # A "green" exit with zero executed tests is missing/invalid output, never a pass.
    baseline_root = tmp_path / "baselines"
    _write_baseline(baseline_root, duration=10.0)
    monkeypatch.setattr(llm_eval, "_git_commit", lambda: "abc1234")
    monkeypatch.setattr(
        llm_eval, "run_pytest_pack", lambda **kwargs: _fake_pack(exit_code=0, passed=0, failed=0, duration=1.0)
    )

    payload = llm_eval._regression_payload(baseline_root=baseline_root, inventory=dict(_EMPTY_INVENTORY))

    assert payload["status"] == "fail"
    assert payload["duration_warning"] is False


def test_run_skips_docs_report_write_by_default(monkeypatch, tmp_path: Path) -> None:
    docs_report_path = tmp_path / "docs" / "LLM_ACCEPTANCE_REPORT.md"
    output_root = tmp_path / "reports" / "llm_eval" / "latest"
    baseline_root = tmp_path / "reports" / "llm_eval" / "baselines"
    live_run_root = tmp_path / "artifacts" / "acceptance_runs" / "llm_eval_live"

    monkeypatch.setattr(llm_eval, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(llm_eval, "_git_commit", lambda: "abc123")
    monkeypatch.setattr(llm_eval, "_git_branch", lambda: "main")
    monkeypatch.setattr(llm_eval.time, "strftime", lambda fmt, now=None: "2026-03-28T20:00:00Z")
    monkeypatch.setattr(llm_eval.local_acceptance, "_machine_info", lambda: {"platform": "macOS", "python": "3.11.15", "cpu": "Apple M4", "ram_gb": 24.0, "gpu": "Apple M4"})
    monkeypatch.setattr(
        llm_eval.local_acceptance,
        "load_profile",
        lambda path: llm_eval.local_acceptance.AcceptanceProfile(
            profile_id=PROFILE_ID,
            display_name=PROFILE_NAME,
            model=PRIMARY_MODEL,
            cold_start_max_seconds=120.0,
            simple_prompt_median_max_seconds=8.0,
            simple_prompt_hard_max_seconds=20.0,
            file_task_median_max_seconds=15.0,
            live_lookup_median_max_seconds=45.0,
            chained_task_median_max_seconds=60.0,
            consistency_min_passes=2,
            manual_btc_source_label="CoinGecko",
            manual_btc_source_url="https://example.invalid",
            bundle_models=BUNDLE_MODELS,
        ),
    )
    monkeypatch.setattr(llm_eval, "collect_recent_llm_inventory", lambda repo_root, since_hours=48: {"since_hours": since_hours, "changed_paths": [], "relevant_paths": [], "tests": [], "scripts": [], "docs": [], "workflows": []})
    monkeypatch.setattr(
        llm_eval,
        "_regression_payload",
        _passing_regression_payload,
    )
    monkeypatch.setattr(llm_eval, "_scenario_group_result", lambda name, scenarios: _passing_group_result(name))

    args = Namespace(
        output_root=str(output_root),
        baseline_root=str(baseline_root),
        live_run_root=str(live_run_root),
        profile=str(llm_eval.DEFAULT_PROFILE_PATH),
        base_url=llm_eval.DEFAULT_BASE_URL,
        branch_label="",
        docs_report_path="",
        runtime_home="",
        workspace_root="",
        skip_live_runtime=True,
    )

    assert llm_eval.run(args) == 0
    assert not docs_report_path.exists()
    routing = json.loads((output_root / "routing_reliability.json").read_text(encoding="utf-8"))
    assert routing["corpus"]["total"] == 49
    assert routing["corpus"]["master_spec_complete"] is False
    assert all(case["cleanup"] for case in routing["corpus"]["cases"])


def test_run_writes_docs_report_only_when_explicitly_requested(monkeypatch, tmp_path: Path) -> None:
    docs_report_path = tmp_path / "docs" / "LLM_ACCEPTANCE_REPORT.md"
    output_root = tmp_path / "reports" / "llm_eval" / "latest"
    baseline_root = tmp_path / "reports" / "llm_eval" / "baselines"
    live_run_root = tmp_path / "artifacts" / "acceptance_runs" / "llm_eval_live"

    monkeypatch.setattr(llm_eval, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(llm_eval, "_git_commit", lambda: "abc123")
    monkeypatch.setattr(llm_eval, "_git_branch", lambda: "main")
    monkeypatch.setattr(llm_eval.time, "strftime", lambda fmt, now=None: "2026-03-28T20:00:00Z")
    monkeypatch.setattr(llm_eval.local_acceptance, "_machine_info", lambda: {"platform": "macOS", "python": "3.11.15", "cpu": "Apple M4", "ram_gb": 24.0, "gpu": "Apple M4"})
    monkeypatch.setattr(
        llm_eval.local_acceptance,
        "load_profile",
        lambda path: llm_eval.local_acceptance.AcceptanceProfile(
            profile_id=PROFILE_ID,
            display_name=PROFILE_NAME,
            model=PRIMARY_MODEL,
            cold_start_max_seconds=120.0,
            simple_prompt_median_max_seconds=8.0,
            simple_prompt_hard_max_seconds=20.0,
            file_task_median_max_seconds=15.0,
            live_lookup_median_max_seconds=45.0,
            chained_task_median_max_seconds=60.0,
            consistency_min_passes=2,
            manual_btc_source_label="CoinGecko",
            manual_btc_source_url="https://example.invalid",
            bundle_models=BUNDLE_MODELS,
        ),
    )
    monkeypatch.setattr(llm_eval, "collect_recent_llm_inventory", lambda repo_root, since_hours=48: {"since_hours": since_hours, "changed_paths": [], "relevant_paths": [], "tests": [], "scripts": [], "docs": [], "workflows": []})
    monkeypatch.setattr(
        llm_eval,
        "_regression_payload",
        _passing_regression_payload,
    )
    monkeypatch.setattr(llm_eval, "_scenario_group_result", lambda name, scenarios: _passing_group_result(name))

    args = Namespace(
        output_root=str(output_root),
        baseline_root=str(baseline_root),
        live_run_root=str(live_run_root),
        profile=str(llm_eval.DEFAULT_PROFILE_PATH),
        base_url=llm_eval.DEFAULT_BASE_URL,
        branch_label="",
        docs_report_path="docs/LLM_ACCEPTANCE_REPORT.md",
        runtime_home="",
        workspace_root="",
        skip_live_runtime=True,
    )

    assert llm_eval.run(args) == 0
    assert docs_report_path.exists()
    assert "VOOL LLM Acceptance Summary" in docs_report_path.read_text(encoding="utf-8")


def test_run_sanitizes_summary_artifacts_before_writing(monkeypatch, tmp_path: Path) -> None:
    output_root = tmp_path / "reports" / "llm_eval" / "latest"
    baseline_root = tmp_path / "reports" / "llm_eval" / "baselines"
    live_run_root = tmp_path / "artifacts" / "acceptance_runs" / "llm_eval_live"

    monkeypatch.setattr(llm_eval, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(llm_eval, "_git_commit", lambda: "abc123")
    monkeypatch.setattr(llm_eval, "_git_branch", lambda: "main")
    monkeypatch.setattr(llm_eval.time, "strftime", lambda fmt, now=None: "2026-03-28T20:00:00Z")
    monkeypatch.setattr(llm_eval.local_acceptance, "_machine_info", lambda: {"platform": "macOS", "python": "3.11.15", "cpu": "Apple M4", "ram_gb": 24.0, "gpu": "Apple M4"})
    monkeypatch.setattr(
        llm_eval.local_acceptance,
        "load_profile",
        lambda path: llm_eval.local_acceptance.AcceptanceProfile(
            profile_id=PROFILE_ID,
            display_name=PROFILE_NAME,
            model=PRIMARY_MODEL,
            cold_start_max_seconds=120.0,
            simple_prompt_median_max_seconds=8.0,
            simple_prompt_hard_max_seconds=20.0,
            file_task_median_max_seconds=15.0,
            live_lookup_median_max_seconds=45.0,
            chained_task_median_max_seconds=60.0,
            consistency_min_passes=2,
            manual_btc_source_label="CoinGecko",
            manual_btc_source_url="https://example.invalid",
            bundle_models=BUNDLE_MODELS,
        ),
    )
    monkeypatch.setattr(llm_eval, "collect_recent_llm_inventory", lambda repo_root, since_hours=48: {"since_hours": since_hours, "changed_paths": [], "relevant_paths": [], "tests": [], "scripts": [], "docs": [], "workflows": []})
    monkeypatch.setattr(
        llm_eval,
        "_regression_payload",
        lambda baseline_root, inventory: {
            "status": "pass",
            "baseline_path": "",
            "inventory": inventory,
            "current": {
                "status": "pass",
                "targets": ["tests/test_run_local_acceptance.py"],
                "command": ["/Users/local-user/vool-hive-mind/.venv/bin/python", "-m", "pytest"],
                "stdout": "/Users/local-user/vool-hive-mind/.venv/bin/python -m pytest\n",
                "stderr": "",
                "summary": {"passed": 1, "failed": 0, "skipped": 0, "xfailed": 0, "xpassed": 0},
                "duration_seconds": 0.1,
            },
            "comparison": {"status": "equal", "baseline_available": False, "summary_delta": {}, "duration_delta_seconds": 0.0, "pass_regressed": False, "duration_regressed": False},
        },
    )
    monkeypatch.setattr(
        llm_eval,
        "_scenario_group_result",
        lambda name, scenarios: {
            "category": name,
            "status": "pass",
            "scenarios": [
                {
                    "scenario_id": "leak-check",
                    "description": "sanitizer",
                    "target": "tests/test_dummy.py::test_dummy",
                    "status": "pass",
                    "duration_seconds": 0.1,
                    "summary": {"passed": 1, "failed": 0, "errors": 0, "skipped": 0, "xfailed": 0, "xpassed": 0, "deselected": 0},
                    "exit_code": 0,
                    "stdout": "/Users/local-user/private/log\n",
                    "stderr": "",
                }
            ],
            "totals": {"total": 1, "passed": 1, "failed": 0},
        },
    )

    args = Namespace(
        output_root=str(output_root),
        baseline_root=str(baseline_root),
        live_run_root=str(live_run_root),
        profile=str(llm_eval.DEFAULT_PROFILE_PATH),
        base_url=llm_eval.DEFAULT_BASE_URL,
        branch_label="",
        docs_report_path="",
        runtime_home="",
        workspace_root="",
        skip_live_runtime=True,
    )

    assert llm_eval.run(args) == 0
    summary_text = (output_root / "summary.json").read_text(encoding="utf-8")
    assert "/home/vool-user" not in summary_text
    assert "/Users/<redacted>" in summary_text
