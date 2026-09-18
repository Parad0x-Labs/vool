"""SERVED proof for the provider-answer recovery: the budget fix reaches the wire, and one UsePod
price review holds the limits dispatch actually enforces.

Two real daemons (``apps.vool_api_server``, each in its own process, home and port), one per
part, both against SYNTHETIC loopback providers:

* **Part A — the OpenRouter-compatible lane** (`openrouter-byok`). The daemon's cached catalog
  row for the synthetic model declares the ``reasoning`` parameter (as OpenRouter's live catalog
  does for the models in the 2026-09-16 capture, whose names match no thinking marker). The
  counterexample the root repair answers: the prompt table's 240–520-token chat ceiling went on
  the wire un-reserved, the reasoning model spent it thinking, and the turn died as
  ``EmptyProviderResponseError`` with no answer. Proof here: the request that actually reaches
  the synthetic provider carries the lane-resolved ceiling (paid target + reasoning reserve), and
  the exact A–D evaluation publishes its scripted answer through the normal served ingress.
* **Part B — the UsePod lane.** The journey the owner asked for, against the daemon's REAL route
  authority: pin → approve the route at the observed price → a message answers under the budget →
  the marketplace price RISES above the saved maximum → the next message is refused BEFORE
  sending with the violating axes named and an identity receipt that says NO RESPONSE → the
  reviewed two-axis maxima are saved through ``/api/cloud/usepod/approve-route`` (exact decimals)
  → the same message sends and answers under the raised bound. A one-axis review is refused;
  nothing about the refusal implies the refused operation was ever charged.

Monetary note: the UsePod part runs on the labelled monetary TEST DOUBLE of ``tests/usepod``
(``DOUBLE_LABEL``); no wallet exists, nothing is paid, and the OpenRouter lane's key is a
synthetic never-a-real-credential. No live provider is contacted.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

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
from tests.usepod.strict_usepod_service import Listing, StrictUsePodService, default_reply
from tests.usepod.test_usepod_served_flow import (
    CENTRAL,
    INFERENCE_PATHS,
    MARKET,
    MARKET_ID,
    MODEL,
    UsePodServedDaemon,
    _completed_receipts,
    _session,
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


# --- Part B: the UsePod price review journey -----------------------------------------------------


RAISED = (750_000, 1_800_000)
def _expire_price_cache(daemon) -> None:
    """Drop the daemon's cached marketplace snapshot so the next dispatch re-fetches live.

    The cache TTL is 120s; a test that raises the price must not wait it out. Deleting the cache
    file is the same state the TTL passing leaves: the next `current_snapshot(allow_network=True)`
    fetches from the (synthetic) feed and sees the new price.
    """
    (daemon.home / "data" / "usepod" / "marketplace_models.json").unlink(missing_ok=True)



@pytest.fixture(scope="module")
def price_review_journey(tmp_path_factory):
    token = str(uuid.uuid4())
    service = StrictUsePodService(
        tokens={token: 80_000_000},
        models={MODEL: [Listing("marketplace", MARKET_ID, *MARKET), Listing("centralized", *CENTRAL)]},
    ).start()
    daemon = UsePodServedDaemon(tmp_path_factory.mktemp("recovery-usepod") / "home")
    try:
        daemon.start()
        status, saved = daemon.call("POST", "/api/settings/credentials", {"provider": "usepod", "value": f"{service.origin}/proxy/{token}/v1", "base_url": service.origin})
        assert status == 200, saved
        status, _probe = daemon.call("POST", "/api/cloud/test", {"provider": "usepod"})
        assert status == 200
        status, _refreshed = daemon.call("POST", "/api/cloud/usepod/refresh", {})
        assert status == 200
        status, policy = daemon.call("POST", "/api/cloud/usepod/route-policy", {"mode": "marketplace-only"})
        assert status == 200, policy
        status, pinned = daemon.call("POST", "/api/cloud/model", {"model": MODEL, "provider": "usepod", "confirm_paid": True})
        assert status == 200 and pinned.get("ok") is True, pinned
        yield SimpleNamespace(service=service, daemon=daemon, token=token)
    finally:
        daemon.stop()
        service.stop()


def test_select_review_confirm_refuse_and_correct_through_the_real_route_authority(price_review_journey) -> None:
    daemon, service = price_review_journey.daemon, price_review_journey.service
    service.reply = lambda body, protocol: default_reply(body, protocol)

    # 1. Approve the route at the observed price (the owner's one explicit confirmation today).
    status, approved = daemon.call("POST", "/api/cloud/usepod/approve-route", {"model_id": MODEL})
    assert status == 200, approved
    assert approved["approved_route"]["max_input_microunits_per_million"] == MARKET[0]
    assert approved["approved_route"]["input_basis"] == "discovered_marketplace_price"

    # 2. A message answers under the existing budget.
    session_one = _session(f"one-{uuid.uuid4()}")
    start = len(service.requests_to(INFERENCE_PATHS["openai"]))
    status, answer = daemon.chat("Summarize the tradeoff between strong consistency and availability for a small ops ledger.", session_id=session_one)
    assert status == 200, answer
    assert len(service.requests_to(INFERENCE_PATHS["openai"])) > start, "nothing was sent for the in-budget turn"
    assert _completed_receipts(daemon.events(session_one)), "no completed UsePod receipt for the in-budget turn"

    # 3. The marketplace price RISES above the saved maximum; the next message is refused
    #    BEFORE sending, names the axes with observed and saved values, and never claims a
    #    provider response existed.
    service.models[MODEL] = [Listing("marketplace", MARKET_ID, *RAISED), Listing("centralized", *CENTRAL)]
    _expire_price_cache(daemon)
    refused_session = _session(f"refused-{uuid.uuid4()}")
    refused_start = len(service.requests_to(INFERENCE_PATHS["openai"]))
    status, refused = daemon.chat("Summarize the tradeoff between strong consistency and availability for a small ops ledger.", session_id=refused_session)
    events = daemon.events(refused_session)
    _keep("recovery_usepod_refusal.json", {"status": status, "response": refused, "events": events})
    assert len(service.requests_to(INFERENCE_PATHS["openai"])) == refused_start, "the refused turn must not reach the provider"
    assert "usepod_dispatch_refused:route_price_above_approved_bound" in _rejections(events)
    reply_text = _answer_text(refused)
    assert "input price 0.7 USDC per 1M is above your saved maximum 0.51" in reply_text, reply_text
    assert "output price 1.8 USDC per 1M is above your saved maximum 1.53" in reply_text, reply_text
    assert "nothing was sent and nothing was charged" in reply_text
    # The identity receipt for a turn with NO response says so; it does not report a claim.
    identity = [event for event in events if event.get("event_type") == "model_verification_receipt"]
    assert identity and identity[-1]["verification_receipt"]["verification"]["status"] == "NO_RESPONSE"
    # The failure carries the empty-reply-free facts of a pre-send refusal, not an inference failure.
    failed = [event for event in events if event.get("event_type") == "model.call_failed"]
    assert failed and "not_sent" in json.dumps(failed[-1])

    # 4. The corrected review: both axes, exact decimals, through the real route authority.
    status, one_axis = daemon.call("POST", "/api/cloud/usepod/approve-route", {"model_id": MODEL, "max_input_usdc_per_million": "0.8"})
    assert status == 400 and one_axis.get("code") == "owner_review_requires_both_axes", one_axis
    status, reviewed = daemon.call(
        "POST", "/api/cloud/usepod/approve-route",
        {"model_id": MODEL, "max_input_usdc_per_million": "0.8", "max_output_usdc_per_million": "1.900001"},
    )
    assert status == 200, reviewed
    bound = reviewed["approved_route"]
    # Exact decimal conversion: 0.8 USDC = 800_000 microunits; 1.900001 = 1_900_001 exactly.
    assert bound["max_input_microunits_per_million"] == 800_000
    assert bound["max_output_microunits_per_million"] == 1_900_001
    assert bound["input_basis"] == "explicit_owner_review" and bound["output_basis"] == "explicit_owner_review"
    # The saved maxima are what the daemon's own route state now reports.
    status, view = daemon.call("GET", "/api/cloud/usepod/discovery")
    assert view["approved_routes"][MODEL]["max_input_microunits_per_million"] == 800_000

    # 5. The SAME message now sends and answers under the raised bound.
    session_two = _session(f"two-{uuid.uuid4()}")
    send_start = len(service.requests_to(INFERENCE_PATHS["openai"]))
    status, answer_two = daemon.chat("Summarize the tradeoff between strong consistency and availability for a small ops ledger.", session_id=session_two)
    assert status == 200, answer_two
    arrived = service.requests_to(INFERENCE_PATHS["openai"])[send_start:]
    assert arrived, "the corrected review must let the message send"
    receipts = _completed_receipts(daemon.events(session_two))
    assert receipts, "no completed receipt after the corrected review"
    ceiling = receipts[-1].get("price_ceiling_microunits_per_million") or {}
    assert ceiling.get("input") == 800_000 and ceiling.get("output") == 1_900_001, ceiling
    _keep("recovery_usepod_journey.json", {"refusal_axes_seen": True, "reviewed_bound": bound, "resend_receipts": receipts[-1:]})


def test_an_input_only_rise_is_named_on_its_own(price_review_journey) -> None:
    """Independent axes, the other direction: an input-only rise above the reviewed maximum
    refuses naming input only. The centralized listing rises with the marketplace one so the
    synthetic feed's documented cap (a marketplace price never above the centralized price)
    does not mask the rise."""
    daemon, service = price_review_journey.daemon, price_review_journey.service
    service.models[MODEL] = [Listing("marketplace", MARKET_ID, 900_000, 1_600_000), Listing("centralized", "together", 950_000, 2_100_000)]
    _expire_price_cache(daemon)
    session = _session(f"in-{uuid.uuid4()}")
    refused_start = len(service.requests_to(INFERENCE_PATHS["openai"]))
    status, refused = daemon.chat("Give one sentence on idempotent command application.", session_id=session)
    events = daemon.events(session)
    _keep("recovery_usepod_input_axis.json", {"status": status, "response": refused, "events": events})
    assert len(service.requests_to(INFERENCE_PATHS["openai"])) == refused_start
    assert "usepod_dispatch_refused:route_price_above_approved_bound" in _rejections(events)
    reply_text = _answer_text(refused)
    assert "input price 0.9 USDC per 1M is above your saved maximum 0.8" in reply_text, reply_text
    assert "output price" not in reply_text, "the output axis is within its saved maximum and must not be named"


def test_an_output_only_rise_is_named_on_its_own(price_review_journey) -> None:
    """Independent axes: an output-only rise above the reviewed maximum refuses naming output."""
    daemon, service = price_review_journey.daemon, price_review_journey.service
    service.models[MODEL] = [Listing("marketplace", MARKET_ID, RAISED[0], 2_400_000), Listing("centralized", *CENTRAL)]
    _expire_price_cache(daemon)
    session = _session(f"out-{uuid.uuid4()}")
    refused_start = len(service.requests_to(INFERENCE_PATHS["openai"]))
    status, refused = daemon.chat("Give one sentence on idempotent command application.", session_id=session)
    events = daemon.events(session)
    _keep("recovery_usepod_output_axis.json", {"status": status, "response": refused, "events": events})
    assert len(service.requests_to(INFERENCE_PATHS["openai"])) == refused_start
    assert "usepod_dispatch_refused:route_price_above_approved_bound" in _rejections(events)
    reply_text = _answer_text(refused)
    assert "output price 2.1 USDC per 1M is above your saved maximum 1.900001" in reply_text, reply_text
    assert "input price" not in reply_text, "the input axis is within its saved maximum and must not be named"
