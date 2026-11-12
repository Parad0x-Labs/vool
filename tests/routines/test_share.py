"""Sharing a routine: the file holds the rules, never where it worked or private words; bringing one in
is strict, and what comes in stays inactive until the person chooses a folder."""
from __future__ import annotations

import io
import json
import zipfile

import pytest

from core.routines import share, store
from core.routines.model import RoutineError


@pytest.fixture(autouse=True)
def _no_contacts_left_over(person):
    # The test session shares one database, and saved contacts are not among the tables reset per test.
    from core.contacts.store import reset_contacts_for_tests

    reset_contacts_for_tests()
    yield
    reset_contacts_for_tests()


def _saved(person):
    from core.contacts.store import ACTOR_OWNER, create_contact

    create_contact(display_name="Anna Kowalski", actor=ACTOR_OWNER)
    folder = person / "Downloads"
    return store.save({
        "name": "Invoices for Anna Kowalski",
        "folder": str(folder),
        "types": [".pdf"],
        "rename": [{"token": "date"}, {"text": " for anna.k@example.com "}, {"token": "name"}],
        "into": [{"text": "Clients/Call +44 20 7946 0958"}],
        "log": {"file": "paid by Anna Kowalski.csv",
                "columns": [[{"token": "date"}], [{"text": "Anna Kowalski"}], [{"text": "IBAN GB82 WEST 1234 5698 7654 32"}]]},
        "schedule": {"every": "weekly", "time": "08:15", "weekday": 2},
    })


def _view(routine):
    return {key: value for key, value in routine.items() if key != "version"}


def test_the_shared_file_has_the_rules_and_none_of_the_private_details(person):
    routine = _saved(person)
    seen = share.preview(_view(routine))
    assert sorted(seen["files"]) == ["SKILL.md", "routine.json"]
    assert seen["kept_here"] == ["folder", "schedule", "history", "runs"]
    assert seen["replaced"] == 6 and seen["contacts_checked"] is True
    data = share.export(_view(routine), seen["sha256"])
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert sorted(archive.namelist()) == ["SKILL.md", "routine.json"]
        contents = {name: archive.read(name).decode("utf-8") for name in archive.namelist()}
    assert contents == seen["files"]  # the file written is the one the person looked at
    everything = "\n".join(contents.values())
    for private in ("anna.k@example.com", "7946 0958", "Kowalski", "GB82", "5698", str(person), "Downloads", routine["id"],
                    "08:15"):
        assert private not in everything, private
    record = json.loads(contents["routine.json"])
    assert set(record) == {"format", "name", "types", "rename", "into", "log", "max_files"}
    assert record["format"] == share.SHARE_FORMAT
    assert record["rename"][0] == {"token": "date"} and record["rename"][2] == {"token": "name"}
    assert record["name"] == "Invoices for Person 1"  # a routine's name takes no brackets
    assert record["rename"][1] == {"text": " for [Email 1] "}
    assert record["into"] == [{"text": "Clients/Call [Phone 1]"}]
    assert record["log"] == {"file": "paid by [Person 1].csv", "columns": [
        [{"token": "date"}], [{"text": "[Person 1]"}], [{"text": "IBAN [Bank account 1]"}]]}


def test_words_that_look_like_a_key_are_refused_when_saving(person):
    folder = person / "Downloads"
    with pytest.raises(RoutineError) as caught:
        store.save({"name": "Bank", "folder": str(folder), "types": [".pdf"],
                    "rename": [{"text": "4111 1111 1111 1111 "}, {"token": "name"}]})
    assert caught.value.code == "save_failed" and "safety check" in caught.value.message
    assert store.all_routines() == []


def test_the_file_must_still_be_the_one_the_person_saw(person):
    routine = _saved(person)
    seen = share.preview(_view(routine))
    with pytest.raises(RoutineError) as caught:
        share.export(_view(routine), "0" * 64)
    assert caught.value.code == "share_changed" and caught.value.status == 409
    changed = store.save(dict(_view(routine), into=[{"text": "Clients/Other"}]))
    with pytest.raises(RoutineError) as caught:
        share.export(_view(changed), seen["sha256"])
    assert caught.value.code == "share_changed"


def test_a_shared_routine_comes_in_new_inactive_and_without_a_folder(person):
    routine = _saved(person)
    seen = share.preview(_view(routine))
    incoming = share.read(share.export(_view(routine), seen["sha256"]))
    assert incoming["id"] != routine["id"]
    assert incoming["folder"] == ""
    assert incoming["schedule"] == {"every": "", "time": "09:00", "weekday": 0}
    assert incoming["types"] == routine["types"] and incoming["into"] != routine["into"]
    staged = store.stage(incoming)
    assert [item["id"] for item in store.staged()] == [staged["id"]]
    assert [item["id"] for item in store.all_routines()] == [routine["id"]]  # not a routine until saved
    store.unstage(staged["id"])
    assert store.staged() == []


def _zip(members, *, encrypted=False):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for name, data in members.items():
            archive.writestr(zipfile.ZipInfo(name), data)
    data = bytearray(out.getvalue())
    if encrypted:
        # Python writes no encrypted members: set the "encrypted" flag in both headers by hand.
        data[data.find(b"PK\x03\x04") + 6] |= 0x1
        data[data.find(b"PK\x01\x02") + 8] |= 0x1
    return bytes(data)


GOOD = {"format": share.SHARE_FORMAT, "name": "Sort scans", "types": [".pdf"], "rename": [{"token": "date"}],
        "into": [], "log": None, "max_files": 25}


@pytest.mark.parametrize("data", [
    b"",
    b"not a zip at all",
    _zip({"routine.json": json.dumps(GOOD), "run.sh": "rm -rf ~"}),
    _zip({"SKILL.md": "# only words"}),
    _zip({"../routine.json": json.dumps(GOOD)}),
    _zip({"routine.json": json.dumps(dict(GOOD, format="vool.routine-share/2"))}),
    _zip({"routine.json": json.dumps(dict(GOOD, folder="/Users/someone/Downloads"))}),
    _zip({"routine.json": json.dumps(dict(GOOD, schedule={"every": "daily", "time": "03:00", "weekday": 0}))}),
    _zip({"routine.json": json.dumps(dict(GOOD, rename=[{"text": "../../x"}]))}),
    _zip({"routine.json": json.dumps(dict(GOOD, types=[".app"], rename=[{"token": "run"}]))}),
    _zip({"routine.json": json.dumps(GOOD) + " " * share.MAX_MEMBER_BYTES}),
    _zip({"routine.json": json.dumps(GOOD)}, encrypted=True),
    _zip({"routine.json": "[1, 2, 3]"}),
    _zip({"routine.json": b"\xff\xfe not text"}),
    b"PK" + b"\x00" * (share.MAX_SHARE_BYTES + 1),
], ids=["empty", "not-zip", "extra-member", "no-routine", "traversal-name", "newer-format", "carries-folder",
        "carries-schedule", "slash-in-name", "unknown-value", "member-too-big", "encrypted", "not-an-object",
        "not-text", "file-too-big"])
def test_anything_but_a_routine_shared_from_vool_is_refused(person, data):
    with pytest.raises(RoutineError) as caught:
        share.read(data)
    assert caught.value.code in {"bad_share", "bad_text", "bad_rule"}, caught.value.code
    assert store.staged() == []


def test_the_minimal_shared_routine_is_read(person):
    incoming = share.read(_zip({"routine.json": json.dumps(GOOD)}))
    assert incoming["name"] == "Sort scans" and incoming["folder"] == ""
