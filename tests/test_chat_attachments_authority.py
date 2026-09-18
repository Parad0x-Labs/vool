"""The ONE attachment authority: validation, staging, turn binding, lifecycle, cleanup.

Every attachment that reaches the runtime passes through `core.chat_attachments`. Nothing else
reads the browser's file name as a path, sniffs a type, or decides what a model may see. These
tests are built around the hostile inputs the door will actually meet: traversal-shaped names,
symlinks planted in the staging area, executables wearing a `.txt`, images whose bytes disagree
with their extension, oversized payloads, and a second chat trying to spend another chat's files.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import struct
import zlib
from pathlib import Path

import pytest

from core import runtime_paths

pytestmark = pytest.mark.usefixtures("_isolated_home")


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    yield
    runtime_paths.configure_runtime_home(None)


SESSION_A = "openclaw:aaaaaaaaaaaaaaaaaaaa"
SESSION_B = "openclaw:bbbbbbbbbbbbbbbbbbbb"


# --- byte fixtures -------------------------------------------------------------------------------


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)


def tiny_png(*, with_private_metadata: bool = False) -> bytes:
    """A real 1x1 PNG; optionally carrying tEXt / eXIf chunks a camera or editor would leave."""
    ihdr = _png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    raw = zlib.compress(b"\x00\xff\x00\x00")
    idat = _png_chunk(b"IDAT", raw)
    parts = [b"\x89PNG\r\n\x1a\n", ihdr]
    if with_private_metadata:
        parts.append(_png_chunk(b"tEXt", b"Author\x00Jane Fixture <jane@example.test>"))
        parts.append(_png_chunk(b"eXIf", b"Exif\x00\x00GPSLatitude=51.5074"))
    parts.append(idat)
    parts.append(_png_chunk(b"IEND", b""))
    return b"".join(parts)


def tiny_jpeg(*, with_exif: bool = False) -> bytes:
    """Structurally valid JPEG framing (SOI, optional APP1 Exif, a DQT, SOS, EOI)."""
    segments = [b"\xff\xd8"]
    if with_exif:
        exif = b"Exif\x00\x00MM\x00*GPSLatitudeRef=N;SerialNumber=CAM-0042"
        segments.append(b"\xff\xe1" + struct.pack(">H", len(exif) + 2) + exif)
    dqt = bytes(64)
    segments.append(b"\xff\xdb" + struct.pack(">H", len(dqt) + 3) + b"\x00" + dqt)
    scan = b"\x01\x01\x00\x00\x3f\x00"
    segments.append(b"\xff\xda" + struct.pack(">H", len(scan) + 2) + scan + b"\x12\x34")
    segments.append(b"\xff\xd9")
    return b"".join(segments)


def tiny_webp(*, with_exif: bool = False) -> bytes:
    vp8 = b"VP8 " + struct.pack("<I", 4) + b"\x00\x00\x00\x00"
    chunks = vp8
    if with_exif:
        exif_payload = b"MM\x00*GPSAltitude=12"
        chunks += b"EXIF" + struct.pack("<I", len(exif_payload)) + exif_payload + (b"\x00" if len(exif_payload) % 2 else b"")
    body = b"WEBP" + chunks
    return b"RIFF" + struct.pack("<I", len(body)) + body


def tiny_gif() -> bytes:
    return b"GIF89a" + b"\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff" + b"\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b"


# --- the contract --------------------------------------------------------------------------------


def _authority():
    from core import chat_attachments

    return chat_attachments


def test_limits_are_stated_once_and_carried_to_the_client() -> None:
    a = _authority()
    limits = a.limits_payload()
    assert limits["max_files_per_turn"] == a.MAX_FILES_PER_TURN >= 4
    assert limits["max_bytes_per_file"] == a.MAX_BYTES_PER_FILE >= 4 * 1024 * 1024
    assert limits["max_bytes_per_turn"] == a.MAX_BYTES_PER_TURN >= limits["max_bytes_per_file"]
    # The accept string the native picker uses is derived from the SAME tables the server checks.
    assert ".txt" in limits["text_extensions"] and ".py" in limits["text_extensions"]
    assert "image/png" in limits["image_types"] and "image/jpeg" in limits["image_types"]
    for ext in limits["text_extensions"]:
        assert ext in limits["accept"]
    for media_type in limits["image_types"]:
        assert media_type in limits["accept"]
    # A file extension that can carry secrets by convention is not an attachment type.
    assert ".env" not in limits["text_extensions"]


def test_a_text_file_stages_under_a_generated_id_never_under_its_own_name() -> None:
    a = _authority()
    record = a.stage_attachment(
        session_id=SESSION_A, declared_name="notes.txt", declared_type="text/plain", data=b"hello attachments\n"
    )
    assert record["id"].startswith("att_") and len(record["id"]) == 4 + 32
    assert record["kind"] == "text"
    assert record["name"] == "notes.txt"
    assert record["size_bytes"] == len(b"hello attachments\n")
    assert record["sha256"] == hashlib.sha256(b"hello attachments\n").hexdigest()
    assert record["state"] == "staged"
    # Nothing in the public record is a filesystem path.
    assert str(a.stage_dir()) not in json.dumps(record)
    assert not any(isinstance(v, str) and v.startswith(("/", "~", "\\")) for v in record.values())
    # On disk the bytes live under the id; the client's name is never a path component.
    on_disk = sorted(p.name for p in a.stage_dir().iterdir())
    assert all(name.startswith(record["id"]) for name in on_disk), on_disk
    assert not any("notes" in name for name in on_disk)


def test_traversal_shaped_names_are_refused_not_sanitized_into_acceptance() -> None:
    a = _authority()
    for hostile in ("../../etc/passwd", "..\\..\\boot.ini", "/etc/hosts.txt", "a/b.txt", "nul\x00l.txt", "..", "."):
        with pytest.raises(a.AttachmentRefused) as info:
            a.stage_attachment(session_id=SESSION_A, declared_name=hostile, declared_type="text/plain", data=b"x")
        assert info.value.code == "name_rejected", hostile
    assert list(a.stage_dir().glob("*")) == [] if a.stage_dir().exists() else True


def test_oversized_and_empty_files_are_refused_with_the_limit_named() -> None:
    a = _authority()
    with pytest.raises(a.AttachmentRefused) as info:
        a.stage_attachment(
            session_id=SESSION_A, declared_name="big.txt", declared_type="text/plain", data=b"x" * (a.MAX_BYTES_PER_FILE + 1)
        )
    assert info.value.code == "too_large"
    assert str(a.MAX_BYTES_PER_FILE // (1024 * 1024)) in info.value.message
    with pytest.raises(a.AttachmentRefused) as info:
        a.stage_attachment(session_id=SESSION_A, declared_name="empty.txt", declared_type="text/plain", data=b"")
    assert info.value.code == "empty_file"


def test_the_per_turn_count_and_byte_budgets_hold_across_uploads() -> None:
    a = _authority()
    for index in range(a.MAX_FILES_PER_TURN):
        a.stage_attachment(session_id=SESSION_A, declared_name=f"f{index}.txt", declared_type="text/plain", data=b"ok")
    with pytest.raises(a.AttachmentRefused) as info:
        a.stage_attachment(session_id=SESSION_A, declared_name="one-too-many.txt", declared_type="text/plain", data=b"ok")
    assert info.value.code == "too_many"
    # A second chat has its own budget: the count is per chat, not global.
    a.stage_attachment(session_id=SESSION_B, declared_name="fine.txt", declared_type="text/plain", data=b"ok")


def test_unsupported_types_and_executable_masquerades_are_refused() -> None:
    a = _authority()
    with pytest.raises(a.AttachmentRefused) as info:
        a.stage_attachment(session_id=SESSION_A, declared_name="tool.exe", declared_type="application/octet-stream", data=b"MZ\x90\x00")
    assert info.value.code == "unsupported_type"
    # A PE header wearing .txt is not text, whatever the extension says.
    with pytest.raises(a.AttachmentRefused) as info:
        a.stage_attachment(session_id=SESSION_A, declared_name="readme.txt", declared_type="text/plain", data=b"MZ\x90\x00\x03\x00\x00\x00")
    assert info.value.code == "executable_masquerade"
    with pytest.raises(a.AttachmentRefused) as info:
        a.stage_attachment(session_id=SESSION_A, declared_name="readme.md", declared_type="text/markdown", data=b"\x7fELF\x02\x01\x01")
    assert info.value.code == "executable_masquerade"
    with pytest.raises(a.AttachmentRefused) as info:
        a.stage_attachment(session_id=SESSION_A, declared_name="notes.txt", declared_type="text/plain", data=b"#!/bin/sh\nrm -rf /\n")
    assert info.value.code == "executable_masquerade"
    # A shebang inside a script KIND is that kind's ordinary shape.
    record = a.stage_attachment(session_id=SESSION_A, declared_name="run.sh", declared_type="text/x-sh", data=b"#!/bin/sh\necho ok\n")
    assert record["kind"] == "text"
    # Binary garbage in a .txt is unsupported content, named as such.
    with pytest.raises(a.AttachmentRefused) as info:
        a.stage_attachment(session_id=SESSION_A, declared_name="blob.txt", declared_type="text/plain", data=b"\x00\x01\x02\xff\xfe")
    assert info.value.code == "not_text"


def test_image_bytes_must_agree_with_the_extension_and_the_declared_type() -> None:
    a = _authority()
    # JPEG bytes wearing .png: forged extension.
    with pytest.raises(a.AttachmentRefused) as info:
        a.stage_attachment(session_id=SESSION_A, declared_name="photo.png", declared_type="image/png", data=tiny_jpeg())
    assert info.value.code == "content_mismatch"
    # Text wearing .jpg.
    with pytest.raises(a.AttachmentRefused) as info:
        a.stage_attachment(session_id=SESSION_A, declared_name="photo.jpg", declared_type="image/jpeg", data=b"not an image at all")
    assert info.value.code == "content_mismatch"
    # A declared MIME that contradicts the kind (an image claiming to be text) is a forgery too.
    with pytest.raises(a.AttachmentRefused) as info:
        a.stage_attachment(session_id=SESSION_A, declared_name="photo.png", declared_type="text/plain", data=tiny_png())
    assert info.value.code == "declared_type_mismatch"
    # An empty declared type (many OS pickers) is fine: the bytes decide.
    record = a.stage_attachment(session_id=SESSION_A, declared_name="photo.png", declared_type="", data=tiny_png())
    assert record["kind"] == "image" and record["media_type"] == "image/png"
    for name, data, media in (("a.jpg", tiny_jpeg(), "image/jpeg"), ("a.webp", tiny_webp(), "image/webp"), ("a.gif", tiny_gif(), "image/gif")):
        record = a.stage_attachment(session_id=SESSION_B, declared_name=name, declared_type=media, data=data)
        assert record["media_type"] == media


def test_private_image_metadata_never_persists_and_never_reaches_a_model() -> None:
    a = _authority()
    png = a.stage_attachment(session_id=SESSION_A, declared_name="cam.png", declared_type="image/png", data=tiny_png(with_private_metadata=True))
    jpg = a.stage_attachment(session_id=SESSION_A, declared_name="cam.jpg", declared_type="image/jpeg", data=tiny_jpeg(with_exif=True))
    webp = a.stage_attachment(session_id=SESSION_A, declared_name="cam.webp", declared_type="image/webp", data=tiny_webp(with_exif=True))
    a.bind_to_turn(session_id=SESSION_A, turn_id="turn-1", attachment_ids=[png["id"], jpg["id"], webp["id"]])
    for path in a.stage_dir().glob("*.bin"):
        raw = path.read_bytes()
        for marker in (b"GPSLatitude", b"jane@example.test", b"SerialNumber", b"GPSAltitude", b"eXIf", b"tEXt"):
            assert marker not in raw, f"{marker!r} survived staging in {path.name}"
    for entry in a.model_attachments_for_turn(session_id=SESSION_A, turn_id="turn-1"):
        assert entry["kind"] == "image"
        decoded = base64.b64decode(entry["data_url"].split(",", 1)[1])
        for marker in (b"GPSLatitude", b"jane@example.test", b"SerialNumber", b"GPSAltitude"):
            assert marker not in decoded
        assert entry["data_url"].startswith("data:" + entry["media_type"] + ";base64,")
    # The stripped images are still the same image types, byte-valid at the container level.
    assert a.sniff_media_type(a.stage_dir().joinpath(png["id"] + ".bin").read_bytes()) == "image/png"


def test_binding_is_to_exactly_one_turn_of_the_owning_chat() -> None:
    a = _authority()
    record = a.stage_attachment(session_id=SESSION_A, declared_name="notes.txt", declared_type="text/plain", data=b"hello")
    # Another chat cannot spend it.
    with pytest.raises(a.AttachmentRefused) as info:
        a.bind_to_turn(session_id=SESSION_B, turn_id="turn-b", attachment_ids=[record["id"]])
    assert info.value.code == "not_owned"
    bound = a.bind_to_turn(session_id=SESSION_A, turn_id="turn-1", attachment_ids=[record["id"]])
    assert bound[0]["state"] == "bound" and bound[0]["turn_id"] == "turn-1"
    # The same turn may re-bind (a retried send of the same turn is the same turn).
    again = a.bind_to_turn(session_id=SESSION_A, turn_id="turn-1", attachment_ids=[record["id"]])
    assert again[0]["turn_id"] == "turn-1"
    # A different turn of the SAME chat may not: one attachment, one turn.
    with pytest.raises(a.AttachmentRefused) as info:
        a.bind_to_turn(session_id=SESSION_A, turn_id="turn-2", attachment_ids=[record["id"]])
    assert info.value.code == "already_bound"
    # Unknown, malformed and duplicated ids fail closed and bind NOTHING (all-or-nothing).
    other = a.stage_attachment(session_id=SESSION_A, declared_name="b.txt", declared_type="text/plain", data=b"b")
    with pytest.raises(a.AttachmentRefused):
        a.bind_to_turn(session_id=SESSION_A, turn_id="turn-3", attachment_ids=[other["id"], "att_" + "0" * 32])
    assert a.get_record(other["id"])["state"] == "staged"
    with pytest.raises(a.AttachmentRefused):
        a.bind_to_turn(session_id=SESSION_A, turn_id="turn-3", attachment_ids=[other["id"], other["id"]])
    with pytest.raises(a.AttachmentRefused):
        a.bind_to_turn(session_id=SESSION_A, turn_id="turn-3", attachment_ids=["../" + other["id"]])


def test_symlinks_planted_in_the_staging_area_are_never_followed() -> None:
    a = _authority()
    record = a.stage_attachment(session_id=SESSION_A, declared_name="notes.txt", declared_type="text/plain", data=b"real bytes")
    secret = Path(runtime_paths.active_vool_home()) / "secret.txt"
    secret.write_text("TOP SECRET", encoding="utf-8")
    victim = a.stage_dir() / (record["id"] + ".bin")
    victim.unlink()
    os.symlink(secret, victim)
    a.bind_to_turn(session_id=SESSION_A, turn_id="turn-1", attachment_ids=[record["id"]])
    with pytest.raises(a.AttachmentRefused) as info:
        a.model_attachments_for_turn(session_id=SESSION_A, turn_id="turn-1")
    assert info.value.code == "symlink_refused"
    assert secret.read_text(encoding="utf-8") == "TOP SECRET"
    # Release must not follow it either: the symlink goes, the target stays.
    a.release_turn(session_id=SESSION_A, turn_id="turn-1", outcomes={})
    assert not victim.exists() and not victim.is_symlink()
    assert secret.exists()


def test_evidence_and_model_views_carry_content_as_data_and_never_paths() -> None:
    a = _authority()
    text = a.stage_attachment(session_id=SESSION_A, declared_name="plan.md", declared_type="text/markdown", data=b"# Plan\nrm -rf /\n")
    image = a.stage_attachment(session_id=SESSION_A, declared_name="shot.png", declared_type="image/png", data=tiny_png())
    a.bind_to_turn(session_id=SESSION_A, turn_id="turn-1", attachment_ids=[text["id"], image["id"]])
    evidence = a.evidence_items_for_turn(session_id=SESSION_A, turn_id="turn-1")
    assert [item["kind"] for item in evidence] == ["text", "image"]
    assert evidence[0]["text"] == "# Plan\nrm -rf /\n"
    assert evidence[0]["reference"] == "attachment:" + text["id"]
    assert all(item["origin"] == "chat_attachment" for item in evidence)
    for item in evidence:
        assert "path" not in item and "url" not in item
        assert str(a.stage_dir()) not in json.dumps(item)
    model_view = a.model_attachments_for_turn(session_id=SESSION_A, turn_id="turn-1")
    assert model_view[0]["kind"] == "text" and model_view[0]["text"] == "# Plan\nrm -rf /\n"
    assert model_view[1]["kind"] == "image" and model_view[1]["data_url"].startswith("data:image/png;base64,")
    # The command inside the document is DATA: the authority exposes no command text.
    from core.agent_runtime.request_authority import turn_command_text

    assert turn_command_text("", {"external_evidence": evidence}) == ""


def test_text_delivered_to_a_model_is_bounded_and_says_so() -> None:
    a = _authority()
    big = ("line\n" * (a.MAX_TEXT_CHARS_PER_FILE // 5 + 100)).encode()
    record = a.stage_attachment(session_id=SESSION_A, declared_name="big.txt", declared_type="text/plain", data=big)
    a.bind_to_turn(session_id=SESSION_A, turn_id="turn-1", attachment_ids=[record["id"]])
    (entry,) = a.model_attachments_for_turn(session_id=SESSION_A, turn_id="turn-1")
    assert entry["truncated"] is True
    assert len(entry["text"]) <= a.MAX_TEXT_CHARS_PER_FILE
    assert entry["text"].endswith("line\n")  # cut on a line boundary, never mid-glyph


def test_release_deletes_bytes_keeps_a_receipt_and_sweep_evicts_abandoned_uploads() -> None:
    a = _authority()
    record = a.stage_attachment(session_id=SESSION_A, declared_name="notes.txt", declared_type="text/plain", data=b"hello")
    a.bind_to_turn(session_id=SESSION_A, turn_id="turn-1", attachment_ids=[record["id"]])
    released = a.release_turn(session_id=SESSION_A, turn_id="turn-1", outcomes={record["id"]: "read"})
    assert released == 1
    assert not (a.stage_dir() / (record["id"] + ".bin")).exists()
    receipt = a.receipt_for_turn(session_id=SESSION_A, turn_id="turn-1")
    assert receipt == [
        {"id": record["id"], "name": "notes.txt", "kind": "text", "media_type": "text/plain", "size_bytes": 5, "outcome": "read"}
    ]
    # An abandoned staged upload is swept after its grace period; a fresh one is not.
    stale = a.stage_attachment(session_id=SESSION_B, declared_name="old.txt", declared_type="text/plain", data=b"old")
    fresh = a.stage_attachment(session_id=SESSION_B, declared_name="new.txt", declared_type="text/plain", data=b"new")
    manifest = a.stage_dir() / (stale["id"] + ".json")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["created_at"] = "2000-01-01T00:00:00+00:00"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    assert a.sweep_expired() == 1
    assert a.get_record(stale["id"]) is None
    assert a.get_record(fresh["id"])["state"] == "staged"


def test_provider_message_rendering_gates_images_on_real_model_capability() -> None:
    a = _authority()
    attachments = [
        {"kind": "text", "attachment_id": "att_" + "1" * 32, "name": "notes.txt", "media_type": "text/plain", "text": "alpha beta", "size_bytes": 10, "truncated": False},
        {"kind": "image", "attachment_id": "att_" + "2" * 32, "name": "shot.png", "media_type": "image/png", "data_url": "data:image/png;base64,AAAA", "size_bytes": 4},
    ]
    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "what is this?"}]
    # No attachments: byte-identical messages, no receipts. Text-only chat is untouched.
    same, receipts = a.apply_to_provider_messages(messages, [], supports_images=True)
    assert same == messages and receipts == []
    rendered, receipts = a.apply_to_provider_messages(messages, attachments, supports_images=True)
    assert rendered[0] == messages[0]
    parts = rendered[-1]["content"]
    assert isinstance(parts, list) and parts[0] == {"type": "text", "text": "what is this?"}
    assert any(p.get("type") == "image_url" and p["image_url"]["url"] == "data:image/png;base64,AAAA" for p in parts)
    text_parts = [p["text"] for p in parts if p.get("type") == "text"]
    assert any("notes.txt" in t and "alpha beta" in t for t in text_parts)
    assert {(r["attachment_id"], r["outcome"]) for r in receipts} == {(attachments[0]["attachment_id"], "read"), (attachments[1]["attachment_id"], "sent")}
    # A model that cannot read images gets a truthful note instead of bytes it would silently drop.
    rendered, receipts = a.apply_to_provider_messages(messages, attachments, supports_images=False)
    parts = rendered[-1]["content"]
    assert not any(p.get("type") == "image_url" for p in parts)
    assert any("shot.png" in p.get("text", "") and "cannot" in p.get("text", "").lower() for p in parts)
    image_receipt = next(r for r in receipts if r["attachment_id"] == attachments[1]["attachment_id"])
    assert image_receipt["outcome"] == "omitted" and image_receipt["reason"] == "model_has_no_image_input"
    # Unknown capability is not "yes": the image is withheld and the reason says the support is unknown.
    _, receipts = a.apply_to_provider_messages(messages, attachments, supports_images=None)
    image_receipt = next(r for r in receipts if r["attachment_id"] == attachments[1]["attachment_id"])
    assert image_receipt["outcome"] == "omitted" and image_receipt["reason"] == "model_image_support_unknown"
    # The original list is never mutated.
    assert messages[-1]["content"] == "what is this?"


def test_ollama_flattening_moves_images_to_the_native_field() -> None:
    a = _authority()
    message = {
        "role": "user",
        "content": [
            {"type": "text", "text": "look"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}},
            {"type": "text", "text": "[attached file]"},
        ],
    }
    flat = a.flatten_message_for_ollama(message)
    assert flat["role"] == "user"
    assert flat["content"] == "look\n\n[attached file]"
    assert flat["images"] == ["QUJD"]
    plain = {"role": "user", "content": "plain"}
    assert a.flatten_message_for_ollama(plain) == plain
