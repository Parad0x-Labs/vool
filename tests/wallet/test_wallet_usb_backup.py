"""A wallet's backup key on a USB stick, sealed with the person's backup password.

The stick here is a folder standing in for a mounted removable drive (the cloud has no USB); the file is really
written, flushed, read back and opened. Everything else is the real wallet: real keys, seals, recovery and import."""
from __future__ import annotations

import json
import plistlib
import subprocess
import uuid
from pathlib import Path

import pytest

from core.wallet.errors import WalletFault

pytestmark = [pytest.mark.safety]

SOLANA_MAINNET = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"
PIN = "482913"
NEW_PIN = "551177"
BACKUP_PASSWORD = "correct horse battery"


@pytest.fixture
def stick(monkeypatch, tmp_path):
    monkeypatch.setenv("VOOL_WALLET_ENABLED", "1")
    for name in ("VOOL_WALLET_NETWORK_ENVIRONMENT", "VOOL_WALLET_RPC_URLS", "VOOL_WALLET_TESTNET_RPC_URL", "VOOL_WALLET_UI_CAPABILITY_SHA256"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    from core.blackbox import store as store_module
    from core.wallet import chains, usb_backup

    store_module.reset_default_store()
    chains.invalidate_chain_identity()
    mount = tmp_path / "media" / "SANDISK"
    mount.mkdir(parents=True)
    plugged = {"mounts": [(mount, "SANDISK")]}
    monkeypatch.setattr(usb_backup, "_mounts", lambda: list(plugged["mounts"]))
    yield {"mount": mount, "plugged": plugged}
    chains.invalidate_chain_identity()
    store_module.reset_default_store()


def _create(*, pin: str = PIN) -> dict:
    from core.wallet import pilot_custody

    made = pilot_custody.create_pilot_wallet(network=SOLANA_MAINNET, method="pin", credential=pin, credential_confirmation=pin, creation_key=f"c-{uuid.uuid4().hex}")
    revealed = pilot_custody.reveal_pilot_backup(made["wallet_id"], credential=pin)
    pilot_custody.acknowledge_pilot_backup(made["wallet_id"], ack_token=revealed["ack_token"])
    return {**made, "backup_value": revealed["backup_value"]}


def _drive_id() -> str:
    from core.wallet import usb_backup

    (drive,) = usb_backup.drives()
    return drive["drive_id"]


def _files(mount: Path) -> list[Path]:
    from core.wallet import usb_backup

    return sorted((mount / usb_backup.FOLDER).glob(f"*{usb_backup.SUFFIX}"))


def test_the_file_on_the_stick_holds_no_readable_key_and_opens_only_with_the_backup_password(stick):
    from core.wallet import custody, keychain_backup, usb_backup

    made = _create()
    drive = usb_backup.drives()[0]
    assert drive["name"] == "SANDISK" and str(stick["mount"]) not in json.dumps(drive)  # the page never sees a path
    usb_backup.save(made["wallet_id"], drive_id=drive["drive_id"], credential=PIN, password=BACKUP_PASSWORD, confirmation=BACKUP_PASSWORD)
    (written,) = _files(stick["mount"])
    text = written.read_text()
    assert made["backup_value"] not in text and PIN not in text and BACKUP_PASSWORD not in text
    sealed = json.dumps(json.loads(text)["sealed"], sort_keys=True)
    assert json.loads(sealed)["kdf"] == "scrypt" and json.loads(sealed)["n"] == 2**17
    assert keychain_backup.unseal(sealed, password="wrong password!!", network=SOLANA_MAINNET, address=made["address"]) is None
    assert keychain_backup.unseal(sealed, password=BACKUP_PASSWORD, network=SOLANA_MAINNET, address="Vote111111111111111111111111111111111111111") is None
    assert keychain_backup.unseal(sealed, password=BACKUP_PASSWORD, network=SOLANA_MAINNET, address=made["address"]) == made["backup_value"]
    assert not [p for p in (stick["mount"] / usb_backup.FOLDER).iterdir() if p.name.endswith(".tmp")]
    listed = next(e for e in custody.list_wallets() if e["wallet_id"] == made["wallet_id"])
    assert listed["usb_backup"]["drive"] == "SANDISK"
    (found,) = usb_backup.found()
    assert found["state"] == "on_this_computer" and found["wallet_id"] == made["wallet_id"] and str(stick["mount"]) not in json.dumps(found)


def test_saving_needs_the_wallets_pin_a_strong_separate_password_and_a_plugged_stick(stick):
    from core.wallet import usb_backup

    made = _create()
    drive_id = _drive_id()
    for credential, password, confirmation, reason in (
        (PIN, "short", "short", "backup_password_weak"),
        (PIN, BACKUP_PASSWORD, BACKUP_PASSWORD + "x", "backup_password_mismatch"),
        (PIN, PIN * 3, PIN * 3, "backup_password_weak"),
    ):
        with pytest.raises(WalletFault) as refused:
            usb_backup.save(made["wallet_id"], drive_id=drive_id, credential=credential, password=password, confirmation=confirmation)
        assert refused.value.context["reason"] == reason
    with pytest.raises(WalletFault) as wrong_pin:
        usb_backup.save(made["wallet_id"], drive_id=drive_id, credential="000000", password=BACKUP_PASSWORD, confirmation=BACKUP_PASSWORD)
    assert wrong_pin.value.code == "wallet_pin_invalid"
    # a made-up drive id, or a path, is never a place VOOL writes to
    for bogus in ("usb-0000000000000000", str(stick["mount"]), "/tmp"):
        with pytest.raises(WalletFault) as refused:
            usb_backup.save(made["wallet_id"], drive_id=bogus, credential=PIN, password=BACKUP_PASSWORD, confirmation=BACKUP_PASSWORD)
        assert refused.value.context["reason"] == "usb_drive_not_found"
    stick["plugged"]["mounts"] = []
    with pytest.raises(WalletFault) as unplugged:
        usb_backup.save(made["wallet_id"], drive_id=drive_id, credential=PIN, password=BACKUP_PASSWORD, confirmation=BACKUP_PASSWORD)
    assert unplugged.value.context["reason"] == "usb_drive_not_found"
    assert _files(stick["mount"]) == []


def test_a_forgotten_pin_is_replaced_from_the_stick_and_wrong_passwords_count(stick):
    from core.wallet import pilot_custody, usb_backup

    made = _create()
    usb_backup.save(made["wallet_id"], drive_id=_drive_id(), credential=PIN, password=BACKUP_PASSWORD, confirmation=BACKUP_PASSWORD)
    (found,) = usb_backup.found()
    with pytest.raises(WalletFault) as refused:
        usb_backup.open_backup(found["file_id"], password="not the password", method="pin", credential=NEW_PIN, credential_confirmation=NEW_PIN)
    assert refused.value.context["reason"] == "backup_password_wrong"
    opened = usb_backup.open_backup(found["file_id"], password=BACKUP_PASSWORD, method="pin", credential=NEW_PIN, credential_confirmation=NEW_PIN)
    assert opened["opened"] == "new_pin" and opened["wallet_id"] == made["wallet_id"]
    pilot_custody.prove_credential(made["wallet_id"], NEW_PIN)
    with pytest.raises(WalletFault):
        pilot_custody.prove_credential(made["wallet_id"], PIN)


def test_guessing_the_backup_password_locks_after_five_tries(stick):
    from core.wallet import usb_backup

    made = _create()
    usb_backup.save(made["wallet_id"], drive_id=_drive_id(), credential=PIN, password=BACKUP_PASSWORD, confirmation=BACKUP_PASSWORD)
    (found,) = usb_backup.found()
    codes = []
    for attempt in range(7):
        with pytest.raises(WalletFault) as refused:
            usb_backup.open_backup(found["file_id"], password=f"guess number {attempt}", method="pin", credential=NEW_PIN, credential_confirmation=NEW_PIN)
        codes.append(refused.value.code)
    assert codes[:5] == ["wallet_recovery_refused"] * 5 and codes[-1] == "wallet_unlock_throttled"
    with pytest.raises(WalletFault) as locked:  # the right password waits too: the stick is not a fast oracle
        usb_backup.open_backup(found["file_id"], password=BACKUP_PASSWORD, method="pin", credential=NEW_PIN, credential_confirmation=NEW_PIN)
    assert locked.value.code == "wallet_unlock_throttled"


def test_a_deleted_wallet_comes_back_from_the_stick_and_never_takes_main(stick):
    from core.wallet import custody, usb_backup, wallet_roles

    main = _create()
    spare = _create(pin=NEW_PIN)
    usb_backup.save(spare["wallet_id"], drive_id=_drive_id(), credential=NEW_PIN, password=BACKUP_PASSWORD, confirmation=BACKUP_PASSWORD)
    wallet_roles.remove_wallet(spare["wallet_id"], credential=NEW_PIN, backup_confirmed=True)
    (found,) = usb_backup.found()
    assert found["state"] == "can_bring_back" and found["address"] == spare["address"]
    back = usb_backup.open_backup(found["file_id"], password=BACKUP_PASSWORD, method="pin", credential=PIN, credential_confirmation=PIN)
    assert back["opened"] == "brought_back" and back["address"] == spare["address"]
    assert usb_backup.found()[0]["state"] == "on_this_computer"
    assert wallet_roles.main_wallet_id(SOLANA_MAINNET) == main["wallet_id"]
    assert custody.default_wallet().wallet_id == main["wallet_id"]


def test_a_new_computer_brings_the_wallet_back_from_the_stick(stick, monkeypatch, tmp_path):
    from core.blackbox import store as store_module
    from core.wallet import custody, pilot_custody, usb_backup

    made = _create()
    usb_backup.save(made["wallet_id"], drive_id=_drive_id(), credential=PIN, password=BACKUP_PASSWORD, confirmation=BACKUP_PASSWORD)
    # a fresh wallet store, as on a new computer: no wallets at all, only the stick
    from core.runtime_continuity import _runtime_db_path, configure_runtime_continuity_db_path

    previous = _runtime_db_path()
    configure_runtime_continuity_db_path(tmp_path / "new-computer.db")
    try:
        monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "new-computer-blackbox"))
        store_module.reset_default_store()
        assert [e for e in custody.list_wallets() if e.get("public_key") == made["address"]] == []
        (found,) = usb_backup.found()
        assert found["state"] == "can_bring_back"
        back = usb_backup.open_backup(found["file_id"], password=BACKUP_PASSWORD, method="pin", credential=NEW_PIN, credential_confirmation=NEW_PIN)
        assert back["address"] == made["address"] and back["opened"] == "brought_back"
        pilot_custody.prove_credential(back["wallet_id"], NEW_PIN)
    finally:
        configure_runtime_continuity_db_path(previous)


