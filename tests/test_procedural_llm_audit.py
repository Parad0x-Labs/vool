from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import ops.llm_eval as llm_eval
from core.llm_eval.procedural import run_procedural_audit
from core.llm_eval.procedural_generator import generate_procedural_pack
from core.llm_eval.procedural_scorer import compare_procedural_scores, score_procedural_run


def test_generate_procedural_pack_is_seeded_and_reproducible(tmp_path: Path) -> None:
    first = generate_procedural_pack(seed=1337, output_root=tmp_path, include_blind=False)
    second = generate_procedural_pack(seed=1337, output_root=tmp_path, include_blind=False)

    assert first["seed"] == second["seed"] == 1337
    assert first["scenarios"] == second["scenarios"]


def test_generate_procedural_pack_changes_entities_or_order_with_new_seed(tmp_path: Path) -> None:
    first = generate_procedural_pack(seed=1337, output_root=tmp_path, include_blind=False)
    second = generate_procedural_pack(seed=7331, output_root=tmp_path, include_blind=False)

    assert first["scenarios"] != second["scenarios"]


def test_generate_procedural_pack_loads_local_blind_pack(tmp_path: Path) -> None:
    blind_root = tmp_path / "blind"
    blind_root.mkdir(parents=True)
    (blind_root / "held_out.json").write_text(
        json.dumps(
            {
                "scenarios": [
                    {
                        "scenario_id": "blind_{seed_hex}",
                        "family": "blind_family",
                        "title": "Blind {seed}",
                        "description": "held out",
                        "workspace": "{workspace_root}/blind_{seed_hex}",
                        "conversation_id": "blind-{seed_hex}",
                        "source_context": {"surface": "openclaw", "platform": "openclaw"},
                        "fixtures": [],
                        "observations": [],
                        "turns": [{"turn_id": "probe", "prompt": "blind prompt {seed_hex}"}],
                        "checks": [],
                        "categories": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    pack = generate_procedural_pack(seed=2026, output_root=tmp_path, blind_pack_root=blind_root, include_blind=True)

    assert any("held_out.json" in path for path in pack["loaded_blind_pack_files"])
    assert any(item["scenario_id"].startswith("blind_") for item in pack["scenarios"])


def test_generate_procedural_pack_includes_memory_lifecycle_and_snapshot_checks(tmp_path: Path) -> None:
    pack = generate_procedural_pack(seed=20260331, output_root=tmp_path, include_blind=False)

    assert "snapshot_field_equals" in pack["scoring_schema"]["check_types"]
    assert "snapshot_field_contains_any" in pack["scoring_schema"]["check_types"]
    assert any(item["family"] == "memory_lifecycle" for item in pack["scenarios"])
    assert any(item["family"] == "failed_turn_reset" for item in pack["scenarios"])


def test_generate_procedural_pack_archives_failed_turns_in_operator_snapshot_checks(tmp_path: Path) -> None:
    pack = generate_procedural_pack(seed=20260331, output_root=tmp_path, include_blind=False)
    scenario = next(item for item in pack["scenarios"] if item["family"] == "failed_turn_reset")
    checks = {item["check_id"]: item for item in scenario["checks"]}

    assert checks["failed_reset_snapshot_archives_closed_topic"]["field_path"] == "dialogue_lifecycle.recent_archived_topic_count"
    assert checks["failed_reset_snapshot_marks_unresolved_failure"]["field_path"] == "dialogue_lifecycle.recent_archived_topics.0.closure_reason"


def test_generate_procedural_pack_accepts_live_non_scripted_openers(tmp_path: Path) -> None:
    pack = generate_procedural_pack(seed=20260331, output_root=tmp_path, include_blind=False)
    opening = next(item for item in pack["scenarios"] if item["family"] == "opening_sanity")
    opening_check = next(item for item in opening["checks"] if item["check_id"] == "opening_reply_addresses_human_turn")

    assert "active" in opening_check["terms"]
    assert "ready" in opening_check["terms"]


def test_score_procedural_run_marks_failing_categories_with_exact_evidence() -> None:
    pack = {
        "scenarios": [
            {
                "scenario_id": "blocked_case",
                "checks": [
                    {
                        "check_id": "no_fake_read",
                        "category": "no_fake_visibility_access_claims",
                        "type": "turn_absent_terms",
                        "turn_id": "blocked_read",
                        "terms": ["super secret"],
                        "why": "blocked read must not leak secret text",
                    },
                    {
                        "check_id": "honest_boundary",
                        "category": "failure_honesty",
                        "type": "turn_contains_any",
                        "turn_id": "blocked_read",
                        "terms": ["cannot", "blocked"],
                        "why": "blocked read must admit the boundary",
                    },
                ],
            }
        ]
    }
    run_result = {
        "duration_seconds": 1.0,
        "scenarios": [
            {
                "scenario_id": "blocked_case",
                "family": "blocked_access_recovery",
                "title": "blocked",
                "turns": [
                    {
                        "turn_id": "blocked_read",
                        "prompt": "read forbidden.txt exactly",
                        "response_text": "super secret",
                        "error": "",
                    }
                ],
                "observations": {},
                "runtime_events": {},
            }
        ],
    }

    scored = score_procedural_run(pack=pack, run_result=run_result)

    assert scored["status"] == "fail"
    category_map = {item["category"]: item for item in scored["category_results"]}
    assert category_map["no_fake_visibility_access_claims"]["status"] == "fail"
    assert category_map["failure_honesty"]["status"] == "fail"
    failed_check = category_map["no_fake_visibility_access_claims"]["failed_checks"][0]
    assert failed_check["evidence"]["response_text"] == "super secret"


def test_score_procedural_run_supports_operator_snapshot_checks() -> None:
    pack = {
        "scenarios": [
            {
                "scenario_id": "snapshot_case",
                "checks": [
                    {
                        "check_id": "snapshot_tool_visible",
                        "category": "multi_turn_execution_discipline",
                        "type": "snapshot_field_contains_any",
                        "field_path": "session.execution_history.latest_tool",
                        "terms": ["workspace.read_file"],
                        "why": "Operator snapshot should expose the last grounded tool.",
                    },
                    {
                        "check_id": "snapshot_memory_filtered",
                        "category": "memory_relevance_filtering",
                        "type": "snapshot_field_equals",
                        "field_path": "memory_lifecycle.relevant_memory_count",
                        "expected": 0,
                        "why": "Operator snapshot should show no relevant durable memory for an unrelated utility query.",
                    },
                ],
            }
        ]
    }
    run_result = {
        "duration_seconds": 1.0,
        "scenarios": [
            {
                "scenario_id": "snapshot_case",
                "family": "memory_lifecycle",
                "title": "snapshot",
                "turns": [],
                "observations": {},
                "runtime_events": {},
                "operator_snapshot": {
                    "ok": True,
                    "session": {"execution_history": {"latest_tool": "workspace.read_file"}},
                    "memory_lifecycle": {"relevant_memory_count": 0},
                },
            }
        ],
    }

    scored = score_procedural_run(pack=pack, run_result=run_result)

    category_map = {item["category"]: item for item in scored["category_results"]}
    assert category_map["multi_turn_execution_discipline"]["status"] == "pass"
    assert category_map["memory_relevance_filtering"]["status"] == "pass"


def test_compare_procedural_scores_detects_regression() -> None:
    baseline = {
        "status": "pass",
        "duration_seconds": 1.0,
        "category_results": [
            {"category": "opening_sanity_anti_scripted_behavior", "status": "pass"},
            {"category": "tool_result_grounding", "status": "pass"},
        ],
    }
    current = {
        "status": "fail",
        "duration_seconds": 1.6,
        "category_results": [
            {"category": "opening_sanity_anti_scripted_behavior", "status": "fail"},
            {"category": "tool_result_grounding", "status": "pass"},
        ],
    }

    comparison = compare_procedural_scores(current=current, baseline=baseline)

    assert comparison["status"] == "degraded"
    assert comparison["regressed_categories"] == ["opening_sanity_anti_scripted_behavior"]
    assert comparison["duration_regressed"] is True


def test_run_procedural_audit_updates_baseline_when_green(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "core.llm_eval.procedural.generate_procedural_pack",
        lambda **kwargs: {
            "seed": 42,
            "seed_hex": "000042",
            "generated_at_utc": "2026-03-31T00:00:00Z",
            "scenarios": [{"scenario_id": "opening", "checks": []}],
        },
    )
    monkeypatch.setattr(
        "core.llm_eval.procedural.run_procedural_pack",
        lambda **kwargs: {
            "seed": 42,
            "generated_at_utc": "2026-03-31T00:00:00Z",
            "duration_seconds": 0.4,
            "turn_latency_rows": [{"request_type": "opening_sanity", "latency_seconds": 0.4}],
            "scenarios": [{"scenario_id": "opening", "family": "opening_sanity", "title": "opening", "turns": [], "observations": {}, "runtime_events": {}}],
        },
    )
    monkeypatch.setattr(
        "core.llm_eval.procedural.score_procedural_run",
        lambda **kwargs: {
            "status": "pass",
            "category_results": [{"category": "opening_sanity_anti_scripted_behavior", "status": "pass", "checks_total": 1, "checks_passed": 1, "failed_checks": []}],
            "scenario_results": [{"scenario_id": "opening", "status": "pass", "checks": [], "turns": [], "observations": {}, "runtime_events": {}}],
            "failing_scenarios": [],
        },
    )

    payload = run_procedural_audit(
        base_url="http://127.0.0.1:11435",
        output_root=tmp_path / "reports",
        baseline_root=tmp_path / "baselines",
        seed=42,
        include_blind=False,
    )

    assert payload["status"] == "pass"
    assert (tmp_path / "baselines" / "procedural_audit.json").exists()


def test_llm_eval_run_blocks_full_gate_when_procedural_audit_fails(monkeypatch, tmp_path: Path) -> None:
    output_root = tmp_path / "reports" / "llm_eval" / "latest"
    baseline_root = tmp_path / "reports" / "llm_eval" / "baselines"
    live_run_root = tmp_path / "artifacts" / "acceptance_runs" / "llm_eval_live"

    monkeypatch.setattr(llm_eval, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(llm_eval, "_git_commit", lambda: "abc123")
    monkeypatch.setattr(llm_eval, "_git_branch", lambda: "main")
    monkeypatch.setattr(llm_eval.time, "strftime", lambda fmt, now=None: "2026-03-31T00:00:00Z")
    monkeypatch.setattr(
        llm_eval.local_acceptance,
        "_machine_info",
        lambda: {"platform": "macOS", "python": "3.11.15", "cpu": "Apple M4", "ram_gb": 24.0, "gpu": "Apple M4"},
    )
    monkeypatch.setattr(
        llm_eval.local_acceptance,
        "load_profile",
        lambda path: llm_eval.local_acceptance.AcceptanceProfile(
            profile_id="local-bundle-ollama-v1",
            display_name="VOOL local acceptance for the hardware-aware local Ollama bundle",
            model="qwen3:8b",
            cold_start_max_seconds=120.0,
            simple_prompt_median_max_seconds=8.0,
            simple_prompt_hard_max_seconds=20.0,
            file_task_median_max_seconds=15.0,
            live_lookup_median_max_seconds=45.0,
            chained_task_median_max_seconds=60.0,
            consistency_min_passes=2,
            manual_btc_source_label="CoinGecko",
            manual_btc_source_url="https://example.invalid",
            bundle_models=("qwen3:8b", "deepseek-r1:8b"),
        ),
    )
    monkeypatch.setattr(
        llm_eval,
        "_regression_payload",
        lambda baseline_root, inventory, pack_timeout_seconds=None: {
            "status": "pass",
            "baseline_path": "",
            "inventory": inventory,
            "current": {"status": "pass", "targets": [], "summary": {"passed": 0, "failed": 0, "skipped": 0, "xfailed": 0, "xpassed": 0}, "duration_seconds": 0.0},
            "comparison": {"status": "equal", "baseline_available": False, "summary_delta": {}, "duration_delta_seconds": 0.0, "pass_regressed": False, "duration_regressed": False},
        },
    )
    monkeypatch.setattr(
        llm_eval,
        "_scenario_group_result",
        lambda name, scenarios, pack_timeout_seconds=None: {"category": name, "status": "pass", "scenarios": [], "totals": {"total": 0, "passed": 0, "failed": 0}},
    )
    monkeypatch.setattr(
        llm_eval,
        "_run_live_acceptance",
        lambda **kwargs: {
            "status": "pass",
            "summary": {},
            "online": {"profile": {"runtime_selected_models": ["qwen3:8b"], "runtime_selected_model_roles": []}, "health": {}, "capabilities": {}},
            "offline": {},
            "report_path": "artifacts/acceptance_runs/example.md",
        },
    )
    monkeypatch.setattr(
        llm_eval,
        "run_procedural_audit",
        lambda **kwargs: {
            "status": "fail",
            "seed": 4242,
            "category_results": [{"category": "opening_sanity_anti_scripted_behavior", "status": "fail", "checks_total": 1, "checks_passed": 0, "failed_checks": []}],
            "failing_scenarios": ["opening_sanity_4242"],
            "comparison": {"status": "degraded", "baseline_available": True, "regressed_categories": ["opening_sanity_anti_scripted_behavior"], "improved_categories": [], "duration_regressed": False},
            "summary_markdown": "fail",
            "failure_report_markdown": "fail",
            "generated_scenarios": {"seed": 4242, "scenarios": []},
            "runner_output": {"seed": 4242, "scenarios": []},
        },
    )
    monkeypatch.setattr(
        llm_eval,
        "_live_routing_reliability_result",
        lambda **kwargs: {"status": "pass", "target": llm_eval.LIVE_ROUTING_RELIABILITY_TARGET},
    )
    monkeypatch.setattr(llm_eval, "build_proof_manifest", lambda **kwargs: {"overall_consistent": True})
    monkeypatch.setattr(llm_eval, "write_proof_manifest", lambda path, payload: None)

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
        skip_live_runtime=False,
        procedural_seed=4242,
        blind_pack_root=str(tmp_path / "blind"),
        skip_blind_pack=True,
    )

    assert llm_eval.run(args) == 1
    summary = json.loads((output_root / "summary.json").read_text(encoding="utf-8"))
    assert summary["procedural_audit"]["status"] == "fail"
    assert "procedural:opening_sanity_4242" in summary["failing_targets"]
