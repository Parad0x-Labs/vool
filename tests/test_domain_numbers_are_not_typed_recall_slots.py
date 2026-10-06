"""A domain number is a fact about a subject, not a typed recall slot.

Live incident this pins (q90 dev corpus F12-08, measured 2026-09-29 on
e1dfdb63): "What did you say the correct archive number for the 1934 Virgo
plate was?" was answered "That number does not appear in the visible history
of this chat" — a FALSE proved absence: the assistant had stated PV-2217
earlier in the same chat. The typed-recall fast path can only prove absence
for slots it can enumerate (labelled parts, typed request numbers, visible
math results); "the archive number" names a property of the plate, so the
turn must fall through to the model lane with the expanded transcript.
"""

from __future__ import annotations

from core.agent_runtime.same_chat_recall import (
    _DOMAIN_NUMBER_BOUND_RE,
    same_chat_transcript_recall_fast_path,
)

_SOURCE = {"surface": "api", "platform": "api"}


def _exchanges_source(pairs: list[tuple[str, str]]) -> dict[str, object]:
    history = []
    for user_text, assistant_text in pairs:
        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": assistant_text})
    return {"surface": "api", "platform": "api",
            "conversation_history": history}


class TestDomainNumberBinding:
    def test_domain_number_shapes_bind(self) -> None:
        assert _DOMAIN_NUMBER_BOUND_RE.search("the correct archive number for the plate")
        assert _DOMAIN_NUMBER_BOUND_RE.search("what was the serial number of the pump?")
        assert _DOMAIN_NUMBER_BOUND_RE.search("her phone number again?")
        assert _DOMAIN_NUMBER_BOUND_RE.search("the plate's value in the ledger")
        assert _DOMAIN_NUMBER_BOUND_RE.search("the lot numbers you quoted")

    def test_typed_slot_shapes_stay_slots(self) -> None:
        assert _DOMAIN_NUMBER_BOUND_RE.search("what was the number?") is None
        assert _DOMAIN_NUMBER_BOUND_RE.search("what was the value again?") is None
        assert _DOMAIN_NUMBER_BOUND_RE.search("the number for part 2") is None
        assert _DOMAIN_NUMBER_BOUND_RE.search("the third number") is None
        assert _DOMAIN_NUMBER_BOUND_RE.search("item 3 value") is None


class TestFastPathDeclinesDomainNumbers:
    def test_reproduction_archive_number(self) -> None:
        source = _exchanges_source([
            ("Can you check the ledger entry for the 1934 Virgo plate? I keep writing it two ways.",
             "Checked the plate ledger: the 1934 Virgo plate is archived as PV-2217 — the ledger's PV-2177 is a known transposition."),
            ("Knew it. Thanks.", ""),
        ])
        recalled = same_chat_transcript_recall_fast_path(
            "What did you say the correct archive number for the 1934 Virgo plate was?",
            source_surface="api",
            source_context=source,
        )
        # No false proved absence: the turn belongs to the model lane with the
        # expanded transcript, where the visible PV-2217 statement lives.
        assert recalled is None

    def test_fresh_domain_number_variants_decline(self) -> None:
        source = _exchanges_source([
            ("Register says the anchor winch is serial WK-4471.",
             "Winch serial WK-4471 — noted."),
        ])
        assert same_chat_transcript_recall_fast_path(
            "What was the serial number on the winch again?",
            source_surface="api", source_context=source) is None
        assert same_chat_transcript_recall_fast_path(
            "Remind me: the locker code number for the gear room?",
            source_surface="api", source_context=source) is None

    def test_typed_number_recall_still_works(self) -> None:
        source = _exchanges_source([
            ("what is 137 x 29?", "137 x 29 = 3973"),
        ])
        recalled = same_chat_transcript_recall_fast_path(
            "what was the number you gave?",
            source_surface="api",
            source_context=source,
        )
        assert recalled is not None and recalled.found
        assert "3973" in recalled.response

    def test_typed_absence_is_still_proved_for_slot_questions(self) -> None:
        source = _exchanges_source([
            ("tell me about the harbor", "The harbor freezes in January."),
        ])
        recalled = same_chat_transcript_recall_fast_path(
            "what was the number you gave?",
            source_surface="api",
            source_context=source,
        )
        assert recalled is not None and not recalled.found
        assert "does not appear" in recalled.response
