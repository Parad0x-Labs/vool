"""Custody law: default disabled, watch-only by default, external signing preferred, and a
sealed pocket wallet only behind a typed warning. The recovery phrase is shown once and
never lands anywhere else. Skills, plugins and models get a proposal API and nothing else.
"""
from __future__ import annotations

import inspect
import json
import logging

import pytest

from tests.wallet._rig import DESTINATION, DEVNET, phrase_leaked

pytestmark = [pytest.mark.safety]


def test_wallet_is_disabled_by_default_and_every_money_door_refuses(monkeypatch):
    monkeypatch.delenv("VOOL_WALLET_ENABLED", raising=False)
    from core.wallet import config, custody, proposals
    from core.wallet.errors import WalletFault

    assert config.wallet_enabled() is False
    with pytest.raises(WalletFault) as exc:
        custody.create_watch_only_wallet(DESTINATION)
    assert exc.value.code == "wallet_disabled"
    with pytest.raises(WalletFault) as exc2:
        proposals.propose_transaction(wallet_id="w", destination=DESTINATION, amount_minor=1, asset="SOL", origin="user")
    assert exc2.value.code == "wallet_disabled"


def test_watch_only_is_the_default_mode_and_cannot_sign(wallet_env):
    from core.wallet import custody, signers
    from core.wallet.errors import WalletFault

    profile = custody.create_watch_only_wallet(DESTINATION, label="cold")
    assert profile.mode == custody.MODE_WATCH_ONLY
    assert custody.default_wallet().wallet_id == profile.wallet_id
    with pytest.raises(WalletFault) as exc:
        signers.signer_for(profile)
    assert exc.value.code == "wallet_signing_unavailable"


def test_external_signer_is_preferred_and_a_forged_signature_is_refused(wallet_env):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from core.vool_wallet import b58encode
    from core.wallet import custody, signers
    from core.wallet.errors import WalletFault

    # The key lives OUTSIDE the process: only the public key is registered.
    external = Ed25519PrivateKey.generate()
    pubkey = b58encode(external.public_key().public_bytes_raw())
    profile = custody.register_external_signer_wallet(pubkey, network=DEVNET, label="hardware")
    assert profile.mode == custody.MODE_EXTERNAL_SIGNER
    assert custody.preferred_signing_mode() == custody.MODE_EXTERNAL_SIGNER

    seen: list[signers.SigningRequest] = []

    def provider(request: signers.SigningRequest) -> bytes:
        seen.append(request)
        return external.sign(request.message)

    signer = signers.signer_for(profile, external_signature_provider=provider)
    signature = signer.sign(b"payload")
    assert len(signature) == 64 and seen and seen[0].public_key == pubkey
    # The request handed to the outside world carries no key material of any kind.
    assert not any(k in json.dumps(seen[0].to_dict()).lower() for k in ("seed", "secret", "private", "phrase"))

    forged = signers.signer_for(profile, external_signature_provider=lambda _r: b"\x00" * 64)
    with pytest.raises(WalletFault) as exc:
        forged.sign(b"payload")
    assert exc.value.code == "wallet_signature_invalid"


def test_pocket_wallet_needs_the_typed_warning_and_the_exact_confirmation_phrase(wallet_env):
    from core.wallet import custody
    from core.wallet.errors import WalletFault

    with pytest.raises(WalletFault) as exc:
        custody.create_pocket_wallet(acknowledged_warning=False, confirmation_phrase=custody.POCKET_CONFIRMATION_PHRASE, pin="246810")
    assert exc.value.code == "wallet_confirmation_required"
    with pytest.raises(WalletFault) as exc2:
        custody.create_pocket_wallet(acknowledged_warning=True, confirmation_phrase="yes please", pin="246810")
    assert exc2.value.code == "wallet_confirmation_required"
    with pytest.raises(WalletFault) as exc3:
        custody.create_pocket_wallet(acknowledged_warning=True, confirmation_phrase=custody.POCKET_CONFIRMATION_PHRASE, pin="12")
    assert exc3.value.code == "wallet_pin_invalid"
    assert custody.list_wallets() == []
    assert "key" in custody.POCKET_WARNING_TEXT.lower() and "device" in custody.POCKET_WARNING_TEXT.lower()


def test_recovery_phrase_is_shown_once_and_never_persisted_logged_or_exported(wallet_env, caplog):
    from core.runtime_continuity import _conn
    from core.wallet import custody, receipts, status

    caplog.set_level(logging.DEBUG)
    created = custody.create_pocket_wallet(
        acknowledged_warning=True, confirmation_phrase=custody.POCKET_CONFIRMATION_PHRASE, pin="246810", network=DEVNET
    )
    phrase = created.recovery_phrase
    assert phrase and len(phrase.split()) >= 12
    profile = created.profile
    assert profile.mode == custody.MODE_POCKET_SEALED

    # shown once: the profile, the store, the status, the receipts and the logs never carry it
    assert "recovery_phrase" not in profile.to_dict()
    assert phrase not in json.dumps(profile.to_dict())
    assert phrase not in json.dumps(status.wallet_status())
    assert phrase not in json.dumps(custody.list_wallets(), default=str)
    conn = _conn()
    try:
        for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'wallet_%'"):
            rows = conn.execute(f"SELECT * FROM {name}").fetchall()
            dump = json.dumps(rows, default=str)
            assert phrase_leaked(phrase, dump) is None, f"recovery phrase leaked into {name}: {phrase_leaked(phrase, dump)}"
    finally:
        conn.close()
    assert phrase not in caplog.text
    assert phrase not in json.dumps(receipts.list_receipts(), default=str)
    # and it can be shown exactly once: asking again is refused, not repeated
    assert custody.reveal_recovery_phrase(profile.wallet_id) is None


