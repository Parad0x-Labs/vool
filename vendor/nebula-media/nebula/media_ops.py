"""nebula.media_ops — local media editing mechanisms for M1.

Pure ffmpeg/ffprobe mechanisms: every operation reads an explicit source and
writes an explicit destination. Sources are never mutated (non-destructive by
construction). No VOOL concepts live here: no projects, receipts, skills,
permissions, chat.

All public ops raise :class:`MediaOpError` with a stable machine-readable
``code`` on failure — corrupt input is a controlled typed failure, not a crash.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import asdict, is_dataclass
from pathlib import Path

log = logging.getLogger(__name__)

FFMPEG_TIMEOUT = 600


class MediaOpError(Exception):
    """Typed failure for any media operation."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code          # invalid_media | io_error | unsupported | op_failed | timeout
        self.message = message

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message}


def _bin(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise MediaOpError("op_failed", f"{name} not found on PATH")
    return found


def _run(cmd: list[str], timeout: int = FFMPEG_TIMEOUT) -> subprocess.CompletedProcess:
    """Run a subprocess; convert every failure mode into a typed error."""
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise MediaOpError("timeout", f"operation timed out after {timeout}s") from exc
    except OSError as exc:
        raise MediaOpError("op_failed", f"failed to spawn {cmd[0]}: {exc}") from exc
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-3:]
        raise MediaOpError("op_failed", "; ".join(tail) or f"{cmd[0]} exited {proc.returncode}")
    return proc


def _check_src(src: Path) -> Path:
    src = Path(src)
    if not src.is_file():
        raise MediaOpError("io_error", f"source not found: {src}")
    return src


def _ensure_parent(dst: Path) -> Path:
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    return dst


# ---------------------------------------------------------------- probe

def probe(src: Path) -> dict:
    """Full media metadata (video + audio streams) via ffprobe."""
    src = _check_src(Path(src))
    proc = _run([
        _bin("ffprobe"), "-v", "quiet", "-print_format", "json",
        "-show_streams", "-show_format", str(src),
    ], timeout=30)
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise MediaOpError("invalid_media", f"unparseable ffprobe output for {src}") from exc

    vstream = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    astream = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    fmt = data.get("format", {})
    if vstream is None and astream is None:
        raise MediaOpError("invalid_media", f"no media streams in '{src}'")

    def _rate(s: dict, key: str, default: float) -> float:
        raw = s.get(key, "")
        try:
            num, den = raw.split("/")
            den_f = float(den)
            return float(num) / den_f if den_f else default
        except (ValueError, ZeroDivisionError):
            return default

    out: dict = {
        "path": str(src),
        "duration": float(fmt.get("duration", vstream.get("duration", 0) if vstream else 0) or 0),
        "size": int(fmt.get("size", src.stat().st_size)),
        "container": fmt.get("format_name", ""),
        "has_video": vstream is not None,
        "has_audio": astream is not None,
    }
    if vstream:
        out.update({
            "width": int(vstream.get("width", 0)),
            "height": int(vstream.get("height", 0)),
            "fps": round(_rate(vstream, "avg_frame_rate", 24.0), 3),
            "video_codec": vstream.get("codec_name", ""),
            "pix_fmt": vstream.get("pix_fmt", ""),
        })
    if astream:
        out["audio"] = {
            "codec": astream.get("codec_name", ""),
            "channels": int(astream.get("channels", 0)),
            "sample_rate": int(astream.get("sample_rate", 0)),
        }
    return out


# ------------------------------------------------------------ video edits

_X264 = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23"]
_AAC = ["-c:a", "aac", "-b:a", "128k"]


