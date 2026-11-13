"""Served proof: one chat's memory listing never shows another chat's own facts.

The Intel package smoke on 271e90da read /api/memory/entries?session=release-pin-check and found "my favourite
colour is teal", said in another chat. That row is a profile fact, which every chat shares on purpose (cross-chat
recall). This pins the line on the real daemon: profile facts show in every chat, a chat's own facts only in it.
"""
from __future__ import annotations

import json
from urllib.parse import quote
from urllib.request import urlopen

from tests._blackbox_served_rig import ServedDaemon, run_in_home

SEED = """
from core.context_namespace import ensure_chat_namespace
from core.memory.entries import add_memory_fact
from core.web.api.runtime import stable_openclaw_session_id

a, b = (stable_openclaw_session_id(body={"session_id": name}, history=[], headers={}) for name in ("chat-a", "chat-b"))
for chat in (a, b):  # two chats the way the chat door opens them: each may read the owner's profile facts
    ensure_chat_namespace(chat, grant_confirmed_profile=True)
assert add_memory_fact("my bike is parked by the blue mat", session_id=a, scope="chat")
assert add_memory_fact("my favourite colour is teal", session_id=a, scope="user_profile")
"""


def _facts(daemon: ServedDaemon, session: str) -> list[dict]:
    with urlopen(f"{daemon.base_url}/api/memory/entries?limit=100&session={quote(session)}", timeout=60) as response:
        return list(json.loads(response.read().decode()).get("entries") or [])


def test_a_chats_own_facts_stay_in_that_chat_and_profile_facts_are_shared(tmp_path):
    home = tmp_path / "home"
    run_in_home(home, SEED)
    with ServedDaemon(home) as daemon:
        other = _facts(daemon, "chat-b")
        own = _facts(daemon, "chat-a")
    other_text = " ".join(str(e.get("fact") or "") for e in other)
    own_text = " ".join(str(e.get("fact") or "") for e in own)
    assert "blue mat" not in other_text, other
    assert "blue mat" in own_text, own
    # a profile fact is the cross-chat recall the product promises: it shows in every chat
    assert "teal" in other_text and "teal" in own_text, (other, own)
    assert all(str(e.get("scope")) == "user_profile" for e in other), other
