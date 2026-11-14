"""Served, Mac-shaped: row 12 on b7a5266d (qwen3:8b, Web on, "suggest a good tripod for travel and tell me what it
costs today").

The Mac's stored state: the guide's "priced lines" were its byline ("Written by Laurence Norah ... a professional
travel photographer"), "Jess and I write this site ..." and five FAQ questions; no tripod and no price. The second
guide was never read. The model named the "Gitzo Traveler 2" and "Befree 190" from its own knowledge; the edge took
the names out but left "It's highly recommended ..." and "It's a good choice ..." leaning on them, and showed the
byline, the FAQ and "from $20 to $600" as what the pages say.

Here the guide holds its priced picks below the byline and the second guide cannot be read: the pages' priced picks
lead the answer, the sentences leaning on a dropped name go with it, and no byline, question or price
span is shown. The page reads are stood in for by a test-only `sitecustomize` in the served daemon (the cloud cannot
read real pages); the search engine is a local SearXNG stub.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

from tests._blackbox_served_rig import REPO_ROOT, ScriptedProvider, ServedDaemon, run_in_home
from tests.test_advice_beside_prices_served_web_off_v08 import _SEED, ASK, MODEL, _text

FTU = "https://www.findingtheuniverse.example.test/best-travel-tripods/"
REVELL = "https://revellphotography.example.test/best-travel-tripods/"
OUTDOOR = "https://outdoor-guides.example.test/travel-tripods/"
RESULTS = {"results": [
    {"title": "Best Travel Tripods 2026: A Photographer's Buying Guide", "url": FTU,
     "content": "I travel with a Canon R5 and heavy telephoto zoom. These are the travel and camera tripods that I "
                "recommend in 2026, from budget carbon to Gitzo."},
    {"title": "12 Best Travel Tripods (November 2026): Tested &amp; Reviewed", "url": REVELL,
     "content": "We tested 12 of the best travel tripods for 2026 from $20 to $600."},
]}
OUTDOOR_ROW = {"title": "Best Travel Tripod 2026: The Ones We Pack", "url": OUTDOOR,
               "snippet": "The best travel tripod for 2026, with prices from the shops we checked."}
FTU_PAGE = (
    "Best Travel Tripods 2026: A Photographer's Buying Guide\n"
    "Written by Laurence Norah - Leave a Comment I'm Laurence , a professional travel photographer since 2010.\n"
    "Jess and I write this site from our own travels.\n"
    "One thing to be clear about before the list: I haven’t used every tripod here for months on end.\n"
    "Gitzo Traveler Series 1 Kit\n"
    "The tripod I travel with for my Canon R5 and heavy telephoto zoom. Carbon legs that pack small.\n"
    "Price: About £540\n"
    "Peak Design Travel Tripod Carbon\n"
    "Compact and light, it folds to the size of a water bottle. Around $599.\n"
    "Budget travel tripods run from $50 to $150.\n"
    "Is a carbon fibre travel tripod worth it?\n"
    "Can you take a tripod on a plane in hand luggage?\n"
    "How tall should a travel tripod be?\n"
)
OUTDOOR_PAGE = "Travel Tripods We Pack\nUlanzi Zero Y Travel Tripod\nA light carbon pick that costs $149.99.\n"
SITECUSTOMIZE = f'''
import tools.web.http_fetch as _hf
_real = _hf.http_fetch_text
_PAGES = {{{FTU!r}: {FTU_PAGE!r}, {OUTDOOR!r}: {OUTDOOR_PAGE!r}}}
def _page(url, **kw):
    if str(url) in _PAGES:
        return {{"status": "ok", "url": url, "final_url": url, "text": _PAGES[str(url)], "html": ""}}
    if str(url) == {REVELL!r}:
        return {{"status": "error", "url": url, "error": "HTTP 403"}}
    return _real(url, **kw)
_hf.http_fetch_text = _page
'''
CALL = {"id": "call-1", "type": "function",
        "function": {"name": "web__search", "arguments": {"query": "best travel tripod 2026", "limit": 5}}}
MAC_REPLY = (
    "For travel, I'd suggest the **Gitzo Traveler 2**. It's highly recommended for its stability and durability, "
    "especially for heavy telephoto lenses. It's a bit more expensive, but it's worth the investment if you're "
    "serious about travel photography.\n\n"
    "The **Manfrotto Befree 190** is another option. It's a good choice if you're looking for a balance between cost "
    "and quality.\n\n"
    "Prices may vary slightly depending on the retailer and any promotions."
)


class _SearXNG(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps(RESULTS).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _row12(tmp_path: Path, reply: str) -> tuple[str, list[str]]:
    from core.entity_ambiguity import AMBIGUITY_SYSTEM_PROMPT

    srv = HTTPServer(("127.0.0.1", 0), _SearXNG)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}"
    site = tmp_path / "site"
    site.mkdir()
    (site / "sitecustomize.py").write_text(SITECUSTOMIZE, encoding="utf-8")
    prompts: list[str] = []

    def reply_fn(body: dict[str, Any]) -> Any:
        prompt = " ".join(str(m.get("content") or "") for m in body.get("messages") or [] if isinstance(m, dict))
        if AMBIGUITY_SYSTEM_PROMPT in prompt:
            return json.dumps({"ambiguous": False, "referents": [], "clarification": ""})
        names = [str((t.get("function") or {}).get("name")) for t in body.get("tools") or [] if isinstance(t, dict)]
        if "web__search" in names:
            prompts.append(prompt)
            return CALL if len(prompts) == 1 else reply
        return reply

    home = tmp_path / "home"
    (home / "config").mkdir(parents=True, exist_ok=True)
    (home / "config" / "default_policy.yaml").write_text(
        "system:\n  allow_web_fallback: true\nweb:\n  allowed_engines: [searxng]\n  provider_order: [searxng]\n"
        f"  searxng_url: {url}\n",
        encoding="utf-8",
    )
    provider = ScriptedProvider({MODEL: reply}, reply_fn=reply_fn)
    daemon = ServedDaemon(home, env_extra={
        "OLLAMA_HOST": provider.base_url, "VOOL_OLLAMA_URL": provider.base_url,
        "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat", "HOME": str(tmp_path / "owner-home"),
        "SEARXNG_URL": url, "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
        "PYTHONPATH": f"{site}:{REPO_ROOT}",
    })
    provider.__enter__()
    try:
        try:
            daemon.start(timeout=240)
        except Exception as exc:  # pragma: no cover - environment, not the reply
            pytest.skip(f"served daemon could not boot here: {exc}")
        run_in_home(home, _SEED.format(root=REPO_ROOT, base_url=provider.base_url, registered=[MODEL], model=MODEL))
        provider.reset()
        text = _text(daemon.chat(ASK, session_id="row12-b7a5266d", model="vool", model_selection="auto",
                                 surface="desktop", messages=[{"role": "user", "content": ASK}]))
    finally:
        daemon.stop()
        provider.__exit__(None, None, None)
        srv.shutdown()
    return text, prompts


def test_the_guides_byline_and_faq_are_not_its_priced_lines() -> None:
    from core.execution.web_tools import priced_page_lines

    lines = priced_page_lines(FTU_PAGE, topic=["travel", "tripod"])
    assert lines[:2] == [
        "Gitzo Traveler Series 1 Kit -- Price: About £540",
        "Peak Design Travel Tripod Carbon -- Compact and light, it folds to the size of a water bottle. Around $599.",
    ], lines
    assert not any("Laurence" in line or "Jess" in line or "?" in line or "clear about" in line for line in lines), lines


def test_a_guide_that_cannot_be_read_does_not_use_up_a_read() -> None:
    from core.execution.models import _tool_observation
    from core.execution.web_tools import execute_web_tool

    rows = [{"title": r["title"], "url": r["url"], "snippet": r["content"]} for r in RESULTS["results"]] + [OUTDOOR_ROW]
    fetched: list[str] = []

    def call_tool(name: str, **kwargs: Any) -> Any:
        fetched.append(kwargs["url"])
        if kwargs["url"] == REVELL:
            return {"status": "error", "url": REVELL, "error": "HTTP 403"}
        return {"status": "ok", "url": kwargs["url"], "text": {FTU: FTU_PAGE, OUTDOOR: OUTDOOR_PAGE}[kwargs["url"]]}

    execution = execute_web_tool(
        "web.search", {"query": "best travel tripod 2026", "limit": 5}, task_id="t-row12c",
        source_context={"surface": "desktop"}, allow_web_fallback_fn=lambda: True, load_builtin_tools_fn=lambda: None,
        planned_search_query_fn=lambda *a, **k: [dict(row) for row in rows], call_tool_fn=call_tool,
        adaptive_research_fn=None, unsupported_execution_for_intent_fn=None, tool_observation_fn=_tool_observation,
        audit_log_fn=lambda *a, **k: None,
    )

    assert fetched == [FTU, REVELL, OUTDOOR], fetched
    lines = [line for page in execution.details["observation"]["page_excerpts"] for line in page["priced_lines"]]
    assert "Ulanzi Zero Y Travel Tripod -- A light carbon pick that costs $149.99." in lines, lines


def test_the_pages_priced_picks_lead_and_nothing_leans_on_a_dropped_name(tmp_path) -> None:
    from core.agent_runtime.reply_edge import UNFETCHED_PRICES_NAMES_LINE

    text, prompts = _row12(tmp_path, MAC_REPLY)

    assert "Gitzo Traveler 2" not in text and "Befree 190" not in text, text
    assert "It's highly recommended" not in text and "It's a bit more expensive" not in text, text
    assert "It's a good choice" not in text, text
    assert text.startswith("Here is what the pages I read say"), text
    assert "Gitzo Traveler Series 1 Kit -- Price: About £540" in text, text
    assert "Around $599" in text, text
    for junk in ("Written by", "Laurence", "hand luggage", "worth it?", "from $20 to $600", "from $50 to $150"):
        assert junk not in text, (junk, text)
    assert "&amp;" not in text, text
    assert "Prices may vary slightly" in text, text
    assert text.rstrip().endswith(UNFETCHED_PRICES_NAMES_LINE), text
    # The priced picks, not the byline, were in front of the model on the round after the search.
    assert "Gitzo Traveler Series 1 Kit -- Price: About £540" in prompts[1], prompts[1][-1500:]
    assert "Written by Laurence" not in prompts[1], prompts[1][-1500:]