def _encode_cmd(src: Path, dst: Path, *, ss: float | None = None,
                dur: float | None = None, vf: list[str] | None = None,
                af: list[str] | None = None, extra_v: list[str] | None = None,
                no_audio: bool = False, audio_only: bool = False,
                faststart: bool = True) -> list[str]:
    cmd = [_bin("ffmpeg"), "-y", "-v", "error"]
    if ss is not None:
        cmd += ["-ss", f"{ss:.6f}"]
    cmd += ["-i", str(src)]
    if dur is not None:
        cmd += ["-t", f"{dur:.6f}"]
    if audio_only:
        cmd += ["-vn"]
    elif vf:
        cmd += ["-vf", ",".join(vf)]
    if af:
        cmd += ["-af", ",".join(af)]
    if not audio_only:
        cmd += _X264 + (extra_v or [])
    if no_audio:
        cmd += ["-an"]
    elif not audio_only:
        cmd += _AAC
    if faststart and dst.suffix == ".mp4":
        cmd += ["-movflags", "+faststart"]
    cmd.append(str(dst))
    return cmd


def trim(src: Path, dst: Path, start: float, end: float) -> dict:
    """Cut [start, end) seconds into dst (re-encoded, frame accurate)."""
    src = _check_src(Path(src))
    if start < 0 or end <= start:
        raise MediaOpError("unsupported", f"invalid trim range [{start}, {end})")
    info = probe(src)
    end = min(end, info["duration"])
    if start >= end:
        raise MediaOpError("unsupported", f"trim range [{start}, {end}) outside media")
    _run(_encode_cmd(src, _ensure_parent(Path(dst)), ss=start, dur=end - start))
    return probe(dst)


def remove_range(src: Path, dst: Path, start: float, end: float) -> dict:
    """Cut out [start, end) from the middle in one pass (trim+concat filter)."""
    src = _check_src(Path(src))
    if end <= start or start < 0:
        raise MediaOpError("unsupported", f"invalid removal range [{start}, {end})")
    info = probe(src)
    end = min(end, info["duration"])
    if start >= end:
        raise MediaOpError("unsupported", f"removal range [{start}, {end}) outside media")
    has_audio = bool(info.get("has_audio"))
    parts = [f"[0:v]trim=start=0:end={start:.6f},setpts=PTS-STARTPTS[v0]"]
    a_parts = [f"[0:a]atrim=start=0:end={start:.6f},asetpts=PTS-STARTPTS[a0]"] if has_audio else []
    tail = f"[0:v]trim=start={end:.6f},setpts=PTS-STARTPTS[v1]"
    parts.append(tail)
    if has_audio:
        a_parts.append(f"[0:a]atrim=start={end:.6f},asetpts=PTS-STARTPTS[a1]")
    n_streams = 2 + (1 if has_audio else 0)
    concat_ins = "".join("[v0][v1]" if not has_audio else "[v0][a0][v1][a1]")
    fc = ";".join(parts + a_parts + [f"{concat_ins}concat=n={n_streams}:v=1:a={int(has_audio)}[v][a]"
                                     if has_audio else "[v0][v1]concat=n=2:v=1:a=0[v]"])
    cmd = [_bin("ffmpeg"), "-y", "-v", "error", "-i", str(src),
           "-filter_complex", fc]
    if has_audio:
        cmd += ["-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast",
                "-crf", "23", "-c:a", "aac"]
    else:
        cmd += ["-map", "[v]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23"]
    if dst.suffix == ".mp4":
        cmd += ["-movflags", "+faststart"]
    _run(cmd + [str(_ensure_parent(Path(dst)))])
    return probe(dst)


def split(src: Path, out_dir: Path, times: list[float]) -> list[dict]:
    """Split at time points into consecutive segments [0,t1),[t1,t2)..."""
    src = _check_src(Path(src))
    pts = sorted(float(t) for t in times)
    info = probe(src)
    bounds = [0.0] + pts + [info["duration"]]
    if any(b <= prev for prev, b in zip(bounds, bounds[1:])):
        raise MediaOpError("unsupported", f"invalid split times {times}")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    segs = []
    stem = src.stem
    for i, (a, b) in enumerate(zip(bounds, bounds[1:])):
        seg_path = out_dir / f"{stem}.part{i + 1}.mp4"
        _run(_encode_cmd(src, seg_path, ss=a, dur=b - a))
        segs.append(probe(seg_path))
    return segs


