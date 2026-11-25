"""Served proof: AI spending on a running VOOL with a paid model pinned.

A real daemon on the OpenRouter-compatible lane, pointed at a synthetic provider (the rig of
tests/test_conversation_intent_served.py), so the pin, the reservation, the settlement and the proof
are this checkout's own code; only the model is substituted.

- the limits door refuses a value that makes no sense and a write from another site, and changes nothing;
- a daily limit too small for one paid answer refuses it before anything is sent, in plain words;
- a paid answer the provider did not price is shown as VOOL's estimate, and the day counts it;
- an answer the provider billed is shown as billed;
- an answer that brings the day near its limit carries the warning, and the next one that would pass
  the limit is refused without reaching the provider;
- the Settings page shows the day against its limit, billed apart from estimated.
"""
from __future__ import annotations

import json
import os as _os
import uuid
from urllib.request import Request, urlopen

import pytest

from tests._served_daemon import _answer_text, _session
from tests.test_conversation_intent_served import _or_completion, _or_scripted, _user_text, served_openrouter


@pytest.fixture(scope="module")
def rig(tmp_path_factory):
    yield from served_openrouter(tmp_path_factory)


def _spend(daemon, session: str = "") -> dict:
    status, got = daemon.call("GET", "/api/spend" + (f"?session_id={session}" if session else ""))
    assert status == 200, got
    return got


def _limits(daemon, limits: dict, *, origin: str = "") -> tuple[int, dict]:
    headers = {"Content-Type": "application/json"}
    if origin:
        headers["Origin"] = origin
    request = Request(f"{daemon.base_url}/api/spend/limits", data=json.dumps({"limits": limits}).encode(), headers=headers, method="POST")
    try:
        with urlopen(request, timeout=60) as response:
            return response.status, json.loads(response.read().decode())
    except Exception as exc:  # urllib raises on 4xx
        body = exc.read().decode() if hasattr(exc, "read") else "{}"
        return int(getattr(exc, "code", 0)), json.loads(body or "{}")


