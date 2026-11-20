"""The phone's private capture hands photos, videos and recordings to the paired computer over the real sealed listener.

Owner request, 2026-11-17: what VOOL captures must never reach the phone's gallery (so Google or Apple never
see it). The phone keeps it encrypted, sends it full quality to the computer, and deletes its copy
only when the computer answers with the same SHA-256. These tests drive the actual listener.
"""
import base64
import hashlib
import os

from tests.mobile.test_phone_listener_served import desktop as desktop
from tests.mobile.test_phone_listener_served import http
from tests.mobile.test_phone_notifications import call
from tests.mobile.test_sealed_transport import pair

PHOTO = b"\xff\xd8\xff\xe1" + os.urandom(2_000_000) + b"\xff\xd9"
SHA = hashlib.sha256(PHOTO).hexdigest()


def _camera_phone(desktop, **extra):
    phone = pair(desktop)
    rules = {"camera": True, "attachments": True, **extra}
    assert http(desktop[0], "/api/mobile/settings", {"operation": "device_rules", "device_id": phone[3]["device_id"], "rules": rules})[0] == 200
    return phone


def _send(phone, data, start=0, sha=SHA, finish=True, media_type="image/jpeg"):
    status, value = call(*phone, "capture.begin", {"sha256": sha, "size_bytes": len(data), "media_type": media_type, "taken_at": 1795000000})
    assert status == 200, value
    begun = value["result"]
    if begun["done"]:
        return begun
    offset = max(start, begun["next_offset"])
    step = begun["chunk_bytes"]
    while offset < len(data):
        piece = data[offset:offset + step]
        status, value = call(*phone, "capture.chunk", {"upload_id": begun["upload_id"], "offset": offset, "content_b64": base64.b64encode(piece).decode()})
        assert status == 200, value
        offset = value["result"]["next_offset"]
    if not finish:
        return begun
    return call(*phone, "capture.finish", {"upload_id": begun["upload_id"]})


def test_photo_arrives_whole_and_the_hash_comes_back(desktop):
    phone = _camera_phone(desktop)
    status, value = _send(phone, PHOTO)
    assert status == 200, value
    result = value["result"]
    assert result["done"] is True and result["sha256"] == SHA and result["size_bytes"] == len(PHOTO)
    saved = list((desktop[2] / "phone-camera").rglob("*.jpg"))
    assert len(saved) == 1 and saved[0].name == result["saved_as"]
    assert saved[0].read_bytes() == PHOTO
    assert oct(saved[0].stat().st_mode & 0o777) == "0o600"


def test_retry_after_a_lost_answer_never_makes_a_second_copy(desktop):
    phone = _camera_phone(desktop)
    assert _send(phone, PHOTO)[0] == 200
    again = _send(phone, PHOTO)
    assert again["done"] is True and again["sha256"] == SHA
    assert len(list((desktop[2] / "phone-camera").rglob("*.jpg"))) == 1


def test_a_half_sent_photo_carries_on_from_where_it_stopped(desktop):
    phone = _camera_phone(desktop)
    status, value = call(*phone, "capture.begin", {"sha256": SHA, "size_bytes": len(PHOTO), "media_type": "image/jpeg"})
    begun = value["result"]
    piece = PHOTO[:begun["chunk_bytes"]]
    assert call(*phone, "capture.chunk", {"upload_id": begun["upload_id"], "offset": 0, "content_b64": base64.b64encode(piece).decode()})[0] == 200
    # the phone went offline here; later it starts over and the computer says how far it got
    status, value = call(*phone, "capture.begin", {"sha256": SHA, "size_bytes": len(PHOTO), "media_type": "image/jpeg"})
    assert value["result"]["next_offset"] == len(piece)
    # a piece sent at the wrong place is refused with the place to carry on from
    status, value = call(*phone, "capture.chunk", {"upload_id": begun["upload_id"], "offset": 0, "content_b64": base64.b64encode(piece).decode()})
    assert status == 409
    status, value = _send(phone, PHOTO)
    assert status == 200 and value["result"]["sha256"] == SHA


