from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest import mock

from core.provider_routing import ProviderCapabilityTruth
from installer.doctor import _doctor_output_path, build_report
from installer.write_install_receipt import build_receipt


def _write_platform_launchers(root: Path) -> None:
    suffix = ".bat" if os.name == "nt" else ".sh"
    for stem in ("Start_VOOL", "Talk_To_VOOL", "OpenClaw_VOOL", "Stage_Trainable_Base"):
        root.joinpath(f"{stem}{suffix}").write_text("@echo off\n" if suffix == ".bat" else "#!/bin/sh\n", encoding="utf-8")


def test_install_receipt_exposes_doctor_report_path() -> None:
    receipt = build_receipt(
        project_root="/tmp/vool",
        runtime_home="/tmp/vool-home",
        model_tag="qwen2.5:7b",
        openclaw_enabled=True,
        openclaw_config_path="/tmp/openclaw.json",
        openclaw_agent_dir="/tmp/agent",
        ollama_binary="/tmp/ollama",
    )

    doctor_report_path = Path(receipt["doctor_report_path"])
    assert doctor_report_path.name == "install_doctor.json"
    assert doctor_report_path.parent.name == "reports"
    assert doctor_report_path.parent.parent.name == "vool-home"
    assert receipt["install_profile"]["schema"] == "vool.install_profile.v1"


def test_doctor_output_never_defaults_inside_inspected_bundle(tmp_path) -> None:
    project = tmp_path / "install"
    project.mkdir()
    runtime = project / "reports"

    target = _doctor_output_path(project.resolve(), runtime.resolve())

    assert project.resolve() not in target.parents
    assert target.name == "install_doctor.json"


def test_install_receipt_records_launch_agent_path() -> None:
    receipt = build_receipt(
        project_root="/tmp/vool",
        runtime_home="/tmp/vool-home",
        model_tag="qwen2.5:7b",
        openclaw_enabled=False,
        openclaw_config_path="",
        openclaw_agent_dir="",
        ollama_binary="/tmp/ollama",
        launch_agent_path="/Users/test/Library/LaunchAgents/ai.vool.runtime.plist",
    )

    assert receipt["launch_agent"]["enabled"] is True
    assert receipt["launch_agent"]["macos"].endswith("ai.vool.runtime.plist")
    assert receipt["launch_agent"]["windows"].endswith("ai.vool.runtime.plist")


def test_build_report_marks_missing_components_as_degraded() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / ".venv").mkdir()
        _write_platform_launchers(root)
        (root / "install_receipt.json").write_text("{}\n", encoding="utf-8")
        runtime_home = root / ".vool_runtime"
        runtime_home.mkdir()
        with mock.patch("core.trainable_base_manager.list_staged_trainable_bases", return_value=[]):
            report = build_report(
                project_root=str(root),
                runtime_home=str(runtime_home),
                model_tag="qwen2.5:7b",
                openclaw_enabled=True,
                openclaw_config_path=str(root / "missing-openclaw.json"),
                openclaw_agent_dir=str(root / "missing-agent"),
                ollama_binary=str(root / "missing-ollama"),
            )

    assert report["overall_status"] == "degraded"
    assert "openclaw" in report["degraded_components"]
    assert "ollama" in report["degraded_components"]
    assert report["components"]["launchers"]["ok"] is True
    assert report["components"]["trainable_base"]["ok"] is True
    assert report["components"]["public_hive"]["ok"] is True
    assert report["components"]["public_hive"]["enabled"] is False
    assert report["install_profile"]["schema"] == "vool.install_profile.v1"


