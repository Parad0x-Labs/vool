"""Private-key export exists, is device-authenticated only, and speaks the target wallet's format.

Operator decisions (2026-09-07): a pocket wallet's key can be exported for Phantom (the 64-byte Solana keypair as
base58) or MetaMask (the secp256k1 key as 0x-hex, derived from the same recovery seed at the standard Ethereum
path); the export is unlocked ONLY by device authentication (Touch ID or the Mac password releasing a Keychain
user-presence item) -- never by the PIN, never by anything else; every export is journaled without the key.

Sealing changed to make this possible: the sealed material is now the 64-byte BIP-39 seed (kind `bip39_seed_v2`)
so both keys derive on demand; a wallet sealed before this change (a bare Ed25519 secret) still signs and still
exports for Phantom, and says plainly that MetaMask export needs a restore from its phrase.
"""
from __future__ import annotations

import inspect
import json

import pytest

from tests.wallet._device_auth_fake import FakeDeviceAuthority

PIN = "482913"
VECTOR = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"
VECTOR_EVM_ADDRESS = "0x9858EfFD232B4033E47d90003D41EC34EcaEda94"
VECTOR_SOLANA_PUBKEY = "HAgk14JpMQLgt6rVgv7cBQFJWFto5Dqxi472uT3DKpqk"


@pytest.fixture
def fake_device(wallet_env):
    from core.wallet import device_auth

    fake = FakeDeviceAuthority()
    device_auth.set_device_authority_for_tests(fake)
    yield fake
    device_auth.set_device_authority_for_tests(None)


def _pocket(custody):
    return custody.create_pocket_wallet(acknowledged_warning=True, confirmation_phrase=custody.POCKET_CONFIRMATION_PHRASE, pin=PIN).profile


def _eth_account_present() -> bool:
    try:
        import eth_account
        return True
    except Exception:
        return False


def test_creation_seals_the_seed_and_binds_the_device_without_a_prompt(fake_device):
    from core.wallet import custody

    profile = _pocket(custody)
    assert custody.seal_kind(profile.wallet_id) == custody.SEAL_KIND_BIP39_SEED_V2
    assert custody.device_bound(profile.wallet_id) is True
    assert len(fake_device.stores) == 1 and fake_device.prompts == [], "storing the unlock secret never prompts; only reading does"


def test_export_for_phantom_is_the_wallets_own_keypair_and_needs_one_device_prompt(fake_device):
    from solders.keypair import Keypair

    from core.wallet import custody
    from core.wallet.external_signing import b58decode

    profile = _pocket(custody)
    result = custody.export_private_key(profile.wallet_id, target="phantom")
    assert result["target"] == "phantom" and result["format"] == "solana_keypair_base58" and result["shown_once"] is True
    assert len(fake_device.prompts) == 1 and "export" in fake_device.prompts[0].lower()
    raw = b58decode(result["value"])
    assert len(raw) == 64 and str(Keypair.from_bytes(raw).pubkey()) == profile.public_key == result["address"]
    assert result["import_steps"] and "Phantom" in " ".join(result["import_steps"])


def test_export_has_no_pin_parameter_and_the_pin_never_unlocks_it(fake_device):
    from core.wallet import custody

    assert "pin" not in inspect.signature(custody.export_private_key).parameters
    profile = _pocket(custody)
    fake_device.deny_next = True
    with pytest.raises(Exception) as exc:
        custody.export_private_key(profile.wallet_id, target="phantom")
    assert getattr(exc.value, "code", "") == "wallet_device_auth_denied"


def test_export_is_unavailable_without_device_authentication(wallet_env):
    from core.wallet import custody, device_auth

    fake = FakeDeviceAuthority(available=False)
    device_auth.set_device_authority_for_tests(fake)
    try:
        profile = _pocket(custody)  # creation still works; the wallet just is not device-bound
        assert custody.device_bound(profile.wallet_id) is False
        with pytest.raises(Exception) as exc:
            custody.export_private_key(profile.wallet_id, target="phantom")
        assert getattr(exc.value, "code", "") == "wallet_device_auth_unavailable"
    finally:
        device_auth.set_device_authority_for_tests(None)


