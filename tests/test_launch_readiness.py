"""
tests/test_launch_readiness.py
Tests covering the 6 launch surfaces:
  1. Earnings page
  2. Wallet info routes (read-only adapters over core.wallet — never a key minted on GET)
  3. Task market routes (queue / claim / complete / settle)
  4. Receipt wallet wiring + credit award (recipient read from core.wallet, never minted)
  5. Solana anchor hook — retired: the receipt path never broadcasts, flag on or off
  6. Background task poll loop (idempotency)
"""
from __future__ import annotations

import json
from unittest import mock

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.meet_and_greet_service import MeetAndGreetService
from core.vool_wallet import WALLET_FILENAME, b58encode
from core.web.api.runtime import RuntimeServices
from core.web.api.service import _attach_work_receipt, dispatch_get, dispatch_post
from core.web.meet.routes import dispatch_request, resolve_static_route
from tests.wallet._rig import rpc  # noqa: F401 - scripted devnet RPC (balances), no real chain

LEGACY = "wallet_legacy_surface_retired"


@pytest.fixture(autouse=True)
def _isolated_vool_home(tmp_path, monkeypatch):
    """Give each test its own VOOL_HOME, with core.wallet off unless a test turns it on.

    The wallet-info routes are read-only adapters: whatever the home holds, a GET must never
    mint a key. The teardown assertion holds every test in this module to that.
    """
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    monkeypatch.delenv("VOOL_WALLET_ENABLED", raising=False)
    from core import runtime_paths

    monkeypatch.setattr(runtime_paths, "_VOOL_HOME_OVERRIDE", None, raising=False)
    yield
    assert not list(tmp_path.rglob(WALLET_FILENAME)), "a launch surface minted a legacy key file"


def _register_watch_only(monkeypatch, rpc_stub) -> str:
    monkeypatch.setenv("VOOL_WALLET_ENABLED", "1")
    monkeypatch.setenv("VOOL_WALLET_NETWORK_ENVIRONMENT", "testnet")
    monkeypatch.setenv("VOOL_WALLET_TESTNET_RPC_URL", rpc_stub.url)
    from core.wallet import custody

    pubkey = b58encode(Ed25519PrivateKey.generate().public_key().public_bytes_raw())
    custody.create_watch_only_wallet(pubkey, label="launch")
    return pubkey


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _svc() -> MeetAndGreetService:
    return MeetAndGreetService()


def _rt() -> RuntimeServices:
    return RuntimeServices(display_name="VOOL")


def _meet_get(path: str, query: dict | None = None) -> tuple[int, dict]:
    return dispatch_request("GET", path, query or {}, {}, _svc())


def _meet_post(path: str, body: dict | None = None) -> tuple[int, dict]:
    return dispatch_request("POST", path, {}, body or {}, _svc())


def _assert_read_only_view(view: dict) -> None:
    assert view["authority"] == "core.wallet" and view["read_only"] is True
    assert "pubkey" in view and "custody_mode" in view
    assert view["network"] == "solana-devnet" and view["mainnet_enabled"] is False
    assert "enabled" in view


# ---------------------------------------------------------------------------
# Gap 1 — Earnings / Task Queue panel
# ---------------------------------------------------------------------------

def test_earnings_static_route_returns_200() -> None:
    result = resolve_static_route("/earnings")
    assert result is not None
    assert result[0] == 200
    assert "text/html" in result[1]


def test_earnings_page_contains_key_elements() -> None:
    result = resolve_static_route("/earnings")
    assert result is not None
    body = result[2].decode("utf-8")
    assert "Earnings" in body
    assert "Task Queue" in body or "task-tbody" in body
    assert "/v1/wallet/info" in body
    assert "/v1/credits/balance" in body
    assert "/v1/tasks/queue" in body
    assert "/v1/workers" in body


def test_earnings_trailing_slash() -> None:
    result = resolve_static_route("/earnings/")
    assert result is not None
    assert result[0] == 200


# ---------------------------------------------------------------------------
# Gap 2 — Wallet info (meet server routes): read-only adapter, no create-on-read
# ---------------------------------------------------------------------------

def test_get_wallet_info_returns_the_read_only_adapter_shape() -> None:
    status, data = _meet_get("/v1/wallet/info")
    assert status == 200 and data["ok"] is True
    _assert_read_only_view(data["result"])


def test_get_wallet_info_without_a_registered_wallet_has_an_empty_pubkey_and_mints_nothing() -> None:
    for _ in range(2):  # a second read does not "eventually" create one either
        _, data = _meet_get("/v1/wallet/info")
        assert data["result"]["pubkey"] == ""
        assert data["result"]["custody_mode"] == "none" and data["result"]["enabled"] is False
    from core.wallet import custody

    assert custody.default_wallet() is None