def crop(src: Path, dst: Path, width: int, height: int, x: int, y: int) -> dict:
    src = _check_src(Path(src))
    info = probe(src)
    if not (info.get("has_video") and width > 0 and height > 0
            and x >= 0 and y >= 0 and x + width <= info["width"]
            and y + height <= info["height"]):
        raise MediaOpError(
            "unsupported",
            f"crop rect {width}x{height}+{x}+{y} invalid for {info['width']}x{info['height']}",
        )
    w, h = width - width % 2, height - height % 2  # keep yuv420p-happy dims
    _run(_encode_cmd(src, _ensure_parent(Path(dst)), vf=[f"crop={w}:{h}:{x}:{y}"]))
    return probe(dst)


def resize(src: Path, dst: Path, width: int, height: int) -> dict:
    src = _check_src(Path(src))
    if width <= 0 or height <= 0:
        raise MediaOpError("unsupported", f"invalid target size {width}x{height}")
    w, h = width - width % 2, height - height % 2
    _run(_encode_cmd(src, _ensure_parent(Path(dst)), vf=[f"scale={w}:{h}"]))
    return probe(dst)


ASPECT_PRESETS = {"16:9": (16, 9), "9:16": (9, 16), "1:1": (1, 1), "4:5": (4, 5)}


def aspect(src: Path, dst: Path, ratio: str, long_edge: int = 1080) -> dict:
    """Center-crop + scale source to an aspect preset (fill, no letterbox)."""
    if ratio not in ASPECT_PRESETS:
        raise MediaOpError("unsupported",
                           f"unknown aspect '{ratio}', expected one of {sorted(ASPECT_PRESETS)}")
    aw, ah = ASPECT_PRESETS[ratio]
    if aw >= ah:
        tw, th = long_edge, long_edge * ah // aw
    else:
        th, tw = long_edge, long_edge * aw // ah
    tw -= tw % 2
    th -= th % 2
    vf = [f"scale={tw}:{th}:force_original_aspect_ratio=increase",
          f"crop={tw}:{th}"]
    _run(_encode_cmd(src, _ensure_parent(Path(dst)), vf=vf))
    return probe(dst)


def rotate(src: Path, dst: Path, degrees: int) -> dict:
    """Rotate by 90/180/270 degrees clockwise."""
    transposes = {90: "transpose=1", 180: "transpose=1,transpose=1", 270: "transpose=2"}
    if degrees not in transposes:
        raise MediaOpError("unsupported", f"rotation must be one of 90/180/270, got {degrees}")
    _run(_encode_cmd(src, _ensure_parent(Path(dst)), vf=[transposes[degrees]]))
    return probe(dst)


# ------------------------------------------------------------ audio edits

def set_volume(src: Path, dst: Path, factor: float) -> dict:
    """Set audio volume (0.0 = mute). Video re-encoded alongside."""
    src = _check_src(Path(src))
    if factor < 0 or factor > 10:
        raise MediaOpError("unsupported", f"volume factor {factor} outside [0, 10]")
    _run(_encode_cmd(src, _ensure_parent(Path(dst)), af=[f"volume={factor:.4f}"]))
    return probe(dst)


def normalize_audio(src: Path, dst: Path) -> dict:
    """EBU R128 loudness normalization (I=-16 LUFS)."""
    src = _check_src(Path(src))
    _run(_encode_cmd(src, _ensure_parent(Path(dst)),
                     af=["loudnorm=I=-16:TP=-1.5:LRA=11"]))
    return probe(dst)


def extract_audio(src: Path, dst: Path) -> dict:
    """Audio-only export (.wav or .m4a/.aac per destination suffix)."""
    src = _check_src(Path(src))
    dst = _ensure_parent(Path(dst))
    if dst.suffix.lower() == ".wav":
        cmd = [_bin("ffmpeg"), "-y", "-v", "error", "-i", str(src), "-vn",
               "-c:a", "pcm_s16le", str(dst)]
    else:
        cmd = [_bin("ffmpeg"), "-y", "-v", "error", "-i", str(src), "-vn",
               "-c:a", "aac", "-b:a", "192k", str(dst)]
    _run(cmd)
    return probe(dst)