def test_build_report_marks_launch_agent_present_when_file_exists() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / ".venv").mkdir()
        _write_platform_launchers(root)
        (root / "install_receipt.json").write_text("{}\n", encoding="utf-8")
        runtime_home = root / ".vool_runtime"
        runtime_home.mkdir()
        launch_agent_path = root / "ai.vool.runtime.plist"
        launch_agent_path.write_text("<plist></plist>\n", encoding="utf-8")
        with mock.patch("core.trainable_base_manager.list_staged_trainable_bases", return_value=[]), mock.patch(
            "installer.doctor.sys.platform",
            "darwin",
        ), mock.patch(
            "installer.doctor.subprocess.run",
            return_value=mock.Mock(returncode=0, stdout="state = running\n", stderr=""),
        ):
            report = build_report(
                project_root=str(root),
                runtime_home=str(runtime_home),
                model_tag="qwen2.5:7b",
                openclaw_enabled=False,
                openclaw_config_path="",
                openclaw_agent_dir="",
                ollama_binary="",
                launch_agent_path=str(launch_agent_path),
            )

    assert report["components"]["launch_agent"]["ok"] is True
    assert report["components"]["launch_agent"]["path"].endswith("ai.vool.runtime.plist")
    assert report["components"]["launch_agent"]["loaded"] is True
    assert report["components"]["launch_agent"]["running"] is True


def test_build_report_marks_launch_agent_degraded_when_file_exists_but_agent_is_not_loaded() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / ".venv").mkdir()
        _write_platform_launchers(root)
        (root / "install_receipt.json").write_text("{}\n", encoding="utf-8")
        runtime_home = root / ".vool_runtime"
        runtime_home.mkdir()
        launch_agent_path = root / "ai.vool.runtime.plist"
        launch_agent_path.write_text("<plist></plist>\n", encoding="utf-8")
        with mock.patch("core.trainable_base_manager.list_staged_trainable_bases", return_value=[]), mock.patch(
            "installer.doctor.sys.platform",
            "darwin",
        ), mock.patch(
            "installer.doctor.subprocess.run",
            return_value=mock.Mock(returncode=1, stdout="", stderr="not loaded"),
        ):
            report = build_report(
                project_root=str(root),
                runtime_home=str(runtime_home),
                model_tag="qwen2.5:7b",
                openclaw_enabled=False,
                openclaw_config_path="",
                openclaw_agent_dir="",
                ollama_binary="",
                launch_agent_path=str(launch_agent_path),
            )

    assert report["components"]["launch_agent"]["ok"] is False
    assert report["components"]["launch_agent"]["loaded"] is False
    assert "launch_agent" in report["degraded_components"]


def test_build_report_flags_missing_public_hive_write_auth() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / ".venv").mkdir()
        (root / "config").mkdir()
        (root / "config" / "agent-bootstrap.json").write_text(
            json.dumps({"meet_seed_urls": ["https://seed-eu.example.test:8766"]}) + "\n",
            encoding="utf-8",
        )
        _write_platform_launchers(root)
        (root / "install_receipt.json").write_text("{}\n", encoding="utf-8")
        runtime_home = root / ".vool_runtime"
        runtime_home.mkdir()
        with mock.patch("core.trainable_base_manager.list_staged_trainable_bases", return_value=[]):
            report = build_report(
                project_root=str(root),
                runtime_home=str(runtime_home),
                model_tag="qwen2.5:7b",
                openclaw_enabled=False,
                openclaw_config_path="",
                openclaw_agent_dir="",
                ollama_binary="",
            )

    assert report["components"]["public_hive"]["ok"] is False
    assert report["components"]["public_hive"]["requires_auth"] is True
    assert report["components"]["public_hive"]["write_enabled"] is False
    assert "public_hive" in report["degraded_components"]


