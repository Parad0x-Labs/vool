from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .metrics import summarize_latency_rows
from .procedural_generator import DEFAULT_BLIND_PACK_ROOT, generate_procedural_pack
from .procedural_runner import run_procedural_pack
from .procedural_scorer import compare_procedural_scores, score_procedural_run


def _read_json_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _failure_report_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Procedural LLM Audit Failures",
        "",
    ]
    failing_categories = [item for item in list(payload.get("category_results") or []) if item.get("status") != "pass"]
    if not failing_categories:
        lines.append("- none")
        return "\n".join(lines)
    for category in failing_categories:
        lines.append(f"## {category['category']}")
        lines.append("")
        for failed_check in list(category.get("failed_checks") or []):
            lines.append(f"- check: `{failed_check['check_id']}`")
            lines.append(f"  why: {failed_check['why']}")
            evidence = dict(failed_check.get("evidence") or {})
            response_text = str(evidence.get("response_text") or "").strip()
            if response_text:
                lines.append(f"  response: {response_text}")
            actual_entries = evidence.get("actual_entries")
            if actual_entries is not None:
                lines.append(f"  actual_entries: {actual_entries}")
            path = str(evidence.get("path") or "").strip()
            if path:
                lines.append(f"  path: {path}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _summary_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Procedural LLM Audit Summary",
        "",
        f"- seed: {payload['seed']}",
        f"- generated at: {payload['generated_at_utc']}",
        f"- duration seconds: {payload['duration_seconds']}",
        f"- status: {payload['status']}",
        f"- comparison: {payload['comparison']['status']}",
        "",
        "## Category Results",
        "",
        "| Category | Status | Passed | Total |",
        "| --- | --- | ---: | ---: |",
    ]
    for category in list(payload.get("category_results") or []):
        lines.append(
            f"| {category['category']} | {category['status']} | {category['checks_passed']} | {category['checks_total']} |"
        )
    lines.extend(
        [
            "",
            "## Scenario Results",
            "",
            "| Scenario | Status | Family |",
            "| --- | --- | --- |",
        ]
    )
    for scenario in list(payload.get("scenario_results") or []):
        lines.append(
            f"| {scenario.get('scenario_id', '')} | {scenario.get('status', '')} | {scenario.get('family', '')} |"
        )
    lines.extend(
        [
            "",
            "## Comparison",
            "",
            f"- regressed categories: {', '.join(payload['comparison'].get('regressed_categories') or []) or 'none'}",
            f"- improved categories: {', '.join(payload['comparison'].get('improved_categories') or []) or 'none'}",
            f"- duration delta seconds: {payload['comparison'].get('duration_delta_seconds')}",
            "",
            "## Failure Report",
            "",
            payload["failure_report_markdown"],
        ]
    )
    return "\n".join(lines).rstrip()


def run_procedural_audit(
    *,
    base_url: str,
    output_root: Path,
    baseline_root: Path,
    seed: int,
    blind_pack_root: Path | None = None,
    include_blind: bool = True,
) -> dict[str, Any]:
    pack = generate_procedural_pack(
        seed=seed,
        output_root=output_root,
        blind_pack_root=blind_pack_root,
        include_blind=include_blind,
    )
    run_result = run_procedural_pack(
        base_url=base_url,
        pack=pack,
    )
    scored = score_procedural_run(pack=pack, run_result=run_result)
    baseline_path = baseline_root / "procedural_audit.json"
    baseline = _read_json_if_exists(baseline_path)
    comparison = compare_procedural_scores(current={**scored, "duration_seconds": run_result["duration_seconds"]}, baseline=baseline)
    latency_summary = summarize_latency_rows(list(run_result.get("turn_latency_rows") or []))
    payload = {
        "status": scored["status"],
        "seed": int(pack["seed"]),
        "seed_hex": str(pack.get("seed_hex") or ""),
        "generated_at_utc": str(pack.get("generated_at_utc") or ""),
        "duration_seconds": float(run_result.get("duration_seconds") or 0.0),
        "category_results": scored["category_results"],
        "scenario_results": scored["scenario_results"],
        "failing_scenarios": scored["failing_scenarios"],
        "comparison": comparison,
        "latency_summary": latency_summary,
        "generated_scenarios": pack,
        "runner_output": run_result,
        "blind_pack_root": str((blind_pack_root or DEFAULT_BLIND_PACK_ROOT).expanduser()),
        "failure_report_markdown": "",
    }
    payload["failure_report_markdown"] = _failure_report_markdown(payload)
    payload["summary_markdown"] = _summary_markdown(payload)
    if payload["status"] == "pass":
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(
            json.dumps(
                {
                    "status": payload["status"],
                    "duration_seconds": payload["duration_seconds"],
                    "category_results": payload["category_results"],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return payload
