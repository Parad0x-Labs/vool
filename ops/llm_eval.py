from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.llm_eval import (
    DEFAULT_BLIND_PACK_ROOT,
    collect_recent_llm_inventory,
    compare_pytest_results,
    run_procedural_audit,
    run_pytest_pack,
    summarize_latency_rows,
)
from core.proof_manifest import build_proof_manifest, write_proof_manifest
from ops import run_local_acceptance as local_acceptance
from ops.routing_reliability_corpus import ROUTING_RELIABILITY_CASES

DEFAULT_OUTPUT_ROOT = REPO_ROOT / "reports" / "llm_eval" / "latest"
DEFAULT_BASELINE_ROOT = REPO_ROOT / "reports" / "llm_eval" / "baselines"
DEFAULT_LIVE_RUN_ROOT = REPO_ROOT / "artifacts" / "acceptance_runs" / "llm_eval_live"
DEFAULT_PROFILE_PATH = local_acceptance.DEFAULT_PROFILE_PATH
DEFAULT_BASE_URL = "http://127.0.0.1:18080"
DEFAULT_DOCS_REPORT_PATH = REPO_ROOT / "docs" / "LLM_ACCEPTANCE_REPORT.md"

RECENT_48H_BASELINE_TARGETS = [
    "tests/test_run_local_acceptance.py",
    "tests/test_milestone1_ai_first_evals.py",
    "tests/test_alpha_hardening_pass2_live_soak.py",
    "tests/test_vool_hive_task_flow.py",
    "tests/test_voolbook_api.py",
    "tests/test_voolbook_identity.py",
    "tests/test_reward_engine.py",
    "tests/test_openclaw_tooling_context.py",
    "tests/test_vool_web_freshness_and_lookup.py",
]

CONTEXT_SCENARIOS = [
    {
        "id": "active_task_followup_short_id",
        "target": "tests/test_openclaw_tooling_context.py::OpenClawToolingContextTests::test_openclaw_can_confirm_short_followup_after_hive_task_list",
        "description": "Short follow-up resolves against the active Hive task list instead of drifting.",
    },
    {
        "id": "fresh_short_id_reference",
        "target": "tests/test_openclaw_tooling_context.py::OpenClawToolingContextTests::test_openclaw_can_start_hive_task_from_fresh_short_id_reference",
        "description": "Fresh short task references resolve cleanly against the current task list.",
    },
    {
        "id": "history_recovery_followup",
        "target": "tests/test_openclaw_tooling_context.py::OpenClawToolingContextTests::test_openclaw_can_confirm_short_followup_from_recent_history_when_session_state_is_empty",
        "description": "Recent history can recover a short follow-up when volatile session state is empty.",
    },
    {
        "id": "stale_active_task_not_sticky",
        "target": "tests/test_openclaw_tooling_context.py::OpenClawToolingContextTests::test_openclaw_hive_create_confirm_beats_stale_active_task_state",
        "description": "Stale active task state does not hijack a new confirmed create flow.",
    },
    {
        "id": "watched_topic_followup",
        "target": "tests/test_openclaw_tooling_context.py::OpenClawToolingContextTests::test_openclaw_hive_status_followup_uses_watched_topic_context",
        "description": "Watched-topic follow-ups stay on the currently watched Hive topic.",
    },
    {
        "id": "recent_history_topic_followup",
        "target": "tests/test_openclaw_tooling_context.py::OpenClawToolingContextTests::test_openclaw_hive_status_followup_can_resolve_topic_from_recent_history",
        "description": "Recent history can restore the correct topic when state is re-entered.",
    },
    {
        "id": "vilnius_short_followup",
        "target": "tests/test_vool_runtime_contracts.py::test_short_vilnius_time_followup_reuses_recent_time_context",
        "description": "Short follow-up keeps the active time context instead of falling back to generic chat.",
    },
    {
        "id": "vilnius_malformed_followup",
        "target": "tests/test_vool_runtime_contracts.py::test_exact_vilnius_malformed_followup_reuses_session_time_context",
        "description": "Malformed follow-up still binds to the active time question instead of leaking stale context.",
    },
    {
        "id": "stale_person_context_purged_for_math",
        "target": "tests/test_vool_runtime_contracts.py::test_direct_math_overrides_stale_toly_context",
        "description": "Direct math suppresses stale conversational context.",
    },
    {
        "id": "hive_problem_review_followup",
        "target": "tests/test_vool_hive_task_flow.py::test_review_the_problem_clarifies_when_multiple_tasks_are_open",
        "description": "Review/problem follow-up disambiguates against real open Hive tasks.",
    },
]

