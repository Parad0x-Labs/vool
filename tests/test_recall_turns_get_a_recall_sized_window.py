"""A same-chat recall turn gets a window that can hold the chat it recalls.

The recall envelope (RECALL_HISTORY_MESSAGES) exists because the ordinary
expanded window (10 messages ≈ 4 exchanges) truncates the exchange carrying
the recalled value in longer chats — the model then honestly reports absence
for something the user really was told (q90 dev corpus F12-08: the PV-2217
ledger answer sat 5 exchanges back).
"""

from __future__ import annotations

from core.context_history_authority import (
    ADJACENCY_FLOOR_MESSAGES,
    EXPANDED_HISTORY_MESSAGES,
    RECALL_HISTORY_MESSAGES,
    enforce_history_budget,
    select_history_policy,
)


class TestRecallEnvelope:
    def test_ordinary_expansion_keeps_the_ten_message_shape(self) -> None:
        selection = select_history_policy(
            scope_allows_transcript=True,
            expansion_hint=True,
            authority_reason="authorized_scope",
        )
        assert selection.max_messages == EXPANDED_HISTORY_MESSAGES == 10

    def test_recall_request_widens_to_the_recall_envelope(self) -> None:
        selection = select_history_policy(
            scope_allows_transcript=True,
            expansion_hint=True,
            authority_reason="authorized_scope",
            requested_max_messages=RECALL_HISTORY_MESSAGES,
        )
        assert selection.max_messages == RECALL_HISTORY_MESSAGES == 24
        assert selection.expands_beyond_adjacency

    def test_absurd_requests_still_cannot_exceed_the_recall_envelope(self) -> None:
        selection = select_history_policy(
            scope_allows_transcript=True,
            expansion_hint=True,
            authority_reason="authorized_scope",
            requested_max_messages=10_000,
        )
        assert selection.max_messages == RECALL_HISTORY_MESSAGES

    def test_adjacency_floor_is_unchanged(self) -> None:
        selection = select_history_policy(
            scope_allows_transcript=True,
            expansion_hint=False,
            authority_reason="authorized_scope",
            requested_max_messages=RECALL_HISTORY_MESSAGES,
        )
        assert selection.max_messages == ADJACENCY_FLOOR_MESSAGES == 2

    def test_budget_holds_recall_sized_transcripts(self) -> None:
        items = [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i} body"}
            for i in range(RECALL_HISTORY_MESSAGES)
        ]
        kept = enforce_history_budget(
            items, max_messages=RECALL_HISTORY_MESSAGES, max_chars=5000
        )
        assert len(kept) == RECALL_HISTORY_MESSAGES
        # The oldest exchange is the first thing to fall when the char budget binds.
        tight = enforce_history_budget(
            items, max_messages=RECALL_HISTORY_MESSAGES, max_chars=120
        )
        assert len(tight) < RECALL_HISTORY_MESSAGES
        assert kept[-1]["content"] == f"turn {RECALL_HISTORY_MESSAGES - 1} body"

    def test_default_envelope_still_clamps_at_ten(self) -> None:
        items = [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i} body"}
            for i in range(30)
        ]
        kept = enforce_history_budget(items)
        assert len(kept) == EXPANDED_HISTORY_MESSAGES
