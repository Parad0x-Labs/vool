"""Gauntlet — category 9: x402 / wallet safety (zero-tolerance).

Rewritten when core.wallet became the one money authority. What these pin now:

  1. The freeze that actually reaches a user goes through POST /api/chat, not the unit brake.
     The HTTP surface freezes the canonical wallet store (core.wallet.limits) AND the device-keyed
     legacy policy file, never invokes the model, and never mints a wallet to do it.
  2. Deleting the legacy policy file still reverts THAT file's loader to permissive defaults (the
     documented gap, kept as a canary) -- but the canonical freeze lives in the wallet database and
     is untouched by the deletion, so the wallet lifecycle stays frozen.
  3. `pay.x402` is a retired money surface: every argument shape (no opt-in, full opt-in, an
     over-ceiling cap) gets the same typed, receipt-backed refusal; the payment callable is never
     invoked; no wallet is loaded. The former "disabled lane" / "preview clamps the cap" pins are
     gone with the preview: there is no preview and no lane to disable.
  4. The .null registration freeze check still runs BEFORE the human consent prompt.

Every test runs in an isolated VOOL_HOME (tmp) so it can never touch the real wallet on this
box, and injects rpc/blockhash/consent so no default ever fires a live RPC call or a real OS
consent prompt.
"""
from __future__ import annotations

import hashlib
import json
from unittest import mock

import pytest

pytestmark = [pytest.mark.gauntlet, pytest.mark.safety]

LEGACY = "wallet_legacy_surface_retired"


class _FakeWallet:
    """A would-be signer for the .null registration tail. Its sign_transaction is a spy: any call
    is a fence breach. Mirrors the helper in tests/test_wallet_spend_policy_store.py."""

    def __init__(self, seed: bytes = b"\x01" * 32, pubkey: str = "28hxXaSfXrY2UTEEuHseP1VfRdq3nUyyPaYBMHsWW2VX") -> None:
        self._seed = seed
        self.pubkey = pubkey
        self.sign_transaction = mock.Mock(return_value=b"\x00" * 64)

    def sign(self, payload: bytes) -> bytes:
        return hashlib.sha512(self._seed + bytes(payload)).digest()  # 64 bytes


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Point every runtime path at a throwaway home so wallet/policy writes can
    never touch the operator's real data dir."""
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    from core import runtime_paths
    from core.wallet import limits

    monkeypatch.setattr(runtime_paths, "_VOOL_HOME_OVERRIDE", None, raising=False)
    limits.set_frozen(False)
    yield tmp_path
    limits.set_frozen(False)
    assert not list(tmp_path.rglob("solana_wallet.enc")), "a legacy key file was minted under the isolated home"


def _post_chat(home, text: str, *, run_agent_provider):
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_post

    return dispatch_post(
        path="/api/chat",
        body={"messages": [{"role": "user", "content": text}]},
        headers={},
        runtime=RuntimeServices(display_name="VOOL"),
        model_name="vool",
        workspace_root_provider=lambda: str(home),
        run_agent_provider=run_agent_provider,
        resolve_null_domain_provider=lambda name: None,
    )


# ---------------------------------------------------------------------------
# 1. HTTP end-to-end: /stopx402 freezes both stores and never calls the model
# ---------------------------------------------------------------------------

def test_http_stopx402_freezes_both_stores_without_invoking_model(isolated_home):
    from core.wallet import limits
    from core.wallet_spend_policy_store import load_policy_and_ledger, policy_path

    # If the model path is ever reached for a /stopx402 message, that is a bug: a stop
    # must not depend on the model being alive. Make the model provider explode.
    def _model_must_not_run(*_args, **_kwargs):
        raise AssertionError("the model was invoked for a /stopx402 turn — the brake must short-circuit first")

    assert limits.is_frozen() is False
    resp = _post_chat(isolated_home, "/stopx402", run_agent_provider=_model_must_not_run)

    assert resp.status == 200
    body = json.loads(resp.body)
    # Ollama-shaped chat payload: the brake's text lands in message.content.
    text = json.dumps(body).lower()
    assert "frozen" in text or "stopped" in text

    # The canonical freeze the wallet lifecycle consults is on...
    assert limits.is_frozen() is True
    verdict = limits.check_limits("wallet-1", "SOL", 1, "11111111111111111111111111111111", now=1_000.0)
    assert verdict.ok is False and verdict.limit == limits.LIMIT_FROZEN
    # ...and the device-keyed legacy policy file under THIS home mirrors it, with no wallet loaded.
    assert policy_path().parent == isolated_home / "data" / "keys"
    policy, _ledger = load_policy_and_ledger(None)
    assert policy.frozen is True


