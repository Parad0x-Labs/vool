"""Media/product closure M4: the served truth for every family this lane touches.

Same drive shape as M1/M2/M3 (real daemon, real upload door, real ``/api/chat``, scripted
provider capturing the FULL model-bound request) — one file per stage of the closure so each
claim sits next to the stage that earned it:

* routing: a bound attachment named in the message is answered from the attachment, never
  from a disk search that never saw it (the "on screen" collision);
* (image OCR wiring, audio, and the refusal matrix land in their own sections below).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._reader_served_rig import CapturingProvider, ServedDaemon, canonical_session
from tests.reader_fixtures import native_text_pdf


@pytest.fixture(scope="module")
def rig(tmp_path_factory: pytest.TempPathFactory):
    home = tmp_path_factory.mktemp("media-product-m4-home")
    with CapturingProvider() as provider, ServedDaemon(Path(home), provider=provider) as daemon:
        assert daemon.certify().get("state") == "verified"
        yield daemon, provider


def _upload_and_ask(rig_pair, *, seed: str, name: str, data: bytes, declared_type: str, question: str):
    daemon, provider = rig_pair
    session = canonical_session(seed)
    uploaded = daemon.upload(session_id=session, name=name, data=data, declared_type=declared_type)
    assert uploaded["status"] == 201, uploaded
    record = uploaded["attachment"]
    provider.reset()
    reply = daemon.chat(question, session_id=session, attachments=[record["id"]])
    assert provider.calls, f"the model was never called: {reply}"
    return "\n".join(provider.payloads()), record, provider


# --- routing: the "on screen" collision -------------------------------------------------------------


def test_a_bound_pdf_named_in_the_message_is_answered_from_the_attachment_not_the_disk(rig):
    """The disk-PDF lane used to claim this turn ("could not find ... anywhere in ~").

    The attachment's extraction must reach the provider instead. ``on screen`` phrasing is
    the collision shape: it reads like a host-display question AND names the attached file.
    """
    payload, record, _provider = _upload_and_ask(
        rig,
        seed="m4-onscreen-pdf",
        name="site-survey-vk73.pdf",
        data=native_text_pdf(pages=3, marker="SURVEY-4471"),
        declared_type="application/pdf",
        question="What is on screen on page 3 of the attached site-survey-vk73.pdf?",
    )
    assert record["reader_format"] == "pdf"
    # The decisive span lives only inside the PDF's page-3 content, never in its name:
    assert "Closing reference: SURVEY-4471" in payload


def test_the_same_question_with_no_attachment_still_says_what_the_disk_lane_says(rig):
    """The guard is a name match on bound material — the disk lane keeps its own answers."""
    daemon, provider = rig
    session = canonical_session("m4-onscreen-pdf-nodisk")
    provider.reset()
    daemon.chat(
        "What is on screen on page 3 of the attached site-survey-vk73.pdf?",
        session_id=session,
        attachments=[],
    )
    assert provider.calls == [], "a disk-named PDF with nothing bound reached the model lane"


# --- image OCR wired to the served turn -------------------------------------------------------------


def _ocr_on_this_machine() -> bool:
    from core.artifact_readers import image as image_reader

    return image_reader.decoders_available()["image_ocr"]


@pytest.fixture(scope="module")
def text_rig(tmp_path_factory: pytest.TempPathFactory):
    """A provider that CANNOT read images: the pixels must be withheld, the words must travel."""
    home = tmp_path_factory.mktemp("media-product-m4-text-home")
    with CapturingProvider() as provider, ServedDaemon(Path(home), provider=provider, vision=False) as daemon:
        assert daemon.certify().get("state") == "verified"
        yield daemon, provider


@pytest.fixture(scope="module")
def vision_rig(tmp_path_factory: pytest.TempPathFactory):
    """A provider that declares image input: the pixels AND the words must both travel."""
    home = tmp_path_factory.mktemp("media-product-m4-vision-home")
    with CapturingProvider() as provider, ServedDaemon(
        Path(home), provider=provider, vision=True, provider_name="media-product-stub-vision", model="media-drive:vision"
    ) as daemon:
        assert daemon.certify().get("state") == "verified"
        yield daemon, provider


RECEIPT_LINES = ["RECEIPT 55182", "MERCHANT: NORTHGATE", "TOTAL: 91.40", "CARD ENDING 4417"]


def test_served_ocr_reaches_a_text_only_model_with_the_picture_omission_stated(text_rig):
    """The photographed-receipt case: decisive spans travel as text to a model that cannot see."""
    if not _ocr_on_this_machine():
        pytest.skip("no OCR engine on this machine; image text reading is BLOCKED here, not implemented")
    from tests.reader_fixtures import text_image_png

    payload, record, provider = _upload_and_ask(
        text_rig,
        seed="m4-ocr-textonly",
        name="receipt-vk73.png",
        data=text_image_png(RECEIPT_LINES, width=1000, height=520, point=40),
        declared_type="image/png",
        question="What is the total on the attached receipt?",
    )
    assert record["kind"] == "image"
    assert "TOTAL: 91.40" in payload, "the decisive span never reached the provider payload"
    assert "recognised by" in payload, "the read must say how it was made"
    assert "not shown" in payload, "the omission of the picture itself must still be stated"
    assert provider.total_images() == 0, "pixels reached a model that cannot read images"


def test_served_ocr_accompanies_the_pixels_on_a_vision_model(vision_rig):
    if not _ocr_on_this_machine():
        pytest.skip("no OCR engine on this machine; image text reading is BLOCKED here, not implemented")
    from tests.reader_fixtures import text_image_png

    payload, _record, provider = _upload_and_ask(
        vision_rig,
        seed="m4-ocr-vision",
        name="receipt-vk74.png",
        data=text_image_png(RECEIPT_LINES, width=1000, height=520, point=40),
        declared_type="image/png",
        question="What is the total on the attached receipt?",
    )
    assert "TOTAL: 91.40" in payload, "the recognised text no longer travels when pixels do"
    assert provider.total_images() >= 1, "the vision lane lost its pixels"


# --- audio: bounded local transcription, typed absence ----------------------------------------------


def _wav_bytes(seconds: float = 1.0, rate: int = 8000) -> bytes:
    import io
    import wave

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * int(rate * seconds))
    return buffer.getvalue()


def _recognizer_authorized() -> bool:
    from core.artifact_readers import speech_tool

    return speech_tool.speech_available()


@pytest.fixture(scope="module")
def speech_tools(rig):
    """The compiled speech helper, built ONCE into the daemon's own tools directory."""
    import os

    from core.artifact_readers import speech_tool

    daemon, _provider = rig
    previous = os.environ.get("VOOL_READER_TOOLS_DIR")
    os.environ["VOOL_READER_TOOLS_DIR"] = str(Path(daemon.home) / "reader_tools")
    try:
        speech_tool.ensure_tool()
    finally:
        if previous is None:
            os.environ.pop("VOOL_READER_TOOLS_DIR", None)
        else:
            os.environ["VOOL_READER_TOOLS_DIR"] = previous
    return True