def test_get_wallet_info_reports_the_registered_core_wallet(monkeypatch, rpc) -> None:  # noqa: F811
    pubkey = _register_watch_only(monkeypatch, rpc)
    status, data = _meet_get("/v1/wallet/info")
    assert status == 200
    view = data["result"]
    _assert_read_only_view(view)
    assert view["pubkey"] == pubkey and view["custody_mode"] == "watch_only" and view["enabled"] is True
    assert view["sol_balance"] == pytest.approx(5.0)  # the scripted devnet RPC answers 5 SOL
    assert rpc.send_count() == 0


def test_get_credits_balance_returns_200() -> None:
    status, data = _meet_get("/v1/credits/balance")
    assert status == 200
    result = data["result"]
    assert "balance" in result
    assert "peer_id" in result
    assert "entries" in result


def test_get_credits_balance_entries_is_list() -> None:
    _, data = _meet_get("/v1/credits/balance")
    assert isinstance(data["result"]["entries"], list)


# ---------------------------------------------------------------------------
# Gap 2 — Wallet routes on VOOL API (:11435 service.py)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", ["/v1/wallet/info", "/api/wallet/info"])
def test_service_wallet_info_is_the_read_only_adapter(path: str) -> None:
    r = dispatch_get(path=path, query={}, runtime=_rt(), model_name="vool", client_host="127.0.0.1")
    assert r.status == 200
    body = json.loads(r.body)
    assert body["ok"] is True
    _assert_read_only_view(body)
    assert body["pubkey"] == ""
    assert "error" not in body


def test_service_wallet_info_reports_the_registered_core_wallet(monkeypatch, rpc) -> None:  # noqa: F811
    pubkey = _register_watch_only(monkeypatch, rpc)
    for path in ("/v1/wallet/info", "/api/wallet/info"):
        body = json.loads(dispatch_get(path=path, query={}, runtime=_rt(), model_name="vool", client_host="127.0.0.1").body)
        assert body["pubkey"] == pubkey and body["custody_mode"] == "watch_only"
    assert rpc.send_count() == 0


@pytest.mark.parametrize("peer", ["", "203.0.113.9"])
def test_service_wallet_info_answers_only_the_owners_loopback_session(peer: str) -> None:
    # a caller with no peer or a foreign peer is not the owner's session: wallet reads fail closed like writes
    for path in ("/v1/wallet/info", "/api/wallet/info"):
        r = dispatch_get(path=path, query={}, runtime=_rt(), model_name="vool", client_host=peer)
        assert r.status == 403 and json.loads(r.body) == {"ok": False, "error": "owner_local_required"}, (peer, path)


def test_service_wallet_info_never_accepts_a_post_that_would_mint() -> None:
    r = dispatch_post(
        path="/api/wallet/info",
        body={"create": True},
        headers={},
        runtime=_rt(),
        model_name="vool",
        workspace_root_provider=lambda: "/tmp",
        run_agent_provider=None,
    )
    assert r.status != 200 or json.loads(r.body).get("ok") is not True
    from core.wallet import custody

    assert custody.default_wallet() is None


def test_service_credits_balance_returns_200() -> None:
    r = dispatch_get(path="/v1/credits/balance", query={}, runtime=_rt(), model_name="vool")
    assert r.status == 200
    body = json.loads(r.body)
    assert "balance" in body


def test_service_credits_settle_returns_200() -> None:
    r = dispatch_post(
        path="/v1/credits/settle",
        body={},
        headers={},
        runtime=_rt(),
        model_name="vool",
        workspace_root_provider=lambda: "/tmp",
        run_agent_provider=None,
    )
    assert r.status == 200
    body = json.loads(r.body)
    assert "balance" in body
    assert "mode" in body


# ---------------------------------------------------------------------------
# Gap 3 — Task market routes
# ---------------------------------------------------------------------------

def test_get_tasks_queue_returns_200() -> None:
    status, data = _meet_get("/v1/tasks/queue")
    assert status == 200
    assert isinstance(data["result"], list)


def test_post_task_claim_nonexistent_returns_409() -> None:
    status, _ = _meet_post("/v1/tasks/nonexistent-task-xyz/claim", {})
    assert status == 409


def test_post_credits_settle_meet_returns_200() -> None:
    status, data = _meet_post("/v1/credits/settle", {})
    assert status == 200
    assert "balance" in data["result"]


def test_post_task_complete_nonexistent_returns_200() -> None:
    # complete on missing task still returns 200 (no-op on DB, returns status)
    status, data = _meet_post("/v1/tasks/nonexistent-task-xyz/complete", {"result_hash": "abc123"})
    assert status == 200
    assert data["result"]["status"] == "complete"


# ---------------------------------------------------------------------------
# Gap 3b — task_offer_store unit tests
# ---------------------------------------------------------------------------

