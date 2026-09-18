"""The meaning reaches every approval surface, and red fails closed without the capability said back.

* every pending proposal in the status carries `meaning` (tier, headline, lines, ack_text) from its typed fields;
* every external signing request carries the meaning of the exact bytes, checked against the proposal;
* `/api/wallet/approve` and `/api/wallet/external/submit` refuse a red meaning unless the request repeats its
  acknowledgement text byte for byte (typed fault `wallet_acknowledgement_required`, 400);
* the fragment renders the block per tier and disables approving until the red acknowledgement is checked.
"""
from __future__ import annotations

import json
import os

import pytest

from tests.wallet.test_wallet_api import _post


@pytest.fixture
def app(wallet_env):
    from apps.vool_api_server import create_app
    from core.web.api.runtime import RuntimeServices

    return create_app(RuntimeServices(display_name="VOOL"))

DESTINATION = "9xQeWvG816bUx9EPjHmaT23yvVM2ZWbrrpZb9PusVFin"
PIN = "246810"


def _red_meaning() -> dict:
    from core.wallet import meaning

    return meaning.unlimited_allowance("USDC", "0x1a2b3c4d5e6f70819293a4b5c6d7e8f90a1b2c9f").to_dict()


def test_pending_proposals_carry_their_meaning(wallet_env):
    from core.wallet import custody, lifecycle, proposals
    from core.wallet.status import wallet_status

    profile = custody.create_watch_only_wallet(DESTINATION)
    p = proposals.propose_transaction(wallet_id=profile.wallet_id, destination=DESTINATION, amount_minor=1_200_000, asset="SOL", origin="user")
    lifecycle.default_lifecycle().prepare(p.proposal_id)  # pending approval only after preparation
    row = next(r for r in wallet_status()["pending"] if r["proposal_id"] == p.proposal_id)
    assert row["meaning"]["tier"] == "green"
    assert row["meaning"]["headline"] == "Exactly 0.0012 SOL goes to 9xQeWvG8…VFin, once. No continuing permission."
    assert row["meaning"]["ack_text"] == ""


def test_a_red_meaning_fails_closed_until_the_capability_is_said_back(app, monkeypatch):
    from core.wallet import custody, status

    monkeypatch.setattr(status, "proposal_meaning", lambda proposal: _red_meaning())
    created = _post(app, "/api/wallet/pocket/create", {"pin": PIN, "acknowledged_warning": True, "confirmation_phrase": custody.POCKET_CONFIRMATION_PHRASE})
    assert created[0] == 200
    code, prop = _post(app, "/api/wallet/propose", {"destination": DESTINATION, "amount_minor": 1, "asset": "SOL"})
    assert code == 200, prop
    proposal_id = prop["proposal"]["proposal_id"]
    code, body = _post(app, "/api/wallet/approve", {"proposal_id": proposal_id, "pin": PIN})
    assert code == 400 and body["error"] == "wallet_acknowledgement_required", body
    assert body["meaning"]["tier"] == "red" and body["acknowledged_capability"] == _red_meaning()["ack_text"]
    code, body = _post(app, "/api/wallet/approve", {"proposal_id": proposal_id, "pin": PIN, "acknowledged_capability": "I understand the risks"})
    assert code == 400 and body["error"] == "wallet_acknowledgement_required", "a generic phrase is not the capability"
    code, body = _post(app, "/api/wallet/approve", {"proposal_id": proposal_id, "pin": PIN, "acknowledged_capability": _red_meaning()["ack_text"]})
    assert code == 200 and body["receipt"]["state"] in ("confirmed", "broadcast"), body


def test_a_green_meaning_needs_no_acknowledgement(app):
    from core.wallet import custody

    _post(app, "/api/wallet/pocket/create", {"pin": PIN, "acknowledged_warning": True, "confirmation_phrase": custody.POCKET_CONFIRMATION_PHRASE})
    code, prop = _post(app, "/api/wallet/propose", {"destination": DESTINATION, "amount_minor": 1, "asset": "SOL"})
    assert code == 200, prop
    code, body = _post(app, "/api/wallet/approve", {"proposal_id": prop["proposal"]["proposal_id"], "pin": PIN})
    assert code == 200, body


def test_an_external_signing_request_carries_the_bytes_meaning(wallet_env):
    from core.wallet import custody, lifecycle, proposals

    profile = custody.register_external_signer_wallet(DESTINATION)
    p = proposals.propose_transaction(wallet_id=profile.wallet_id, destination=DESTINATION, amount_minor=5_000, asset="SOL", origin="user")
    engine = lifecycle.default_lifecycle()
    engine.prepare(p.proposal_id)
    view = engine.request_external_signature(p.proposal_id)
    assert view["meaning"]["tier"] == "green", view["meaning"]
    assert "The bytes to sign match this description." in view["meaning"]["lines"]


@pytest.mark.timeout(600)
def test_served_fragment_gates_red_behind_the_capability_and_shows_green_on_the_card(tmp_path):
    import tests._reader_served_rig as rig
    from tests.served_browser import launch_chromium

    red = _red_meaning()
    with rig.CapturingProvider(default="ok") as provider:
        daemon = rig.ServedDaemon(tmp_path / "home", provider=provider, env_extra={"VOOL_WALLET_ENABLED": "1", "VOOL_WALLET_NETWORK_ENVIRONMENT": "testnet", "PLAYWRIGHT_BROWSERS_PATH": os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")})
        daemon.register_provider()
        try:
            daemon.start()
        except RuntimeError as exc:
            daemon.stop()
            pytest.skip(f"served daemon could not boot here: {exc}")
        manager, browser = launch_chromium()
        try:
            page = browser.new_page()
            page.goto(f"{daemon.base_url}/settings#wallet", wait_until="networkidle")
            page.wait_for_selector("#vwStatus", state="visible", timeout=20000)
            result = page.evaluate(
                "(m) => { const host = document.createElement('div'); document.body.appendChild(host); const ack = window.VoolWallet.renderMeaning(host, m);"
                " const before = window.VoolWallet.approvalAllowed(m, ack && ack.checked); ack.checked = true; const after = window.VoolWallet.approvalAllowed(m, ack.checked);"
                " return { before, after, text: host.innerText, role: host.firstChild.getAttribute('role'), hasBox: !!host.querySelector('.vw-ack-box') }; }",
                red,
            )
            assert result["before"] is False and result["after"] is True, result
            assert result["hasBox"] and result["role"] == "alert"
            assert red["headline"] in result["text"] and red["ack_text"] in result["text"] and "standing authority" in result["text"]
            # a green card in the chat transcript shows the sentence with no acknowledgement box
            import urllib.request
            def post(path, body):
                req = urllib.request.Request(daemon.base_url + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
                with urllib.request.urlopen(req, timeout=60) as r: return json.loads(r.read().decode())
            post("/api/wallet/watch-only", {"public_key": DESTINATION})
            proposed = post("/api/wallet/propose", {"destination": DESTINATION, "amount_minor": 1_200_000, "asset": "SOL"})
            pid = proposed["proposal"]["proposal_id"]
            page.goto(f"{daemon.base_url}/chat", wait_until="networkidle")
            page.wait_for_selector(f'.vw-card[data-proposal="{pid}"] .vw-meaning.green', timeout=30000)
            card_text = page.inner_text(f'.vw-card[data-proposal="{pid}"]')
            assert "Exactly 0.0012 SOL goes to 9xQeWvG8…VFin, once. No continuing permission." in card_text
            assert page.query_selector(f'.vw-card[data-proposal="{pid}"] .vw-ack-box') is None
        finally:
            browser.close()
            manager.stop()
            daemon.stop()
