"""The clock's own answer is runtime code's, whatever the turn's judges picked.

iMac gate22 on 1e85219dc (qwen3:8b, Web lookups off): "what time is it in Lima right now?" was judged a web ask, the
date and time lane answered from the clock, and publication replaced that answer with "Web lookups are off, so I can't
look this up online": the judges' own model calls were read as a model having written the answer, so the answer was
held to sources a web lookup should have found. Served daemon, a stand-in model, no network.
"""
from __future__ import annotations

import json
from typing import Any

import pytest


def test_a_judged_web_pick_does_not_replace_the_clock(tmp_path) -> None:
    from tests._blackbox_served_rig import REPO_ROOT, SEED_MANIFEST, ScriptedProvider, ServedDaemon, run_in_home

    model, session = "qwen3-stub:2b", "clock-web-pick"
    ask = "what's the current time in Nairobi?"

    def reply_fn(body: dict[str, Any]) -> Any:
        messages = [m for m in body.get("messages") or [] if isinstance(m, dict)]
        prompt = " ".join(str(m.get("content") or "") for m in messages)
        if "which of these tools does the message ask" in prompt:
            return json.dumps({"answer": [14]})   # the web, as qwen3:8b picked it on the iMac
        if "need the INTERNET" in prompt:
            return json.dumps({"answer": "yes"})
        return json.dumps({"answer": "no"}) if '{"answer"' in prompt else "It is noon."

    home = tmp_path / "home"
    provider = ScriptedProvider({model: "It is noon."}, reply_fn=reply_fn)
    daemon = ServedDaemon(home, env_extra={
        "OLLAMA_HOST": provider.base_url, "VOOL_OLLAMA_URL": provider.base_url,
        "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat", "HOME": str(tmp_path / "owner-home"),
    })
    provider.__enter__()
    try:
        try:
            daemon.start(timeout=240)
        except Exception as exc:  # pragma: no cover - environment, not the guard
            pytest.skip(f"served daemon could not boot here: {exc}")
        seed = SEED_MANIFEST + '''
from tests._authorship_certification import certify_for_authorship
certify_for_authorship(manifest({model!r}))
'''
        run_in_home(home, seed.format(root=REPO_ROOT, base_url=provider.base_url, registered=[model], model=model))
        answered = daemon.chat(ask, session_id=session, model=model, mode="auto")
    finally:
        daemon.stop()
        provider.__exit__(None, None, None)

    said = str((answered.get("message") or {}).get("content") or "")
    assert "Web lookups are off" not in said and "look this up online" not in said, said
    assert "Nairobi" in said, said
