from __future__ import annotations

from unittest import mock

from core.provider_routing import ProviderCapabilityTruth
from installer.write_install_receipt import build_receipt


def test_install_receipt_includes_enabled_web_stack_defaults() -> None:
    with mock.patch(
        "installer.write_install_receipt.build_provider_registry_snapshot",
        return_value=mock.Mock(capability_truth=()),
    ):
        receipt = build_receipt(
            project_root="/tmp/vool",
            runtime_home="/tmp/vool-home",
            model_tag="qwen2.5:7b",
            openclaw_enabled=True,
            openclaw_config_path="/tmp/openclaw.json",
            openclaw_agent_dir="/tmp/agent",
            ollama_binary="ollama",
        )

    web_stack = receipt["web_stack"]
    assert web_stack["provider_order"] == ["searxng", "duckduckgo_html", "ddg_instant"]
    assert web_stack["searxng_url"] == "http://127.0.0.1:8080"
    assert web_stack["playwright_enabled"] is True
    assert web_stack["browser_engine"] == "chromium"


def _empty_snapshot() -> mock.Mock:
    # Isolate from the live provider-registry snapshot (which would try to reach a
    # local Ollama and is blocked under pytest); the wallet fields don't depend on it.
    return mock.Mock(capability_truth=())


def test_install_receipt_surfaces_agent_wallet_pubkey_only() -> None:
    pubkey = "5EKQBbMQE6S4LcNP3vuFiFUDbPDiJ5JheUeaWaKxK8Dd"
    with mock.patch(
        "installer.write_install_receipt.build_provider_registry_snapshot",
        return_value=_empty_snapshot(),
    ):
        receipt = build_receipt(
            project_root="/tmp/vool",
            runtime_home="/tmp/vool-home",
            model_tag="qwen2.5:7b",
            openclaw_enabled=True,
            openclaw_config_path="/tmp/openclaw.json",
            openclaw_agent_dir="/tmp/agent",
            ollama_binary="ollama",
            agent_wallet_pubkey=pubkey,
        )

    wallet = receipt["agent_wallet"]
    assert wallet["pubkey"] == pubkey
    assert wallet["created_at_install"] is True
    assert wallet["storage"] == "encrypted_local:aes-256-gcm"
    # The receipt must NEVER carry private-key material of any kind. Check for key-material
    # markers specifically -- a bare "private" substring collides with resolved filesystem
    # paths (macOS resolves /tmp -> /private/tmp), which are not secrets.
    blob = str(receipt).lower()
    for marker in ("private_key", "privatekey", "private key", "private-key", "privkey", "priv_key"):
        assert marker not in blob, marker
    assert "seed" not in blob
    assert "ciphertext" not in blob


def test_install_receipt_wallet_absent_is_marked_not_created() -> None:
    with mock.patch(
        "installer.write_install_receipt.build_provider_registry_snapshot",
        return_value=_empty_snapshot(),
    ):
        receipt = build_receipt(
            project_root="/tmp/vool",
            runtime_home="/tmp/vool-home",
            model_tag="qwen2.5:7b",
            openclaw_enabled=False,
            openclaw_config_path="",
            openclaw_agent_dir="",
            ollama_binary="ollama",
        )

    assert receipt["agent_wallet"]["pubkey"] == ""
    assert receipt["agent_wallet"]["created_at_install"] is False


def test_install_receipt_uses_provider_snapshot_truth_for_profile_and_output() -> None:
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
                queue_depth=0,
                max_safe_concurrency=2,
            ),
        )
    )

    with mock.patch("installer.write_install_receipt.build_provider_registry_snapshot", return_value=snapshot), mock.patch.dict(
        "os.environ",
        {"VOOL_INSTALL_PROFILE": "hybrid-kimi", "KIMI_API_KEY": "test-key"},
        clear=False,
    ):
        receipt = build_receipt(
            project_root="/tmp/vool",
            runtime_home="/tmp/vool-home",
            model_tag="qwen2.5:7b",
            openclaw_enabled=True,
            openclaw_config_path="/tmp/openclaw.json",
            openclaw_agent_dir="/tmp/agent",
            ollama_binary="ollama",
        )

    truth = receipt["provider_capability_truth"]
    provider_ids = {item["provider_id"] for item in truth}
    mix_ids = {item["provider_id"] for item in receipt["install_profile"]["provider_mix"]}
    recommendation = receipt["install_recommendation"]
    assert provider_ids == {"ollama-local:qwen2.5:7b", "kimi-remote:kimi-k2"}
    assert mix_ids <= provider_ids
    assert "kimi-remote:kimi-k2" in mix_ids
    assert recommendation["recommended_default_profile"] == "local-only"
    assert recommendation["primary_local_model"] == "qwen2.5:7b"
    queen_lane = next(item for item in receipt["install_profile"]["provider_mix"] if item["role"] == "queen")
    assert queen_lane["availability_state"] == "ready"