def test_a_changed_or_foreign_file_on_the_stick_is_not_offered(stick):
    from core.wallet import usb_backup

    made = _create()
    usb_backup.save(made["wallet_id"], drive_id=_drive_id(), credential=PIN, password=BACKUP_PASSWORD, confirmation=BACKUP_PASSWORD)
    (written,) = _files(stick["mount"])
    data = json.loads(written.read_text())
    data["address"] = "Vote111111111111111111111111111111111111111"  # the outer label no longer matches the sealed copy
    written.write_text(json.dumps(data))
    (stick["mount"] / usb_backup.FOLDER / f"notes{usb_backup.SUFFIX}").write_text("not a backup")
    (stick["mount"] / usb_backup.FOLDER / f"huge{usb_backup.SUFFIX}").write_text("x" * (usb_backup.MAX_FILE_BYTES + 1))
    assert usb_backup.found() == []


def test_a_copy_that_does_not_read_back_is_removed_and_refused(stick, monkeypatch):
    from core.wallet import usb_backup

    made = _create()
    monkeypatch.setattr(usb_backup, "_read_document", lambda path: None)
    with pytest.raises(WalletFault) as refused:
        usb_backup.save(made["wallet_id"], drive_id=_drive_id(), credential=PIN, password=BACKUP_PASSWORD, confirmation=BACKUP_PASSWORD)
    assert refused.value.context["reason"] == "usb_readback_failed"
    assert _files(stick["mount"]) == []


