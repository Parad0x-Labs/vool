"""Served proof: forgetting a number that is also in today's date forgets only what the user said.

Measured on the v0.7.1 Mac smoke (real model, 2026-10-14): "Remember that my favourite colour is teal.",
"Remember that my locker is 14.", "Forget 14." answered "Forget could not be verified as fully applied ...", and
afterwards the memory list was empty and teal was no longer recalled. The runtime stamps the day on what it stores,
and the forget matched "14" inside those stamps. The forgotten number here is always today's day of the month, so
this covers whatever date it runs on.
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


def _entries(daemon: ServedDaemon) -> list[dict[str, Any]]:
    with urlopen(f"{daemon.base_url}/api/memory/entries?limit=100", timeout=60) as response:
        return list(json.loads(response.read().decode()).get("entries") or [])


def test_forgetting_todays_day_number_keeps_every_other_memory(tmp_path):
    home = tmp_path / "home"
    provider = _Provider({MODEL: "Got it."})
    with provider:
        run_in_home(home, SEED.format(root=REPO, base_url=provider.base_url, models=[(MODEL, {})]))
        with ServedDaemon(home, env_extra={"VOOL_MODEL_LOAD_FLOOR_GB": "0"}) as daemon:
            assert _certify(daemon).get("state") == "verified"
            chat = "openclaw:" + "5e" * 10
            locker = datetime.now().strftime("%d")  # "04", "14": the day exactly as every stamp written today has it

            kept, _ = _stream(daemon, "Remember that my favourite colour is teal.", chat)
            assert "Locked in" in kept, kept
            saved, _ = _stream(daemon, f"Remember that my locker is {locker}.", chat)
            assert "Locked in" in saved, saved
            facts = [str(e.get("fact") or "") for e in _entries(daemon)]
            assert any("teal" in f for f in facts) and any(locker in f for f in facts), facts

            forgot, _ = _stream(daemon, f"Forget {locker}.", chat)
            assert "could not be verified" not in forgot and "retry" not in forgot.lower(), forgot

            facts = [str(e.get("fact") or "") for e in _entries(daemon)]
            assert any("teal" in f for f in facts), f"the unrelated memory was dropped: {facts}"
            assert not any(f"locker is {locker}" in f for f in facts), facts

            recalled, _ = _stream(daemon, "What is my favourite colour?", chat)
            sent = "\n".join(f"{c.get('system', '')}\n{c.get('full_prompt', '')}" for c in provider.calls[-3:])
            assert "teal" in recalled.lower() or "teal" in sent, (recalled, sent[-2000:])
            assert f"locker is {locker}" not in sent


def test_forgetting_a_number_equal_to_a_stored_page_size_keeps_every_other_memory(tmp_path):
    """Measured on the 0.7.1 Mac smoke: "Forget 14." still failed to verify when a stored turn page happened to
    be 114 bytes long. The forgotten number here is exactly the byte size of the turn page that holds the
    other memory, so the bookkeeping number and the user's number always collide."""
    import sqlite3

    home = tmp_path / "home"
    provider = _Provider({MODEL: "Got it."})
    with provider:
        run_in_home(home, SEED.format(root=REPO, base_url=provider.base_url, models=[(MODEL, {})]))
        with ServedDaemon(home, env_extra={"VOOL_MODEL_LOAD_FLOOR_GB": "0"}) as daemon:
            assert _certify(daemon).get("state") == "verified"
            chat = "openclaw:" + "6f" * 10

            kept, _ = _stream(daemon, "Remember that my favourite colour is teal.", chat)
            assert "Locked in" in kept, kept
            conn = sqlite3.connect(home / "data" / "vool_web0_v2.db")
            try:
                sizes = [int(size) for (size,) in conn.execute(
                    "SELECT bytes FROM context_pages WHERE session_id = ? AND erased = 0", (chat,))]
            finally:
                conn.close()
            assert sizes, "the turn page this test is about was not stored"
            number = str(max(sizes))

            saved, _ = _stream(daemon, f"Remember that my locker is {number}.", chat)
            assert "Locked in" in saved, saved
            forgot, _ = _stream(daemon, f"Forget {number}.", chat)
            assert "could not be verified" not in forgot and "retry" not in forgot.lower(), forgot

            facts = [str(e.get("fact") or "") for e in _entries(daemon)]
            assert any("teal" in f for f in facts), f"the unrelated memory was dropped: {facts}"
            assert not any(f"locker is {number}" in f for f in facts), facts
