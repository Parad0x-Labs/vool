"""With Web lookups off, a message judged to ask nothing of the internet is not offered the lookup tools.

Gate iMac on f5cff3511 (qwen3:8b, a chat with earlier messages, nothing saved about Greta): "what is Greta's rabbit
called?" went to the tool lane, the model called a web lookup, and the whole reply was "Web lookups are off. You can
turn them on in Settings ... paste the article text". A question about the person's own life is never answered with
the web-off refusal. A message judged to need the web, or one nobody judged, keeps the offer and its refusal.
"""
from __future__ import annotations

import pytest

from core.execution.web_tools import WEB_LOOKUP_INTENTS
from core.tool_offer_assembly import assemble_tool_offer


def _offered(monkeypatch, text: str, verdict: str | None) -> set[str]:
    import core.online_asks as online_asks
    import core.remote_fetch_policy as remote_fetch_policy

    monkeypatch.setattr(remote_fetch_policy, "effective_web_available", lambda *_a, **_k: False)
    monkeypatch.setattr(online_asks, "web_ask_verdict", lambda *_a, **_k: verdict)
    offer = assemble_tool_offer(user_text=text, source_context={})
    return {str(spec.get("intent") or "") for spec in offer.specs}


@pytest.mark.parametrize("text", ["what is Greta's rabbit called?", "kaip vadinasi Gretos triušis?",
                                  "search the web for Greta's rabbit breed"])
def test_lookups_are_offered_only_to_a_message_judged_to_need_the_web(monkeypatch, text):
    assert not _offered(monkeypatch, text, "no") & WEB_LOOKUP_INTENTS
    if text.startswith("what is Greta"):
        assert _offered(monkeypatch, text, "yes") & WEB_LOOKUP_INTENTS  # what the gate's turn was offered
    if "search the web" in text:
        # Judged a web ask, or not judged at all: the offer (and so the web-off refusal) stays as it was.
        assert _offered(monkeypatch, text, "yes") == _offered(monkeypatch, text, None)
