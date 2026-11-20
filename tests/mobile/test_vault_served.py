"""VOOL Vault: the paired phone browses and opens this computer's protected media library over the real sealed
listener, only with its own "See protected media" right, piece by piece, each opening kept as a receipt."""
import base64
import hashlib
import io
import os

from tests.mobile.test_phone_listener_served import desktop as desktop
from tests.mobile.test_phone_listener_served import http
from tests.mobile.test_phone_notifications import call
from tests.mobile.test_private_capture_served import _receipts, _send
from tests.mobile.test_sealed_transport import pair


def _phone(desktop, **rules):
    phone = pair(desktop)
    wanted = {"camera": True, "attachments": True, **rules}
    assert http(desktop[0], "/api/mobile/settings", {"operation": "device_rules", "device_id": phone[3]["device_id"], "rules": wanted})[0] == 200
    return phone


def _jpeg() -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.frombytes("RGB", (640, 480), os.urandom(640 * 480 * 3)).save(out, format="JPEG", quality=95)
    return out.getvalue()



def test_the_vault_needs_its_own_right(desktop):
    phone = _phone(desktop)
    status, value = call(*phone, "media.list", {"section": "mobile", "kind": "photo"})
    assert status == 403 and value["code"] == "phone_right_off_or_expired", value


def test_a_photo_sent_from_the_phone_is_listed_previewed_and_streamed_back_whole(desktop):
    phone = _phone(desktop, media=True)
    photo = _jpeg()
    sha = hashlib.sha256(photo).hexdigest()
    assert _send(phone, photo, sha=sha)[0] == 200

    status, listed = call(*phone, "media.list", {"section": "mobile", "kind": "photo"})
    assert status == 200 and listed["result"]["total"] == 1, listed
    item = listed["result"]["items"][0]
    assert item["kind"] == "photo" and item["size"] == len(photo) and "path" not in item and "sha256" not in item

    status, thumb = call(*phone, "media.thumb", {"id": item["id"]})
    assert status == 200 and base64.b64decode(thumb["result"]["content_b64"])[:3] == b"\xff\xd8\xff"

    pieces, offset = [], 0
    while True:
        status, piece = call(*phone, "media.read", {"id": item["id"], "offset": offset, "length": 100_000})
        assert status == 200, piece
        pieces.append(base64.b64decode(piece["result"]["content_b64"]))
        offset = piece["result"]["next_offset"]
        if piece["result"]["done"]:
            break
    assert b"".join(pieces) == photo and len(pieces) > 1
    # a player asking from the middle gets exactly that part
    status, middle = call(*phone, "media.read", {"id": item["id"], "offset": 12345, "length": 999})
    assert base64.b64decode(middle["result"]["content_b64"]) == photo[12345:13344]
    assert [r["detail"]["item"] for r in _receipts("media.opened_on_phone")] == [item["id"]]

    assert call(*phone, "media.exported", {"id": item["id"], "how": "share"})[0] == 200
    assert _receipts("media.exported_on_phone")[-1]["detail"] == {"item": item["id"], "how": "share"}


def test_unknown_and_bad_requests_are_refused_plainly(desktop):
    phone = _phone(desktop, media=True)
    assert call(*phone, "media.read", {"id": "0" * 32})[0] == 404
    assert call(*phone, "media.list", {"kind": "documents"})[0] == 400
    assert call(*phone, "media.exported", {"id": "x", "how": "print"})[0] == 400


def test_a_whole_phone_piece_crosses_the_sealed_link(desktop):
    """The phone asks for 768 KiB at a time; one such piece must fit through the sealed link whole."""
    from PIL import Image

    out = io.BytesIO()
    Image.frombytes("RGB", (1400, 1000), os.urandom(1400 * 1000 * 3)).save(out, format="JPEG", quality=95)
    photo = out.getvalue()
    assert len(photo) > 2 * 768 * 1024
    phone = _phone(desktop, media=True)
    assert _send(phone, photo, sha=hashlib.sha256(photo).hexdigest())[0] == 200
    item = call(*phone, "media.list", {"section": "mobile", "kind": "photo"})[1]["result"]["items"][0]
    status, piece = call(*phone, "media.read", {"id": item["id"], "offset": 768 * 1024, "length": 768 * 1024})
    assert status == 200, piece
    assert base64.b64decode(piece["result"]["content_b64"]) == photo[768 * 1024:2 * 768 * 1024]


def _small_jpeg() -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (320, 240), (40, 90, 160)).save(out, format="JPEG", quality=80)
    return out.getvalue()


