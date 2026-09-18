""".null registration execution: the gate, the refusal paths, the preview -- and the retired tail.

The sign+broadcast tail is a retired money surface. The former happy path
(`test_execute_happy_path_signs_and_broadcasts`) is now the proof that a fully-gated, consented
registration ends in a typed refusal with the wallet never asked to sign and the RPC never asked
to send. Every other gate is unchanged.
"""
from __future__ import annotations

import base64

import pytest

from core.null_register_execute import (
    MAX_REGISTER_LAMPORTS_CEILING,
    SpendGate,
    execute_registration,
    gate_permits_spend,
    preview_registration,
)

pytest.importorskip("solders")
from solders.pubkey import Pubkey

_OWNER = "So11111111111111111111111111111111111111112"
_TREASURY = Pubkey.from_string(_OWNER)
_BLOCKHASH = "11111111111111111111111111111111"  # 32 zero bytes → valid base58 hash for tests
LEGACY = "wallet_legacy_surface_retired"


from core.null_registrar import derive_config_pda, derive_owner_cap_pda
from core.wallet_spend_policy import SpendLedger, SpendPolicy

_CONFIG_PDA = derive_config_pda()


def _never_record(*_a, **_k):
    raise AssertionError("nothing may be recorded: no spend happened")
_OWNER_CAP_PDA = derive_owner_cap_pda(_OWNER)


def _config_bytes(sol_fee: int) -> bytes:
    buf = bytearray(122)
    buf[0] = 0x52
    buf[33:41] = int(sol_fee).to_bytes(8, "little")
    buf[81:113] = bytes(_TREASURY)
    return bytes(buf)


def _owner_cap_bytes(count: int) -> bytes:
    buf = bytearray(36)
    buf[0] = 0x4B  # 'K'
    buf[33:35] = int(count).to_bytes(2, "little")  # count u16 LE @33
    return bytes(buf)


def _make_rpc(sol_fee=0, sig="SIG_ABC", owner_count=0, calls=None):
    seen = calls if calls is not None else []

    def rpc(method, params, **kw):
        seen.append(method)
        if method == "getAccountInfo":
            pubkey = params[0]
            if pubkey == _CONFIG_PDA:
                return {"value": {"data": [base64.b64encode(_config_bytes(sol_fee)).decode(), "base64"]}}
            # owner_cap PDA: absent (count 0) or present with the given lifetime count.
            if owner_count == 0:
                return {"value": None}
            return {"value": {"data": [base64.b64encode(_owner_cap_bytes(owner_count)).decode(), "base64"]}}
        if method == "getMinimumBalanceForRentExemption":
            size = params[0]
            return 900_000 if size == 36 else 2_700_000  # owner_cap (36B) vs domain (314B)
        if method == "sendTransaction":
            return sig
        return None

    return rpc


class _Wallet:
    pubkey = _OWNER

    def __init__(self):
        self.signed = 0

    def sign_transaction(self, message_bytes: bytes) -> bytes:
        self.signed += 1
        return b"\x00" * 64


def _full_gate():
    return SpendGate(allow_spend=True, approve=True, max_spend_lamports=10_000_000, wallet_present=True)


# ---- gate unit tests -------------------------------------------------------

def test_gate_requires_every_condition():
    cost = 3_000_000
    assert gate_permits_spend(SpendGate(), cost)[0] is False  # nothing set
    assert gate_permits_spend(SpendGate(wallet_present=True), cost)[0] is False  # no allow_spend
    assert gate_permits_spend(SpendGate(wallet_present=True, allow_spend=True), cost)[0] is False  # no approve
    g = SpendGate(wallet_present=True, allow_spend=True, approve=True)  # no cap
    assert gate_permits_spend(g, cost)[0] is False
    ok = SpendGate(wallet_present=True, allow_spend=True, approve=True, max_spend_lamports=10_000_000)
    assert gate_permits_spend(ok, cost)[0] is True


def test_gate_rejects_over_cap_and_over_ceiling():
    g = SpendGate(wallet_present=True, allow_spend=True, approve=True, max_spend_lamports=1_000_000)
    assert gate_permits_spend(g, 3_000_000)[0] is False  # over the user cap
    huge = SpendGate(wallet_present=True, allow_spend=True, approve=True, max_spend_lamports=10**12)
    assert gate_permits_spend(huge, MAX_REGISTER_LAMPORTS_CEILING + 1)[0] is False  # over hard ceiling


# ---- execute: refusal paths (never sign / never broadcast) -----------------

def _patch_available(monkeypatch, available: bool):
    monkeypatch.setattr("core.null_resolver.resolve_null_domain", lambda name, **kw: (None if available else {"owner": "x"}))


def test_execute_refuses_without_wallet(monkeypatch):
    _patch_available(monkeypatch, True)
    out = execute_registration("test", gate=_full_gate(), wallet=None, rpc=_make_rpc(), consent=lambda r: True)
    assert out.status == "refused"


