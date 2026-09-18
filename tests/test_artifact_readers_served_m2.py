"""M2: legacy DOC, RAR, image OCR, and video read as timestamped frames -- through the served door.

The video tests are the ones that matter most here, because video is where a reader is most
tempted to lie. Three claims are made and each is tested by something that can fail:

* **The middle is reachable.** The fixture's event appears only in the middle third of the clip.
  A reader that samples the first and last frame -- the cheap implementation, and the one that
  looks fine in a demo -- cannot produce it, so `test_video_event_in_the_middle_reaches_the_model`
  fails rather than passing quietly.
* **Sampling is disclosed, not implied.** The payload must say how many frames of how long a video
  were looked at, and must say the frames between them were not seen.
* **Audio is a fact, not an assumption.** A silent clip and a clip whose speech was never
  transcribed are different, and the payload distinguishes them. "The video has no dialogue" about
  an untranscribed AAC track would be a fabrication.

Frame delivery is tested BOTH ways, against two registered providers: one that declares image
input (the frames travel, each under its own timestamp label) and one that does not (the frames
are withheld and the model is told it has not seen the video). The second is not a lesser case --
it is the case where a reader is most likely to let a model answer as though it had looked.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._reader_served_rig import CapturingProvider, ServedDaemon, canonical_session
from tests.reader_fixtures import (
    VIDEO_CLOSING_TEXT,
    VIDEO_EVENT_TEXT,
    VIDEO_OPENING_TEXT,
    legacy_doc,
    rar_archive,
    rar_with_traversal_member,
    sample_video,
    text_image_png,
)

RECEIPT_LINES = ["RECEIPT 55182", "MERCHANT: NORTHGATE", "TOTAL: 91.40", "CARD ENDING 4417"]


@pytest.fixture(scope="module")
def text_rig(tmp_path_factory: pytest.TempPathFactory):
    """A provider that CANNOT read images. Frames must be withheld and the omission stated."""
    home = tmp_path_factory.mktemp("reader-m2-text-home")
    with CapturingProvider() as provider, ServedDaemon(Path(home), provider=provider, vision=False) as daemon:
        assert daemon.certify().get("state") == "verified"
        yield daemon, provider


@pytest.fixture(scope="module")
def vision_rig(tmp_path_factory: pytest.TempPathFactory):
    """A provider that declares image input. Frames must actually travel, labelled by timestamp."""
    home = tmp_path_factory.mktemp("reader-m2-vision-home")
    with CapturingProvider() as provider, ServedDaemon(
        Path(home), provider=provider, vision=True, provider_name="reader-stub-vision", model="reader-drive:vision"
    ) as daemon:
        assert daemon.certify().get("state") == "verified"
        yield daemon, provider


def _ask(rig_pair, *, name: str, data: bytes, declared_type: str, question: str, seed: str):
    daemon, provider = rig_pair
    session = canonical_session(seed)
    uploaded = daemon.upload(session_id=session, name=name, data=data, declared_type=declared_type)
    assert uploaded["status"] == 201, uploaded
    provider.reset()
    reply = daemon.chat(question, session_id=session, attachments=[uploaded["attachment"]["id"]])
    assert provider.calls, f"the model was never called: {reply}"
    return "\n".join(provider.payloads()), uploaded["attachment"], provider


# --- legacy DOC ------------------------------------------------------------------------------


def test_legacy_doc_is_converted_and_reaches_the_model(text_rig):
    document = legacy_doc()
    if not document:
        pytest.skip("no legacy .doc converter on this machine; the format is BLOCKED here, not implemented")
    payload, record, _provider = _ask(
        text_rig, name="legacy.doc", data=document, declared_type="application/msword",
        question="What is the document reference in the attached Word file?", seed="m2-doc",
    )
    assert record["reader_format"] == "doc"
    assert "Regional Performance Review" in payload
    assert "OBSIDIAN-5512" in payload
    # The conversion's cost is stated: layout is gone, and the model is told so.
    assert "Converted from legacy .doc" in payload


# --- RAR -------------------------------------------------------------------------------------


def test_rar_members_are_listed_and_the_named_one_is_read(text_rig):
    archive = rar_archive(
        {
            "readme.txt": b"Atlas bundle, prepared by the regional desk\n",
            "data/values.csv": b"item,amount\nalpha,120\nbeta,305\ngamma,77\n",
            "notes/closing.txt": b"Closing token: FLINT-2208\n",
        }
    )
    payload, record, _provider = _ask(
        text_rig, name="bundle.rar", data=archive, declared_type="application/vnd.rar",
        question="What amounts are in values.csv inside the attached rar?", seed="m2-rar",
    )
    assert record["reader_format"] == "rar"
    assert "3 member(s) in bundle.rar" in payload
    assert "member data/values.csv" in payload
    assert "beta,305" in payload
    # Evidence from a member the question did not name still reached the model, because the
    # member is small and textual -- and the listing declares everything either way.
    assert "notes/closing.txt" in payload


def test_rar_traversal_member_is_refused(text_rig):
    payload, _record, _provider = _ask(
        text_rig, name="hostile.rar", data=rar_with_traversal_member(), declared_type="application/vnd.rar",
        question="What is in this archive?", seed="m2-rar-hostile",
    )
    assert "REFUSED: ../../etc/passwd" in payload
    assert "escapes the archive" in payload
    assert "root:x:0:0" not in payload, "a refused member's CONTENT must never reach the model"


# --- image OCR -------------------------------------------------------------------------------


def test_image_text_is_recognised_for_a_model_that_cannot_see(text_rig):
    """A photographed receipt, and a text-only model. Without OCR this turn is unanswerable."""
    from core.artifact_readers import image as image_reader

    if not image_reader.decoders_available()["image_ocr"]:
        pytest.skip("no OCR engine on this machine; image text reading is BLOCKED here, not implemented")
    daemon, provider = text_rig
    session = canonical_session("m2-image-ocr")
    png = text_image_png(RECEIPT_LINES, width=1000, height=520, point=40)
    uploaded = daemon.upload(session_id=session, name="receipt.png", data=png, declared_type="image/png")
    assert uploaded["status"] == 201
    assert uploaded["attachment"]["kind"] == "image", "an image keeps the image lane"
    assert uploaded["attachment"].get("reader_format") == "image", "the manifest says the text was read"
    provider.reset()
    daemon.chat("What is the total on the attached receipt?", session_id=session, attachments=[uploaded["attachment"]["id"]])
    payload = "\n".join(provider.payloads())
    # The words the pixels carry reach the model — the photographed-receipt case is answerable:
    assert "TOTAL: 91.40" in payload
    assert "recognised by" in payload
    # The existing omission truth is preserved: the model is told it did not see the picture.
    assert "Attached image: receipt.png" in payload
    assert "not shown" in payload


def test_image_ocr_reader_recovers_characters_a_vision_model_would_paraphrase():
    """Read directly: the claim is about the OCR engine's output, not about transport."""
    from core.artifact_readers import image as image_reader

    if not image_reader.decoders_available()["image_ocr"]:
        pytest.skip("no OCR engine on this machine; image text reading is BLOCKED here, not implemented")
    result = image_reader.read(text_image_png(RECEIPT_LINES, width=1000, height=520, point=40), name="receipt.png")
    text = result.units[0].text
    assert "55182" in text and "91.40" in text and "4417" in text
    assert result.meta["ocr"]["mean_confidence"] > 0.5
    assert any("OCR reads characters, not pictures" in warning for warning in result.warnings)


