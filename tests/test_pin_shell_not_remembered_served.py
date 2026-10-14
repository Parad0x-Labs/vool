"""Served proof: a PIN said with "please remember it" leaves no shell of itself in memory.

Measured on the v0.7.1 Mac smoke (real model): "My card PIN is 4821, please remember it." answered with the one
"I didn't save your PIN" notice, and /api/memory/entries then listed "My card PIN is [redacted]". The masked
sentence still read as a statement about the user, so a background writer kept it. A statement that is only a
secret once masked is nothing to remember.
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.request import urlopen

from tests._blackbox_served_rig import ServedDaemon, run_in_home
from tests._served_sufficiency_provider import SEED
from tests.test_private_chat_served_workflow import MODEL, _certify, _Provider, _stream

REPO = Path(__file__).resolve().parents[1]


def _facts(daemon: ServedDaemon) -> list[str]:
    with urlopen(f"{daemon.base_url}/api/memory/entries?limit=100", timeout=60) as response:
        return [str(e.get("fact") or "") for e in json.loads(response.read().decode()).get("entries") or []]


def test_a_pin_to_remember_is_not_remembered_even_masked(tmp_path):
    home = tmp_path / "home"
    provider = _Provider({MODEL: "Understood."})
    with provider:
        run_in_home(home, SEED.format(root=REPO, base_url=provider.base_url, models=[(MODEL, {})]))
        with ServedDaemon(home, env_extra={"VOOL_MODEL_LOAD_FLOOR_GB": "0"}) as daemon:
            assert _certify(daemon).get("state") == "verified"
            chat = "openclaw:" + "4b" * 10

            answer, _ = _stream(daemon, "My card PIN is 4821, please remember it.", chat)
            assert "didn't save your PIN" in answer, answer
            assert answer.count("didn't save your PIN") == 1 and "Locked in" not in answer, answer
            # Something else said in the same chat is still remembered as before.
            kept, _ = _stream(daemon, "Remember that my bank is Northwind Credit Union.", chat)
            assert "Locked in" in kept, kept

            facts = _facts(daemon)
            assert not any("PIN" in f or "[redacted" in f or "4821" in f for f in facts), facts
            assert any("Northwind" in f for f in facts), facts
