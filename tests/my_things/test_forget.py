from __future__ import annotations

import base64

from core.artifact_readers.vision_tool import ocr_available
from tests.my_things.test_match import label_photo
from tests.my_things.test_served import call, daemon, file


def test_served_broad_record_erasure_needs_exact_revisioned_confirmation(daemon):
    base, _ = daemon
    one = call(base, "save", {"fields": {"name": "Amazon kettle"}, "confirm": True})[1]["thing"]
    preview = call(base, "forget", {"target": "Amazon"})[1]
    assert preview["requires_confirmation"] and preview["removed"] == 0
    assert preview["matches"] == [{"id": one["id"], "revision": 1, "name": "Amazon kettle"}]
    assert one["id"] in {r["id"] for r in call(base)[1]["things"]}
    assert call(base, "forget", {"target": "Amazon", "confirm": True})[0] == 409
    two = call(base, "save", {"fields": {"name": "Amazon toaster"}, "confirm": True})[1]["thing"]
    assert call(base, "forget", {"target": "Amazon", "confirm": True, "expected": preview["matches"]})[0] == 409
    assert {one["id"], two["id"]} <= {r["id"] for r in call(base)[1]["things"]}
    updated = call(base, "save", {"id": one["id"], "revision": 1, "fields": {"name": "Amazon new kettle"}, "confirm": True})[1]["thing"]
    assert updated["revision"] == 2
    current = call(base, "forget", {"target": "Amazon"})[1]
    removed = call(base, "forget", {"target": "Amazon", "confirm": True, "expected": current["matches"]})[1]
    assert removed["status"] == "verified" and removed["removed"] == 2
    assert {r["name"] for r in removed["removed_things"]} == {"Amazon new kettle", "Amazon toaster"}
    assert "Removed 2" in removed["message"] and "Nothing was removed" not in removed["message"]


def test_served_forget_uses_cached_text_and_unreadable_names_without_blocking(daemon):
    base, home = daemon
    marker = "CYPRESS-42819"
    target = call(base, "save", {"fields": {"name": "Receipt-only match"}, "file": file("receipt.txt", marker), "confirm": True})[1]["thing"]
    opaque = call(base, "save", {"fields": {"name": "Keep unreadable manual"}, "file": file("unknown.bin", b"\xff\x00opaque", "manual"), "confirm": True})[1]["thing"]
    named = call(base, "save", {"fields": {"name": "Filename-only match"}, "file": file("ZQX-lamp-receipt.png", b"\x89PNG\x00broken", "photo"), "confirm": True})[1]["thing"]
    assert set(target["files"][0]) == {"id", "name", "kind", "size"}, "cached text must stay private"
    for token, item in [(marker, target), ("ZQX", named)]:
        preview = call(base, "forget", {"target": token})[1]
        assert preview["status"] == "confirmation_required" and preview["removed"] == 0
        assert [r["id"] for r in preview["matches"]] == [item["id"]]
        result = call(base, "forget", {"target": token, "confirm": True, "expected": preview["matches"]})[1]
        assert result["status"] == "verified" and result["removed"] == 1
        assert result["removed_things"][0]["name"] == item["name"]
        assert call(base, "forget", {"target": token})[1]["status"] == "verified"
    assert opaque["id"] in {r["id"] for r in call(base)[1]["things"]}
    assert not list((home / "data/my_things/read_scratch").rglob("input.png"))
    for path in home.rglob("*"):
        if path.is_file():
            raw = path.read_bytes()
            assert marker.encode() not in raw and marker.encode("utf-16le") not in raw
            assert base64.b64encode(marker.encode()) not in raw
    call(base, "delete", {"id": opaque["id"], "revision": 1, "confirm": True})


def test_served_saved_photo_words_are_encrypted_and_matched_at_forget_time(daemon):
    base, home = daemon
    photo = label_photo("MODEL PINE-87219")
    item = call(base, "save", {"fields": {"name": "Saved photo"}, "file": file("photo.png", photo, "photo"), "confirm": True})[1]["thing"]
    preview = call(base, "forget", {"target": "PINE-87219"})[1]
    if ocr_available():
        assert preview["matches"] == [{"id": item["id"], "revision": 1, "name": "Saved photo"}]
        result = call(base, "forget", {"target": "PINE-87219", "confirm": True, "expected": preview["matches"]})[1]
        assert result["removed"] == 1 and "Saved photo" in result["message"]
    else:
        assert preview["status"] == "verified" and not preview["matches"]
        assert call(base, "delete", {"id": item["id"], "revision": 1, "confirm": True})[0] == 200
    assert all(b"PINE-87219" not in f.read_bytes() for f in home.rglob("*") if f.is_file())
    assert not list((home / "data/my_things/read_scratch").rglob("*"))


def test_served_empty_or_short_forget_never_changes_records(daemon):
    base, _ = daemon
    before = call(base)[1]["things"]
    for target in ["", "A", None, []]:
        assert call(base, "forget", {"target": target, "confirm": True, "expected": []})[0] == 400
        assert call(base)[1]["things"] == before