def test_build_report_accepts_bundled_public_hive_auth() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / ".venv").mkdir()
        (root / "config").mkdir()
        (root / "config" / "agent-bootstrap.json").write_text(
            json.dumps(
                {
                    "meet_seed_urls": ["https://seed-eu.example.test:8766"],
                    "auth_token": "bundle-token",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (root / "Start_VOOL.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        (root / "Talk_To_VOOL.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        (root / "OpenClaw_VOOL.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        (root / "Stage_Trainable_Base.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        (root / "install_receipt.json").write_text("{}\n", encoding="utf-8")
        runtime_home = root / ".vool_runtime"
        runtime_home.mkdir()
        with mock.patch("core.trainable_base_manager.list_staged_trainable_bases", return_value=[]):
            report = build_report(
                project_root=str(root),
                runtime_home=str(runtime_home),
                model_tag="qwen2.5:7b",
                openclaw_enabled=False,
                openclaw_config_path="",
                openclaw_agent_dir="",
                ollama_binary="",
            )

    assert report["components"]["public_hive"]["ok"] is True
    assert report["components"]["public_hive"]["write_enabled"] is True
    assert report["components"]["public_hive"]["bundled_auth_loaded"] is True

def test_build_report_exposes_provider_snapshot_truth_and_profile_mix() -> None:
    snapshot = mock.Mock(
        capability_truth=(
            ProviderCapabilityTruth(
                provider_id="ollama-local:qwen2.5:7b",
                model_id="qwen2.5:7b",
                role_fit="coder",
                context_window=32768,
                tool_support=("workspace.read_file",),
                structured_output_support=True,
                tokens_per_second=22.0,
                ram_budget_gb=8.0,
                vram_budget_gb=0.0,
                quantization="q4",
                locality="local",
                privacy_class="private",
                queue_depth=0,
                max_safe_concurrency=1,
            ),
            ProviderCapabilityTruth(
                provider_id="llamacpp-local:qwen2.5:14b-gguf",
                model_id="qwen2.5:14b-gguf",
                role_fit="verifier",
                context_window=32768,
                tool_support=("workspace.read_file", "workspace.run_tests"),
                structured_output_support=True,
                tokens_per_second=14.0,
                ram_budget_gb=12.0,
                vram_budget_gb=0.0,
                quantization="q4_k_m",
                locality="local",
                privacy_class="private",
                queue_depth=0,
                max_safe_concurrency=1,
            ),
        )
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / ".venv").mkdir()
        (root / "Start_VOOL.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        (root / "Talk_To_VOOL.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        (root / "OpenClaw_VOOL.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        (root / "Stage_Trainable_Base.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        (root / "install_receipt.json").write_text("{}\n", encoding="utf-8")
        runtime_home = root / ".vool_runtime"
        runtime_home.mkdir()
        with mock.patch("core.trainable_base_manager.list_staged_trainable_bases", return_value=[]), mock.patch(
            "installer.doctor.build_provider_registry_snapshot",
            return_value=snapshot,
        ):
            report = build_report(
                project_root=str(root),
                runtime_home=str(runtime_home),
                model_tag="qwen2.5:7b",
                openclaw_enabled=False,
                openclaw_config_path="",
                openclaw_agent_dir="",
                ollama_binary="",
            )

    truth = report["provider_capability_truth"]
    provider_ids = {item["provider_id"] for item in truth}
    mix_ids = {item["provider_id"] for item in report["install_profile"]["provider_mix"]}
    recommendation = report["install_recommendation"]
    assert provider_ids == {"ollama-local:qwen2.5:7b", "llamacpp-local:qwen2.5:14b-gguf"}
    assert mix_ids <= provider_ids
    assert recommendation["recommended_default_profile"] == "local-only"
    assert recommendation["primary_local_model"] == "qwen2.5:7b"


def test_build_report_marks_degraded_install_profile_as_degraded() -> None:
    snapshot = mock.Mock(
        capability_truth=(
            ProviderCapabilityTruth(
                provider_id="ollama-local:qwen2.5:7b",
                model_id="qwen2.5:7b",
                role_fit="coder",
                context_window=32768,
                tool_support=("workspace.read_file",),
                structured_output_support=True,
                tokens_per_second=22.0,
                ram_budget_gb=8.0,
                vram_budget_gb=0.0,
                quantization="q4",
                locality="local",
                privacy_class="private",
                queue_depth=0,
                max_safe_concurrency=1,
                availability_state="ready",
            ),
            ProviderCapabilityTruth(
                provider_id="kimi-remote:kimi-k2",
                model_id="kimi-k2",
                role_fit="queen",
                context_window=131072,
                tool_support=("workspace.read_file", "workspace.run_tests"),
                structured_output_support=True,
                tokens_per_second=48.0,
                ram_budget_gb=0.0,
                vram_budget_gb=0.0,
                quantization="remote",
                locality="remote",
                privacy_class="delegated",
                queue_depth=1,
                max_safe_concurrency=2,
                availability_state="degraded",
            ),
        )
    )
    with tempfile.TemporaryDirectory() as tmpdir, mock.patch.dict(
        "os.environ",
        {"VOOL_INSTALL_PROFILE": "hybrid-kimi", "KIMI_API_KEY": "test-key"},
        clear=False,
    ):
        root = Path(tmpdir)
        (root / ".venv").mkdir()
        (root / "Start_VOOL.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        (root / "Talk_To_VOOL.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        (root / "OpenClaw_VOOL.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        (root / "Stage_Trainable_Base.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        (root / "install_receipt.json").write_text("{}\n", encoding="utf-8")
        runtime_home = root / ".vool_runtime"
        runtime_home.mkdir()
        with mock.patch("core.trainable_base_manager.list_staged_trainable_bases", return_value=[]), mock.patch(
            "installer.doctor.build_provider_registry_snapshot",
            return_value=snapshot,
        ):
            report = build_report(
                project_root=str(root),
                runtime_home=str(runtime_home),
                model_tag="qwen2.5:7b",
                openclaw_enabled=False,
                openclaw_config_path="",
                openclaw_agent_dir="",
                ollama_binary="",
            )

    assert report["install_profile"]["degraded"] is True
    assert "install_profile" in report["degraded_components"]


def test_build_report_resolves_ollama_binary_from_path_lookup() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / ".venv").mkdir()
        (root / "Start_VOOL.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        (root / "Talk_To_VOOL.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        (root / "OpenClaw_VOOL.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        (root / "Stage_Trainable_Base.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        (root / "install_receipt.json").write_text("{}\n", encoding="utf-8")
        runtime_home = root / ".vool_runtime"
        runtime_home.mkdir()
        with mock.patch("core.trainable_base_manager.list_staged_trainable_bases", return_value=[]):
            with mock.patch("shutil.which", return_value="/usr/local/bin/ollama"):
                report = build_report(
                    project_root=str(root),
                    runtime_home=str(runtime_home),
                    model_tag="qwen2.5:7b",
                    openclaw_enabled=False,
                    openclaw_config_path="",
                    openclaw_agent_dir="",
                    ollama_binary="ollama",
                )

    assert report["components"]["ollama"]["ok"] is True
    assert report["components"]["ollama"]["path"] == "/usr/local/bin/ollama"


def test_bundle_doctor_uses_manifest_model_and_fails_closed_after_pull_failure() -> None:
    snapshot = mock.Mock(
        capability_truth=(
            ProviderCapabilityTruth(
                provider_id="ollama-local:qwen2.5:7b",
                model_id="qwen2.5:7b",
                role_fit="coder",
                context_window=32768,
                tool_support=("workspace.read_file",),
                structured_output_support=True,
                tokens_per_second=22.0,
                ram_budget_gb=8.0,
                vram_budget_gb=0.0,
                quantization="q4",
                locality="local",
                privacy_class="private",
                queue_depth=0,
                max_safe_concurrency=1,
                availability_state="ready",
            ),
        )
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "python").mkdir()
        (root / "ollama").mkdir()
        (root / "ollama" / "ollama.exe").write_bytes(b"stub")
        (root / "bundle_supervisor.py").write_text("# supervisor\n", encoding="utf-8")
        (root / "bundle_manifest.json").write_text(
            json.dumps(
                {
                    "schema": "vool.bundle_manifest.v1",
                    "selected_model": "qwen2.5:7b",
                    "model_store": "%LOCALAPPDATA%\\\\VOOL\\\\models",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        runtime_home = root / "runtime"
        (runtime_home / "run").mkdir(parents=True)
        (runtime_home / "run" / "model-pull.json").write_text(
            json.dumps({"status": "failed", "detail": "pull failed"}) + "\n",
            encoding="utf-8",
        )
        with mock.patch("installer.doctor.build_provider_registry_snapshot", return_value=snapshot):
            report = build_report(
                project_root=str(root),
                runtime_home=str(runtime_home),
                model_tag="wrong-model:latest",
                openclaw_enabled=False,
                openclaw_config_path="",
                openclaw_agent_dir="",
                ollama_binary="",
            )

    assert report["selected_model"] == "qwen2.5:7b"
    assert report["bundle"]["selected_model"] == "qwen2.5:7b"
    assert report["components"]["bundle_model_pull"]["ok"] is False
    assert report["provider_capability_truth"][0]["availability_state"] == "blocked"
    # Compare RESOLVED paths: on macOS /var is a symlink to /private/var, so the doctor's resolved
    # path and the raw tempfile path denote the same directory but differ as strings. Windows never
    # hits this, which is why it only shows up on the Apple lane.
    assert Path(report["bundle"]["components"]["model_store"]["path"]).resolve() == (runtime_home / "models").resolve()


def test_bundle_doctor_grades_shipped_launchers_without_source_install_artifacts() -> None:
    snapshot = mock.Mock(capability_truth=())
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "python").mkdir()
        (root / "ollama").mkdir()
        (root / "ollama" / "ollama.exe").write_bytes(b"stub")
        (root / "app" / "apps").mkdir(parents=True)
        (root / "app" / "apps" / "vool_api_server.py").write_text("# api\n", encoding="utf-8")
        for name in ("VOOL.cmd", "vool-open.ps1", "vool.vbs", "vool_window.py", "bundle_supervisor.py"):
            (root / name).write_text("# launcher\n", encoding="utf-8")
        (root / "bundle_manifest.json").write_text(
            json.dumps(
                {
                    "schema": "vool.bundle_manifest.v1",
                    "selected_model": "qwen2.5:7b",
                    "model_store": "%VOOL_HOME%\\models",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        runtime_home = root / "runtime"
        (runtime_home / "models").mkdir(parents=True)

        with mock.patch("installer.doctor.build_provider_registry_snapshot", return_value=snapshot):
            report = build_report(
                project_root=str(root),
                runtime_home=str(runtime_home),
                model_tag="qwen2.5:7b",
                openclaw_enabled=False,
                openclaw_config_path="",
                openclaw_agent_dir="",
                ollama_binary=str(root / "ollama" / "ollama.exe"),
                launch_agent_path=str(root / "vool-open.ps1"),
            )

    assert report["components"]["launchers"]["ok"] is True
    venv_report = report["components"]["venv"]
    # Compare RESOLVED paths: on macOS /var is a symlink to /private/var, so the doctor's resolved
    # path and the raw tempfile path denote the same directory but differ as strings. Windows never
    # hits this, which is why it only shows up on the Apple lane.
    assert Path(venv_report["path"]).resolve() == (root / ".venv").resolve()
    assert {key: value for key, value in venv_report.items() if key != "path"} == {
        "ok": True,
        "detail": "not required for self-contained bundle",
        "required": False,
        "present": False,
    }
    assert report["components"]["install_receipt"]["ok"] is True
    assert report["components"]["trace_surface"]["ok"] is True
    assert report["components"]["bundle_layout"]["ok"] is True