def _turn(daemon, text: str, session: str) -> tuple[str, dict]:
    body = {"messages": [{"role": "user", "content": text}], "stream": True, "session_id": session,
            "model": daemon.LANE_ID, "mode": "auto"}
    request = Request(f"{daemon.base_url}/api/chat", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    answer, commit = "", {}
    with urlopen(request, timeout=300) as response:
        for raw in response:
            try:
                frame = json.loads(raw.decode("utf-8").strip() or "{}")
            except json.JSONDecodeError:
                continue
            if isinstance(frame.get("vool_response_commit"), dict):
                commit = frame["vool_response_commit"]
            answer += str((frame.get("message") or {}).get("content") or "")
    proof: dict = {}
    if commit.get("request_id"):
        status, proof = daemon.call("GET", f"/api/chat/proof?session={session}&request_id={commit['request_id']}")
        assert status == 200, proof
    return str(commit.get("canonical_content") or answer), proof


def _priced(cost: float):
    def reply(body):
        done = _or_scripted(body)
        if _user_text(body).startswith("[spend]"):
            done["usage"]["cost"] = cost
        return done
    return reply


def test_spending_is_enforced_counted_told_apart_and_warned_about(rig):
    daemon, service = rig.daemon, rig.service
    fresh = _spend(daemon)
    assert fresh["today"]["counted_usd"] == 0 and fresh["alerts"] == []

    # Nonsense and a foreign site change nothing.
    status, said = _limits(daemon, {"daily": 50, "monthly": 10})
    assert status == 400 and said["error"] == "day_above_month", said
    status, _ = _limits(daemon, {"daily": 9000}, origin="http://example.invalid")
    assert status == 403
    assert _spend(daemon)["limits"] == fresh["limits"]

    # A day too small for one paid answer: refused before anything is sent.
    assert _limits(daemon, {"daily": 0.01})[0] == 200
    session = _session(f"spend-{uuid.uuid4()}")
    before = len(service.inference_requests())
    answer, _proof = _turn(daemon, f"[spend] name a {uuid.uuid4().hex[:4]} colour", session)
    assert len(service.inference_requests()) == before, "a call that would pass the limit is never sent"
    assert "spend cap" in answer.lower() or "limit" in answer.lower(), answer

    # Room for answers again. The provider prices nothing: the answer and the day say "estimate".
    assert _limits(daemon, {"daily": 4.0})[0] == 200
    service.reply = _or_scripted
    answer, proof = _turn(daemon, f"[plain] which {uuid.uuid4().hex[:4]} fruit is yellow?", session)
    spend = proof["compact"]["spend"]
    assert spend is not None, ("no paid call on this answer's record", answer, proof.get("state"), proof["compact"].get("route"))
    assert spend["estimated_usd"] > 0 and spend["billed_usd"] == 0, spend
    keys = [part.get("key") for part in proof["compact"]["cost"]["parts"]]
    assert "proof.usage.estimated" in keys and "proof.usage.unreported" not in keys, keys
    day = _spend(daemon, session)
    assert day["today"]["estimated_usd"] == pytest.approx(spend["estimated_usd"], abs=1e-6)
    assert day["session"]["counted_usd"] == pytest.approx(day["today"]["counted_usd"], abs=1e-6)

    # The provider bills this one.
    service.reply = _priced(0.012)
    _answer, proof = _turn(daemon, f"[spend] one word for {uuid.uuid4().hex[:4]} happy", session)
    assert proof["compact"]["spend"]["billed_usd"] == pytest.approx(0.012), proof["compact"]["spend"]
    assert _spend(daemon)["today"]["billed_usd"] == pytest.approx(0.012)

    # A limit set so the next billed answer fits but leaves the day near it: the answer warns.
    counted = _spend(daemon)["today"]["counted_usd"]
    held = proof["compact"]["spend"]
    # One answer's reservation on this lane, as the ledger sized it for the last paid answer.
    reserved = [e for e in daemon.events(session) if e.get("event_type") == "paid_call.reserved"]
    ceiling = max(float(e.get("reserved_usd") or e.get("hard_cap_usd") or 0) for e in reserved)
    assert ceiling > 0, reserved[-1:]
    limit = round(counted + ceiling + 0.01, 2)
    assert _limits(daemon, {"daily": limit})[0] == 200
    service.reply = _priced(round(limit * 0.9 - counted, 4))
    _answer, proof = _turn(daemon, f"[spend] a {uuid.uuid4().hex[:4]} synonym for fast", session)
    alerts = proof["compact"]["spend"]["alerts"]
    assert alerts and alerts[0]["window"] == "today" and alerts[0]["state"] in {"near", "reached"}, (alerts, held)

    # The next paid answer would pass the day: not sent.
    before = len(service.inference_requests())
    answer, _proof = _turn(daemon, f"[spend] spell {uuid.uuid4().hex[:4]} backwards", session)
    assert len(service.inference_requests()) == before, answer

    # Settings shows the day against its limit, billed apart from estimated.
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--no-sandbox"], **({"executable_path": _os.environ["VOOL_TEST_CHROMIUM_EXECUTABLE"]} if _os.environ.get("VOOL_TEST_CHROMIUM_EXECUTABLE") else {}))
        try:
            page = browser.new_page()
            page.goto(f"{daemon.base_url}/settings#budgets/spend", wait_until="domcontentloaded")
            page.wait_for_selector("[data-window=today]", timeout=30000)
            today = page.inner_text("[data-window=today]")
            assert "billed by the provider" in today and "estimated by VOOL" in today, today
            assert page.get_attribute("[data-window=today]", "data-state") in {"near", "reached"}
            assert page.input_value("[data-limit=daily]") == f"{limit:.2f}"
        finally:
            browser.close()
    service.reply = _or_scripted