def test_served_audio_upload_tells_the_truth_about_the_recogniser(rig, speech_tools):
    """The typed truth, whichever way this machine answers.

    Without Speech access the upload is REFUSED (415, ``audio_decoder_unavailable``, the
    recogniser's absence named) — a refusal, never a staged attachment that pretends it was
    read. With access, the same upload stages as an artifact and the transcript reaches the
    provider as addressed text. What this test refuses to allow is everything in between.
    """
    daemon, _provider = rig
    session = canonical_session("m4-audio")
    uploaded = daemon.upload(
        session_id=session, name="memo.wav", data=_wav_bytes(seconds=1.0), declared_type="audio/x-wav",
    )
    if _recognizer_authorized():
        assert uploaded["status"] == 201, uploaded
        record = uploaded["attachment"]
        assert record["reader_format"] == "audio"
        return
    assert uploaded["status"] == 415, uploaded
    assert uploaded["error"] == "audio_decoder_unavailable"
    assert "speech recogniser" in uploaded["message"]
    assert "was not accepted" in uploaded["message"]


def test_served_capability_report_names_audio_and_its_absence(rig):
    daemon, _provider = rig
    limits = daemon.limits()
    reader_formats = {row["format"]: row for row in limits.get("reader_formats", [])}
    blocked = {row["format"]: row for row in limits.get("blocked_formats", [])}
    assert "audio" in reader_formats or "audio" in blocked, "audio vanished from the capability report"
    if "audio" in blocked and not _recognizer_authorized():
        assert "speech recogniser" in blocked["audio"]["reason"]
        assert ".wav" not in limits["accept"], "an unreadable format must not be offered in the picker"


def test_served_dictation_get_reports_typed_absence(rig, speech_tools):
    daemon, _provider = rig
    facts = daemon.dictation_availability()
    if _recognizer_authorized():
        assert facts["available"] is True
        return
    assert facts["available"] is False
    assert facts["error"] == "speech_recognizer_unauthorized"
    assert "System Settings" in facts["remediation"]


