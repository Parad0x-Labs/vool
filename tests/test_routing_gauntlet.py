"""The routing gauntlet: real transcript messages -> the route they MUST take.

Every case in the mis-route corpus is a message a real user actually sent that the keyword
router got wrong (or a close variant). Every case in the unambiguous corpus is a phrasing
that routes correctly today and must never regress. Any change to detectors, claim probes,
or the intent arbiter has to keep this file green — routing accuracy is a tested number
here, not an anecdote.

Layer 1 (this file): pure detector/probe truth — no model, no tools executed.
Layer 2 (test_intent_arbiter.py): the arbiter's tie-break on the ambiguous/near-miss cases.
Live layer: driven against the running daemon at ship time (recorded in the delivery ledger).
"""
from __future__ import annotations

import pytest

from core.agent_runtime.intent_claims import (
    FAMILY_DISK_USAGE,
    FAMILY_FIND_FOLDER,
    FAMILY_IMAGE_GENERATION,
    FAMILY_LIST_DIRECTORY,
    FAMILY_MACHINE_SPECS,
    is_ambiguous,
    near_miss,
    probe_claims,
)
from core.execution.constants import machine_folder_search_intent

# ---------------------------------------------------------------------------------------------
# Corpus A — real mis-routes (message, the family that MUST win, the wrong family it once got)
# ---------------------------------------------------------------------------------------------
MISROUTES = [
    pytest.param(
        "right, can you check Token hunter folder on this machine and run audit, but only audit no changes in codes or stuctures",
        FAMILY_FIND_FOLDER,
        FAMILY_MACHINE_SPECS,
        id="specs-hijack-check-folder-on-this-machine",
    ),
    pytest.param(
        "ok inspect the local token hunter folder. No changes to the code, you are doing audit",
        FAMILY_FIND_FOLDER,
        None,
        id="inspect-verb-folder",
    ),
    pytest.param(
        "audit the token-hunter folder and tell me what Vool missed",
        FAMILY_FIND_FOLDER,
        None,
        id="audit-verb-folder",
    ),
]

# ---------------------------------------------------------------------------------------------
# Corpus B — unambiguous phrasings that must keep routing exactly as they do today
# ---------------------------------------------------------------------------------------------
UNAMBIGUOUS = [
    pytest.param("what are my machine specs?", FAMILY_MACHINE_SPECS, id="plain-specs"),
    pytest.param("check my machine specs", FAMILY_MACHINE_SPECS, id="check-specs"),
    pytest.param("find my dropbox folder", FAMILY_FIND_FOLDER, id="plain-find-folder"),
    pytest.param("how much free space on my disk?", FAMILY_DISK_USAGE, id="disk-space"),
    pytest.param("what's on my desktop?", FAMILY_LIST_DIRECTORY, id="desktop-listing"),
    pytest.param("generate me a picture: a corgi astronaut", FAMILY_IMAGE_GENERATION, id="image-gen"),
]

# ---------------------------------------------------------------------------------------------
# Corpus C — plain conversation: must claim nothing, trip nothing
# ---------------------------------------------------------------------------------------------
CHAT_ONLY = [
    "hey how are you today",
    "meh boring, give me summary",
    "ok you check waht we were working on and we will continue from there",
    "thanks, looks great!",
    "why can't we improve our PnL?",
]


@pytest.mark.parametrize(("text", "want", "wrong"), MISROUTES)
def test_misroute_corpus_right_family_claims(text: str, want: str, wrong: str | None) -> None:
    claims = probe_claims(text)
    families = {claim.family for claim in claims}
    assert want in families, f"the correct family must claim: {text!r}"
    if wrong is not None:
        # The old hijacker may STILL claim (that is what makes it ambiguous) — but then the
        # ambiguity must be visible so the arbiter/dispatch can resolve it deliberately.
        if wrong in families:
            assert is_ambiguous(claims), "competing readings must be flagged, never silent"


@pytest.mark.parametrize(("text", "want"), UNAMBIGUOUS)
def test_unambiguous_corpus_single_claim(text: str, want: str) -> None:
    claims = probe_claims(text)
    assert {claim.family for claim in claims} == {want}, text
    assert not is_ambiguous(claims)


@pytest.mark.parametrize("text", CHAT_ONLY)
def test_chat_corpus_claims_nothing(text: str) -> None:
    claims = probe_claims(text)
    assert claims == [], text
    assert near_miss(text, claims) is False, text


def test_typoed_folder_ask_is_surfaced_as_near_miss() -> None:
    # 'fint the oken hunter folder' — no detector can know every typo; the near-miss signal is
    # what hands it to the arbiter instead of a silent model-lane fallthrough.
    text = "Ok. please fint the oken hunter folder on desktop and we will analyse it"
    claims = probe_claims(text)
    assert claims == []
    assert near_miss(text, claims) is True


def test_folder_name_extraction_survives_the_full_sentence() -> None:
    got = machine_folder_search_intent(
        "right, can you check Token hunter folder on this machine and run audit, but only audit no changes"
    )
    assert got == ("machine.find_folder", "token hunter")