def test_http_stopx402_then_check_spend_allowed_refuses(isolated_home):
    from core.wallet_spend_policy import check_spend_allowed
    from core.wallet_spend_policy_store import load_policy_and_ledger

    _post_chat(isolated_home, "/stopx402", run_agent_provider=lambda *a, **k: {"response": "unused"})

    policy, ledger = load_policy_and_ledger(None)
    allowed, reason = check_spend_allowed(policy, ledger, 1_000_000, 1_000.0)
    assert allowed is False
    assert "frozen" in reason.lower()


def test_http_startx402_from_a_non_loopback_client_stays_frozen(isolated_home):
    from core.wallet import limits

    _post_chat(isolated_home, "/stopx402", run_agent_provider=lambda *a, **k: {"response": "unused"})
    assert limits.is_frozen() is True
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_post

    resp = dispatch_post(
        path="/api/chat",
        body={"messages": [{"role": "user", "content": "/startx402"}]},
        headers={"X-Forwarded-For": "203.0.113.9"},
        runtime=RuntimeServices(display_name="VOOL"),
        model_name="vool",
        workspace_root_provider=lambda: str(isolated_home),
        run_agent_provider=lambda *a, **k: {"response": "unused"},
        resolve_null_domain_provider=lambda name: None,
        client_host="203.0.113.9",
    )
    assert resp.status in {200, 403}
    assert limits.is_frozen() is True, "a non-owner HTTP caller lifted the canonical freeze"


# ---------------------------------------------------------------------------
# 2. Gap pin: deleting the legacy policy file reverts THAT loader, not the wallet
# ---------------------------------------------------------------------------

def test_deleting_policy_file_reverts_legacy_loader_but_not_the_canonical_freeze(isolated_home):
    """DOCUMENTED behaviour of the legacy file, pinned as a canary: with no wallet handle present,
    deleting the signed spend_policy.json makes its loader return permissive defaults
    (frozen=False) instead of failing closed. The freeze the wallet lifecycle actually consults
    lives in the wallet database (core.wallet.limits) and survives the deletion.
    """
    from core.vool_agent_brake import maybe_handle_agent_brake
    from core.wallet import limits
    from core.wallet_spend_policy_store import load_policy_and_ledger, policy_path

    result = maybe_handle_agent_brake("/stopx402", "s", wallet_fn=None)
    assert result is not None and result["success"]
    assert load_policy_and_ledger(None)[0].frozen is True  # freeze persisted in the file
    assert limits.is_frozen() is True

    # Attacker (or a bug) deletes the signed legacy policy file.
    policy_path().unlink()

    reverted, ledger = load_policy_and_ledger(None)
    # GAP (legacy file only): permissive defaults, NOT a fail-closed frozen state.
    assert reverted.frozen is False
    assert reverted.daily_cap_lamports == 0 and reverted.weekly_cap_lamports == 0
    assert ledger.entries == []
    # The canonical freeze is untouched: the wallet lifecycle still refuses to hold an amount.
    assert limits.is_frozen() is True
    assert limits.reserve_spend(wallet_id="w", asset="SOL", amount_minor=1, destination="11111111111111111111111111111111", proposal_id="p-1").ok is False
    assert limits.reservation_state("p-1") == ""


# ---------------------------------------------------------------------------
# 3. pay.x402 is retired: every shape refuses typed, nothing is paid, no wallet is loaded
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "arguments,source_context",
    [
        ({"resource": "https://example.com/paid"}, {}),  # no opt-in / approve / wallet
        ({"resource": "https://example.com/paid", "allow_spend": True, "approve": True, "max_spend_usdc": 0.5}, {"vool_wallet": object(), "owner_local": True}),
        ({"resource": "https://example.com/paid", "allow_spend": True, "approve": True, "max_spend_usdc": 100.0}, {"vool_wallet": object(), "owner_local": True}),
        ({"null_name": "agent.null", "allow_spend": True, "approve": True}, {"owner_local": True}),
        ({}, {}),
    ],
)
def test_pay_x402_refuses_typed_for_every_argument_shape(isolated_home, monkeypatch, arguments, source_context):
    from core.execution.payment_tools import execute_payment_tool
    from core.faults.recorder import list_faults

    monkeypatch.setenv("VOOL_ENABLE_X402_SPEND", "1")
    pay_calls: list[tuple] = []
    quote_calls: list[tuple] = []

    result = execute_payment_tool(
        "pay.x402",
        arguments,
        source_context=source_context,
        dna_pay_and_unlock_fn=lambda *a, **k: pay_calls.append((a, k)) or {"status": "paid", "receipt_hash": "a" * 64},
        dna_get_quote_fn=lambda *a, **k: quote_calls.append((a, k)) or {"amount_usdc": 0.25},
        resolve_x402_endpoint_fn=lambda name: "https://example.com/resolved",
    )

    assert pay_calls == [], "the payment callable was invoked"
    assert result.handled and result.ok is False and result.status == LEGACY
    assert result.mode == "tool_failed" and result.tool_name == "pay.x402"
    fault = result.details["fault"]
    assert fault["code"] == LEGACY and fault["fault_id"].startswith("fault-")
    assert fault["context"]["surface"] == "tool.pay.x402"
    assert result.details["executed"] is False
    assert "action_required" not in result.details and "max_spend_usdc" not in result.details
    assert result.details["observation"]["status"] == LEGACY
    assert "wallet" in result.response_text.lower()
    receipts = [r for r in list_faults(code=LEGACY, limit=50) if r.fault_id == fault["fault_id"]]
    assert receipts and receipts[0].context.get("surface") == "tool.pay.x402"


