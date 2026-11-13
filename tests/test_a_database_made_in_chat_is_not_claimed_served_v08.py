"""Served: a database the model says it made in chat, when nothing was made.

Mac recheck 2026-11-13 on f50e52eb (real qwen3:8b, clean loop on): "make a database called freezer ..." came back
"I've created a new database called 'freezer' with a table named 'items'... I've added the following rows", with no
tool selected, none run, and no database file. Chat on the Database is read and preview only in 0.8, so the reply
says that, in the same words the Database skill uses: "I can't create or change a database from chat. Nothing was
made." It names no screen, because there is no create-a-database screen.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests._blackbox_served_rig import REPO_ROOT, SEED_MANIFEST, ScriptedProvider, ServedDaemon, run_in_home

MODEL = "qwen3-stub:2b"
ASK = "make me a database called freezer with name and expiry columns, and add frozen peas expiring 2026-12-01"
MADE = (
    "I've created a new database called 'freezer' with a table named 'items'. I've added the following rows:\n"
    "1. frozen peas, expiry 2026-12-01"
)
LINE = "I can't create or change a database from chat. Nothing was made."

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

    def reply_fn(body: dict[str, Any]) -> Any:
        prompt = " ".join(str(m.get("content") or "") for m in body.get("messages") or [] if isinstance(m, dict))
        if AMBIGUITY_SYSTEM_PROMPT in prompt:
            return json.dumps({"ambiguous": False, "referents": [], "clarification": ""})
        return MADE

    home = tmp_path / "home"
    provider = ScriptedProvider({MODEL: MADE}, reply_fn=reply_fn)
    daemon = ServedDaemon(home, env_extra={
        "OLLAMA_HOST": provider.base_url, "VOOL_OLLAMA_URL": provider.base_url,
        "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat", "HOME": str(tmp_path / "owner-home"),
    })
    provider.__enter__()
    try:
        daemon.start(timeout=240)
    except Exception as exc:  # pragma: no cover - environment, not the gate
        provider.__exit__(None, None, None)
        pytest.skip(f"served daemon could not boot here: {exc}")
    run_in_home(home, _SEED.format(root=REPO_ROOT, base_url=provider.base_url, registered=[MODEL], model=MODEL))
    provider.reset()
    yield home, provider, daemon
    daemon.stop()
    provider.__exit__(None, None, None)


@pytest.mark.parametrize("lane", ["buffered", "streamed"])
def test_a_database_the_model_says_it_made_is_corrected(served, lane: str) -> None:
    home, provider, daemon = served
    chat = daemon.chat if lane == "buffered" else daemon.chat_stream
    text = _text(chat(ASK, session_id=f"served-db-made-{lane}", model=MODEL, mode="auto"))

    assert provider.calls, "the model was never asked"
    assert "I've created a new database" not in text, text
    assert "I've added the following rows" not in text, text
    assert "frozen peas" not in text, text
    assert LINE in text, text
    assert "Settings" not in text, text
    assert not list((home / "data").glob("**/freezer*")), text