RESEARCH_SCENARIOS = [
    {
        "id": "planned_search_for_live_updates",
        "target": "tests/test_vool_web_freshness_and_lookup.py::test_latest_telegram_updates_trigger_planned_web_lookup",
        "description": "Fresh update queries route through planned search with source grounding.",
    },
    {
        "id": "offline_honesty",
        "target": "tests/test_vool_web_freshness_and_lookup.py::test_live_info_without_web_fallback_returns_deterministic_disabled_response",
        "description": "When live lookup is disabled, VOOL refuses to bluff current information.",
    },
    {
        "id": "ultra_fresh_honesty",
        "target": "tests/test_vool_web_freshness_and_lookup.py::test_ultra_fresh_market_question_returns_insufficient_evidence_without_bluffing",
        "description": "Ultra-fresh prompts stay honest instead of inventing minute-level certainty.",
    },
    {
        "id": "structured_weather_lookup",
        "target": "tests/test_vool_web_freshness_and_lookup.py::test_weather_live_lookup_uses_structured_weather_wording",
        "description": "Weather lookups use the structured live weather lane instead of generic sludge.",
    },
    {
        "id": "structured_news_lookup",
        "target": "tests/test_vool_web_freshness_and_lookup.py::test_news_live_lookup_uses_structured_headline_wording",
        "description": "News lookups use structured headline wording with freshness semantics.",
    },
    {
        "id": "weak_evidence_uncertainty",
        "target": "tests/test_vool_web_freshness_and_lookup.py::test_adaptive_research_surfaces_uncertainty_when_evidence_stays_weak",
        "description": "Adaptive research says uncertain when evidence remains weak.",
    },
    {
        "id": "empty_lookup_honesty",
        "target": "tests/test_vool_web_freshness_and_lookup.py::test_empty_fresh_lookup_honestly_degrades_instead_of_using_memory_as_final_speaker",
        "description": "Empty fresh lookups degrade honestly instead of laundering stale memory as live truth.",
    },
    {
        "id": "openclaw_live_lookup",
        "target": "tests/test_openclaw_tooling_context.py::OpenClawToolingContextTests::test_openclaw_surface_triggers_live_web_lookup_for_fresh_requests",
        "description": "OpenClaw fresh requests trigger the live lookup lane rather than generic chat.",
    },
]

HIVE_SCENARIOS = [
    {
        "id": "ux_preview_before_confirm",
        "target": "tests/test_openclaw_tooling_context.py::OpenClawToolingContextTests::test_openclaw_hive_task_preview_beats_twitter_route_and_stays_clean",
        "description": "Hive task creation uses the real user preview lane before any create call.",
    },
    {
        "id": "confirm_posts_improved_copy",
        "target": "tests/test_openclaw_tooling_context.py::OpenClawToolingContextTests::test_openclaw_hive_create_yes_improved_posts_improved_copy",
        "description": "Confirmed Hive task creation posts the improved draft through the real UX path.",
    },
    {
        "id": "unsigned_write_blocked",
        "target": "tests/test_meet_and_greet_service.py::MeetAndGreetServerDispatchTests::test_http_server_requires_signed_write_envelope",
        "description": "Unsigned task writes are rejected at the live HTTP boundary.",
    },
    {
        "id": "spoofed_update_blocked",
        "target": "tests/test_meet_and_greet_service.py::MeetAndGreetServerDispatchTests::test_http_server_rejects_spoofed_topic_update_actor",
        "description": "Spoofed topic update actors cannot mutate Hive state.",
    },
    {
        "id": "status_validation_no_mutation",
        "target": "tests/test_meet_and_greet_service.py::MeetAndGreetServerDispatchTests::test_http_server_failed_status_validation_does_not_mutate_topic",
        "description": "Failed status validation cannot mutate the underlying Hive topic.",
    },
    {
        "id": "reward_release_once",
        "target": "tests/test_reward_engine.py::RewardEngineTests::test_releasing_mature_reward_mints_compute_credits_once",
        "description": "Released rewards mint credits once and stay replay-safe.",
    },
    {
        "id": "reward_finalization_ordered",
        "target": "tests/test_reward_engine.py::RewardEngineTests::test_confirmed_reward_finalizes_after_quiet_window",
        "description": "Reward finalization only occurs after the ordered quiet-window progression.",
    },
    {
        "id": "late_negative_review_blocks_finality",
        "target": "tests/test_reward_engine.py::RewardEngineTests::test_negative_review_after_confirmation_slashes_work",
        "description": "Late negative review blocks or reverses finality instead of silently settling.",
    },
]

PROVENANCE_SCENARIOS = [
    {
        "id": "token_identity_mismatch_blocked",
        "target": "tests/test_meet_and_greet_service.py::MeetAndGreetServerDispatchTests::test_http_server_rejects_voolbook_post_token_identity_mismatch",
        "description": "Mismatched human token identity cannot write under another profile.",
    },
    {
        "id": "auth_channel_sets_origin",
        "target": "tests/test_meet_and_greet_service.py::MeetAndGreetServerDispatchTests::test_http_server_sets_voolbook_post_provenance_from_auth_channel",
        "description": "The live HTTP boundary assigns human vs AI origin from the auth channel.",
    },
    {
        "id": "runtime_fast_path_marks_ai_origin",
        "target": "tests/test_agent_runtime_voolbook.py::test_execute_voolbook_post_marks_runtime_posts_as_ai_origin",
        "description": "Runtime-authored VoolBook posts are marked AI-originated.",
    },
    {
        "id": "api_ignores_client_provenance_spoof",
        "target": "tests/test_voolbook_api.py::test_create_post_ignores_client_supplied_provenance_fields",
        "description": "Client-supplied provenance fields cannot spoof the stored origin.",
    },
    {
        "id": "store_default_human_origin",
        "target": "tests/test_voolbook_store.py::test_create_post",
        "description": "Default social posts stay human-originated unless a trusted internal path says otherwise.",
    },
    {
        "id": "store_explicit_ai_origin",
        "target": "tests/test_voolbook_store.py::test_create_post_supports_explicit_ai_provenance",
        "description": "Trusted internal paths can explicitly write AI-originated posts.",
    },
]

ROUTING_RELIABILITY_SCENARIOS = [
    {
        "id": "issue5_canonical_routing_acceptance",
        "target": "tests/test_routing_reliability_acceptance.py",
        "description": "All 49 canonical prompts traverse detector and full-agent routing with model/tool/fail-closed assertions.",
    },
]
LIVE_ROUTING_RELIABILITY_TARGET = "tests/gauntlet/test_routing_reliability_live.py"


