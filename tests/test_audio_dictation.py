"""Bounded local audio transcription and dictation, with typed absence when deps say no.

The claims under test, each against something that can fail:

* **Registry truth.** Audio is a row in the table (BEFORE video, whose RIFF/OggS magics
  overlap it); bytes decide; Ogg/Theora is video, Ogg/Vorbis and Opus are audio.
* **Typed absence.** On a machine without the on-device recogniser, the capability report
  says why, the door refuses the upload with ``audio_decoder_unavailable``, and dictation
  reports the recogniser's own reason — never a silent empty answer.
* **Bounded transcription.** With the recogniser stubbed, the transcript is addressed by
  segment time ("t=1.20-3.84s"), carries confidence, is cut at the character bound WITH THE
  CUT STATED, and its disclosures say local/on-device/cloud-never in plain words.
* **Dictation is draft text.** The module returns text for the composer; it never dispatches
  a turn, and its bounds (bytes, duration) are enforced before any decode.
"""

from __future__ import annotations

import io
import wave

import pytest

from core import chat_attachments, dictation
from core.artifact_readers import REGISTRY, audio, speech_tool
from core.artifact_readers import read as readers_read
from core.artifact_readers._types import ReaderUnavailable


def _wav_bytes(seconds: float = 1.0, rate: int = 8000) -> bytes:
    """A real, tiny WAV: silence, but a genuine header the parser can read."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * int(rate * seconds))
    return buffer.getvalue()


def _registry_row(fmt: str):
    return next(spec for spec in REGISTRY if spec.fmt == fmt)


def _stub_recogniser(monkeypatch, *, segments=None, complete=True, duration_s=1.0):
    """An authorized on-device recogniser with a scripted transcript."""
    monkeypatch.setattr(speech_tool, "transcribe_unavailable", lambda locale=speech_tool.DEFAULT_LOCALE: None)
    monkeypatch.setattr(speech_tool, "toolchain_available", lambda: True)

    def fake_transcribe(path, *, locale=speech_tool.DEFAULT_LOCALE, max_seconds, scratch):
        return {
            "engine": "Speech.SFSpeechRecognizer",
            "locale": locale,
            "duration_s": duration_s,
            "on_device": True,
            "complete": complete,
            "segments": [
                {"start": 0.2, "end": 1.4, "text": "the deploy window closes at noon", "confidence": 0.91},
                {"start": 1.6, "end": 3.8, "text": "confirm with the duty officer", "confidence": 0.42},
            ] if segments is None else segments,
        }

    monkeypatch.setattr(speech_tool, "transcribe", fake_transcribe)
    from core.artifact_readers import audio as audio_module

    monkeypatch.setattr(audio_module, "decoders_available", lambda: {"speech": True})


# --- registry truth --------------------------------------------------------------------------------


def test_audio_is_registered_before_video_and_claims_its_own_extensions():
    formats = [spec.fmt for spec in REGISTRY]
    assert "audio" in formats and "video" in formats
    assert formats.index("audio") < formats.index("video"), "the OggS/RIFF magics overlap: audio must decide first"
    row = _registry_row("audio")
    assert ".wav" in row.extensions and ".mp3" in row.extensions and ".m4a" in row.extensions
    assert row.unit_kind == "track"
    assert "on device" in row.notes


def test_bytes_decide_ogg_audio_from_ogg_video():
    vorbis = b"OggS" + b"\x00" * 24 + b"\x01vorbis" + b"\x00" * 32
    opus = b"OggS" + b"\x00" * 24 + b"OpusHead" + b"\x00" * 32
    theora = b"OggS" + b"\x00" * 24 + b"\x80theora" + b"\x00" * 32
    assert audio.sniff(vorbis) and audio.sniff(opus)
    assert not audio.sniff(theora), "Theora is video and belongs to the video reader"
    from core.artifact_readers import detect

    assert detect(vorbis, extension=".ogg").fmt == "audio"
    assert detect(theora, extension=".ogg").fmt == "video"


def test_a_riff_container_that_is_not_audio_is_not_claimed_by_audio():
    assert not audio.sniff(b"RIFF\x00\x00\x00\x00AVI LIST" + b"\x00" * 32), "an AVI is video"


def test_wav_header_facts_are_parsed_natively():
    data = _wav_bytes(seconds=2.0, rate=8000)
    facts = audio._wav_facts(data)
    assert facts["channels"] == 1 and facts["sample_rate"] == 8000
    assert abs(facts["duration_s"] - 2.0) < 0.01


# --- typed absence ---------------------------------------------------------------------------------


def test_capability_report_lists_audio_with_its_reason_when_absent(monkeypatch):
    from core import artifact_readers

    monkeypatch.setattr(audio, "decoders_available", lambda: {"speech": False})
    report = artifact_readers.capability_report()
    row = next(item for item in report["formats"] if item["format"] == "audio")
    assert row["available"] is False
    assert "speech recogniser" in row["blocked_reason"]


def test_an_audio_upload_on_a_machine_without_the_recogniser_is_refused_typed(monkeypatch):
    monkeypatch.setattr(audio, "decoders_available", lambda: {"speech": False})
    with pytest.raises(chat_attachments.AttachmentRefused) as caught:
        chat_attachments.stage_attachment(
            session_id="audio-lane-session",
            declared_name="memo.wav",
            declared_type="audio/x-wav",
            data=_wav_bytes(),
        )
    assert caught.value.code == "audio_decoder_unavailable"
    assert caught.value.http_status == 415
    assert "speech recogniser" in caught.value.message
    assert "was not accepted" in caught.value.message


def test_dictation_reports_the_recognisers_own_typed_reason(monkeypatch):
    monkeypatch.setattr(
        speech_tool, "transcribe_unavailable",
        lambda locale=speech_tool.DEFAULT_LOCALE: ReaderUnavailable(
            "This machine has not granted Speech Recognition access.",
            code="speech_recognizer_unauthorized",
            remediation="Enable Speech Recognition in System Settings.",
            fmt="audio",
        ),
    )
    facts = dictation.availability()
    assert facts["available"] is False
    assert facts["error"] == "speech_recognizer_unauthorized"
    with pytest.raises(dictation.DictationUnavailable) as caught:
        dictation.transcribe(_wav_bytes(), media_type="audio/x-wav")
    assert caught.value.code == "speech_recognizer_unauthorized"
    assert caught.value.http_status == 503
    assert caught.value.remediation


# --- bounded transcription (stubbed recogniser) -----------------------------------------------------


def test_transcription_is_segment_addressed_with_confidence_and_local_disclosure(monkeypatch):
    _stub_recogniser(monkeypatch)
    result = readers_read(_wav_bytes(seconds=4.0), name="memo.wav", extension=".wav")
    assert result.fmt == "audio"
    assert [unit.locator for unit in result.units] == ["t=0.20-1.40s", "t=1.60-3.80s"]
    assert "duty officer (low confidence" in result.units[1].text
    rendered = result.rendered_text()
    assert "t=0.20-1.40s" in rendered
    assert any("on-device only" in warning and "cloud service" in warning for warning in result.warnings)


def test_the_transcript_is_cut_at_the_character_bound_and_says_so(monkeypatch):
    word = "persist " * 112  # ~900 characters per recognised segment
    segments = [
        {"start": float(i) * 2.0, "end": float(i) * 2.0 + 1.9, "text": word, "confidence": 0.9}
        for i in range(200)
    ]
    _stub_recogniser(monkeypatch, segments=segments)
    result = readers_read(_wav_bytes(seconds=400.0), name="long.wav", extension=".wav")
    total = sum(len(unit.text) for unit in result.units)
    assert total <= audio.MAX_AUDIO_TEXT_CHARS + len(word)
    assert any("was cut at" in warning for warning in result.warnings), "the cut must be stated"


def test_an_incomplete_recognition_discloses_what_was_never_transcribed(monkeypatch):
    _stub_recogniser(monkeypatch, complete=False, duration_s=900.0)
    result = readers_read(_wav_bytes(seconds=4.0), name="memo.wav", extension=".wav")
    assert result.meta["complete"] is False
    assert any("did not finish" in warning and "unknown, not absent" in warning for warning in result.warnings)


def test_an_over_limit_audio_file_is_refused_before_any_decode(monkeypatch):
    from core.artifact_readers._types import ReaderRefused

    monkeypatch.setattr(audio, "MAX_AUDIO_BYTES", 1024)
    with pytest.raises(ReaderRefused) as caught:
        audio.read(_wav_bytes() + b"\x00" * 2048, name="big.wav")
    assert caught.value.code == "audio_too_large"


# --- dictation ---------------------------------------------------------------------------------------


def test_dictation_returns_draft_text_and_never_a_turn(monkeypatch):
    _stub_recogniser(monkeypatch)
    result = dictation.transcribe(_wav_bytes(seconds=4.0), media_type="audio/x-wav")
    assert result["ok"] is True
    assert "deploy window" in result["text"]
    assert result["complete"] is True and result["on_device"] is True
    assert result.get("messages") is None and result.get("dispatch") is None


def test_dictation_bounds_run_before_any_decode(monkeypatch):
    from core.artifact_readers._limits import MAX_DICTATION_BYTES

    with pytest.raises(dictation.DictationUnavailable) as empty:
        dictation.transcribe(b"", media_type="audio/x-wav")
    assert empty.value.code == "empty_recording" and empty.value.http_status == 400
    with pytest.raises(dictation.DictationUnavailable) as big:
        dictation.transcribe(b"\x00" * (MAX_DICTATION_BYTES + 1), media_type="audio/x-wav")
    assert big.value.code == "recording_too_large" and big.value.http_status == 413


def test_a_staged_audio_transcript_goes_through_the_same_secret_scan(monkeypatch):
    _stub_recogniser(
        monkeypatch,
        segments=[{"start": 0.0, "end": 2.0, "text": "the access key is AKIAABCDEFGHIJKLMNOP in the vault", "confidence": 0.95}],
    )
    with pytest.raises(chat_attachments.AttachmentRefused) as caught:
        chat_attachments.stage_attachment(
            session_id="audio-lane-session",
            declared_name="memo.wav",
            declared_type="audio/x-wav",
            data=_wav_bytes(seconds=2.0),
        )
    assert caught.value.code == "secret_detected"
    assert caught.value.http_status == 422
