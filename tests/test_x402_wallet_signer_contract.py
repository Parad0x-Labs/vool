"""The VoolWallet <-> x402 signer contract is retired; this module pins the refusal.

Before the money-authority retirement these tests created the installer wallet with
``get_or_create_wallet`` and proved ``wallet_signer`` produced signatures verifying under its
pubkey. Both halves are retired surfaces: no legacy key is ever created, and no wallet can be
wrapped into a payment signer. Every test drives the same entry points and proves the typed,
receipt-backed refusal -- with no key file on disk and no signature produced. The verify-only
helper (``verify_wallet_signature``) carries no money authority and stays.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.vool_wallet import (
    VoolWallet,
    b58encode,
    get_or_create_wallet,
    reveal_wallet_secret_key_base58,
    verify_wallet_signature,
)
from core.wallet.authority import EXPORT_REFUSED, LEGACY_RETIRED
from core.wallet.errors import WalletFault
from core.x402.client import wallet_signer


class _NeverSigns:
    def __init__(self) -> None:
        self.touched = 0

    def pubkey(self):
        self.touched += 1
        raise AssertionError("pubkey read")

    def sign(self, *_args):
        self.touched += 1
        raise AssertionError("sign called")

    sign_message = sign


def _assert_refusal(exc: WalletFault, surface: str, *, code: str = LEGACY_RETIRED) -> None:
    from core.faults.recorder import fault_by_id
    from core.security_events.catalog import SEC_WALLET_EXPORT_REFUSED, SEC_WALLET_LEGACY_SURFACE_RETIRED
    from core.security_events.store import list_security_events

    assert exc.code == code
    assert exc.fault_id.startswith("fault-"), exc.to_dict()
    assert exc.user_message
    assert exc.context["surface"] == surface
    record = fault_by_id(exc.fault_id)
    assert record is not None and record.code == code and record.context.get("surface") == surface
    expected_sec = SEC_WALLET_EXPORT_REFUSED if code == EXPORT_REFUSED else SEC_WALLET_LEGACY_SURFACE_RETIRED
    observed = [e for e in list_security_events(limit=50) if e.fault_id == exc.fault_id]
    assert observed and observed[0].sec_code == expected_sec


def _nothing_on_disk(root: Path) -> None:
    assert not root.exists() or list(root.rglob("*")) == [], list(root.rglob("*"))


def test_get_or_create_wallet_refuses_and_mints_no_key_file(tmp_path: Path) -> None:
    home = tmp_path / "runtime"
    with pytest.raises(WalletFault) as info:
        get_or_create_wallet(runtime_home=str(home))
    _assert_refusal(info.value, "vool_wallet.get_or_create_wallet")
    _nothing_on_disk(home)
    assert not list(tmp_path.rglob("*.enc"))


def test_wallet_signer_refuses_before_reading_or_signing(tmp_path: Path) -> None:
    wallet = _NeverSigns()
    with pytest.raises(WalletFault) as info:
        wallet_signer(wallet)
    _assert_refusal(info.value, "x402.client.wallet_signer")
    assert wallet.touched == 0

    # The retired class itself (never loaded) is refused the same way, and creates nothing.
    home = tmp_path / "runtime"
    with pytest.raises(WalletFault) as legacy:
        wallet_signer(VoolWallet(runtime_home=home))
    _assert_refusal(legacy.value, "x402.client.wallet_signer")
    _nothing_on_disk(home)


def test_legacy_wallet_key_and_signing_doors_refuse(tmp_path: Path) -> None:
    home = tmp_path / "runtime"
    wallet = VoolWallet(runtime_home=home)
    for call, surface in (
        (wallet.generate_and_save, "vool_wallet.generate_and_save"),
        (wallet.load, "vool_wallet.load"),
        (lambda: wallet.sign(b"x402-exact-payment-message-v0"), "vool_wallet.sign"),
        (lambda: wallet.sign_message("x402-exact-payment-message-v0"), "vool_wallet.sign"),
        (lambda: wallet.sign_transaction(b"\x00" * 64), "vool_wallet.sign"),
    ):
        with pytest.raises(WalletFault) as info:
            call()
        _assert_refusal(info.value, surface)
    assert wallet.exists() is False
    with pytest.raises(RuntimeError):
        _ = wallet.pubkey  # never loaded: there is no pubkey to sign under
    _nothing_on_disk(home)


def test_private_key_export_doors_refuse_with_the_export_code(tmp_path: Path) -> None:
    home = tmp_path / "runtime"
    with pytest.raises(WalletFault) as direct:
        VoolWallet(runtime_home=home).export_secret_key_base58()
    _assert_refusal(direct.value, "vool_wallet.export_secret_key_base58", code=EXPORT_REFUSED)

    with pytest.raises(WalletFault) as reveal:
        reveal_wallet_secret_key_base58(runtime_home=str(home), reason="phantom import")
    _assert_refusal(reveal.value, "vool_wallet.reveal_wallet_secret_key_base58", code=EXPORT_REFUSED)
    _nothing_on_disk(home)


def test_verify_only_helper_still_verifies_and_rejects_tampering() -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    # An ephemeral test key: the helper only ever sees the PUBLIC key, and signs nothing.
    key = Ed25519PrivateKey.generate()
    pubkey = b58encode(key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))
    message = b"x402-exact-payment-message-v0"
    signature = key.sign(message)

    assert verify_wallet_signature(wallet_pubkey=pubkey, message=message, signature=signature) is True
    assert verify_wallet_signature(wallet_pubkey=pubkey, message=message + b"!", signature=signature) is False
    assert verify_wallet_signature(wallet_pubkey=pubkey, message=message, signature=bytes(64)) is False
