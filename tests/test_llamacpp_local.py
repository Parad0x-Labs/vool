from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import installer.llamacpp_local as llamacpp_local
from installer.llamacpp_local import build_llamacpp_local_config, download_llamacpp_model, write_llamacpp_local_config

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_build_llamacpp_local_config_uses_runtime_home_defaults(tmp_path: Path) -> None:
    config = build_llamacpp_local_config(runtime_home=tmp_path, env={})

    assert config.profile_id == "local-max"
    assert config.backend == "llama.cpp"
    assert config.model_id == "qwen2.5:14b-gguf"
    assert config.repo_id == "Qwen/Qwen2.5-Coder-14B-Instruct-GGUF"
    assert config.filename == "qwen2.5-coder-14b-instruct-q4_k_m.gguf"
    model_path = Path(config.model_path)
    assert model_path.name == "qwen2.5-coder-14b-instruct-q4_k_m.gguf"
    assert model_path.parent.name == "llamacpp"
    assert model_path.parent.parent.name == "models"
    assert config.base_url == "http://127.0.0.1:8090/v1"
    assert config.port == 8090
    assert config.cache is True
    assert config.cache_type == "ram"
    assert config.draft_model == "prompt-lookup-decoding"
    assert config.draft_model_num_pred_tokens == 10


def test_build_llamacpp_local_config_parses_custom_int_env_overrides(tmp_path: Path) -> None:
    # Regression guard: _env_int previously had no int-parsing body (a stray
    # try/except had been misplaced after _env_bool's `return default`), so any
    # non-empty override silently became None instead of the parsed integer.
    config = build_llamacpp_local_config(
        runtime_home=tmp_path,
        env={
            "VOOL_LLAMACPP_N_GPU_LAYERS": "20",
            "VOOL_LLAMACPP_DRAFT_MODEL_NUM_PRED_TOKENS": "16",
        },
    )

    assert config.n_gpu_layers == 20
    assert config.draft_model_num_pred_tokens == 16


def test_env_int_falls_back_to_default_on_unparseable_value() -> None:
    assert llamacpp_local._env_int({"X": "not-a-number"}, "X", default=7) == 7
    assert llamacpp_local._env_int({"X": "42"}, "X", default=7) == 42
    assert llamacpp_local._env_int({}, "X", default=7) == 7


def test_write_llamacpp_local_config_persists_json(tmp_path: Path) -> None:
    config, target = write_llamacpp_local_config(
        runtime_home=tmp_path,
        env={"VOOL_LLAMACPP_MODEL_PATH": str(tmp_path / "weights.gguf")},
    )

    assert target == tmp_path / "config" / "llamacpp-local.json"
    assert target.exists()
    assert config.model_path == str((tmp_path / "weights.gguf").resolve())
    assert '"backend": "llama.cpp"' in target.read_text(encoding="utf-8")


def test_provision_llamacpp_local_script_emits_shell_env(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "installer" / "provision_llamacpp_local.py"),
            "--runtime-home",
            str(tmp_path),
            "--emit-shell-env",
        ],
        check=True,
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )

    output = completed.stdout
    assert "export LLAMACPP_BASE_URL=" in output
    assert "export VOOL_LLAMACPP_MODEL=qwen2.5:14b-gguf" in output
    assert "export VOOL_LLAMACPP_MODEL_PATH=" in output
    assert "export VOOL_LLAMACPP_CACHE=1" in output
    assert "export VOOL_LLAMACPP_DRAFT_MODEL=prompt-lookup-decoding" in output


def test_download_llamacpp_model_prefers_public_curl_download(monkeypatch, tmp_path: Path) -> None:
    config = build_llamacpp_local_config(runtime_home=tmp_path, env={})

    def fake_write(**_: object) -> tuple[llamacpp_local.LlamacppLocalConfig, Path]:
        return config, tmp_path / "config" / "llamacpp-local.json"

    def fake_run(command: list[str], check: bool = False) -> SimpleNamespace:
        target = Path(command[command.index("-o") + 1])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"gguf")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(llamacpp_local, "write_llamacpp_local_config", fake_write)
    monkeypatch.setattr(llamacpp_local.shutil, "which", lambda name: "/usr/bin/curl" if name == "curl" else None)
    monkeypatch.setattr(llamacpp_local.subprocess, "run", fake_run)

    resolved = download_llamacpp_model(runtime_home=tmp_path)

    assert resolved.model_path == config.model_path
    assert Path(config.model_path).read_bytes() == b"gguf"


def test_download_llamacpp_model_falls_back_to_hf_hub_when_public_download_fails(monkeypatch, tmp_path: Path) -> None:
    config = build_llamacpp_local_config(runtime_home=tmp_path, env={})
    calls: list[str] = []

    def fake_write(**_: object) -> tuple[llamacpp_local.LlamacppLocalConfig, Path]:
        return config, tmp_path / "config" / "llamacpp-local.json"

    def fake_public_download(**_: object) -> bool:
        calls.append("public")
        return False

    def fake_hf_download(**_: object) -> None:
        calls.append("hf")
        Path(config.model_path).parent.mkdir(parents=True, exist_ok=True)
        Path(config.model_path).write_bytes(b"hub")

    monkeypatch.setattr(llamacpp_local, "write_llamacpp_local_config", fake_write)
    monkeypatch.setattr(llamacpp_local, "_download_public_hf_file", fake_public_download)
    monkeypatch.setattr(llamacpp_local, "_download_via_hf_hub", fake_hf_download)

    resolved = download_llamacpp_model(runtime_home=tmp_path)

    assert resolved.model_path == config.model_path
    assert calls == ["public", "hf"]
    assert Path(config.model_path).read_bytes() == b"hub"
