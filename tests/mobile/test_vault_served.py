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
