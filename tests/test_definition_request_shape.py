"""A definition request is recognised by shape, and an authoring request is never one of them.

`fix conductor factual sibling admission` taught the conductor that "what API means in software" is
the same request as "what is an API".  That repair is about the SHAPE of an indirect question, so
the exam that proves it must not be able to lean on the reported vocabulary: every positive case
here is a definition request from a domain the repair never saw, and none of them contain API, DAO,
software, or crypto.

The same commit also admitted the bare imperative "define X".  "Define" spells two different
requests -- asking what a term means, and authoring a named artifact -- and the second one asks for
a change, so it must never be settled by a read-only factual sibling.
"""

from __future__ import annotations

import pytest

from core.conductor.capabilities import decide_operation_compatibility
from core.conductor.registry import operation_spec
from core.turn_ir import ClauseKind, classify_clause_kind, parse_turn_ir


def _single_clause(text: str):
    clauses = parse_turn_ir(text).clauses
    assert len(clauses) == 1, f"expected one clause, got {[c.request_text for c in clauses]}"
    return clauses[0]


def _admits_factual_explanation(text: str) -> bool:
    spec = operation_spec("factual_explanation")
    assert spec is not None
    return bool(decide_operation_compatibility(spec.capability, _single_clause(text)).allowed)


@pytest.mark.parametrize(
    "request_text",
    (
        # No acronym at all, and a domain the repair never saw.
        "What does colophon mean in publishing?",
        # A long hyphenated proper-noun term, near the bound the shape pattern allows.
        "What does the Nyquist-Shannon sampling theorem mean in signal processing?",
        # The term is a quoted foreign word rather than an English token.
        "What does 'Sehnsucht' mean in German?",
        # Indirect "tell me what ..." with an indefinite article and a musical term.
        "Tell me what a fermata means in sheet music.",
        # "stands for" against a lowercase initialism outside computing.
        "What ppm stands for in water treatment.",
        # An imperative definition whose object is a named principle, not an artifact.
        "Define the principle of least astonishment in interface design.",
        # An imperative definition whose object noun IS an artifact word used conceptually.
        "Define a class in object-oriented programming.",
    ),
)
def test_definition_requests_from_unseen_domains_are_admitted(request_text: str) -> None:
    assert classify_clause_kind(request_text) is ClauseKind.KNOW
    assert _admits_factual_explanation(request_text)


@pytest.mark.parametrize(
    "request_text",
    (
        "Define a variable called total in the report file.",
        "Define a helper function in utils.py.",
        "Define a new column in the users table.",
        "Define a migration in the schema file that backfills archived rows.",
    ),
)
def test_authoring_a_named_artifact_is_never_a_factual_sibling(request_text: str) -> None:
    """A request to change something must not be settled by a read-only explanation.

    The earliest decision is the clause kind: these place or name an artifact this runtime would
    author, so they belong to generative admission, not to `factual_explanation`.
    """

    assert classify_clause_kind(request_text) is ClauseKind.CREATE
    assert not _admits_factual_explanation(request_text)


@pytest.mark.parametrize(
    "request_text",
    (
        # Consequence of an event, not the meaning of a term -- and it is live.
        "What does this outage mean for our customers right now?",
        # A meaning question bound to the newest build is a freshness request.
        "What does the log say the error code means in the latest build?",
        # An observation request that merely mentions a definition.
        "Search the workspace for the retry backoff definition.",
    ),
)
def test_live_or_observation_framing_cannot_borrow_definition_authority(request_text: str) -> None:
    assert not _admits_factual_explanation(request_text)


def test_both_readings_of_define_survive_one_mixed_turn() -> None:
    """The adversarial near-miss: the authoring and the asking reading, in a single request."""

    turn = parse_turn_ir(
        "Define a helper function in utils.py and tell me what idempotence means in distributed systems."
    )
    kinds = [classify_clause_kind(clause.request_text) for clause in turn.clauses]

    assert ClauseKind.CREATE in kinds, [clause.request_text for clause in turn.clauses]
    assert ClauseKind.KNOW in kinds, [clause.request_text for clause in turn.clauses]


def test_sabotage_removing_the_authored_artifact_check_readmits_the_write_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prove the object check is load-bearing rather than decorative."""

    import re

    import core.turn_ir as turn_ir

    monkeypatch.setattr(turn_ir, "_AUTHORED_ARTIFACT_RE", re.compile(r"(?!x)x"))
    request_text = "Define a variable called total in the report file."

    assert classify_clause_kind(request_text) is ClauseKind.KNOW
    assert _admits_factual_explanation(request_text)