def test_task_offer_store_list_returns_list() -> None:
    from storage.task_offer_store import list_open_task_offers
    rows = list_open_task_offers(limit=5)
    assert isinstance(rows, list)


def test_task_offer_store_get_nonexistent_returns_none() -> None:
    from storage.task_offer_store import get_task_offer
    assert get_task_offer("does-not-exist-xyz") is None


def test_task_offer_store_claim_nonexistent_returns_false() -> None:
    from storage.task_offer_store import claim_task_offer
    assert claim_task_offer("not-a-real-task", "peer-1") is False


# ---------------------------------------------------------------------------
# Gap 4 — Receipt wallet wiring + credit award
# ---------------------------------------------------------------------------

def _receipt_recipient(session_id: str) -> str:
    payload = _attach_work_receipt(
        {"response": "result text"},
        result={"response": "result text"},
        session_id=session_id,
    )
    receipt = payload.get("web0_receipt") or {}
    payment = receipt.get("payment") or {}
    return payment.get("recipient_wallet") or ""


def test_attach_receipt_uses_the_registered_core_wallet_pubkey(monkeypatch, rpc) -> None:  # noqa: F811
    pubkey = _register_watch_only(monkeypatch, rpc)
    assert _receipt_recipient("wiring-test") == pubkey
    assert rpc.send_count() == 0


def test_attach_receipt_without_a_wallet_keeps_the_stub_and_mints_nothing() -> None:
    # No registered wallet: the recipient stays the explicit stub rather than a key minted as a
    # side effect of attaching a receipt (the old create-on-read door).
    assert _receipt_recipient("wiring-test-empty") == "stub-wallet"
    from core.wallet import custody

    assert custody.default_wallet() is None


def test_attach_receipt_awards_credits_to_local_peer() -> None:
    from core.credit_ledger import get_credit_balance
    from core.web.api import service as svc
    from network.signer import get_local_peer_id

    svc._task_award_window.clear()  # the award window is process-global; start from an empty one
    peer_id = get_local_peer_id()
    balance_before = get_credit_balance(peer_id)
    _attach_work_receipt(
        {"response": "earn some credits"},
        result={"response": "earn some credits"},
        session_id="credit-award-test",
    )
    balance_after = get_credit_balance(peer_id)
    assert balance_after >= balance_before + 1.0


# ---------------------------------------------------------------------------
# Gap 4b — Solana anchor hook: retired. The receipt path never broadcasts.
# ---------------------------------------------------------------------------

def _legacy_receipts() -> list:
    from core.faults.recorder import list_faults

    return [f for f in list_faults(limit=200) if f.code == LEGACY]


def test_receipt_path_never_anchors_even_with_the_flag_on(monkeypatch, rpc) -> None:  # noqa: F811
    _register_watch_only(monkeypatch, rpc)
    receipts_before = len(_legacy_receipts())
    with mock.patch.dict("os.environ", {"VOOL_ANCHOR_RECEIPTS": "1"}):
        with mock.patch("core.solana_anchor.anchor_vault_proof") as mock_anchor, \
             mock.patch("core.solana_anchor.submit_memo_anchor") as mock_submit, \
             mock.patch("core.solana_anchor.dispatch_anchor_in_background") as mock_dispatch:
            payload = _attach_work_receipt(
                {"response": "anchor test"},
                result={"response": "anchor test"},
                session_id="anchor-env-test",
            )
    assert payload.get("web0_receipt"), "the receipt itself still attaches"
    mock_anchor.assert_not_called()
    mock_submit.assert_not_called()
    mock_dispatch.assert_not_called()
    # the retired surfaces file a fault when called; the receipt path called none of them
    assert len(_legacy_receipts()) == receipts_before
    assert rpc.send_count() == 0


def test_receipt_path_never_anchors_when_the_flag_is_off() -> None:
    os_env = dict(__import__("os").environ)
    os_env.pop("VOOL_ANCHOR_RECEIPTS", None)
    with mock.patch.dict("os.environ", os_env, clear=True):
        with mock.patch("core.solana_anchor.anchor_vault_proof") as mock_anchor:
            _attach_work_receipt(
                {"response": "no anchor"},
                result={"response": "no anchor"},
                session_id="no-anchor-test",
            )
            mock_anchor.assert_not_called()


# ---------------------------------------------------------------------------
# Gap 6 — Background task poll loop idempotency
# ---------------------------------------------------------------------------

def test_start_web0_background_workers_is_idempotent() -> None:
    import threading

    from core.runtime_backbone import start_web0_background_workers
    count_before = threading.active_count()
    start_web0_background_workers()
    start_web0_background_workers()
    start_web0_background_workers()
    # Should start at most 1 new daemon thread
    assert threading.active_count() <= count_before + 1
