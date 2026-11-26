"""A turn is offered only tools it can call: with the web out, a judged ask for something of the person's own is not
offered the lookups, and a folder listing the turn's preflight ruled out is not offered at all.

iMac gate39 on f31c60b65 (Offline, qwen3:8b): a file ask was judged find + web, the tool lane offered web.search and
machine.list_directory beside machine.find_file, the model listed the Desktop, core.tool_intent_executor refused the
listing (no machine or path target), and the reply was a plan ("Would you like me to proceed?") instead of the find.
"""
from __future__ import annotations

from core import plugin_ask
from core.execution.web_tools import WEB_LOOKUP_INTENTS
from core.tool_offer_assembly import assemble_tool_offer

ASK = "my boat insurance letter should be somewhere on this mac, also check online when such letters usually renew"
LISTING = "show me what is in the Downloads folder on this computer"


def _offered(monkeypatch, text: str, *, web: bool, verdict: str | None, context: dict | None = None) -> set[str]:
    import core.online_asks as online_asks
    import core.remote_fetch_policy as remote_fetch_policy

    monkeypatch.setattr(remote_fetch_policy, "effective_web_available", lambda *_a, **_k: web)
    monkeypatch.setattr(online_asks, "web_ask_verdict", lambda *_a, **_k: verdict)
    offer = assemble_tool_offer(user_text=text, source_context=dict(context or {}))
    return {str(spec.get("intent") or "") for spec in offer.specs}


def test_with_the_web_out_a_judged_own_ask_is_not_offered_the_lookups(monkeypatch):
    plugin_ask._record_builtins(ASK, ("web",))
    # Judged the web alone and read as needing it: the offer (and so the web-off refusal) stays.
    assert _offered(monkeypatch, ASK, web=False, verdict="yes") & WEB_LOOKUP_INTENTS
    plugin_ask._record_builtins(ASK, ("find", "web"))
    offered = _offered(monkeypatch, ASK, web=False, verdict="yes")
    assert not offered & WEB_LOOKUP_INTENTS
    # The same judgement with the web reachable keeps the lookups: only a call that cannot run is taken out.
    assert _offered(monkeypatch, ASK, web=True, verdict="yes") & WEB_LOOKUP_INTENTS
    plugin_ask._record_builtins(ASK, ("find",))
    assert not _offered(monkeypatch, ASK, web=False, verdict="yes") & WEB_LOOKUP_INTENTS


def test_a_listing_the_preflight_ruled_out_is_not_offered(monkeypatch):
    unstamped = _offered(monkeypatch, LISTING, web=False, verdict="no")
    assert "machine.list_directory" in unstamped   # what the words alone are offered
    admitted = _offered(monkeypatch, LISTING, web=False, verdict="no",
                        context={"_semantic_machine_list_directory_admitted": True})
    assert "machine.list_directory" in admitted
    refused = _offered(monkeypatch, LISTING, web=False, verdict="no",
                       context={"_semantic_machine_list_directory_admitted": False})
    assert "machine.list_directory" not in refused
    assert refused - {"machine.list_directory"} == unstamped - {"machine.list_directory"}
