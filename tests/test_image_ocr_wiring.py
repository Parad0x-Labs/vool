"""Image OCR is wired to the served turn, not left as a dead reader.

The reader existed; the product never called it. An attached image travelled as pixels — and
on any model that cannot (or provably may not) read images, the turn lost the picture AND the
words in it, which is exactly the photographed-receipt/screenshot case OCR exists for. This
pack pins the closed loop: staging OCRs the pixels once (and scans them like any other
extraction), the derivative travels as labeled text to EVERY model, the pixels still follow
the vision-capability rule, and a machine with no recogniser stages the image exactly as
before — typed absence, never fake text.
"""

from __future__ import annotations

import pytest

from core import chat_attachments
from tests.reader_fixtures import text_image_png

RECEIPT_LINES = ["RECEIPT 55182", "MERCHANT: NORTHGATE", "TOTAL: 91.40", "CARD ENDING 4417"]


def _ocr_on_this_machine() -> bool:
    from core.artifact_readers import image as image_reader

    return image_reader.decoders_available()["image_ocr"]


requires_ocr = pytest.mark.skipif(not _ocr_on_this_machine(), reason="no OCR engine on this machine; the wiring is BLOCKED here, not implemented")


def _staged_receipt(monkeypatch, *, name="receipt.png"):
    record = chat_attachments.stage_attachment(
        session_id="ocr-wiring-session",
        declared_name=name,
        declared_type="image/png",
        data=text_image_png(RECEIPT_LINES, width=1000, height=520, point=40),
    )
    return record


# --- staging ---------------------------------------------------------------------------------------


@requires_ocr
def test_staging_an_image_runs_ocr_once_and_records_what_was_found(monkeypatch):
    record = _staged_receipt(monkeypatch)
    assert record["kind"] == "image"
    assert record.get("reader_format") == "image", "the manifest must say the image's text was read"
    assert record.get("reader_units") == 1
    derivative = chat_attachments._read_derivative(record["id"])
    assert derivative is not None and derivative.get("rendered"), "the OCR derivative was not written"
    assert "TOTAL: 91.40" in str(derivative.get("rendered") or "")


def test_when_no_ocr_engine_exists_the_image_stages_pixels_only(monkeypatch):
    from core.artifact_readers import image as image_reader

    monkeypatch.setattr(image_reader, "ocr_available", lambda: False)
    record = _staged_receipt(monkeypatch)
    assert record["kind"] == "image"
    assert not record.get("reader_format"), "a machine with no recogniser must not claim it read text"
    assert chat_attachments._read_derivative(record["id"]) is None


# --- delivery --------------------------------------------------------------------------------------


@requires_ocr
def test_bound_image_delivers_ocr_text_within_the_turn_budget(monkeypatch):
    record = _staged_receipt(monkeypatch)
    chat_attachments.bind_to_turn(
        session_id="ocr-wiring-session", turn_id="ocr-wiring-turn", attachment_ids=[record["id"]],
    )
    entries = chat_attachments.model_attachments_for_turn(
        session_id="ocr-wiring-session", turn_id="ocr-wiring-turn",
    )
    image_entries = [e for e in entries if e.get("kind") == "image"]
    assert image_entries, "the bound image never reached the model-entry list"
    entry = image_entries[0]
    assert entry.get("data_url", "").startswith("data:image/png;base64,"), "the pixels lane is unchanged"
    assert "TOTAL: 91.40" in str(entry.get("ocr_text") or "")
    assert entry.get("extractor"), "the OCR block must say what read the text"


