"""Served: a model that fails the round after a good web lookup still leaves an answer (Mac AQ row 12, 98b42315).

Measured on the Mac (qwen3:8b under load): web.search returned two on-topic guides, the next round sent no usable tool
call after 103 s, the loop asked the model again to write the answer, and the turn was stopped with nothing published
("Cancelled before publication"). The pages were already in hand. The turn now answers from them at once, in their own
words, without a second model call. A local SearXNG stub stands in for the search engine.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

from tests._blackbox_served_rig import REPO_ROOT, ScriptedProvider, ServedDaemon, run_in_home
from tests.test_advice_beside_prices_served_web_off_v08 import _SEED, ASK, MODEL, _text

GUIDE = "12 Best Travel Tripods (November 2026)"
RESULTS = {"results": [{"title": GUIDE, "url": "https://guides.example.test/best-travel-tripods",
                        "content": "We tested the Peak Design Travel Tripod and budget tripods from $20 to $600."}]}
CALL = {"id": "call-1", "type": "function", "function": {"name": "web__search", "arguments": {"query": "best travel tripod 2026"}}}


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


def _events(home) -> list[str]:
    conn = sqlite3.connect(str(home / "data" / "vool_web0_v2.db"))
    try:
        return [str(row[0]) for row in conn.execute("SELECT event_type FROM runtime_session_events ORDER BY rowid")]
    finally:
        conn.close()


def _served_row12(tmp_path, after_search: str) -> tuple[str, list[str], list[str]]:
    """Row 12 served: the model searches, then answers the next round with `after_search`."""
    from core.entity_ambiguity import AMBIGUITY_SYSTEM_PROMPT

    srv = HTTPServer(("127.0.0.1", 0), _SearXNG)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}"
    rounds: list[str] = []

    def reply_fn(body: dict[str, Any]) -> Any:
        prompt = " ".join(str(m.get("content") or "") for m in body.get("messages") or [] if isinstance(m, dict))
        if AMBIGUITY_SYSTEM_PROMPT in prompt:
            return json.dumps({"ambiguous": False, "referents": [], "clarification": ""})
        names = [str((t.get("function") or {}).get("name")) for t in body.get("tools") or [] if isinstance(t, dict)]
        if "web__search" in names:
            rounds.append("tool round")
            return CALL if len(rounds) == 1 else after_search
        return "The Peak Design Travel Tripod costs $379."

    home = tmp_path / "home"
    (home / "config").mkdir(parents=True, exist_ok=True)
    (home / "config" / "default_policy.yaml").write_text(
        "system:\n  allow_web_fallback: true\nweb:\n  allowed_engines: [searxng]\n  provider_order: [searxng]\n"
        f"  searxng_url: {url}\n",
        encoding="utf-8",
    )
    provider = ScriptedProvider({MODEL: "unused"}, reply_fn=reply_fn)
    daemon = ServedDaemon(home, env_extra={
        "OLLAMA_HOST": provider.base_url, "VOOL_OLLAMA_URL": provider.base_url,
        "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat", "HOME": str(tmp_path / "owner-home"),
        "SEARXNG_URL": url, "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
    })
    provider.__enter__()
    try:
        try:
            daemon.start(timeout=240)
        except Exception as exc:  # pragma: no cover - environment, not the reply
            pytest.skip(f"served daemon could not boot here: {exc}")
        run_in_home(home, _SEED.format(root=REPO_ROOT, base_url=provider.base_url, registered=[MODEL], model=MODEL))
        provider.reset()
        text = _text(daemon.chat(ASK, session_id="served-row12-stalled", model=MODEL, mode="auto"))
        events = _events(home)
    finally:
        daemon.stop()
        provider.__exit__(None, None, None)
        srv.shutdown()

    return text, rounds, events


def test_a_failed_round_after_a_good_search_answers_from_the_pages(tmp_path) -> None:
    # After the search: no call and no text, as a stalled model leaves it.
    text, rounds, events = _served_row12(tmp_path, "")
    assert rounds and rounds[0] == "tool round", rounds
    # No second model call to write the answer once the model has failed a round.
    assert "tool_synthesis_skipped" in events, events
    assert "I looked this up, but I couldn't finish writing the answer" in text, text
    assert GUIDE in text and "Peak Design Travel Tripod" in text, text
    assert "$379" not in text, text
    assert "Cancelled" not in text and "Incomplete" not in text, text


def test_only_a_turn_of_good_web_lookups_is_answered_this_way() -> None:
    from core.agent_runtime.research_tool_loop_facade import web_lookup_in_plain_words

    search = {"tool_name": "web.search", "ok": True, "observation": {
        "results": [{"title": GUIDE, "url": "https://guides.example.test/t", "snippet": "from $20 to $600"}],
        "page_excerpts": [{"url": "https://guides.example.test/t", "priced_lines": ["1. Peak Design Travel Tripod -- $379.95"]}],
    }}
    text = web_lookup_in_plain_words([search])
    assert GUIDE in text and "1. Peak Design Travel Tripod -- $379.95" in text, text
    assert web_lookup_in_plain_words([search, {"tool_name": "machine.read_file", "ok": True}]) == ""
    assert web_lookup_in_plain_words([{**search, "ok": False}]) == ""


# Quiet-Mac rerun of row 12 on 98b42315: the whole reply was "The lookup I ran didn't confirm current measured-quantity
# figures, so I've left out any figure it didn't state." and the receipt still listed Wikipedia's "Tripod (web hosting)".
NOTICE = ("I didn't run any live lookup on this turn, so I can't verify current measured-quantity figures — any specific "
          "numbers I gave would be invented, and I'm not going to state them. Ask again and I'll fetch the real data first.")


def test_the_lookup_notice_says_prices_on_a_price_ask() -> None:
    from core.model_output_guard import looked_up_live_value_notice

    assert looked_up_live_value_notice(NOTICE, ASK) == (
        "The lookup I ran didn't confirm current prices, so I've left out any figure it didn't state."
    )
    said = looked_up_live_value_notice(NOTICE, "how heavy is the peak design travel tripod")
    assert "measured-quantity" not in said and "current figures" in said, said


def test_a_bare_lookup_notice_gets_the_pages_in_front_of_it() -> None:
    from core.agent_runtime.reply_edge import edge_reply, pages_instead_of_a_bare_notice
    from core.model_output_guard import looked_up_live_value_notice

    observation = {"intent": "web.search", "ok": True, "results": [
        {"title": GUIDE, "url": "https://guides.example.test/t", "snippet": "travel tripods from $20 to $600"}],
        "page_excerpts": [{"url": "https://guides.example.test/t",
                           "priced_lines": ["Peak Design Travel Tripod (Carbon) 2.8 lb -- $379.95"]}]}
    notice = looked_up_live_value_notice(NOTICE, ASK)
    context = {"_tool_loop_web_text": json.dumps(observation)}
    text = edge_reply({"response": notice}, ASK, context)["response"]

    assert text.startswith("Here is what the pages I found say"), text
    assert GUIDE in text and "Peak Design Travel Tripod (Carbon) 2.8 lb -- $379.95" in text, text
    assert text.endswith(notice), text
    # A reply that says something beside the notice is left as it is.
    kept = "The Peak Design Travel Tripod is the compact pick.\n\n" + notice
    assert pages_instead_of_a_bare_notice(kept, context) == kept


def test_a_shopping_search_keeps_only_on_topic_rows_for_the_receipt_and_the_model() -> None:
    from tests.test_mac_aq_row12_pass2_on_f7a80b44_v08 import GUIDE as GUIDE_ROW
    from tests.test_mac_aq_row12_pass2_on_f7a80b44_v08 import NOVELS, WIKI, _search

    fetched: list[str] = []
    execution = _search("best travel tripod 2026 price", [WIKI, NOVELS, GUIDE_ROW], fetched)
    titles = [row["title"] for row in execution.details["observation"]["results"]]
    assert titles == [GUIDE_ROW["title"]], titles
    assert "Tripod (web hosting)" not in execution.response_text
    # A search that isn't about buying keeps every row.
    plain = _search("tripod history", [WIKI, NOVELS], [])
    assert len(plain.details["observation"]["results"]) == 2


def test_a_reply_whose_figures_are_all_withdrawn_still_shows_the_pages(tmp_path) -> None:
    # The quiet-Mac shape: the model answers in figures the pages don't state, every one is withdrawn, and only the
    # notice was left.
    text, _rounds, _events = _served_row12(
        tmp_path, "Right now the Peak Design Travel Tripod weighs 2.8 lb and currently costs $379."
    )
    assert text.startswith("Here is what the pages I found say"), text
    assert GUIDE in text, text
    assert text.endswith("The lookup I ran didn't confirm current prices, so I've left out any figure it didn't state."), text
    assert "$379" not in text and "measured-quantity" not in text, text
