"""The one API seam, driven through the production dispatchers: status is public-safe, every
state change is owner-local, the recovery phrase crosses the wire exactly once, and the
subprocess daemon journey proves the whole lifecycle against a scripted testnet RPC.
"""
from __future__ import annotations

import json
import sqlite3
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from tests.asgi_harness import asgi_request
from tests.wallet._rig import DESTINATION, DEVNET, ScriptedRpc, phrase_leaked

pytestmark = [pytest.mark.safety]

HEADERS = {"Host": "127.0.0.1", "Content-Type": "application/json", "Origin": "http://127.0.0.1"}


@pytest.fixture
def app(wallet_env):
    from apps.vool_api_server import create_app
    from core.web.api.runtime import RuntimeServices

    return create_app(RuntimeServices(display_name="VOOL"))


def _post(app, path: str, body: dict[str, Any], *, headers: dict[str, str] | None = None):
    status, _h, raw = asgi_request(app, method="POST", path=path, headers=headers or HEADERS, body=json.dumps(body).encode())
    return status, json.loads(raw or b"{}")


def _get(app, path: str):
    status, _h, raw = asgi_request(app, method="GET", path=path, headers={"Host": "127.0.0.1"})
    return status, json.loads(raw or b"{}")


def test_status_route_serves_the_public_safe_status(app):
    status, body = _get(app, "/api/wallet/status")
    assert status == 200 and body["ok"] is True
    assert body["status"]["custody_mode"] == "none" and body["status"]["mainnet_enabled"] is False


def test_disabled_wallet_reports_disabled_and_refuses_state_changes(monkeypatch):
    monkeypatch.delenv("VOOL_WALLET_ENABLED", raising=False)
    from apps.vool_api_server import create_app
    from core.web.api.runtime import RuntimeServices

    app = create_app(RuntimeServices(display_name="VOOL"))
    status, body = _get(app, "/api/wallet/status")
    assert status == 200 and body["status"]["enabled"] is False
    status, body = _post(app, "/api/wallet/watch-only", {"public_key": DESTINATION})
    assert status == 403 and body["error"] == "wallet_disabled"


def test_pocket_creation_over_the_wire_shows_the_phrase_once_and_only_to_loopback(app):
    from core.wallet import custody

    status, body = _post(app, "/api/wallet/pocket/create", {"pin": "246810"})
    assert status == 400 and body["error"] == "wallet_confirmation_required"
    assert body["warning"] == custody.POCKET_WARNING_TEXT and body["confirmation_phrase"] == custody.POCKET_CONFIRMATION_PHRASE

    status, body = _post(app, "/api/wallet/pocket/create", {"pin": "246810", "acknowledged_warning": True, "confirmation_phrase": custody.POCKET_CONFIRMATION_PHRASE})
    assert status == 200 and body["shown_once"] is True and len(body["recovery_phrase"].split()) >= 12
    phrase = body["recovery_phrase"]
    status, again = _get(app, "/api/wallet/status")
    assert phrase not in json.dumps(again)
    status, again = _get(app, f"/api/wallet/wallets/{body['wallet']['wallet_id']}")
    assert status == 200 and phrase not in json.dumps(again)


def test_state_changing_routes_are_owner_local_only(app):
    from core.web.api.service import dispatch_post

    # dispatch_post is the one door both HTTP shells share; a non-loopback client is refused before any wallet code runs.
    resp = dispatch_post(
        path="/api/wallet/propose", body={"destination": DESTINATION, "amount_minor": 1, "asset": "SOL"}, headers=HEADERS,
        runtime=app.state.runtime, model_name="", workspace_root_provider=lambda: Path("."), client_host="203.0.113.9",
    )
    assert resp.status == 403 and json.loads(resp.body)["error"] == "owner_local_required"
    resp = dispatch_post(path="/api/wallet/approve", body={"proposal_id": "x", "pin": "1"}, headers=HEADERS, runtime=app.state.runtime, model_name="", workspace_root_provider=lambda: Path("."), client_host="203.0.113.9")
    assert resp.status == 403


