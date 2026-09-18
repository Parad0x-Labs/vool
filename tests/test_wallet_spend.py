"""The retired `wallet.spend` seam: every spend door refuses typed and receipt-backed; the guardrail
files it owned (HMAC-signed policy and ledger) still verify and still fail closed.

Rewritten when core.wallet became the one money authority. The tests that used to drive a FAKE
wallet through `execute_spend` and watch the ledger fill are gone with the transfer path: there is
no transfer path. What remains is proved here instead -- an injected wallet is never reached, a
provider can no longer be registered, the runtime handler has no confirm-and-retry door, every
refusal carries a fault receipt naming its surface, and no legacy key file appears under the home.
The tamper-evidence of the policy/ledger files is unchanged and still pinned.
"""
from __future__ import annotations

import json

import pytest

from core import runtime_paths, wallet_spend_tools
from core import wallet_spend_policy as wsp
from core.faults.recorder import list_faults
from core.runtime_execution_tools import _wallet_spend, execute_runtime_tool
from core.wallet.errors import WalletFault

LEGACY = "wallet_legacy_surface_retired"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    monkeypatch.delenv("VOOL_ENABLE_WALLET_SPEND", raising=False)
    runtime_paths.configure_runtime_home(tmp_path)
    monkeypatch.setattr(wallet_spend_tools, "_WALLET_PROVIDER", None)
    yield
    runtime_paths.configure_runtime_home(None)
    assert not list(tmp_path.rglob("solana_wallet.enc")), "a legacy key file was minted under the test home"


class FakeWallet:
    """A would-be signer. If any code path still reaches `.transfer`, `calls` fills up."""

    def __init__(self, ref: str = "tx-fake-1") -> None:
        self.ref = ref
        self.calls: list[tuple] = []

    def transfer(self, to: str, amount: int, asset: str) -> str:
        self.calls.append((to, amount, asset))
        return self.ref


def _enable(monkeypatch) -> None:
    monkeypatch.setenv("VOOL_ENABLE_WALLET_SPEND", "1")


def _configure(per_tx: int = 0, daily: int = 0, weekly: int = 0, frozen: bool = False) -> None:
    policy = wsp.SpendPolicy(per_tx_cap_lamports=per_tx, daily_cap_lamports=daily, weekly_cap_lamports=weekly, frozen=frozen)
    assert wallet_spend_tools.save_policy(policy) is True  # node key available in the test env


def _assert_typed_refusal(result: wallet_spend_tools.SpendResult, *, surface: str) -> None:
    assert result.ok is False and result.status == LEGACY
    fault = result.details["fault"]
    assert fault["code"] == LEGACY
    assert fault["fault_id"].startswith("fault-"), fault
    assert fault["context"]["surface"] == surface
    assert "wallet.propose" in result.message
    receipts = [r for r in list_faults(code=LEGACY, limit=50) if r.fault_id == fault["fault_id"]]
    assert receipts and receipts[0].context.get("surface") == surface, "the refusal left no fault receipt"


# --- execute_spend: refuses whether the flag is off, on, or a wallet is injected --------------------

def test_execute_spend_refuses_with_the_flag_off_and_a_wallet_injected() -> None:
    w = FakeWallet()
    r = wallet_spend_tools.execute_spend(to="addr", amount_lamports=5, wallet=w)
    _assert_typed_refusal(r, surface="wallet_spend_tools.execute_spend")
    assert w.calls == []


def test_execute_spend_refuses_even_when_enabled_configured_and_wired(monkeypatch) -> None:
    _enable(monkeypatch)
    _configure(per_tx=1_000, daily=1_000, weekly=1_000)
    w = FakeWallet(ref="tx-would-have-sent")
    r = wallet_spend_tools.execute_spend(to="dest", amount_lamports=50, asset="SOL", now=2000.0, wallet=w)
    _assert_typed_refusal(r, surface="wallet_spend_tools.execute_spend")
    assert w.calls == [], "the injected wallet must never be reached"
    assert r.details["to"] == "dest" and r.details["amount_lamports"] == 50 and r.details["asset"] == "SOL"
    assert "tx_ref" not in r.details
    assert wallet_spend_tools.load_ledger().entries == [], "a refusal must not touch the ledger"


