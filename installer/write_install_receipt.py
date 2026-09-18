"""Write a small install receipt for support and launcher diagnostics."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from core.install_recommendations import build_install_recommendation_truth
from core.proof_manifest import repo_source_snapshot
from core.runtime_backbone import build_provider_registry_snapshot
from core.runtime_install_profiles import InstallProfileTruth, build_install_profile_truth


def _provider_snapshot_and_profile(
    *,
    model_tag: str,
    runtime_home: str,
) -> tuple[list[dict], InstallProfileTruth]:
    snapshot = build_provider_registry_snapshot(
        runtime_home=runtime_home,
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


def build_receipt(
    *,
    project_root: str,
    runtime_home: str,
    model_tag: str,
    openclaw_enabled: bool,
    openclaw_config_path: str,
    openclaw_agent_dir: str,
    ollama_binary: str,
    launch_agent_path: str = "",
    agent_wallet_pubkey: str = "",
) -> dict:
    project = Path(project_root).resolve()
    provider_capability_truth, install_profile = _provider_snapshot_and_profile(
        model_tag=model_tag,
        runtime_home=runtime_home,
    )
    install_recommendation = build_install_recommendation_truth(
        selected_model=model_tag,
        runtime_home=runtime_home,
    )
    source_truth = repo_source_snapshot(project)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project_root": str(project),
        "runtime_home": runtime_home,
        "branch": str(source_truth.get("branch") or ""),
        "commit": str(source_truth.get("commit") or ""),
        "dirty_state": source_truth.get("dirty_state"),
        "source_kind": str(source_truth.get("source_kind") or ""),
        "selected_model": model_tag,
        # Only the PUBLIC key is ever surfaced. The private seed stays encrypted at
        # rest (AES-256-GCM) under a locally-derived key and is never written here.
        "agent_wallet": {
            "pubkey": str(agent_wallet_pubkey or "").strip(),
            "created_at_install": bool(str(agent_wallet_pubkey or "").strip()),
            "storage": "encrypted_local:aes-256-gcm",
        },
        "provider_capability_truth": provider_capability_truth,
        "install_profile": install_profile.to_dict(),
        "install_recommendation": install_recommendation.to_dict(),
        "api_url": "http://127.0.0.1:11435",
        "openclaw_url": "http://127.0.0.1:18789",
        "trace_url": "http://127.0.0.1:11435/trace",
        "doctor_report_path": str(Path(runtime_home).expanduser().resolve() / "reports" / "install_doctor.json"),
        "openclaw_enabled": bool(openclaw_enabled),
        "openclaw_config_path": openclaw_config_path,
        "openclaw_agent_dir": openclaw_agent_dir,
        "ollama_binary": ollama_binary,
        "launch_agent": {
            "macos": str(launch_agent_path or ""),
            "windows": str(launch_agent_path or ""),
            "enabled": bool(str(launch_agent_path or "").strip()),
        },
        "web_stack": {
            "provider_order": ["searxng", "duckduckgo_html", "ddg_instant"],
            "searxng_url": "http://127.0.0.1:8080",
            "playwright_enabled": True,
            "browser_engine": "chromium",
            "browser_render_default": "enabled_via_installer_launchers",
            "xsearch_bootstrap": "attempted_by_installer_and_launchers",
        },
        "launchers": {
            "install_and_run": {
                "macos": str(project / "Install_And_Run_VOOL.command"),
                "linux": str(project / "Install_And_Run_VOOL.sh"),
                "windows": str(project / "Install_And_Run_VOOL.bat"),
            },
            "start": {
                "macos": str(project / "Start_VOOL.command"),
                "linux": str(project / "Start_VOOL.sh"),
                "windows": str(project / "Start_VOOL.bat"),
            },
            "chat": {
                "macos": str(project / "Talk_To_VOOL.command"),
                "linux": str(project / "Talk_To_VOOL.sh"),
                "windows": str(project / "Talk_To_VOOL.bat"),
            },
            "openclaw": {
                "macos": str(project / "OpenClaw_VOOL.command"),
                "linux": str(project / "OpenClaw_VOOL.sh"),
                "windows": str(project / "OpenClaw_VOOL.bat"),
            },
            "stage_trainable_base": {
                "macos": str(project / "Stage_Trainable_Base.command"),
                "linux": str(project / "Stage_Trainable_Base.sh"),
                "windows": str(project / "Stage_Trainable_Base.bat"),
            },
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="write_install_receipt")
    parser.add_argument("project_root")
    parser.add_argument("runtime_home")
    parser.add_argument("model_tag")
    parser.add_argument("openclaw_enabled")
    parser.add_argument("openclaw_config_path")
    parser.add_argument("openclaw_agent_dir")
    parser.add_argument("ollama_binary")
    parser.add_argument("launch_agent_path")
    # Optional trailing arg so older callers that don't pass a wallet pubkey still work.
    parser.add_argument("agent_wallet_pubkey", nargs="?", default="")
    args = parser.parse_args()

    receipt = build_receipt(
        project_root=args.project_root,
        runtime_home=args.runtime_home,
        model_tag=args.model_tag,
        openclaw_enabled=str(args.openclaw_enabled).strip().lower() in {"1", "true", "yes", "on"},
        openclaw_config_path=args.openclaw_config_path,
        openclaw_agent_dir=args.openclaw_agent_dir,
        ollama_binary=args.ollama_binary,
        launch_agent_path=args.launch_agent_path,
        agent_wallet_pubkey=args.agent_wallet_pubkey,
    )
    target_path = Path(args.project_root).resolve() / "install_receipt.json"
    target_path.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(str(target_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
