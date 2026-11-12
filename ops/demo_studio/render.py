"""Render the deliverable set from the REAL recording.

Input: the Playwright screen recording of the real product (WebM), captions
already derived from public typed events and redacted, and the validated
event-justified zoom plan. Output: demo.mp4 (H.264 + embedded mov_text
subtitles), demo.webm (source codec, zoomed identically), captions.srt
sidecar, poster.png (a real frame), and a render report with per-artifact
hashes.

Zoom is post-production cropping of the real footage — the pixels stay the
product's; nothing is composited or redrawn. ffmpeg's crop filter has no
per-frame timeline gating, so zoom windows are realized as disjoint
re-encoded segments concatenated in order: total duration and frame order
are preserved, so subtitle timing stays aligned with the original capture.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

FFMPEG_TIMEOUT_S = 600


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _ffmpeg(args: list[str]) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", *args],
        check=True,
        timeout=FFMPEG_TIMEOUT_S,
    )


def _srt_time(seconds: float) -> str:
    total = max(0.0, float(seconds))
    millis = round(total * 1000)
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def write_srt(captions: Sequence[Mapping[str, Any]], path: Path) -> None:
    lines: list[str] = []
    for index, row in enumerate(captions, start=1):
        lines.append(str(index))
        lines.append(f"{_srt_time(row['start_s'])} --> {_srt_time(row['end_s'])}")
        lines.append(str(row["text"]))
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _probe_size(path: Path) -> tuple[int, int]:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, check=True, timeout=60,
        )
        width, height = out.stdout.strip().split(",")[:2]
        return int(width), int(height)
    except Exception:  # fall back to the studio viewport
        return 1280, 800


def _probe_duration_s(path: Path) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, check=True, timeout=60,
        )
        return max(0.0, float(out.stdout.strip()))
    except Exception:  # a zero duration renders one piece
        return 0.0


def _merge_windows(focus_plan: Sequence[Mapping[str, Any]], duration: float) -> list[tuple[float, float]]:
    """Disjoint, ordered zoom windows from the validated plan."""
    raw = sorted(
        (float(e.get("start_s") or 0.0), float(e.get("end_s") or 0.0)) for e in focus_plan
    )
    merged: list[list[float]] = []
    for start, end in raw:
        end = min(end, duration) if duration > 0 else end
        start = max(0.0, start)
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(s, e) for s, e in merged]


def _crop_filter(focus_plan: Sequence[Mapping[str, Any]], src_w: int, src_h: int) -> str | None:
    """One static crop-to-region-then-rescale filter for a zoom segment."""
    for entry in focus_plan:
        box = entry.get("box") or {}
        width = int(box.get("width") or 0)
        height = int(box.get("height") or 0)
        x = int(box.get("x") or 0)
        y = int(box.get("y") or 0)
        if width >= 2 and height >= 2:
            width = max(2, min(width, src_w - x)) - (max(2, min(width, src_w - x)) % 2)
            height = max(2, min(height, src_h - y)) - (max(2, min(height, src_h - y)) % 2)
            if width >= 2 and height >= 2:
                return f"crop={width}:{height}:{max(0, x)}:{max(0, y)},scale={src_w}:{src_h}"
    return None


_ENCODER = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", "-an"]


def render_deliverables(
    *,
    video_source: Path,
    captions: Sequence[Mapping[str, Any]],
    focus_plan: Sequence[Mapping[str, Any]],
    out_dir: Path,
    poster_at_s: float = 0.5,
) -> dict[str, Any]:
    if not ffmpeg_available():
        raise RuntimeError("ffmpeg is required to render demo deliverables")
    out_dir.mkdir(parents=True, exist_ok=True)
    srt_path = out_dir / "captions.srt"
    write_srt(captions, srt_path)

    src_w, src_h = _probe_size(video_source)
    duration = _probe_duration_s(video_source)
    windows = _merge_windows(focus_plan, duration)

    # The segment pipeline: every piece re-encoded with identical settings so
    # the concat is a stream copy; zoom pieces carry their static crop.
    seg_dir = out_dir / "_segments"
    seg_dir.mkdir(exist_ok=True)
    cuts: list[tuple[float, float, str | None]] = []
    cursor = 0.0
    for start, end in windows:
        if start > cursor:
            cuts.append((cursor, start, None))
        cuts.append((start, end, _crop_filter(focus_plan, src_w, src_h)))
        cursor = end
    if duration <= 0 or cursor < duration:
        cuts.append((cursor, duration, None))

    pieces: list[Path] = []
    zoom_applied = 0
    for index, (start, end, crop) in enumerate(cuts):
        piece = seg_dir / f"piece_{index:04d}.mp4"
        args = ["-i", str(video_source)]
        if start > 0:
            args += ["-ss", f"{start:.3f}"]
        args += ["-t", f"{max(0.05, end - start):.3f}"]
        if crop:
            args += ["-vf", crop]
            zoom_applied += 1
        args += [*_ENCODER, str(piece)]
        _ffmpeg(args)
        pieces.append(piece)
    if len(pieces) == 1:
        pieces[0].replace(out_dir / "_raw.mp4")
    else:
        concat_list = seg_dir / "list.txt"
        concat_list.write_text(
            "".join(f"file '{p.resolve()}'\n" for p in pieces), encoding="utf-8"
        )
        _ffmpeg(
            ["-f", "concat", "-safe", "0", "-i", str(concat_list),
             "-c", "copy", str(out_dir / "_raw.mp4")]
        )
    raw_mp4 = out_dir / "_raw.mp4"

    # MP4 deliverable: the (zoomed) real footage + embedded mov_text captions.
    _ffmpeg(
        [
            "-i", str(raw_mp4),
            "-i", str(srt_path),
            "-map", "0:v:0", "-map", "1:0",
            "-c", "copy", "-c:s", "mov_text",
            "-metadata:s:s:0", "language=eng",
            "-movflags", "+faststart",
            str(out_dir / "demo.mp4"),
        ]
    )
    # WebM deliverable: identical footage in the source container.
    _ffmpeg(["-i", str(raw_mp4), "-c:v", "libvpx", "-q:v", "5", "-an", str(out_dir / "demo.webm")])
    # Poster: a real frame from the real recording.
    _ffmpeg(
        ["-ss", f"{max(0.0, poster_at_s):.3f}", "-i", str(out_dir / "demo.mp4"),
         "-frames:v", "1", str(out_dir / "poster.png")]
    )

    shutil.rmtree(seg_dir, ignore_errors=True)
    raw_mp4.unlink(missing_ok=True)

    report: dict[str, Any] = {
        "zoom_applied": zoom_applied,
        "zoom_windows": [[round(s, 3), round(e, 3)] for s, e in windows],
        "zoom_segments": [dict(e) for e in focus_plan],
    }
    for key, name in (("mp4", "demo.mp4"), ("webm", "demo.webm"), ("srt", "captions.srt"), ("poster", "poster.png")):
        digest, size = _hash_file(out_dir / name)
        # The file name relative to the manifest's own folder: the report is
        # copied into a shareable manifest, which never names the machine's folders.
        report[key] = {"path": name, "bytes": size, "sha256": digest}
    return report


def _hash_file(path: Path) -> tuple[str, int]:
    from ops.demo_studio.evidence import sha256_file

    return sha256_file(path)