@pytest.mark.parametrize("to,amount", [("  ", 5), ("addr", 0), ("addr", -1), ("addr", "not-a-number")])
def test_execute_spend_refuses_before_any_input_validation_can_be_reached(monkeypatch, to, amount) -> None:
    # Malformed inputs land on the same typed refusal: there is no path that returns a different status
    # and no path that could be coaxed into moving on to a signer by fixing the arguments.
    _enable(monkeypatch)
    _configure(per_tx=1_000)
    try:
        r = wallet_spend_tools.execute_spend(to=to, amount_lamports=amount, wallet=FakeWallet())  # type: ignore[arg-type]
    except (TypeError, ValueError):
        pytest.fail("a malformed spend argument crashed the refusal instead of refusing typed")
    assert r.ok is False and r.status == LEGACY


def test_frozen_policy_is_still_a_refusal_not_a_different_door(monkeypatch) -> None:
    _enable(monkeypatch)
    _configure(per_tx=1_000, frozen=True)
    w = FakeWallet()
    r = wallet_spend_tools.execute_spend(to="addr", amount_lamports=5, wallet=w)
    assert r.ok is False and r.status == LEGACY and w.calls == []


# --- the provider seam is closed ----------------------------------------------------------------

def test_no_provider_registered_by_default() -> None:
    assert wallet_spend_tools.wallet_provider_registered() is False


def test_register_wallet_provider_refuses_typed_and_registers_nothing() -> None:
    with pytest.raises(WalletFault) as exc:
        wallet_spend_tools.register_wallet_provider(lambda: FakeWallet())
    assert exc.value.code == LEGACY
    assert exc.value.fault_id.startswith("fault-")
    assert exc.value.context["surface"] == "wallet_spend_tools.register_wallet_provider"
    assert wallet_spend_tools.wallet_provider_registered() is False
    assert wallet_spend_tools._WALLET_PROVIDER is None


def test_clearing_the_provider_is_refused_the_same_way() -> None:
    with pytest.raises(WalletFault) as exc:
        wallet_spend_tools.register_wallet_provider(None)
    assert exc.value.code == LEGACY


def test_a_provider_smuggled_into_the_module_global_is_never_resolved(monkeypatch) -> None:
    # Even a direct write to the module global (bypassing register_wallet_provider) supplies nothing.
    w = FakeWallet(ref="tx-provider")
    monkeypatch.setattr(wallet_spend_tools, "_WALLET_PROVIDER", lambda: w)
    assert wallet_spend_tools._resolve_wallet(None) is None
    assert wallet_spend_tools._resolve_wallet(w) is None, "an explicitly injected wallet is not resolved either"
    assert wallet_spend_tools.wallet_provider_registered() is False
    monkeypatch.setenv("VOOL_ENABLE_WALLET_SPEND", "1")
    _configure(per_tx=1_000)
    r = wallet_spend_tools.execute_spend(to="addr", amount_lamports=5)
    assert r.ok is False and r.status == LEGACY and w.calls == []


# --- the runtime handler and the tool contract --------------------------------------------------

def test_contract_disabled_by_default() -> None:
    r = execute_runtime_tool("wallet.spend", {"to": "addr", "amount_lamports": 5, "allow_spend": True, "approve": True})
    assert r is not None and r.handled and not r.ok and r.status == "disabled"


def test_contract_stays_disabled_under_the_env_flag(monkeypatch) -> None:
    # The runtime contract's own gate (policy engine `wallet.spend_enabled`) is independent of the
    # module env flag; the env flag alone does not open the contract.
    _enable(monkeypatch)
    r = execute_runtime_tool("wallet.spend", {"to": "addr", "amount_lamports": 5, "allow_spend": True, "approve": True})
    assert r is not None and r.handled and not r.ok and r.status == "disabled"


@pytest.mark.parametrize(
    "arguments",
    [
        {"to": "dest", "amount_lamports": 5},
        {"to": "dest", "amount_lamports": 5, "allow_spend": True, "approve": True},
        {"to": "dest", "amount_lamports": 5, "allow_spend": True, "approve": True, "asset": "USDC"},
        {},
    ],
)
def test_handler_refuses_typed_regardless_of_approval_flags(monkeypatch, arguments) -> None:
    _enable(monkeypatch)
    _configure(per_tx=1_000)
    res = _wallet_spend(arguments)
    assert res.handled and res.ok is False and res.status == LEGACY
    fault = res.details["fault"]
    assert fault["code"] == LEGACY and fault["fault_id"].startswith("fault-")
    assert fault["context"]["surface"] == "tool.wallet.spend"
    assert "action_required" not in res.details, "there is no confirm-and-retry door any more"
    assert res.details["observation"]["status"] == LEGACY
    assert res.details["observation"]["fault_id"] == fault["fault_id"]
    assert "wallet.propose" in res.response_text


