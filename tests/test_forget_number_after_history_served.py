"""Served proof: "Forget <number>" keeps working on a profile that already has history.

Measured on the 0.7.1 Mac smoke (2026-10-14): on a fresh profile "Forget 14." passed, but after ten private turns
and in five later fresh chats on the same app, every round answered "Forget could not be verified as fully
applied: context pages, ..." and the user's other memory (teal) was no longer listed or recalled. Each round here
says the same words as the round before, in a new chat, after earlier rounds and private turns, exactly as the
smoke did. The forgotten number is today's day of the month, so it also sits inside every day stamp written today.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.request import urlopen

from tests._blackbox_served_rig import ServedDaemon, run_in_home
from tests._served_sufficiency_provider import SEED
from tests.test_private_chat_served_workflow import MODEL, _certify, _Provider, _stream

REPO = Path(__file__).resolve().parents[1]
ROUNDS = 4


def _entries(daemon: ServedDaemon) -> list[dict[str, Any]]:
    with urlopen(f"{daemon.base_url}/api/memory/entries?limit=100", timeout=60) as response:
        return list(json.loads(response.read().decode()).get("entries") or [])


def test_forgetting_a_number_again_and_again_on_a_profile_with_history(tmp_path):
    home = tmp_path / "home"
    provider = _Provider({MODEL: "Got it."})
    with provider:
        run_in_home(home, SEED.format(root=REPO, base_url=provider.base_url, models=[(MODEL, {})]))
        with ServedDaemon(home, env_extra={"VOOL_MODEL_LOAD_FLOOR_GB": "0"}) as daemon:
            assert _certify(daemon).get("state") == "verified"
            locker = datetime.now().strftime("%d")

            # History first: ordinary turns, private turns, and forgets of other things.
            warm = "openclaw:" + "a1" * 10
            for text in ("Remember that my sister is called Odile.", "Remember that the gate code is 7731.",
                         "What do you know about me?", "Forget 7731.", "Forget Odile."):
                _stream(daemon, text, warm)
            private = "openclaw:" + "b2" * 10
            assert "Private chat is on" in _stream(daemon, "don't record this", private)[0]
            for turn in range(4):
                _stream(daemon, f"Private note {turn}: the parcel is at locker {locker}.", private)
            assert "Recording is back on" in _stream(daemon, "turn recording back on", private)[0]

            for round_number in range(ROUNDS):
                chat = "openclaw:" + f"{round_number:02d}" * 10
                kept, _ = _stream(daemon, "Remember that my favourite colour is teal.", chat)
                assert "Locked in" in kept, (round_number, kept)
                saved, _ = _stream(daemon, f"Remember that my locker is {locker}.", chat)
                assert "Locked in" in saved, (round_number, saved)

                forgot, _ = _stream(daemon, f"Forget {locker}.", chat)
                assert "could not be verified" not in forgot and "retry" not in forgot.lower(), (round_number, forgot)

                facts = [str(e.get("fact") or "") for e in _entries(daemon)]
                assert any("teal" in f for f in facts), (round_number, f"the unrelated memory was dropped: {facts}")
                assert not any(f"locker is {locker}" in f for f in facts), (round_number, facts)
                recalled, _ = _stream(daemon, "What is my favourite colour?", chat)
                sent = "\n".join(f"{c.get('system', '')}\n{c.get('full_prompt', '')}" for c in provider.calls[-3:])
                assert "teal" in recalled.lower() or "teal" in sent, (round_number, recalled, sent[-2000:])
                assert f"locker is {locker}" not in sent, round_number