def test_propose_then_approve_over_the_wire_reaches_the_testnet_rpc_once(app, wallet_env):
    from core.wallet import custody

    _post(app, "/api/wallet/pocket/create", {"pin": "246810", "acknowledged_warning": True, "confirmation_phrase": custody.POCKET_CONFIRMATION_PHRASE})
    status, body = _post(app, "/api/wallet/propose", {"destination": DESTINATION, "amount_minor": 2500, "asset": "SOL", "memo": "api", "idempotency_key": "api-1"})
    assert status == 200 and body["proposal"]["state"] == "pending_approval", body
    proposal_id = body["proposal"]["proposal_id"]
    status, dup = _post(app, "/api/wallet/propose", {"destination": DESTINATION, "amount_minor": 2500, "asset": "SOL", "memo": "api", "idempotency_key": "api-1"})
    assert status == 200 and dup["proposal"]["proposal_id"] == proposal_id and dup["duplicate"] is True

    status, wrong = _post(app, "/api/wallet/approve", {"proposal_id": proposal_id, "pin": "000000"})
    assert status == 403 and wrong["error"] == "wallet_approval_rejected" and wrong["fault"]["code"] == "wallet_approval_rejected"
    # a mistyped PIN is a counted refusal; the same proposal is still the owner's to approve
    status, still = _get(app, f"/api/wallet/proposals/{proposal_id}")
    assert still["proposal"]["state"] == "pending_approval"
    status, ok = _post(app, "/api/wallet/approve", {"proposal_id": proposal_id, "pin": "246810"})
    assert status == 200 and ok["receipt"]["state"] == "confirmed" and ok["receipt"]["tx_signature"]
    assert wallet_env["rpc"].send_count() == 1
    status, st = _get(app, "/api/wallet/status")
    assert st["status"]["pending_approvals"] == 0 and st["status"]["last_receipt"]["tx_signature"] == ok["receipt"]["tx_signature"]
    status, ev = _get(app, f"/api/wallet/proposals/{proposal_id}")
    assert status == 200 and [e["state"] for e in ev["events"]][-1] == "confirmed"


def test_chat_turn_reply_carries_the_wallet_status_block(app, monkeypatch):
    """The chat door is the status path users actually see: an ordinary turn's reply payload
    carries the same public-safe wallet block the API serves, and never a secret."""
    from core.web.api import runtime as runtime_module

    payload: dict[str, Any] = {"message": {"role": "assistant", "content": "hi"}, "done": True}
    runtime_module.attach_wallet_status(payload)
    assert payload["wallet_status"]["mainnet_enabled"] is False and "custody_mode" in payload["wallet_status"]
    monkeypatch.delenv("VOOL_WALLET_ENABLED", raising=False)
    payload2: dict[str, Any] = {"message": {"role": "assistant", "content": "hi"}, "done": True}
    runtime_module.attach_wallet_status(payload2)
    assert "wallet_status" not in payload2, "a disabled wallet is silent on every ordinary turn"


# --- the subprocess served journey ----------------------------------------------------------

