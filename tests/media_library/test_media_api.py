"""The media library's doors and page: owner-only, sliced uploads sealed as they arrive, items opened for the page
only (whole, by range, or as a preview), removal with a count, and the USB backup run as a job."""

from __future__ import annotations

import io
import json
import re
import time
from pathlib import Path

import pytest

from core.media_library import api as media_api
from core.media_library import backup as mb
from core.media_library.store import Library
from core.web.api.service import (
    RuntimeServices,
    dispatch_get,
    dispatch_post,
    dispatch_upload,
    is_raw_upload_path,
    raw_upload_body_ceiling,
)

KEY = bytes(range(32))
OWN = {"Content-Type": "application/json", "Sec-Fetch-Site": "same-origin"}
RUNTIME = RuntimeServices(display_name="VOOL")


@pytest.fixture()
def home(tmp_path, monkeypatch):
    lib = Library(tmp_path / "home" / "media_library.noindex", key=KEY)
    monkeypatch.setattr(media_api, "library", lambda: lib)
    monkeypatch.setattr(media_api, "_settings_path", lambda: tmp_path / "home" / "media_library_settings.json")
    drive = tmp_path / "stick"
    drive.mkdir()
    from core.wallet import usb_backup

    monkeypatch.setattr(usb_backup, "_drives_now", lambda: [(drive, "Stick", "usb")])
    monkeypatch.setattr(usb_backup, "_drive_id", lambda mount: "stick-1")
    monkeypatch.setattr(usb_backup, "_free_bytes", lambda mount: 10 * 1024 ** 3)
    remembered: dict[str, bytes] = {}
    monkeypatch.setattr(mb, "remembered_key", lambda vault_id: remembered.get(vault_id))
    monkeypatch.setattr(mb, "remember_key", lambda vault: remembered.__setitem__(vault.vault_id, vault.key))
    monkeypatch.setattr(mb, "_free", lambda folder: 10 * 1024 ** 3)
    return lib, drive, remembered


def _post(body, headers=OWN, host="127.0.0.1"):
    response = dispatch_post(path="/api/media", body=body, headers=dict(headers), runtime=RUNTIME, model_name="vool",
                             workspace_root_provider=lambda: None, client_host=host)
    return response.status, json.loads(response.body)


def _get(query, headers=None, host="127.0.0.1"):
    response = dispatch_get(path="/api/media/item", query=query, runtime=RUNTIME, model_name="vool", client_host=host,
                            headers=dict(headers or {"Sec-Fetch-Site": "same-origin"}))
    body = response.body if response.stream is None else b"".join(response.stream)
    return response.status, response.headers, body


def _upload(upload_id, offset, data, content_type="application/octet-stream"):
    response = dispatch_upload(path="/api/media/upload", raw_body=data, runtime=RUNTIME, client_host="127.0.0.1",
                               headers={"Content-Type": content_type, "Sec-Fetch-Site": "same-origin",
                                        "X-Media-Upload": upload_id, "X-Media-Offset": str(offset)})
    return response.status, json.loads(response.body)


def _png() -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (800, 600), (40, 120, 90)).save(out, format="PNG")
    return out.getvalue()


def _add_through_doors(data: bytes, name: str, media_type: str, slice_bytes: int = 0) -> dict:
    status, began = _post({"action": "upload_begin", "name": name, "media_type": media_type, "size": len(data), "taken_at": 1_700_000_000_000})
    assert status == 200, began
    step = slice_bytes or began["slice_bytes"]
    for offset in range(0, len(data), step):
        status, sent = _upload(began["upload_id"], offset, data[offset:offset + step])
        assert status == 200 and sent["next_offset"] == min(len(data), offset + step), sent
    status, done = _post({"action": "upload_finish", "upload_id": began["upload_id"]})
    assert status == 200, done
    return done["item"]


def test_the_doors_are_listed_with_their_own_ceiling():
    assert is_raw_upload_path("/api/media/upload")
    assert raw_upload_body_ceiling("/api/media/upload") == media_api.SLICE_BYTES + 64 * 1024


def test_only_this_computers_own_page_gets_in(home):
    assert _post({"action": "overview"}, host="192.168.1.20")[0] == 403
    assert _post({"action": "overview"}, headers={**OWN, "Sec-Fetch-Site": "cross-site"})[0] == 403
    assert _post({"action": "overview"}, headers={**OWN, "Content-Type": "text/plain"})[0] in (403, 415)
    assert _get({"id": ["0" * 32]}, headers={"Sec-Fetch-Site": "cross-site"})[0] == 403
    assert _get({"id": ["0" * 32]}, host="10.0.0.5")[0] == 403
    status, refused = _upload("x" * 32, 0, b"abc", content_type="application/json")
    assert status == 415 and refused["error"] == "content_type"