def test_recovery_phrase_restores_the_same_public_key(wallet_env):
    from core.wallet import custody

    created = custody.create_pocket_wallet(acknowledged_warning=True, confirmation_phrase=custody.POCKET_CONFIRMATION_PHRASE, pin="246810")
    restored = custody.restore_pocket_wallet(created.recovery_phrase, pin="135791", label="restored")
    assert restored.public_key == created.profile.public_key
    assert restored.wallet_id != created.profile.wallet_id


def test_pocket_signing_needs_the_pin_and_the_seed_never_leaves_the_custody_module(wallet_env):
    from core.vool_wallet import verify_wallet_signature
    from core.wallet import custody, signers
    from core.wallet.errors import WalletFault

    created = custody.create_pocket_wallet(acknowledged_warning=True, confirmation_phrase=custody.POCKET_CONFIRMATION_PHRASE, pin="246810")
    profile = created.profile
    with pytest.raises(WalletFault) as exc:
        signers.signer_for(profile, pin="000000")
    assert exc.value.code == "wallet_pin_invalid"
    signer = signers.signer_for(profile, pin="246810")
    sig = signer.sign(b"hello")
    assert verify_wallet_signature(wallet_pubkey=profile.public_key, message=b"hello", signature=sig)
    # No public attribute on any signer exposes the seed or the private key object.
    for name in dir(signer):
        if name.startswith("_"):
            continue
        value = getattr(signer, name)
        assert not isinstance(value, bytes | bytearray) or len(value) != 32, f"signer exposes 32-byte material via {name}"
        assert "PrivateKey" not in type(value).__name__


def test_the_proposal_surface_models_and_skills_use_carries_no_signing_capability(wallet_env):
    """Skills, plugins and models get exactly propose + read. Every key-touching name lives in
    custody/signers and is never re-exported by the package or the proposal module."""
    from core import wallet
    from core.wallet import custody, proposals

    forbidden = {"unseal", "seed", "private", "sign", "recovery", "pin"}
    exported = {n.lower() for n in dir(proposals) if not n.startswith("_")}
    assert not any(any(f in n for f in forbidden) for n in exported if callable(getattr(proposals, n, None))), exported
    top = {n.lower() for n in dir(wallet) if not n.startswith("_")}
    assert not any(any(f in n for f in ("unseal", "seed", "private_key", "recovery")) for n in top), top

    custody.create_watch_only_wallet(DESTINATION)
    for origin in (proposals.ORIGIN_MODEL, proposals.ORIGIN_SKILL, proposals.ORIGIN_PLUGIN):
        proposal = proposals.propose_transaction(
            wallet_id=custody.default_wallet().wallet_id, destination=DESTINATION, amount_minor=10, asset="SOL", origin=origin
        )
        assert proposal.origin == origin and proposal.state == proposals.STATE_PROPOSED
        payload = json.dumps(proposal.to_dict()).lower()
        assert "seed" not in payload and "private" not in payload and "phrase" not in payload
    # a proposal can never carry an approval or a signature from its proposer
    sig = inspect.signature(proposals.propose_transaction)
    assert not {"approved", "signature", "pin", "signer"} & set(sig.parameters)


def test_wallet_tables_are_runtime_tables_so_the_suite_wipes_them():
    # every table the wallet store declares, not a hand-kept subset: a new wallet table can never escape the wipe
    from core.wallet import store
    from tests.conftest import RUNTIME_TABLES

    missing = [table for table in store.TABLES if table not in RUNTIME_TABLES]
    assert not missing, missing


def test_sqlite_module_is_the_only_persistence_and_the_seed_column_is_sealed(wallet_env):
    from core.runtime_continuity import _conn
    from core.wallet import custody

    created = custody.create_pocket_wallet(acknowledged_warning=True, confirmation_phrase=custody.POCKET_CONFIRMATION_PHRASE, pin="246810")
    conn = _conn()
    try:
        row = conn.execute("SELECT sealed_blob FROM wallet_profiles WHERE wallet_id=?", (created.profile.wallet_id,)).fetchone()
    finally:
        conn.close()
    assert row and row[0]
    blob = json.loads(row[0])
    assert set(blob) >= {"ciphertext", "nonce", "salt", "kdf"}
    assert len(blob["ciphertext"]) > 40 and blob["kdf"].startswith("pbkdf2-sha256")


def test_pocket_recovery_phrase_is_standard_bip39_and_phantom_derives_the_same_address(wallet_env):
    from core.wallet import custody, mnemonic

    created = custody.create_pocket_wallet(acknowledged_warning=True, confirmation_phrase=custody.POCKET_CONFIRMATION_PHRASE, pin="246810")
    words = created.recovery_phrase.split()
    assert len(words) == 12 and all(w in mnemonic.INDEX for w in words) and mnemonic.validate_mnemonic(created.recovery_phrase)
    # what a Phantom import of the same phrase yields (m/44'/501'/0'/0') is exactly this wallet's address
    assert mnemonic.address_for_mnemonic(created.recovery_phrase) == created.profile.public_key
    assert not hasattr(custody, "phrase_from_seed") and not hasattr(custody, "seed_from_phrase")
    import importlib.util

    assert importlib.util.find_spec("core.wallet.wordlist") is None, "the custom 33-word scheme is gone"
