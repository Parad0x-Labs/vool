""""Forget what I told you about X" forgets X.

Measured on the real Intel smoke of the v0.7.1 combined PR (i7-8700B, ef99b3e and set A alike): after
"Remember that my sister Ilona's favourite tree is the silver birch.", the turn "Forget what I told you
about Ilona's favourite tree." answered 'Nothing was removed. Forget found no memory in this chat that
matched "what I told you about Ilona's favourite tree"' and the fact stayed stored. The lead-in was
searched as if it were part of the stored words.

Served over /api/chat on one isolated daemon, read back through /api/memory/entries, the way the
smoke and a person check it. Authored wording only.
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.request import urlopen

import pytest

from core.persistent_memory import _normalize_forget_target
from tests._blackbox_served_rig import ServedDaemon, run_in_home
from tests._served_sufficiency_provider import SEED
from tests.test_private_chat_served_workflow import _certify, _Provider, _stream

REPO = Path(__file__).resolve().parents[1]
MODEL = "stub-private:4b"


def _entries(daemon: ServedDaemon, session: str) -> str:
    with urlopen(f"{daemon.base_url}/api/memory/entries?session={session}", timeout=60) as response:
        return response.read().decode().lower()


@pytest.mark.parametrize(
    ("said", "target"),
    [
        ("what I told you about Ilona's favourite tree", "Ilona's favourite tree"),
        ("everything I told you about the allotment gate", "the allotment gate"),
        ("what I said about my cousin's ferry", "my cousin's ferry"),
        ("what I shared with you about the attic boiler", "the attic boiler"),
        ("anything I've told you about Marek's bakery", "Marek's bakery"),
    ],
)
def test_the_lead_in_is_not_part_of_the_target(said, target):
    assert _normalize_forget_target(said) == target


@pytest.mark.parametrize(
    "said",
    [
        # Only a leading lead-in goes; the same words inside a target stay.
        "the note on what I told you about the boiler",
        "Ilona's favourite tree",
        # A lead-in with nothing after it names nothing new.
        "what I told you about",
    ],
)
def test_words_inside_a_target_are_kept(said):
    assert _normalize_forget_target(said) == said


def test_forget_what_i_told_you_about_removes_the_fact_and_nothing_else(tmp_path):
    home = tmp_path / "home"
    provider = _Provider({MODEL: "Noted."})
    with provider:
        run_in_home(home, SEED.format(root=REPO, base_url=provider.base_url, models=[(MODEL, {})]))
        with ServedDaemon(home, env_extra={"VOOL_MODEL_LOAD_FLOOR_GB": "0"}) as daemon:
            assert _certify(daemon).get("state") == "verified"
            chat = "openclaw:" + "4e" * 10

            said, _ = _stream(daemon, "Remember that my sister Ilona's favourite tree is the silver birch.", chat)
            assert "remember" in said.lower(), said
            said, _ = _stream(daemon, "Remember that the allotment gate code is the blue padlock.", chat)
            assert "remember" in said.lower(), said
            stored = _entries(daemon, chat)
            assert "birch" in stored and "blue padlock" in stored

            forgot, _ = _stream(daemon, "Forget what I told you about Ilona's favourite tree.", chat)
            assert forgot.startswith("Forget applied."), forgot
            left = _entries(daemon, chat)
            assert "birch" not in left
            assert "blue padlock" in left, "forgetting one fact must leave the other in place"

            # Nothing stored matches: still the plain truth, nothing claimed as forgotten.
            missed, _ = _stream(daemon, "Forget everything I told you about my neighbour's sailing boat.", chat)
            assert missed.startswith("Nothing was removed."), missed
            assert "neighbour's sailing boat" in missed and "told you" not in missed, missed
            assert "blue padlock" in _entries(daemon, chat)