def _git_branch() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(REPO_ROOT),
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        build_source_path = REPO_ROOT / "config" / "build-source.json"
        try:
            payload = json.loads(build_source_path.read_text(encoding="utf-8"))
        except Exception:
            return "archive"
        return str(payload.get("branch") or payload.get("ref") or "archive").strip() or "archive"


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(REPO_ROOT),
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        build_source_path = REPO_ROOT / "config" / "build-source.json"
        try:
            payload = json.loads(build_source_path.read_text(encoding="utf-8"))
        except Exception:
            return "archive"
        return str(payload.get("commit") or "archive").strip() or "archive"


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _write_markdown(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def _sanitize_output(value: Any) -> Any:
    return local_acceptance._sanitize_data(value, repo_root=REPO_ROOT)


def _read_json_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _display_path(path: Path | None) -> str:
    if not path:
        return ""
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _resolve_optional_output_path(raw_value: str | None) -> Path | None:
    value = str(raw_value or "").strip()
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path.resolve()


def _write_latency_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "run_id",
        "commit_sha",
        "request_type",
        "test_id",
        "cold_state",
        "prompt_chars",
        "response_chars",
        "latency_seconds",
        "ttfb_seconds",
        "first_useful_token_seconds",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name) for name in fieldnames})


def _copy_tree_with_timestamp(root: Path, *, status: str) -> Path:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    archive_root = root.parent / f"{root.name}_preserved_{status}_{stamp}"
    suffix = 1
    while archive_root.exists():
        archive_root = root.parent / f"{root.name}_preserved_{status}_{stamp}_{suffix}"
        suffix += 1
    shutil.copytree(root, archive_root)
    return archive_root


def _preserve_previous_live_run_artifacts(*, run_root: Path, profile_path: Path) -> Path | None:
    profile = local_acceptance.load_profile(profile_path)
    return local_acceptance._preserve_previous_run_artifacts(run_root=run_root, profile=profile)


def _preserve_previous_output_bundle(output_root: Path) -> Path | None:
    summary_payload = _read_json_if_exists(output_root / "summary.json")
    if summary_payload is None:
        return None
    live_status = str(dict(summary_payload.get("live_acceptance") or {}).get("status") or "").strip().lower()
    status = "pass" if bool(summary_payload.get("overall_full_green")) else live_status or "fail"
    if status == "pass":
        return None
    return _copy_tree_with_timestamp(output_root, status=status)


