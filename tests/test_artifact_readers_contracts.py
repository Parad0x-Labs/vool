"""The reader contracts, exercised directly: typed refusals, bounds, and the derivative's honesty.

These are the cases where a reader is most likely to be quietly wrong rather than loudly broken --
a password-protected file that "appears empty", a bomb that is refused only after it has been
decompressed, an extracted text that outlives the original it claims to come from. Each one is
asserted on the thing that would actually go wrong, not on a proxy for it.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile

import pytest

import core.artifact_readers as readers
from core.artifact_readers import archive as archive_reader
from core.artifact_readers import image as image_reader
from core.artifact_readers import office, pdf, video
from core.artifact_readers._limits import MAX_ARCHIVE_EXPANSION_RATIO, MAX_ARCHIVE_MEMBERS
from core.artifact_readers._types import ReaderRefused, ReaderUnavailable
from tests.reader_fixtures import (
    docx_document,
    native_text_pdf,
    rar_archive,
    scanned_pdf,
    zip_archive,
    zip_many_members,
)

# --- the registry is a declared table, not scattered regexes ---------------------------------


def test_capability_report_names_every_format_and_why_a_blocked_one_is_blocked():
    report = readers.capability_report()
    formats = {row["format"]: row for row in report["formats"]}
    assert set(formats) == {"pdf", "docx", "doc", "xlsx", "xls", "pptx", "odt", "epub", "rtf", "zip", "rar", "audio", "video"}
    for row in report["formats"]:
        assert row["available"] or row["blocked_reason"], f"{row['format']} is unavailable with no reason given"
        assert row["available"] is False or not row["blocked_reason"]
    # The accept list is derived from availability, never hardcoded.
    for row in report["formats"]:
        for extension in row["extensions"]:
            assert (extension in report["available_extensions"]) is bool(row["available"])


def test_bytes_decide_the_reader_not_the_extension():
    """A ZIP named .pdf is detected as a ZIP. The name never overrides the content."""
    blob = zip_archive({"a.txt": b"hello\n"})
    assert readers.detect(blob, extension=".pdf").fmt == "zip"
    assert readers.detect(native_text_pdf(), extension=".zip").fmt == "pdf"


def test_an_unreadable_format_raises_unavailable_not_an_empty_result(monkeypatch: pytest.MonkeyPatch):
    """A missing decoder is a BLOCKED format that says so -- never a read that found nothing."""
    monkeypatch.setattr(archive_reader, "_bsdtar_available", lambda: False)
    report = readers.capability_report()
    assert "rar" in report["blocked_formats"]
    with pytest.raises(ReaderUnavailable) as caught:
        readers.read(rar_archive({"a.txt": b"x\n"}), name="b.rar", extension=".rar")
    assert caught.value.code == "rar_decoder_unavailable"
    assert caught.value.remediation, "a blocked format must tell the operator what would unblock it"


# --- typed, actionable failures --------------------------------------------------------------


def test_password_protected_pdf_is_named_as_such_not_read_as_empty():
    pypdf = pytest.importorskip("pypdf", reason="needs a PDF writer to build an encrypted fixture")
    writer = pypdf.PdfWriter(clone_from=io.BytesIO(native_text_pdf()))
    writer.encrypt("correct-horse")
    buffer = io.BytesIO()
    writer.write(buffer)
    with pytest.raises(ReaderRefused) as caught:
        readers.read(buffer.getvalue(), name="locked.pdf", extension=".pdf")
    assert caught.value.code == "password_protected"
    assert "password" in caught.value.message.lower()
    assert caught.value.remediation


def test_corrupt_docx_is_refused_with_a_reason():
    broken = bytearray(docx_document())
    # Damage the central directory, which is what a truncated download actually looks like.
    broken[-64:] = b"\x00" * 64
    with pytest.raises(ReaderRefused) as caught:
        readers.read(bytes(broken), name="broken.docx", extension=".docx")
    # A DOCX with a shredded central directory no longer sniffs AS a DOCX -- bytes decide, so it
    # is refused as the damaged ZIP container it now is. Either code is honest; what must hold is
    # that the refusal names the file, says it is damaged, and tells the operator what to do.
    assert caught.value.code in {"docx_corrupt", "docx_not_a_document", "docx_unreadable", "archive_corrupt"}
    assert "broken.docx" in caught.value.message and "damaged" in caught.value.message
    assert caught.value.remediation


def test_a_zip_that_is_entirely_encrypted_is_refused_as_password_protected():
    # Flag bit 0 set on every member is what a password-protected ZIP looks like on the wire.
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as handle:
        handle.writestr("secret.txt", b"cannot be read without a password\n")
    raw = bytearray(buffer.getvalue())
    for offset in range(len(raw) - 4):
        if raw[offset : offset + 4] in (b"PK\x03\x04", b"PK\x01\x02"):
            flag_at = offset + (6 if raw[offset : offset + 4] == b"PK\x03\x04" else 8)
            raw[flag_at] |= 0x01
    with pytest.raises(ReaderRefused) as caught:
        readers.read(bytes(raw), name="locked.zip", extension=".zip")
    assert caught.value.code == "password_protected"


def test_a_video_with_no_video_stream_is_refused_not_silently_empty():
    if not video.decoders_available()["video"]:
        pytest.skip("no video decoder on this machine; the format is BLOCKED here")
    # A valid MP4 header over bytes that are not a decodable stream.
    junk = b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2avc1mp41" + b"\x00" * 4096
    with pytest.raises((ReaderRefused, ReaderUnavailable)) as caught:
        readers.read(junk, name="broken.mp4", extension=".mp4")
    assert caught.value.code in {"video_unreadable", "video_no_stream", "video_no_frames", "not_a_video"}


# --- bounds hold on the numbers, not on hope --------------------------------------------------


def test_a_high_ratio_member_under_the_size_cap_is_still_refused_as_a_bomb():
    """The ratio guard, isolated from the size guard: small enough to store, absurd to expand."""
    payload = b"\x00" * (4 * 1024 * 1024)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as handle:
        handle.writestr("quiet-bomb.bin", payload)
    result = readers.read(buffer.getvalue(), name="quiet.zip", extension=".zip")
    listing = result.units[0].text
    assert "REFUSED: quiet-bomb.bin" in listing
    assert f"over the {MAX_ARCHIVE_EXPANSION_RATIO}x limit" in listing
    assert result.meta["members_read"] == 0


def test_member_flood_is_bounded_and_the_overflow_is_declared():
    result = readers.read(zip_many_members(MAX_ARCHIVE_MEMBERS + 500), name="flood.zip", extension=".zip")
    assert result.meta["members"] == MAX_ARCHIVE_MEMBERS
    assert any("were listed" in warning for warning in result.warnings)
    assert any("beyond the first" in entry for entry in result.omitted)


def test_an_image_declaring_an_absurd_pixel_count_is_refused_before_decoding():
    pytest.importorskip("PIL", reason="the pixel bound is enforced by the image decoder")
    # A 5-byte IDAT with a header claiming 60000x60000: tiny file, impossible raster.
    import struct
    import zlib

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)

    bomb = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 60000, 60000, 8, 0, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00" * 16))
        + chunk(b"IEND", b"")
    )
    with pytest.raises(ReaderRefused) as caught:
        image_reader.dimensions(bomb)
    assert caught.value.code == "image_too_many_pixels"


# --- extraction is a derivative, never a replacement -------------------------------------------


def test_the_result_carries_the_hash_of_the_original_and_the_extractor_that_read_it():
    blob = native_text_pdf()
    result = readers.read(blob, name="report.pdf", extension=".pdf")
    assert result.source_sha256 == hashlib.sha256(blob).hexdigest()
    assert result.extractor, "an extraction with no named extractor cannot be traced to any code"
    assert all(unit.locator for unit in result.units), "every unit must be addressable"


def test_every_unit_is_addressable_and_rendered_under_its_own_locator():
    result = readers.read(native_text_pdf(pages=4), name="four.pdf", extension=".pdf")
    rendered = result.rendered_text()
    for number in range(1, 5):
        assert f"[page {number}]" in rendered
    assert result.unit("page 3") is not None
    assert result.unit("page 99") is None


def test_a_scanned_page_states_ocr_and_a_native_page_does_not():
    if not pdf.decoders_available()["ocr"]:
        pytest.skip("no OCR engine on this machine; scanned PDFs are BLOCKED here")
    scanned = readers.read(scanned_pdf(["TOKEN QUARTZ 4471"]), name="scan.pdf", extension=".pdf")
    assert scanned.meta["ocr_pages"] == 1
    assert any("no text layer" in warning for warning in scanned.units[0].warnings)
    assert "QUARTZ" in scanned.units[0].text

    native = readers.read(native_text_pdf(), name="native.pdf", extension=".pdf")
    assert native.meta["ocr_pages"] == 0
    assert not any("OCR" in warning for warning in native.units[0].warnings)


def test_docx_reports_a_macro_without_reading_or_running_it():
    result = readers.read(docx_document(with_macro=True), name="m.docx", extension=".docx")
    assert result.meta["has_macros"] is True
    assert any("NOT run" in warning for warning in result.warnings)
    body = result.units[0].text
    assert "vbaProject" not in body, "macro bytes must not appear in the extracted text"


# --- the attachment door's side of the contract ------------------------------------------------


def test_the_door_stores_a_derivative_beside_an_untouched_original(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VOOL_DATA_DIR", str(tmp_path))
    from core import chat_attachments, runtime_paths

    runtime_paths.active_data_dir.cache_clear() if hasattr(runtime_paths.active_data_dir, "cache_clear") else None
    blob = native_text_pdf()
    record = chat_attachments.stage_attachment(
        session_id="s-derivative", declared_name="report.pdf", declared_type="application/pdf", data=blob
    )
    assert record["kind"] == "artifact"
    assert record["sha256"] == hashlib.sha256(blob).hexdigest()

    stored = chat_attachments._bytes_path(record["id"]).read_bytes()
    assert stored == blob, "the original bytes must be stored byte-for-byte"

    derivative = json.loads(chat_attachments._derivative_path(record["id"]).read_text("utf-8"))
    assert derivative["source_sha256"] == record["sha256"]
    assert derivative["extractor"] and derivative["registry_version"]
    assert [unit["locator"] for unit in derivative["units"]] == ["page 1", "page 2", "page 3"]


def test_erasing_an_attachment_erases_its_derivative_too(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VOOL_DATA_DIR", str(tmp_path))
    from core import chat_attachments

    record = chat_attachments.stage_attachment(
        session_id="s-erase", declared_name="report.pdf", declared_type="application/pdf", data=native_text_pdf()
    )
    derivative = chat_attachments._derivative_path(record["id"])
    assert derivative.exists()
    assert chat_attachments.remove_staged(session_id="s-erase", attachment_id=record["id"]) is True
    assert not chat_attachments._bytes_path(record["id"]).exists()
    assert not derivative.exists(), "extracted text must never outlive the original it attests to"


def test_a_pdf_named_file_whose_bytes_are_a_zip_is_refused_at_the_door(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VOOL_DATA_DIR", str(tmp_path))
    from core import chat_attachments

    with pytest.raises(chat_attachments.AttachmentRefused) as caught:
        chat_attachments.stage_attachment(
            session_id="s-mismatch",
            declared_name="report.pdf",
            declared_type="application/pdf",
            data=zip_archive({"a.txt": b"not a pdf\n"}),
        )
    assert caught.value.code == "content_mismatch"


def test_limits_payload_offers_only_formats_this_machine_can_actually_read():
    from core import chat_attachments

    payload = chat_attachments.limits_payload()
    report = readers.capability_report()
    # Membership in the comma-joined list, never substring containment: ".xls" is a substring of
    # ".xlsx", so the old check passed a blocked xls whenever xlsx was offered.
    accepted = set(payload["accept"].split(","))
    for row in report["formats"]:
        for extension in row["extensions"]:
            assert (extension in accepted) is bool(row["available"])
    for blocked in payload["blocked_formats"]:
        assert blocked["reason"], "a format withheld from the picker must say why"


def test_office_and_video_decoder_probes_do_not_raise_when_absent(monkeypatch: pytest.MonkeyPatch):
    """Availability is a question, never an exception: the picker asks it on every page load."""
    monkeypatch.setattr(office, "doc_converter_available", lambda: False)
    monkeypatch.setattr(video, "ffmpeg_path", lambda: "")
    report = readers.capability_report()
    assert "doc" in report["blocked_formats"]
    assert "video" in report["blocked_formats"]
    assert ".doc" not in report["available_extensions"]
