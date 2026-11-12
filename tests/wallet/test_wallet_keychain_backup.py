"""A wallet's backup key in the computer's secure store, sealed with the person's backup password.

The store here is an in-memory keyring backend standing in for Keychain / Secret Service (the cloud has neither);
everything else is the real wallet: real keys, real seals, real recovery and import."""
from __future__ import annotations

import json
import uuid

import keyring
import pytest
from keyring.backend import KeyringBackend

from core.wallet.errors import WalletFault

pytestmark = [pytest.mark.safety]

SOLANA_MAINNET = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"
PIN = "482913"
NEW_PIN = "551177"
BACKUP_PASSWORD = "correct horse battery"


class MemoryStore(KeyringBackend):
    priority = 5

    def __init__(self):
        super().__init__()
        self.items: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):
        return self.items.get((service, username))

    def set_password(self, service, username, password):
        self.items[(service, username)] = password

    def delete_password(self, service, username):
        self.items.pop((service, username), None)


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("VOOL_WALLET_ENABLED", "1")
    for name in ("VOOL_WALLET_NETWORK_ENVIRONMENT", "VOOL_WALLET_RPC_URLS", "VOOL_WALLET_TESTNET_RPC_URL", "VOOL_WALLET_UI_CAPABILITY_SHA256"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    from core.blackbox import store as store_module
    from core.wallet import chains

    store_module.reset_default_store()
    chains.invalidate_chain_identity()
    previous = keyring.get_keyring()
    store = MemoryStore()
    keyring.set_keyring(store)
    yield store
    keyring.set_keyring(previous)
    chains.invalidate_chain_identity()
    store_module.reset_default_store()


def _create(*, pin: str = PIN) -> dict:
    from core.wallet import pilot_custody

    made = pilot_custody.create_pilot_wallet(network=SOLANA_MAINNET, method="pin", credential=pin, credential_confirmation=pin, creation_key=f"c-{uuid.uuid4().hex}")
    revealed = pilot_custody.reveal_pilot_backup(made["wallet_id"], credential=pin)
    pilot_custody.acknowledge_pilot_backup(made["wallet_id"], ack_token=revealed["ack_token"])
    return {**made, "backup_value": revealed["backup_value"]}


def test_the_saved_copy_holds_no_readable_key_and_opens_only_with_the_backup_password(home):
    from core.wallet import keychain_backup as kb

    made = _create()
    kb.save(made["wallet_id"], credential=PIN, password=BACKUP_PASSWORD, confirmation=BACKUP_PASSWORD)
    stored = home.items[(kb.SERVICE, f"{SOLANA_MAINNET}|{made['address']}")]
    assert made["backup_value"] not in stored and PIN not in stored and BACKUP_PASSWORD not in stored
    envelope = json.loads(stored)
    assert envelope["kdf"] == "scrypt" and envelope["n"] == 2**17
    assert kb.unseal(stored, password="wrong password!!", network=SOLANA_MAINNET, address=made["address"]) is None
    # a copy cannot be passed off as another wallet's
    assert kb.unseal(stored, password=BACKUP_PASSWORD, network=SOLANA_MAINNET, address="Vote111111111111111111111111111111111111111") is None
    assert kb.unseal(stored, password=BACKUP_PASSWORD, network=SOLANA_MAINNET, address=made["address"]) == made["backup_value"]
    from core.wallet import custody

    assert next(e for e in custody.list_wallets() if e["wallet_id"] == made["wallet_id"])["keychain_backup"] is True


def test_saving_needs_the_wallets_pin_and_a_strong_separate_password(home):
    from core.wallet import keychain_backup as kb

    made = _create()
    for credential, password, confirmation, reason in (
        (PIN, "short", "short", "backup_password_weak"),
        (PIN, "123456789012345", "123456789012345", "backup_password_weak"),
        (PIN, BACKUP_PASSWORD, BACKUP_PASSWORD + "x", "backup_password_mismatch"),
    ):
        with pytest.raises(WalletFault) as refused:
            kb.save(made["wallet_id"], credential=credential, password=password, confirmation=confirmation)
        assert refused.value.context["reason"] == reason
    with pytest.raises(WalletFault):
        kb.save(made["wallet_id"], credential="000000", password=BACKUP_PASSWORD, confirmation=BACKUP_PASSWORD)
    assert [k for k in home.items if k[1] != kb.INDEX_ACCOUNT] == []


def test_a_forgotten_pin_is_replaced_from_the_keychain_copy(home):
    from core.wallet import keychain_backup as kb
    from core.wallet import pilot_custody

    made = _create()
    kb.save(made["wallet_id"], credential=PIN, password=BACKUP_PASSWORD, confirmation=BACKUP_PASSWORD)
    with pytest.raises(WalletFault) as refused:
        kb.recover(made["wallet_id"], password="not the password", method="pin", credential=NEW_PIN, credential_confirmation=NEW_PIN)
    assert refused.value.context["reason"] == "backup_password_wrong"
    kb.recover(made["wallet_id"], password=BACKUP_PASSWORD, method="pin", credential=NEW_PIN, credential_confirmation=NEW_PIN)
    pilot_custody.prove_credential(made["wallet_id"], NEW_PIN)
    with pytest.raises(WalletFault):
        pilot_custody.prove_credential(made["wallet_id"], PIN)


def test_a_deleted_wallet_comes_back_from_the_keychain_and_never_takes_main(home):
    from core.wallet import custody, wallet_roles
    from core.wallet import keychain_backup as kb

    main = _create()
    spare = _create(pin=NEW_PIN)
    kb.save(spare["wallet_id"], credential=NEW_PIN, password=BACKUP_PASSWORD, confirmation=BACKUP_PASSWORD)
    wallet_roles.remove_wallet(spare["wallet_id"], credential=NEW_PIN, backup_confirmed=True)
    assert [r["address"] for r in kb.restorable()] == [spare["address"]]
    with pytest.raises(WalletFault):
        kb.restore(SOLANA_MAINNET, spare["address"], password="not the password", method="pin", credential=PIN, credential_confirmation=PIN)
    back = kb.restore(SOLANA_MAINNET, spare["address"], password=BACKUP_PASSWORD, method="pin", credential=PIN, credential_confirmation=PIN)
    assert back["address"] == spare["address"]
    assert kb.restorable() == []
    assert wallet_roles.main_wallet_id(SOLANA_MAINNET) == main["wallet_id"]
    assert custody.default_wallet().wallet_id == main["wallet_id"]


def test_without_a_secure_store_nothing_is_saved_and_the_page_is_told(home, monkeypatch):
    from core.wallet import keychain_backup as kb

    made = _create()
    monkeypatch.setattr(kb, "_backend", lambda: None)
    assert kb.available() is False and kb.restorable() == []
    with pytest.raises(WalletFault) as refused:
        kb.save(made["wallet_id"], credential=PIN, password=BACKUP_PASSWORD, confirmation=BACKUP_PASSWORD)
    assert refused.value.context["reason"] == "secure_store_unavailable"
