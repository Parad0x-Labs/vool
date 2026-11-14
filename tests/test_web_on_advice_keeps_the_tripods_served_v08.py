"""Served: with Web lookups on, advice beside today's prices keeps what the fetched pages name (Mac AQ row 12, 617983f9).

Measured 2026-11-14 (qwen3:8b, web.search ran): "Based on the latest 2026 reviews, the best travel tripods balance
portability and stability. Prices fluctuate ... The lookup I ran didn't confirm current prices ..." -- no tripod
named. Whole sentences were withdrawn for their prices, and the tripods went with them. The prices the pages don't
state go; the tripods stay. A local SearXNG stub stands in for the search engine.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from tests._blackbox_served_rig import REPO_ROOT, ScriptedProvider, ServedDaemon, run_in_home
from tests.test_advice_beside_prices_served_web_off_v08 import _SEED, ASK, MODEL, _text

REPLY = ("Based on the latest 2026 reviews, the Peak Design Travel Tripod (from $380) and the Manfrotto Befree Advanced "
         "(around $180) balance portability and stability. Budget carbon fiber options start at $20.")
CALL = {"id": "call-1", "type": "function", "function": {"name": "web__search", "arguments": {"query": "best travel tripod 2026"}}}
RESULTS = {"results": [
    {"title": "12 Best Travel Tripods (November 2026): Tested & Reviewed", "url": "https://example.org/travel-tripods",
     "content": "We tested the Peak Design Travel Tripod, the Manfrotto Befree Advanced and budget carbon fiber tripods, "
                "from $20 to $600."}]}


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


def test_the_tripods_the_pages_name_stay_and_their_unstated_prices_go(tmp_path):
    from core.entity_ambiguity import AMBIGUITY_SYSTEM_PROMPT

    srv = HTTPServer(("127.0.0.1", 0), _SearXNG)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}"
    searched: list[int] = []

    def reply_fn(body):
        prompt = " ".join(str(m.get("content") or "") for m in body.get("messages") or [] if isinstance(m, dict))
        if AMBIGUITY_SYSTEM_PROMPT in prompt:
            return json.dumps({"ambiguous": False, "referents": [], "clarification": ""})
        names = [str((t.get("function") or {}).get("name")) for t in body.get("tools") or [] if isinstance(t, dict)]
        if "web__search" in names and not searched:
            searched.append(1)
            return CALL
        return REPLY

    home = tmp_path / "home"
    (home / "config").mkdir(parents=True, exist_ok=True)
    (home / "config" / "default_policy.yaml").write_text(
        "system:\n  allow_web_fallback: true\nweb:\n  allowed_engines: [searxng]\n  provider_order: [searxng]\n"
        f"  searxng_url: {url}\n",
        encoding="utf-8",
    )
    provider = ScriptedProvider({MODEL: REPLY}, reply_fn=reply_fn)
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
        text = _text(daemon.chat(ASK, session_id="served-row12-names", model=MODEL, mode="auto"))
    finally:
        daemon.stop()
        provider.__exit__(None, None, None)
        srv.shutdown()

    assert searched, "the model was never offered the web search"
    assert "Peak Design Travel Tripod" in text and "Manfrotto Befree Advanced" in text, text
    assert "$380" not in text and "$180" not in text, text
    assert "start at $20" in text, text
    assert "The lookup I ran didn't confirm current prices" in text, text
    assert "didn't run any live lookup" not in text, text
