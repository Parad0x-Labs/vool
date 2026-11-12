"""With Web lookups off, every kind of web ask is told so plainly, through the real /api/chat door.

0.7.8 sweep block 2c (Web lookups off): weather and ETH prices were still fetched; "look up the latest
news", "how cold is it in oslo right now" and "what does the bbc homepage say right now" were answered
"That is not mentioned in the records I have from our conversations"; "open https://example.org and
tell me what the page says" said "I did not open it" and "go to example.org and summarize it" said
"I can't publish an answer"; "whats on example.org" showed "disabled by runtime web policy..".

The asks below are new wordings of those cases. The served daemon runs with the shipped default
(Web lookups off) and a scripted model; every reply must name the switch, and no ask may reach the
public web.
"""

from __future__ import annotations

import time
import uuid

import pytest

import tests._reader_served_rig as rig
from tests.test_memory_forget_served_p0 import _reply_text

pytestmark = pytest.mark.served

ASKS = (
    "hows the weather in lisbon at the moment",            # weather
    "what's a dogecoin worth right now",                   # price
    "any headlines about the world cup today?",            # news
    "how warm is it in tallinn this minute",               # weather, other wording
    "can you read https://example.com and tell me what it says",    # URL read
    "go to example.net and give me the gist",              # bare-domain digest
    "what does the guardian homepage show right now",      # a live page without a URL
    "look up online who won the 2026 eurovision",          # explicit lookup
    "what time does the sun go down in bergen today",      # sunset in its verb form (red team R1-16)
)

SWITCH_WORDS = ("web lookups are off", "turn them on in settings")


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    with rig.CapturingProvider(default="It is sunny and 21 degrees, and the price is 0.42 USD.") as provider:
        daemon = rig.ServedDaemon(tmp_path_factory.mktemp("web-off") / "home", provider=provider)
        daemon.start()
        assert daemon.certify(timeout=180).get("state") == "verified"
        try:
            yield daemon, provider
        finally:
            daemon.stop()


def _ask(daemon, asked: str) -> tuple[str, float]:
    started = time.monotonic()
    payload = daemon.chat(asked, session_id=rig.canonical_session("weboff-" + uuid.uuid4().hex[:8]))
    _LAST_PAYLOAD[asked] = payload
    return _reply_text(payload), time.monotonic() - started


_LAST_PAYLOAD: dict = {}


@pytest.mark.parametrize("asked", ASKS)
def test_a_web_ask_with_web_lookups_off_names_the_switch(served, asked):
    daemon, _provider = served
    reply, seconds = _ask(daemon, asked)
    low = reply.lower()
    assert "web lookups are off" in low or "web lookups are switched off" in low, reply
    assert "settings" in low, reply
    # Nothing the scripted model would have invented, nothing from the records lane, no engineering words.
    for wrong in ("21 degrees", "0.42", "records i have from our conversations", "runtime", "policy", "`", ".."):
        assert wrong not in low, (wrong, reply)
    assert seconds < 60, (seconds, reply)


def test_a_web_off_refusal_is_not_sealed_as_a_finished_answer(served):
    import json

    daemon, _provider = served
    _ask(daemon, "any headlines about the world cup right now?")
    sealed = json.dumps(_LAST_PAYLOAD["any headlines about the world cup right now?"])
    import sqlite3

    assert "web_off_lookup_refusal" in sealed
    # The runtime's own record of the turn: a refusal of the request, not a finished answer.
    conn = sqlite3.connect(f"file:{daemon.home / 'data' / 'vool_web0_v2.db'}?mode=ro", uri=True)
    try:
        last = conn.execute(
            "SELECT lifecycle_state, terminal_reason FROM runtime_attempts ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    assert last is not None and last[0] == "REFUSED_POLICY", last
    assert last[1] == "web_off_lookup_refusal", last


@pytest.mark.parametrize("asked", ["is the british museum open on mondays this month", "how much does a cup of coffee cost in cafes these days"])
def test_an_answer_from_memory_with_web_off_says_web_is_off(served, asked):
    daemon, _provider = served
    reply, _seconds = _ask(daemon, asked)
    assert "web lookups are" in reply.lower(), reply
