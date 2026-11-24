"""A local model's answer check is read for the identity running NOW, and boot never leans on a model too big to use.

Measured 2026-11-24 on sls's iMac profile (installed 6f5532699, Ollama 0.40.14): every turn pinned to
local qwen3:8b was refused with "hasn't passed VOOL's answer check yet". The store held a verified
qwen3:8b run under 0.40.14 (6 Nov) and a newer verified one under 0.40.3 (23 Nov). Only the newest run
was read; its identity was not the running one, so the model read as stale. Boot did not check it
again either, because a verified qwen3.8:27b, too large for that computer and never used for turns,
counted as "a local model is ready".
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from core.local_model_readiness import ready_local_models
from core.local_model_tool_certification import certification_status, run_local_model_tool_certification
from storage.model_provider_manifest import ModelProviderManifest
from tests.test_local_model_tool_certification import ProbeExchange


def _manifest() -> ModelProviderManifest:
    return ModelProviderManifest(
        provider_name="ollama-local",
        model_name="qwen-test:8b",
        source_type="http",
        adapter_type="openai_compatible",
        runtime_config={"base_url": "http://127.0.0.1:11434"},
        metadata={"runtime_family": "ollama", "model_digest": "sha256:same", "chat_template_hash": "t1",
                  "quantization": "q4_K_M"},
    )


def _check(db: Path, backend: str, **probe) -> dict:
    return run_local_model_tool_certification(_manifest(), exchange=ProbeExchange(**probe), backend_version=backend,
                                              db_path=db)


def test_a_newer_run_under_another_backend_does_not_hide_the_one_under_the_running_backend(tmp_path):
    db = tmp_path / "c.db"
    _check(db, "0.40.14")
    _check(db, "0.40.3")
    assert certification_status(_manifest(), backend_version="0.40.14", db_path=db)["state"] == "verified"
    assert certification_status(_manifest(), backend_version="0.40.3", db_path=db)["state"] == "verified"


def test_the_newest_run_under_the_running_backend_decides_even_when_it_failed(tmp_path):
    db = tmp_path / "c.db"
    _check(db, "0.40.14")
    _check(db, "0.40.14", first_mode="wrong_args")
    _check(db, "0.40.3")
    assert certification_status(_manifest(), backend_version="0.40.14", db_path=db)["state"] != "verified"


def test_a_backend_no_run_was_taken_under_is_still_stale(tmp_path):
    db = tmp_path / "c.db"
    _check(db, "0.40.14")
    status = certification_status(_manifest(), backend_version="0.41.0", db_path=db)
    assert status["state"] == "stale" and status["current_fingerprint"] != status["fingerprint"]


def _named(name: str) -> SimpleNamespace:
    return SimpleNamespace(model_name=name, provider_name="ollama-local", adapter_type="openai_compatible",
                           runtime_config={"base_url": "http://127.0.0.1:11434"}, metadata={})


def test_boot_checks_the_stale_primary_when_the_only_verified_model_cannot_fit(monkeypatch):
    monkeypatch.setattr("core.local_model_tool_certification.certification_applies", lambda _m: True)
    states = {"qwen3.8:27b": "verified", "qwen3:8b": "stale"}
    ran = []
    rows = ready_local_models(
        [_named("qwen3.8:27b"), _named("qwen3:8b")],
        primary_model="qwen3:8b",
        status_of=lambda m: {"state": states[m.model_name]},
        run_check=lambda m: ran.append(m.model_name) or {"state": "verified"},
        fits=lambda m: m.model_name != "qwen3.8:27b",
    )
    assert ran == ["qwen3:8b"], rows


def test_boot_still_does_nothing_when_a_verified_model_fits(monkeypatch):
    monkeypatch.setattr("core.local_model_tool_certification.certification_applies", lambda _m: True)
    ran = []
    rows = ready_local_models(
        [_named("qwen3:4b"), _named("qwen3:8b")],
        primary_model="qwen3:8b",
        status_of=lambda m: {"state": "verified" if m.model_name == "qwen3:4b" else "stale"},
        run_check=lambda m: ran.append(m.model_name) or {"state": "verified"},
        fits=lambda m: True,
    )
    assert rows == [] and ran == []