def test_the_phone_gives_a_preview_the_computer_could_not_make_and_it_is_kept_once(desktop):
    from core.media_library.store import library

    phone = _phone(desktop, media=True)
    photo = _jpeg()
    assert _send(phone, photo, sha=hashlib.sha256(photo).hexdigest())[0] == 200
    item = call(*phone, "media.list", {"section": "mobile", "kind": "photo"})[1]["result"]["items"][0]
    preview = _small_jpeg()
    # the computer already made one: the phone's is not taken in its place
    assert call(*phone, "media.set_thumb", {"id": item["id"], "content_b64": base64.b64encode(preview).decode()})[1]["result"] == {"kept": False}
    library()._thumb_path(item["id"]).unlink()  # as for a HEIC photo this computer cannot open
    assert call(*phone, "media.thumb", {"id": item["id"]})[0] == 404
    assert call(*phone, "media.set_thumb", {"id": item["id"], "content_b64": base64.b64encode(preview).decode()})[1]["result"] == {"kept": True}
    assert base64.b64decode(call(*phone, "media.thumb", {"id": item["id"]})[1]["result"]["content_b64"]) == preview
    for bad in (b"not a jpeg", b"\xff\xd8\xff" + os.urandom(200_000)):
        assert call(*phone, "media.set_thumb", {"id": item["id"], "content_b64": base64.b64encode(bad).decode()})[0] == 400


def test_the_trace_keeps_what_happened_never_what_was_in_it(desktop):
    from core.mobile_companion import trace

    phone = _phone(desktop, media=True)
    photo = _jpeg()
    assert _send(phone, photo, sha=hashlib.sha256(photo).hexdigest())[0] == 200
    item = call(*phone, "media.list", {"section": "mobile", "kind": "photo"})[1]["result"]["items"][0]
    assert call(*phone, "media.read", {"id": item["id"], "offset": 0, "length": 5000})[0] == 200
    assert call(*phone, "trace.phone", {"lines": ["vault.open from=icon", "shutter press ms=12"]})[0] == 200
    text = trace._path().read_text()
    lines = [__import__("json").loads(line) for line in text.splitlines()]
    read = [line for line in lines if line.get("action") == "media.read"][-1]
    assert read["status"] == 200 and read["item"] == item["id"][:8] and read["offset"] == 0 and read["length"] == 5000
    assert {"from": "phone", "line": "shutter press ms=12"}.items() <= [l for l in lines if l.get("from") == "phone"][-1].items()
    assert item["name"] not in text and "content_b64" not in text and item["id"] not in text
    assert call(*phone, "trace.phone", {"lines": ["x"] * 201})[0] == 400


def test_starred_items_are_kept_sealed_and_found_quickly(desktop):
    from core.media_library.store import library

    phone = _phone(desktop, media=True)
    photos = [_jpeg(), _jpeg()]
    for photo in photos:
        assert _send(phone, photo, sha=hashlib.sha256(photo).hexdigest())[0] == 200
    items = call(*phone, "media.list", {"section": "mobile", "kind": "photo"})[1]["result"]["items"]
    assert [item["favorite"] for item in items] == [False, False]
    first = items[0]["id"]

    status, value = call(*phone, "media.favorite", {"ids": [first, first, "0" * 32], "on": True})
    assert status == 200 and value["result"] == {"changed": 1, "missing": 1, "on": True}, value
    starred = call(*phone, "media.list", {"section": "mobile", "kind": "photo", "favorites": True})[1]["result"]
    assert starred["total"] == 1 and starred["items"][0]["id"] == first and starred["items"][0]["favorite"] is True
    # the star lives in the item's sealed details: a fresh read of the library still has it, and no plain text holds it
    lib = library()
    lib._index = None
    assert lib.get(first)["favorite"] is True
    assert b"favorite" not in lib._meta_path(first).read_bytes()

    assert call(*phone, "media.favorite", {"ids": [first], "on": False})[1]["result"]["changed"] == 1
    assert call(*phone, "media.list", {"section": "mobile", "kind": "photo", "favorites": True})[1]["result"]["total"] == 0
    for bad in ({"ids": [], "on": True}, {"ids": "x", "on": True}, {"ids": ["a"] * 201, "on": True}, {"ids": [1], "on": True}):
        assert call(*phone, "media.favorite", bad)[0] == 400


def test_stars_need_the_vault_right(desktop):
    phone = _phone(desktop)
    status, value = call(*phone, "media.favorite", {"ids": ["0" * 32], "on": True})
    assert status == 403 and value["code"] == "phone_right_off_or_expired", value
