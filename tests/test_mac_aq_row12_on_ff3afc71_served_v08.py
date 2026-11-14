"""Served, Mac-shaped: row 12 on ff3afc71 (qwen3:8b, Web on, "suggest a good tripod for travel and tell me what it
costs today").

The Mac read both guides (findingtheuniverse, revellphotography). The reply named the "Gitzo Traveler 2" and the
"Manfrotto Befree 190" -- the pages have "Gitzo Traveler Series 1 Kit" (about £540), "Manfrotto Befree" and "Befree GT
XPRO", and no "190" anywhere -- and used none of the prices the pages carry (the Peak Design Travel Tripod Carbon
"around $599"). Names the pages don't have go; what the pages did say comes with the reply.

The page reads are stood in for by a test-only `sitecustomize` in the served daemon (the cloud cannot read real
pages, and hits are read public-only); the search engine is a local SearXNG stub.
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
RESULTS = {"results": [
    {"title": "Best Travel Tripods 2026: A Photographer's Buying Guide", "url": FTU,
     "content": "These are the travel and camera tripods that I recommend in 2026, from budget carbon to Gitzo."},
    {"title": "12 Best Travel Tripods (November 2026): Tested & Reviewed", "url": REVELL,
     "content": "We tested 12 of the best travel tripods for 2026 from $20 to $600."},
]}
PAGES = {
    FTU: ("Best Travel Tripods 2026\nGitzo Traveler Series 1 Kit\n£540\nManfrotto Befree\n"
          "A light aluminium travel tripod for smaller cameras.\nManfrotto Befree GT XPRO\n"
          "Taller, with a 90-degree column.\n"),
    REVELL: ("12 Best Travel Tripods\n1. Peak Design Travel Tripod (Carbon)\nOur top pick, around $599.\n"
             "2. Ulanzi Zero Y\nBudget carbon pick.\n"),
}
SITECUSTOMIZE = f'''
import tools.web.http_fetch as _hf
_real = _hf.http_fetch_text
_PAGES = {PAGES!r}
def _page(url, **kw):
    if str(url) in _PAGES:
        return {{"status": "ok", "url": url, "final_url": url, "text": _PAGES[str(url)], "html": ""}}
    return _real(url, **kw)
_hf.http_fetch_text = _page
'''
CALL = {"id": "call-1", "type": "function",
        "function": {"name": "web__search", "arguments": {"query": "best travel tripod 2026", "limit": 5}}}
MAC_REPLY = (
    "For travel, two tripods stand out:\n"
    "- **Gitzo Traveler 2**: very compact carbon legs that fold flat.\n"
    "- **Manfrotto Befree 190**: a sturdy, affordable all-rounder.\n"
    "The Peak Design Travel Tripod (Carbon) is the top pick on the reviews I found."
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
        text = _text(daemon.chat(ASK, session_id="row12-ff3afc71", model="vool", model_selection="auto",
                                 surface="desktop", messages=[{"role": "user", "content": ASK}]))
    finally:
        daemon.stop()
        provider.__exit__(None, None, None)
        srv.shutdown()
    return text, prompts


def test_names_the_pages_dont_have_go_and_what_the_pages_say_comes_with_the_reply(tmp_path) -> None:
    text, prompts = _row12(tmp_path, MAC_REPLY)

    assert "Gitzo Traveler 2" not in text and "Befree 190" not in text, text
    assert "The Peak Design Travel Tripod (Carbon) is the top pick" in text, text
    assert "Here is what the pages I read say" in text, text
    assert "Gitzo Traveler Series 1 Kit -- £540" in text, text
    assert "around $599" in text, text
    assert text.rstrip().endswith("left out any figure or model the pages I read didn't state."), text
    # The pages' lines were in front of the model on the round after the search.
    assert "Gitzo Traveler Series 1 Kit -- £540" in prompts[1], prompts[1][-1500:]


def test_a_reply_the_pages_back_is_left_as_it_is(tmp_path) -> None:
    reply = "The Peak Design Travel Tripod (Carbon) is the top pick, and the Gitzo Traveler Series 1 Kit is the premium one."
    text, _prompts = _row12(tmp_path, reply)

    assert text.strip() == reply, text
