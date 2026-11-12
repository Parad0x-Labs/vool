"""Real-capture e2e: the studio films the actual served product.

This lane boots the exact-SHA staged app (isolated home, network-contained
stub model lane), drives the REAL chat page in a real chromium with screen
recording and reduced motion, collects the public typed event trace, and
produces the full deliverable set bound to the staged SHA. It is the proof
that the pipeline records the product rather than a stand-in.

It is opt-in (``DEMO_STUDIO_E2E=1``) because it boots a daemon and a browser:
outside that flag it skips with the reason on record, never silently green.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

from ops.demo_studio.runner import StudioConfig, StudioMode, run_journey

REPO = Path(__file__).resolve().parent.parent


def _e2e_enabled() -> bool:
    return os.environ.get("DEMO_STUDIO_E2E") == "1"


def _preconditions() -> str | None:
    if not _e2e_enabled():
        return "DEMO_STUDIO_E2E=1 not set (daemon+browser lane is opt-in)"
    try:
        import playwright
    except ImportError:
        return "playwright not installed"
    if not shutil.which("ffmpeg"):
        return "ffmpeg not available"
    return None


@pytest.mark.skipif(_preconditions() is not None, reason=_preconditions() or "")
def test_real_capture_of_the_local_chat_journey(tmp_path: Path) -> None:
    cfg = StudioConfig(
        repo=REPO,
        out_dir=tmp_path,
        mode=StudioMode.CAPTURE,
        sha="HEAD",
        python_bin=os.environ.get("DEMO_STUDIO_PYTHON", sys.executable),
        live_models=False,
    )
    outcome = run_journey("local_chat", cfg)
    assert outcome.verdict == "pass", f"real capture failed: {outcome.detail}"
    out = outcome.run_dir
    manifest = json.loads((out / "evidence.manifest.json").read_text(encoding="utf-8"))
    # The binding: exact staged commit, honest model lane, real artifacts.
    assert manifest["product"]["commit_full"], "manifest must bind the full commit"
    assert manifest["product"]["dirty"] is False
    assert manifest["boot"]["healthz_verified"] is True
    assert manifest["events"]["count"] > 0, "a real turn emits public typed events"
    assert manifest["events"]["types"].get("task_completed", 0) >= 1
    for artifact in ("demo.mp4", "demo.webm", "captions.srt", "poster.png", "events.jsonl"):
        assert (out / artifact).is_file(), f"deliverable {artifact} missing"
        assert artifact in manifest["artifacts"], f"{artifact} unbound in the manifest"
    # A real browser actually rendered the page: the video is not empty.
    assert manifest["render"]["mp4"]["bytes"] > 10_000, (
        "a real recording of a real chat page is never a few hundred bytes"
    )
    assert manifest["honesty"]["captured"] is True
    assert manifest["honesty"]["model_lane"] == "scripted_stub"
    violations = manifest.get("verification", {}).get("violations")
    assert violations == [], f"capture manifest must verify clean: {violations}"


@pytest.mark.skipif(_preconditions() is not None, reason=_preconditions() or "")
def test_real_failed_journey_aborts_visibly(tmp_path: Path) -> None:
    # A capture whose expectation cannot be met (the local-chat journey asked
    # to expect a live-web retrieval that the contained lane will never
    # produce) must abort loudly with an abort report and NO success
    # manifest — against the real product, not a fixture.
    from ops.demo_studio.journeys import JOURNEYS, StepSpec
    from ops.demo_studio.runner import JourneyAborted

    spec = JOURNEYS["local_chat"]
    doomed = type(spec)(
        id=spec.id + "_doomed",
        title=spec.title,
        summary=spec.summary,
        requires=spec.requires,
        capabilities_required=spec.capabilities_required,
        steps=spec.steps,
        expected_events=("web_retrieval_completed",),  # never happens offline
        stub_rules=spec.stub_rules,
    )
    from ops.demo_studio import journeys as journeys_mod

    journeys_mod.JOURNEYS[doomed.id] = doomed
    try:
        cfg = StudioConfig(
            repo=REPO,
            out_dir=tmp_path,
            mode=StudioMode.CAPTURE,
            sha="HEAD",
            python_bin=os.environ.get("DEMO_STUDIO_PYTHON", sys.executable),
        )
        with pytest.raises(JourneyAborted):
            run_journey(doomed.id, cfg)
        reports = [r for r in tmp_path.rglob("abort-report.json") if doomed.id in str(r)]
        assert reports, "the real abort must leave its report"
        report = json.loads(reports[0].read_text(encoding="utf-8"))
        assert report["verdict"] == "aborted"
        assert report["journey"] == doomed.id
        # Only THIS journey's runs are under scrutiny: a sibling passing run
        # in the same out_dir may legitimately own a manifest.
        doomed_dirs = [d for d in tmp_path.rglob("*") if d.is_dir() and doomed.id in d.name]
        assert not [m for d in doomed_dirs for m in d.glob("evidence.manifest.json")], (
            "an aborted run must not ship a success manifest"
        )
    finally:
        journeys_mod.JOURNEYS.pop(doomed.id, None)
