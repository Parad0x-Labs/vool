from __future__ import annotations

import json
import unittest.mock as mock

from core.web0_capability_broadcast import (
    DEFAULT_PRICE_PER_TOKEN,
    Web0CapabilityManifest,
    announce,
    announce_from_env,
    build_manifest,
    build_manifest_from_env,
)


def test_build_manifest_returns_correct_defaults() -> None:
    m = build_manifest(worker_id="vool", provider_ids=("mlx-local:qwen3",))
    assert m.worker_id == "vool"
    assert m.provider_ids == ("mlx-local:qwen3",)
    assert m.price_per_token_usdc == DEFAULT_PRICE_PER_TOKEN
    assert m.privacy_mode == "plain"
    assert m.announced_at > 0


def test_build_manifest_from_env_uses_worker_id() -> None:
    m = build_manifest_from_env(env={"VOOL_WORKER_ID": "node-abc"})
    assert m.worker_id == "node-abc"


def test_build_manifest_from_env_defaults_to_vool() -> None:
    m = build_manifest_from_env(env={})
    assert m.worker_id == "vool"


def test_announce_from_env_no_ops_when_gate_off() -> None:
    m = build_manifest(worker_id="vool", provider_ids=())
    assert announce_from_env(m, env={}) is False


def test_announce_from_env_no_ops_when_url_missing() -> None:
    m = build_manifest(worker_id="vool", provider_ids=())
    assert announce_from_env(m, env={"VOOL_WEB0_ANNOUNCE": "1"}) is False


def test_manifest_to_dict_is_json_serialisable() -> None:
    m = build_manifest(
        worker_id="vool",
        provider_ids=("mlx-local:qwen3",),
        top_tps=31.3,
        top_tier="queen",
        tools=("code", "bash"),
    )
    d = m.to_dict()
    json.dumps(d)
    assert d["top_tps"] == 31.3
    assert d["top_tier"] == "queen"
    assert "code" in d["tools"]


def test_zk_attest_fn_is_called_and_payload_included() -> None:
    received: list[Web0CapabilityManifest] = []

    def fake_zk(manifest: Web0CapabilityManifest) -> str:
        received.append(manifest)
        return "zk-stub-proof"

    m = build_manifest(worker_id="vool", provider_ids=())
    captured: dict = {}

    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_door(url, *, data=None, headers=None, method="", timeout=0.0, context=None):
        captured["url"] = url
        captured["data"] = data
        return _Resp()

    with mock.patch("core.remote_fetch_policy.open_remote_url", side_effect=fake_door):
        result = announce(m, mesh_url="http://localhost:9999", zk_attest_fn=fake_zk)

    assert result is True
    assert len(received) == 1
    assert received[0] is m
    assert captured["url"] == "http://localhost:9999/v1/workers/announce"
    call_payload = json.loads(captured["data"])
    assert call_payload["zk_attestation"] == "zk-stub-proof"


def test_announce_returns_false_on_http_error() -> None:
    m = build_manifest(worker_id="vool", provider_ids=())

    class _Resp:
        status = 500

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    with mock.patch("core.remote_fetch_policy.open_remote_url", return_value=_Resp()):
        assert announce(m, mesh_url="http://localhost:9999") is False


def test_privacy_mode_plain_is_default() -> None:
    m = build_manifest(worker_id="n", provider_ids=())
    assert m.privacy_mode == "plain"


def test_privacy_mode_zk_ready_can_be_set() -> None:
    m = build_manifest(worker_id="n", provider_ids=(), privacy_mode="zk_ready")
    assert m.privacy_mode == "zk_ready"
