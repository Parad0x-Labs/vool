"""Video: timestamped frames a vision model can actually look at, and an honest account of the rest.

No model watches a video. What reaches one is a handful of still frames, and the single most
dishonest thing a video reader can do is let that read as "I watched it." Everything here exists to
make the sampling visible:

* Frames are taken across the WHOLE duration, not the first N seconds, so an event at 4:12 of a
  9-minute clip is reachable. Each frame carries its exact timestamp as its locator.
* The sweep spends only part of the frame budget. The rest stays available for a second,
  question-directed pass -- ``seek`` -- so "what happens just after the sign appears" can be
  answered by decoding around that moment instead of re-uploading the file.
* Every result states how many frames of how many were sampled, at what interval, and that the
  frames between them were not seen. ``sampling_disclosure`` is not optional prose; the attachment
  door renders it into the model-bound payload verbatim.
* Audio is reported as a fact: present-and-not-transcribed, or absent. A silent video and a video
  whose speech nobody transcribed are different situations, and a model told "no audio" about the
  second one would confidently mislead the operator.

``ffprobe``/``ffmpeg`` do the decoding, sandboxed and offline: a video with a remote playlist or an
HLS reference cannot fetch anything, because the process holding it has no network.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import _sandbox
from ._limits import (
    MAX_VIDEO_DURATION_S,
    MAX_VIDEO_FRAMES,
    SUBPROCESS_TIMEOUT_S,
    VIDEO_FRAME_MAX_EDGE,
    VIDEO_SWEEP_FRAMES,
)
from ._sandbox import Scratch, extraction_budget, require_confinement, run_confined
from ._types import UNIT_FRAME, ReaderRefused, ReaderResult, ReaderUnavailable, ReaderUnit

_FFMPEG_CANDIDATES = ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg")
_FFPROBE_CANDIDATES = ("/opt/homebrew/bin/ffprobe", "/usr/local/bin/ffprobe", "/usr/bin/ffprobe")

#: Container magic. The extension is untrusted like every other byte-decided type in this runtime.
_MAGIC: tuple[tuple[int, bytes, str], ...] = (
    (4, b"ftyp", "mp4"),
    (0, b"\x1a\x45\xdf\xa3", "matroska"),
    (0, b"RIFF", "avi"),
    (0, b"OggS", "ogg"),
    (0, b"FLV\x01", "flv"),
)
VIDEO_TYPES: dict[str, tuple[str, ...]] = {
    "video/mp4": (".mp4", ".m4v"),
    "video/quicktime": (".mov",),
    "video/x-matroska": (".mkv",),
    "video/webm": (".webm",),
    "video/x-msvideo": (".avi",),
}
_EXTENSION_TO_TYPE = {ext: media for media, exts in VIDEO_TYPES.items() for ext in exts}


def _which(candidates: tuple[str, ...]) -> str:
    for path in candidates:
        if os.access(path, os.X_OK):
            return path
    found = shutil.which(os.path.basename(candidates[0]))
    return found if found and os.path.isabs(found) else ""


def ffmpeg_path() -> str:
    return _which(_FFMPEG_CANDIDATES)


def ffprobe_path() -> str:
    return _which(_FFPROBE_CANDIDATES)


def decoders_available() -> dict[str, bool]:
    """ffmpeg parses attacker-chosen container bytes, so it is available only when confinable."""
    return {"video": bool(ffmpeg_path() and ffprobe_path() and _sandbox.confinement_available())}


def media_type_for_extension(extension: str) -> str:
    return _EXTENSION_TO_TYPE.get(extension.lower(), "")


def sniff(data: bytes) -> str:
    head = bytes(data[:32])
    for offset, magic, label in _MAGIC:
        if head[offset : offset + len(magic)] == magic:
            if label == "avi" and head[8:12] != b"AVI ":
                continue
            return label
    return ""


@dataclass(frozen=True)
class Frame:
    timestamp: float
    png: bytes
    locator: str


def _timestamp(seconds: float) -> str:
    total = max(0.0, float(seconds))
    minutes, remainder = divmod(total, 60)
    hours, minutes = divmod(int(minutes), 60)
    return (f"{hours:d}:{minutes:02d}:{remainder:06.3f}" if hours else f"{minutes:d}:{remainder:06.3f}")


def _require_tools() -> tuple[str, str]:
    ffmpeg, ffprobe = ffmpeg_path(), ffprobe_path()
    if not ffmpeg or not ffprobe:
        raise ReaderUnavailable(
            "This machine has no video decoder, so the video was not read.",
            code="video_decoder_unavailable",
            remediation="Install ffmpeg to enable reading video attachments.",
            fmt="video",
        )
    return ffmpeg, ffprobe


def probe(data: bytes) -> dict[str, Any]:
    """Duration, streams and whether there is any audio at all -- before a frame is decoded."""
    _, ffprobe = _require_tools()
    with Scratch("vool-video-probe-") as scratch, extraction_budget(scratch=scratch.path):
        source = scratch.path / "input.bin"
        source.write_bytes(data)
        result = run_confined(
            [ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(source)],
            scratch=scratch.path,
            fmt="video",
            timeout=min(SUBPROCESS_TIMEOUT_S, 60),
        )
    if result.returncode != 0:
        raise ReaderRefused(
            "The video could not be opened; the file may be corrupt or use an unsupported codec.",
            code="video_unreadable",
            remediation="Re-export the video as H.264 MP4 and attach it again.",
            fmt="video",
        )
    try:
        payload = json.loads(result.stdout.decode("utf-8", "replace") or "{}")
    except ValueError as exc:
        raise ReaderRefused("The video could not be inspected.", code="video_unreadable", fmt="video") from exc
    streams = [s for s in payload.get("streams") or [] if isinstance(s, dict)]
    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    if not video_streams:
        raise ReaderRefused(
            "That file carries no video stream, so there were no frames to read.",
            code="video_no_stream",
            fmt="video",
        )
    fmt = payload.get("format") if isinstance(payload.get("format"), dict) else {}
    try:
        duration = float(fmt.get("duration") or video_streams[0].get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    first = video_streams[0]
    return {
        "duration_s": duration,
        "width": int(first.get("width") or 0),
        "height": int(first.get("height") or 0),
        "video_codec": str(first.get("codec_name") or ""),
        "has_audio": bool(audio_streams),
        "audio_codec": str(audio_streams[0].get("codec_name") or "") if audio_streams else "",
        "container": str(fmt.get("format_name") or ""),
        "nb_streams": len(streams),
    }


def _extract_frames(source: Path, timestamps: list[float], *, scratch: Path, max_edge: int) -> list[Frame]:
    """One decode per timestamp, seeking to it. Input-seeking makes a 40-minute file as cheap as
    a 40-second one, which is what makes a targeted second pass affordable at all."""
    ffmpeg, _ = _require_tools()
    frames: list[Frame] = []
    for index, when in enumerate(timestamps):
        out = scratch / f"frame-{index:03d}.png"
        result = run_confined(
            [
                ffmpeg, "-nostdin", "-loglevel", "error",
                "-ss", f"{max(0.0, when):.3f}", "-i", str(source),
                "-frames:v", "1",
                "-vf", f"scale='min({max_edge},iw)':-2:flags=bicubic",
                "-f", "image2", "-y", str(out),
            ],
            scratch=scratch,
            fmt="video",
        )
        if result.returncode != 0 or not out.exists() or out.stat().st_size == 0:
            continue
        frames.append(Frame(timestamp=when, png=out.read_bytes(), locator=f"t={_timestamp(when)}"))
        out.unlink(missing_ok=True)
    return frames


def _sweep_timestamps(duration: float, count: int) -> list[float]:
    """Evenly spaced across the whole clip, biased off the exact endpoints.

    The first and last frames of a video are very often black, a title card or a fade -- sampling
    them wastes two of a small budget and produces "the video shows a black screen."
    """
    if duration <= 0:
        return [0.0]
    count = max(1, count)
    if count == 1:
        return [duration / 2]
    step = duration / (count + 1)
    return [round(step * (index + 1), 3) for index in range(count)]


def read(
    data: bytes,
    *,
    name: str = "video.mp4",
    question: str = "",
    frame_budget: int = MAX_VIDEO_FRAMES,
    sweep_frames: int = VIDEO_SWEEP_FRAMES,
    seek: list[float] | None = None,
    ocr_frames: bool = True,
) -> ReaderResult:
    """Sample a video into timestamped frames.

    ``seek`` is the targeted second pass: the exact moments to decode in addition to the sweep.
    Its frames are marked as requested, so a receipt distinguishes "we happened to sample this"
    from "we went and looked because you asked". ``sweep_frames=0`` decodes ONLY the requested
    moments, which is what makes a follow-up question cost one frame instead of a whole re-read.

    ``ocr_frames`` defaults on. On-screen text is most of what a frame carries in the cases people
    actually attach -- a slide, a dashboard, a licence plate, an error dialog -- and it is the only
    thing that reaches a model which cannot see images at all. It is bounded by the frame budget.
    """
    if not sniff(data):
        raise ReaderRefused(f"{name} does not look like a video file.", code="not_a_video", fmt="video")
    require_confinement("video")
    digest = hashlib.sha256(data).hexdigest()
    facts = probe(data)
    duration = float(facts["duration_s"])
    warnings: list[str] = []
    omitted: list[str] = []

    considered = duration
    if duration > MAX_VIDEO_DURATION_S:
        considered = float(MAX_VIDEO_DURATION_S)
        omitted.append(f"everything after {_timestamp(considered)}")
        warnings.append(f"This video is {_timestamp(duration)} long; only the first {_timestamp(considered)} was sampled.")

    sweep = _sweep_timestamps(considered, min(sweep_frames, frame_budget)) if sweep_frames > 0 else []
    requested = [round(float(value), 3) for value in (seek or []) if 0.0 <= float(value) <= max(considered, 0.0)]
    rejected = [float(value) for value in (seek or []) if not (0.0 <= float(value) <= max(considered, 0.0))]
    if rejected:
        warnings.append("Requested timestamps outside the video were not read: " + ", ".join(_timestamp(v) for v in rejected) + ".")
    room = max(0, frame_budget - len(sweep))
    if len(requested) > room:
        omitted.append(f"{len(requested) - room} requested timestamps beyond the frame budget")
        requested = requested[:room]
    wanted = sorted({*sweep, *requested})

    with Scratch("vool-video-") as scratch, extraction_budget(scratch=scratch.path):
        source = scratch.path / "input.bin"
        source.write_bytes(data)
        frames = _extract_frames(source, wanted, scratch=scratch.path, max_edge=VIDEO_FRAME_MAX_EDGE)
        ocr_by_locator: dict[str, tuple[str, dict[str, Any]]] = {}
        if ocr_frames and frames:
            from .vision_tool import ocr_available, ocr_text_with_confidence

            if ocr_available():
                for frame in frames:
                    path = scratch.path / f"ocr-{frame.locator}.png".replace(":", "-")
                    path.write_bytes(frame.png)
                    try:
                        text, ocr_facts = ocr_text_with_confidence(path, scratch=scratch.path)
                    except Exception:
                        continue
                    finally:
                        path.unlink(missing_ok=True)
                    if text.strip():
                        ocr_by_locator[frame.locator] = (text, ocr_facts)

    if not frames:
        raise ReaderRefused(
            f"No frames could be decoded from {name}; the video may be corrupt or use an unsupported codec.",
            code="video_no_frames",
            remediation="Re-export the video as H.264 MP4 and attach it again.",
            fmt="video",
        )

    requested_set = set(requested)
    units: list[ReaderUnit] = []
    for frame in frames:
        note: list[str] = []
        meta: dict[str, Any] = {
            "timestamp_s": frame.timestamp,
            "png_bytes": frame.png,
            "media_type": "image/png",
            "requested": frame.timestamp in requested_set,
        }
        if frame.timestamp in requested_set:
            note.append("decoded because this moment was asked about")
        text = ""
        if frame.locator in ocr_by_locator:
            text, ocr_facts = ocr_by_locator[frame.locator]
            meta["ocr"] = ocr_facts
            note.append(f"on-screen text read by OCR (mean confidence {ocr_facts['mean_confidence']:.2f})")
        units.append(ReaderUnit(locator=frame.locator, kind=UNIT_FRAME, text=text, warnings=tuple(note), meta=meta))

    interval = (considered / (len(frames) or 1)) if considered else 0.0
    disclosure = (
        f"{len(frames)} frames were sampled from a {_timestamp(duration)} video "
        f"(roughly one every {interval:.1f}s). Everything between those frames was NOT seen; "
        f"any claim about the whole video is an inference from these samples, not a viewing of it."
    )
    if facts["has_audio"]:
        audio_note = (
            f"This video has an audio track ({facts['audio_codec']}). It was NOT transcribed — no speech "
            f"recogniser is wired into this reader — so anything spoken is unknown, not absent."
        )
    else:
        audio_note = "This video has no audio track at all, so there is nothing spoken to miss."
    warnings.extend([disclosure, audio_note])
    omitted.append("every frame between the sampled timestamps")

    return ReaderResult(
        fmt="video",
        extractor="ffmpeg/ffprobe via vool.video 1.0.0",
        units=tuple(units),
        warnings=tuple(warnings),
        omitted=tuple(omitted),
        source_sha256=digest,
        meta={
            **{key: value for key, value in facts.items()},
            "frames_sampled": len(frames),
            "frame_timestamps": [frame.timestamp for frame in frames],
            "requested_timestamps": requested,
            "sweep_timestamps": sweep,
            "sampling_disclosure": disclosure,
            "audio_disclosure": audio_note,
            "audio_transcribed": False,
            "duration_considered_s": considered,
        },
    )


__all__ = ["VIDEO_TYPES", "Frame", "decoders_available", "ffmpeg_path", "ffprobe_path", "media_type_for_extension", "probe", "read", "sniff"]
