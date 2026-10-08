"""An ordinary one-off request ("read config.json for me", "poem, winter harbour, short") is not a standing rule.

The plain-rule law (no "always", no "from now on") saved such requests and carried them into every later prompt of the
workspace as "Standing instructions the user gave in earlier chats". Two readings were wrong:

* a file name or code identifier was read as a format word -- "json" in "config.json" -- and an order whose verb or
  object is a file or identifier ("read config.json", "parse_header source") works on that one thing;
* in a request written as keywords, a format word after a comma ("…, short") describes the thing asked for; a rule
  names its format in the order itself ("Use metric units", "Write dates as day, month, year").

Questions were never affected. Genuine rules, said plainly or with standing words, are still saved.
"""
from __future__ import annotations

import pytest

from core import standing_instructions as si

OWNER = {"surface": "chat", "_owner_local": True, "workspace_root": "/srv/projects/harbour"}


@pytest.fixture(autouse=True)
def _store(tmp_path, monkeypatch):
    monkeypatch.setattr(si, "_store_path", lambda: tmp_path / "standing_instructions.json")


def _say(text):
    return si.observe_turn(text, OWNER, principal="owner_local", session_id="chat:" + "c" * 20)


ONE_OFF_REQUESTS = [
    # Seen in the replay corpus (regression).
    "read config.json for me",
    "cat config.json",
    "read conifg.json",
    "parse_header source",
    "poem, winter harbour, short",
    # Independently written.
    "open notes.md",
    "diff old_report.csv against the new one",
    "render build_sources",
    "haiku, autumn rain, brief",
    "limerick about a cat, in french",
    "story, lighthouse keeper, detailed",
    "show me settings.yaml in markdown",
    "read setup.py, answer in bullet points",
    "joke, short",
]


@pytest.mark.parametrize("text", ONE_OFF_REQUESTS)
def test_a_one_off_request_saves_nothing(text):
    assert _say(text)["saved"] == []


def test_a_one_off_request_never_reaches_a_later_prompt():
    for text in ONE_OFF_REQUESTS:
        _say(text)
    assert si.standing_block(OWNER, principal="owner_local") == ""


@pytest.mark.parametrize("text", [
    "Always answer in British English.",
    "Answer in British English.",
    "Use metric units, not imperial.",
    "Write dates as day, month, year.",
    "Give me answers in JSON.",
    "Reply in short sentences.",
    "Always read README.md before you change a file.",
    # A name or greeting before the comma, and a file name or identifier that is not the order's object.
    "Sam, answer in bullet points.",
    "Thanks, keep answers short.",
    "Answer in en_US English.",
    "Answer in JSON, like config.json.",
])
def test_a_genuine_rule_is_still_saved_and_reaches_a_later_prompt(text):
    assert _say(text)["saved"] == [text]
    assert si.standing_block(OWNER, principal="owner_local").splitlines()[1:] == [f"- {text}"]


def test_a_long_hostile_turn_is_read_in_linear_time():
    import time

    for text in ("a-" * 50_000, "state-of-the-art-" * 6_000, "x_" * 50_000, "poem, " * 20_000):
        started = time.perf_counter()
        si.read_turn(text)
        assert time.perf_counter() - started < 2.0, text[:20]
