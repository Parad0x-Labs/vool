"""Private chat and the clause splitter read every apostrophe mark as one (2026-10-20).

Measured on 7f7f0c7, on v0.7.2 (29945fd) and on v0.7.1 (baf115c) alike:

- "Don´t record this." with an acute accent, prime, reversed or fullwidth apostrophe was no private-chat command
  and not even a privacy request, so the chat was recorded and the model was told nothing: private chat folded a
  smaller apostrophe set than memory admission.
- turn_slices raised KeyError on a curly apostrophe after a non-letter ("’Cause I asked, ...", "(’90s music)"):
  the splitter opened a quote for a mark that has no closing pair.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.agent_runtime.answer_coverage import turn_slices
from core.private_chat import ON, parse_private_command
from tests._blackbox_served_rig import ServedDaemon, run_in_home
from tests._served_sufficiency_provider import SEED
from tests.test_private_chat_served_workflow import MODEL, REPO, _certify, _files_holding, _history, _Provider, _stream

# Straight, curly left/right, reversed, modifier letter, prime, acute accent, fullwidth.
MARKS = pytest.mark.parametrize("mark", ["'", "‘", "’", "‛", "ʼ", "′", "´", "＇"],
                                ids=lambda mark: f"U+{ord(mark):04X}")
# Authored marker for the private words, built so the source never holds it: the daemon's preflight keeps a copy of
# the repo's test sources under its home.
SECRET = "Thorn" + "vale"


@MARKS
def test_asking_not_to_be_recorded_turns_private_chat_on_whatever_apostrophe_is_typed(mark):
    for text in (f"Don{mark}t record this.", f"I don{mark}t want this saved"):
        command = parse_private_command(text)
        assert command is not None and command.action == ON and command.bare, (text, command)


@MARKS
@pytest.mark.parametrize("text", [
    "{m}Cause I asked, what is the weather in Vilnius?",
    "What is up? ({m}90s music) What is the weather in Vilnius?",
    "Hi. {m}tis the season. What is the weather in Vilnius?",
], ids=["cause", "nineties", "tis"])
def test_a_message_with_an_apostrophe_after_a_non_letter_is_split_into_its_requests(mark, text):
    slices = [item.text for item in turn_slices(text.format(m=mark))]

    assert any("weather in Vilnius" in piece for piece in slices), slices


def test_a_quote_opened_by_a_quote_mark_still_holds_its_boundary():
    # A real quote keeps "Stop. Go." as one quoted span; only marks without a closing pair stopped opening one.
    slices = [item.text for item in turn_slices("Translate ‘Stop. Go.’ into Lithuanian.")]

    assert slices == ["Translate ‘Stop. Go.’ into Lithuanian."], slices


def test_served_dont_record_with_an_acute_apostrophe_keeps_the_chat_private(tmp_path):
    home = tmp_path / "home"
    provider = _Provider({MODEL: "Sounds lovely."})
    with provider:
        run_in_home(home, SEED.format(root=REPO, base_url=provider.base_url, models=[(MODEL, {})]))
        with ServedDaemon(home, env_extra={"VOOL_MODEL_LOAD_FLOOR_GB": "0"}) as daemon:
            assert _certify(daemon).get("state") == "verified"
            chat = "openclaw:" + "7d" * 10

            reply, _ = _stream(daemon, "’Cause I asked, what is the weather like for a picnic?", chat)
            assert reply.strip(), "a turn with a curly apostrophe after a non-letter got no answer"

            switched, on_commit = _stream(daemon, "Don´t record this.", chat)
            assert "Private chat is on" in switched, switched
            assert on_commit.get("display_metadata", {}).get("private_chat_on") is True

            _stream(daemon, f"The surprise party is at {SECRET} Hall on Friday.", chat)
            assert SECRET not in json.dumps(_history(daemon, chat))

        assert _files_holding(Path(home), SECRET) == []