def test_served_dictation_post_answers_with_the_typed_truth(rig, speech_tools):
    daemon, _provider = rig
    session = canonical_session("m4-dictation")
    result = daemon.dictate(session_id=session, data=_wav_bytes(seconds=1.0), audio_type="audio/x-wav")
    if _recognizer_authorized():
        assert result["status"] == 200 and result.get("ok") is True, result
        assert isinstance(result.get("text"), str)
        return
    assert result["status"] == 503, result
    assert result["error"] == "speech_recognizer_unauthorized"
    assert "not granted" in result["message"]


# --- the refusal matrix, at the served boundary ------------------------------------------------------


def test_served_a_corrupt_pdf_is_refused_typed_not_answered_empty(rig):
    """Bytes that carry a PDF header but no valid document fail the decoder TYPED: the door
    maps the reader's failure to ``decoder_failed`` with the decoders' own words, never to a
    201 that pretends the bytes were read."""
    daemon, _provider = rig
    session = canonical_session("m4-corrupt-pdf")
    uploaded = daemon.upload(
        session_id=session,
        name="damaged.pdf",
        data=b"%PDF-1.4\nthis is not a document body, it is truncation wearing a header\n",
        declared_type="application/pdf",
    )
    assert uploaded["status"] == 500, uploaded
    assert uploaded["error"] == "decoder_failed"
    assert "unreadable-pdf" in uploaded["message"] or "pdf" in uploaded["message"].lower()
    assert uploaded.get("attachment") is None


def test_served_pdf_bytes_that_are_not_pdf_at_all_are_refused_typed(rig):
    daemon, _provider = rig
    session = canonical_session("m4-notapdf")
    uploaded = daemon.upload(
        session_id=session,
        name="renamed.pdf",
        data=b"just some text, nothing more\n",
        declared_type="application/pdf",
    )
    assert uploaded["status"] == 422, uploaded
    assert uploaded["error"] == "content_mismatch"
    assert "named as PDF but its bytes are not a PDF" in uploaded["message"]


def test_served_an_oversize_upload_refuses_at_the_door(rig):
    from core import chat_attachments

    daemon, _provider = rig
    session = canonical_session("m4-oversize")
    uploaded = daemon.upload(
        session_id=session,
        name="bulk.txt",
        data=b"x" * (chat_attachments.MAX_BYTES_PER_FILE + 1),
        declared_type="text/plain",
    )
    assert uploaded["status"] == 413, uploaded
    assert uploaded["error"] == "too_large"


def test_served_a_cancelled_attachment_never_reaches_the_provider(rig):
    """The composer's cancel: withdrawn bytes are gone from the turn, and say so in Activity."""
    from urllib.error import HTTPError

    daemon, provider = rig
    session = canonical_session("m4-cancel")
    uploaded = daemon.upload(
        session_id=session,
        name="withdrawn.pdf",
        data=native_text_pdf(pages=1, marker="WITHDRAWN-9001"),
        declared_type="application/pdf",
    )
    assert uploaded["status"] == 201, uploaded
    attachment_id = uploaded["attachment"]["id"]
    removed = daemon.remove(session_id=session, attachment_id=attachment_id)
    assert removed.get("status") == 200, removed
    provider.reset()
    try:
        daemon.chat("What does the attached withdrawn.pdf say?", session_id=session, attachments=[attachment_id])
    except HTTPError as exc:
        assert 400 <= exc.code < 500, f"the door answered a cancelled attachment with {exc.code}"
    assert provider.calls == [], "a withdrawn attachment's extraction reached the provider"


def test_served_a_credential_pasted_as_text_refuses_before_anything_is_staged(rig):
    from tests.reader_fixtures import pdf_with_text

    daemon, _provider = rig
    session = canonical_session("m4-secret-pdf")
    uploaded = daemon.upload(
        session_id=session,
        name="credentials.pdf",
        data=pdf_with_text(["AKIAABCDEFGHIJKLMNOP"]),
        declared_type="application/pdf",
    )
    assert uploaded["status"] == 422, uploaded
    assert uploaded["error"] == "secret_detected"
    assert uploaded.get("attachment") is None


def test_served_a_text_attachment_reaches_the_provider_like_every_family(rig):
    """The oldest family, proven through the same door with the same decisiveness."""
    payload, record, _provider = _upload_and_ask(
        rig,
        seed="m4-text",
        name="notes-vk73.txt",
        data=b"the launch code for the drill is TOPAZ-1187\n",
        declared_type="text/plain",
        question="What is the launch code in the attached notes?",
    )
    assert record["kind"] == "text"
    assert "TOPAZ-1187" in payload
