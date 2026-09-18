"""A model that returns only its reasoning, or nothing, yields a typed notice, never the reasoning
text and never a fabricated answer.

Scratch daemon; the scripted model answers the comparison with a reasoning-only block (`<think>`)
on the first case and an empty string on the second.
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from urllib.request import Request, urlopen

import pytest

import tests._reader_served_rig as rig

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "validation-logs" / "comparison-truth-overnight-20260906" / "fixtures" / "comparison"
SHIM = ROOT / "tests" / "fixture_transport"
REASONING_ONLY = "<think>The user wants a comparison. I should list production periods first, then sales. Let me plan the sections.</think>"


def _turn(daemon, text: str) -> str:
    session = rig.canonical_session("degenerate-" + uuid.uuid4().hex[:8])
    body = {"messages": [{"role": "user", "content": text}], "stream": True, "stream_task_events": True,
            "session_id": session, "turn_id": uuid.uuid4().hex, "model": daemon.model, "model_selection": "sticky", "mode": "ask"}
    req = Request(f"{daemon.base_url}/api/chat", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    parts = []
    with urlopen(req, timeout=900) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            try:
                obj = json.loads(line) if line else {}
            except Exception:
                continue
            msg = obj.get("message")
            if isinstance(msg, dict) and msg.get("content"):
                parts.append(str(msg["content"]))
    return "".join(parts)


@pytest.mark.parametrize("reply", [REASONING_ONLY, ""], ids=["reasoning_only", "empty"])
def test_degenerate_output_becomes_a_typed_notice(tmp_path, reply):
    env_extra = {
        "PYTHONPATH": os.pathsep.join([str(SHIM), str(rig.REPO_ROOT), str(rig.READER_DEPS)]),
        "VOOL_FIXTURE_TRANSPORT_MANIFEST": str(FIX / "manifest_complete.json"),
        "ALLOW_BROWSER_FALLBACK": "0", "WEB_SEARCH_PROVIDER_ORDER": "google_html",
    }
    with rig.CapturingProvider(default=reply) as provider:
        daemon = rig.ServedDaemon(tmp_path / "home", provider=provider, env_extra=env_extra)
        daemon.register_provider()
        daemon.start()
        assert daemon.certify().get("state") == "verified"
        try:
            answer = _turn(daemon, "Compare the VW Passat and the VW Golf in detail: production periods and sales.")
        finally:
            daemon.stop()
    assert "<think>" not in answer and "Let me plan" not in answer, answer[:300]
    assert answer.strip(), "an empty completion must still yield a typed notice"
    # The reply is the runtime's typed notice, never a fabricated comparison: a source report may
    # quote what the sources said, but no sentence of the model's own is published.
    lowered = answer.lower()
    assert lowered.startswith("i can't") or lowered.startswith("i couldn't") or "no usable answer" in lowered, answer[:300]
    assert "golf production began" not in lowered and "passat production in" not in lowered, answer[:300]
