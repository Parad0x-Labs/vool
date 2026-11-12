"""Sabotage proofs for the studio's load-bearing guards (CLAUDE.md §6b.4).

Each test breaks one guard and requires the *independent* layer to catch the
break, with a failure that names the cause:

* the SHA check (boot layer blinded → the manifest binding layer must still
  catch a lying daemon; and vice versa);
* the redaction guard (engine disabled → the leak scan must still flag the
  canary);
* reduced-motion pinning (option removed → the context-options validator must
  refuse);
* event-justified zoom (unjustified entry → the plan validator must refuse).

A sabotage that survives means the guard is dead or a backstop absorbed it —
that is a defect, reported as one.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

import ops.demo_studio.boot as boot_mod
from ops.demo_studio.boot import verify_served_boot
from ops.demo_studio.evidence import build_evidence_manifest, verify_evidence_manifest
from ops.demo_studio.focus import FocusEntry, validate_focus_plan
from ops.demo_studio.redact import RedactionEngine
from ops.demo_studio.render import ffmpeg_available
from ops.demo_studio.wire import DemoWireClient
from tests.test_demo_studio_boot_gate import SHA_OK, SHA_OTHER, _FakeDaemon, _healthz_payload


def test_sabotage_blinding_the_boot_sha_check_is_still_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Break layer 1: the boot gate's full-sha comparison is blinded to
    # always-true. A daemon reporting a DIFFERENT commit_full must still be
    # refused by the independent cross-consistency check (short commit vs
    # full commit vs expected prefix), which is written against different
    # fields and cannot inherit the blindness.
    monkeypatch.setattr(boot_mod, "_full_sha_matches", lambda reported, expected: True)
    daemon = _FakeDaemon(healthz=_healthz_payload(commit_full=SHA_OTHER))
    try:
        wire = DemoWireClient(f"http://127.0.0.1:{daemon.port}")
        with pytest.raises(boot_mod.StudioBootRefused) as excinfo:
            verify_served_boot(wire, expected_sha=SHA_OK, required_capabilities=())
        assert "commit" in str(excinfo.value).lower()
    finally:
        daemon.close()


def test_sabotage_blinding_the_consistency_check_is_still_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Break layer 2: the short/full cross-consistency check is blinded. A
    # daemon reporting a wrong commit_full must still be refused by the
    # full-sha check.
    monkeypatch.setattr(boot_mod, "_short_consistent", lambda runtime: True)
    daemon = _FakeDaemon(healthz=_healthz_payload(commit_full=SHA_OTHER))
    try:
        wire = DemoWireClient(f"http://127.0.0.1:{daemon.port}")
        with pytest.raises(boot_mod.StudioBootRefused):
            verify_served_boot(wire, expected_sha=SHA_OK, required_capabilities=())
    finally:
        daemon.close()


def test_sabotage_disabling_redaction_is_caught_by_the_leak_scan() -> None:
    # The redaction engine is disabled (sabotage). The evidence layer's
    # mandatory leak scan — an independent pass over every emitted text —
    # must flag the canary that now sails through.
    engine = RedactionEngine(
        workspace_root="/tmp/ws", runtime_home="/Users/someuser/h", scan_secrets=[]
    )
    canary = "CANARY-sk-sabotage0000000000"
    raw_caption = f"ran at /Users/someuser/h/x with {canary}"
    broken = engine.redact_without_guard(raw_caption) if hasattr(engine, "redact_without_guard") else raw_caption
    assert broken == raw_caption or canary in broken or "/Users/" in broken, (
        "sabotage setup: the broken path must actually leak"
    )
    from ops.demo_studio.evidence import leak_scan as evidence_leak_scan

    found = evidence_leak_scan(
        {"captions.srt": broken},
        workspace_root="/tmp/ws",
        runtime_home="/Users/someuser/h",
        scan_secrets=[canary],
    )
    assert found, "the evidence-layer leak scan must catch what redaction missed"
    assert any("captions" in v["artifact"] for v in found)


def test_sabotage_removing_reduced_motion_is_caught() -> None:
    from ops.demo_studio.driver import browser_context_options, validate_browser_context_options

    opts = browser_context_options(record_video_dir="/tmp/v")
    assert opts["reduced_motion"] == "reduce", "precondition: the studio pins reduced motion"
    broken = dict(opts)
    del broken["reduced_motion"]
    with pytest.raises(ValueError) as excinfo:
        validate_browser_context_options(broken)
    assert "reduced_motion" in str(excinfo.value)


def test_sabotage_removing_video_recording_is_caught() -> None:
    from ops.demo_studio.driver import browser_context_options, validate_browser_context_options

    broken = {k: v for k, v in browser_context_options(record_video_dir="/tmp/v").items() if not k.startswith("record")}
    with pytest.raises(ValueError) as excinfo:
        validate_browser_context_options(broken)
    assert "record" in str(excinfo.value).lower(), (
        "a context that records no video must be refused: it cannot produce a demo"
    )


def test_sabotage_unjustified_zoom_is_caught() -> None:
    zoom = FocusEntry(
        start_s=0.0, end_s=1.0, region="task-card", selector=".task-card",
        box={"x": 0, "y": 0, "width": 10, "height": 10},
        justified_by={"seq": 3, "event_type": "tool_executed"},
    )
    # The trace never contained seq 3 — a cinematic cut with no event behind it.
    with pytest.raises(ValueError):
        validate_focus_plan([zoom], event_seqs={1, 2})


def test_sabotage_missing_capability_row_is_caught() -> None:
    # The capability gate compares against the DEDICATED manifest endpoint;
    # blinding it must not let a row-less manifest pass.
    daemon = _FakeDaemon(
        healthz=_healthz_payload(),
        capabilities={"capabilities": [{"name": "local_runtime", "state": "implemented"}]},
    )
    try:
        wire = DemoWireClient(f"http://127.0.0.1:{daemon.port}")
        with pytest.raises(boot_mod.StudioBootRefused):
            verify_served_boot(
                wire, expected_sha=SHA_OK, required_capabilities=("workspace_write_tools",)
            )
    finally:
        daemon.close()
