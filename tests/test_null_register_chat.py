"""Security + behavior tests for in-chat .null registration.

The central property under test: a chat message NEVER moves SOL on its own. The
chat "yes" only *triggers* an attempt; the live OS-consent (Windows Hello) prompt
inside execute_registration is the real, un-spoofable gate, and it is fail-closed.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from core.null_register_chat import (
    CHAT_REGISTER_CAP_LAMPORTS,
    maybe_handle_null_registration,
)

_PUBKEY = "28hxXaSfXrY2UTEEuHseP1VfRdq3nUyyPaYBMHsWW2VX"


class _MemStore:
    """In-memory stand-in for storage.null_register_pending_store."""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}

    def stage(self, session_id, *, name, cost_lamports, owner_pubkey) -> None:
        self.rows[session_id] = {
            "session_id": session_id,
            "name": name,
            "cost_lamports": cost_lamports,
            "owner_pubkey": owner_pubkey,
        }

    def load(self, session_id, **_kw):
        return self.rows.get(session_id)

    def clear(self, session_id) -> None:
        self.rows.pop(session_id, None)


def _wallet(pubkey: str = _PUBKEY):
    return SimpleNamespace(pubkey=pubkey, sign_transaction=mock.Mock(return_value=b"\x00" * 64))


def _preview_ok(total: int = 11_200_000, rent: int = 2_000_000, fee: int = 9_200_000):
    plan = SimpleNamespace(total_lamports=total, rent_lamports=rent, sol_fee_lamports=fee)
    return SimpleNamespace(status="preview", message="Register mysite on Solana MAINNET", plan=plan)


def _call(
    text,
    session_id: str = "sess-1",
    *,
    store: _MemStore | None = None,
    wallet=None,
    preview=None,
    execute=None,
):
    store = store or _MemStore()
    wallet = wallet if wallet is not None else _wallet()
    preview_fn = preview or (lambda name, owner: _preview_ok())
    execute_fn = execute or mock.Mock(
        return_value=SimpleNamespace(status="submitted", message="ok", signature="SIG123")
    )
    result = maybe_handle_null_registration(
        text,
        session_id,
        preview_fn=preview_fn,
        execute_fn=execute_fn,
        wallet_fn=lambda: wallet,
        stage_fn=store.stage,
        load_fn=store.load,
        clear_fn=store.clear,
    )
    return result, store, wallet, execute_fn


# --- phase 1: offer is read-only, never spends -----------------------------------

def test_register_command_offers_and_stages_but_never_executes() -> None:
    result, store, _wallet_obj, execute_fn = _call("register mysite.null")
    assert result is not None
    assert result["intent"] == "null_register_offer"
    execute_fn.assert_not_called()  # offering must never sign/broadcast
    assert "sess-1" in store.rows  # a pending offer was staged
    text = result["response"].lower()
    assert "windows hello" in text  # the offer names the real gate
    assert "mainnet" in text
    assert "yes" in text  # asks for explicit confirmation


def test_register_command_with_trailing_words_still_offers() -> None:
    result, _store, _w, execute_fn = _call("register mysite.null for me please")
    assert result is not None and result["intent"] == "null_register_offer"
    execute_fn.assert_not_called()


def test_register_phrase_with_domain_recall_does_not_stage_offer() -> None:
    result, store, _wallet_obj, execute_fn = _call(
        "Register alice.null as my project domain, noted. What domain did I ask for?"
    )

    assert result is None
    assert store.rows == {}
    execute_fn.assert_not_called()


# --- question / non-command must NOT fire (falls through to grounding) ------------

@pytest.mark.parametrize(
    "text",
    [
        "can I register foo.null?",
        "how do I register foo.null",
        "what does it cost to register foo.null",
        "is foo.null available",
        "hi, what's up?",
        "register for the newsletter",
    ],
)
def test_non_command_does_not_fire(text) -> None:
    result, store, _w, execute_fn = _call(text)
    assert result is None
    execute_fn.assert_not_called()
    assert store.rows == {}


# --- phase 2: confirmation -------------------------------------------------------

def test_confirm_yes_executes_with_trusted_capped_gate() -> None:
    store = _MemStore()
    # 1) offer
    _call("register mysite.null", store=store)
    assert "sess-1" in store.rows
    # 2) a strict "yes" confirms
    result, _store2, _w, execute_fn = _call("yes", store=store)
    assert result is not None
    assert result["intent"] == "null_register_submitted"
    assert "SIG123" in result["response"]
    execute_fn.assert_called_once()
    # The gate must be built from TRUSTED constants, not from anything the user typed.
    _args, kwargs = execute_fn.call_args
    gate = kwargs["gate"]
    assert gate.allow_spend is True
    assert gate.approve is True
    assert gate.wallet_present is True
    assert gate.max_spend_lamports == CHAT_REGISTER_CAP_LAMPORTS
    # single-use: pending cleared after execution
    assert store.rows == {}


def test_yes_with_no_pending_offer_returns_none() -> None:
    result, _store, _w, execute_fn = _call("yes")  # no prior offer
    assert result is None
    execute_fn.assert_not_called()


def test_ambiguous_yes_does_not_execute_and_keeps_pending() -> None:
    store = _MemStore()
    _call("register mysite.null", store=store)
    result, _store, _w, execute_fn = _call("yes but let me think about it first", store=store)
    assert result is None  # not a clean confirmation -> don't hijack
    execute_fn.assert_not_called()
    assert "sess-1" in store.rows  # offer still pending, not consumed


def test_negative_cancels_without_executing() -> None:
    store = _MemStore()
    _call("register mysite.null", store=store)
    result, _store, _w, execute_fn = _call("no", store=store)
    assert result is not None
    assert result["intent"] == "null_register_cancelled"
    execute_fn.assert_not_called()
    assert store.rows == {}  # cleared


def test_new_command_supersedes_pending_offer() -> None:
    store = _MemStore()
    _call("register mysite.null", store=store)
    result, _store, _w, execute_fn = _call("register other.null", store=store)
    assert result is not None and result["intent"] == "null_register_offer"
    execute_fn.assert_not_called()
    assert store.rows["sess-1"]["name"] == "other.null"


# --- cost guardrails -------------------------------------------------------------

def test_over_cap_redirects_to_cli_and_does_not_stage() -> None:
    over = _preview_ok(total=CHAT_REGISTER_CAP_LAMPORTS + 1)
    result, store, _w, execute_fn = _call(
        "register mysite.null", preview=lambda n, o: over
    )
    assert result is not None
    assert result["intent"] == "null_register_over_cap"
    assert "vool register" in result["response"]
    execute_fn.assert_not_called()
    assert store.rows == {}  # nothing staged -> a later "yes" can't fire it


def test_premium_or_taken_name_is_reported_not_staged() -> None:
    refused = SimpleNamespace(status="refused", message="mysite.null is already registered", plan=None)
    result, store, _w, execute_fn = _call("register mysite.null", preview=lambda n, o: refused)
    assert result is not None
    assert result["intent"] == "null_register_unavailable"
    execute_fn.assert_not_called()
    assert store.rows == {}


def test_preview_error_points_to_cli_and_does_not_stage() -> None:
    err = SimpleNamespace(status="error", message="rpc down", plan=None)
    result, store, _w, execute_fn = _call("register mysite.null", preview=lambda n, o: err)
    assert result is not None
    assert result["intent"] == "null_register_preview_error"
    execute_fn.assert_not_called()
    assert store.rows == {}


# --- THE money-safety proof: chat "yes" cannot spend without OS consent -----------

def _real_plan():
    from core.null_register_execute import NULL_REGISTRAR_MAINNET
    from core.null_registrar import RegisterPlan

    return RegisterPlan(
        name="mysite.null",
        program_id=NULL_REGISTRAR_MAINNET,
        domain_pda="",
        config_pda="",
        owner_cap_pda="",
        owner=_PUBKEY,
        treasury="",
        sol_fee_lamports=9_200_000,
        rent_lamports=2_000_000,
        owner_cap_rent_lamports=0,
        instruction_data_hex="",
        accounts=[],
    )


def _permissive_policy():
    """Pin the spend-policy loader to permissive defaults so these consent tests are
    hermetic (no dependency on any on-disk policy in the shared test home)."""
    from core.wallet_spend_policy import SpendLedger, SpendPolicy

    return mock.patch(
        "core.wallet_spend_policy_store.load_policy_and_ledger",
        return_value=(SpendPolicy(), SpendLedger()),
    )


@pytest.fixture
def _reset_consent():
    from core.os_consent_gate import set_consent_override_for_tests

    yield set_consent_override_for_tests
    set_consent_override_for_tests(None)


def test_chat_yes_cannot_spend_when_os_consent_declined(_reset_consent) -> None:
    """Even with a passing app-gate, a declined OS-consent prompt = no signing, no spend."""
    from core.null_register_execute import execute_registration

    _reset_consent(lambda _reason: False)  # OS consent DECLINES
    wallet = _wallet()
    store = _MemStore()
    _call("register mysite.null", store=store, wallet=wallet)

    with mock.patch("core.null_register_execute._is_available", return_value=True), mock.patch(
        "core.null_register_execute._plan_with_costs", return_value=(_real_plan(), None)
    ), _permissive_policy():
        result, _store, _w2, _exec = _call(
            "yes", store=store, wallet=wallet, execute=execute_registration
        )

    assert result is not None
    assert result["intent"] == "null_register_refused"
    assert "did **not** spend" in result["response"].lower() or "not spend" in result["response"].lower()
    wallet.sign_transaction.assert_not_called()  # the key proof: never signed


def test_chat_yes_cannot_spend_when_os_consent_unavailable(_reset_consent) -> None:
    """If the OS prompt can't even be shown (raises), the flow fails closed."""
    from core.null_register_execute import execute_registration

    def _raise(_reason):
        raise RuntimeError("no consent mechanism")

    _reset_consent(_raise)
    wallet = _wallet()
    store = _MemStore()
    _call("register mysite.null", store=store, wallet=wallet)

    with mock.patch("core.null_register_execute._is_available", return_value=True), mock.patch(
        "core.null_register_execute._plan_with_costs", return_value=(_real_plan(), None)
    ), _permissive_policy():
        result, _store, _w, _exec = _call(
            "yes", store=store, wallet=wallet, execute=execute_registration
        )

    assert result is not None
    assert result["intent"] == "null_register_refused"
    wallet.sign_transaction.assert_not_called()


def test_no_wallet_confirmation_spends_nothing() -> None:
    store = _MemStore()
    # stage a pending row directly (as if an offer had been made on another wallet)
    store.stage("sess-1", name="mysite.null", cost_lamports=11_200_000, owner_pubkey=_PUBKEY)
    execute_fn = mock.Mock()
    result = maybe_handle_null_registration(
        "yes",
        "sess-1",
        preview_fn=lambda n, o: _preview_ok(),
        execute_fn=execute_fn,
        wallet_fn=lambda: None,  # wallet unreachable
        stage_fn=store.stage,
        load_fn=store.load,
        clear_fn=store.clear,
    )
    assert result is not None
    assert result["intent"] == "null_register_no_wallet"
    execute_fn.assert_not_called()
    assert store.rows == {}  # pending consumed (single-use), still nothing spent
