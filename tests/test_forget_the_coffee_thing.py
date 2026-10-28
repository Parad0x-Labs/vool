""""Forget the coffee thing would you" forgets the coffee memory.

Measured 2026-10-28 on qwen3:8b, the PC and a phone alike: after "remember that i take my coffee with no sugar",
the turn "forget the coffee thing would you" answered 'Nothing was removed. Forget found no memory in this chat that
matched "the coffee thing would you"' and the fact stayed stored. The way the person asked ("would you") and the
topic word ("thing") were searched as if they were stored words.

Served over /api/chat on one isolated daemon, read back through /api/memory/entries. Authored wording only.
"""
from __future__ import annotations

from pathlib import Path
from urllib.request import urlopen

import pytest

from core.persistent_memory import _normalize_forget_target
from tests._blackbox_served_rig import ServedDaemon, run_in_home
from tests._served_sufficiency_provider import SEED
from tests.test_private_chat_served_workflow import _certify, _Provider, _stream

REPO = Path(__file__).resolve().parents[1]
MODEL = "stub-private:4b"


@pytest.mark.parametrize(
    ("said", "target"),
    [
        ("the coffee thing would you", "coffee"),
        ("that gym stuff, could you", "gym"),
        ("about the coffee thing", "coffee"),
        ("my coffee thing for me", "coffee"),
        ("the old wifi password thing ok", "wifi password"),
        ("the tea thing please", "tea"),
        ("my sister's birthday thing can you", "sister's birthday"),
    ],
)
def test_the_way_it_was_asked_is_not_part_of_the_target(said, target):
    assert _normalize_forget_target(said) == target


@pytest.mark.parametrize(
    "said",
    [
        # No topic before "thing": nothing to narrow to.
        "the thing",
        # A target that names no topic-thing keeps its own words.
        "Ilona's favourite tree",
        "my Thanks Giving plans",
    ],
)
def test_other_targets_keep_their_words(said):
    assert _normalize_forget_target(said) == said


def _entries(daemon: ServedDaemon, session: str) -> str:
    with urlopen(f"{daemon.base_url}/api/memory/entries?session={session}", timeout=60) as response:
        return response.read().decode().lower()


def test_forget_the_coffee_thing_removes_the_coffee_fact_and_nothing_else(tmp_path):
    home = tmp_path / "home"
    provider = _Provider({MODEL: "Noted."})
    with provider:
        run_in_home(home, SEED.format(root=REPO, base_url=provider.base_url, models=[(MODEL, {})]))
        with ServedDaemon(home, env_extra={"VOOL_MODEL_LOAD_FLOOR_GB": "0"}) as daemon:
            assert _certify(daemon).get("state") == "verified"
            chat = "openclaw:" + "c0" * 10

            said, _ = _stream(daemon, "remember that i take my coffee with no sugar", chat)
            assert "remember" in said.lower(), said
            said, _ = _stream(daemon, "remember that my bike lock code is the green dial", chat)
            assert "remember" in said.lower(), said
            stored = _entries(daemon, chat)
            assert "no sugar" in stored and "green dial" in stored

            forgot, _ = _stream(daemon, "forget the coffee thing would you", chat)
            assert forgot.startswith("Forget applied."), forgot
            left = _entries(daemon, chat)
            assert "no sugar" not in left
            assert "green dial" in left, "forgetting one fact must leave the other in place"
