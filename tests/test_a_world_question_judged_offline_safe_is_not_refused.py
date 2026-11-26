"""With the web out of reach, the live-info lane does not refuse a message the turn's own web judgement read as
needing nothing from the internet; one judged to need it (or not judged) still gets the refusal.

iMac gate40 on 50f8bed75 (Offline chat, qwen3:8b): "who built the Colosseum in Rome?" was judged "web ask verdict:
no" and was still answered "Live web lookup is off because this chat is Offline ...". Wordings are fresh.
"""
from __future__ import annotations

import pytest

from core.agent_runtime import fast_live_info_runtime_preflight as preflight


class _Agent:
    def __init__(self) -> None:
        self.refused: list[str] = []

    def _live_info_mode(self, text, *, interpretation=None):
        return "fresh_lookup"   # the topic words read as a lookup, as the lane's reader did on the gate

    def _recover_price_lookup_query(self, text, *, source_context=None):
        return ""


@pytest.fixture
def offline(monkeypatch):
    import core.policy_engine as policy_engine
    import core.remote_fetch_policy as remote_fetch_policy

    monkeypatch.setattr(policy_engine, "allow_web_fallback", lambda: True)
    monkeypatch.setattr(remote_fetch_policy, "effective_web_available", lambda *_a, **_k: False)
    monkeypatch.setattr(preflight, "_explicit_remote_fetch_disabled", lambda *_a, **_k: False)
    monkeypatch.setattr(preflight, "disabled_live_info_result", lambda *_a, **_k: {"response": "refused"})


def _verdict(monkeypatch, verdict):
    import core.online_asks as online_asks

    monkeypatch.setattr(online_asks, "web_ask_verdict", lambda *_a, **_k: verdict)


@pytest.mark.parametrize("text", ["who designed the Sydney Opera House?", "kas pastatė Trakų pilį?",
                                  "wann wurde die Golden Gate Bridge eröffnet?"])
def test_a_message_judged_to_need_no_web_is_left_to_the_model(monkeypatch, offline, text):
    _verdict(monkeypatch, "no")
    mode, _query, result = preflight.prepare_live_info_request(
        _Agent(), text, session_id="s", source_context={}, interpretation=None)
    assert (mode, result) == ("", None)


@pytest.mark.parametrize("verdict", ["yes", None])
def test_a_message_judged_to_need_the_web_or_not_judged_still_gets_the_refusal(monkeypatch, offline, verdict):
    _verdict(monkeypatch, verdict)
    mode, _query, result = preflight.prepare_live_info_request(
        _Agent(), "what's the weather in Lisbon right now?", session_id="s", source_context={}, interpretation=None)
    assert mode == "fresh_lookup" and result == {"response": "refused"}