def test_pay_x402_refuses_even_with_a_frozen_policy_on_disk(isolated_home):
    # A frozen policy used to be the reason a buy was blocked; now the door itself is gone, and the
    # refusal is the same typed one whether or not a freeze is set.
    from core.execution.payment_tools import execute_payment_tool
    from core.wallet_spend_policy import SpendLedger, SpendPolicy, freeze
    from core.wallet_spend_policy_store import load_policy_and_ledger, save_policy_and_ledger

    save_policy_and_ledger(None, freeze(SpendPolicy()), SpendLedger())
    assert load_policy_and_ledger(None)[0].frozen is True

    paid = {"called": False}

    def _spy_pay(resource_url, wallet_arg, **kwargs):
        paid["called"] = True
        return {"status": "paid", "receipt_hash": "a" * 64, "amount_usdc": 0.5}

    result = execute_payment_tool(
        "pay.x402",
        {"resource": "https://example.com/paid", "allow_spend": True, "approve": True, "max_spend_usdc": 0.5},
        source_context={"vool_wallet": _FakeWallet(), "owner_local": True},
        dna_pay_and_unlock_fn=_spy_pay,
        dna_get_quote_fn=lambda *a, **k: {"amount_usdc": 0.5},
    )
    assert paid["called"] is False
    assert result.ok is False and result.status == LEGACY


def test_sell_quote_stays_read_only_and_unretired():
    from core.execution.payment_tools import execute_payment_tool

    result = execute_payment_tool("sell.quote", {"task": "summarize a page"}, source_context={})
    assert result.handled and result.status != LEGACY
    assert "no payment was made" in result.response_text.lower()


# ---------------------------------------------------------------------------
# 4. Ordering guarantee: consent is never prompted when the spend is blocked
# ---------------------------------------------------------------------------

def test_frozen_registration_never_prompts_consent():
    """The freeze check must run BEFORE the human consent prompt: a blocked spend
    must not pop an OS dialog. (Complements test_null_register_policy_enforcement
    by spying the consent callable through the injected-deps path.)"""
    from core.null_register_execute import NULL_REGISTRAR_MAINNET, SpendGate, execute_registration
    from core.null_registrar import RegisterPlan
    from core.wallet_spend_policy import SpendLedger, SpendPolicy

    consent = mock.Mock(return_value=True)
    wallet = _FakeWallet()

    plan = RegisterPlan(
        name="mysite.null",
        program_id=NULL_REGISTRAR_MAINNET,
        domain_pda="",
        config_pda="",
        owner_cap_pda="",
        owner=wallet.pubkey,
        treasury="",
        sol_fee_lamports=11_200_000,
        rent_lamports=0,
        owner_cap_rent_lamports=0,
        instruction_data_hex="",
        accounts=[],
    )
    rpc_calls: list[str] = []

    with mock.patch("core.null_register_execute._is_available", return_value=True), mock.patch(
        "core.null_register_execute._plan_with_costs", return_value=(plan, None)
    ), mock.patch("core.null_register_execute._build_register_message", return_value=b"msg"):
        outcome = execute_registration(
            "mysite.null",
            gate=SpendGate(allow_spend=True, approve=True, wallet_present=True, max_spend_lamports=30_000_000),
            wallet=wallet,
            consent=consent,
            rpc=lambda method, params: rpc_calls.append(method) or ("SIG" if method == "sendTransaction" else {}),
            blockhash_fn=lambda: "blockhash11111111111111111111111111111111111",
            policy_loader=lambda _w: (SpendPolicy(frozen=True), SpendLedger()),
            recorder=lambda *a, **k: None,
            now_fn=lambda: 1_000_000.0,
        )

    assert outcome.status == "blocked"
    consent.assert_not_called()
    wallet.sign_transaction.assert_not_called()
    assert "sendTransaction" not in rpc_calls
