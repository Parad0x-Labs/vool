"""Brick 3 — user_address is the single authoritative identity fact.

Covers the live "what's my name -> Rick" failure: the deterministic recall answers from the
authoritative user_address, and the fact-admission gate never persists a name that contradicts it.
"""
from __future__ import annotations

from core.fact_extractor import ExtractedFact, _fact_grounded
from core.web.api.runtime import _private_memory_values


def _name_fact(name: str) -> ExtractedFact:
    return ExtractedFact(action="ADD", block="user_profile", content=f"Name: {name}")


class _FakeMemory:
    def __init__(self, blocks: dict[str, str]) -> None:
        self._blocks = blocks

    def block_read(self, name: str) -> str:
        return self._blocks.get(name, "")


# --- admission gate (_fact_grounded) -----------------------------------------------------------

def test_non_name_fact_passes(monkeypatch):
    monkeypatch.setattr("core.user_preferences.user_address", lambda: "")
    fact = ExtractedFact(action="ADD", block="preferences", content="Answer style: terse")
    assert _fact_grounded(fact, "") is True


def test_name_is_not_admitted_from_the_users_text_alone_when_no_authority(monkeypatch):
    # The Operator Profile is the ONE name authority: with no profile name a harvested "Name:"
    # line is refused even when the user typed it -- the stated name becomes a profile candidate
    # the user confirms, never a memory block written behind their back.
    monkeypatch.setattr("core.user_preferences.user_address", lambda: "")
    assert _fact_grounded(_name_fact("Loop"), "please call me loop") is False


def test_model_invented_name_rejected_when_no_authority(monkeypatch):
    monkeypatch.setattr("core.user_preferences.user_address", lambda: "")
    # "Rick" never appears in the user's own text -> not grounded (the pre-existing guard).
    assert _fact_grounded(_name_fact("Rick"), "what's the weather like") is False


def test_name_conflicting_with_user_address_is_rejected(monkeypatch):
    monkeypatch.setattr("core.user_preferences.user_address", lambda: "LOOP")
    # Even if "Rick" appears in the user's own text, the authoritative address wins.
    assert _fact_grounded(_name_fact("Rick"), "my friend Rick says hi") is False


def test_name_matching_user_address_is_admitted(monkeypatch):
    monkeypatch.setattr("core.user_preferences.user_address", lambda: "LOOP")
    assert _fact_grounded(_name_fact("loop"), "") is True  # case-insensitive match


# --- resolver prefers user_address (_private_memory_values) -------------------------------------

def test_resolver_prefers_user_address_over_stored_name(monkeypatch):
    monkeypatch.setattr("core.user_preferences.user_address", lambda: "LOOP")
    memory = _FakeMemory({"user_profile": "Name: Rick"})
    values = _private_memory_values(
        memory,
        include_profile=True,
        include_project=False,
    )
    assert values["name"] == "LOOP"


def test_resolver_never_falls_back_to_a_stored_block_name(monkeypatch):
    # A memory-block "Name:" line is not a fallback for the profile: that fallback is how a
    # chat-only, unconfirmed name leaked into every chat's recall.
    monkeypatch.setattr("core.user_preferences.user_address", lambda: "")
    memory = _FakeMemory({"user_profile": "Name: Rick"})
    values = _private_memory_values(
        memory,
        include_profile=True,
        include_project=False,
    )
    assert values["name"] == ""