def test_handler_refusal_leaves_a_receipt_per_call() -> None:
    first = _wallet_spend({"to": "dest", "amount_lamports": 5, "allow_spend": True, "approve": True})
    second = _wallet_spend({"to": "dest", "amount_lamports": 5, "allow_spend": True, "approve": True})
    ids = {first.details["fault"]["fault_id"], second.details["fault"]["fault_id"]}
    assert len(ids) == 2
    recorded = {r.fault_id for r in list_faults(code=LEGACY, limit=50)}
    assert ids <= recorded


# --- the guardrail files that remain: HMAC-signed, atomic, fail-closed ---------------------------

def test_policy_roundtrip_and_tamper_detection() -> None:
    _configure(per_tx=1_000)
    loaded = wallet_spend_tools.load_policy()
    assert loaded is not None and loaded.per_tx_cap_lamports == 1_000
    path = wallet_spend_tools._policy_path()
    blob = json.loads(path.read_text(encoding="utf-8"))
    blob["sig"] = "00" * 32  # forge the signature
    path.write_text(json.dumps(blob), encoding="utf-8")
    assert wallet_spend_tools.load_policy() is None


def test_policy_value_tamper_is_detected() -> None:
    _configure(per_tx=1_000)
    path = wallet_spend_tools._policy_path()
    # raise the cap on disk without the key
    text = path.read_text(encoding="utf-8").replace("1000", "999000000000")
    path.write_text(text, encoding="utf-8")
    assert wallet_spend_tools.load_policy() is None


def test_ledger_roundtrip_is_signed_and_verifies() -> None:
    ledger = wsp.SpendLedger(entries=[(100.0, 5), (200.0, 7)])
    assert wallet_spend_tools.save_ledger(ledger) is True
    blob = json.loads(wallet_spend_tools._ledger_path().read_text(encoding="utf-8"))
    assert blob.get("sig")  # HMAC-signed like the policy
    loaded = wallet_spend_tools.load_ledger()
    assert loaded is not None and loaded.entries == [(100.0, 5), (200.0, 7)]


def test_missing_ledger_is_a_fresh_empty_ledger() -> None:
    loaded = wallet_spend_tools.load_ledger()
    assert loaded is not None and loaded.entries == []  # no file yet -> empty, not tampered


def test_tampered_ledger_fails_closed() -> None:
    assert wallet_spend_tools.save_ledger(wsp.SpendLedger(entries=[(1000.0, 50)])) is True
    # Forge an empty ledger to dodge a daily cap (the classic "reset my caps" attack).
    path = wallet_spend_tools._ledger_path()
    blob = json.loads(path.read_text(encoding="utf-8"))
    blob["entries"] = []
    path.write_text(json.dumps(blob), encoding="utf-8")
    assert wallet_spend_tools.load_ledger() is None  # signature mismatch detected


def test_unparseable_ledger_fails_closed() -> None:
    wallet_spend_tools._ledger_path().parent.mkdir(parents=True, exist_ok=True)
    wallet_spend_tools._ledger_path().write_text("{not json", encoding="utf-8")
    assert wallet_spend_tools.load_ledger() is None


@pytest.mark.parametrize("amount", ["not-a-number", object(), float("nan"), [1]])
def test_a_malformed_amount_still_gets_the_typed_refusal_not_a_crash(amount) -> None:
    # The refusal echoes the caller's arguments; a bad amount must never turn the typed refusal
    # into an untyped ValueError/TypeError.
    result = wallet_spend_tools.execute_spend(to="Recipient111111111111111111111111111111111", amount_lamports=amount)
    assert result.ok is False and result.status == LEGACY
    assert result.details["fault"]["fault_id"].startswith("fault-")
    assert result.details["amount_lamports"] is None or isinstance(result.details["amount_lamports"], int)
