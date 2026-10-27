"""A file on the computer goes to the owner's paired phone, which saves it ("send day4.jpg to my phone").

Asked from a phone 2026-10-27: after finding the day4 picture, sls wanted it on the phone. VOOL could
list it and nothing more: there was no way for a file to leave the computer for the phone.
"""
from __future__ import annotations

import base64
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import request_trust as rt
from core.execution.constants import machine_send_to_phone_intent

_REMOTE = {rt.HTTP_INGRESS_KEY: True, rt.OWNER_LOCAL_KEY: False}


@pytest.mark.parametrize(("text", "phone_turn", "kind"), [
    ("send day4.jpg to my phone", False, "search"),
    ("can you send me my resume pdf", True, "search"),
    ("send me day4.jpg", True, "search"),
    ("get the picture named day4 in downloads onto my phone", False, "search"),
    ("send me the newest screenshot from my downloads to my phone", False, "listing"),
    ("send ~/Downloads/day4.jpg to my phone", False, "path"),
])
def test_a_send_to_phone_ask(text, phone_turn, kind):
    request = machine_send_to_phone_intent(text, phone_turn=phone_turn)
    assert request is not None
    assert {"search": request.search is not None, "listing": request.listing is not None, "path": bool(request.path)}[kind]


@pytest.mark.parametrize(("text", "phone_turn"), [
    ("send me day4.jpg", False), ("send day4.jpg to bob", False), ("send day4.jpg to my phone and tell bob", False),
    ("send me a joke", True), ("send me the pictures in downloads", True), ("send a message to my phone", False),
    ("get my phone number", False),
])
def test_not_a_send_to_phone_ask(text, phone_turn):
    assert machine_send_to_phone_intent(text, phone_turn=phone_turn) is None


class _Store:
    def __init__(self, *devices):
        self.records = {d["device_id"]: d for d in devices}
        self.receipts = []

    def device(self, device_id):
        record = self.records.get(device_id)
        return dict(record) if record else None

    def devices(self):
        return dict(self.records)

    def append_receipt(self, kind, **kw):
        self.receipts.append(kind)

    def mark_device(self, *a, **k):
        return None


def _phone(device_id="phone:abc", **extra):
    return {"device_id": device_id, "name": "Pixel", "status": "active", "kind": "phone", "sealed_protocol": True,
            "rules": {"computer_tools": True, "chat": True}, "right_expiry": {"computer_tools": time.time() + 3600},
            **extra}


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "home"
    for name in ("Desktop", "Downloads", "Documents"):
        (root / name).mkdir(parents=True)
    (root / "Downloads" / "day4.jpg").write_bytes(b"\xff\xd8" + bytes(range(256)) * 9)
    monkeypatch.setenv("HOME", str(root))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: root))
    data = tmp_path / "data"
    import core.runtime_paths as runtime_paths

    monkeypatch.setattr(runtime_paths, "active_data_dir", lambda: data)
    return root


@pytest.fixture
def phones(monkeypatch):
    store = _Store(_phone())
    import core.mobile_companion.listener as listener
    import core.web.api.mobile_companion_api as api

    monkeypatch.setattr(api, "mobile_companion_service", lambda: SimpleNamespace(store=store))
    monkeypatch.setattr(listener, "load_settings", lambda: {"enabled": True})
    return store


def test_a_sent_file_is_fetched_whole_by_that_phone_only(home):
    from core.mobile_companion import outgoing_files

    source = home / "Downloads" / "day4.jpg"
    sent = outgoing_files.send("phone:abc", source)
    assert [row["id"] for row in outgoing_files.listing("phone:abc")] == [sent["id"]]
    assert outgoing_files.listing("phone:other") == []
    with pytest.raises(outgoing_files.SendRefused):
        outgoing_files.chunk("phone:other", sent["id"], 0)
    record, data = outgoing_files.chunk("phone:abc", sent["id"], 0)
    assert data == source.read_bytes() and record["name"] == "day4.jpg" and record["media_type"] == "image/jpeg"
    outgoing_files.forget_device("phone:abc")
    assert outgoing_files.listing("phone:abc") == []


def test_a_sent_file_expires(home, monkeypatch):
    from core.mobile_companion import outgoing_files

    outgoing_files.send("phone:abc", home / "Downloads" / "day4.jpg")
    later = time.time() + outgoing_files.TTL_SECONDS + 1
    monkeypatch.setattr(outgoing_files.time, "time", lambda: later)
    assert outgoing_files.listing("phone:abc") == []


