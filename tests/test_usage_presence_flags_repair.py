"""SWITCHBOARD Repair 5: usage_input_reported and usage_output_reported must be recorded
independently in the PERSISTED usage_meter ledger -- not one combined flag written into both
columns.

Before this repair, `memory_first_router._try_free_cloud_boost` recorded:
    prompt_tokens_reported=normalized.usage_reported, output_tokens_reported=normalized.usage_reported
where `usage_reported` was `(input_reported OR output_reported)` on the single, combined
`NormalizedProviderResult` field -- so a response whose usage payload carried only ONE side of the
count (a real shape) recorded BOTH ledger columns as reported, fabricating a "the provider told us
this" claim for the field it never touched.

This drives the actual `core.memory_first_router.MemoryFirstRouter._try_free_cloud_boost` path
against a stub OpenRouter transport (no network) and reads back the real `token_usage` row
`core.usage_meter` wrote -- not just the in-memory `NormalizedProviderResult` (see
tests/test_normalized_provider_result.py for the unit-level 6-case matrix on the normalize
functions themselves).
"""
from __future__ import annotations

from types import SimpleNamespace

from core import usage_meter as um
from core.cloud_provider_contract import CloudTaskRequirements, PrivacyClass

DUMMY_KEY = "sk-or-v1-DUMMY-NOT-A-REAL-KEY"

LIVE_SHAPE_PAYLOAD = {
    "data": [
        {
            "id": "vendor/free-chat:free",
            "name": "Free Chat",
            "context_length": 262144,
            "pricing": {"prompt": "0", "completion": "0"},
            "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
            "supported_parameters": ["tools"],
        },
    ]
}


def _requirements() -> CloudTaskRequirements:
    return CloudTaskRequirements(
        min_context_tokens=1000,
        expected_output_tokens=200,
        required_capabilities=("text",),
        privacy_class=PrivacyClass.PUBLIC,
    )


class _AsymmetricUsageTransport:
    """Answers OpenRouter's endpoints; the completion response's `usage` deliberately carries
    only ONE side of the token count -- the exact shape that exposed the OR'd-flag defect."""

    def __init__(self, usage: dict) -> None:
        self._usage = usage
        self.bodies: list[dict] = []

    def request_json(self, *, method, url, headers=None, body=None, credential_name="",
                     credential_env="", credential_scheme="bearer", timeout_seconds=30.0):
        if url.endswith("/models"):
            return 200, {}, LIVE_SHAPE_PAYLOAD
        if url.endswith("/auth/key"):
            return 200, {}, {"data": {"limit_remaining": None, "is_free_tier": True}}
        if url.endswith("/chat/completions"):
            self.bodies.append(dict(body or {}))
            return 200, {}, {
                "choices": [{"message": {"content": "free lane answer"}}],
                "usage": self._usage,
            }
        raise AssertionError(f"unexpected url {url}")


def _reset_usage_table() -> None:
    um._SCHEMA_READY_PATHS.clear()
    from storage.db import get_connection

    conn = get_connection()
    try:
        conn.execute("DROP TABLE IF EXISTS token_usage")
        conn.commit()
    finally:
        conn.close()


def _run_free_cloud_boost(monkeypatch, *, usage: dict):
    monkeypatch.setenv("OPENROUTER_API_KEY", DUMMY_KEY)
    from adapters.base_adapter import ModelRequest
    from adapters.openrouter_cloud_provider import OpenRouterCloudProvider
    from core.cloud_broker import CloudModelBroker
    from core.cloud_privacy_policy import CloudPrivacyGrant
    from core.memory_first_router import MemoryFirstRouter

    provider = OpenRouterCloudProvider()
    transport = _AsymmetricUsageTransport(usage)
    registry = SimpleNamespace(
        get=lambda pid: provider if pid == "openrouter" else None,
        list=lambda: [provider],
    )
    router = MemoryFirstRouter(cloud_broker=CloudModelBroker(registry=registry, transports={"openrouter": transport}))
    monkeypatch.setattr(
        "core.memory_first_router.cloud_escalation_policy.load_policy",
        lambda: SimpleNamespace(free_cloud_enabled=True),
    )
    return router._try_free_cloud_boost(
        request=ModelRequest(
            task_kind="chat",
            prompt="hello",
            messages=[{"role": "user", "content": "hello"}],
            output_mode="plain_text",
            max_output_tokens=200,
        ),
        task=SimpleNamespace(task_id="usage-flags-task"),
        task_hash="usage-flags-hash",
        output_mode="plain_text",
        source_context={
            "surface": "cli",
            "session_id": "usage-flags-session",
            "turn_id": "usage-flags-turn",
            "cloud_task_requirements": _requirements(),
            "cloud_privacy_grant": CloudPrivacyGrant(),
        },
    )


def _latest_row():
    from storage.db import get_connection

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT prompt_tokens, output_tokens, prompt_tokens_reported, output_tokens_reported "
            "FROM token_usage ORDER BY created_ts DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    return row


def test_only_completion_present_records_distinct_flags_not_both_true(monkeypatch) -> None:
    """The exact defect: usage={"completion_tokens": 12} (no prompt count at all) used to record
    prompt_tokens_reported=True in the persisted ledger row -- a fabricated claim about a field
    the provider never sent."""
    _reset_usage_table()
    decision = _run_free_cloud_boost(monkeypatch, usage={"completion_tokens": 12, "cost": 0})
    assert decision is not None

    row = _latest_row()
    assert row is not None
    prompt_tokens, output_tokens, prompt_reported, output_reported = row
    assert output_tokens == 12
    assert output_reported == 1
    assert prompt_tokens == 0
    assert prompt_reported == 0, "prompt_tokens_reported must not be fabricated from the output field"


def test_only_prompt_present_records_distinct_flags_not_both_true(monkeypatch) -> None:
    _reset_usage_table()
    decision = _run_free_cloud_boost(monkeypatch, usage={"prompt_tokens": 9, "cost": 0})
    assert decision is not None

    row = _latest_row()
    assert row is not None
    prompt_tokens, output_tokens, prompt_reported, output_reported = row
    assert prompt_tokens == 9
    assert prompt_reported == 1
    assert output_tokens == 0
    assert output_reported == 0, "output_tokens_reported must not be fabricated from the prompt field"


def test_both_present_records_both_reported(monkeypatch) -> None:
    """Control: the ordinary, common case is unaffected by the split."""
    _reset_usage_table()
    decision = _run_free_cloud_boost(monkeypatch, usage={"prompt_tokens": 9, "completion_tokens": 12, "cost": 0})
    assert decision is not None

    row = _latest_row()
    assert row is not None
    assert tuple(row) == (9, 12, 1, 1)
