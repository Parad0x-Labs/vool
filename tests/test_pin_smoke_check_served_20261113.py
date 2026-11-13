"""Served proof: the release smoke's PIN check judges the real app, not the listing's first-turn gap.

Measured on the Intel smoke of d5d044b0: the PIN turn answered with the one "I didn't save your PIN" notice,
yet the check reported pin_in_memory=true. The one "added" row was "my favourite colour is teal.", a profile
fact written 11 minutes earlier by another chat. A session's memory listing shows the shared profile facts only
once the chat exists, so the listing read before the PIN chat's first turn was empty and every shared fact
looked new. Nothing of the PIN was stored. This drives the smoke's own check against the served app with a
shared profile fact already in memory, and also sweeps every file the app wrote for the PIN as said.
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.request import urlopen

from ops import macos_bundle_smoke as smoke
from tests._blackbox_served_rig import ServedDaemon, run_in_home
from tests._served_sufficiency_provider import SEED
from tests.test_private_chat_served_workflow import MODEL, _certify, _Provider, _stream

REPO = Path(__file__).resolve().parents[1]


def _entries(daemon: ServedDaemon, query: str) -> list[dict]:
    with urlopen(f"{daemon.base_url}/api/memory/entries?{query}", timeout=60) as response:
        return json.loads(response.read().decode()).get("entries") or []


def test_smoke_pin_check_passes_on_the_served_app_with_a_shared_profile_fact(tmp_path):
    home = tmp_path / "home"
    provider = _Provider({MODEL: "Understood."})
    with provider:
        run_in_home(home, SEED.format(root=REPO, base_url=provider.base_url, models=[(MODEL, {})]))
        with ServedDaemon(home, env_extra={"VOOL_MODEL_LOAD_FLOOR_GB": "0"}) as daemon:
            assert _certify(daemon).get("state") == "verified"
            kept, _ = _stream(daemon, "Remember that my favourite colour is teal.", "release-forget-digit-check")
            assert "Locked in" in kept, kept
            shared = [e for e in _entries(daemon, "limit=200") if "teal" in str(e.get("fact"))]
            assert shared and shared[0].get("scope") == "user_profile", shared

            # The listing gap the old check fell into: a chat with no turn yet lists nothing, and after its
            # first turn it lists the shared fact.
            assert _entries(daemon, f"session={smoke.PIN_SESSION}") == []

            scratch = tmp_path / "scratch"
            scratch.mkdir()
            ok, detail = smoke.pin_check(smoke.Run(scratch), daemon.base_url, home, MODEL, 600)

            after = _entries(daemon, f"session={smoke.PIN_SESSION}&limit=200")
            assert [e["record_id"] for e in after] == [e["record_id"] for e in shared], after
            assert ok, detail
            assert detail["added"] == [] and detail["files_holding_pin"] == [], detail
            assert "didn't save your PIN" in detail["reply"], detail


SECRETS = (
    ("My card PIN is 4821, please remember it.", "PIN is 4821"),
    ("My card number is 4111 1111 1111 1111, keep it for later.", "4111 1111 1111 1111"),
    ("My email password is Zebra-Lantern-77, remember it please.", "Zebra-Lantern-77"),
)


def test_no_file_the_app_writes_holds_a_refused_secret(tmp_path):
    """Every store, at the write: transcript, memory, dialogue state, topic archive, recall index, logs."""
    home = tmp_path / "home"
    provider = _Provider({MODEL: "Understood."})
    with provider:
        run_in_home(home, SEED.format(root=REPO, base_url=provider.base_url, models=[(MODEL, {})]))
        with ServedDaemon(home, env_extra={"VOOL_MODEL_LOAD_FLOOR_GB": "0"}) as daemon:
            assert _certify(daemon).get("state") == "verified"
            for n, (said, _) in enumerate(SECRETS):
                _stream(daemon, said, f"openclaw:{n}" + "5c" * 9)
            # A new topic closes the old one into the topic archive, which keeps the old goal.
            _stream(daemon, "What is a good name for a grey cat?", "openclaw:0" + "5c" * 9)
            held = {value: smoke.files_holding(home, value) for _, value in SECRETS}
            assert held == {value: [] for _, value in SECRETS}, held
