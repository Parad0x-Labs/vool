"""A remembered value asked back with a present-tense word is the user's record, not a live reading.

Repro on the port before this fix (2026-10-07): "What is my current highest score in Ticket to Ride?" answered
"132 points." from the user's own record was replaced by "I could not obtain a current reading", because the
current-claim guard (core.unsourced_current_claim.inspect_unsourced_current_claim) reads any measured value on a
current-information turn as a live observation the turn never made. The claim binder
(core.evidence_kernel.claim_binder) labels each value the reply states as stated in, or derived from, the user's own
evidence lines; only a reply whose every value is so bound counts as sourced.
"""
from __future__ import annotations

import pytest

import core.bootstrap_context as bootstrap_context
from core.unsourced_current_claim import inspect_unsourced_current_claim

CYCLING = (
    "- [2023-03-02] user said: I picked up new cycling shoes for $140 today.\n"
    "- [2023-03-09] user said: Got some bike lights, they were $48.\n"
    "- [2023-03-20] user said: Finally bought a helmet, $95.\n"
)


def _verdict(monkeypatch, question: str, answer: str, evidence: str):
    monkeypatch.setattr(bootstrap_context, "admitted_capsule_evidence_text", lambda *_a, **_k: evidence)
    return inspect_unsourced_current_claim(
        answer=answer, requires_current=True, user_turn_text=question, source_context={}, session_id="s"
    )


@pytest.mark.parametrize(
    "question,answer,evidence",
    [
        ("What is my current highest score in Ticket to Ride?", "132 points.",
         "- [2023-05-01] user said: I just scored 132 points in Ticket to Ride, my best game yet.\n"),
        ("How much have I spent in total on cycling gear?", "$283 total: shoes $140, lights $48, helmet $95.",
         CYCLING),
        ("Which cycling item that I currently own cost the most?", "The helmet, at $95.", CYCLING),
    ],
    ids=["stated-score", "derived-total", "stated-extremum"],
)
def test_a_value_the_users_records_state_or_derive_is_not_withdrawn(monkeypatch, question, answer, evidence):
    verdict = _verdict(monkeypatch, question, answer, evidence)
    assert verdict.requires_current is True
    assert verdict.has_evidence is True
    assert not verdict.unsupported
    assert verdict.claim_binding and verdict.claim_binding.get("all_supported") is True


def test_a_live_price_is_still_withdrawn_even_with_a_matching_number_in_memory(monkeypatch):
    verdict = _verdict(
        monkeypatch,
        "What is the bitcoin price right now?",
        "Bitcoin is trading at $64,000 right now.",
        "- [2023-01-05] user said: I bought some bitcoin at $64,000 last year.\n",
    )
    assert verdict.unsupported


def test_a_total_the_records_do_not_support_is_still_withdrawn(monkeypatch):
    verdict = _verdict(
        monkeypatch,
        "What have I spent on cycling gear so far, as of now?",
        "$300 total so far.",
        CYCLING,
    )
    assert verdict.unsupported
