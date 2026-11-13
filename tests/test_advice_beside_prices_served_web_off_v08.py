"""Served: advice beside today's prices with Web lookups off (Mac answer-quality row 10 on d5d044b0).

The reply keeps the advice, carries no price the turn never looked up, and ends by saying the prices need Web
lookups and where to turn them on. Measured on the Mac (real qwen3:8b, loop off and on): the advice came back
with "you can expect to pay between $100 and $150" and no word about Web lookups.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from tests._blackbox_served_rig import REPO_ROOT, SEED_MANIFEST, ScriptedProvider, ServedDaemon, run_in_home

MODEL = "qwen3-stub:2b"
ASK = "suggest a good tripod for travel and tell me what it costs today"
REPLY = ("I can't access real-time pricing, but the Peak Design Travel Tripod is a great choice for travel: light, "
         "compact and quick to set up. On average, you can expect to pay between $100 and $150 for this model.")
PLAIN_LINE = "Current prices need Web lookups, which are off"

_SEED = SEED_MANIFEST + '''
from tests._authorship_certification import certify_for_authorship
certify_for_authorship(manifest({model!r}))
'''


def _text(payload: dict[str, Any]) -> str:
    commit = payload.get("vool_response_commit") if isinstance(payload.get("vool_response_commit"), dict) else {}
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    return str(commit.get("canonical_content") or message.get("content") or "")


def _start(tmp_path: Path, *, web_on: bool):
    from core.entity_ambiguity import AMBIGUITY_SYSTEM_PROMPT

    def reply_fn(body: dict[str, Any]) -> Any:
        prompt = " ".join(str(m.get("content") or "") for m in body.get("messages") or [] if isinstance(m, dict))
        if AMBIGUITY_SYSTEM_PROMPT in prompt:
            return json.dumps({"ambiguous": False, "referents": [], "clarification": ""})
        return REPLY

    home = tmp_path / "home"
    if web_on:
        (home / "config").mkdir(parents=True, exist_ok=True)
        (home / "config" / "default_policy.yaml").write_text("system:\n  allow_web_fallback: true\n", encoding="utf-8")
    provider = ScriptedProvider({MODEL: REPLY}, reply_fn=reply_fn)
    daemon = ServedDaemon(home, env_extra={
        "OLLAMA_HOST": provider.base_url, "VOOL_OLLAMA_URL": provider.base_url,
        "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat", "HOME": str(tmp_path / "owner-home"),
    })
    provider.__enter__()
    try:
        daemon.start(timeout=240)
    except Exception as exc:  # pragma: no cover - environment, not the reply
        provider.__exit__(None, None, None)
        pytest.skip(f"served daemon could not boot here: {exc}")
    run_in_home(home, _SEED.format(root=REPO_ROOT, base_url=provider.base_url, registered=[MODEL], model=MODEL))
    provider.reset()
    return provider, daemon


@pytest.fixture
def served(tmp_path: Path):
    provider, daemon = _start(tmp_path, web_on=False)
    yield provider, daemon
    daemon.stop()
    provider.__exit__(None, None, None)


@pytest.fixture
def served_web_on(tmp_path: Path):
    provider, daemon = _start(tmp_path, web_on=True)
    yield provider, daemon
    daemon.stop()
    provider.__exit__(None, None, None)


@pytest.mark.parametrize("lane", ["buffered", "streamed"])
def test_the_advice_stays_and_the_prices_are_named_as_needing_web_lookups(served, lane):
    provider, daemon = served
    chat = daemon.chat if lane == "buffered" else daemon.chat_stream
    text = _text(chat(ASK, session_id=f"served-prices-{lane}", model=MODEL, mode="auto"))

    assert "Peak Design Travel Tripod" in text, text
    assert not re.search(r"[$£€]\s?\d", text), text
    assert PLAIN_LINE in text and "Settings, Privacy & Permissions" in text, text
    assert "Web lookups are off, so I can't look this up online" not in text, text


def test_with_web_lookups_on_the_model_is_handed_a_web_search_for_the_prices(served_web_on):
    """Mac row 12 on d5d044b0, Web lookups on: no lookup ran and every price came back "(withheld: not supported)".
    The model must at least be offered the search that would back them."""
    provider, daemon = served_web_on
    daemon.chat(ASK, session_id="served-prices-web-on", model=MODEL, mode="auto")
    offered = {str(name) for call in provider.calls for name in (call.get("tools") or [])}
    assert any(name.startswith("web") for name in offered), (offered, [c["prompt"][:120] for c in provider.calls])
