"""core.vool_wallet after the retirement of the legacy signing wallet.

What still exists and must keep working: the base58 codecs, Solana pubkey validation, Ed25519
signature verification and the read-only ``legacy_wallet_view`` adapter over core.wallet.

What is retired and must REFUSE typed and receipt-backed: key creation, loading, signing, the
create-on-read door and every private-key export. A refusal proves itself by three facts, all
checked here: the exception is a ``WalletFault`` with the documented code and a filed fault
receipt, no ``solana_wallet.enc`` appears anywhere under the runtime home, and no signature bytes
come back from any signing entry point.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import vool_wallet
from core.vool_wallet import (
    WALLET_FILENAME,
    VoolWallet,
    b58decode,
    b58encode,
    decode_solana_pubkey,
    get_or_create_wallet,
    is_solana_pubkey,
    legacy_wallet_view,
    reveal_wallet_secret_key_base58,
    verify_wallet_signature,
)
from core.wallet.errors import WalletFault
from tests.wallet._rig import rpc

LEGACY = "wallet_legacy_surface_retired"
EXPORT = "wallet_export_refused"
SYSTEM_PROGRAM = "11111111111111111111111111111111"


def _fault_receipts(code: str) -> list:
    from core.faults.recorder import list_faults

    return [f for f in list_faults(limit=200) if f.code == code]


def _security_codes() -> list[str]:
    from core.security_events.store import list_security_events

    return [e.sec_code for e in list_security_events(limit=200)]


def _no_key_file(home: Path) -> None:
    assert not list(home.rglob(WALLET_FILENAME)), "a legacy key file was minted"
    assert not list(home.rglob("*.enc")), "an encrypted key-like file was written"


def _assert_refusal(exc: WalletFault, *, code: str, surface: str) -> None:
    assert exc.code == code
    assert exc.fault_id.startswith("fault-"), exc.to_dict()
    assert exc.user_message.strip(), "a refusal must carry a user-facing message"
    assert exc.context["surface"] == surface
    from core.faults.recorder import fault_by_id

    record = fault_by_id(exc.fault_id)
    assert record is not None and record.code == code, "the refusal is not receipt-backed"


# ---------------------------------------------------------------------------
# codecs and validation (kept)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw",
    [b"\x00", b"\x00\x00\x01", b"\x00" * 32, bytes(range(32)), b"\xff" * 64, os.urandom(17)],
)
def test_base58_roundtrips_including_leading_zero_bytes(raw: bytes) -> None:
    encoded = b58encode(raw)
    assert set(encoded) <= set("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")
    assert b58decode(encoded) == raw


def test_base58_known_vector() -> None:
    assert b58encode(b"\x00" * 32) == SYSTEM_PROGRAM
    assert b58decode(SYSTEM_PROGRAM) == b"\x00" * 32
    assert b58encode(b"") == "1"


@pytest.mark.parametrize("bad", ["", "   ", "0OIl", "abc-def", "hello world", "é"])
def test_base58_decode_rejects_bad_input(bad: str) -> None:
    with pytest.raises(ValueError):
        b58decode(bad)


def test_decode_solana_pubkey_requires_exactly_32_bytes() -> None:
    assert decode_solana_pubkey(SYSTEM_PROGRAM) == b"\x00" * 32
    with pytest.raises(ValueError):
        decode_solana_pubkey(b58encode(b"\x01" * 31))
    with pytest.raises(ValueError):
        decode_solana_pubkey(b58encode(b"\x01" * 33))
    with pytest.raises(ValueError):
        decode_solana_pubkey("not base58 at all!")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (SYSTEM_PROGRAM, True),
        (b58encode(os.urandom(32)), True),
        (b58encode(os.urandom(31)), False),
        (b58encode(os.urandom(64)), False),
        ("", False),
        ("0000", False),
        (None, False),
    ],
)
def test_is_solana_pubkey(value, expected: bool) -> None:
    assert is_solana_pubkey(value) is expected


# ---------------------------------------------------------------------------
# signature verification (kept) against an in-test key, never a runtime wallet
# ---------------------------------------------------------------------------

def _keypair() -> tuple[Ed25519PrivateKey, str]:
    key = Ed25519PrivateKey.generate()
    return key, b58encode(key.public_key().public_bytes_raw())


def test_verify_wallet_signature_accepts_a_valid_ed25519_signature() -> None:
    key, pubkey = _keypair()
    assert verify_wallet_signature(wallet_pubkey=pubkey, message="hello wallet", signature=key.sign(b"hello wallet"))
    assert verify_wallet_signature(wallet_pubkey=pubkey, message=b"\x00bytes\xff", signature=key.sign(b"\x00bytes\xff"))


def test_verify_wallet_signature_rejects_tamper_wrong_key_and_garbage() -> None:
    key, pubkey = _keypair()
    _other, other_pubkey = _keypair()
    signature = key.sign(b"hello wallet")
    assert not verify_wallet_signature(wallet_pubkey=pubkey, message="hello wallet!", signature=signature)
    assert not verify_wallet_signature(wallet_pubkey=other_pubkey, message="hello wallet", signature=signature)
    assert not verify_wallet_signature(wallet_pubkey=pubkey, message="hello wallet", signature=b"\x00" * 64)
    assert not verify_wallet_signature(wallet_pubkey=pubkey, message="hello wallet", signature=b"")
    assert not verify_wallet_signature(wallet_pubkey="not-a-pubkey", message="hello wallet", signature=signature)
    assert not verify_wallet_signature(wallet_pubkey=b58encode(b"\x01" * 31), message="hello wallet", signature=signature)


# ---------------------------------------------------------------------------
# retired key authority: every door refuses typed, receipt-backed, and mints nothing
# ---------------------------------------------------------------------------

def test_generate_and_save_refuses_and_writes_no_key_file(tmp_path: Path) -> None:
    home = tmp_path / "runtime"
    wallet = VoolWallet(runtime_home=home, derivation_key=b"w" * 32)
    with pytest.raises(WalletFault) as exc:
        wallet.generate_and_save()
    _assert_refusal(exc.value, code=LEGACY, surface="vool_wallet.generate_and_save")
    with pytest.raises(WalletFault):
        wallet.generate_and_save(overwrite=True)
    assert not wallet.exists()
    assert not home.exists(), "the refusal must not even create the runtime home"
    _no_key_file(tmp_path)


def test_load_refuses_even_when_a_stale_legacy_file_exists(tmp_path: Path) -> None:
    home = tmp_path / "runtime"
    stale = home / "data" / "keys" / WALLET_FILENAME
    stale.parent.mkdir(parents=True)
    stale.write_text('{"version": 1, "pubkey": "stale", "ciphertext_b64": "AAAA"}', encoding="utf-8")
    wallet = VoolWallet(runtime_home=home, derivation_key=b"w" * 32)
    assert wallet.exists() is True  # reports the old file; nothing reads or rewrites it
    with pytest.raises(WalletFault) as exc:
        wallet.load()
    _assert_refusal(exc.value, code=LEGACY, surface="vool_wallet.load")
    assert stale.read_text(encoding="utf-8") == '{"version": 1, "pubkey": "stale", "ciphertext_b64": "AAAA"}'
    with pytest.raises(RuntimeError):
        _ = wallet.pubkey  # no key was decrypted, so there is no public key to report


@pytest.mark.parametrize("method", ["sign", "sign_message", "sign_transaction"])
def test_every_signing_entry_point_refuses_without_producing_bytes(tmp_path: Path, method: str) -> None:
    wallet = VoolWallet(runtime_home=tmp_path / "runtime")
    produced: list[object] = []
    with pytest.raises(WalletFault) as exc:
        produced.append(getattr(wallet, method)(b"payload to sign"))
    _assert_refusal(exc.value, code=LEGACY, surface="vool_wallet.sign")
    assert produced == []
    _no_key_file(tmp_path)


def test_get_or_create_wallet_refuses_and_never_creates_on_read(tmp_path: Path) -> None:
    home = tmp_path / "runtime"
    for _ in range(2):  # a retry does not "eventually" create one either
        with pytest.raises(WalletFault) as exc:
            get_or_create_wallet(runtime_home=home)
        _assert_refusal(exc.value, code=LEGACY, surface="vool_wallet.get_or_create_wallet")
    with pytest.raises(WalletFault):
        get_or_create_wallet(runtime_home=home, derivation_key=b"k" * 32)
    with pytest.raises(WalletFault):
        get_or_create_wallet()  # default home: still nothing
    assert not home.exists()
    _no_key_file(tmp_path)
    assert len(_fault_receipts(LEGACY)) >= 4
    assert "SEC_WALLET_LEGACY_SURFACE_RETIRED" in _security_codes()


def test_export_doors_refuse_before_any_consent_prompt(tmp_path: Path) -> None:
    from core.os_consent_gate import set_consent_override_for_tests

    prompted: list[str] = []

    def _must_not_prompt(reason: str) -> bool:
        prompted.append(reason)
        raise AssertionError("an export refusal must happen before the OS consent prompt")

    set_consent_override_for_tests(_must_not_prompt)
    try:
        wallet = VoolWallet(runtime_home=tmp_path / "runtime")
        with pytest.raises(WalletFault) as exc:
            wallet.export_secret_key_base58()
        _assert_refusal(exc.value, code=EXPORT, surface="vool_wallet.export_secret_key_base58")
        with pytest.raises(WalletFault) as exc2:
            reveal_wallet_secret_key_base58(runtime_home=tmp_path / "runtime", reason="Back up wallet")
        _assert_refusal(exc2.value, code=EXPORT, surface="vool_wallet.reveal_wallet_secret_key_base58")
    finally:
        set_consent_override_for_tests(None)
    assert prompted == []
    assert "SEC_WALLET_EXPORT_REFUSED" in _security_codes()
    _no_key_file(tmp_path)


def test_refusal_context_carries_no_key_material_and_serializes() -> None:
    with pytest.raises(WalletFault) as exc:
        VoolWallet().export_secret_key_base58()
    payload = exc.value.to_dict()
    assert set(payload) == {"code", "fault_id", "user_message", "context"}
    assert payload["context"] == {"surface": "vool_wallet.export_secret_key_base58", "reason": "no_export_door"}
    # the message tells the user there is no export; it carries no material (no base58 run that could be a key)
    import re

    assert re.search(r"[1-9A-HJ-NP-Za-km-z]{40,}", payload["user_message"]) is None


def test_retired_wallet_reports_zero_balances_and_never_calls_rpc(tmp_path: Path) -> None:
    calls: list[str] = []

    def spying_rpc(method: str, params: list[object], **_kw: object) -> object:
        calls.append(method)
        return {"value": 1}

    wallet = VoolWallet(runtime_home=tmp_path / "runtime", rpc_call=spying_rpc)
    # A retired wallet answers a balance question with the typed refusal, never a quiet zero.
    for door in (wallet.get_sol_balance, wallet.get_usdc_balance):
        with pytest.raises(WalletFault) as info:
            door()
        assert info.value.code == "wallet_legacy_surface_retired"
    assert repr(wallet) == "VoolWallet(retired)"
    assert calls == []


def test_module_exports_no_signing_helper() -> None:
    for name in vool_wallet.__all__:
        assert hasattr(vool_wallet, name), name
    assert "export_safe" not in vool_wallet.__all__
    assert not hasattr(vool_wallet, "_encrypt_seed") and not hasattr(vool_wallet, "_decrypt_seed")
    assert not hasattr(vool_wallet, "derive_wallet_key")


# ---------------------------------------------------------------------------
# the read-only adapter over core.wallet
# ---------------------------------------------------------------------------

def test_legacy_view_when_wallet_is_disabled_is_empty_and_read_only(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("VOOL_WALLET_ENABLED", raising=False)
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    view = legacy_wallet_view(include_balances=True)
    assert view["authority"] == "core.wallet" and view["read_only"] is True
    assert view["pubkey"] == "" and view["custody_mode"] == "none"
    assert view["enabled"] is False and view["mainnet_enabled"] is False
    assert view["network"] == "solana-devnet"
    assert "sol_balance" not in view  # nothing to look up, so no lookup
    _no_key_file(tmp_path)


def test_legacy_view_reports_the_registered_core_wallet_without_minting(monkeypatch, tmp_path: Path, rpc) -> None:
    monkeypatch.setenv("VOOL_WALLET_ENABLED", "1")
    monkeypatch.setenv("VOOL_WALLET_NETWORK_ENVIRONMENT", "testnet")
    monkeypatch.setenv("VOOL_WALLET_TESTNET_RPC_URL", rpc.url)
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    from core.wallet import custody

    assert legacy_wallet_view(include_balances=False)["pubkey"] == "", "enabled but unregistered: still no key minted"
    _, pubkey = _keypair()
    profile = custody.create_watch_only_wallet(pubkey, label="ops")

    view = legacy_wallet_view(include_balances=True)
    assert view["pubkey"] == pubkey and view["wallet_id"] == profile.wallet_id
    assert view["custody_mode"] == "watch_only" and view["read_only"] is True
    assert view["sol_balance"] == pytest.approx(5.0)  # the scripted devnet RPC answers 5 SOL
    assert rpc.send_count() == 0
    assert VoolWallet(runtime_home=tmp_path).export_safe(include_balances=False)["pubkey"] == pubkey
    _no_key_file(tmp_path)
