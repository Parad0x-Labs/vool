from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any


def repo_source_snapshot(repo_root: str | Path) -> dict[str, Any]:
    root = Path(repo_root).expanduser().resolve()
    branch = _git_output(root, ["git", "rev-parse", "--abbrev-ref", "HEAD"])
    commit = _git_output(root, ["git", "rev-parse", "HEAD"])
    dirty_output = _git_output(root, ["git", "status", "--porcelain"], default=None)
    if branch and commit:
        return {
            "source_kind": "git",
            "branch": branch,
            "commit": commit,
            "dirty_state": bool(str(dirty_output or "").strip()),
        }
    build_source = _read_json_if_exists(root / "config" / "build-source.json") or {}
    return {
        "source_kind": str(build_source.get("source_kind") or "archive").strip() or "archive",
        "branch": str(build_source.get("branch") or build_source.get("ref") or "archive").strip() or "archive",
        "commit": str(build_source.get("commit") or "archive").strip() or "archive",
        "dirty_state": _coerce_optional_bool(build_source.get("dirty_state")),
    }


def build_proof_manifest(
    *,
    repo_root: str | Path,
    generated_by: str,
    install_receipt: dict[str, Any] | None = None,
    runtime_health: dict[str, Any] | None = None,
    runtime_capabilities: dict[str, Any] | None = None,
    acceptance_summary: dict[str, Any] | None = None,
    llm_eval_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = Path(repo_root).expanduser().resolve()
    source_truth = repo_source_snapshot(root)
    receipt_payload = dict(install_receipt or _read_json_if_exists(root / "install_receipt.json") or {})
    runtime_health_payload = dict(runtime_health or {})
    runtime_capabilities_payload = dict(runtime_capabilities or runtime_health_payload.get("capabilities") or {})
    runtime_version = dict(runtime_health_payload.get("runtime") or {})
    receipt_profile = dict(receipt_payload.get("install_profile") or {})
    runtime_profile = dict(runtime_capabilities_payload.get("install_profile") or {})
    recommendation = dict(
        runtime_capabilities_payload.get("install_recommendation")
        or receipt_payload.get("install_recommendation")
        or {}
    )
    receipt_selected_models = tuple(
        str(item).strip()
        for item in list(receipt_profile.get("selected_models") or receipt_payload.get("selected_models") or [])
        if str(item).strip()
    )
    runtime_selected_models = tuple(
        str(item).strip()
        for item in list(runtime_profile.get("selected_models") or [])
        if str(item).strip()
    )
    primary_local_model = (
        str(runtime_version.get("model_tag") or "").strip()
        or str(receipt_payload.get("selected_model") or "").strip()
        or str(receipt_profile.get("selected_model") or "").strip()
        or str(recommendation.get("primary_local_model") or "").strip()
    )
    checks: list[dict[str, Any]] = []
    _append_consistency_check(
        checks,
        name="repo_vs_runtime_commit",
        expected=str(source_truth.get("commit") or "").strip(),
        observed=str(runtime_version.get("commit") or "").strip(),
    )
    _append_consistency_check(
        checks,
        name="repo_vs_receipt_commit",
        expected=str(source_truth.get("commit") or "").strip(),
        observed=str(receipt_payload.get("commit") or "").strip(),
    )
    _append_consistency_check(
        checks,
        name="runtime_vs_receipt_profile",
        expected=str(runtime_profile.get("profile_id") or "").strip(),
        observed=str(receipt_profile.get("profile_id") or "").strip(),
    )
    _append_consistency_check(
        checks,
        name="runtime_vs_receipt_primary_model",
        expected=str(runtime_version.get("model_tag") or "").strip(),
        observed=str(receipt_payload.get("selected_model") or "").strip(),
    )
    if runtime_selected_models and receipt_selected_models:
        _append_consistency_check(
            checks,
            name="runtime_vs_receipt_selected_models",
            expected="|".join(runtime_selected_models),
            observed="|".join(receipt_selected_models),
        )
    # overall_consistent means "no inconsistency was detected". An archive/no-git build with
    # nothing comparable evaluates zero checks and is legitimately not inconsistent, so this
    # stays all(pass) (True for zero checks); consistency_checks_evaluated surfaces how many
    # checks actually ran so a caller can tell "verified" from "nothing to verify".
    overall_consistent = all(bool(item["pass"]) for item in checks)
    return {
        "schema": "vool.proof_manifest.v1",
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "generated_by": str(generated_by or "").strip(),
        "branch": str(source_truth.get("branch") or "").strip(),
        "commit": str(source_truth.get("commit") or "").strip(),
        "dirty_state": source_truth.get("dirty_state"),
        "source_kind": str(source_truth.get("source_kind") or "").strip(),
        "install_profile_id": str(runtime_profile.get("profile_id") or receipt_profile.get("profile_id") or "").strip(),
        "install_profile_label": str(runtime_profile.get("label") or receipt_profile.get("label") or "").strip(),
        "primary_local_model": primary_local_model,
        "selected_models": list(runtime_selected_models or receipt_selected_models),
        "bundle_id": str(runtime_profile.get("bundle_id") or receipt_profile.get("bundle_id") or "").strip(),
        "bundle_kind": str(runtime_profile.get("bundle_kind") or receipt_profile.get("bundle_kind") or "").strip(),
        "secondary_local_model": str(recommendation.get("secondary_local_model") or "").strip(),
        "secondary_local_supported": bool(recommendation.get("secondary_local_supported")),
        "recommended_default_profile": str(recommendation.get("recommended_default_profile") or "").strip(),
        "recommended_optional_profile": str(recommendation.get("recommended_optional_profile") or "").strip(),
        "consistency_checks": checks,
        "consistency_checks_evaluated": len(checks),
        "overall_consistent": overall_consistent,
        "runtime_surface_present": bool(runtime_health_payload),
        "install_receipt_present": bool(receipt_payload),
        "acceptance_overall_green": bool(dict(acceptance_summary or {}).get("overall_green")),
        "llm_eval_overall_green": bool(dict(llm_eval_summary or {}).get("overall_full_green")),
    }


def write_proof_manifest(path: str | Path, payload: dict[str, Any]) -> Path:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return target


# A git short SHA is 7 chars; below this, a prefix match is too weak to mean "consistent",
# so require exact equality (stops a 1-2 char prefix from vacuously passing a commit check).
_MIN_PREFIX_MATCH_LEN = 7


def _values_consistent(expected: str, observed: str) -> bool:
    if expected == observed:
        return True
    shorter, longer = sorted((expected, observed), key=len)
    return len(shorter) >= _MIN_PREFIX_MATCH_LEN and longer.startswith(shorter)


def _append_consistency_check(checks: list[dict[str, Any]], *, name: str, expected: str, observed: str) -> None:
    expected_value = str(expected or "").strip()
    observed_value = str(observed or "").strip()
    if _is_unknown_value(expected_value) or _is_unknown_value(observed_value):
        return
    checks.append(
        {
            "name": name,
            "expected": expected_value,
            "observed": observed_value,
            "pass": _values_consistent(expected_value, observed_value),
        }
    )


def _is_unknown_value(value: str) -> bool:
    normalized = str(value or "").strip().lower()
    return normalized in {"", "archive", "unknown", "none"}


def _coerce_optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    normalized = str(value or "").strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return None


def _git_output(repo_root: Path, command: list[str], *, default: str | None = "") -> str | None:
    try:
        return subprocess.check_output(command, cwd=str(repo_root), text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return default


def _read_json_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


__all__ = [
    "build_proof_manifest",
    "repo_source_snapshot",
    "write_proof_manifest",
]
