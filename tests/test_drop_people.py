"""Drop between people: contact cards, names, sending to a contact, and what a synced folder does to order."""
import os

import pytest

from core.drop import DropError, people
from core.drop.service import CONTACT_SLOTS, DROPS_PER_PEER, MAX_FILE, DropHome, LocalRelay, check_relay


@pytest.fixture
def trio(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_DROP", "1")
    relay = tmp_path / "relay"
    relay.mkdir(mode=0o755)  # what a sync service makes: not private, and it does not have to be
    with DropHome(tmp_path / "a", relay) as a, DropHome(tmp_path / "b", relay) as b, \
            DropHome(tmp_path / "c", relay) as c:
        a.set_name("Ana")
        b.set_name("Ben")
        c.set_name("Cy")
        yield a, b, c, relay


def friends(a, b):
    a.contact_add(b.me()["card"])
    b.contact_add(a.me()["card"])


def test_two_people_add_cards_send_receive_save_and_the_sender_learns_it_arrived(trio, tmp_path):
    a, b, _, relay = trio
    assert relay.stat().st_mode & 0o777 == 0o755
    friends(a, b)
    assert [p["name"] for p in a.contacts()] == ["Ben"] and a.contacts()[0]["ready"]
    data = os.urandom(130000)
    sid, manifest = a.send_to(b.fingerprint, "the plans", "plans.pdf", data)
    assert a.show(sid)["file"] == {"state": "sent"}
    assert data[:64] not in b"".join(p.read_bytes() for p in relay.rglob("*") if p.is_file())
    b.poll()
    (item,) = b.inbox()
    assert item["person"]["name"] == "Ana" and item["person"]["tag"] == people.tag(a.fingerprint)
    assert item["name"] == "plans.pdf" and item["size"] == 130000 and item["file"]["state"] == "ready"
    assert not item["sent"] and item["created"] > 0
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    (downloads / "plans.pdf").write_text("mine, not to be replaced")
    saved = b.save_file(sid, downloads)
    assert saved.name == "plans (1).pdf" and saved.read_bytes() == data
    assert (downloads / "plans.pdf").read_text() == "mine, not to be replaced"
    assert b.show(sid)["file"] == {"state": "ready", "saved": "plans (1).pdf"}
    # Only the receiver has a file to save.
    with pytest.raises(DropError, match="FILE_NOT_READY"):
        a.save_file(sid, downloads)
    a.poll()
    assert a.show(sid)["file"] == {"state": "delivered"}
    # A second and a third file need no new pairing.
    for index in range(2):
        again, _ = a.send_to(b.fingerprint, "", f"more-{index}.txt", b"x" * index)
        b.poll()
        assert b.file_bytes(again) == (f"more-{index}.txt", b"x" * index)


def test_a_name_from_the_other_side_cannot_leave_the_save_folder(trio, tmp_path):
    a, b, _, _ = trio
    friends(a, b)
    sid, _ = a.send_to(b.fingerprint, "", ".bashrc", b"echo hi")
    b.poll()
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    saved = b.save_file(sid, downloads)
    assert saved.parent == downloads and saved.name == "bashrc" and saved.read_bytes() == b"echo hi"
    for bad in ("../up.txt", "a/b.txt", "x\\y", ""):
        with pytest.raises(DropError, match="INVALID_FILENAME"):
            a.send_to(b.fingerprint, "", bad, b"1")
    with pytest.raises(DropError, match="SAVE_FOLDER_MISSING"):
        b.save_file(sid, tmp_path / "nowhere")


def test_one_name_one_key_and_lookalikes_are_refused(trio):
    a, b, c, _ = trio
    a.contact_add(b.me()["card"], "Anna")
    for name, code in (("Anna", "NAME_TAKEN"), ("anna", "NAME_TAKEN"), ("Аnnа", "NAME_LOOKALIKE"),
                       ("Anna​", "NAME_TAKEN"), ("‮Anna", "NAME_TAKEN")):
        with pytest.raises(DropError, match=code):
            a.contact_add(c.me()["card"], name)
    assert [p["name"] for p in a.contacts()] == ["Anna"]
    # The same card twice, this VOOL's own card, and things that are not cards.
    with pytest.raises(DropError, match="CONTACT_EXISTS"):
        a.contact_add(b.me()["card"], "Someone else")
    with pytest.raises(DropError, match="SELF_CARD"):
        a.contact_add(a.me()["card"], "Me")
    for junk in ("", "hello", "vool-drop://card/!!!", "vool-drop://invite/" + "0" * 32, 7,
                 people.CARD_PREFIX + "e30", b.me()["card"][:-6]):
        with pytest.raises(DropError, match="INVALID_CARD"):
            a.contact_add(junk, "X")
    # A different name is fine, and renaming obeys the same rule.
    a.contact_add(c.me()["card"], "Cy")
    with pytest.raises(DropError, match="NAME_TAKEN"):
        a.contact_rename(c.fingerprint, "ANNA")
    assert a.contact_rename(c.fingerprint, "Cyrus")["name"] == "Cyrus"
    # The card's own name is only a suggestion; the tag and number come from the key alone.
    preview = a.card_preview(people.make_card(b.fingerprint, "Totally Ana"))
    assert preview["known_as"] == "Anna" and preview["tag"] == people.tag(b.fingerprint)
    assert preview["safety_number"] == b.me()["safety_number"] != a.me()["safety_number"]
    assert len(preview["safety_number"].replace(" ", "")) == 20 and preview["tag"].count("-") == 2
    assert a.contact_verify(b.fingerprint, True)["verified"] and not a.contacts()[1]["verified"]


def test_sending_needs_the_other_side_to_have_added_you_and_to_come_online(trio):
    a, b, _, _ = trio
    a.contact_add(b.me()["card"])
    with pytest.raises(DropError, match="CONTACT_NOT_READY"):
        a.send_to(b.fingerprint, "", "f.txt", b"1")
    with pytest.raises(DropError, match="UNKNOWN_CONTACT"):
        b.send_to(a.fingerprint, "", "f.txt", b"1")
    assert a.inbox() == [] and b.inbox() == []
    b.contact_add(a.me()["card"])
    sent = [a.send_to(b.fingerprint, "", f"{i}.txt", b"%d" % i)[0] for i in range(CONTACT_SLOTS)]
    # Every invite Ben published is used and Ben has not opened VOOL since.
    with pytest.raises(DropError, match="CONTACT_BUSY"):
        a.send_to(b.fingerprint, "", "late.txt", b"late")
    assert len(a.inbox()) == CONTACT_SLOTS
    b.poll()
    assert sorted(b.file_bytes(sid)[1] for sid in sent) == [b"0", b"1", b"2"]
    sid, _ = a.send_to(b.fingerprint, "", "late.txt", b"late")
    b.poll()
    assert b.file_bytes(sid) == ("late.txt", b"late")


def test_removing_a_person_deletes_their_drops_and_refuses_what_they_send_next(trio):
    a, b, _, relay = trio
    friends(a, b)
    first, _ = b.send_to(a.fingerprint, "old", "old.txt", b"old")
    a.poll()
    assert a.file_bytes(first)[1] == b"old"
    a.contact_remove(b.fingerprint)
    assert a.contacts() == [] and a.inbox() == []
    assert not (a.root / first).exists()
    # Ben still holds invites he read before; none of them opens anything on Ana's side now.
    for index in range(CONTACT_SLOTS):
        try:
            b.send_to(a.fingerprint, "again", f"again-{index}.txt", b"again")
        except DropError as exc:
            assert exc.code in {"CONTACT_BUSY", "CONTACT_NOT_READY"}
    outcomes = a.poll()
    assert "received" not in outcomes and a.inbox() == []
    with pytest.raises(DropError, match="UNKNOWN_CONTACT"):
        a.send_to(b.fingerprint, "", "f.txt", b"1")


def test_a_large_file_whose_first_pieces_sync_last_arrives_whole(trio, tmp_path):
    a, b, _, relay = trio
    friends(a, b)
    data = os.urandom(1_600_000)  # 40 pieces
    sid, manifest = a.send_to(b.fingerprint, "big", "big.bin", data)
    frames = a.relay.messages(b.fingerprint)
    assert len(frames) == 1 + 1 + manifest["chunks"]
    late = tmp_path / "late"
    late.mkdir()
    for path in frames[1:37]:  # the header and the first 35 pieces have not synced yet
        path.rename(late / path.name)
    outcomes = b.poll()
    assert "rejected" not in outcomes and outcomes.count("waiting") == 5
    assert a.relay.messages(b.fingerprint) == []
    assert b.inbox()[0]["file"]["state"] == "empty"
    with pytest.raises(DropError, match="FILE_NOT_READY"):
        b.file_bytes(sid)
    # Half of the late ones arrive, in the wrong order.
    arrived = sorted(late.iterdir())
    for path in reversed(arrived[:20]):
        path.rename(frames[0].parent / path.name)
    assert "rejected" not in b.poll()
    state = b.show(sid)["file"]
    assert state["state"] == "arriving" and state["total"] == 40 and 0 < state["have"] < 40
    for path in arrived[20:]:
        path.rename(frames[0].parent / path.name)
    assert "rejected" not in b.poll()
    assert b.file_bytes(sid) == ("big.bin", data)
    assert not list((b.root / sid).glob("*.ahead")) and not list((b.root / sid).glob("*.part"))
    a.poll()
    assert a.show(sid)["file"] == {"state": "delivered"}


def test_forged_far_ahead_frames_cost_one_drop_its_room_and_nothing_else(trio, tmp_path):
    import json

    from core.drop import service
    from core.drop.service import _write

    a, b, c, relay = trio
    friends(a, b)
    friends(c, b)
    sid, _ = a.send_to(b.fingerprint, "", "one.txt", b"one")
    b.poll()
    template = None
    extra, _ = a.send_to(b.fingerprint, "", "two.txt", b"two")
    for path in a.relay.messages(b.fingerprint):
        frame = json.loads(path.read_bytes())
        if frame.get("type") == "message":
            template = frame
    assert template is not None
    b.poll()
    mailbox = relay / b.fingerprint
    for index in range(service.AHEAD_LIMIT + 40):  # anyone who can write to the folder can do this
        forged = dict(template, n=4000 - index % 3000, sid=sid, pn=index % 7)
        _write(mailbox / f"{index:020d}-{'A' * 22}-forged.wire", json.dumps(forged, sort_keys=True).encode())
    outcomes = b.poll()
    assert outcomes.count("waiting") <= service.AHEAD_LIMIT and "held" not in outcomes
    assert len(list((b.root / sid).glob("*.ahead"))) <= service.AHEAD_LIMIT
    assert a.relay.messages(b.fingerprint) == []  # nothing is left to clog the folder
    other, _ = c.send_to(b.fingerprint, "", "real.bin", os.urandom(50000))
    b.poll()
    assert b.show(other)["file"]["state"] == "ready"
    b.delete(sid)
    assert not (b.root / sid).exists()


def test_one_person_may_only_hold_so_many_open_drops(trio):
    a, b, _, _ = trio
    friends(a, b)
    made = 0
    for _ in range(DROPS_PER_PEER + 6):
        try:
            a.send_to(b.fingerprint, "", "f.txt", b"1")
            made += 1
        except DropError as exc:
            assert exc.code == "CONTACT_BUSY"
        b.poll()
    assert len(b.inbox()) == DROPS_PER_PEER and made >= DROPS_PER_PEER


def test_the_shared_folder_must_exist_and_never_follows_a_link(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_DROP", "1")
    with pytest.raises(DropError, match="MISSING_RELAY"):
        check_relay(tmp_path / "typo")
    assert not (tmp_path / "typo").exists()
    file = tmp_path / "file"
    file.write_text("x")
    with pytest.raises(DropError, match="RELAY_NOT_FOLDER"):
        check_relay(file)
    with pytest.raises(DropError, match="RELAY_REQUIRED"):
        check_relay("relative/path")
    real = tmp_path / "CloudStorage" / "Dropbox" / "VOOL"
    real.mkdir(parents=True, mode=0o755)
    (tmp_path / "Dropbox").symlink_to(tmp_path / "CloudStorage" / "Dropbox")
    relay = check_relay(tmp_path / "Dropbox" / "VOOL")  # the shortcut a sync app leaves in the home folder
    assert relay == real.resolve()
    locked = tmp_path / "locked"
    locked.mkdir(mode=0o500)
    if os.getuid() != 0:
        with pytest.raises(DropError, match="RELAY_READ_ONLY"):
            check_relay(locked)
    with DropHome(tmp_path / "a", relay) as a, DropHome(tmp_path / "b", relay) as b:
        a.contact_add(b.me()["card"], "Ben")
        b.contact_add(a.me()["card"], "Ana")
        with pytest.raises(DropError, match="INVALID_NAME"):
            a.contact_add(people.make_card("A" * 43, ""), "")  # no name on the card and none typed
        # Someone with access to the folder swaps Ben's mailbox for a link to a private place.
        victim = tmp_path / "victim"
        victim.mkdir()
        (victim / "keep.wire").write_text("not a frame")
        (relay / b.fingerprint).symlink_to(victim)
        with pytest.raises(DropError, match="RELAY_NOT_FOLDER"):
            a.send_to(b.fingerprint, "", "f.txt", b"1")
        with pytest.raises(DropError, match="RELAY_NOT_FOLDER"):
            b.poll()
        assert sorted(p.name for p in victim.iterdir()) == ["keep.wire"]


def test_what_drop_keeps_on_this_disk_is_sealed(trio, tmp_path):
    a, b, _, _ = trio
    friends(a, b)
    secret_note, secret_name, data = "meet at the red door", "ledger-2031.xlsx", os.urandom(4000) + b"MARKER-IN-FILE"
    sid, _ = a.send_to(b.fingerprint, secret_note, secret_name, data)
    b.poll()
    for home in (a, b):
        home.db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
        home.client.checkpoint()
        stored = b"".join(p.read_bytes() for p in home.root.rglob("*") if p.is_file() and p.name != "vault.key")
        for plain in (secret_note.encode(), secret_name.encode(), b"MARKER-IN-FILE", b"Ana", b"Ben"):
            assert plain not in stored
    assert b.show(sid)["note"] == secret_note and b.file_bytes(sid) == (secret_name, data)
    # A sealed copy that was changed on disk is damage, never a different file.
    target = b.root / sid / "selected.bin"
    blob = bytearray(target.read_bytes())
    blob[40] ^= 1
    target.write_bytes(bytes(blob))
    os.chmod(target, 0o600)
    with pytest.raises(DropError, match="LOCAL_COPY_DAMAGED"):
        b.file_bytes(sid)
