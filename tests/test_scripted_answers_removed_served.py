"""The scripted web0/.null topic answers no longer preempt real questions -- served proof.

Owner instruction, 2026-09-16: the deterministic quick-answer family ("web0, .null, vool and
so on") had overridden real requests once too often. The measured trigger: a provider-health
checker asking to "capture the returned model CLAIM" and "calculate the request COST" was
answered, wholesale, with the `.null` registration fee boilerplate -- "claim" sat on the
registration-verb list, "cost" on the fee-marker list, and `claim + cost` was enough for the
fee-followup matcher to replace the entire reply. The same disease, different lane: "I am
running low on space on this machine, can you check the largest folders?" matched the
runtime-truth serve-verb + locality pattern ("running ... on this machine") and got the canned
"Last model call: ..." status text.

Repairs: the served ingress no longer consults the web0/.null topic answerers at all (the
exact-command brake/update controls stay), and the runtime-truth matcher learned the depletion
idioms ("running low/out/short") so a disk-space question is not a lane question.

These cases drive the real HTTP door with a certified scripted provider and assert the
contract that matters: the request reaches the model lane and the published answer is the
model's own, not a scripted topic answer.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

import pytest

from tests._blackbox_served_rig import SEED_MANIFEST, ServedDaemon, run_in_home

try:
    from tests.test_answer_integrity_consecutive_turns import _RecordingProvider

    _SERVED_AVAILABLE = True
except Exception:  # pragma: no cover
    _SERVED_AVAILABLE = False

REPO_ROOT = Path(__file__).resolve().parents[1]

CHECKER_REQUEST = (
    "Build me a small Python provider-health checker for OpenRouter. Research the current "
    "OpenRouter API and available documentation first. The program should accept a model ID such "
    "as: z-ai/glm-5.3-flash Then: - verify that the model currently exists - retrieve whatever "
    "current pricing/context metadata OpenRouter exposes - make a very small chat completion "
    "request - measure total request latency - capture the returned model identity/provider "
    "metadata when available - calculate the approximate request cost from returned usage - "
    "clearly distinguish: requested model, returned model claim, provider/router information, "
    "latency, prompt tokens, completion tokens, total tokens, estimated or reported cost. "
    "Return the result as both: 1. a human-readable terminal table 2. JSON. Use the current "
    "official API format rather than relying on remembered endpoint shapes. Handle failures "
    "cleanly and never print the API key."
)

FOLDERS_REQUEST = (
    "cool, anyways, I am running low on space on this machine, can you check what are the "
    "largest folders?"
)


def _answer(frame: dict[str, Any]) -> str:
    message = frame.get("message")
    text = str((message or {}).get("content") or "") if isinstance(message, dict) else ""
    if not text.strip():
        commit = frame.get("vool_response_commit")
        if isinstance(commit, dict):
            text = str(commit.get("canonical_content") or "")
    return text


@pytest.mark.served
@pytest.mark.skipif(not _SERVED_AVAILABLE, reason="served rig unavailable")
def test_the_incidents_requests_reach_the_model_lane_not_a_scripted_answer(tmp_path) -> None:
    home = tmp_path / "home"
    with _RecordingProvider() as provider:
        run_in_home(
            home,
            SEED_MANIFEST.format(
                root=REPO_ROOT, base_url=provider.base_url, registered=["stub-chat:2b"]
            ),
        )
        with ServedDaemon(home) as daemon:
            request = Request(
                f"{daemon.base_url}/api/model-tool-certification/run",
                data=json.dumps(
                    {
                        "provider_name": "ollama-local",
                        "model_name": "stub-chat:2b",
                        "timeout_seconds": 60,
                    }
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request, timeout=180) as response:
                assert json.loads(response.read().decode("utf-8")).get("state") == "verified"

            session = "scripted-removed"

            # The provider-health checker: the incident's exact shape. The .null registration
            # boilerplate must not appear, and the model lane must have seen the request.
            provider.calls.clear()
            frame = daemon.chat_stream(CHECKER_REQUEST, session_id=session, model="stub-chat:2b")
            published = _answer(frame)
            assert ".null" not in published and "Windows Hello" not in published, published[:200]
            assert "isn't free" not in published and "null-auction" not in published
            lane_saw_it = any(
                any(
                    str(m.get("role")) == "user"
                    and "provider-health checker" in str(m.get("content") or "")
                    for m in (call.get("messages") or [])
                )
                for call in provider.calls
            )
            assert lane_saw_it, "the request never reached the model lane"

            # The disk-space question: not the canned runtime-status answer.
            provider.calls.clear()
            frame = daemon.chat_stream(FOLDERS_REQUEST, session_id=session, model="stub-chat:2b")
            published = _answer(frame)
            assert not published.startswith("Last model call"), published[:160]
            assert "Selection: cloud model" not in published
