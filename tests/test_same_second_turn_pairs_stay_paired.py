"""Same-second dialogue turns must stay paired when the transcript is assembled.

Live incident this pins (q90 dev corpus F05-06, measured 2026-09-29 on
e1dfdb63): a chat held two exchanges — an original booking and a dated UPDATE
swapping the two dates — and the served provider request paired the FIRST
user message with the LAST assistant reply, dropping the update turn that
carried the expected value. Cause: ``recent_dialogue_turns`` ordered by
``created_at`` alone, a user turn and its reply written under the same
instant (pinned clock, imported coarse timestamps) tie, the tie order is
unspecified, and the transcript builder's ``reversed()`` then scrambles each
pair to [assistant, user] so ``most_recent_completed_exchange`` mispairs
across exchanges. The store's "newest first" contract is now a total order
(``created_at DESC, rowid DESC``): within one instant, the later-written row
— the reply — is newer.
"""

from __future__ import annotations

import uuid
from unittest import mock

from core.context_history_authority import most_recent_completed_exchange
from storage import dialogue_memory
from storage.dialogue_memory import recent_dialogue_turns, record_dialogue_turn


def _write_pair(session_id: str, user_text: str, assistant_text: str) -> None:
    record_dialogue_turn(
        session_id,
        raw_input=user_text,
        normalized_input=user_text,
        reconstructed_input=user_text,
        speaker_role="user",
        topic_hints=[],
        reference_targets=[],
        understanding_confidence=0.9,
        quality_flags=[],
    )
    record_dialogue_turn(
        session_id,
        raw_input=assistant_text,
        normalized_input=assistant_text,
        reconstructed_input=assistant_text,
        speaker_role="assistant",
        topic_hints=[],
        reference_targets=[],
        understanding_confidence=0.9,
        quality_flags=[],
    )


def test_same_second_pairs_keep_write_order_newest_first() -> None:
    session_id = f"same-second:{uuid.uuid4().hex}"
    frozen = "2026-03-19T10:10:00+00:00"
    with mock.patch.object(dialogue_memory, "_utcnow", return_value=frozen):
        _write_pair(
            session_id,
            "Circle the dates: the trawler Meri's haul-out is booked for April 9, "
            "and the wharf-wide safety drill is April 16.",
            "April 9 for Meri's haul-out and April 16 for the safety drill — noted.",
        )
        _write_pair(
            session_id,
            "Update: the two dates got swapped — Meri's haul-out moves to April 16 "
            "and the safety drill happens April 9.",
            "Swapped noted — drill on April 9, haul-out on April 16.",
        )

    turns = recent_dialogue_turns(
        session_id, speaker_roles=("user", "assistant"), limit=10
    )
    # Newest first as a TOTAL order: within the tied instant the reply (written
    # later) precedes the user turn, so `reversed()` yields true chronology.
    assert [t["speaker_role"] for t in turns] == [
        "assistant", "user", "assistant", "user",
    ]
    chronological = list(reversed(turns))
    assert [t["speaker_role"] for t in chronological] == [
        "user", "assistant", "user", "assistant",
    ]


def test_reversed_turns_keep_the_update_exchange_paired() -> None:
    session_id = f"same-second-pairing:{uuid.uuid4().hex}"
    frozen = "2026-03-26T07:48:00+00:00"
    with mock.patch.object(dialogue_memory, "_utcnow", return_value=frozen):
        _write_pair(
            session_id,
            "Circle the dates: the trawler Meri's haul-out is booked for April 9, "
            "and the wharf-wide safety drill is April 16.",
            "April 9 for Meri's haul-out and April 16 for the safety drill — noted.",
        )
        _write_pair(
            session_id,
            "Update: the two dates got swapped — Meri's haul-out moves to April 16 "
            "and the safety drill happens April 9.",
            "Swapped noted — drill on April 9, haul-out on April 16.",
        )

    turns = recent_dialogue_turns(
        session_id, speaker_roles=("user", "assistant"), limit=10
    )
    transcript = [
        {"role": t["speaker_role"], "content": str(t["raw_input"])}
        for t in reversed(turns)
    ]
    pair = most_recent_completed_exchange(transcript)
    assert [m["role"] for m in pair] == ["user", "assistant"]
    assert pair[0]["content"].startswith("Update: the two dates got swapped")
    assert pair[1]["content"].startswith("Swapped noted")
