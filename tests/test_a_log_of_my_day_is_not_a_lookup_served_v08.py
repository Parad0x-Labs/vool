"""Served: keeping a log of the person's own day is not a lookup, and the tool catalogue is no source.

Mac answer-quality row 4 on d5d044b0 (real qwen3:8b, loop off and on, gentle-workout-plan installed): "log
today's session: 5k run, 28 minutes" ran operator.list_tools, and the reply was withheld as "I looked this up,
but what I found doesn't back the answer ... What the pages said: - Tool families on this runtime:".
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests._blackbox_served_rig import REPO_ROOT, SEED_MANIFEST, ScriptedProvider, ServedDaemon, run_in_home

MODEL = "qwen3-stub:2b"
ASK = "log today's session: 5k run, 28 minutes"
REPLY = "Logged today's session: a 5k run in 28 minutes."
LIST_TOOLS = {"id": "call-1", "type": "function", "function": {"name": "operator__list_tools", "arguments": {}}}

_SEED = SEED_MANIFEST + '''
from tests._authorship_certification import certify_for_authorship
certify_for_authorship(manifest({model!r}))
'''


def _text(payload: dict[str, Any]) -> str:
    commit = payload.get("vool_response_commit") if isinstance(payload.get("vool_response_commit"), dict) else {}
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    return str(commit.get("canonical_content") or message.get("content") or "")


@pytest.fixture
def served(tmp_path: Path):
    from core.entity_ambiguity import AMBIGUITY_SYSTEM_PROMPT

    rounds: list[int] = []

    def reply_fn(body: dict[str, Any]) -> Any:
        prompt = " ".join(str(m.get("content") or "") for m in body.get("messages") or [] if isinstance(m, dict))
        if AMBIGUITY_SYSTEM_PROMPT in prompt:
            return json.dumps({"ambiguous": False, "referents": [], "clarification": ""})
        if body.get("tools"):
            rounds.append(1)
            if len(rounds) == 1:
                return LIST_TOOLS
        return REPLY

    home = tmp_path / "home"
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
    yield provider, daemon
    daemon.stop()
    provider.__exit__(None, None, None)


def test_the_log_is_answered_and_no_tool_catalogue_reaches_the_reply(served):
    _provider, daemon = served
    text = _text(daemon.chat(ASK, session_id="served-log-my-day", model=MODEL, mode="auto"))
    assert "Tool families" not in text and "What the pages said" not in text, text
    assert "doesn't back the answer" not in text and "I didn't look anything up" not in text, text
    assert "5k run" in text, text
