"""Dictation: the ONE authority for turning a spoken composer recording into text.

Bounded and local, exactly like the audio attachment reader it shares a recogniser with
(``core.artifact_readers.speech_tool``): on device only, at most ``MAX_DICTATION_S`` seconds,
at most ``MAX_DICTATION_BYTES`` of audio, with the typed unavailability of the underlying
recogniser passed through verbatim rather than reworded into something vaguer.

A transcript that leaves this module is DRAFT TEXT FOR THE COMPOSER. It is never dispatched
as a turn by itself: the words land in the composer's input, where the operator can edit or
discard them before anything is sent. Whatever a transcript says, it was said by the person
holding the microphone, and it is treated as their input only once they send it — the same
authority law that governs every other input path (see ``core.agent_runtime.request_authority``).
"""

from __future__ import annotations

from typing import Any

from core.artifact_readers import speech_tool
from core.artifact_readers._limits import MAX_DICTATION_BYTES, MAX_DICTATION_S
from core.artifact_readers._sandbox import Scratch, extraction_budget
from core.artifact_readers._types import ReaderFailed, ReaderRefused, ReaderUnavailable


class DictationUnavailable(Exception):
    # matching core.artifact_readers._types.ReaderUnavailable's identical convention.
    """A typed answer about THIS machine, not an error to hide behind a generic message."""

    def __init__(self, code: str, message: str, remediation: str = "", *, http_status: int = 503) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.remediation = remediation
        self.http_status = http_status

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"ok": False, "error": self.code, "message": self.message}
        if self.remediation:
            payload["remediation"] = self.remediation
        return payload


ACCEPTED_TYPES: dict[str, str] = {
    # The composer records what the browser gives it. AVFoundation opens the first family; the
    # recogniser itself decides whether it can truly read the stream.
    "audio/x-wav": ".wav",
    "audio/wav": ".wav",
    "audio/wave": ".wav",
    "audio/mp4": ".m4a",
    "audio/aac": ".m4a",
    "audio/mpeg": ".mp3",
    "audio/ogg": ".ogg",
    "audio/webm": ".webm",
    "audio/x-flac": ".flac",
    "audio/flac": ".flac",
}


def availability(*, locale: str = speech_tool.DEFAULT_LOCALE) -> dict[str, Any]:
    """Whether dictation can run HERE, and the typed reason when it cannot."""
    unavailable = speech_tool.transcribe_unavailable(locale=locale)
    if unavailable is not None:
        return {
            "available": False,
            "error": unavailable.code,
            "message": unavailable.message,
            "remediation": unavailable.remediation,
        }
    return {"available": True, "locale": locale, "engine": "Speech.SFSpeechRecognizer"}


def transcribe(data: bytes, *, media_type: str = "", locale: str = speech_tool.DEFAULT_LOCALE) -> dict[str, Any]:
    """One dictation recording, transcribed on device. Raises ``DictationUnavailable`` when absent deps say no."""
    del media_type  # the recogniser reads the bytes it is given; the declared type is advisory
    if len(data) == 0:
        raise DictationUnavailable("empty_recording", "That recording is empty; there is nothing to transcribe.", http_status=400)
    if len(data) > MAX_DICTATION_BYTES:
        raise DictationUnavailable(
            "recording_too_large",
            f"That recording is {len(data) / (1024 * 1024):.1f} MB, over the {MAX_DICTATION_BYTES // (1024 * 1024)} MB dictation limit.",
            "Keep dictation notes short; this is a composer input, not an archive.",
            http_status=413,
        )
    unavailable = speech_tool.transcribe_unavailable(locale=locale)
    if unavailable is not None:
        raise DictationUnavailable(unavailable.code, unavailable.message, unavailable.remediation)
    try:
        with Scratch("vool-dictation-") as scratch, extraction_budget(scratch=scratch.path):
            audio_path = scratch.path / "dictation-input"
            audio_path.write_bytes(data)
            payload = speech_tool.transcribe(
                audio_path, locale=locale, max_seconds=MAX_DICTATION_S, scratch=scratch.path,
            )
    except ReaderUnavailable as exc:
        raise DictationUnavailable(exc.code, exc.message, exc.remediation) from exc
    except ReaderRefused as exc:
        raise DictationUnavailable(exc.code, exc.message, exc.remediation) from exc
    except ReaderFailed as exc:
        raise DictationUnavailable(exc.code, exc.message, exc.remediation) from exc
    segments = [segment for segment in (payload.get("segments") or []) if isinstance(segment, dict)]
    text = " ".join(str(segment.get("text") or "").strip() for segment in segments).strip()
    complete = payload.get("complete") is True
    duration = float(payload.get("duration_s") or 0.0)
    return {
        "ok": True,
        "text": text,
        "complete": complete,
        "duration_s": duration,
        "engine": str(payload.get("engine") or "Speech.SFSpeechRecognizer"),
        "locale": locale,
        "on_device": True,
        "disclosure": (
            ""
            if complete
            else f"Recognition stopped at the {MAX_DICTATION_S}s budget; the recording may continue past what was transcribed."
        ),
    }


__all__ = ["ACCEPTED_TYPES", "DictationUnavailable", "availability", "transcribe"]
