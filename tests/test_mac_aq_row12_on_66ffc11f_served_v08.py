"""Served, Mac-shaped: row 12 on 66ffc11f (qwen3:8b, Web on, "suggest a good tripod for travel and tell me what it
costs today").

The Mac ledger: web.search ("best travel tripod 2026", 3 sources: Wikipedia "Tripod (photography)", two guides), the
guide page was read and named the Peak Design Travel Tripod (Carbon) 2.8 lb and (Aluminium) 3.44 lb with "Check price
on Amazon", qwen3:8b then sent no usable reply on the next round (MALFORMED_TOOL_CALL, 105 s), the tool loop answered
from the pages -- and the publication gate withheld that as "what I found doesn't back the answer I wrote". Three runs
in a row failed the round after the search.

The page read is stood in for by a test-only `sitecustomize` in the served daemon: the cloud cannot read real pages,
and search hits are read public-only. The search engine is a local SearXNG stub.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

from tests._blackbox_served_rig import REPO_ROOT, ScriptedProvider, ServedDaemon, run_in_home
from tests.test_advice_beside_prices_served_web_off_v08 import _SEED, ASK, MODEL, _text

GUIDE_URL = "https://www.guides.example.test/best-travel-tripods/"
RESULTS = {"results": [
    {"title": "Tripod (photography) - Wikipedia", "url": "https://en.wikipedia.example.test/wiki/Tripod_(photography)",
     "content": "In photography, a tripod is a portable device used to support a camera. Travel tripods fold small."},
    {"title": "Best Travel Tripods 2026: A Photographer's Buying Guide", "url": GUIDE_URL,
     "content": "These are the travel and camera tripods that I recommend in 2026, from budget carbon to Gitzo."},
]}
PAGE = (
    "Best Travel Tripods 2026\nTripod | Weight | Price\nPeak Design Travel Tripod (Carbon)\n2.8 lb\n"
    "Check price on Amazon\nPeak Design Travel Tripod (Aluminium)\n3.44 lb\nCheck price on Amazon\n"
    "Peak Design launched a Pro Tripod line in November 2025, priced from £699.99 to £999.\n"
)
SITECUSTOMIZE = f'''
import tools.web.http_fetch as _hf
_real = _hf.http_fetch_text
def _page(url, **kw):
    if str(url).startswith({GUIDE_URL!r}):
        return {{"status": "ok", "url": url, "final_url": url, "text": {PAGE!r}, "html": ""}}
    return _real(url, **kw)
_hf.http_fetch_text = _page
'''
CALL = {"id": "call-1", "type": "function",
        "function": {"name": "web__search", "arguments": {"query": "best travel tripod 2026", "limit": 5}}}
REASK_MARK = "You already have the tool results above."


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


def _row12(tmp_path: Path, after_search, *, reask_reply: str = "") -> tuple[str, list[dict[str, Any]], list[str]]:
    from core.entity_ambiguity import AMBIGUITY_SYSTEM_PROMPT

    srv = HTTPServer(("127.0.0.1", 0), _SearXNG)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}"
    site = tmp_path / "site"
    site.mkdir()
    (site / "sitecustomize.py").write_text(SITECUSTOMIZE, encoding="utf-8")
    prompts: list[str] = []

    def reply_fn(body: dict[str, Any]) -> Any:
        messages = [m for m in body.get("messages") or [] if isinstance(m, dict)]
        prompt = " ".join(str(m.get("content") or "") for m in messages)
        if AMBIGUITY_SYSTEM_PROMPT in prompt:
            return json.dumps({"ambiguous": False, "referents": [], "clarification": ""})
        names = [str((t.get("function") or {}).get("name")) for t in body.get("tools") or [] if isinstance(t, dict)]
        if "web__search" in names:
            prompts.append(prompt)
            if len(prompts) == 1:
                return CALL
            if REASK_MARK in str(messages[-1].get("content") or ""):
                return reask_reply
            return after_search
        return "unused"

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
        # The body the Mac driver sends: the app's own model choice, the desktop surface.
        text = _text(daemon.chat(ASK, session_id="row12-66ffc11f", model="vool", model_selection="auto",
                                 surface="desktop", messages=[{"role": "user", "content": ASK}]))
        conn = sqlite3.connect(str(home / "data" / "vool_web0_v2.db"))
        try:
            rows = conn.execute("SELECT event_type, details_json FROM runtime_session_events ORDER BY rowid").fetchall()
        finally:
            conn.close()
    finally:
        daemon.stop()
        provider.__exit__(None, None, None)
        srv.shutdown()
    events = [{"type": str(kind), **json.loads(details or "{}")} for kind, details in rows]
    return text, events, prompts


def test_a_stalled_round_after_the_page_read_publishes_the_pages(tmp_path) -> None:
    text, events, prompts = _row12(tmp_path, "")

    assert "doesn't back the answer I wrote" not in text, text
    assert text.startswith("I looked this up, but I couldn't finish writing the answer in time."), text
    assert "Peak Design Travel Tripod (Carbon) -- 2.8 lb" in text, text
    assert "Peak Design Travel Tripod (Aluminium) -- 3.44 lb" in text, text
    assert "Wikipedia" not in text, text
    # The failure record now says what came back instead of a call.
    said = [e["type"] for e in events if "content_chars=0" in json.dumps(e)]
    assert said, sorted({e["type"] for e in events})
    # The page's lines reached the model on the round after the search.
    assert "Peak Design Travel Tripod (Carbon) -- 2.8 lb" in prompts[1], prompts[1][-2000:]


def test_the_round_after_the_search_may_answer_on_the_reask(tmp_path) -> None:
    answer = "For travel, the Peak Design Travel Tripod (Carbon) is the light pick at 2.8 lb; the page links Amazon for its price."
    text, events, _prompts = _row12(tmp_path, "", reask_reply=answer)

    assert "Peak Design Travel Tripod (Carbon)" in text, text
    assert "couldn't finish writing the answer" not in text, text
    assert not [e for e in events if e["type"] == "model.call_failed"], [e["type"] for e in events]
