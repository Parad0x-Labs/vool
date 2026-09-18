from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from core.local_model_tool_certification import (
    CERTIFICATION_ROUTING_EFFECT,
    CertificationBoundaryError,
    certification_fingerprint_payload,
    certification_status,
    run_local_model_tool_certification,
)
from storage.migrations import run_migrations
from storage.model_provider_manifest import ModelProviderManifest


def _manifest(
    *,
    base_url: str = "http://127.0.0.1:11434",
    digest: str = "sha256:model-v1",
    adapter_type: str = "openai_compatible",
) -> ModelProviderManifest:
    return ModelProviderManifest(
        provider_name="ollama-local",
        model_name="qwen-test:4b",
        source_type="http",
        adapter_type=adapter_type,
        runtime_config={"base_url": base_url},
        metadata={
            "runtime_family": "ollama",
            "model_digest": digest,
            "chat_template_hash": "template-v1",
            "quantization": "q4_K_M",
        },
    )


def _call(name: str, arguments: Any, call_id: str) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def _reply(*calls: dict[str, Any], content: str = "", status: int = 200) -> dict[str, Any]:
    return {
        "status_code": status,
        "latency_ms": 5.0,
        "dialect": "openai",
        "body": {
            "model": "qwen-test:4b",
            "choices": [
                {
                    "finish_reason": "tool_calls" if calls else "stop",
                    "message": {"role": "assistant", "content": content, "tool_calls": list(calls)},
                }
            ],
        },
    }


