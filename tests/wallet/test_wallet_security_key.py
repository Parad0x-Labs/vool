"""A security key (YubiKey and similar) as a second thing a wallet needs to open.

The key is a software FIDO2 authenticator (tests/wallet/_soft_fido2.py) speaking real CTAP2 with the hmac-secret
extension, driven by VOOL's real fido2 client code; only the USB wire is skipped. Everything else is the real wallet:
real keys, real seals, real signing, recovery and backups."""
from __future__ import annotations

import uuid

import pytest

from core.wallet.errors import WalletFault
from tests.wallet._soft_fido2 import SoftKey

pytestmark = [pytest.mark.safety]

SOLANA_MAINNET = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"
PIN = "482913"
NEW_PIN = "551177"


@pytest.fixture
def keys(monkeypatch, tmp_path):
    monkeypatch.setenv("VOOL_WALLET_ENABLED", "1")
    for name in ("VOOL_WALLET_NETWORK_ENVIRONMENT", "VOOL_WALLET_RPC_URLS", "VOOL_WALLET_TESTNET_RPC_URL", "VOOL_WALLET_UI_CAPABILITY_SHA256"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    from core.blackbox import store as store_module
    from core.wallet import chains, security_key

    store_module.reset_default_store()
    chains.invalidate_chain_identity()
    plugged = {"keys": [SoftKey(name="YubiKey 5C NFC")]}
    security_key.set_device_source_for_tests(lambda: list(plugged["keys"]))
    yield plugged
    security_key.set_device_source_for_tests(None)
    chains.invalidate_chain_identity()
    store_module.reset_default_store()


def _create(*, pin: str = PIN) -> dict:
    from core.wallet import pilot_custody

    made = pilot_custody.create_pilot_wallet(network=SOLANA_MAINNET, method="pin", credential=pin, credential_confirmation=pin, creation_key=f"c-{uuid.uuid4().hex}")
    revealed = pilot_custody.reveal_pilot_backup(made["wallet_id"], credential=pin)
    pilot_custody.acknowledge_pilot_backup(made["wallet_id"], ack_token=revealed["ack_token"])
    return {**made, "backup_value": revealed["backup_value"]}


def _reason(call) -> tuple[str, str]:
    with pytest.raises(WalletFault) as refused:
        call()
    return refused.value.code, str(refused.value.context.get("reason") or "")


def test_after_adding_a_key_the_wallet_opens_only_with_the_pin_and_that_key(keys):
    from core.wallet import custody, pilot_custody

    made = _create()
    key = keys["keys"][0]
    view = pilot_custody.add_security_key(made["wallet_id"], credential=PIN)
    assert view["security_key"]["name"] == "YubiKey 5C NFC" and key.touches == 2
    listed = next(e for e in custody.list_wallets() if e["wallet_id"] == made["wallet_id"])
    assert listed["security_key"]["name"] == "YubiKey 5C NFC" and "credential_id" not in listed["security_key"] and "salt" not in listed["security_key"]
    pilot_custody.prove_credential(made["wallet_id"], PIN)
    assert key.touches == 3
    # the stored seal does not open with the PIN alone, even inside VOOL's own code
    row = pilot_custody._load(made["wallet_id"])
    assert _reason(lambda: pilot_custody._open_seal(row, PIN, None, source_context=None))[0] == "wallet_pin_invalid"
    # unplugged: refused in its own words, and it is not a wrong PIN
    keys["keys"] = []
    assert _reason(lambda: pilot_custody.prove_credential(made["wallet_id"], PIN)) == ("wallet_security_key_needed", "no_security_key")
    # another key of the same model answers nothing for this wallet
    keys["keys"] = [SoftKey(name="YubiKey 5C NFC")]
    assert _reason(lambda: pilot_custody.prove_credential(made["wallet_id"], PIN)) == ("wallet_security_key_needed", "not_this_key")
    # the right key with a wrong PIN is still a wrong PIN
    keys["keys"] = [key]
    assert _reason(lambda: pilot_custody.prove_credential(made["wallet_id"], "000000"))[0] == "wallet_pin_invalid"
    # two keys plugged in: VOOL finds the one that belongs to this wallet
    keys["keys"] = [SoftKey(), key]
    pilot_custody.prove_credential(made["wallet_id"], PIN)


def test_a_key_left_untouched_changes_nothing(keys):
    from core.wallet import pilot_custody

    made = _create()
    pilot_custody.add_security_key(made["wallet_id"], credential=PIN)
    keys["keys"][0].touch = False
    assert _reason(lambda: pilot_custody.prove_credential(made["wallet_id"], PIN)) == ("wallet_security_key_needed", "touch_timeout")
    keys["keys"][0].touch = True
    pilot_custody.prove_credential(made["wallet_id"], PIN)


def test_payments_sign_with_the_key_and_never_without_it(keys):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    from core.vool_wallet import decode_solana_pubkey
    from core.wallet import pilot_custody

    made = _create()
    pilot_custody.add_security_key(made["wallet_id"], credential=PIN)
    with pilot_custody.signing_session(made["wallet_id"], PIN) as signer:
        signature = signer.sign_svm(b"a transfer message")
    Ed25519PublicKey.from_public_bytes(decode_solana_pubkey(made["address"])).verify(signature, b"a transfer message")
    keys["keys"] = []
    with pytest.raises(WalletFault) as refused, pilot_custody.signing_session(made["wallet_id"], PIN):
        pass
    assert refused.value.code == "wallet_security_key_needed"


def test_a_missing_key_does_not_count_toward_the_wrong_pin_lock(keys):
    from core.wallet import pilot_custody

    made = _create()
    key = keys["keys"][0]
    pilot_custody.add_security_key(made["wallet_id"], credential=PIN)
    keys["keys"] = []
    for _ in range(pilot_custody.WALLET_LOCK_AFTER + 2):
        assert _reason(lambda: pilot_custody.prove_credential(made["wallet_id"], PIN))[0] == "wallet_security_key_needed"
    keys["keys"] = [key]
    pilot_custody.prove_credential(made["wallet_id"], PIN)


def test_a_lost_key_is_replaced_by_the_backup_key_which_removes_it(keys):
    from core.wallet import pilot_custody

    made = _create()
    pilot_custody.add_security_key(made["wallet_id"], credential=PIN)
    keys["keys"] = []  # the key is gone for good
    pilot_custody.recover_with_backup(made["wallet_id"], backup_value=made["backup_value"], method="pin", credential=NEW_PIN, credential_confirmation=NEW_PIN)
    assert pilot_custody.security_key_of(made["wallet_id"]) is None
    pilot_custody.prove_credential(made["wallet_id"], NEW_PIN)


def test_a_usb_backup_opened_for_a_forgotten_pin_also_frees_a_lost_key(keys, monkeypatch, tmp_path):
    from core.wallet import pilot_custody, usb_backup

    mount = tmp_path / "STICK"
    mount.mkdir()
    monkeypatch.setattr(usb_backup, "_mounts", lambda: [(mount, "STICK")])
    made = _create()
    pilot_custody.add_security_key(made["wallet_id"], credential=PIN)
    # saving a backup opens the wallet, so it needs the key too
    keys_saved = list(keys["keys"])
    keys["keys"] = []
    drive_id = usb_backup.drives()[0]["drive_id"]
    assert _reason(lambda: usb_backup.save(made["wallet_id"], drive_id=drive_id, credential=PIN, password="correct horse battery",
                                           confirmation="correct horse battery"))[0] == "wallet_security_key_needed"
    keys["keys"] = keys_saved
    usb_backup.save(made["wallet_id"], drive_id=drive_id, credential=PIN, password="correct horse battery", confirmation="correct horse battery")
    keys["keys"] = []
    (found,) = usb_backup.found()
    usb_backup.open_backup(found["file_id"], password="correct horse battery", method="pin", credential=NEW_PIN, credential_confirmation=NEW_PIN)
    assert pilot_custody.security_key_of(made["wallet_id"]) is None
    pilot_custody.prove_credential(made["wallet_id"], NEW_PIN)


def test_changing_the_pin_keeps_the_key(keys):
    from core.wallet import pilot_custody

    made = _create()
    pilot_custody.add_security_key(made["wallet_id"], credential=PIN)
    pilot_custody.change_pilot_credential(made["wallet_id"], current_credential=PIN, method="pin", credential=NEW_PIN, credential_confirmation=NEW_PIN)
    assert pilot_custody.security_key_of(made["wallet_id"]) is not None
    pilot_custody.prove_credential(made["wallet_id"], NEW_PIN)
    keys["keys"] = []
    assert _reason(lambda: pilot_custody.prove_credential(made["wallet_id"], NEW_PIN))[0] == "wallet_security_key_needed"


def test_removing_the_key_needs_the_key_and_brings_back_pin_only(keys):
    from core.wallet import pilot_custody

    made = _create()
    key = keys["keys"][0]
    pilot_custody.add_security_key(made["wallet_id"], credential=PIN)
    keys["keys"] = []
    assert _reason(lambda: pilot_custody.remove_security_key(made["wallet_id"], credential=PIN))[0] == "wallet_security_key_needed"
    keys["keys"] = [key]
    assert _reason(lambda: pilot_custody.remove_security_key(made["wallet_id"], credential="000000"))[0] == "wallet_pin_invalid"
    pilot_custody.remove_security_key(made["wallet_id"], credential=PIN)
    assert pilot_custody.security_key_of(made["wallet_id"]) is None
    keys["keys"] = []
    pilot_custody.prove_credential(made["wallet_id"], PIN)
    assert _reason(lambda: pilot_custody.remove_security_key(made["wallet_id"], credential=PIN)) == ("wallet_setup_state_invalid", "no_security_key_added")


@pytest.mark.parametrize(("plugged", "reason"), [
    (lambda: [], "no_security_key"),
    (lambda: [SoftKey(), SoftKey()], "more_than_one_key"),
    (lambda: [SoftKey(supports_hmac_secret=False)], "key_unsupported"),
    (lambda: [SoftKey(client_pin_set=True, make_cred_uv_not_required=False)], "key_needs_pin"),
    (lambda: [SoftKey(touch=False)], "touch_timeout"),
])
def test_adding_a_key_that_cannot_answer_changes_nothing(keys, plugged, reason):
    from core.wallet import pilot_custody

    made = _create()
    generation = pilot_custody.credential_generation(made["wallet_id"])
    keys["keys"] = plugged()
    assert _reason(lambda: pilot_custody.add_security_key(made["wallet_id"], credential=PIN)) == ("wallet_security_key_needed", reason)
    assert pilot_custody.security_key_of(made["wallet_id"]) is None
    assert pilot_custody.credential_generation(made["wallet_id"]) == generation
    keys["keys"] = []
    pilot_custody.prove_credential(made["wallet_id"], PIN)  # still opens with the PIN alone


def test_adding_needs_the_wallets_pin_and_happens_once(keys):
    from core.wallet import pilot_custody

    made = _create()
    assert _reason(lambda: pilot_custody.add_security_key(made["wallet_id"], credential="000000"))[0] == "wallet_pin_invalid"
    assert keys["keys"][0].touches == 0  # a wrong PIN never reaches the key
    pilot_custody.add_security_key(made["wallet_id"], credential=PIN)
    assert _reason(lambda: pilot_custody.add_security_key(made["wallet_id"], credential=PIN)) == ("wallet_setup_state_invalid", "security_key_already_added")


def test_deleting_a_wallet_forgets_its_key_and_a_wallet_brought_back_does_not_need_it(keys):
    import keyring
    from keyring.backend import KeyringBackend

    from core.wallet import keychain_backup, pilot_custody, wallet_roles

    class _Memory(KeyringBackend):
        priority = 5
        items: dict = {}

        def get_password(self, service, username):
            return self.items.get((service, username))

        def set_password(self, service, username, password):
            self.items[(service, username)] = password

        def delete_password(self, service, username):
            self.items.pop((service, username), None)

    previous = keyring.get_keyring()
    keyring.set_keyring(_Memory())
    try:
        _create()
        spare = _create(pin=NEW_PIN)
        pilot_custody.add_security_key(spare["wallet_id"], credential=NEW_PIN)
        keychain_backup.save(spare["wallet_id"], credential=NEW_PIN, password="correct horse battery", confirmation="correct horse battery")
        wallet_roles.remove_wallet(spare["wallet_id"], credential=NEW_PIN, backup_confirmed=True)
        assert pilot_custody.security_key_of(spare["wallet_id"]) is None
        keys["keys"] = []
        back = keychain_backup.restore(SOLANA_MAINNET, spare["address"], password="correct horse battery", method="pin", credential=PIN, credential_confirmation=PIN)
        pilot_custody.prove_credential(back["wallet_id"], PIN)
    finally:
        keyring.set_keyring(previous)


def test_an_agent_wallet_takes_no_security_key(keys, monkeypatch):
    from core.wallet import custody, pilot_custody

    made = _create()
    monkeypatch.setattr(custody, "agent_of", lambda wallet_id: "assistant" if wallet_id == made["wallet_id"] else "")
    assert _reason(lambda: pilot_custody.add_security_key(made["wallet_id"], credential=PIN)) == ("wallet_setup_state_invalid", "agent_wallet_credential_is_fixed")


# --- from the security review of this piece ----------------------------------------------------------------------

def _database_files() -> tuple[bytes, bytes]:
    import os

    from core import runtime_continuity

    path = str(runtime_continuity._runtime_db_path())
    main = open(path, "rb").read()
    wal = open(path + "-wal", "rb").read() if os.path.exists(path + "-wal") else b""
    return main, wal


def test_the_pin_only_seal_is_gone_from_the_database_files_while_the_app_keeps_them_open(keys):
    import json

    from core import runtime_continuity
    from core.wallet import pilot_custody
    from storage.db import get_connection

    made = _create()
    old = json.loads(pilot_custody._load(made["wallet_id"])["sealed_blob"])["ciphertext"].encode()
    keeper = get_connection(runtime_continuity._runtime_db_path())  # the running app holds the database open
    keeper.execute("SELECT count(*) FROM wallet_profiles").fetchone()
    try:
        pilot_custody.add_security_key(made["wallet_id"], credential=PIN)
        main, wal = _database_files()
        assert old not in main and old not in wal, "a copy of VOOL's files and the PIN must not open the wallet after the key is added"
    finally:
        keeper.close()


def test_a_key_problem_answers_the_same_for_the_right_and_a_wrong_pin(keys):
    from core.wallet import pilot_custody

    made = _create()
    key = keys["keys"][0]
    pilot_custody.add_security_key(made["wallet_id"], credential=PIN)
    key.touch = False
    right = _reason(lambda: pilot_custody.prove_credential(made["wallet_id"], PIN))
    wrong = _reason(lambda: pilot_custody.prove_credential(made["wallet_id"], "000000"))
    assert right == wrong == ("wallet_security_key_needed", "touch_timeout")
    key.touch = True
    pilot_custody.prove_credential(made["wallet_id"], PIN)


def test_a_backup_recovery_says_it_removed_the_security_key(keys, monkeypatch):
    from core.wallet import pilot_custody, receipts

    seen: list[tuple[str, dict]] = []
    real = receipts.journal_security_event
    monkeypatch.setattr(receipts, "journal_security_event", lambda kind, payload, **kw: (seen.append((kind, dict(payload))), real(kind, payload, **kw))[1])
    made = _create()
    pilot_custody.add_security_key(made["wallet_id"], credential=PIN)
    keys["keys"] = []
    view = pilot_custody.recover_with_backup(made["wallet_id"], backup_value=made["backup_value"], method="pin", credential=NEW_PIN, credential_confirmation=NEW_PIN)
    assert view["security_key_removed"] is True and pilot_custody.SECURITY_KEY_DROPPED_NOTE in view["note"]
    journaled = [payload for kind, payload in seen if kind == "wallet_pilot_recovered_with_backup"]
    assert journaled and journaled[-1]["security_key_removed"] is True and journaled[-1]["old_seal_cleared"] is True
    again = pilot_custody.change_pilot_credential(made["wallet_id"], current_credential=NEW_PIN, method="pin", credential=PIN, credential_confirmation=PIN)
    assert again["security_key_removed"] is False


def _touch_id_wallet():
    from core.wallet import device_auth, pilot_custody
    from tests.wallet._device_auth_fake import FakeDeviceAuthority

    device_auth.set_device_authority_for_tests(FakeDeviceAuthority())
    made = pilot_custody.create_pilot_wallet(network=SOLANA_MAINNET, method="device", creation_key=f"c-{uuid.uuid4().hex}")
    revealed = pilot_custody.reveal_pilot_backup(made["wallet_id"])
    pilot_custody.acknowledge_pilot_backup(made["wallet_id"], ack_token=revealed["ack_token"])
    return made


def test_a_touch_id_wallet_takes_a_key_keeps_it_through_touch_id_recovery_and_lets_it_go(keys):
    from core.wallet import device_auth, pilot_custody

    try:
        made = _touch_id_wallet()
        key = keys["keys"][0]
        pilot_custody.add_security_key(made["wallet_id"])
        pilot_custody.prove_credential(made["wallet_id"], "")
        with pytest.raises(WalletFault):
            pilot_custody._open_seal(pilot_custody._load(made["wallet_id"]), "", None, source_context=None)  # Touch ID alone opens nothing
        keys["keys"] = []
        assert _reason(lambda: pilot_custody.prove_credential(made["wallet_id"], ""))[0] == "wallet_security_key_needed"
        keys["keys"] = [key]
        options = pilot_custody.recovery_options(made["wallet_id"])
        pilot_custody.recover_with_device(made["wallet_id"], challenge=options["device_recovery"]["challenge"], method="device")
        assert pilot_custody.security_key_of(made["wallet_id"]) is not None
        pilot_custody.remove_security_key(made["wallet_id"])
        keys["keys"] = []
        pilot_custody.prove_credential(made["wallet_id"], "")
    finally:
        device_auth.set_device_authority_for_tests(None)


def test_a_second_add_while_one_waits_for_a_touch_is_refused_and_touch_id_still_opens(keys, monkeypatch):
    from core.wallet import device_auth, pilot_custody, security_key

    try:
        made = _touch_id_wallet()
        real_enroll = security_key.enroll
        seen: dict[str, tuple[str, str]] = {}

        def enroll(**kw):
            if "other" not in seen:  # the other window's add lands while this one waits for a touch
                seen["other"] = _reason(lambda: pilot_custody.add_security_key(made["wallet_id"]))
            return real_enroll(**kw)

        monkeypatch.setattr(security_key, "enroll", enroll)
        pilot_custody.add_security_key(made["wallet_id"])
        assert seen["other"] == ("wallet_setup_state_invalid", "security_key_change_in_progress")
        pilot_custody.prove_credential(made["wallet_id"], "")  # Touch ID and the key still open it
    finally:
        device_auth.set_device_authority_for_tests(None)