def test_execute_refuses_when_name_taken(monkeypatch):
    _patch_available(monkeypatch, False)
    w = _Wallet()
    out = execute_registration("test", gate=_full_gate(), wallet=w, rpc=_make_rpc(), consent=lambda r: True)
    assert out.status == "refused"
    assert w.signed == 0  # never signed


def test_execute_action_required_when_gate_incomplete(monkeypatch):
    _patch_available(monkeypatch, True)
    w = _Wallet()
    gate = SpendGate(allow_spend=False, approve=True, max_spend_lamports=10_000_000, wallet_present=True)
    out = execute_registration("test", gate=gate, wallet=w, rpc=_make_rpc(), consent=lambda r: True)
    assert out.status == "action_required"
    assert w.signed == 0


def test_execute_refused_when_consent_denied(monkeypatch):
    _patch_available(monkeypatch, True)
    w = _Wallet()
    out = execute_registration("test", gate=_full_gate(), wallet=w, rpc=_make_rpc(), consent=lambda r: False,
                               blockhash_fn=lambda: _BLOCKHASH)
    assert out.status == "refused"
    assert w.signed == 0  # consent denied before signing


def test_execute_fails_closed_when_consent_unavailable(monkeypatch):
    _patch_available(monkeypatch, True)
    w = _Wallet()

    def raising_consent(reason):
        raise RuntimeError("no consent mechanism")

    out = execute_registration("test", gate=_full_gate(), wallet=w, rpc=_make_rpc(), consent=raising_consent,
                               blockhash_fn=lambda: _BLOCKHASH)
    assert out.status == "refused"
    assert w.signed == 0  # fail-closed: no consent -> no spend


def test_execute_refuses_over_cap(monkeypatch):
    _patch_available(monkeypatch, True)
    w = _Wallet()
    tiny_cap = SpendGate(allow_spend=True, approve=True, max_spend_lamports=1, wallet_present=True)
    out = execute_registration("test", gate=tiny_cap, wallet=w, rpc=_make_rpc(),
                               consent=lambda r: True, blockhash_fn=lambda: _BLOCKHASH)
    assert out.status == "action_required"  # cost exceeds cap
    assert w.signed == 0


# ---- execute: the fully-gated path ends in the retired tail's typed refusal ---

def test_execute_fully_gated_path_refuses_typed_and_never_signs_or_sends(monkeypatch):
    from core.faults.recorder import list_faults

    _patch_available(monkeypatch, True)
    w = _Wallet()
    rpc_calls: list[str] = []
    out = execute_registration(
        "parad0x",
        gate=_full_gate(),
        wallet=w,
        rpc=_make_rpc(sol_fee=0, sig="REALSIG123", calls=rpc_calls),
        consent=lambda r: True,
        blockhash_fn=lambda: _BLOCKHASH,
        policy_loader=lambda _w: (SpendPolicy(), SpendLedger()),
        recorder=_never_record,
    )
    assert out.status == "refused"
    assert out.message.startswith(LEGACY + ":")
    assert not out.signature
    assert out.plan is not None and out.plan.total_lamports == 3_600_000  # the plan was priced, then refused
    assert w.signed == 0  # the wallet was never asked to sign
    assert "sendTransaction" not in rpc_calls  # the RPC was never asked to send
    assert any(r.context.get("surface") == "null_register_execute.sign_and_broadcast_registration" for r in list_faults(code=LEGACY, limit=50))


def test_preview_never_signs(monkeypatch):
    _patch_available(monkeypatch, True)
    out = preview_registration("parad0x", _OWNER, rpc=_make_rpc(sol_fee=0))
    assert out.status == "preview"
    assert "MAINNET" in out.message
    assert out.plan is not None and out.plan.total_lamports == 3_600_000


def test_execute_refuses_premium_1to3_char_name(monkeypatch):
    _patch_available(monkeypatch, True)
    w = _Wallet()
    out = execute_registration("abc", gate=_full_gate(), wallet=w, rpc=_make_rpc(),
                               consent=lambda r: True, blockhash_fn=lambda: _BLOCKHASH)
    assert out.status == "refused"
    assert "premium" in out.message.lower() and "auction" in out.message.lower()
    assert w.signed == 0  # never broadcast a Register that would revert with NameTooShort


def test_execute_refuses_when_wallet_at_cap(monkeypatch):
    _patch_available(monkeypatch, True)
    w = _Wallet()
    out = execute_registration("newname", gate=_full_gate(), wallet=w,
                               rpc=_make_rpc(owner_count=3), consent=lambda r: True,
                               blockhash_fn=lambda: _BLOCKHASH)
    assert out.status == "refused"
    assert "cap" in out.message.lower()
    assert w.signed == 0  # never broadcast a 4th register that would revert with CapacityExceeded