def _diskutil(monkeypatch, info: dict) -> None:
    from core.wallet import usb_backup

    def fake_run(argv, **kwargs):
        assert argv[:3] == ["/usr/sbin/diskutil", "info", "-plist"]
        return subprocess.CompletedProcess(argv, 0, stdout=plistlib.dumps(info), stderr=b"")

    monkeypatch.setattr(usb_backup.subprocess, "run", fake_run)


@pytest.mark.parametrize(("info", "kind"), [
    ({"MountPoint": "/Volumes/SANDISK", "Internal": False, "Ejectable": True, "RemovableMedia": True, "WritableVolume": True, "BusProtocol": "USB"}, "usb_stick"),
    ({"MountPoint": "/Volumes/T7", "Internal": False, "Ejectable": True, "RemovableMedia": False, "WritableVolume": True, "BusProtocol": "USB"}, "external_drive"),
    ({"MountPoint": "/Volumes/Data", "Internal": True, "Ejectable": False, "WritableVolume": True, "BusProtocol": "Apple Fabric"}, None),
    ({"MountPoint": "/Volumes/VOOL", "Internal": False, "Ejectable": True, "WritableVolume": False, "BusProtocol": "Disk Image", "VirtualOrPhysical": "Virtual"}, None),
    ({"MountPoint": "/Volumes/Locked", "Internal": False, "Ejectable": True, "RemovableMedia": True, "WritableVolume": False, "BusProtocol": "USB"}, None),
    ({"MountPoint": "/Volumes/Other", "Internal": False, "Ejectable": True, "WritableVolume": True}, None),  # diskutil answered for another mount
])
def test_a_mac_volume_counts_only_when_diskutil_says_it_is_external_and_writable(monkeypatch, info, kind):
    """A stick (removable media) and an external drive (a fixed disk in its own case, like an SSD) both take an offline
    backup; each is called what it is."""
    from core.wallet import usb_backup

    _diskutil(monkeypatch, info)
    mount = Path(info["MountPoint"]) if info["MountPoint"] != "/Volumes/Other" else Path("/Volumes/Mine")
    assert usb_backup._mac_volume_kind(mount) == kind
    assert usb_backup._mac_volume_is_removable(mount) is (kind is not None)


