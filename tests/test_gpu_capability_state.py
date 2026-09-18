from __future__ import annotations

from pathlib import Path

from core.gpu_capability_state import (
    apply_persisted_cpu_fallback,
    gpu_capability_path,
    load_gpu_capability,
    save_gpu_capability,
)


def test_save_then_load_round_trips_the_v1_record(tmp_path: Path) -> None:
    save_gpu_capability(
        tmp_path,
        {
            "outcome": "driver_too_old",
            "model": "qwen2.5:7b",
            "driver_version": "566.36",
            "applied_cpu_fallback": True,
        },
    )
    path = gpu_capability_path(tmp_path)
    assert path.exists()
    assert path == (tmp_path / "config" / "gpu_capability.json").resolve()

    loaded = load_gpu_capability(tmp_path)
    assert loaded == {
        "schema": "vool.gpu_capability.v1",
        "outcome": "driver_too_old",
        "model": "qwen2.5:7b",
        "driver_version": "566.36",
        "applied_cpu_fallback": True,
    }


def test_save_coerces_partial_verdict_and_defaults_flag(tmp_path: Path) -> None:
    save_gpu_capability(tmp_path, {"outcome": "ok", "model": "qwen3:8b"})
    loaded = load_gpu_capability(tmp_path)
    assert loaded is not None
    assert loaded["outcome"] == "ok"
    assert loaded["model"] == "qwen3:8b"
    assert loaded["driver_version"] == ""
    assert loaded["applied_cpu_fallback"] is False


def test_load_missing_file_is_none(tmp_path: Path) -> None:
    assert load_gpu_capability(tmp_path) is None


def test_load_corrupt_file_is_none(tmp_path: Path) -> None:
    path = gpu_capability_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid json", encoding="utf-8")
    assert load_gpu_capability(tmp_path) is None


def test_load_wrong_schema_is_none(tmp_path: Path) -> None:
    path = gpu_capability_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"schema": "vool.gpu_capability.v0", "outcome": "ok"}', encoding="utf-8")
    assert load_gpu_capability(tmp_path) is None


def test_apply_persisted_cpu_fallback_sets_num_gpu_zero_when_fallback(tmp_path: Path) -> None:
    save_gpu_capability(
        tmp_path,
        {"outcome": "vram_insufficient", "model": "qwen3:8b", "applied_cpu_fallback": True},
    )
    env: dict[str, str] = {}
    assert apply_persisted_cpu_fallback(tmp_path, env=env) is True
    assert env["VOOL_OLLAMA_NUM_GPU"] == "0"


def test_apply_persisted_cpu_fallback_noop_without_fallback(tmp_path: Path) -> None:
    save_gpu_capability(tmp_path, {"outcome": "ok", "model": "qwen3:8b", "applied_cpu_fallback": False})
    env: dict[str, str] = {}
    assert apply_persisted_cpu_fallback(tmp_path, env=env) is False
    assert "VOOL_OLLAMA_NUM_GPU" not in env


def test_apply_persisted_cpu_fallback_does_not_override_explicit_env(tmp_path: Path) -> None:
    save_gpu_capability(
        tmp_path,
        {"outcome": "driver_too_old", "model": "qwen2.5:7b", "applied_cpu_fallback": True},
    )
    env = {"VOOL_OLLAMA_NUM_GPU": "20"}
    assert apply_persisted_cpu_fallback(tmp_path, env=env) is False
    assert env["VOOL_OLLAMA_NUM_GPU"] == "20"


def test_apply_persisted_cpu_fallback_missing_verdict_is_noop(tmp_path: Path) -> None:
    env: dict[str, str] = {}
    assert apply_persisted_cpu_fallback(tmp_path, env=env) is False
    assert "VOOL_OLLAMA_NUM_GPU" not in env