# --- video -----------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def silent_clip() -> bytes:
    clip = sample_video(seconds=12, fps=5, with_audio=False)
    if not clip:
        pytest.skip("no video encoder available to build the fixture")
    return clip


@pytest.fixture(scope="module")
def audio_clip() -> bytes:
    clip = sample_video(seconds=12, fps=5, with_audio=True)
    if not clip:
        pytest.skip("no video encoder available to build the fixture")
    return clip


def test_video_event_in_the_middle_reaches_the_model(vision_rig, silent_clip):
    payload, record, provider = _ask(
        vision_rig, name="clip.mp4", data=silent_clip, declared_type="video/mp4",
        question="What is shown in the middle of the attached video?", seed="m2-video-middle",
    )
    assert record["reader_format"] == "video"
    # Beginning, middle and end were all sampled, and the MIDDLE event is the one that proves it.
    assert VIDEO_OPENING_TEXT in payload
    assert VIDEO_EVENT_TEXT in payload, "the middle-of-clip event never reached the model"
    assert VIDEO_CLOSING_TEXT in payload
    # Each frame is addressed by its own timestamp, which is how the answer can cite one.
    assert "t=0:0" in payload
    assert "Cite evidence by frame timestamp" in payload
    # The frames themselves travelled, labelled, because this model declares image input.
    assert provider.total_images() >= 4, f"only {provider.total_images()} frames reached a vision model"
    labels = [label for call in provider.calls for label in call["images"]]
    assert any("t=" in label for label in labels), "frames arrived without their timestamps"