def test_the_page_names_each_drive_by_its_kind(stick, monkeypatch):
    from core.wallet import usb_backup
    from core.wallet_fragment import render_wallet_fragment

    made = _create()
    usb_backup.save(made["wallet_id"], drive_id=_drive_id(), credential=PIN, password=BACKUP_PASSWORD, confirmation=BACKUP_PASSWORD)
    assert [(d["name"], d["kind"]) for d in usb_backup.drives()] == [("SANDISK", "usb_stick")]
    monkeypatch.setattr(usb_backup, "_mounts", lambda: [(stick["mount"], "VOOL_WORK", "external_drive")])
    assert [(d["name"], d["kind"]) for d in usb_backup.drives()] == [("VOOL_WORK", "external_drive")]
    assert [(b["drive"], b["drive_kind"]) for b in usb_backup.overview()["backups"]] == [("VOOL_WORK", "external_drive")]
    page = render_wallet_fragment()
    assert "(d.kind === 'external_drive' ? ' (external drive)' : ' (USB stick)')" in page
    assert "USB stick or external drive" in page and "Look on USB sticks" not in page


@pytest.mark.parametrize("bad", [
    json.dumps({"kind": "vool-wallet-backup", "v": [1], "sealed": {}}),
    json.dumps({"kind": "vool-wallet-backup", "v": "x", "sealed": {}}),
    json.dumps({"kind": "vool-wallet-backup", "v": {}, "sealed": {}}),
    json.dumps({"kind": "vool-wallet-backup", "v": True, "sealed": {}}),
    json.dumps({"kind": "vool-wallet-backup", "v": 1, "network": ["solana"], "address": {"a": 1}, "sealed": {"network": 1}}),
    json.dumps([1, 2, 3]),
    "\udcff not utf-8",
])
def test_one_malformed_file_never_hides_the_good_backup_beside_it(stick, bad):
    from core.wallet import usb_backup

    made = _create()
    usb_backup.save(made["wallet_id"], drive_id=_drive_id(), credential=PIN, password=BACKUP_PASSWORD, confirmation=BACKUP_PASSWORD)
    target = stick["mount"] / usb_backup.FOLDER / f"VOOL zz broken{usb_backup.SUFFIX}"
    target.write_bytes(bad.encode("utf-8", "surrogateescape"))
    assert [row["address"] for row in usb_backup.overview()["backups"]] == [made["address"]]


def test_a_pipe_or_a_link_named_like_a_backup_is_skipped_without_blocking(stick, tmp_path):
    import os

    from core.wallet import usb_backup

    made = _create()
    usb_backup.save(made["wallet_id"], drive_id=_drive_id(), credential=PIN, password=BACKUP_PASSWORD, confirmation=BACKUP_PASSWORD)
    folder = stick["mount"] / usb_backup.FOLDER
    os.mkfifo(folder / f"a pipe{usb_backup.SUFFIX}")  # opening it for reading would block without O_NONBLOCK
    (written,) = [p for p in _files(stick["mount"]) if not p.name.startswith("a ")]
    (folder / f"a link{usb_backup.SUFFIX}").symlink_to(written)
    assert [row["address"] for row in usb_backup.overview()["backups"]] == [made["address"]]