def test_the_standard_vector_exports_the_known_addresses(fake_device):
    from core.wallet import custody

    profile = custody.restore_pocket_wallet(VECTOR, pin=PIN)
    assert profile.public_key == VECTOR_SOLANA_PUBKEY
    phantom = custody.export_private_key(profile.wallet_id, target="phantom")
    assert phantom["address"] == VECTOR_SOLANA_PUBKEY
    if not _eth_account_present():
        with pytest.raises(Exception) as exc:
            custody.export_private_key(profile.wallet_id, target="metamask")
        assert getattr(exc.value, "code", "") == "wallet_dependency_unavailable"
        pytest.skip("eth_account absent in this interpreter: the MetaMask branch is proven under the crypto lane's interpreter")
    metamask = custody.export_private_key(profile.wallet_id, target="metamask")
    assert metamask["format"] == "evm_private_key_hex" and metamask["value"].startswith("0x") and len(metamask["value"]) == 66
    assert metamask["address"] == VECTOR_EVM_ADDRESS
    assert "MetaMask" in " ".join(metamask["import_steps"])


def test_export_is_journaled_without_the_key(fake_device, monkeypatch):
    from core.wallet import custody, receipts

    seen: list[dict] = []
    monkeypatch.setattr(receipts, "journal_security_event", lambda kind, payload, *, source_context=None: seen.append({"kind": kind, **payload}))
    profile = _pocket(custody)
    result = custody.export_private_key(profile.wallet_id, target="phantom")
    assert seen and seen[-1]["kind"] == "wallet_private_key_exported" and seen[-1]["target"] == "phantom"
    assert result["value"] not in json.dumps(seen)


def test_a_legacy_sealed_wallet_still_signs_and_exports_for_phantom_but_not_metamask(fake_device):
    from core.wallet import custody

    profile = custody._create_legacy_pocket_for_tests(pin=PIN)
    assert custody.seal_kind(profile.wallet_id) == custody.SEAL_KIND_ED25519_SECRET_V1
    assert custody.verify_pin(profile.wallet_id, PIN)
    secret = custody._unseal_seed(profile.wallet_id, PIN)
    assert len(secret) == 32, "the signer still receives the 32-byte Ed25519 secret"
    phantom = custody.export_private_key(profile.wallet_id, target="phantom")
    assert phantom["address"] == profile.public_key
    with pytest.raises(Exception) as exc:
        custody.export_private_key(profile.wallet_id, target="metamask")
    assert getattr(exc.value, "code", "") == "wallet_export_unavailable"
    assert "restore" in str(exc.value.context.get("remediation", "")).lower()


def test_the_signer_still_signs_with_the_new_seal(fake_device):
    from core.wallet import custody, lifecycle, proposals

    profile = _pocket(custody)
    p = proposals.propose_transaction(wallet_id=profile.wallet_id, destination="9xQeWvG816bUx9EPjHmaT23yvVM2ZWbrrpZb9PusVFin", amount_minor=1, asset="SOL", origin="user")
    from core.wallet import approval

    engine = lifecycle.default_lifecycle()
    engine.prepare(p.proposal_id)
    receipt = engine.approve_and_execute(p.proposal_id, approver=approval.PinApprover(PIN))
    assert receipt.state == proposals.STATE_CONFIRMED


def test_the_route_refuses_a_pin_and_serves_the_export(fake_device):
    from apps.vool_api_server import create_app
    from core.wallet import custody
    from core.web.api.runtime import RuntimeServices
    from tests.wallet.test_wallet_api import _post

    app = create_app(RuntimeServices(display_name="VOOL"))
    profile = _pocket(custody)
    status, body = _post(app, "/api/wallet/export", {"wallet_id": profile.wallet_id, "target": "phantom", "pin": PIN})
    assert status == 400 and body["error"] == "wallet_export_pin_not_accepted", body
    status, body = _post(app, "/api/wallet/export", {"wallet_id": profile.wallet_id, "target": "phantom"})
    assert status == 200 and body["export"]["address"] == profile.public_key and body["export"]["shown_once"] is True
    status, body = _post(app, "/api/wallet/export", {"wallet_id": profile.wallet_id, "target": "ledger"})
    assert status == 400 and body["error"] == "wallet_export_unavailable"