def test_video_sampling_is_disclosed_not_implied(vision_rig, silent_clip):
    payload, _record, _provider = _ask(
        vision_rig, name="clip.mp4", data=silent_clip, declared_type="video/mp4",
        question="Describe the attached video.", seed="m2-video-disclosure",
    )
    assert "frames were sampled" in payload
    assert "was NOT seen" in payload
    assert "not a viewing of it" in payload
    assert "every frame between the sampled timestamps" in payload


def test_silent_video_and_untranscribed_audio_are_reported_differently(vision_rig, silent_clip, audio_clip):
    silent_payload, _r1, _p1 = _ask(
        vision_rig, name="silent.mp4", data=silent_clip, declared_type="video/mp4",
        question="Is anything said in the attached video?", seed="m2-video-silent",
    )
    assert "no audio track at all" in silent_payload

    audio_payload, _r2, _p2 = _ask(
        vision_rig, name="spoken.mp4", data=audio_clip, declared_type="video/mp4",
        question="Is anything said in the attached video?", seed="m2-video-audio",
    )
    assert "has an audio track" in audio_payload
    assert "NOT transcribed" in audio_payload
    assert "unknown, not absent" in audio_payload


def test_a_question_naming_a_timestamp_decodes_that_moment(vision_rig, silent_clip):
    """The targeted re-read: asking about 0:05 decodes 0:05, not just the even sweep."""
    payload, _record, provider = _ask(
        vision_rig, name="clip.mp4", data=silent_clip, declared_type="video/mp4",
        question="What does the attached video show at 0:05?", seed="m2-video-seek",
    )
    assert "t=0:05.000" in payload, "the named moment was never decoded"
    labels = [label for call in provider.calls for label in call["images"]]
    assert any("you asked about this moment" in label for label in labels), "the requested frame was not marked as requested"


def test_frames_are_withheld_from_a_model_that_cannot_see_and_it_is_told(text_rig, silent_clip):
    payload, _record, provider = _ask(
        text_rig, name="clip.mp4", data=silent_clip, declared_type="video/mp4",
        question="What happens in the attached video?", seed="m2-video-noimages",
    )
    assert provider.total_images() == 0, "frames reached a model that cannot read images"
    assert "were NOT shown" in payload
    assert "You have not seen this video" in payload
    # The extraction still travelled: the timestamps and any on-screen text remain available.
    assert "t=0:0" in payload


def test_on_screen_phrasing_is_not_hijacked_by_the_display_recognizer(vision_rig, silent_clip):
    payload, _record, provider = _ask(
        vision_rig, name="clip.mp4", data=silent_clip, declared_type="video/mp4",
        question="What is on screen at 0:05 in the attached video?", seed="m2-video-onscreen",
    )
    assert provider.calls, "the display recognizer answered a turn that carried a video"
    assert "t=0:05.000" in payload


def test_mixed_video_and_host_display_demand_reaches_synthesis(vision_rig, silent_clip):
    payload, _record, provider = _ask(
        vision_rig, name="clip.mp4", data=silent_clip, declared_type="video/mp4",
        question="Describe the attached video; what is my screen resolution?", seed="m2-video-and-host",
    )
    assert VIDEO_EVENT_TEXT in payload
    assert provider.total_images() >= 4
    # A synthesis request carries the measured tool observation, not merely the
    # tool name in a catalogue. The payload must preserve both demands.
    assert "machine.display_inspect" in payload
    assert "Describe the attached video" in payload