def test_the_phone_pulls_it_in_pieces_that_join_as_base64(home, phones, monkeypatch):
    from core.mobile_companion import outgoing_files
    from core.web.api.mobile_companion_api import _action_files_fetch, _action_files_list

    monkeypatch.setattr(outgoing_files, "CHUNK_BYTES", 999)
    source = home / "Downloads" / "day4.jpg"
    sent = outgoing_files.send("phone:abc", source)
    device = phones.device("phone:abc")
    service = SimpleNamespace(store=phones)
    _status, listed = _action_files_list({}, device=device)
    assert [row["name"] for row in listed["files"]] == ["day4.jpg"]
    offset, pieces = 0, []
    while True:
        status, piece = _action_files_fetch({"file_id": sent["id"], "offset": offset}, service=service, device=device)
        assert status == 200
        pieces.append(piece["content_b64"])
        if piece["done"]:
            break
        offset = piece["next_offset"]
    assert base64.b64decode("".join(pieces)) == source.read_bytes()
    stranger = _phone("phone:other")
    assert _action_files_fetch({"file_id": sent["id"], "offset": 0}, service=service, device=stranger)[0] == 404


def test_a_phone_turn_sends_to_that_phone_and_keeps_its_folder_fence(home, phones):
    from core.mobile_companion import outgoing_files
    from core.runtime_execution_tools import execute_runtime_tool

    turn = {**_REMOTE, rt.REMOTE_DEVICE_KEY: "phone:abc"}
    sent = execute_runtime_tool("machine.send_to_phone", {"path": "~/Downloads/day4.jpg"}, source_context=turn)
    assert sent.ok, sent.response_text
    assert sent.response_text.startswith("Sent day4.jpg (")
    assert [row["name"] for row in outgoing_files.listing("phone:abc")] == ["day4.jpg"]
    phones.records["phone:abc"]["file_scope"] = {"mode": "folders", "folders": [str(home / "Documents")]}
    fenced = execute_runtime_tool("machine.send_to_phone", {"path": "~/Downloads/day4.jpg"}, source_context=turn)
    assert not fenced.ok and fenced.status == "phone_folders_only"
    (home / "Documents" / "notes.txt").write_text("milk")
    inside = execute_runtime_tool("machine.send_to_phone", {"path": "~/Documents/notes.txt"}, source_context=turn)
    assert inside.ok, inside.response_text


def test_a_phone_without_computer_tools_or_a_chat_app_cannot_send(home, phones):
    from core.runtime_execution_tools import execute_runtime_tool

    phones.records["phone:abc"]["rules"] = {"chat": True}
    turn = {**_REMOTE, rt.REMOTE_DEVICE_KEY: "phone:abc"}
    assert not execute_runtime_tool("machine.send_to_phone", {"path": "~/Downloads/day4.jpg"}, source_context=turn).ok
    assert not execute_runtime_tool("machine.send_to_phone", {"path": "~/Downloads/day4.jpg"}, source_context=dict(_REMOTE)).ok


def test_the_owner_sends_to_the_one_paired_phone(home, phones):
    from core.mobile_companion import outgoing_files
    from core.runtime_execution_tools import execute_runtime_tool

    owner = {rt.OWNER_LOCAL_KEY: True}
    sent = execute_runtime_tool("machine.send_to_phone", {"path": "~/Downloads/day4.jpg"}, source_context=owner)
    assert sent.ok and "to Pixel" in sent.response_text
    assert outgoing_files.listing("phone:abc")
    phones.records["phone:two"] = _phone("phone:two")
    two = execute_runtime_tool("machine.send_to_phone", {"path": "~/Downloads/day4.jpg"}, source_context=owner)
    assert not two.ok and two.status == "no_phone" and "More than one phone" in two.response_text
    outside = execute_runtime_tool("machine.send_to_phone", {"path": "/etc/hosts"}, source_context=owner)
    assert not outside.ok


def test_send_day4_to_my_phone_finds_and_sends_it(home, phones, monkeypatch):
    import core.authorized_tool_execution as ate
    from core.agent_runtime.fast_paths_machine import send_file_to_phone
    from core.mobile_companion import outgoing_files
    from core.runtime_execution_tools import execute_runtime_tool

    monkeypatch.setattr(ate, "execute_authorized_runtime_tool",
                        lambda intent, args, source_context=None, **_k: execute_runtime_tool(intent, args, source_context=source_context))
    owner = {rt.OWNER_LOCAL_KEY: True}
    sent = send_file_to_phone(machine_send_to_phone_intent("send day4.jpg to my phone"), source_context=owner)
    assert sent.ok and sent.response_text.startswith("Sent day4.jpg"), sent.response_text
    (home / "Documents" / "day4.jpg").write_bytes(b"\xff\xd8other")
    two = send_file_to_phone(machine_send_to_phone_intent("send day4.jpg to my phone"), source_context=owner)
    assert two.status == "needs_choice" and "~/Documents/day4.jpg" in two.response_text
    assert len(outgoing_files.listing("phone:abc")) == 1
