"""Claims probe: competing family readings become visible (ambiguity + near-miss detection)."""
from __future__ import annotations

from core.agent_runtime.intent_claims import (
    FAMILY_FIND_FOLDER,
    FAMILY_IMAGE_GENERATION,
    FAMILY_LIST_DIRECTORY,
    FAMILY_MACHINE_SPECS,
    is_ambiguous,
    near_miss,
    probe_claims,
)


def _families(text: str) -> set[str]:
    return {claim.family for claim in probe_claims(text)}


def test_historical_specs_hijack_is_flagged_ambiguous() -> None:
    # The real reported mis-route: folder ask + " this machine " marker. Both families must claim,
    # so dispatch KNOWS the priority order is guessing instead of silently answering with specs.
    text = "right, can you check Token hunter folder on this machine and run audit, but only audit no changes"
    claims = probe_claims(text)
    assert {c.family for c in claims} >= {FAMILY_FIND_FOLDER, FAMILY_MACHINE_SPECS}
    assert is_ambiguous(claims) is True
    folder = next(c for c in claims if c.family == FAMILY_FIND_FOLDER)
    assert folder.argument == "token hunter"


def test_clean_cases_claim_exactly_one_family() -> None:
    assert _families("what are my machine specs?") == {FAMILY_MACHINE_SPECS}
    assert _families("find my dropbox folder") == {FAMILY_FIND_FOLDER}
    assert _families("what's on my desktop?") == {FAMILY_LIST_DIRECTORY}
    assert _families("generate me a picture: a corgi astronaut") == {FAMILY_IMAGE_GENERATION}


def test_typoed_verb_is_a_near_miss_not_a_silent_fallthrough() -> None:
    # "fint the oken hunter folder" — no detector knows the verb, but 'folder' is tool-ish.
    text = "Ok. please fint the oken hunter folder on desktop and we will analyse it"
    claims = probe_claims(text)
    assert claims == []
    assert near_miss(text, claims) is True


def test_ordinary_chat_is_neither_claimed_nor_near_miss() -> None:
    for text in (
        "hey how are you today",
        "tell me a joke",
        "what do you think about rust vs go?",
        "what do you think about memory?",
        "Why should an assistant avoid claiming it opened a folder that it did not inspect? Take no action.",
    ):
        claims = probe_claims(text)
        assert claims == [], text
        assert near_miss(text, claims) is False, text


def test_probes_never_raise_on_junk() -> None:
    for junk in ("", "   ", "?" * 500, "\x00\x01", "🦞" * 100):
        probe_claims(junk)  # must not raise
