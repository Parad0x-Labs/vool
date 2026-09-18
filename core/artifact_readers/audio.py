"""Audio: bounded, local, on-device transcription of an attached sound file.

The same promise every reader here makes, kept under conditions audio makes uniquely
tempting to break:

* **Local means local.** The transcript comes from the machine's own on-device speech
  recogniser (``speech_tool``, the Speech framework with ``requiresOnDeviceRecognition``).
  There is no cloud speech path, no model download, no "just this once" network call. A
  machine without the dependency refuses typed (``audio_decoder_unavailable`` at the door,
  the recogniser's own typed reason beneath it) — it never returns fake text.
* **Bounded.** At most ``MAX_AUDIO_TRANSCRIBE_S`` seconds are transcribed and at most
  ``MAX_AUDIO_TEXT_CHARS`` characters kept, each cut stated where a reader could otherwise
  stop silently.
* **The segments are the provenance.** Every transcript line carries its own offset
  ("t=1.20-3.84s") and the engine's confidence, so an answer citing the audio can be
  checked against a moment in it, and low-confidence words arrive flagged as uncertain.
"""

from __future__ import annotations

import hashlib
import struct
from typing import Any

from . import speech_tool
from ._limits import MAX_AUDIO_BYTES, MAX_AUDIO_TEXT_CHARS, MAX_AUDIO_TRANSCRIBE_S
from ._sandbox import Scratch, extraction_budget
from ._types import UNIT_TRACK, ReaderRefused, ReaderResult, ReaderUnit

#: Segments carried as their own addressed units before the remainder is merged, stated.
MAX_AUDIO_SEGMENTS = 400

#: Container magic. Bytes decide; the extension only orders the registry's candidates.
_MAGIC: tuple[tuple[int, bytes, str], ...] = (
    (0, b"RIFF", "wav"),
    (0, b"fLaC", "flac"),
    (0, b"ID3", "mp3"),
    (0, b"FORM", "aiff"),
    (0, b"OggS", "ogg"),
    (4, b"ftyp", "m4a"),
)

MEDIA_TYPES: dict[str, str] = {
    "wav": "audio/x-wav",
    "mp3": "audio/mpeg",
    "flac": "audio/flac",
    "ogg": "audio/ogg",
    "m4a": "audio/mp4",
    "aiff": "audio/aiff",
}

EXTENSIONS: tuple[str, ...] = (".wav", ".mp3", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".aif", ".aiff")

#: Bytes of an Ogg stream examined for its codec identification header.
_OGG_PEEK = 128


def sniff(data: bytes) -> bool:
    """True when these bytes are an AUDIO container a recogniser could open.

    Ogg is deliberately selective: an Ogg stream whose identification header says Theora is
    VIDEO and belongs to the video reader, which sits right after this format in the registry.
    Everything OggS carries that speaks Vorbis or Opus is audio and is ours.
    """
    head = bytes(data[: max(_OGG_PEEK, 16)])
    if len(head) < 12:
        return False
    for offset, magic, _kind in _MAGIC:
        if head[offset : offset + len(magic)] == magic:
            break
    else:
        return False
    if head[0:4] == b"RIFF":
        return head[8:12] in (b"WAVE", b"AIFC", b"AIFF")
    if head[0:4] == b"FORM":
        return head[8:12] in (b"AIFF", b"AIFC")
    if head[4:8] == b"ftyp":
        return head[8:12] in (b"M4A ", b"M4B ")
    if head[0:4] == b"OggS":
        identification = head[28:_OGG_PEEK]
        return identification.startswith(b"\x01vorbis") or identification.startswith(b"OpusHead")
    return True


