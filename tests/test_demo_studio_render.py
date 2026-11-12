"""Rendering: real recording in, MP4/WebM + subtitles + poster + binding out.

The renderer consumes the REAL Playwright recording (a fixture webm produced
by ffmpeg stands in for the browser's codec output here — same container,
same pipeline) and emits the deliverable set: MP4 (H.264 with embedded
mov_text subtitles), the source-format WebM, a sidecar SRT built only from
redacted public-event captions, a poster frame, and artifact hashes bound
into the evidence manifest. A tampered artifact must fail manifest
verification — that is the binding the "no fake video" contract rests on.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from ops.demo_studio.evidence import build_evidence_manifest, verify_evidence_manifest
from ops.demo_studio.render import FFMPEG_TIMEOUT_S, ffmpeg_available, render_deliverables


def _make_fixture_video(path: Path, *, seconds: float = 2.0) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=640x360:rate=10",
            "-c:v", "libvpx", "-q:v", "6",
            str(path),
        ],
        check=True,
        timeout=FFMPEG_TIMEOUT_S,
    )


@pytest.fixture()
def fixture_video(tmp_path: Path) -> Path:
    if not ffmpeg_available():
        pytest.skip("ffmpeg not available for the render lane")
    out = tmp_path / "video-source.webm"
    _make_fixture_video(out)
    return out


_CAPTIONS = [
    {"start_s": 0.2, "end_s": 1.4, "text": "VOOL received the turn."},
    {"start_s": 1.5, "end_s": 2.8, "text": "Executed workspace.list_files."},
]


def test_render_produces_the_full_deliverable_set(fixture_video: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    report = render_deliverables(
        video_source=fixture_video,
        captions=_CAPTIONS,
        focus_plan=[],
        out_dir=out,
        poster_at_s=0.5,
    )
    for name in ("demo.mp4", "demo.webm", "captions.srt", "poster.png"):
        assert (out / name).is_file(), f"deliverable {name} missing"
    assert report["mp4"]["bytes"] > 0
    assert report["webm"]["bytes"] > 0
    assert report["poster"]["bytes"] > 0


def test_mp4_is_h264_with_embedded_subtitles(fixture_video: Path, tmp_path: Path) -> None:
    if not shutil.which("ffprobe"):
        pytest.skip("ffprobe not available")
    out = tmp_path / "out"
    render_deliverables(video_source=fixture_video, captions=_CAPTIONS, focus_plan=[], out_dir=out, poster_at_s=0.5)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,codec_type", "-of", "json", str(out / "demo.mp4")],
        capture_output=True, text=True, check=True, timeout=60,
    )
    streams = json.loads(probe.stdout)["streams"]
    codecs = {s["codec_type"]: s["codec_name"] for s in streams}
    assert codecs.get("video") == "h264", f"mp4 must be H.264, got {codecs}"
    assert codecs.get("subtitle") == "mov_text", (
        "the MP4 must carry the captions as mov_text so the demo is readable everywhere"
    )


def test_srt_is_built_from_the_redacted_captions(fixture_video: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    render_deliverables(video_source=fixture_video, captions=_CAPTIONS, focus_plan=[], out_dir=out, poster_at_s=0.5)
    srt = (out / "captions.srt").read_text(encoding="utf-8")
    assert "VOOL received the turn." in srt
    assert "Executed workspace.list_files." in srt
    assert "-->" in srt and srt.lstrip().startswith("1\n"), "SRT must be a numbered timecoded track"


def test_manifest_binds_artifact_hashes_and_detects_tampering(
    fixture_video: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    report = render_deliverables(video_source=fixture_video, captions=_CAPTIONS, focus_plan=[], out_dir=out, poster_at_s=0.5)
    manifest = build_evidence_manifest(
        mode="capture",
        journey={"id": "local_chat", "title": "Ordinary local chat", "spec_hash": "x" * 64, "steps": []},
        product={"commit_full": "a" * 40, "commit": "a" * 12, "app_version": "1.0.0", "dirty": False},
        render_report=report,
        captions=_CAPTIONS,
        focus_plan=[],
        timeline={"t0_epoch": 1000.0, "t1_epoch": 1003.0},
        honesty={"model_lane": "scripted_stub"},
        out_dir=out,
    )
    violations = verify_evidence_manifest(manifest, out)
    assert violations == [], f"a fresh capture must verify clean: {violations}"
    # Tamper: one flipped byte in an artifact must break the binding.
    poster = out / "poster.png"
    raw = bytearray(poster.read_bytes())
    raw[-1] ^= 0xFF
    poster.write_bytes(bytes(raw))
    violations = verify_evidence_manifest(manifest, out)
    assert any(v["rule"] == "artifact_hash" for v in violations), (
        "manifest verification must catch a tampered artifact"
    )


def test_manifest_sha_mismatch_is_a_violation(fixture_video: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    report = render_deliverables(video_source=fixture_video, captions=_CAPTIONS, focus_plan=[], out_dir=out, poster_at_s=0.5)
    manifest = build_evidence_manifest(
        mode="capture",
        journey={"id": "local_chat", "title": "t", "spec_hash": "x" * 64, "steps": []},
        product={"commit_full": "a" * 40, "commit": "a" * 12, "app_version": "1.0.0", "dirty": False},
        render_report=report,
        captions=_CAPTIONS,
        focus_plan=[],
        timeline={"t0_epoch": 1000.0, "t1_epoch": 1003.0},
        honesty={"model_lane": "scripted_stub"},
        out_dir=out,
    )
    manifest["product"]["commit_full"] = "b" * 40
    violations = verify_evidence_manifest(manifest, out, expected_sha="a" * 40)
    assert any(v["rule"] == "sha_binding" for v in violations), (
        "the manifest layer must independently verify the exact-SHA binding"
    )


def test_zoom_plan_is_applied_and_published_in_the_render_report(
    fixture_video: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    zoom = [
        {
            "start_s": 0.5, "end_s": 1.5, "region": "task-card", "selector": ".task-card",
            "box": {"x": 20, "y": 40, "width": 360, "height": 120},
            "justified_by": {"seq": 3, "event_type": "tool_executed"},
        }
    ]
    report = render_deliverables(video_source=fixture_video, captions=_CAPTIONS, focus_plan=zoom, out_dir=out, poster_at_s=0.2)
    assert report["zoom_applied"] == 1, "the one justified zoom must be applied"
    assert (out / "demo.mp4").is_file()
