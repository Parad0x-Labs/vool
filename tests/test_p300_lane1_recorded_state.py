"""Lane 1 / Case C: a personal record's latest stored value is recorded state.

The measured failure (2026-09-30 LongMemEval case q48c0fce9504f8410): the capsule
held two dated user-stated records (Ticket to Ride 132 on 2023-05-25 and 124 on
2023-05-23), the reader answered the "current highest score" question correctly,
and the reply-side live-claim seam withdrew the 132 sentence as an unobserved
current measured quantity. The repair draws the semantic distinction at the
owning law (core.unsourced_current_claim.recorded_state_retention), consumed only
at the measured-quantity kind: a record accumulated under a superlative
("highest score", "best time") has its current value DEFINED as the latest
admitted user-stated record entry — restating it is retention of recorded state.
A volatile quantity (temperature, price, balance, telemetry) keeps the full
original guard: dated memory is still never an observation channel, a value no
admitted statement records is still refused, and a superseded value is still
refused.

Regression rows restate the captured case. Fresh rows (Azul 146 vs 119, lake
temperature, Nordic Wind fund price) were frozen with hashed expectations before
their first execution.
"""
from __future__ import annotations

import pytest

from core.agent_runtime.response import _validate_final_chat_output
from core.unsourced_current_claim import recorded_state_retention

_NOTICE = "not going to state"


@pytest.fixture(autouse=True)
def _clean_retrieval_telemetry():
    # The evidence reader has a process-wide telemetry fallback channel; each test
    # starts from the cleared state a fresh turn would see.
    from core.context_retrieval import reset_retrieval_telemetry

    reset_retrieval_telemetry()
    yield
    reset_retrieval_telemetry()


def _wrap(lines: list[str]) -> str:
    return (
        "<retrieved_context>\nDistilled local facts. Answer from these exact facts only.\n"
        + "\n".join(lines)
        + "\n</retrieved_context>"
    )


TTR_CAPSULE = _wrap(
    [
        "- user said: By the way, speaking of building and creating things, I just got "
        "my highest score in Ticket to Ride - 132 points! (stated: Session date: "
        "2023/05/25; stated: 2023-05-25)",
        "- user said: Also, by the way, I've been crushing it in Ticket to Ride lately "
        "- my highest score so far is 124 points, and I'm eager to keep improving. "
        "(stated: Session date: 2023/05/23; stated: 2023-05-23)",
    ]
)
TTR_Q = "What is my current highest score in Ticket to Ride?"
TTR_REPLY = (
    "Your current highest score in Ticket to Ride is **132 points**, which you hit on "
    "May 25, 2023. That beat your earlier best of 124 from a couple of days before. "
    "Nice jump!"
)

AZUL_CAPSULE = _wrap(
    [
        "- user said: I finally beat my Azul record — my best score is now 146 points! "
        "(stated: Session date: 2024/04/02; stated: 2024-04-02)",
        "- user said: My Azul best so far is 119 points after tonight's game. "
        "(stated: Session date: 2024/03/20; stated: 2024-03-20)",
    ]
)
AZUL_Q = "What is my current highest score in Azul?"


def _seam(
    reply: str,
    question: str,
    capsule: str | None,
    *,
    chat_id: str = "chat-1",
    record_chat_id: str | None = None,
):
    source_context: dict = {
        "surface": "openclaw",
        "platform": "openclaw",
        "chat_id": chat_id,
        "conversation_history": [{"role": "user", "content": question}],
    }
    if capsule is not None:
        source_context["admitted_capsule_evidence"] = {
            "text": capsule,
            "chat_id": record_chat_id or chat_id,
            "source": "canonical_runtime_transcript",
        }
    return _validate_final_chat_output(reply, source_context=source_context)


# --------------------------------------------------------------------- regression


def test_captured_latest_record_answer_survives_the_live_claim_seam() -> None:
    assert _seam(TTR_REPLY, TTR_Q, TTR_CAPSULE) == TTR_REPLY


def test_captured_case_still_withdraws_without_admitted_evidence() -> None:
    delivered = _seam(TTR_REPLY, TTR_Q, None)
    assert "132 points" not in delivered
    assert _NOTICE in delivered


def test_retention_verdict_is_recorded_in_response_control() -> None:
    source_context: dict = {
        "surface": "openclaw",
        "platform": "openclaw",
        "chat_id": "chat-1",
        "conversation_history": [{"role": "user", "content": TTR_Q}],
        "admitted_capsule_evidence": {
            "text": TTR_CAPSULE,
            "chat_id": "chat-1",
            "source": "canonical_runtime_transcript",
        },
    }
    _validate_final_chat_output(TTR_REPLY, source_context=source_context)
    final_ui = dict(source_context.get("response_control") or {}).get("final_ui") or {}
    assert final_ui.get("recorded_state_retention_applied") == ["measured-quantity"]
    assert "unobserved_live_claims_rejected" not in final_ui


def test_retention_requires_user_said_lines_only() -> None:
    # Assistant echoes and quotes never establish a personal record (source
    # authority law); the same text labelled assistant-said must not retain.
    assistant_lines = TTR_CAPSULE.replace("- user said:", "- assistant said:")
    assert recorded_state_retention(TTR_REPLY, assistant_lines) is False


