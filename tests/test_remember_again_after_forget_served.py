"""Served proof: saying a forgotten fact again is answered, never a failed turn.

Measured on the 0.7.1 Mac smoke (2026-10-14, kept profile): "Remember that my locker is 14." in a chat where "14" had
been forgotten earlier ended the turn with HTTP 500 ("memory write carries revoked content"). The forget law keeps
holding in that chat: the fact is not stored there, and the reply says so. Another chat may remember it again. And
any other fact said in that chat is still remembered, even when the day's date holds the forgotten number (the
write fence matched "14" in the timestamps of the write's own access grants, so on the 14th every later
"Remember ..." in that chat was refused).
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen

from tests._blackbox_served_rig import ServedDaemon, run_in_home
from tests._served_sufficiency_provider import SEED
from tests.test_private_chat_served_workflow import MODEL, _certify, _Provider, _stream

REPO = Path(__file__).resolve().parents[1]
REFUSED = "You asked me to forget that in this chat, so I won't keep it here."


def _facts(daemon: ServedDaemon) -> list[str]:
    with urlopen(f"{daemon.base_url}/api/memory/entries?limit=100", timeout=60) as response:
        return [str(e.get("fact") or "") for e in json.loads(response.read().decode()).get("entries") or []]


def test_a_forgotten_fact_said_again_is_refused_calmly_here_and_remembered_in_another_chat(tmp_path):
    home = tmp_path / "home"
    provider = _Provider({MODEL: "Got it."})
    with provider:
        run_in_home(home, SEED.format(root=REPO, base_url=provider.base_url, models=[(MODEL, {})]))
        with ServedDaemon(home, env_extra={"VOOL_MODEL_LOAD_FLOOR_GB": "0"}) as daemon:
            assert _certify(daemon).get("state") == "verified"
            locker = datetime.now().strftime("%d")  # today's day: also inside every stamp the runtime writes today
            fact = f"Remember that my locker is {locker}."
            chat = "openclaw:" + "c3" * 10

            assert "Locked in" in _stream(daemon, fact, chat)[0]
            assert "Forget applied" in _stream(daemon, f"Forget {locker}.", chat)[0]

            again, commit = _stream(daemon, fact, chat)  # a 500 raises here
            assert again.startswith(REFUSED), again
            assert commit, "the turn did not complete"
            assert not any(f"locker is {locker}" in f for f in _facts(daemon))

            kept, _ = _stream(daemon, "Remember that my favourite colour is teal.", chat)
            assert "Locked in" in kept, kept
            assert any("teal" in f for f in _facts(daemon)), _facts(daemon)

            other = "openclaw:" + "d4" * 10
            assert "Locked in" in _stream(daemon, fact, other)[0]
            assert any(f"locker is {locker}" in f for f in _facts(daemon)), _facts(daemon)
