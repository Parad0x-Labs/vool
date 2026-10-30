"""Wallet-wide settings that loosen what can be paid need the wallet's own credential, as raising a cap does.

Red-team M1 follow-up (0.7.7, decided by coordinator on sls's delegation 2026-10-30): /api/wallet/environment,
/channels, /ai-endpoints and /dna-fees/policy were reachable from any process on this machine (curl to loopback, no
Origin) with no PIN. Switching to Mainnet, turning a payment channel or a pay-per-answer service on, adding one, and
changing the fee threshold now need the PIN, password or Touch ID; the safe direction (Test networks, off, remove,
list) never asks. With no wallet that holds a key VOOL could sign with, nothing can pay, so nothing is asked.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from tests.asgi_harness import asgi_request
from tests.wallet._device_auth_fake import FakeDeviceAuthority

pytestmark = [pytest.mark.safety]

PIN = "482913"
CURL = {"Host": "127.0.0.1", "Content-Type": "application/json"}
DEVNET = "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"


@pytest.fixture
def app(wallet_env, monkeypatch):
    monkeypatch.delenv("VOOL_WALLET_UI_CAPABILITY_SHA256", raising=False)
    from apps.vool_api_server import create_app
    from core.wallet import device_auth
    from core.web.api import wallet_api
    from core.web.api.runtime import RuntimeServices

    device_auth.set_device_authority_for_tests(FakeDeviceAuthority())
    wallet_api.reset_caller_binding_for_tests()
    yield create_app(RuntimeServices(display_name="VOOL"))
    wallet_api.reset_caller_binding_for_tests()
    device_auth.set_device_authority_for_tests(None)


def _post(app, path: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    status, _headers, raw = asgi_request(app, method="POST", path=path, headers=CURL, body=json.dumps(body).encode())
    return status, json.loads(raw or b"{}")


def _pilot() -> str:
    from core.wallet import pilot_custody

    view = pilot_custody.create_pilot_wallet(network=DEVNET, method="pin", credential=PIN, credential_confirmation=PIN, creation_key=f"set-{uuid.uuid4().hex}")
    revealed = pilot_custody.reveal_pilot_backup(view["wallet_id"], credential=PIN)
    pilot_custody.acknowledge_pilot_backup(view["wallet_id"], ack_token=revealed["ack_token"])
    return view["wallet_id"]


def _needs_pin(status: int, answer: dict[str, Any]) -> bool:
    return status == 400 and (answer.get("fault") or {}).get("context", {}).get("reason") == "credential_required"


@pytest.fixture
def stored_environment(monkeypatch):
    """The wallet fixture pins Test networks by the operator override; these tests drive the stored choice."""
    monkeypatch.delenv("VOOL_WALLET_NETWORK_ENVIRONMENT", raising=False)
    from core.wallet import environment

    environment.set_active_environment("testnet")
    return environment


def test_switching_to_mainnet_needs_the_pin_and_back_to_test_never_does(app, monkeypatch):
    _pilot()
    monkeypatch.delenv("VOOL_WALLET_NETWORK_ENVIRONMENT", raising=False)
    from core.wallet import environment

    status, answer = _post(app, "/api/wallet/environment", {"environment": "testnet"})
    assert status == 200, answer
    status, answer = _post(app, "/api/wallet/environment", {"environment": "mainnet"})
    assert _needs_pin(status, answer), answer
    assert environment.active_environment().environment == "testnet"
    status, answer = _post(app, "/api/wallet/environment", {"environment": "mainnet", "credential": "000000"})
    assert status == 400 and environment.active_environment().environment == "testnet", answer
    status, answer = _post(app, "/api/wallet/environment", {"environment": "mainnet", "credential": PIN})
    assert status == 200 and environment.active_environment().environment == "mainnet", answer
    status, answer = _post(app, "/api/wallet/environment", {"environment": "testnet"})
    assert status == 200 and environment.active_environment().environment == "testnet", answer


def test_turning_a_payment_channel_on_needs_the_pin_and_off_never_does(app):
    from core.wallet import channels

    _pilot()
    channel = channels.CHANNELS[0].channel_id
    status, answer = _post(app, "/api/wallet/channels", {"channel": channel, "on": False})
    assert status == 200 and not channels.is_on(channel), answer
    status, answer = _post(app, "/api/wallet/channels", {"channel": channel, "on": True})
    assert _needs_pin(status, answer) and not channels.is_on(channel), answer
    status, answer = _post(app, "/api/wallet/channels", {"channel": channel, "on": True, "credential": PIN})
    assert status == 200 and channels.is_on(channel), answer


def test_adding_or_turning_on_a_pay_per_answer_service_needs_the_pin(app):
    from core import x402_ai

    _pilot()
    service = {"action": "add", "label": "Answers", "base_url": "https://ai.example.com/v1", "model": "vendor/model-1", "max_price": "0.05"}
    status, answer = _post(app, "/api/wallet/ai-endpoints", service)
    assert _needs_pin(status, answer) and x402_ai.endpoints() == [], answer
    status, answer = _post(app, "/api/wallet/ai-endpoints", {**service, "credential": PIN})
    assert status == 200, answer
    endpoint = x402_ai.endpoints()[0]["id"]
    status, answer = _post(app, "/api/wallet/ai-endpoints", {"action": "set_on", "id": endpoint, "on": False})
    assert status == 200 and x402_ai.endpoints()[0]["on"] is False, answer
    status, answer = _post(app, "/api/wallet/ai-endpoints", {"action": "set_on", "id": endpoint, "on": True})
    assert _needs_pin(status, answer) and x402_ai.endpoints()[0]["on"] is False, answer
    status, answer = _post(app, "/api/wallet/ai-endpoints", {"action": "set_on", "id": endpoint, "on": True, "credential": PIN})
    assert status == 200 and x402_ai.endpoints()[0]["on"] is True, answer
    status, answer = _post(app, "/api/wallet/ai-endpoints", {"action": "list"})
    assert status == 200, answer
    status, answer = _post(app, "/api/wallet/ai-endpoints", {"action": "remove", "id": endpoint})
    assert status == 200 and x402_ai.endpoints() == [], answer


def test_the_fee_threshold_needs_the_pin(app):
    from core.wallet import dna_fees

    _pilot()
    before = dna_fees.collect_min_view("USDC")
    status, answer = _post(app, "/api/wallet/dna-fees/policy", {"asset": "USDC", "collect_min_atomic": 7})
    assert _needs_pin(status, answer) and dna_fees.collect_min_view("USDC") == before, answer
    status, answer = _post(app, "/api/wallet/dna-fees/policy", {"asset": "USDC", "collect_min_atomic": 7, "credential": PIN})
    assert status == 200 and answer["saved"]["atomic"] == 7, answer
    status, answer = _post(app, "/api/wallet/dna-fees/policy", {"asset": "USDC"})  # reading the view asks nothing
    assert status == 200, answer


def test_with_no_wallet_that_can_pay_nothing_is_asked(app, monkeypatch):
    from core.wallet import channels, custody

    custody.create_watch_only_wallet("9xQeWvG816bUx9EPjHmaT23yvVM2ZWbrrpZb9PusVFin", network=DEVNET)
    channel = channels.CHANNELS[0].channel_id
    _post(app, "/api/wallet/channels", {"channel": channel, "on": False})
    status, answer = _post(app, "/api/wallet/channels", {"channel": channel, "on": True})
    assert status == 200 and channels.is_on(channel), answer