# ---------------------------------------------------------------------- fresh rows
# Frozen as C-F1..C-F5 in FRESH-CASES-FREEZE.json before first execution.


def test_fresh_latest_record_answer_ships_intact() -> None:
    reply = (
        "Your current highest Azul score is 146 points, set on April 2nd. That topped "
        "your previous best of 119 from mid-March."
    )
    assert _seam(reply, AZUL_Q, AZUL_CAPSULE) == reply


def test_fresh_superseded_record_value_is_withdrawn() -> None:
    delivered = _seam("Your current highest Azul score is 119 points.", AZUL_Q, AZUL_CAPSULE)
    assert "119" not in delivered
    assert _NOTICE in delivered


def test_fresh_dated_temperature_does_not_authorize_current_reading() -> None:
    capsule = _wrap(
        [
            "- user said: The lake water was 16 degrees when we swam last July. "
            "(stated: Session date: 2023/07/14; stated: 2023-07-14)"
        ]
    )
    delivered = _seam(
        "The current water temperature at the lake is 16 degrees.",
        "What's the current water temperature at the lake?",
        capsule,
    )
    assert "16 degrees" not in delivered
    assert _NOTICE in delivered


def test_fresh_dated_purchase_price_does_not_authorize_current_price() -> None:
    capsule = _wrap(
        [
            "- user said: I bought my Nordic Wind fund shares at 42 euros each. "
            "(stated: Session date: 2024/02/08; stated: 2024-02-08)"
        ]
    )
    delivered = _seam(
        "The current price of your Nordic Wind shares is 42 euros.",
        "What's the current price of my Nordic Wind shares?",
        capsule,
    )
    assert "42 euros" not in delivered
    assert _NOTICE in delivered


def test_fresh_invented_record_value_is_withdrawn() -> None:
    delivered = _seam("Your current highest Azul score is 150 points.", AZUL_Q, AZUL_CAPSULE)
    assert "150" not in delivered
    assert _NOTICE in delivered


# ------------------------------------------------------------- law-level mechanism pins


def test_record_vocabulary_is_required_for_retention() -> None:
    # The same admitted value presented WITHOUT record vocabulary is a live-reading
    # claim: the law's vocabulary edge is grammatical (superlatives), not a domain
    # list.
    capsule = _wrap(
        [
            "- user said: The lake water was 16 degrees when we swam last July. "
            "(stated: Session date: 2023/07/14; stated: 2023-07-14)"
        ]
    )
    assert (
        recorded_state_retention("The current water temperature at the lake is 16 degrees.", capsule)
        is False
    )


def test_foreign_session_evidence_never_retains() -> None:
    # Evidence harvested for a DIFFERENT chat is refused at consumption, so the
    # reply keeps its baseline treatment: withdrawn as an unobserved value.
    delivered = _seam(TTR_REPLY, TTR_Q, TTR_CAPSULE, chat_id="chat-2", record_chat_id="chat-1")
    assert "132 points" not in delivered
    assert _NOTICE in delivered


def test_supersession_requires_same_subject() -> None:
    # A later record for a DIFFERENT game does not supersede the claimed one.
    capsule = _wrap(
        [
            "- user said: I finally beat my Azul record — my best score is now 146 points! "
            "(stated: Session date: 2024/04/02; stated: 2024-04-02)",
            "- user said: My Azul best so far is 119 points after tonight's game. "
            "(stated: Session date: 2024/03/20; stated: 2024-03-20)",
            "- user said: My Wingspan best is 220 points after the weekend. "
            "(stated: Session date: 2024/04/10; stated: 2024-04-10)",
        ]
    )
    reply = "Your current highest Azul score is 146 points, set on April 2nd."
    assert recorded_state_retention(reply, capsule) is True


def test_same_day_records_resolve_by_the_records_extremum() -> None:
    # Native chat ingestion records statement dates as the machine day, so a
    # two-record store can carry one date for both lines. A max record's current
    # value is then the maximum admitted value: the newer statement is the
    # record, and the smaller value claimed as CURRENT is stale.
    capsule = _wrap(
        [
            "- user said: my highest Ticket to Ride score so far was 124 points. "
            "(recorded: 2026-09-30)",
            "- user said: I just got my highest score in Ticket to Ride - 132 points! "
            "(recorded: 2026-09-30)",
        ]
    )
    assert (
        recorded_state_retention(
            "Your current highest score in Ticket to Ride is 132 points.", capsule
        )
        is True
    )
    assert (
        recorded_state_retention(
            "Your current highest score in Ticket to Ride is 124 points.", capsule
        )
        is False
    )


def test_lower_better_record_uses_the_minimum() -> None:
    capsule = _wrap(
        [
            "- user said: my quickest 10k time is now 41 minutes! (recorded: 2026-09-30)",
            "- user said: my best 10k time used to be 44 minutes. (recorded: 2026-09-30)",
        ]
    )
    assert (
        recorded_state_retention(
            "Your current best 10k time is your shortest at 41 minutes.", capsule
        )
        is True
    )
    assert (
        recorded_state_retention(
            "Your current best 10k time is your shortest at 44 minutes.", capsule
        )
        is False
    )