def test_a_damaged_photo_is_not_saved_and_the_phone_keeps_its_copy(desktop):
    phone = _camera_phone(desktop)
    damaged = PHOTO[:-1] + b"\x00"
    status, value = _send(phone, damaged)  # declares the true photo's hash, delivers other bytes
    assert status == 422 and value["code"] == "capture_hash_mismatch"
    assert "stays on your phone" in value["error"]
    assert not list((desktop[2] / "phone-camera").rglob("*.jpg"))
    # the partial is gone, so the next try starts from zero
    status, value = call(*phone, "capture.begin", {"sha256": SHA, "size_bytes": len(PHOTO), "media_type": "image/jpeg"})
    assert value["result"]["next_offset"] == 0


def test_camera_is_off_until_the_computer_turns_it_on(desktop):
    phone = pair(desktop)
    status, value = call(*phone, "capture.begin", {"sha256": SHA, "size_bytes": len(PHOTO), "media_type": "image/jpeg"})
    assert status == 403
    assert not (desktop[2] / "phone-camera").exists()


def test_only_camera_jpegs_are_taken(desktop):
    phone = _camera_phone(desktop)
    for params in ({"sha256": SHA, "size_bytes": 10, "media_type": "application/x-sh"},
                   {"sha256": "nothex", "size_bytes": 10, "media_type": "image/jpeg"},
                   {"sha256": SHA, "size_bytes": 0, "media_type": "image/jpeg"},
                   {"sha256": SHA, "size_bytes": 65 * 1024 * 1024, "media_type": "image/jpeg"}):
        assert call(*phone, "capture.begin", params)[0] in (400, 413)
    script = b"#!/bin/sh\necho hi\n"
    status, value = _send(phone, script, sha=hashlib.sha256(script).hexdigest())
    assert status == 400 and value["code"] == "capture_type_not_allowed"
    assert not list((desktop[2] / "phone-camera").rglob("*"))


VIDEO = b"\x00\x00\x00\x18ftypmp42" + os.urandom(3_000_000)
SOUND = b"\x00\x00\x00\x1cftypM4A " + os.urandom(400_000)


def test_video_and_sound_arrive_the_same_way(desktop):
    phone = _camera_phone(desktop, microphone=True)
    for data, media_type, ending in ((VIDEO, "video/mp4", ".mp4"), (SOUND, "audio/mp4", ".m4a")):
        status, value = _send(phone, data, sha=hashlib.sha256(data).hexdigest(), media_type=media_type)
        assert status == 200, value
        assert value["result"]["saved_as"].endswith(ending)
    saved = {p.suffix: p.read_bytes() for p in (desktop[2] / "phone-camera").rglob("*") if p.is_file()}
    assert saved == {".mp4": VIDEO, ".m4a": SOUND}


HEIC = b"\x00\x00\x00\x18ftypheic" + os.urandom(900_000)
OGG = b"OggS\x00\x02" + os.urandom(200_000)


def test_compressed_photos_and_sound_keep_their_own_kind(desktop):
    phone = _camera_phone(desktop, microphone=True)
    for data, media_type, ending in ((HEIC, "image/heic", ".heic"), (OGG, "audio/ogg", ".ogg")):
        status, value = _send(phone, data, sha=hashlib.sha256(data).hexdigest(), media_type=media_type)
        assert status == 200, value
        assert value["result"]["saved_as"].endswith(ending)
    other_heic = b"\x00\x00\x00\x18ftypmif1" + os.urandom(50_000)
    for data, media_type in ((VIDEO, "image/heic"), (other_heic, "video/mp4"), (VIDEO, "audio/ogg")):
        status, value = _send(phone, data, sha=hashlib.sha256(data).hexdigest(), media_type=media_type)
        assert status == 400 and value["code"] == "capture_type_not_allowed", (media_type, value)


def test_a_recording_needs_the_microphone_right(desktop):
    phone = _camera_phone(desktop)  # camera on, microphone off
    status, value = call(*phone, "capture.begin", {"sha256": hashlib.sha256(SOUND).hexdigest(), "size_bytes": len(SOUND), "media_type": "audio/mp4"})
    assert status == 403 and "permissions on your computer" in value["error"]


def test_a_video_cannot_pretend_to_be_a_photo(desktop):
    phone = _camera_phone(desktop)
    status, value = _send(phone, VIDEO, sha=hashlib.sha256(VIDEO).hexdigest(), media_type="image/jpeg")
    assert status == 400 and value["code"] == "capture_type_not_allowed"
