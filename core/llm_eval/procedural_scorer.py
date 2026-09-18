from __future__ import annotations

import re
from typing import Any

from .procedural_generator import PROCEDURAL_CATEGORY_ORDER


def _find_turn(scenario_result: dict[str, Any], turn_id: str) -> dict[str, Any]:
    for turn in list(scenario_result.get("turns") or []):
        if str(turn.get("turn_id") or "") == str(turn_id or ""):
            return dict(turn)
    return {}


def _find_observation(scenario_result: dict[str, Any], observation_id: str) -> dict[str, Any]:
    return dict(dict(scenario_result.get("observations") or {}).get(str(observation_id or ""), {}))


def _snapshot_field(snapshot: dict[str, Any], path: str) -> Any:
    current: Any = snapshot
    for part in [item for item in str(path or "").split(".") if item]:
        if isinstance(current, dict):
            current = current.get(part)
            continue
        if isinstance(current, list) and part.isdigit():
            index = int(part)
            current = current[index] if 0 <= index < len(current) else None
            continue
        return None
    return current


def _snapshot_text(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(_snapshot_text(item) for item in value)
    if isinstance(value, dict):
        return " ".join(_snapshot_text(item) for item in value.values())
    return str(value or "")


def _score_check(check: dict[str, Any], scenario_result: dict[str, Any]) -> dict[str, Any]:
    check_type = str(check.get("type") or "").strip()
    payload = {
        "check_id": str(check.get("check_id") or ""),
        "category": str(check.get("category") or ""),
        "type": check_type,
        "why": str(check.get("why") or ""),
        "pass": False,
        "evidence": {},
    }
    if check_type.startswith("turn_"):
        turn = _find_turn(scenario_result, str(check.get("turn_id") or ""))
        text = str(turn.get("response_text") or "")
        lowered = text.lower()
        payload["evidence"] = {
            "turn_id": str(check.get("turn_id") or ""),
            "response_text": text,
            "error": str(turn.get("error") or ""),
        }
        if check_type == "turn_min_length":
            payload["pass"] = len(text.strip()) >= int(check.get("minimum") or 0)
        elif check_type == "turn_contains_any":
            terms = [str(item) for item in list(check.get("terms") or []) if str(item).strip()]
            matched = [term for term in terms if term.lower() in lowered]
            payload["evidence"]["matched_terms"] = matched
            payload["pass"] = bool(matched)
        elif check_type == "turn_contains_all":
            terms = [str(item) for item in list(check.get("terms") or []) if str(item).strip()]
            missing = [term for term in terms if term.lower() not in lowered]
            payload["evidence"]["missing_terms"] = missing
            payload["pass"] = not missing
        elif check_type == "turn_absent_terms":
            terms = [str(item) for item in list(check.get("terms") or []) if str(item).strip()]
            hits = [term for term in terms if term.lower() in lowered]
            payload["evidence"]["hits"] = hits
            payload["pass"] = not hits
        elif check_type == "turn_matches_regex":
            pattern = str(check.get("pattern") or "")
            payload["evidence"]["pattern"] = pattern
            payload["pass"] = bool(pattern and re.search(pattern, text))
        else:
            payload["evidence"]["error"] = f"unsupported turn check type: {check_type}"
        return payload

    if check_type.startswith("snapshot_"):
        snapshot = dict(scenario_result.get("operator_snapshot") or {})
        field_path = str(check.get("field_path") or "").strip()
        field_value = _snapshot_field(snapshot, field_path)
        field_text = _snapshot_text(field_value).lower()
        payload["evidence"] = {
            "field_path": field_path,
            "field_value": field_value,
            "snapshot_ok": bool(snapshot.get("ok", False)),
            "snapshot_error": str(snapshot.get("error") or ""),
        }
        if check_type == "snapshot_field_equals":
            payload["pass"] = field_value == check.get("expected")
        elif check_type == "snapshot_field_gte":
            try:
                payload["pass"] = float(field_value) >= float(check.get("expected") or 0)
            except Exception:
                payload["pass"] = False
        elif check_type == "snapshot_field_contains_any":
            terms = [str(item) for item in list(check.get("terms") or []) if str(item).strip()]
            matched = [term for term in terms if term.lower() in field_text]
            payload["evidence"]["matched_terms"] = matched
            payload["pass"] = bool(matched)
        elif check_type == "snapshot_field_contains_all":
            terms = [str(item) for item in list(check.get("terms") or []) if str(item).strip()]
            missing = [term for term in terms if term.lower() not in field_text]
            payload["evidence"]["missing_terms"] = missing
            payload["pass"] = not missing
        elif check_type == "snapshot_field_absent_terms":
            terms = [str(item) for item in list(check.get("terms") or []) if str(item).strip()]
            hits = [term for term in terms if term.lower() in field_text]
            payload["evidence"]["hits"] = hits
            payload["pass"] = not hits
        else:
            payload["evidence"]["error"] = f"unsupported snapshot check type: {check_type}"
        return payload

    observation = _find_observation(scenario_result, str(check.get("observation_id") or ""))
    payload["evidence"] = observation
    if check_type == "observation_file_exists":
        payload["pass"] = bool(observation.get("exists"))
    elif check_type == "observation_file_equals":
        payload["pass"] = bool(observation.get("exists")) and str(observation.get("text") or "") == str(check.get("expected") or "")
    elif check_type == "observation_file_contains":
        text = str(observation.get("text") or "")
        terms = [str(item) for item in list(check.get("terms") or []) if str(item).strip()]
        missing = [term for term in terms if term not in text]
        payload["evidence"]["missing_terms"] = missing
        payload["pass"] = bool(observation.get("exists")) and not missing
    elif check_type == "observation_directory_entries_exact":
        expected = sorted(str(item) for item in list(check.get("expected_entries") or []))
        actual = sorted(str(item) for item in list(observation.get("entries") or []))
        payload["evidence"]["expected_entries"] = expected
        payload["evidence"]["actual_entries"] = actual
        payload["pass"] = bool(observation.get("exists")) and actual == expected
    else:
        payload["evidence"]["error"] = f"unsupported observation check type: {check_type}"
    return payload


def score_procedural_run(
    *,
    pack: dict[str, Any],
    run_result: dict[str, Any],
) -> dict[str, Any]:
    scenario_specs = {
        str(item.get("scenario_id") or ""): dict(item)
        for item in list(pack.get("scenarios") or [])
    }
    scenario_results_out: list[dict[str, Any]] = []
    category_checks: dict[str, list[dict[str, Any]]] = {category: [] for category in PROCEDURAL_CATEGORY_ORDER}

    for scenario_result in list(run_result.get("scenarios") or []):
        scenario_id = str(scenario_result.get("scenario_id") or "")
        scenario_spec = scenario_specs.get(scenario_id, {})
        checks = [_score_check(dict(check), scenario_result) for check in list(scenario_spec.get("checks") or [])]
        failing_categories = sorted({item["category"] for item in checks if not item["pass"]})
        for item in checks:
            category_checks.setdefault(str(item["category"] or ""), []).append(item)
        scenario_results_out.append(
            {
                "scenario_id": scenario_id,
                "family": str(scenario_result.get("family") or ""),
                "title": str(scenario_result.get("title") or ""),
                "status": "pass" if all(item["pass"] for item in checks) else "fail",
                "checks": checks,
                "failing_categories": failing_categories,
                "turns": list(scenario_result.get("turns") or []),
                "observations": dict(scenario_result.get("observations") or {}),
                "runtime_events": dict(scenario_result.get("runtime_events") or {}),
                "operator_snapshot": dict(scenario_result.get("operator_snapshot") or {}),
            }
        )

    categories_out: list[dict[str, Any]] = []
    for category in PROCEDURAL_CATEGORY_ORDER:
        checks = list(category_checks.get(category) or [])
        passed = sum(1 for item in checks if item["pass"])
        categories_out.append(
            {
                "category": category,
                "status": "pass" if checks and passed == len(checks) else ("not_exercised" if not checks else "fail"),
                "checks_total": len(checks),
                "checks_passed": passed,
                "checks_failed": len(checks) - passed,
                "failed_checks": [item for item in checks if not item["pass"]],
            }
        )

    failing_scenarios = [item["scenario_id"] for item in scenario_results_out if item["status"] != "pass"]
    overall_pass = bool(categories_out) and all(item["status"] == "pass" for item in categories_out)
    return {
        "status": "pass" if overall_pass else "fail",
        "category_results": categories_out,
        "scenario_results": scenario_results_out,
        "failing_scenarios": failing_scenarios,
    }


def compare_procedural_scores(
    *,
    current: dict[str, Any],
    baseline: dict[str, Any] | None,
    duration_tolerance_ratio: float = 0.25,
) -> dict[str, Any]:
    if not baseline:
        return {
            "status": "new_baseline",
            "baseline_available": False,
            "regressed_categories": [],
            "improved_categories": [],
            "duration_regressed": False,
        }

    def _category_map(payload: dict[str, Any]) -> dict[str, str]:
        return {
            str(item.get("category") or ""): str(item.get("status") or "")
            for item in list(payload.get("category_results") or [])
        }

    baseline_map = _category_map(baseline)
    current_map = _category_map(current)
    regressed = sorted(
        category
        for category, baseline_status in baseline_map.items()
        if baseline_status == "pass" and current_map.get(category) != "pass"
    )
    improved = sorted(
        category
        for category, current_status in current_map.items()
        if current_status == "pass" and baseline_map.get(category) not in {"pass"}
    )
    baseline_duration = float(baseline.get("duration_seconds") or 0.0)
    current_duration = float(current.get("duration_seconds") or 0.0)
    duration_regressed = bool(
        baseline_duration > 0.0 and current_duration > (baseline_duration * (1.0 + duration_tolerance_ratio))
    )
    if regressed or duration_regressed:
        status = "degraded"
    elif improved or (baseline.get("status") != "pass" and current.get("status") == "pass"):
        status = "improved"
    else:
        status = "unchanged"
    return {
        "status": status,
        "baseline_available": True,
        "regressed_categories": regressed,
        "improved_categories": improved,
        "baseline_duration_seconds": baseline_duration,
        "current_duration_seconds": current_duration,
        "duration_delta_seconds": round(current_duration - baseline_duration, 3),
        "duration_regressed": duration_regressed,
    }