def _wav_facts(data: bytes) -> dict[str, Any]:
    """Duration, channels and rate, parsed natively. A fact about the FILE, not a decode."""
    if bytes(data[:4]) != b"RIFF" or bytes(data[8:12]) != b"WAVE":
        return {}
    facts: dict[str, Any] = {}
    offset = 12
    total = len(data)
    while offset + 8 <= total:
        chunk_id = bytes(data[offset : offset + 4])
        (chunk_size,) = struct.unpack("<I", data[offset + 4 : offset + 8])
        if chunk_id == b"fmt ":
            if chunk_size >= 16:
                audio_format, channels, rate, _byte_rate, _align, bits = struct.unpack(
                    "<HHIIHH", data[offset + 8 : offset + 24]
                )
                facts.update({"channels": channels, "sample_rate": rate, "bits_per_sample": bits, "format": audio_format})
        elif chunk_id == b"data":
            byte_count = min(chunk_size, total - offset - 8)
            if "sample_rate" in facts and "channels" in facts and facts["channels"] and facts.get("bits_per_sample"):
                facts["duration_s"] = round(byte_count / (facts["sample_rate"] * facts["channels"] * facts["bits_per_sample"] // 8), 3)
            break
        offset += 8 + chunk_size + (chunk_size % 2)
        if chunk_size == 0:
            break
    return facts


def decoders_available() -> dict[str, bool]:
    return {"speech": speech_tool.speech_available()}


def media_type_for_extension(extension: str) -> str:
    ext = str(extension or "").lower()
    if ext == ".oga":
        return MEDIA_TYPES["ogg"]
    if ext == ".opus":
        return MEDIA_TYPES["ogg"]
    if ext in (".aif", ".aiff"):
        return MEDIA_TYPES["aiff"]
    return MEDIA_TYPES.get(ext, "audio/x-wav")


def read(
    data: bytes,
    *,
    name: str = "audio.wav",
    media_type: str = "",
    locale: str = speech_tool.DEFAULT_LOCALE,
) -> ReaderResult:
    """Transcribe one audio file on device. Raises the typed reason when this machine cannot."""
    del media_type  # recorded on the manifest by the door; the transcript does not depend on it
    if len(data) > MAX_AUDIO_BYTES:
        raise ReaderRefused(
            f"That audio is {len(data) / (1024 * 1024):.1f} MB, over the {MAX_AUDIO_BYTES // (1024 * 1024)} MB transcription limit; it was not transcribed.",
            code="audio_too_large",
            remediation="Attach a shorter recording, or attach a transcript as text.",
            fmt="audio",
        )
    unavailable = speech_tool.transcribe_unavailable(locale=locale)
    if unavailable is not None:
        raise unavailable
    digest = hashlib.sha256(data).hexdigest()
    wav_facts = _wav_facts(data)
    with Scratch("vool-audio-") as scratch, extraction_budget(scratch=scratch.path):
        audio_path = scratch.path / "input-audio"
        audio_path.write_bytes(data)
        payload = speech_tool.transcribe(
            audio_path, locale=locale, max_seconds=MAX_AUDIO_TRANSCRIBE_S, scratch=scratch.path,
        )
    segments = [segment for segment in (payload.get("segments") or []) if isinstance(segment, dict)]
    complete = payload.get("complete") is True
    duration = float(payload.get("duration_s") or wav_facts.get("duration_s") or 0.0)
    text_parts: list[str] = []
    units: list[ReaderUnit] = []
    warnings: list[str] = []
    engine = str(payload.get("engine") or "Speech.SFSpeechRecognizer")
    kept = 0
    for index, segment in enumerate(segments[:MAX_AUDIO_SEGMENTS]):
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        start = float(segment.get("start") or 0.0)
        end = float(segment.get("end") or start)
        confidence = float(segment.get("confidence") or 0.0)
        if kept + len(text) > MAX_AUDIO_TEXT_CHARS:
            warnings.append(f"The transcript was cut at {kept:,} characters of recognised text.")
            break
        kept += len(text)
        locator = f"t={start:.2f}-{end:.2f}s"
        note = "" if confidence >= 0.55 else " (low confidence: treat these words as uncertain)"
        units.append(
            ReaderUnit(
                locator=locator,
                kind=UNIT_TRACK,
                text=text + note,
                meta={"start_s": start, "end_s": end, "confidence": confidence},
            )
        )
        text_parts.append(f"[{locator}] {text}")
        if index == MAX_AUDIO_SEGMENTS - 1 and len(segments) > MAX_AUDIO_SEGMENTS:
            warnings.append(
                f"Only the first {MAX_AUDIO_SEGMENTS} recognised segments are individually addressed; "
                f"{len(segments) - MAX_AUDIO_SEGMENTS} later segments are summarised as omitted."
            )
    if not units:
        warnings.append("No speech was recognised in this audio. That is not evidence it is silent: music, noise or an unsupported language may not transcribe.")
    warnings.append(
        f"Transcribed locally on this machine ({engine}, locale {locale}, on-device only); nothing was sent to a cloud service."
    )
    if not complete:
        transcribed_to = max((float(unit.meta.get("end_s") or 0.0) for unit in units), default=0.0)
        warnings.append(
            f"Recognition did not finish within the {MAX_AUDIO_TRANSCRIBE_S}s budget: the transcript covers roughly the first "
            f"{transcribed_to:.0f}s of a {duration:.0f}s recording, and what follows is unknown, not absent."
        )
    elif duration and units:
        transcribed_to = max(float(unit.meta.get("end_s") or 0.0) for unit in units)
        if transcribed_to < duration * 0.98:
            warnings.append(
                f"The transcript covers t=0-{transcribed_to:.0f}s of a {duration:.0f}s recording; the rest yielded no recognised speech."
            )
    confidences = [float(segment.get("confidence") or 0.0) for segment in segments if segment.get("confidence")]
    facts: dict[str, Any] = {
        "engine": engine,
        "extractor_version": speech_tool.TOOL_VERSION,
        "locale": locale,
        "on_device": True,
        "complete": complete,
        "duration_s": duration,
        "segments": len(segments),
        "mean_confidence": round(sum(confidences) / len(confidences), 3) if confidences else 0.0,
    }
    facts.update({"wav_" + key: value for key, value in wav_facts.items()})
    return ReaderResult(
        fmt="audio",
        extractor=f"{engine} {speech_tool.TOOL_VERSION}",
        units=tuple(units),
        warnings=tuple(warnings),
        source_sha256=digest,
        omitted=(
            [f"segments {MAX_AUDIO_SEGMENTS + 1}-{len(segments)} merged into the omissions list"]
            if len(segments) > MAX_AUDIO_SEGMENTS
            else []
        ),
        meta=facts,
    )


__all__ = ["EXTENSIONS", "MEDIA_TYPES", "decoders_available", "media_type_for_extension", "read", "sniff"]