@requires_ocr
def test_a_text_only_model_receives_the_words_and_a_truthful_pixel_omission(monkeypatch):
    record = _staged_receipt(monkeypatch)
    chat_attachments.bind_to_turn(
        session_id="ocr-wiring-session", turn_id="ocr-wiring-turn-2", attachment_ids=[record["id"]],
    )
    entries = chat_attachments.model_attachments_for_turn(
        session_id="ocr-wiring-session", turn_id="ocr-wiring-turn-2",
    )
    messages = [{"role": "user", "content": "What is the total on the attached receipt?"}]
    rendered, receipts = chat_attachments.apply_to_provider_messages(messages, entries, supports_images=False)
    parts = rendered[0]["content"]
    text = " ".join(str(part.get("text") or "") for part in parts if isinstance(part, dict))
    assert "TOTAL: 91.40" in text, "the decisive span never reached a model that cannot see"
    assert "not shown" in text, "the omission of the PICTURE must still be stated"
    assert not any(part.get("type") == "image_url" for part in parts), "pixels reached a text-only model"
    receipt = receipts[0]
    assert receipt["outcome"] == "read"
    assert receipt.get("ocr") is True


@requires_ocr
def test_a_vision_model_receives_the_pixels_and_the_words(monkeypatch):
    record = _staged_receipt(monkeypatch)
    chat_attachments.bind_to_turn(
        session_id="ocr-wiring-session", turn_id="ocr-wiring-turn-3", attachment_ids=[record["id"]],
    )
    entries = chat_attachments.model_attachments_for_turn(
        session_id="ocr-wiring-session", turn_id="ocr-wiring-turn-3",
    )
    messages = [{"role": "user", "content": "What is the total on the attached receipt?"}]
    rendered, receipts = chat_attachments.apply_to_provider_messages(messages, entries, supports_images=True)
    parts = rendered[0]["content"]
    assert any(part.get("type") == "image_url" for part in parts), "the vision lane was broken by the wiring"
    text = " ".join(str(part.get("text") or "") for part in parts if isinstance(part, dict))
    assert "TOTAL: 91.40" in text
    receipt = receipts[0]
    assert receipt["outcome"] == "sent"
    assert receipt.get("ocr") is True


# --- the same document policy ------------------------------------------------------------------------


def test_credential_shaped_ocr_text_refuses_the_upload_before_anything_is_staged(monkeypatch):
    """A live key does not stop being a live key because it arrived inside a screenshot."""
    from core.artifact_readers import image as image_reader

    monkeypatch.setattr(image_reader, "ocr_available", lambda: True)
    monkeypatch.setattr(
        image_reader, "ocr_text_with_confidence",
        lambda _path, *, scratch: (
            "deploy token: AKIAABCDEFGHIJKLMNOP special case",
            {"mean_confidence": 0.93, "lines": 1, "low_confidence_lines": 0, "engine": "stub"},
        ),
    )
    with pytest.raises(chat_attachments.AttachmentRefused) as caught:
        chat_attachments.stage_attachment(
            session_id="ocr-wiring-session",
            declared_name="screenshot.png",
            declared_type="image/png",
            data=text_image_png(["nothing to see"], width=200, height=80, point=20),
        )
    assert caught.value.code == "secret_detected"


# --- truncation truth --------------------------------------------------------------------------------


@requires_ocr
def test_ocr_text_is_bounded_and_states_the_cut(monkeypatch):
    from core.artifact_readers import image as image_reader

    real_ocr = image_reader.ocr_text_with_confidence
    monkeypatch.setattr(image_reader, "ocr_available", lambda: True)

    def huge_text(_path, *, scratch):
        return ("NORDIC-7719 " * 40_000, {"mean_confidence": 0.9, "lines": 1, "low_confidence_lines": 0, "engine": "stub"})

    monkeypatch.setattr(image_reader, "ocr_text_with_confidence", huge_text)
    record = chat_attachments.stage_attachment(
        session_id="ocr-wiring-session",
        declared_name="huge.png",
        declared_type="image/png",
        data=text_image_png(["x"], width=80, height=40, point=12),
    )
    chat_attachments.bind_to_turn(
        session_id="ocr-wiring-session", turn_id="ocr-wiring-turn-4", attachment_ids=[record["id"]],
    )
    entries = chat_attachments.model_attachments_for_turn(
        session_id="ocr-wiring-session", turn_id="ocr-wiring-turn-4",
    )
    entry = next(e for e in entries if e.get("kind") == "image")
    assert entry.get("ocr_truncated") is True, "an over-budget OCR must not travel silently whole"
    assert entry.get("selection"), "the cut must say what was delivered"
    assert real_ocr is not None
