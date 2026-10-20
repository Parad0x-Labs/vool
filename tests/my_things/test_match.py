from __future__ import annotations

import io

from PIL import Image, ImageDraw, ImageFont

from core.my_things.reading import match_photo, match_text, proposal
from tests.my_things.test_served import call, daemon, file


def records():
    return [{"id": "a", "name": "Headphones", "model": "WH-1000", "serial": "SN-48105"}, {"id": "b", "name": "Radio", "model": "RT-4821", "serial": ""}]


def label_photo(text="MODEL WH-1000"):
    image = Image.new("RGB", (1100, 240), "white")
    ImageDraw.Draw(image).text((50, 70), text, fill="black", font=ImageFont.load_default(size=58))
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def test_exact_match_has_quote_and_partial_model_is_not_a_match():
    result = match_text("Model: WH-1000", records())
    assert result["state"] == "match" and result["candidates"][0]["id"] == "a"
    assert result["candidates"][0]["evidence"] == [{"field": "model", "saved": "WH-1000", "quote": "Model: WH-1000"}]
    assert match_text("Model: WH-10000", records())["state"] == "no_match"
    assert match_text("No identifiers here", records())["state"] == "no_match"
    assert match_text("Serial: SN-48105", records())["candidates"][0]["evidence"][0]["field"] == "serial"


def test_two_saved_things_require_a_choice():
    both = [*records(), {"id": "c", "name": "Second pair", "model": "WH-1000"}]
    result = match_text("WH-1000", both)
    assert result["state"] == "choose" and len(result["candidates"]) == 2
    assert all(candidate["evidence"] for candidate in result["candidates"])


def test_receipt_proposal_does_not_invent_a_warranty():
    result = proposal(b"Item: Kettle\nModel: KX-4822\nTotal: 35.20\nDate: March", "receipt.txt")
    assert result["requires_confirmation"] is True
    assert result["fields"] == {"name": "Kettle", "model": "KX-4822"}
    assert all(e["quote"] in result["reading"]["text"] for e in result["evidence"])


def test_unreadable_bytes_never_create_a_candidate():
    result = match_photo(b"\x89PNG\x00broken", "bad.png", records())
    assert result["state"] in {"unreadable", "unavailable"}
    assert result["candidates"] == [] and result["message"]


def test_served_match_exact_ambiguous_no_match_and_actual_photo(daemon):
    base, home = daemon
    one = call(base, "save", {"fields": {"name": "Synthetic headphones", "model": "WH-1000"}, "confirm": True})[1]["thing"]
    result = call(base, "match", {"file": file("label.txt", "Model: WH-1000", "photo")})[1]
    assert result["state"] == "match" and result["candidates"][0]["evidence"][0]["quote"] == "Model: WH-1000"
    result = call(base, "match", {"file": file("label.png", label_photo(), "photo")})[1]
    # This exercises the real platform's capability. An unavailable OCR engine is an
    # explicit product path, never a fabricated successful photo match.
    assert result["state"] in {"match", "unavailable", "unreadable"}, result
    if result["state"] == "match":
        assert result["candidates"][0]["id"] == one["id"] and result["candidates"][0]["evidence"]
    else:
        assert result["candidates"] == []
    import json
    import os
    from pathlib import Path

    evidence = os.environ.get("VOOL_THINGS_PHOTO_RESULT")
    if evidence:
        Path(evidence).write_text(json.dumps(result, indent=2))
    two = call(base, "save", {"fields": {"name": "Another pair", "model": "WH-1000"}, "confirm": True})[1]["thing"]
    assert call(base, "match", {"file": file("label.txt", "WH-1000", "photo")})[1]["state"] == "choose"
    assert call(base, "match", {"file": file("none.txt", "ZX-74192", "photo")})[1]["state"] == "no_match"
    result = call(base, "match", {"file": file("bad.png", b"\x89PNG\x00broken", "photo")})[1]
    assert result["state"] in {"unavailable", "unreadable"} and not result["candidates"]
    for record in (one, two):
        assert call(base, "delete", {"id": record["id"], "revision": 1, "confirm": True})[0] == 200


def test_served_ocr_scratch_is_private_and_removed_on_read_and_empty_photo(daemon):
    import time
    from concurrent.futures import ThreadPoolExecutor

    from core.artifact_readers.vision_tool import ocr_available

    base, home = daemon
    root = home / "data" / "my_things" / "read_scratch"
    observed = []
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(call, base, "read", {"file": file("label.png", label_photo(), "photo")})
        while not future.done():
            for path in root.rglob("input.png") if root.exists() else []:
                try:
                    assert path.resolve().is_relative_to(home.resolve())
                    assert path.stat().st_mode & 0o777 == 0o600
                    assert path.parent.stat().st_mode & 0o777 == 0o700
                    observed.append(str(path.relative_to(home)))
                except FileNotFoundError:
                    pass  # completion may remove it between directory listing and stat
            time.sleep(.005)
        status, result, _ = future.result()
    assert status == 200
    if ocr_available():
        assert result["reading"]["state"] == "read" and observed
    else:
        assert result["reading"]["state"] == "unavailable" and not observed
    assert not list(root.rglob("input.png"))
    empty = io.BytesIO()
    Image.new("RGB", (300, 100), "white").save(empty, format="PNG")
    result = call(base, "read", {"file": file("empty.png", empty.getvalue(), "photo")})[1]
    assert result["reading"]["state"] == ("unreadable" if ocr_available() else "unavailable")
    assert not list(root.rglob("*"))
    assert all(b"MODEL WH-1000" not in path.read_bytes() for path in home.rglob("*") if path.is_file())