def _http(url: str, body: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", "Origin": url.rsplit("/api", 1)[0]}, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


@pytest.mark.served
def test_served_daemon_journey_watch_only_default_pocket_opt_in_capped_x402_and_one_broadcast(tmp_path: Path) -> None:
    from tests._blackbox_served_rig import REPO_ROOT, SEED_MANIFEST, ScriptedProvider, ServedDaemon, run_in_home

    model = "qwen3-stub:8b"
    home = tmp_path / "home"
    store_dir = tmp_path / "blackbox-store"
    provider = ScriptedProvider({model: "the model is never consulted on this journey"})
    rpc = ScriptedRpc()
    daemon = ServedDaemon(
        home,
        env_extra={
            "VOOL_WALLET_ENABLED": "1",
            "VOOL_WALLET_NETWORK_ENVIRONMENT": "testnet",
            "VOOL_WALLET_TESTNET_RPC_URL": rpc.url,
            "VOOL_WALLET_X402_CAP_MINOR": "2000",
            "VOOL_BLACKBOX_DIR": str(store_dir),
            "OLLAMA_HOST": provider.base_url,
            "VOOL_OLLAMA_URL": provider.base_url,
            "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat",
        },
    )
    provider.__enter__()
    rpc.__enter__()
    try:
        try:
            daemon.start(timeout=240)
        except Exception as exc:  # pragma: no cover - environment
            pytest.skip(f"served daemon could not boot here: {exc}")
        run_in_home(home, SEED_MANIFEST.format(root=REPO_ROOT, base_url=provider.base_url, registered=[model]))
        base = f"http://127.0.0.1:{daemon.port}"
        from core.wallet import custody

        # 1. fresh daemon: enabled, no wallet, mainnet off
        _s, st = _http(f"{base}/api/wallet/status")
        assert st["status"]["custody_mode"] == "none" and st["status"]["mainnet_enabled"] is False
        # 2. watch-only is one call and the default
        _s, w = _http(f"{base}/api/wallet/watch-only", {"public_key": DESTINATION, "label": "cold"})
        assert w["wallet"]["mode"] == custody.MODE_WATCH_ONLY
        _s, refused = _http(f"{base}/api/wallet/propose", {"destination": DESTINATION, "amount_minor": 1, "asset": "SOL"})
        _s, refused2 = _http(f"{base}/api/wallet/approve", {"proposal_id": refused["proposal"]["proposal_id"], "pin": "246810"})
        assert refused2["error"] == "wallet_signing_unavailable"
        # 3. pocket opt-in behind the typed warning; the phrase crosses the wire once
        _s, created = _http(f"{base}/api/wallet/pocket/create", {"pin": "246810", "acknowledged_warning": True, "confirmation_phrase": custody.POCKET_CONFIRMATION_PHRASE})
        phrase = created["recovery_phrase"]
        assert created["shown_once"] is True and len(phrase.split()) >= 12
        # 4. an x402 challenge captured by the runtime becomes a capped, approvable proposal
        from tests.wallet._rig import x402_body

        _s, x = _http(f"{base}/api/wallet/x402/propose", {"status": 402, "headers": {}, "body": x402_body(amount_minor=1500)})
        assert x["proposal"]["origin"] == "x402" and x["proposal"]["state"] == "pending_approval", x
        _s, over = _http(f"{base}/api/wallet/x402/propose", {"status": 402, "headers": {}, "body": x402_body(amount_minor=2001)})
        assert over["error"] == "wallet_x402_cap_exceeded"
        # 5. approve with the PIN: exactly one sendTransaction on the testnet RPC; duplicates collapse
        _s, done = _http(f"{base}/api/wallet/approve", {"proposal_id": x["proposal"]["proposal_id"], "pin": "246810"})
        assert done["receipt"]["state"] == "confirmed" and done["receipt"]["network"] == DEVNET, done
        _s, dup = _http(f"{base}/api/wallet/approve", {"proposal_id": x["proposal"]["proposal_id"], "pin": "246810"})
        assert dup["error"] == "wallet_duplicate_payment"
        assert rpc.send_count() == 1
        assert all(c["method"] != "sendTransaction" or "mainnet" not in json.dumps(c) for c in rpc.calls)
        # 6. nothing secret anywhere: status, receipts, the daemon log, the runtime db, the blackbox journal
        _s, st2 = _http(f"{base}/api/wallet/status")
        assert st2["status"]["last_receipt"]["tx_signature"] == done["receipt"]["tx_signature"]
        leak_surfaces = [json.dumps(st2), daemon.log_tail(lines=400)]
        db = sqlite3.connect(str(home / "data" / "vool_web0_v2.db"))
        try:
            for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'"):
                leak_surfaces.append(json.dumps(db.execute(f"SELECT * FROM {name}").fetchall(), default=str))
        finally:
            db.close()
        from storage.blackbox.journal import Journal

        entries = [dict(e) for e in Journal(store_dir).entries()]
        leak_surfaces.append(json.dumps(entries))
        assert any(e.get("kind") == "payment_terminal" for e in entries)
        for surface in leak_surfaces:
            assert phrase_leaked(phrase, surface) is None, phrase_leaked(phrase, surface)
        assert "246810" not in json.dumps(entries) and "246810" not in json.dumps(st2)
    finally:
        daemon.stop()
        rpc.__exit__(None, None, None)
        provider.__exit__(None, None, None)
