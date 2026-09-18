"""Post-install health summary for VOOL bundles."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

# Allow running as a file (`python3 installer/doctor.py`) from any cwd by putting
# the repo root on sys.path before importing `core.*` — same bootstrap as
# installer/provider_probe.py.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from core.bundle_manifest import bundle_model_store_path
from core.install_recommendations import build_install_recommendation_truth
from core.runtime_backbone import build_provider_registry_snapshot
from core.runtime_install_profiles import InstallProfileTruth, build_install_profile_truth


def _provider_snapshot_and_profile(
    *,
    model_tag: str,
    runtime_home: str | Path,
) -> tuple[list[dict[str, Any]], InstallProfileTruth]:
    snapshot = build_provider_registry_snapshot(
        runtime_home=str(runtime_home),
        requested_profile=os.environ.get("VOOL_INSTALL_PROFILE"),
        honor_install_profile=True,
        env={**os.environ, "VOOL_REGISTER_INSTALLED_OLLAMA_MODELS": "0"},
    )
    install_profile = build_install_profile_truth(
        requested_profile=os.environ.get("VOOL_INSTALL_PROFILE"),
        selected_model=model_tag,
        runtime_home=runtime_home,
        provider_capability_truth=snapshot.capability_truth,
    )
    return [item.to_dict() for item in snapshot.capability_truth], install_profile


def _status(ok: bool, detail: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"ok": bool(ok), "detail": str(detail)}
    payload.update(extra)
    return payload


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def _doctor_output_path(project: Path, runtime: Path, requested: str = "") -> Path:
    target = (
        Path(requested).expanduser().resolve()
        if str(requested).strip()
        else runtime / "reports" / "install_doctor.json"
    )
    if target != project and project not in target.parents:
        return target

    local_app_data = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()).expanduser()
    candidates = (
        local_app_data / "VOOL" / "reports" / "install_doctor.json",
        Path(tempfile.gettempdir()) / "VOOL" / "reports" / "install_doctor.json",
    )
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved != project and project not in resolved.parents:
            return resolved
    return (Path(tempfile.gettempdir()) / "vool-doctor" / "install_doctor.json").resolve()


def _bundle_truth(project: Path, runtime: Path, *, selected_model: str) -> dict[str, Any] | None:
    manifest_path = project / "bundle_manifest.json"
    if not manifest_path.is_file():
        return None
    manifest = _load_json(manifest_path)
    manifest_model = str(manifest.get("selected_model") or manifest.get("model") or "").strip()
    effective_model = manifest_model or selected_model
    model_status_path = runtime / "run" / "model-pull.json"
    model_status = _load_json(model_status_path)
    pull_status = str(model_status.get("status") or "unverified").strip().lower()
    model_store = bundle_model_store_path(
        env={"VOOL_BUNDLE_ROOT": str(project), "VOOL_HOME": str(runtime)},
        runtime_home=runtime,
    ) or (runtime / "models")
    required_paths = {
        "manifest": manifest_path,
        "python": project / "python",
        "ollama": project / "ollama" / "ollama.exe",
        "supervisor": project / "bundle_supervisor.py",
        "model_store": model_store,
    }
    return {
        "manifest": manifest,
        "manifest_path": str(manifest_path),
        "selected_model": effective_model,
        "model_pull": {
            "status": pull_status,
            "detail": str(model_status.get("detail") or "").strip(),
            "path": str(model_status_path),
        },
        "components": {
            name: _status(path.exists(), "present" if path.exists() else "missing", path=str(path))
            for name, path in required_paths.items()
        },
    }


def _apply_bundle_pull_truth(
    provider_truth: list[dict[str, Any]],
    *,
    model: str,
    bundle: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not bundle:
        return provider_truth
    pull = dict(bundle.get("model_pull") or {})
    status = str(pull.get("status") or "unverified").strip().lower()
    if status not in {"starting", "pulling", "failed", "error", "delegated_to_api"}:
        return provider_truth
    availability = "blocked" if status in {"failed", "error"} else "degraded"
    detail = str(pull.get("detail") or status).strip()
    updated: list[dict[str, Any]] = []
    for item in provider_truth:
        row = dict(item)
        if (
            str(row.get("provider_id") or "").startswith("ollama-local:")
            and str(row.get("model_id") or "").strip().lower() == str(model or "").strip().lower()
        ):
            row["availability_state"] = availability
            row["last_error"] = detail
            row["measurement_source"] = "bundle_model_pull"
        updated.append(row)
    return updated


def _resolve_binary(binary: str) -> str:
    candidate = str(binary or "").strip()
    if not candidate:
        return ""
    direct = Path(candidate).expanduser()
    if direct.exists():
        return str(direct)
    resolved = shutil.which(candidate)
    return str(resolved or "")


def _public_hive_status(project: Path, runtime: Path) -> dict[str, Any]:
    try:
        if str(project) not in sys.path:
            sys.path.insert(0, str(project))
        from core.public_hive_bridge import public_hive_has_auth, public_hive_write_requires_auth
    except Exception as exc:  # pragma: no cover - best effort only
        return _status(False, "public Hive status unavailable", error=str(exc))

    runtime_bootstrap = runtime / "config" / "agent-bootstrap.json"
    bundled_bootstrap = project / "config" / "agent-bootstrap.json"
    runtime_payload = _load_json(runtime_bootstrap)
    bundled_payload = _load_json(bundled_bootstrap)
    seed_urls = [
        str(url).strip()
        for url in list(runtime_payload.get("meet_seed_urls") or bundled_payload.get("meet_seed_urls") or [])
        if str(url).strip()
    ]
    runtime_auth_loaded = public_hive_has_auth(payload=runtime_payload)
    bundled_auth_loaded = public_hive_has_auth(payload=bundled_payload)

    if not seed_urls:
        return _status(
            True,
            "public Hive disabled or not configured",
            enabled=False,
            seed_count=0,
            requires_auth=False,
            write_enabled=False,
            runtime_bootstrap_path=str(runtime_bootstrap),
            runtime_bootstrap_exists=runtime_bootstrap.exists(),
            bundled_bootstrap_path=str(bundled_bootstrap),
            bundled_bootstrap_exists=bundled_bootstrap.exists(),
            runtime_auth_loaded=runtime_auth_loaded,
            bundled_auth_loaded=bundled_auth_loaded,
        )

    requires_auth = public_hive_write_requires_auth(seed_urls=seed_urls)
    write_enabled = not requires_auth or runtime_auth_loaded or bundled_auth_loaded
    if write_enabled:
        if runtime_auth_loaded:
            detail = "public Hive write auth ready"
        elif bundled_auth_loaded:
            detail = "public Hive auth bundled; runtime can hydrate on startup"
        else:
            detail = "public Hive write path ready without auth"
    else:
        detail = "public Hive write auth missing"
    return _status(
        write_enabled,
        detail,
        enabled=True,
        seed_count=len(seed_urls),
        requires_auth=requires_auth,
        write_enabled=write_enabled,
        runtime_bootstrap_path=str(runtime_bootstrap),
        runtime_bootstrap_exists=runtime_bootstrap.exists(),
        bundled_bootstrap_path=str(bundled_bootstrap),
        bundled_bootstrap_exists=bundled_bootstrap.exists(),
        runtime_auth_loaded=runtime_auth_loaded,
        bundled_auth_loaded=bundled_auth_loaded,
    )


def _launch_agent_status(launch_agent_path: str) -> dict[str, Any]:
    candidate = Path(str(launch_agent_path or "")).expanduser()
    if not str(launch_agent_path or "").strip():
        return _status(True, "launch agent skipped", path="")
    if not candidate.exists():
        return _status(False, "launch agent missing", path=str(candidate))
    if sys.platform != "darwin":
        return _status(True, "launch agent file present", path=str(candidate), loaded=None, running=None)

    uid = getattr(os, "getuid", lambda: 0)()
    label = candidate.stem
    try:
        result = subprocess.run(
            ["launchctl", "print", f"gui/{uid}/{label}"],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception as exc:  # pragma: no cover - system dependent
        return _status(
            False,
            "launch agent status unavailable",
            path=str(candidate),
            error=str(exc),
            loaded=False,
            running=False,
        )

    loaded = result.returncode == 0
    running = loaded and "state = running" in str(result.stdout or "")
    if loaded and running:
        detail = "launch agent loaded and running"
    elif loaded:
        detail = "launch agent loaded but not running"
    else:
        detail = "launch agent file present but not loaded"
    return _status(loaded, detail, path=str(candidate), loaded=loaded, running=running)


def build_report(
    *,
    project_root: str,
    runtime_home: str,
    model_tag: str,
    openclaw_enabled: bool,
    openclaw_config_path: str,
    openclaw_agent_dir: str,
    ollama_binary: str,
    launch_agent_path: str = "",
) -> dict[str, Any]:
    project = Path(project_root).resolve()
    runtime = Path(runtime_home).expanduser().resolve()
    bundle = _bundle_truth(project, runtime, selected_model=model_tag)
    effective_model = str((bundle or {}).get("selected_model") or model_tag or "").strip()
    venv = project / ".venv"
    receipt = project / "install_receipt.json"
    liquefy_config = Path.home() / ".liquefy" / "config.json"
    provider_capability_truth, install_profile = _provider_snapshot_and_profile(
        model_tag=effective_model,
        runtime_home=runtime,
    )
    install_recommendation = build_install_recommendation_truth(
        selected_model=effective_model,
        runtime_home=runtime,
    )
    ollama_path = _resolve_binary(ollama_binary)

    if bundle:
        launchers = {
            "command": project / "VOOL.cmd",
            "browser": project / "vool-open.ps1",
            "one_click": project / "vool.vbs",
            "window": project / "vool_window.py",
            "supervisor": project / "bundle_supervisor.py",
        }
    else:
        launcher_suffix = ".bat" if os.name == "nt" else ".sh"
        launchers = {
            "start": project / f"Start_VOOL{launcher_suffix}",
            "chat": project / f"Talk_To_VOOL{launcher_suffix}",
            "openclaw": project / f"OpenClaw_VOOL{launcher_suffix}",
            "stage_trainable_base": project / f"Stage_Trainable_Base{launcher_suffix}",
        }
    launcher_status = {
        name: _status(path.exists(), "present" if path.exists() else "missing", path=str(path))
        for name, path in launchers.items()
    }

    staged_bases: list[dict[str, Any]] = []
    try:
        if str(project) not in sys.path:
            sys.path.insert(0, str(project))
        from core.trainable_base_manager import list_staged_trainable_bases

        staged_bases = list_staged_trainable_bases()
    except Exception as exc:  # pragma: no cover - best effort only
        staged_bases = [{"error": str(exc)}]
    trainable_base_error = bool(staged_bases) and "error" in staged_bases[0]
    trainable_base_ok = not trainable_base_error
    if trainable_base_error:
        trainable_base_detail = "trainable base status unavailable"
    elif staged_bases:
        trainable_base_detail = "staged trainable base found"
    else:
        trainable_base_detail = "no staged trainable base found yet"

    report = {
        "project_root": str(project),
        "runtime_home": str(runtime),
        "selected_model": effective_model,
        "provider_capability_truth": _apply_bundle_pull_truth(
            provider_capability_truth,
            model=effective_model,
            bundle=bundle,
        ),
        "install_profile": install_profile.to_dict(),
        "install_recommendation": install_recommendation.to_dict(),
        "components": {
            "project_root": _status(project.exists(), "project root found" if project.exists() else "project root missing", path=str(project)),
            "runtime_home": _status(runtime.exists(), "runtime home found" if runtime.exists() else "runtime home missing", path=str(runtime)),
            "venv": (
                _status(True, "not required for self-contained bundle", path=str(venv), required=False, present=venv.exists())
                if bundle
                else _status(venv.exists(), "virtualenv present" if venv.exists() else "virtualenv missing", path=str(venv))
            ),
            "install_receipt": (
                _status(True, "not required for self-contained bundle", path=str(receipt), required=False, present=receipt.exists())
                if bundle
                else _status(receipt.exists(), "install receipt present" if receipt.exists() else "install receipt missing", path=str(receipt))
            ),
            "launchers": {
                "ok": all(item["ok"] for item in launcher_status.values()),
                "items": launcher_status,
            },
            "openclaw": _status(
                (not openclaw_enabled) or (bool(openclaw_config_path) and Path(openclaw_config_path).expanduser().exists()),
                "OpenClaw configured" if openclaw_enabled and openclaw_config_path else ("OpenClaw skipped" if not openclaw_enabled else "OpenClaw config missing"),
                enabled=openclaw_enabled,
                config_path=str(openclaw_config_path or ""),
                agent_dir=str(openclaw_agent_dir or ""),
                agent_dir_exists=bool(openclaw_agent_dir) and Path(openclaw_agent_dir).expanduser().exists(),
            ),
            "liquefy": _status(liquefy_config.exists(), "Liquefy config present" if liquefy_config.exists() else "Liquefy config missing", path=str(liquefy_config)),
            "trainable_base": _status(trainable_base_ok, trainable_base_detail, staged_bases=staged_bases),
            "ollama": _status(bool(ollama_path), "Ollama binary found" if ollama_path else "Ollama binary missing", path=str(ollama_path or ollama_binary or "")),
            "trace_surface": (
                _status(
                    (project / "app" / "apps" / "vool_api_server.py").exists(),
                    "bundle API trace surface available"
                    if (project / "app" / "apps" / "vool_api_server.py").exists()
                    else "bundle API trace surface missing",
                    url="http://127.0.0.1:11435/trace",
                )
                if bundle
                else _status(
                    launchers["openclaw"].exists(),
                    "trace launcher path available" if launchers["openclaw"].exists() else "trace launcher path missing",
                    url="http://127.0.0.1:11435/trace",
                )
            ),
            "launch_agent": _launch_agent_status(launch_agent_path),
            "public_hive": _public_hive_status(project, runtime),
        },
    }

    if bundle:
        report["bundle"] = bundle
        report["components"]["bundle"] = _status(True, "self-contained bundle layout detected", root=str(project))
        pull_status = str(bundle["model_pull"].get("status") or "unverified")
        pull_detail = (
            "model pull status is not failed"
            if pull_status not in {"failed", "error"}
            else "model pull failed"
        )
        pull_metadata = dict(bundle["model_pull"])
        pull_metadata.pop("detail", None)
        report["components"]["bundle_model_pull"] = _status(
            pull_status not in {"failed", "error"},
            pull_detail,
            **pull_metadata,
        )
        report["components"]["bundle_layout"] = {
            "ok": all(bool(item.get("ok")) for item in bundle["components"].values()),
            "items": bundle["components"],
        }

    degraded = []
    if not install_profile.ready or install_profile.degraded:
        degraded.append("install_profile")
    for key, value in report["components"].items():
        if isinstance(value, dict) and "ok" in value and not bool(value["ok"]):
            degraded.append(key)
    report["overall_status"] = "healthy" if not degraded else "degraded"
    report["degraded_components"] = degraded
    return report


def main() -> int:
    parser = argparse.ArgumentParser(prog="vool-doctor")
    parser.add_argument("project_root")
    parser.add_argument("runtime_home")
    parser.add_argument("model_tag")
    parser.add_argument("openclaw_enabled")
    parser.add_argument("openclaw_config_path")
    parser.add_argument("openclaw_agent_dir")
    parser.add_argument("ollama_binary")
    parser.add_argument("launch_agent_path")
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    report = build_report(
        project_root=args.project_root,
        runtime_home=args.runtime_home,
        model_tag=args.model_tag,
        openclaw_enabled=str(args.openclaw_enabled).strip().lower() in {"1", "true", "yes", "on"},
        openclaw_config_path=args.openclaw_config_path,
        openclaw_agent_dir=args.openclaw_agent_dir,
        ollama_binary=args.ollama_binary,
        launch_agent_path=args.launch_agent_path,
    )
    project = Path(args.project_root).resolve()
    runtime = Path(args.runtime_home).expanduser().resolve() if str(args.runtime_home).strip() else Path.home() / "VOOL"
    target = _doctor_output_path(project, runtime, args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(str(target))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
