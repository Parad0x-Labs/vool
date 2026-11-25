"""A link asked about with Web lookups off keeps the reason it was not opened.

iMac gate24 on a1cf06d6d (qwen3:8b, Web lookups off): "please open https://example.org/menu and tell me what's on it"
had web.fetch refused with "Web lookups are off ... You can turn them on in Settings", and the unfetched-link backstop
replaced that with "Ask me to fetch it and I'll read the page itself": a fetch that could not run, and no word of the
switch that stops it.
"""
from __future__ import annotations

import pytest

from core.agent_runtime.action_honesty_validator import enforce_url_grounding
from core.remote_fetch_policy import OFFLINE_LOOKUP_TEXT, WEB_OFF_LOOKUP_TEXT

ASK = "could you look at https://example.net/timetable and tell me about the last bus?"


@pytest.mark.parametrize("decline", [WEB_OFF_LOOKUP_TEXT, OFFLINE_LOOKUP_TEXT])
def test_the_web_off_decline_stands(decline):
    out = enforce_url_grounding({"response": decline}, user_input=ASK, fetch_attempts=0)
    assert out["response"] == decline and not out.get("url_grounding_validator")


def test_an_invented_page_is_still_declined():
    out = enforce_url_grounding({"response": "The last bus leaves at 23:40 from the main square."}, user_input=ASK,
                                fetch_attempts=0)
    assert out.get("url_grounding_validator", {}).get("applied"), out
