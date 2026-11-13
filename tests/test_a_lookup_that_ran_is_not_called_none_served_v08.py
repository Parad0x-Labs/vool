"""Served: a web search that ran is never reported as no lookup (Mac answer-quality row 12, recheck on f50e52eb).

Measured 2026-11-13 (qwen3:8b, Web lookups on, clean loop on): "suggest a good tripod for travel and tell me what it
costs today" ran web.search, which bound four pages, and the reply ended "I didn't run any live lookup on this
turn ... Ask again and I'll fetch the real data first." The search's receipt was written to the tool loop's own
copy of the turn context, so the seal saw a turn that observed nothing. A local SearXNG stub stands in for the
search engine; everything between the request and the reply is production code.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from tests._blackbox_served_rig import REPO_ROOT, ScriptedProvider, ServedDaemon, run_in_home
from tests.test_advice_beside_prices_served_web_off_v08 import _SEED, ASK, MODEL, _text

REPLY = ("Here are good travel tripods today: the Peak Design Travel Tripod currently costs $379.95. For ultra-portable needs, the **Gitzo V10** (from $200) balances weight and durability. Budget options start at **$20** (e.g., cheap carbon fiber tripods), but most "
         "reliable travel tripods range **$30–$600** depending on your camera and use case.")
CALL = {"id": "call-1", "type": "function", "function": {"name": "web__search", "arguments": {"query": "best travel tripod 2026"}}}
RESULTS = {"results": [
    {"title": "12 Best Travel Tripods (November 2026): Tested & Reviewed", "url": "https://revellphotography.com/travel-tripods",
     "content": "We tested 12 of the best travel tripods for 2026 from $20 to $600, from budget carbon fiber to the Gitzo."},
    {"title": "Best Travel Tripods 2026: A Photographer's Buying Guide", "url": "https://findingtheuniverse.com/tripods",
     "content": "I travel with a Canon R5 and a heavy zoom; these travel tripods cost $30 to $600 today."}]}

class H(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps(RESULTS).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass

def test_a_search_that_ran_is_named_as_run_when_a_figure_is_withheld(tmp_path):
    from core.entity_ambiguity import AMBIGUITY_SYSTEM_PROMPT
    srv = HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}"
    done = []
    def reply_fn(body):
        prompt = " ".join(str(m.get("content") or "") for m in body.get("messages") or [] if isinstance(m, dict))
        if AMBIGUITY_SYSTEM_PROMPT in prompt:
            return json.dumps({"ambiguous": False, "referents": [], "clarification": ""})
        names = [str((t.get("function") or {}).get("name")) for t in body.get("tools") or [] if isinstance(t, dict)]
        if "web__search" in names and not done:
            done.append(1); return CALL
        return REPLY
    home = tmp_path / "home"
    (home / "config").mkdir(parents=True, exist_ok=True)
    (home / "config" / "default_policy.yaml").write_text(
        "system:\n  allow_web_fallback: true\nweb:\n  allowed_engines: [searxng]\n  provider_order: [searxng]\n  searxng_url: " + url + "\n", encoding="utf-8")
    provider = ScriptedProvider({MODEL: REPLY}, reply_fn=reply_fn)
    daemon = ServedDaemon(home, env_extra={"OLLAMA_HOST": provider.base_url, "VOOL_OLLAMA_URL": provider.base_url,
        "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat", "HOME": str(tmp_path / "owner-home"), "SEARXNG_URL": url,
        "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"})
    provider.__enter__()
    try:
        daemon.start(timeout=240)
        run_in_home(home, _SEED.format(root=REPO_ROOT, base_url=provider.base_url, registered=[MODEL], model=MODEL))
        provider.reset()
        text = _text(daemon.chat(ASK, session_id="served-row12", model=MODEL, mode="auto"))
        assert done, "the model was never offered the web search"
        assert "didn't run any live lookup" not in text, text
        assert "Ask again and I'll fetch the real data first" not in text, text
        assert "Web lookups are off" not in text, text
        assert "Gitzo V10" in text, text
        # The figure no page stated stays withheld, and the reply says why.
        assert "$379.95" not in text, text
        assert "The lookup I ran didn't confirm current prices" in text, text
    finally:
        daemon.stop(); provider.__exit__(None, None, None); srv.shutdown()
