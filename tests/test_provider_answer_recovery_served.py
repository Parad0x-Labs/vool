"""SERVED proof for the provider-answer recovery: the budget fix reaches the wire.

A real daemon (``apps.vool_api_server`` in its own process, home and port) on the
OpenRouter-compatible lane (`openrouter-byok`), against a SYNTHETIC loopback provider. The daemon's
cached catalog row for the synthetic model declares the ``reasoning`` parameter (as OpenRouter's
live catalog does for the models in the 2026-09-16 capture, whose names match no thinking marker).
The counterexample the root repair answers: the prompt table's 240–520-token chat ceiling went on
the wire un-reserved, the reasoning model spent it thinking, and the turn died as
``EmptyProviderResponseError`` with no answer. Proof here: the request that actually reaches the
synthetic provider carries the lane-resolved ceiling (paid target + reasoning reserve), and the
exact A–D evaluation publishes its scripted answer through the normal served ingress.

The lane's key is a synthetic never-a-real-credential. No live provider is contacted.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests._served_daemon import _session
from tests.test_conversation_intent_served import (
    DESIGN_REVIEW,
    EVALUATION,
    EVALUATION_REPLY,
    OpenAICompatibleService,
    OpenRouterCompatibleDaemon,
    _answer_text,
    _keep,
    _rejections,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests" / "fixtures"
OR_MODEL = "z-ai/glm-5.3-flash-synth"
OR_LANE_ID = f"openrouter-byok:{OR_MODEL}"
PAID_TARGET_PLUS_RESERVE = 760 + 2048


# --- Part A: the byok lane's wire budget --------------------------------------------------------


class ReasoningCatalogDaemon(OpenRouterCompatibleDaemon):
    """The byok daemon whose catalog row declares the ``reasoning`` parameter.

    The live capture's models (deepseek-v4-flash-0731, glm-5.3-flash) match no thinking-marker
    and the byok manifest carried no declaration, which is exactly the seam this repairs: the
    catalog's own declaration must be what arms the reasoning reserve.
    """

    def env(self) -> dict[str, str]:
        env = super().env()
        # The parent class pins the ORIGINAL suite's model id into OPENROUTER_MODEL; this lane
        # runs under its own synthetic reasoning model id instead.
        env["OPENROUTER_MODEL"] = OR_MODEL
        return env

    def chat(self, text: str, *, session_id: str, stream: bool = False) -> tuple[int, object]:
        payload = {"messages": [{"role": "user", "content": text}], "stream": stream, "session_id": session_id, "model": OR_LANE_ID, "mode": "auto"}
        return self.call("POST", "/api/chat", payload, timeout=300.0)

    def seed_catalog(self) -> None:
        cache = self.home / "data" / "openrouter_models_cache.json"
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(
            json.dumps(
                {
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    "payload": {
                        "data": [
                            {
                                "id": OR_MODEL,
                                "name": "Synthetic reasoning lane",
                                "context_length": 131072,
                                "pricing": {"prompt": "0.0000001", "completion": "0.0000004", "request": "0"},
                                "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
                                "top_provider": {"max_completion_tokens": 16384},
                                "supported_parameters": ["max_tokens", "temperature", "tools", "reasoning"],
                            }
                        ]
                    },
                }
            ),
            encoding="utf-8",
        )


@pytest.fixture(scope="module")
def reasoning_lane(tmp_path_factory):
    service = OpenAICompatibleService().start()
    daemon = ReasoningCatalogDaemon(tmp_path_factory.mktemp("recovery-openrouter") / "home", service)
    try:
        daemon.start()
        status, pinned = daemon.call("POST", "/api/cloud/model", {"model": OR_MODEL, "provider": "openrouter", "confirm_paid": True})
        assert status == 200 and pinned.get("ok") is True, pinned
        status, certified = daemon.call(
            "POST", "/api/model-tool-certification/run", {"provider_name": "openrouter-byok", "model_name": OR_MODEL}, timeout=420.0
        )
        assert status == 200, certified
        yield SimpleNamespace(service=service, daemon=daemon, pinned=pinned)
    finally:
        daemon.stop()
        service.stop()


def test_the_evaluation_reaches_the_reasoning_lane_with_the_resolved_ceiling_and_publishes(reasoning_lane) -> None:
    daemon, service = reasoning_lane.daemon, reasoning_lane.service

    def reply(body):
        text = json.dumps(body, ensure_ascii=False)
        if "You are being evaluated" in text:
            return {"id": "resp-eval", "model": OR_MODEL, "choices": [{"message": {"content": EVALUATION_REPLY}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 1500, "completion_tokens": 420, "total_tokens": 1920}}
        return {"id": "resp-x", "model": OR_MODEL, "choices": [{"message": {"content": "plain synthetic answer"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 100, "completion_tokens": 12}}

    service.reply = reply
    session_id = f"eval-{uuid.uuid4()}"
    start = len(service.inference_requests())
    status, answer = daemon.chat(EVALUATION, session_id=session_id)
    arrived = service.inference_requests()[start:]
    _keep("recovery_openrouter_evaluation.json", {"status": status, "answer": answer, "arrived": len(arrived), "first_max_tokens": (arrived[0].get("max_tokens") if arrived else None)})
    assert status == 200, answer
    assert arrived, "no request reached the synthetic provider"
    # THE COUNTEREXAMPLE, ANSWERED: the wire ceiling is the lane-resolved budget (paid-cloud
    # target 760 + 2048 reasoning reserve), not the prompt table's 240–520 un-reserved number.
    assert arrived[0]["max_tokens"] == PAID_TARGET_PLUS_RESERVE, arrived[0].get("max_tokens")
    # The whole evaluation is ONE request and its answer publishes unchanged.
    assert len([body for body in arrived if "You are being evaluated" in json.dumps(body, ensure_ascii=False)]) == 1
    assert _answer_text(answer) == EVALUATION_REPLY


def test_an_ordinary_design_review_publishes_on_the_same_lane(reasoning_lane) -> None:
    daemon, service = reasoning_lane.daemon, reasoning_lane.service
    review_reply = "1. Address replacement by a plugin is the top risk; enforce ownership and PIN checks in storage, not the UI."
    service.reply = lambda body: {"id": "resp-review", "model": OR_MODEL, "choices": [{"message": {"content": review_reply}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 900, "completion_tokens": 60}}
    session_id = f"review-{uuid.uuid4()}"
    start = len(service.inference_requests())
    status, answer = daemon.chat(DESIGN_REVIEW, session_id=session_id)
    arrived = service.inference_requests()[start:]
    _keep("recovery_openrouter_review.json", {"status": status, "answer": answer, "arrived": len(arrived)})
    assert status == 200 and arrived
    assert arrived[0]["max_tokens"] == PAID_TARGET_PLUS_RESERVE
    assert _answer_text(answer) == review_reply
