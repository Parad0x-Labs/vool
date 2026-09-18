"""Crypto Pilot, stage 2: who may drive a trusted wallet door.

Loopback alone is not caller authorization: a page on another localhost port, a DNS-rebound host the
operator allow-listed, a framed page, a text/plain form, and the command-dispatch projection all arrive
from 127.0.0.1. Trusted doors (approve, create, restore, export, approval method, limits, external
submit, paid x402 fetch/retry, environment, unfreeze) require same-origin evidence from the real request
and, when the native host configured one, its per-launch capability. Every refusal is typed
``wallet_caller_refused`` and happens before the door reads a proposal or a wallet.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest

from tests.asgi_harness import asgi_request

pytestmark = [pytest.mark.safety]

TRUSTED_DOORS = (
    "/api/wallet/approve", "/api/wallet/pocket/create", "/api/wallet/pocket/restore", "/api/wallet/export",
    "/api/wallet/approval-method", "/api/wallet/limits", "/api/wallet/external/submit", "/api/wallet/x402/fetch",
    "/api/wallet/x402/retry", "/api/wallet/environment",
    "/api/wallet/setup/create", "/api/wallet/setup/reveal", "/api/wallet/setup/acknowledge", "/api/wallet/setup/cancel",
    "/api/wallet/setup/resume", "/api/wallet/quote",
)
SAME_ORIGIN = {"Host": "127.0.0.1:11435", "Origin": "http://127.0.0.1:11435", "Content-Type": "application/json"}


@pytest.fixture
def app(wallet_env, monkeypatch):
    monkeypatch.delenv("VOOL_WALLET_UI_CAPABILITY_SHA256", raising=False)
    monkeypatch.delenv("VOOL_ALLOWED_HOSTS", raising=False)
    from apps.vool_api_server import create_app
    from core.web.api import wallet_api
    from core.web.api.runtime import RuntimeServices

    wallet_api.reset_caller_binding_for_tests()
    yield create_app(RuntimeServices(display_name="VOOL"))
    wallet_api.reset_caller_binding_for_tests()


def _post(app, path: str, body: dict[str, Any], headers: dict[str, str]) -> tuple[int, dict[str, Any]]:
    status, _headers, raw = asgi_request(app, method="POST", path=path, headers=headers, body=json.dumps(body).encode())
    try:
        return status, json.loads(raw or b"{}")
    except ValueError:
        return status, {"raw": raw[:200].decode("utf-8", "replace")}


def _refused(status: int, body: dict[str, Any], reason: str) -> bool:
    return status == 403 and body.get("error") == "wallet_caller_refused" and (body.get("fault") or {}).get("context", {}).get("reason") == reason


@pytest.mark.parametrize("door", TRUSTED_DOORS)
def test_a_page_on_another_localhost_port_is_refused_at_every_trusted_door(app, door):
    status, body = _post(app, door, {"proposal_id": "pay-00000000000000000000"}, {**SAME_ORIGIN, "Origin": "http://127.0.0.1:3000"})
    assert _refused(status, body, "origin_mismatch"), (door, status, body)


def test_a_same_site_fetch_a_null_origin_and_a_text_plain_form_are_refused(app):
    door = "/api/wallet/approve"
    body = {"proposal_id": "pay-00000000000000000000", "pin": "123456"}
    status, answer = _post(app, door, body, {**SAME_ORIGIN, "Sec-Fetch-Site": "same-site"})
    assert _refused(status, answer, "cross_site_fetch")
    # an opaque origin never reaches a wallet door: the app's origin guard refuses it on every route first
    status, answer = _post(app, door, body, {**SAME_ORIGIN, "Origin": "null"})
    assert status == 403 and answer == {"error": "cross-origin request not allowed"}, answer
    # the wallet gate carries the same refusal, typed, for any caller that reaches the handler itself
    from core.web.api.wallet_api import handle_wallet_post

    direct = handle_wallet_post(door, dict(body), client_host="127.0.0.1", headers={**SAME_ORIGIN, "Origin": "null"})
    assert _refused(direct.status, json.loads(direct.body), "origin_null"), direct.body
    status, answer = _post(app, door, body, {**SAME_ORIGIN, "Content-Type": "text/plain"})
    assert _refused(status, answer, "content_type_not_json")


def test_an_allow_listed_rebound_host_cannot_reach_a_trusted_door(app, monkeypatch):
    monkeypatch.setenv("VOOL_ALLOWED_HOSTS", "rebound.example")
    status, answer = _post(app, "/api/wallet/approve", {"proposal_id": "pay-00000000000000000000"},
                           {"Host": "rebound.example:11435", "Origin": "http://rebound.example:11435", "Content-Type": "application/json"})
    assert _refused(status, answer, "host_not_loopback")


UNKNOWN_WALLET = {"wallet_id": "wallet-0000000000000000", "asset": "SOL", "per_tx_minor": 1}


def test_same_origin_and_non_browser_callers_pass_the_gate(app):
    # control: the gate passes and the door itself answers (the wallet does not exist)
    status, answer = _post(app, "/api/wallet/limits", UNKNOWN_WALLET, SAME_ORIGIN)
    assert status == 404 and answer.get("error") == "wallet_not_found", answer
    status, answer = _post(app, "/api/wallet/limits", UNKNOWN_WALLET, {"Host": "127.0.0.1", "Content-Type": "application/json"})
    assert status == 404 and answer.get("error") == "wallet_not_found", answer
    status, answer = _post(app, "/api/wallet/limits", UNKNOWN_WALLET, {**SAME_ORIGIN, "Sec-Fetch-Site": "same-origin"})
    assert status == 404 and answer.get("error") == "wallet_not_found", answer
    # the approve door answers with its own fault once the gate passes: an unknown proposal cannot be explained,
    # so it needs the capability acknowledgement before anything else is read
    status, answer = _post(app, "/api/wallet/approve", {"proposal_id": "pay-00000000000000000000", "pin": "123456"}, SAME_ORIGIN)
    assert status == 400 and answer.get("error") == "wallet_acknowledgement_required", answer


def test_untrusted_doors_keep_their_open_loopback_contract(app):
    from tests.wallet._rig import DESTINATION

    status, answer = _post(app, "/api/wallet/watch-only", {"public_key": DESTINATION, "network": "solana-devnet"}, {**SAME_ORIGIN, "Origin": "http://127.0.0.1:3000"})
    assert status == 200 and answer["ok"] is True


def test_the_command_dispatch_projection_cannot_reach_a_trusted_door(app):
    from core.command_registry.execute import ExecutionContext
    from core.command_registry.groups import delegated_routes

    handler = delegated_routes._wallet_post("/api/wallet/approve")
    context = ExecutionContext(projection="api")
    result = handler(delegated_routes.RouteInput(body={"proposal_id": "pay-00000000000000000000", "pin": "123456"}), context)
    rendered = json.dumps(getattr(result, "data", None) or getattr(result, "__dict__", {}), default=str)
    assert "wallet_caller_refused" in rendered and "no_caller_evidence" in rendered, rendered
    # preservation: an untrusted wallet door stays reachable through the same projection
    from tests.wallet._rig import OTHER_DESTINATION

    watch = delegated_routes._wallet_post("/api/wallet/watch-only")(delegated_routes.RouteInput(body={"public_key": OTHER_DESTINATION, "network": "solana-devnet"}), context)
    assert "wallet_caller_refused" not in json.dumps(getattr(watch, "data", None) or getattr(watch, "__dict__", {}), default=str)


def test_a_configured_native_capability_is_required_and_compared_by_digest(app, monkeypatch):
    from core.web.api import wallet_api

    capability = "native-launch-capability-" + "7" * 40
    monkeypatch.setenv("VOOL_WALLET_UI_CAPABILITY_SHA256", hashlib.sha256(capability.encode()).hexdigest())
    wallet_api.reset_caller_binding_for_tests()
    body = {"proposal_id": "pay-00000000000000000000", "pin": "123456"}
    status, answer = _post(app, "/api/wallet/approve", body, SAME_ORIGIN)
    assert _refused(status, answer, "capability_missing")
    status, answer = _post(app, "/api/wallet/approve", body, {**SAME_ORIGIN, "X-VOOL-Wallet-Capability": "wrong"})
    assert _refused(status, answer, "capability_mismatch")
    # the stored digest itself is not a credential
    status, answer = _post(app, "/api/wallet/approve", body, {**SAME_ORIGIN, "X-VOOL-Wallet-Capability": hashlib.sha256(capability.encode()).hexdigest()})
    assert _refused(status, answer, "capability_mismatch")
    status, answer = _post(app, "/api/wallet/limits", UNKNOWN_WALLET, {**SAME_ORIGIN, "X-VOOL-Wallet-Capability": capability})
    assert status == 404 and answer.get("error") == "wallet_not_found", answer
    import os

    assert "VOOL_WALLET_UI_CAPABILITY_SHA256" not in os.environ, "the digest must leave the process environment once loaded"


def test_status_reports_how_callers_are_bound(app, monkeypatch):
    from core.wallet import status
    from core.web.api import wallet_api

    assert status.wallet_status()["crypto_pilot"]["caller_binding"] == "unbound"
    monkeypatch.setenv("VOOL_WALLET_UI_CAPABILITY_SHA256", "ab" * 32)
    wallet_api.reset_caller_binding_for_tests()
    assert status.wallet_status()["crypto_pilot"]["caller_binding"] == "native_capability"


@pytest.mark.parametrize("page", ["/chat", "/settings"])
def test_the_pages_that_host_wallet_controls_refuse_to_be_framed_by_other_origins(app, page):
    status, headers, _raw = asgi_request(app, method="GET", path=page, headers={"Host": "127.0.0.1:11435"})
    assert status == 200
    lowered = {k.lower(): v for k, v in headers.items()}
    assert "frame-ancestors 'self'" in lowered.get("content-security-policy", "")
    assert lowered.get("x-frame-options", "").upper() == "SAMEORIGIN"


def test_wallet_reads_are_owner_local(app):
    from core.web.api.wallet_api import handle_wallet_get

    remote = handle_wallet_get("/api/wallet/status", {}, client_host="203.0.113.9")
    assert remote.status == 403
    local = handle_wallet_get("/api/wallet/status", {}, client_host="127.0.0.1")
    assert local.status == 200


@pytest.mark.parametrize("configured", ["not-a-digest", "ab" * 31, "zz" * 32, "AB" * 33])
def test_a_malformed_native_capability_binds_every_trusted_door_shut(app, monkeypatch, configured):
    from core.wallet import status
    from core.web.api import wallet_api

    monkeypatch.setenv("VOOL_WALLET_UI_CAPABILITY_SHA256", configured)
    wallet_api.reset_caller_binding_for_tests()
    assert status.wallet_status()["crypto_pilot"]["caller_binding"] == "native_capability_invalid"
    for presented in ("", configured, "anything"):
        headers = {**SAME_ORIGIN, **({"X-VOOL-Wallet-Capability": presented} if presented else {})}
        status_code, answer = _post(app, "/api/wallet/limits", UNKNOWN_WALLET, headers)
        assert status_code == 403 and answer.get("error") == "wallet_caller_refused", (configured, presented, answer)
        assert answer["fault"]["context"]["reason"] in {"capability_missing", "capability_mismatch"}
    # control: an untrusted door keeps its loopback contract under the same misconfiguration
    from tests.wallet._rig import DESTINATION

    status_code, answer = _post(app, "/api/wallet/watch-only", {"public_key": DESTINATION, "network": "solana-devnet"}, SAME_ORIGIN)
    assert status_code == 200 and answer["ok"] is True
