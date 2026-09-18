from __future__ import annotations

import json

from installer.persist_windows_runtime_config import persist_windows_runtime_config


def test_persist_windows_runtime_config_writes_profile_and_provider_env(tmp_path) -> None:
    profile_record, provider_env = persist_windows_runtime_config(
        runtime_home=str(tmp_path),
        install_profile="local-only",
        model_tag="gemma3:4b",
        selected_models_csv="gemma3:4b,qwen2.5:7b",
        ollama_models_dir="G:\\Ollama\\models",
    )

    profile_payload = json.loads(profile_record.read_text(encoding="utf-8"))
    provider_text = provider_env.read_text(encoding="utf-8")

    assert profile_payload["profile_id"] == "local-only"
    assert profile_payload["selected_model"] == "gemma3:4b"
    assert profile_payload["selected_models"] == ["gemma3:4b", "qwen2.5:7b"]
    assert "export VOOL_INSTALL_PROFILE=local-only" in provider_text
    assert "export VOOL_OLLAMA_MODEL=gemma3:4b" in provider_text
    assert "export VOOL_REGISTER_INSTALLED_OLLAMA_MODELS=1" in provider_text
    assert "G:\\Ollama\\models" in provider_text