def test_a_file_added_in_slices_is_sealed_and_opened_for_the_page_only(home):
    lib, _drive, _ = home
    picture = _png()
    item = _add_through_doors(picture, "garden.png", "image/png", slice_bytes=4096)
    assert item["section"] == "local" and item["kind"] == "photo" and item["size"] == len(picture)
    assert all(picture[:64] not in path.read_bytes() for path in lib.root.rglob("*") if path.is_file())

    status, listed = _post({"action": "list", "section": "local", "kind": "photo", "sort": "date"})
    assert status == 200 and [i["id"] for i in listed["items"]] == [item["id"]]
    assert _post({"action": "list", "section": "mobile", "kind": "photo"})[1]["items"] == []

    status, headers, body = _get({"id": [item["id"]]})
    assert status == 200 and body == picture
    assert headers["Cache-Control"].startswith("no-store") and headers["X-Content-Type-Options"] == "nosniff"
    status, headers, body = _get({"id": [item["id"]]}, headers={"Sec-Fetch-Site": "same-origin", "Range": "bytes=100-299"})
    assert status == 206 and body == picture[100:300] and headers["Content-Range"] == f"bytes 100-299/{len(picture)}"
    status, headers, body = _get({"id": [item["id"]], "thumb": ["1"]})
    assert status == 200 and body[:3] == b"\xff\xd8\xff"
    assert _get({"id": ["../../etc/passwd"]})[0] == 404


def test_removal_needs_the_count_said_back(home):
    item = _add_through_doors(b"ID3" + bytes(5000), "note.mp3", "audio/mpeg")
    status, asked = _post({"action": "remove", "ids": [item["id"]]})
    assert status == 409 and asked == {"ok": False, "error": "confirm_needed", "count": 1}
    assert _post({"action": "remove", "ids": [item["id"]], "confirm": 1}) == (200, {"ok": True, "removed": 1})
    assert _post({"action": "list", "section": "local", "kind": "audio"})[1]["items"] == []


def _job() -> dict:
    deadline = time.time() + 30
    while time.time() < deadline:
        job = _post({"action": "job"})[1]["job"]
        if not job["running"]:
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_backup_check_and_restore_run_as_one_job_at_a_time(home):
    lib, drive, remembered = home
    item = _add_through_doors(_png(), "a.png", "image/png")
    status, listed = _post({"action": "drives"})
    assert status == 200 and listed["drives"][0]["has_backup"] is False and listed["settings"]["mirror_removals"] is False

    assert _post({"action": "backup", "drive_id": "stick-1", "create": True, "password": "short"})[1]["error"] == "password_short"
    assert _post({"action": "backup", "drive_id": "stick-1", "create": True, "password": "a long backup password",
                  "confirm_password": "another long password"})[1]["error"] == "password_mismatch"
    status, started = _post({"action": "backup", "drive_id": "stick-1", "create": True, "password": "a long backup password",
                             "confirm_password": "a long backup password"})
    assert status == 200 and started["job"]["kind"] == "backup"
    job = _job()
    assert job["error"] == "" and job["result"]["copied"] == 1 and job["result"]["stopped"] == ""
    assert (drive / mb.FOLDER / "vault.json").exists() and remembered

    status, planned = _post({"action": "plan", "drive_id": "stick-1"})
    assert status == 200 and planned["plan"]["to_copy"] == 0 and planned["plan"]["on_drive"] == 1

    assert _post({"action": "check", "drive_id": "stick-1"})[0] == 200
    assert _job()["result"] == {"ok": True, "damaged": [], "total": 1}

    _post({"action": "remove", "ids": [item["id"]], "confirm": 1})
    assert _post({"action": "restore", "drive_id": "stick-1"})[0] == 200
    assert _job()["result"]["restored"] == 1
    assert len(_post({"action": "list", "section": "local", "kind": "photo"})[1]["items"]) == 1

    remembered.clear()
    assert _post({"action": "check", "drive_id": "stick-1", "password": "not the password at all"})[1]["error"] == "wrong_password"


def test_the_page_draws_and_every_word_it_asks_for_exists():
    from core.i18n.catalog import CATALOGS_DIR
    from core.media_library_fragment import JS

    response = dispatch_get(path="/media", query={}, runtime=RUNTIME, model_name="vool", client_host="127.0.0.1", headers={})
    page = response.body.decode("utf-8")
    assert response.status == 200 and 'id="mediaRoot"' in page and response.headers["Cache-Control"] == "no-store"
    en = json.loads((CATALOGS_DIR / "en.json").read_text(encoding="utf-8"))["messages"]
    asked = set(re.findall(r"\bt\('([a-z_]*[a-z])'", JS)) | set(re.findall(r"'(kind_[a-z]+|sort_[a-z]+)'", JS))
    asked |= {code for code, value in re.findall(r"(\w+):(1|'\w+')", JS.split("const ERR=", 1)[1].split("};", 1)[0]) if value == "1"}
    missing = sorted(key for key in asked if f"medialib.{key}" not in en)
    assert not missing, missing