def _scenario_group_result(name: str, scenarios: list[dict[str, str]]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for scenario in scenarios:
        pack = run_pytest_pack(name=scenario["id"], repo_root=REPO_ROOT, targets=[scenario["target"]])
        results.append(
            {
                "scenario_id": scenario["id"],
                "description": scenario["description"],
                "target": scenario["target"],
                "status": "pass" if pack["exit_code"] == 0 else "fail",
                "duration_seconds": pack["duration_seconds"],
                "summary": pack["summary"],
                "exit_code": pack["exit_code"],
                "stdout": pack["stdout"],
                "stderr": pack["stderr"],
            }
        )
    return _sanitize_output({
        "category": name,
        "status": "pass" if all(item["status"] == "pass" for item in results) else "fail",
        "scenarios": results,
        "totals": {
            "total": len(results),
            "passed": sum(1 for item in results if item["status"] == "pass"),
            "failed": sum(1 for item in results if item["status"] != "pass"),
        },
    })


def _live_routing_reliability_result() -> dict[str, Any]:
    previous = os.environ.get("VOOL_ALLOW_LIVE_OLLAMA_TESTS")
    os.environ["VOOL_ALLOW_LIVE_OLLAMA_TESTS"] = "1"
    try:
        pack = run_pytest_pack(
            name="routing_reliability_live",
            repo_root=REPO_ROOT,
            targets=[LIVE_ROUTING_RELIABILITY_TARGET],
        )
    finally:
        if previous is None:
            os.environ.pop("VOOL_ALLOW_LIVE_OLLAMA_TESTS", None)
        else:
            os.environ["VOOL_ALLOW_LIVE_OLLAMA_TESTS"] = previous
    return _sanitize_output(
        {
            "status": "pass" if pack["exit_code"] == 0 else "fail",
            "target": LIVE_ROUTING_RELIABILITY_TARGET,
            **pack,
        }
    )


def _collect_latency_rows_from_acceptance(
    *,
    run_id: str,
    commit_sha: str,
    online_payload: dict[str, Any],
    offline_payload: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    results = dict(online_payload.get("results") or {})
    request_map = {
        "P0.1a_boot_hello": ("cold_start", True),
        "P0.1b_capabilities": ("warm_simple", False),
        "P0.2_local_file_create": ("tool_invocation", False),
        "P0.3_append": ("tool_invocation", False),
        "P0.3b_readback": ("tool_invocation", False),
        "P0.5_tool_chain": ("chained_task", False),
        "P0.6_logic": ("warm_logic", False),
        "P0.4_live_lookup": ("research_lookup", False),
        "P0.7_honesty_online": ("freshness_honesty", False),
        "P1.3_instruction_fidelity": ("instruction_fidelity", False),
        "P1.4_recovery": ("recovery", False),
    }
    for test_id, (request_type, cold_state) in request_map.items():
        result = dict(results.get(test_id) or {})
        rows.append(
            {
                "run_id": run_id,
                "commit_sha": commit_sha,
                "request_type": request_type,
                "test_id": test_id,
                "cold_state": cold_state,
                "prompt_chars": len(str(result.get("prompt") or "")),
                "response_chars": len(str(result.get("assistant_text") or "")),
                "latency_seconds": result.get("latency_seconds"),
                "ttfb_seconds": None,
                "first_useful_token_seconds": None,
            }
        )
    for index, result in enumerate(list(results.get("P1.1_consistency") or []), start=1):
        rows.append(
            {
                "run_id": run_id,
                "commit_sha": commit_sha,
                "request_type": "consistency_repeat",
                "test_id": f"P1.1_consistency[{index}]",
                "cold_state": False,
                "prompt_chars": 0,
                "response_chars": len(str(result.get("assistant_text") or "")),
                "latency_seconds": result.get("latency_seconds"),
                "ttfb_seconds": None,
                "first_useful_token_seconds": None,
            }
        )
    offline = dict(offline_payload.get("result") or {})
    rows.append(
        {
            "run_id": run_id,
            "commit_sha": commit_sha,
            "request_type": "offline_honesty",
            "test_id": "offline_honesty",
            "cold_state": False,
            "prompt_chars": len(str(offline.get("prompt") or "")),
            "response_chars": len(str(offline.get("assistant_text") or "")),
            "latency_seconds": offline.get("latency_seconds"),
            "ttfb_seconds": None,
            "first_useful_token_seconds": None,
        }
    )
    return rows


def _run_live_acceptance(
    *,
    base_url: str,
    profile_path: Path,
    run_root: Path,
    runtime_home: Path | None = None,
    workspace_root: Path | None = None,
) -> dict[str, Any]:
    if (runtime_home is None) ^ (workspace_root is None):
        raise ValueError("runtime_home and workspace_root must be provided together, or neither.")
    if runtime_home is None and workspace_root is None:
        discovered_roots = local_acceptance._discover_active_runtime_roots(base_url.rstrip("/"))
        if discovered_roots is not None:
            runtime_home, workspace_root = discovered_roots
    preserved_run_root = _preserve_previous_live_run_artifacts(run_root=run_root, profile_path=profile_path)
    profile = local_acceptance.load_profile(profile_path)
    exit_code = local_acceptance.run_full_acceptance(
        base_url=base_url.rstrip("/"),
        repo_root=REPO_ROOT,
        run_root=run_root,
        profile=profile,
        runtime_home=(runtime_home or (run_root / "runtime_home")).resolve(),
        workspace_root=(workspace_root or (run_root / "workspace")).resolve(),
        start_script=local_acceptance.DEFAULT_START_SCRIPT,
    )
    online_payload = json.loads((run_root / "evidence" / "online_acceptance.json").read_text(encoding="utf-8"))
    offline_payload = json.loads((run_root / "evidence" / "offline_honesty.json").read_text(encoding="utf-8"))
    manual_payload = json.loads((run_root / "evidence" / "manual_btc_verification.json").read_text(encoding="utf-8"))
    summary = local_acceptance.build_acceptance_summary(
        online_payload=online_payload,
        offline_payload=offline_payload,
        manual_btc_check=manual_payload,
        profile=profile,
    )
    return {
        "status": "pass" if exit_code == 0 else "fail",
        "exit_code": exit_code,
        "profile": {
            "profile_id": profile.profile_id,
            "display_name": profile.display_name,
            "model": profile.model,
            "bundle_models": list(profile.bundle_models),
            "bundle_roles": [{"role": role, "model": model} for role, model in profile.bundle_roles],
            "capacity_bucket": profile.capacity_bucket,
        },
        "summary": summary,
        "online": online_payload,
        "offline": offline_payload,
        "manual_btc": manual_payload,
        "report_path": _display_path(run_root / "evidence" / "VOOL_LOCAL_ACCEPTANCE_REPORT.md"),
        "preserved_previous_run": _display_path(preserved_run_root),
    }


def _render_regression_markdown(
    *,
    inventory: dict[str, Any],
    recent_pack: dict[str, Any],
    comparison: dict[str, Any],
    baseline_path: Path | None,
) -> str:
    lines = [
        "# 48h Regression Report",
        "",
        f"- baseline source: {_display_path(baseline_path) if baseline_path and baseline_path.exists() else 'none available; new baseline generated if current pack is green'}",
        f"- current rerun status: {recent_pack['status']}",
        f"- current rerun duration: {recent_pack['duration_seconds']}s",
        f"- comparison status: {comparison['status']}",
        "",
        "Changed LLM/runtime surfaces in the last 48 hours:",
    ]
    for path in inventory["relevant_paths"]:
        lines.append(f"- {path}")
    lines.extend(
        [
            "",
            "Current pack summary:",
            f"- targets: {len(recent_pack['targets'])}",
            f"- passed: {recent_pack['summary'].get('passed', 0)}",
            f"- failed: {recent_pack['summary'].get('failed', 0)}",
            f"- skipped: {recent_pack['summary'].get('skipped', 0)}",
            f"- xfailed: {recent_pack['summary'].get('xfailed', 0)}",
            f"- xpassed: {recent_pack['summary'].get('xpassed', 0)}",
        ]
    )
    if comparison.get("baseline_available"):
        threshold_ratio = float(comparison.get("duration_threshold_ratio") or 1.2)
        threshold_pct = round((threshold_ratio - 1.0) * 100)
        lines.extend(
            [
                "",
                "Diff vs baseline:",
                f"- current duration: {comparison.get('current_duration_seconds')}s",
                f"- baseline duration: {comparison.get('baseline_duration_seconds')}s",
                f"- duration delta: {comparison['duration_delta_seconds']}s",
                f"- duration regression: {comparison.get('duration_regression_pct')}% (warning threshold: +{threshold_pct}%)",
                f"- pass regressed: {comparison['pass_regressed']}",
                f"- duration regressed: {comparison['duration_regressed']}",
            ]
        )
        if comparison.get("status") == "duration_warning":
            lines.append(
                f"- DURATION WARNING: exceeded the +{threshold_pct}% threshold -- "
                f"all {recent_pack['summary'].get('passed', 0)} functional tests passed; "
                "duration-only, not a functional regression"
            )
        for key, value in sorted(dict(comparison.get("summary_delta") or {}).items()):
            lines.append(f"- {key}: {value:+d}")
    return "\n".join(lines)


def _render_category_table(category: dict[str, Any]) -> list[str]:
    lines = [
        "| Scenario | Status | Duration (s) | Target |",
        "| --- | --- | ---: | --- |",
    ]
    for item in category["scenarios"]:
        lines.append(
            f"| {item['scenario_id']} | {item['status']} | {item['duration_seconds']} | `{item['target']}` |"
        )
    return lines


def _render_summary_markdown(payload: dict[str, Any]) -> str:
    env = payload["environment"]
    lines = [
        "# VOOL LLM Acceptance Summary",
        "",
        f"- commit SHA: {payload['commit_sha']}",
        f"- branch: {payload['branch']}",
        f"- test run timestamp: {payload['timestamp_utc']}",
        f"- environment: {env['platform']} | python {env['python']} | cpu {env['cpu']} | ram {env['ram_gb']} GB | gpu {env['gpu'] or 'n/a'}",
        f"- model/runtime configuration: {payload['model_runtime']}",
        f"- recent baseline comparison: {payload['regression_48h']['comparison']['status']}",
        f"- overall full gate: {'GREEN' if payload['overall_full_green'] else 'NOT GREEN'}",
        f"- ci fast gate: {'GREEN' if payload['ci_fast_green'] else 'NOT GREEN'}",
        (
            f"- preserved previous non-green output bundle: {payload['preserved_previous_output_root']}"
            if payload.get("preserved_previous_output_root")
            else "- preserved previous non-green output bundle: none"
        ),
        (
            f"- preserved previous live acceptance bundle: {payload['live_acceptance'].get('preserved_previous_run')}"
            if payload["live_acceptance"].get("preserved_previous_run")
            else "- preserved previous live acceptance bundle: none"
        ),
        "",
        "## Pass / Fail Summary",
        "",
        f"- recent 48h regression: {payload['regression_48h']['status']}",
        f"- live runtime acceptance: {payload['live_acceptance']['status']}",
        f"- procedural audit: {payload['procedural_audit']['status']}",
        f"- context discipline: {payload['context_discipline']['status']}",
        f"- routing reliability: {payload['routing_reliability']['status']}",
        f"- routing reliability live lane: {payload['routing_reliability']['live']['status']}",
        f"- research quality: {payload['research_quality']['status']}",
        f"- hive integrity: {payload['hive_integrity']['status']}",
        f"- voolbook provenance: {payload['voolbook_provenance']['status']}",
        "",
        "## Latency Findings",
        "",
    ]
    latency = payload["latency_summary"]
    lines.extend(
        [
            f"- overall p50: {latency['overall']['p50']}",
            f"- overall p95: {latency['overall']['p95']}",
            f"- overall p99: {latency['overall']['p99']}",
            f"- overall max: {latency['overall']['max']}",
            "",
            "| Request Type | Samples | p50 | p95 | p99 | max |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for request_type, summary in sorted(payload["latency_summary"]["by_request_type"].items()):
        lines.append(
            f"| {request_type} | {summary['samples']} | {summary['p50']} | {summary['p95']} | {summary['p99']} | {summary['max']} |"
        )

    for heading, key in (
        ("Procedural Audit Findings", "procedural_audit"),
        ("Routing Reliability Findings", "routing_reliability"),
        ("Context Discipline Findings", "context_discipline"),
        ("Research Quality Findings", "research_quality"),
        ("Hive Integrity Findings", "hive_integrity"),
        ("VoolBook Provenance Findings", "voolbook_provenance"),
    ):
        if key == "procedural_audit":
            lines.extend(
                [
                    "",
                    f"## {heading}",
                    "",
                    f"- seed: {payload[key].get('seed')}",
                    f"- comparison: {payload[key].get('comparison', {}).get('status', 'n/a')}",
                    "",
                    "| Category | Status | Passed | Total |",
                    "| --- | --- | ---: | ---: |",
                ]
            )
            for item in list(payload[key].get("category_results") or []):
                lines.append(
                    f"| {item['category']} | {item['status']} | {item['checks_passed']} | {item['checks_total']} |"
                )
            failing_scenarios = list(payload[key].get("failing_scenarios") or [])
            lines.extend(
                [
                    "",
                    f"- failing scenarios: {', '.join(failing_scenarios) if failing_scenarios else 'none'}",
                ]
            )
            continue
        lines.extend(["", f"## {heading}", "", *_render_category_table(payload[key])])

    lines.extend(
        [
            "",
            "## Regressions",
            "",
            f"- 48h pack comparison: {payload['regression_48h']['comparison']['status']}",
            f"- baseline path: {payload['regression_48h']['baseline_path'] or 'none'}",
            "",
            "## Blockers",
            "",
        ]
    )
    blockers = list(payload["blockers"])
    if blockers:
        for blocker in blockers:
            lines.append(f"- {blocker}")
    else:
        lines.append("- none")

    lines.extend(
        [
            "",
            "## Exact Failing Tests",
            "",
        ]
    )
    if payload["failing_targets"]:
        for target in payload["failing_targets"]:
            lines.append(f"- `{target}`")
    else:
        lines.append("- none")

    lines.extend(
        [
            "",
            "## Next Actions",
            "",
            "- Keep the 48h regression baseline current only from real passing runs.",
            "- Treat provenance or reward integrity regressions as hard release blockers.",
        ]
    )
    if payload["live_acceptance"]["status"] == "pass":
        report_path = str(payload["live_acceptance"].get("report_path") or "").strip()
        if report_path:
            lines.append(f"- Latest live acceptance evidence: `{report_path}`.")
        lines.append("- Re-run the live lane whenever the runtime model, tool path, or acceptance thresholds change.")
        lines.append("- Keep the procedural seed logged so any live failure can be reproduced exactly.")
    else:
        lines.append("- Run the live acceptance lane on a model-provisioned machine before public claims about UX latency.")
    return "\n".join(lines)


def _failures_markdown(payload: dict[str, Any]) -> str:
    lines = ["# LLM Acceptance Failures", ""]
    if payload["overall_full_green"] and not payload["blockers"]:
        lines.append("- none")
        return "\n".join(lines)
    for blocker in payload["blockers"]:
        lines.append(f"- blocker: {blocker}")
    for target in payload["failing_targets"]:
        lines.append(f"- failing target: {target}")
    return "\n".join(lines)


def _append_duration_trend(path: Path, record: dict[str, Any]) -> None:
    """Append one duration measurement to the trend ledger (JSONL, rides the CI artifact).

    Idempotent per run: if the last record matches this run's identity (commit + the two
    durations), the append is skipped so repeated invocations never duplicate history.
    Trend bookkeeping is informational and must never block the gate, so it fails soft.
    """
    key_fields = ("commit", "baseline_duration_seconds", "current_duration_seconds")
    try:
        if path.exists():
            lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
            if lines:
                last = json.loads(lines[-1])
                if all(last.get(field) == record.get(field) for field in key_fields):
                    return
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
    except Exception:
        return


def _regression_payload(
    *,
    baseline_root: Path,
    inventory: dict[str, Any],
) -> dict[str, Any]:
    current_targets = sorted(set(RECENT_48H_BASELINE_TARGETS + inventory["tests"]))
    current = run_pytest_pack(name="recent_48h_llm_regression", repo_root=REPO_ROOT, targets=current_targets)
    baseline_path = baseline_root / "recent_48h_regression.json"
    baseline = None
    if baseline_path.exists():
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    comparison = compare_pytest_results(current, baseline)
    sanitized_current = _sanitize_output(current)

    # Fatal-vs-warning is decided explicitly here, never inferred from a single status
    # string. Fatal: nonzero pytest exit, failed/errored tests, a pass-count regression
    # vs baseline, or malformed/missing pack output (a "green" run that executed nothing).
    # Warning-only: strictly a duration regression with every functional check green.
    summary = dict(current.get("summary") or {})
    executed_total = sum(int(summary.get(field, 0)) for field in ("passed", "failed", "errors", "xfailed", "xpassed"))
    functional_failed = (
        int(current.get("exit_code", 1)) != 0
        or int(summary.get("failed", 0)) > 0
        or int(summary.get("errors", 0)) > 0
        or bool(comparison.get("pass_regressed"))
        or executed_total == 0
    )
    duration_warning = bool(comparison.get("duration_regressed")) and not functional_failed

    # Only a functionally green run with real executed tests may become the new baseline;
    # a malformed pack must never poison future comparisons.
    if not functional_failed:
        _write_json(baseline_path, sanitized_current)

    if comparison.get("baseline_available") and comparison.get("duration_comparable"):
        _append_duration_trend(
            baseline_root / "duration_trend.jsonl",
            {
                "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "commit": _git_commit(),
                "baseline_duration_seconds": comparison.get("baseline_duration_seconds"),
                "current_duration_seconds": comparison.get("current_duration_seconds"),
                "duration_ratio": comparison.get("duration_ratio"),
                "duration_regression_pct": comparison.get("duration_regression_pct"),
                "duration_threshold_ratio": comparison.get("duration_threshold_ratio"),
                "warned": duration_warning,
                "functional_status": "fail" if functional_failed else "pass",
            },
        )

    if duration_warning:
        threshold_pct = round((float(comparison.get("duration_threshold_ratio") or 1.2) - 1.0) * 100)
        print(
            "::warning title=LLM eval duration regression::"
            f"current {comparison.get('current_duration_seconds')}s vs baseline "
            f"{comparison.get('baseline_duration_seconds')}s "
            f"(+{comparison.get('duration_regression_pct')}%, threshold +{threshold_pct}%) "
            "-- all functional tests passed; duration-only, not a functional regression",
            flush=True,
        )

    return {
        "status": "fail" if functional_failed else "pass",
        "duration_warning": duration_warning,
        "baseline_path": _display_path(baseline_path) if baseline_path.exists() else "",
        "inventory": _sanitize_output(inventory),
        "current": sanitized_current,
        "comparison": _sanitize_output(comparison),
    }


def run(args: argparse.Namespace) -> int:
    procedural_seed = int(getattr(args, "procedural_seed", 1337))
    skip_blind_pack = bool(getattr(args, "skip_blind_pack", False))
    output_root = Path(args.output_root).expanduser().resolve()
    baseline_root = Path(args.baseline_root).expanduser().resolve()
    live_run_root = Path(args.live_run_root).expanduser().resolve()
    profile_path = Path(args.profile).expanduser().resolve()
    docs_report_path = _resolve_optional_output_path(getattr(args, "docs_report_path", ""))
    blind_pack_root = Path(args.blind_pack_root).expanduser().resolve() if str(getattr(args, "blind_pack_root", "") or "").strip() else DEFAULT_BLIND_PACK_ROOT
    output_root.mkdir(parents=True, exist_ok=True)
    baseline_root.mkdir(parents=True, exist_ok=True)
    preserved_output_root = _preserve_previous_output_bundle(output_root)

    timestamp_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    commit_sha = _git_commit()
    branch = str(args.branch_label or _git_branch()).strip() or _git_branch()
    machine = local_acceptance._machine_info()
    profile = local_acceptance.load_profile(profile_path)
    run_id = f"llm-eval-{int(time.time())}"

    inventory = collect_recent_llm_inventory(REPO_ROOT, since_hours=48)
    regression_48h = _regression_payload(baseline_root=baseline_root, inventory=inventory)
    context_discipline = _scenario_group_result("context_discipline", CONTEXT_SCENARIOS)
    routing_reliability = _scenario_group_result("routing_reliability", ROUTING_RELIABILITY_SCENARIOS)
    routing_reliability["fast_status"] = routing_reliability["status"]
    routing_reliability["corpus"] = {
        "total": len(ROUTING_RELIABILITY_CASES),
        "cases": [asdict(case) for case in ROUTING_RELIABILITY_CASES],
        "master_spec_scope": ["capability router", "historical routing regressions"],
        "master_spec_complete": False,
    }
    research_quality = _scenario_group_result("research_quality", RESEARCH_SCENARIOS)
    hive_integrity = _scenario_group_result("hive_integrity", HIVE_SCENARIOS)
    voolbook_provenance = _scenario_group_result("voolbook_provenance", PROVENANCE_SCENARIOS)

    blockers: list[str] = []
    live_acceptance: dict[str, Any]
    latency_rows: list[dict[str, Any]]
    procedural_audit: dict[str, Any]
    if args.skip_live_runtime:
        routing_reliability["live"] = {
            "status": "not_run",
            "reason": "Live routing acceptance requires the explicitly triggered Ollama lane.",
            "target": LIVE_ROUTING_RELIABILITY_TARGET,
        }
        live_acceptance = {
            "status": "blocked",
            "reason": "Live runtime acceptance was skipped explicitly.",
            "summary": {},
        }
        procedural_audit = {
            "status": "blocked",
            "reason": "Procedural audit requires a live runtime lane and was skipped with --skip-live-runtime.",
            "seed": procedural_seed,
            "category_results": [],
            "failing_scenarios": [],
            "comparison": {
                "status": "blocked",
                "baseline_available": False,
                "regressed_categories": [],
                "improved_categories": [],
                "duration_regressed": False,
            },
            "summary_markdown": "# Procedural LLM Audit Summary\n\n- blocked: live runtime was skipped.\n",
            "failure_report_markdown": "# Procedural LLM Audit Failures\n\n- blocked: live runtime was skipped.\n",
        }
        blockers.append("Live runtime speed/research acceptance was skipped, so full LLM acceptance is not proven in this run.")
        latency_rows = []
    else:
        live_acceptance = _run_live_acceptance(
            base_url=args.base_url,
            profile_path=profile_path,
            run_root=live_run_root,
            runtime_home=Path(args.runtime_home).expanduser().resolve() if args.runtime_home else None,
            workspace_root=Path(args.workspace_root).expanduser().resolve() if args.workspace_root else None,
        )
        latency_rows = _collect_latency_rows_from_acceptance(
            run_id=run_id,
            commit_sha=commit_sha,
            online_payload=live_acceptance["online"],
            offline_payload=live_acceptance["offline"],
        )
        procedural_audit = run_procedural_audit(
            base_url=args.base_url,
            output_root=output_root,
            baseline_root=baseline_root,
            seed=procedural_seed,
            blind_pack_root=blind_pack_root,
            include_blind=not skip_blind_pack,
        )
        routing_reliability["live"] = _live_routing_reliability_result()
        if routing_reliability["live"]["status"] != "pass":
            routing_reliability["status"] = "fail"
        if live_acceptance["status"] != "pass":
            blockers.append("Live runtime acceptance failed.")
        if procedural_audit["status"] != "pass":
            blockers.append("Procedural live audit failed.")
        if routing_reliability["live"]["status"] != "pass":
            blockers.append("Live routing-reliability acceptance failed.")

    if regression_48h["status"] != "pass":
        blockers.append("The rerun of recent 48h LLM/runtime tests regressed or failed.")
    if context_discipline["status"] != "pass":
        blockers.append("Context-discipline scenarios failed.")
    if routing_reliability["fast_status"] != "pass":
        blockers.append("Routing-reliability scenarios failed.")
    if research_quality["status"] != "pass":
        blockers.append("Research-quality scenarios failed.")
    if hive_integrity["status"] != "pass":
        blockers.append("Hive integrity scenarios failed.")
    if voolbook_provenance["status"] != "pass":
        blockers.append("VoolBook provenance scenarios failed.")

    latency_summary = summarize_latency_rows(latency_rows) if latency_rows else {
        "samples": 0,
        "overall": {"p50": None, "p95": None, "p99": None, "max": None},
        "by_request_type": {},
    }

    failing_targets = []
    for category in (routing_reliability, context_discipline, research_quality, hive_integrity, voolbook_provenance):
        for scenario in category["scenarios"]:
            if scenario["status"] != "pass":
                failing_targets.append(scenario["target"])
    if regression_48h["status"] != "pass":
        failing_targets.extend(regression_48h["current"]["targets"])
    failing_targets.extend(f"procedural:{item}" for item in list(procedural_audit.get("failing_scenarios") or []))
    if routing_reliability["live"]["status"] == "fail":
        failing_targets.append(LIVE_ROUTING_RELIABILITY_TARGET)

    live_profile_meta = dict(live_acceptance.get("online", {}).get("profile") or {})
    runtime_bundle_models = list(
        live_profile_meta.get("runtime_selected_models")
        or live_acceptance.get("online", {}).get("selected_models")
        or profile.bundle_models
        or ()
    )
    payload = {
        "run_id": run_id,
        "timestamp_utc": timestamp_utc,
        "commit_sha": commit_sha,
        "branch": branch,
        "environment": machine,
        "model_runtime": {
            "profile_id": profile.profile_id,
            "profile_name": profile.display_name,
            "model": profile.model,
            "bundle_models": runtime_bundle_models,
            "bundle_roles": list(
                live_profile_meta.get("runtime_selected_model_roles")
                or [{"role": role, "model": model} for role, model in profile.bundle_roles]
            ),
            "capacity_bucket": str(live_profile_meta.get("capacity_bucket") or profile.capacity_bucket or ""),
            "install_profile_id": str(live_profile_meta.get("install_profile_id") or ""),
            "base_url": args.base_url,
        },
        "regression_48h": regression_48h,
        "live_acceptance": live_acceptance,
        "procedural_audit": procedural_audit,
        "latency_summary": latency_summary,
        "routing_reliability": routing_reliability,
        "context_discipline": context_discipline,
        "research_quality": research_quality,
        "hive_integrity": hive_integrity,
        "voolbook_provenance": voolbook_provenance,
        "blockers": blockers,
        "failing_targets": sorted(set(failing_targets)),
        "preserved_previous_output_root": _display_path(preserved_output_root),
        "overall_full_green": not blockers,
        "ci_fast_green": regression_48h["status"] == "pass"
        and routing_reliability["fast_status"] == "pass"
        and context_discipline["status"] == "pass"
        and research_quality["status"] == "pass"
        and hive_integrity["status"] == "pass"
        and voolbook_provenance["status"] == "pass",
    }
    proof_manifest = build_proof_manifest(
        repo_root=REPO_ROOT,
        generated_by="llm_eval",
        runtime_health=dict(live_acceptance.get("online", {}).get("health") or {}),
        runtime_capabilities=dict(live_acceptance.get("online", {}).get("capabilities") or {}),
        acceptance_summary=dict(live_acceptance.get("summary") or {}),
        llm_eval_summary=payload,
    )
    if not proof_manifest["overall_consistent"]:
        payload["blockers"].append(
            "Proof manifest detected mismatched commit/profile/model truth across repo, runtime, or install receipt."
        )
        payload["overall_full_green"] = False

    public_payload = _sanitize_output(payload)
    summary_md = _render_summary_markdown(public_payload)
    regression_md = _render_regression_markdown(
        inventory=dict(public_payload["regression_48h"]["inventory"]),
        recent_pack=dict(public_payload["regression_48h"]["current"]),
        comparison=dict(public_payload["regression_48h"]["comparison"]),
        baseline_path=Path(str(public_payload["regression_48h"]["baseline_path"])) if public_payload["regression_48h"]["baseline_path"] else None,
    )
    failures_md = _failures_markdown(public_payload)

    _write_json(output_root / "summary.json", public_payload)
    _write_markdown(output_root / "summary.md", summary_md)
    _write_latency_csv(output_root / "latency.csv", latency_rows)
    _write_json(output_root / "routing_reliability.json", public_payload["routing_reliability"])
    _write_json(output_root / "context_discipline.json", public_payload["context_discipline"])
    _write_json(output_root / "research_quality.json", public_payload["research_quality"])
    _write_json(output_root / "hive_integrity.json", public_payload["hive_integrity"])
    _write_json(output_root / "voolbook_provenance.json", public_payload["voolbook_provenance"])
    _write_json(output_root / "procedural_audit.json", public_payload["procedural_audit"])
    _write_json(output_root / "procedural_generated_scenarios.json", public_payload["procedural_audit"].get("generated_scenarios") or {})
    _write_json(output_root / "procedural_runner_output.json", public_payload["procedural_audit"].get("runner_output") or {})
    _write_markdown(output_root / "procedural_failures.md", str(public_payload["procedural_audit"].get("failure_report_markdown") or ""))
    _write_markdown(output_root / "procedural_summary.md", str(public_payload["procedural_audit"].get("summary_markdown") or ""))
    _write_markdown(output_root / "regression_48h.md", regression_md)
    _write_markdown(output_root / "failures.md", failures_md)
    write_proof_manifest(output_root / "proof_manifest.json", proof_manifest)
    if docs_report_path is not None:
        _write_markdown(docs_report_path, summary_md)

    return 0 if payload["ci_fast_green"] and (args.skip_live_runtime or payload["overall_full_green"]) else 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Run VOOL LLM evaluation and acceptance reporting.")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--baseline-root", default=str(DEFAULT_BASELINE_ROOT))
    parser.add_argument("--live-run-root", default=str(DEFAULT_LIVE_RUN_ROOT))
    parser.add_argument("--profile", default=str(DEFAULT_PROFILE_PATH))
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--branch-label", default="")
    parser.add_argument("--docs-report-path", default="")
    parser.add_argument("--runtime-home", default="")
    parser.add_argument("--workspace-root", default="")
    parser.add_argument("--skip-live-runtime", action="store_true")
    parser.add_argument("--procedural-seed", type=int, default=1337)
    parser.add_argument("--blind-pack-root", default=str(DEFAULT_BLIND_PACK_ROOT))
    parser.add_argument("--skip-blind-pack", action="store_true")
    args = parser.parse_args(argv)
    if bool(args.runtime_home) ^ bool(args.workspace_root):
        parser.error("`llm_eval` requires both `--runtime-home` and `--workspace-root`, or neither.")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