# ------------------------------------------------------------ compression

COMPRESS_PROFILES = {
    "social":   {"crf": 26, "max_height": 1080, "audio_kbps": "128k"},
    "high":     {"crf": 22, "max_height": 2160, "audio_kbps": "192k"},
    "small":    {"crf": 30, "max_height": 720,  "audio_kbps": "96k"},
}


def compress(src: Path, dst: Path, profile: str = "social") -> dict:
    """Compress to an H.264/AAC profile suited for social platforms."""
    src = _check_src(Path(src))
    if profile not in COMPRESS_PROFILES:
        raise MediaOpError("unsupported",
                           f"unknown profile '{profile}', expected {sorted(COMPRESS_PROFILES)}")
    p = COMPRESS_PROFILES[profile]
    info = probe(src)
    vf = None
    if info.get("has_video") and info["height"] > p["max_height"]:
        mh = p["max_height"] - p["max_height"] % 2
        vf = [f"scale=-2:'min({mh},ih)'"]
    cmd = [_bin("ffmpeg"), "-y", "-v", "error", "-i", str(src)]
    if vf:
        cmd += ["-vf", ",".join(vf)]
    if info.get("has_video"):
        cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", str(p["crf"]),
                "-pix_fmt", "yuv420p"]
    if info.get("has_audio"):
        cmd += ["-c:a", "aac", "-b:a", p["audio_kbps"]]
    if Path(dst).suffix == ".mp4":
        cmd += ["-movflags", "+faststart"]
    _run(cmd + [str(_ensure_parent(Path(dst)))])
    return probe(dst)


# ---------------------------------------------------- preview / inspection

def thumbnail(src: Path, dst: Path, time: float = 0.0) -> dict:
    """Grab a PNG frame at ``time`` seconds."""
    src = _check_src(Path(src))
    _run([_bin("ffmpeg"), "-y", "-v", "error", "-ss", f"{max(0.0, time):.6f}",
          "-i", str(src), "-frames:v", "1", str(_ensure_parent(Path(dst)))])
    return {"path": str(dst), "time": time}


def waveform(src: Path, dst: Path, width: int = 1200, height: int = 160) -> dict:
    """Render an amplitude-over-time PNG of the audio track."""
    src = _check_src(Path(src))
    info = probe(src)
    if not info.get("has_audio"):
        raise MediaOpError("unsupported", f"no audio stream in '{src}'")
    _run([_bin("ffmpeg"), "-y", "-v", "error", "-i", str(src),
          "-filter_complex",
          f"showwavespic=s={width}x{height}:colors=white", "-frames:v", "1",
          str(_ensure_parent(Path(dst)))])
    return {"path": str(dst), "width": width, "height": height}


def proxy(src: Path, dst: Path, max_height: int = 480) -> dict:
    """Low-cost preview proxy encode for editor scrubbing."""
    src = _check_src(Path(src))
    mh = max_height - max_height % 2
    _run(_encode_cmd(src, _ensure_parent(Path(dst)),
                     vf=[f"scale=-2:'min({mh},ih)'"],
                     extra_v=["-pix_fmt", "yuv420p"]))
    return probe(dst)


# ------------------------------------------------------------ worker API

OPS = {
    "probe": probe,
    "trim": trim,
    "remove_range": remove_range,
    "split": split,
    "crop": crop,
    "resize": resize,
    "aspect": aspect,
    "rotate": rotate,
    "set_volume": set_volume,
    "normalize_audio": normalize_audio,
    "extract_audio": extract_audio,
    "compress": compress,
    "thumbnail": thumbnail,
    "waveform": waveform,
    "proxy": proxy,
}


def execute(op: str, params: dict) -> dict | list[dict]:
    """Execute one named op; used by the worker and tests."""
    fn = OPS.get(op)
    if fn is None:
        raise MediaOpError("unsupported", f"unknown op '{op}'")
    result = fn(**params)
    if isinstance(result, list):
        return result
    if is_dataclass(result):
        return asdict(result)
    return result