class ProbeExchange:
    def __init__(
        self,
        *,
        first_mode: str = "valid",
        continuation_ok: bool = True,
        timeout: bool = False,
    ) -> None:
        self.first_mode = first_mode
        self.continuation_ok = continuation_ok
        self.timeout = timeout
        self.calls: list[dict[str, Any]] = []
        self.parallel_attempts = 0

    def __call__(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.timeout:
            raise TimeoutError("local generation timed out with Bearer super-secret")
        tools = tuple(kwargs.get("tools") or ())
        intents = {tool.intent for tool in tools}
        messages = list(kwargs.get("messages") or [])
        if intents == {"probe.add", "probe.lookup_nonce"}:
            self.parallel_attempts += 1
            if self.parallel_attempts == 1 and self.first_mode == "malformed":
                return _reply(
                    _call("vool_probe_add", '{"left":19,', "call-add"),
                    _call("vool_probe_lookup_nonce", '{"key":"alpha"}', "call-nonce"),
                )
            if self.parallel_attempts == 1 and self.first_mode == "wrong_args":
                return _reply(
                    _call("vool_probe_add", '{"left":20,"right":23}', "call-add"),
                    _call("vool_probe_lookup_nonce", '{"key":"alpha"}', "call-nonce"),
                )
            if self.parallel_attempts == 1 and self.first_mode == "duplicate":
                return _reply(
                    _call("vool_probe_add", '{"left":19,"right":23}', "same-id"),
                    _call("vool_probe_lookup_nonce", '{"key":"alpha"}', "same-id"),
                )
            return _reply(
                _call("vool_probe_add", '{"left":19,"right":23}', "call-add"),
                _call("vool_probe_lookup_nonce", '{"key":"alpha"}', "call-nonce"),
            )
        if not tools:
            nonce = "missing"
            for message in messages:
                if message.get("role") != "tool":
                    continue
                try:
                    body = json.loads(str(message.get("content") or "{}"))
                except json.JSONDecodeError:
                    continue
                if body.get("nonce"):
                    nonce = body["nonce"]
            return _reply(content=f"CERTIFIED sum=42 nonce={nonce if self.continuation_ok else 'wrong'}")
        if intents == {"probe.echo"}:
            has_error_result = any(
                message.get("role") == "tool" and "synthetic_validation_error" in str(message.get("content") or "")
                for message in messages
            )
            token = "recovered" if has_error_result else "repair-me"
            return _reply(_call("vool_probe_echo", json.dumps({"token": token}), "call-echo"))
        raise AssertionError(f"unexpected probe tools: {intents}")


def test_valid_probe_verifies_parallel_translation_continuation_and_recovery(tmp_path: Path) -> None:
    exchange = ProbeExchange()
    result = run_local_model_tool_certification(
        _manifest(), exchange=exchange, backend_version="0.21.2", db_path=tmp_path / "probe.db"
    )

    assert result["state"] == "verified"
    assert result["successful"] is True
    # A verified run is now the thing that lets this model take the final-answer author role
    # (`core.final_answer_authorship`). The row says so; it used to say `routing_effect: "none"`
    # while the measurement was read by nobody.
    assert result["observe_only"] is False
    assert result["routing_effect"] == CERTIFICATION_ROUTING_EFFECT
    assert all(stage["state"] == "passed" for stage in result["stages"].values())
    assert "parallel_calls" in result["stages"]["model_emission"]["checks"]
    assert "synthetic_error_recovered" in result["stages"]["result_continuation"]["checks"]
    assert len(exchange.calls) == 4
    assert all({tool.intent for tool in call.get("tools", ())} <= {"probe.echo", "probe.add", "probe.lookup_nonce"} for call in exchange.calls)


@pytest.mark.parametrize("first_mode", ["malformed", "duplicate"])
def test_malformed_or_duplicate_batch_is_never_dispatched_and_can_recover(
    tmp_path: Path, first_mode: str
) -> None:
    exchange = ProbeExchange(first_mode=first_mode)
    result = run_local_model_tool_certification(
        _manifest(), exchange=exchange, db_path=tmp_path / f"{first_mode}.db"
    )

    assert result["state"] == "verified"
    assert exchange.parallel_attempts == 2
    assert "invalid_call_recovered" in result["stages"]["model_emission"]["checks"]
    assert result["evidence"]["raw_payloads_persisted"] is False


def test_schema_valid_but_wrong_arguments_are_a_model_emission_failure(tmp_path: Path) -> None:
    result = run_local_model_tool_certification(
        _manifest(), exchange=ProbeExchange(first_mode="wrong_args"), db_path=tmp_path / "wrong.db"
    )
    assert result["state"] == "degraded"
    assert result["stages"]["model_emission"]["failure_code"] == "wrong_arguments"


def test_wrong_result_continuation_is_not_called_verified(tmp_path: Path) -> None:
    result = run_local_model_tool_certification(
        _manifest(), exchange=ProbeExchange(continuation_ok=False), db_path=tmp_path / "continuation.db"
    )
    assert result["state"] == "degraded"
    assert result["stages"]["result_continuation"]["failure_code"] == "wrong_continuation"


def test_timeout_is_typed_and_evidence_redacts_secrets_and_home_paths(tmp_path: Path) -> None:
    class UnsafeTimeout(ProbeExchange):
        def __call__(self, **kwargs: Any) -> dict[str, Any]:
            raise TimeoutError(
                "request timed out Bearer super-secret token=abc123 /Users/alice/private/model.gguf"
            )

    result = run_local_model_tool_certification(
        _manifest(), exchange=UnsafeTimeout(), db_path=tmp_path / "timeout.db"
    )
    serialized = json.dumps(result, sort_keys=True)
    assert result["state"] == "degraded"
    assert result["stages"]["transport_acceptance"]["failure_code"] == "timeout"
    assert "super-secret" not in serialized
    assert "abc123" not in serialized
    assert "/Users/alice" not in serialized
    assert "[redacted]" in serialized
    assert "[home]" in serialized


@pytest.mark.parametrize(
    "url",
    [
        "https://api.openai.com/v1",
        "http://10.0.0.4:8000",
        "http://0.0.0.0:8000",
        "http://127.0.0.1.evil.example:8000",
    ],
)
def test_remote_paid_or_wildcard_lanes_are_refused_before_exchange(tmp_path: Path, url: str) -> None:
    exchange = ProbeExchange()
    with pytest.raises(CertificationBoundaryError, match="loopback"):
        run_local_model_tool_certification(
            _manifest(base_url=url), exchange=exchange, db_path=tmp_path / "refused.db"
        )
    assert exchange.calls == []


def test_shipped_local_qwen_adapter_uses_the_same_sealed_exchange_contract(tmp_path: Path) -> None:
    result = run_local_model_tool_certification(
        _manifest(adapter_type="local_qwen_provider"),
        exchange=ProbeExchange(),
        db_path=tmp_path / "local-qwen.db",
    )

    assert result["state"] == "verified"
    assert result["fingerprint_components"]["adapter_type"] == "local_qwen_provider"


def test_non_openai_compatible_local_adapter_is_still_refused(tmp_path: Path) -> None:
    exchange = ProbeExchange()
    with pytest.raises(CertificationBoundaryError, match="OpenAI-compatible"):
        run_local_model_tool_certification(
            _manifest(adapter_type="local_subprocess"),
            exchange=exchange,
            db_path=tmp_path / "incompatible-adapter.db",
        )
    assert exchange.calls == []


def test_fingerprint_covers_every_required_identity_component() -> None:
    payload = certification_fingerprint_payload(_manifest(), backend_version="0.21.2")
    assert payload == {
        "adapter_type": "openai_compatible",
        "adapter_version": "openai-compatible-tool-certification.v1",
        "backend_version": "0.21.2",
        "base_identity": "http://127.0.0.1:11434",
        "model_name": "qwen-test:4b",
        "model_digest": "sha256:model-v1",
        "template_hash": "template-v1",
        "quantization": "q4_K_M",
        "schema_version": "vool.local-tool-certification.schema.v1",
        "probe_version": "vool.local-tool-certification.v1",
    }


def test_changed_model_fingerprint_makes_old_evidence_stale(tmp_path: Path) -> None:
    db_path = tmp_path / "stale.db"
    run_local_model_tool_certification(_manifest(), exchange=ProbeExchange(), db_path=db_path)

    status = certification_status(_manifest(digest="sha256:model-v2"), db_path=db_path)

    assert status["state"] == "stale"
    assert status["current_fingerprint"] != status["fingerprint"]
    assert status["routing_effect"] == CERTIFICATION_ROUTING_EFFECT


@pytest.mark.parametrize("adapter_type", ["openai_compatible", "local_qwen_provider"])
def test_status_uses_current_backend_identity_instead_of_immediately_staling_a_live_run(
    tmp_path: Path, monkeypatch, adapter_type: str
) -> None:
    db_path = tmp_path / "backend-version.db"
    run_local_model_tool_certification(
        _manifest(adapter_type=adapter_type),
        exchange=ProbeExchange(),
        backend_version="0.21.2",
        db_path=db_path,
    )
    monkeypatch.setattr(
        "adapters.openai_compatible_adapter.OpenAICompatibleAdapter.tool_certification_runtime_identity",
        lambda _self: {"backend_version": "0.21.2"},
    )

    status = certification_status(_manifest(adapter_type=adapter_type), db_path=db_path)

    assert status["state"] == "verified"


def test_two_failures_after_a_verified_run_confirm_regression_and_withdraw_authorship(tmp_path: Path) -> None:
    db_path = tmp_path / "hysteresis.db"
    run_local_model_tool_certification(_manifest(), exchange=ProbeExchange(), db_path=db_path)
    first = run_local_model_tool_certification(
        _manifest(), exchange=ProbeExchange(first_mode="wrong_args"), db_path=db_path
    )
    second = run_local_model_tool_certification(
        _manifest(), exchange=ProbeExchange(first_mode="wrong_args"), db_path=db_path
    )
    assert first["regression_confirmed"] is False
    assert second["regression_confirmed"] is True
    assert second["consecutive_failures"] == 2
    # Hysteresis is still evidence rather than a second authority -- but the row it produces is
    # now consumed, and a `degraded` state is not a certification, so the model that regressed
    # cannot go on authoring on the strength of the verified run it used to hold.
    assert second["routing_effect"] == CERTIFICATION_ROUTING_EFFECT
    assert second["state"] != "verified"


def test_migration_adds_certification_history_without_damaging_legacy_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE legacy_keep (value TEXT NOT NULL)")
    conn.execute("INSERT INTO legacy_keep(value) VALUES ('preserved')")
    conn.commit()
    conn.close()

    run_migrations(db_path=db_path)

    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT value FROM legacy_keep").fetchone()[0] == "preserved"
        columns = {row[1] for row in conn.execute("PRAGMA table_info(local_model_tool_certification_runs)")}
        assert {"fingerprint", "stages_json", "regression_confirmed", "observe_only"} <= columns
    finally:
        conn.close()
