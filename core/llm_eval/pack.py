from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

_SUMMARY_FIELDS = ("passed", "failed", "errors", "skipped", "xfailed", "xpassed", "deselected")
_LLM_KEYWORDS = (
    "acceptance",
    "agent_runtime",
    "hive",
    "llm",
    "vool",
    "voolbook",
    "openclaw",
    "research",
    "reward",
    "runtime",
    "tooling_context",
    "web",
)


def _is_llm_related_path(path: str) -> bool:
    lowered = str(path or "").lower()
    if not lowered:
        return False
    if lowered.startswith(".github/workflows/"):
        return "ci" in lowered or "acceptance" in lowered or "llm" in lowered
    return any(keyword in lowered for keyword in _LLM_KEYWORDS)


def collect_recent_llm_inventory(repo_root: Path, *, since_hours: int = 48) -> dict[str, Any]:
    try:
        output = subprocess.check_output(
            [
                "git",
                "log",
                f"--since={since_hours} hours ago",
                "--name-only",
                "--pretty=format:",
            ],
            cwd=str(repo_root),
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        output = ""
    changed = sorted({line.strip() for line in output.splitlines() if line.strip()})
    relevant = [path for path in changed if _is_llm_related_path(path)]
    tests = [path for path in relevant if path.startswith("tests/") and path.endswith(".py")]
    scripts = [path for path in relevant if path.startswith("ops/") and path.endswith(".py")]
    docs = [path for path in relevant if path.startswith("docs/") or path in {"README.md", "CONTRIBUTING.md"}]
    workflows = [path for path in relevant if path.startswith(".github/workflows/")]
    return {
        "since_hours": since_hours,
        "changed_paths": changed,
        "relevant_paths": relevant,
        "tests": tests,
        "scripts": scripts,
        "docs": docs,
        "workflows": workflows,
    }


def parse_pytest_summary(output_text: str) -> dict[str, int]:
    text = str(output_text or "")
    summary = {field: 0 for field in _SUMMARY_FIELDS}
    for field in _SUMMARY_FIELDS:
        # pytest prints the singular form for a count of 1 ("1 error", "1 passed"); the
        # archived weekly-run reports under-counted exactly those rows (a scenario summary
        # read errors: 0 next to a stdout ending "1 error"), so the optional plural "s" is
        # load-bearing for truthful reporting even though status comes from exit codes.
        singular = field[:-1] if field.endswith("s") else field
        match = re.search(rf"(\d+)\s+{singular}s?\b", text)
        if match:
            summary[field] = int(match.group(1))
    return summary


def run_pytest_pack(
    *,
    name: str,
    repo_root: Path,
    targets: list[str],
    extra_args: list[str] | None = None,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    args = [sys.executable, "-m", "pytest", "-q", "--tb=short", *list(extra_args or []), *list(targets)]
    started = time.perf_counter()
    timed_out = False
    try:
        process = subprocess.run(
            args,
            cwd=str(repo_root),
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
        )
        stdout, stderr, exit_code = process.stdout, process.stderr, int(process.returncode)
    except subprocess.TimeoutExpired as expired:
        # A hung pack (the repo has measured an unbounded chromium launch holding a CI
        # shard for 86 minutes) must surface as a RED pack carrying its partial evidence,
        # never as an unhandled crash that writes no report at all. Exit 124 is the
        # conventional timeout code; every status decision treats nonzero as fail, so the
        # gate stays non-green.
        timed_out = True
        exit_code = 124
        stdout = str(expired.stdout or "") if isinstance(expired.stdout, str) else ""
        stderr = (
            str(expired.stderr or "") if isinstance(expired.stderr, str) else ""
        ) + f"\npack '{name}' exceeded the {timeout_seconds}s execution bound and was terminated"
    elapsed = round(time.perf_counter() - started, 3)
    combined = f"{stdout}\n{stderr}".strip()
    summary = parse_pytest_summary(combined)
    return {
        "name": name,
        "command": args,
        "targets": list(targets),
        "exit_code": exit_code,
        "duration_seconds": elapsed,
        "timed_out": timed_out,
        "timeout_seconds": timeout_seconds,
        "summary": summary,
        "status": "pass" if exit_code == 0 else "fail",
        "stdout": stdout,
        "stderr": stderr,
    }


def compare_pytest_results(
    current: dict[str, Any],
    baseline: dict[str, Any] | None,
    *,
    duration_tolerance_ratio: float = 0.2,
) -> dict[str, Any]:
    duration_threshold_ratio = 1.0 + duration_tolerance_ratio
    if not baseline:
        return {
            "status": "new_baseline",
            "baseline_available": False,
            "duration_regressed": False,
            "pass_regressed": False,
            "duration_threshold_ratio": duration_threshold_ratio,
        }
    baseline_summary = dict(baseline.get("summary") or {})
    current_summary = dict(current.get("summary") or {})
    baseline_passed = int(baseline_summary.get("failed", 0)) == 0 and int(baseline.get("exit_code", 1)) == 0
    current_passed = int(current_summary.get("failed", 0)) == 0 and int(current.get("exit_code", 1)) == 0
    baseline_duration = float(baseline.get("duration_seconds") or 0.0)
    current_duration = float(current.get("duration_seconds") or 0.0)
    baseline_targets = tuple(str(item).strip() for item in list(baseline.get("targets") or []) if str(item).strip())
    current_targets = tuple(str(item).strip() for item in list(current.get("targets") or []) if str(item).strip())
    # Duration is only comparable across identical target sets with a real (>0) baseline.
    duration_comparable = (not baseline_targets or current_targets == baseline_targets) and baseline_duration > 0.0
    # The decision uses the raw ratio; only the reported percentage is rounded.
    duration_ratio = (current_duration / baseline_duration) if duration_comparable else None
    duration_regressed = bool(duration_comparable and duration_ratio is not None and duration_ratio > duration_threshold_ratio)
    pass_regressed = bool(baseline_passed and not current_passed)
    if pass_regressed:
        status = "degraded"
    elif duration_regressed:
        # Slower but functionally green: a warning, never a functional regression.
        status = "duration_warning"
    elif current_passed and not baseline_passed:
        status = "improved"
    else:
        status = "unchanged"
    return {
        "status": status,
        "baseline_available": True,
        "duration_regressed": duration_regressed,
        "pass_regressed": pass_regressed,
        "baseline_duration_seconds": baseline_duration,
        "current_duration_seconds": current_duration,
        "duration_comparable": duration_comparable,
        "duration_delta_seconds": round(current_duration - baseline_duration, 3),
        "duration_ratio": duration_ratio,
        "duration_threshold_ratio": duration_threshold_ratio,
        "duration_regression_pct": (round((duration_ratio - 1.0) * 100.0, 1) if duration_ratio is not None else None),
        "summary_delta": {
            field: int(current_summary.get(field, 0)) - int(baseline_summary.get(field, 0))
            for field in _SUMMARY_FIELDS
        },
    }
