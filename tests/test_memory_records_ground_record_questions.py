"""This chat's own memory records support a record question the lookup signals escalated.

Measured 2026-10-07 on the memory port's official-150 parity runs: three questions about people in
the chat's own records were refused in all three runs with "I can't publish an answer to this: it
needed current information", although the reader answered them from the records. Their surface
words read as lookups to the current-information signals: a public entity ("Lord of the Rings"),
a departure ("When will Tim leave ..."), a price ("How much does James pay ..."). The publication
gate offered no row the records could fill (`core.grounding_publication._support_rows`), so it
refused before matching a claim.

The records the reader was given (the admitted capsule evidence) are now support rows of their own
kind (`core.memory_grounding`). Claims are still matched one by one: a live lookup the records do
not state is refused exactly as before, and lines the assistant itself said never count.
"""

from __future__ import annotations

import pytest

from core.claim_support import match_claims
from core.grounding_lifecycle import GroundingLifecycle, TurnIdentity
from core.grounding_publication import _support_rows, publication_verdict
from core.memory_grounding import memory_record_rows, publish_memory_records_for_turn


def _lifecycle(request: str, evidence: str) -> GroundingLifecycle:
    return GroundingLifecycle(
        lifecycle_id="memory-record-turn",
        identity=TurnIdentity(),
        request_text=request,
        retrieval_outcome="no_sources",
        model_authored=True,
        memory_records=tuple(memory_record_rows(evidence)),
    )


RECORD_SHAPES = [
    pytest.param(
        "According to John, who is his favorite character from Lord of the Rings?",
        "John: Thanks! I've watched a bunch of them and they're inspiring. My favorite character is "
        "Aragorn, he grows so much throughout the story. (stated: 2023-08-02)",
        "Aragorn.",
        id="public-entity-words",
    ),
    pytest.param(
        "When will Tim leave for Ireland?",
        "Tim: Great news - I'm finally in the study abroad program I applied for! Next month, I'm off "
        "to Ireland for a semester. (stated: Session date: 7 January, 2024; stated: 2024-01-07)",
        'February 2024 — Tim said on 7 January 2024 he was off to Ireland "next month."',
        id="departure-words",
    ),
    pytest.param(
        "How much does James pay per dance class?",
        "James: Yes, two days ago I signed up for a cooking class. It costs $10 per class. "
        "(stated: 2022-05-01)",
        "(b) Not mentioned in the conversation — the $10 per class refers to James's cooking class, "
        "not a dance class.",
        id="price-words",
    ),
]


@pytest.mark.parametrize("question,evidence,answer", RECORD_SHAPES)
def test_an_answer_the_records_state_is_published(question: str, evidence: str, answer: str) -> None:
    verdict = publication_verdict(_lifecycle(question, evidence), answer)
    assert verdict.state == "published", verdict
    assert verdict.content == answer
    assert verdict.support_origin == "memory_record"


@pytest.mark.parametrize("question,evidence,answer", RECORD_SHAPES)
def test_without_the_records_the_same_answer_is_still_refused(question: str, evidence: str, answer: str) -> None:
    verdict = publication_verdict(_lifecycle(question, ""), answer)
    assert verdict.state in {"refused", "failed"}, verdict
    assert answer not in verdict.content


LIVE_LOOKUPS = [
    pytest.param(
        "When does the next train to Vilnius leave?",
        "- [2023-05-01] user said: I love train trips to the coast.",
        "The next train to Vilnius leaves at 14:05 from platform 3.",
        id="train-times",
    ),
    pytest.param(
        "How much does the iPhone 16 Pro cost right now?",
        "- [2023-02-11] user said: My old phone's battery is dying, I need a new one soon.",
        "The iPhone 16 Pro costs $999 right now.",
        id="public-product-price",
    ),
    pytest.param(
        "What's in the news today?",
        "- [2023-09-01] user said: I usually read the news with my morning coffee.",
        "Today Parliament passed the 2026 budget after a 14-hour debate.",
        id="todays-news",
    ),
]


@pytest.mark.parametrize("question,evidence,answer", LIVE_LOOKUPS)
def test_a_live_lookup_the_records_do_not_cover_is_still_refused(question: str, evidence: str, answer: str) -> None:
    verdict = publication_verdict(_lifecycle(question, evidence), answer)
    assert verdict.state in {"refused", "failed"}, verdict
    assert answer not in verdict.content


def test_a_line_the_assistant_itself_said_is_never_support() -> None:
    evidence = "- [2024-01-02] assistant said: The next train to Vilnius leaves at 14:05 from platform 3.\n"
    assert memory_record_rows(evidence) == []
    verdict = publication_verdict(
        _lifecycle("When does the next train to Vilnius leave?", evidence),
        "The next train to Vilnius leaves at 14:05 from platform 3.",
    )
    assert verdict.state in {"refused", "failed"}, verdict


def test_the_records_reach_the_lifecycle_through_the_one_evidence_reader(monkeypatch) -> None:
    import core.bootstrap_context as bootstrap_context
    from core.grounding_lifecycle import _record_for, register_required

    context: dict = {"turn_id": "t-mem-1", "request_id": "r-mem-1"}
    register_required(context, request_text="When will Tim leave for Ireland?", reason_codes=("current_info_signal:schedule_lookup",))
    monkeypatch.setattr(
        bootstrap_context,
        "admitted_capsule_evidence_text",
        lambda *_a, **_k: "<retrieved_context>\nTim: Next month, I'm off to Ireland for a semester.\n</retrieved_context>",
    )
    assert publish_memory_records_for_turn(context) == 1
    lifecycle = _record_for(context)
    rows, origin = _support_rows(lifecycle)
    assert origin == "memory_record"
    assert rows == [{"summary": "Tim: Next month, I'm off to Ireland for a semester.", "source": "memory_record"}]
    # Idempotent: the same evidence published twice adds nothing.
    assert publish_memory_records_for_turn(context) == 0


def test_a_turn_with_no_lifecycle_records_nothing(monkeypatch) -> None:
    import core.bootstrap_context as bootstrap_context

    monkeypatch.setattr(bootstrap_context, "admitted_capsule_evidence_text", lambda *_a, **_k: "Tim: hello")
    assert publish_memory_records_for_turn({"turn_id": "no-lifecycle-turn"}) == 0


def test_a_possessive_names_the_same_entity() -> None:
    rows = [{"summary": "James: I signed up for a cooking class. It costs $10 per class."}]
    claims = match_claims(
        answer="The $10 per class is James's cooking class.",
        notes=rows,
        request_text="How much does James pay per dance class?",
    )
    assert claims.coverage == "full", claims.as_dict()
