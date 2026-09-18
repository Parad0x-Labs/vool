"""BLOCK-C seam 5: evidence lexer unification (signed block 60da1f5f).

t45 class: "Neo4j" lexed as the number 4 — identifier-embedded digits tripped
the numeric-claim gates (prose naming a product was rejected as an unbound
number) and, in the reverse direction, could GROUND a fabricated standalone
number. One lexer rule everywhere: a digit led by a letter/digit/dot fragment
is an identifier member, never a quantity; a digit-led token keeps its
quantity ("24GB", "5pm").
"""
import pytest

from core.kernel import repl
from core.kernel.effects import EffectRunner
from core.kernel.evidence_types import EvidenceTypeError, TypedClaim, _number_tokens, validate_claims


def _judge(extract_rows, synth_claims=None):
    def fake(runner, effect_id, system, user):
        if effect_id == "model.extract":
            return {"obligations": extract_rows}
        if effect_id == "model.synthesize":
            return {"claims": synth_claims or []}
        if effect_id == "model.derive":
            return {"computations": [], "missing": []}
        if effect_id == "model.verify":
            return {"claims": [], "all_parts_answered": "yes", "missing": []}
        raise AssertionError(effect_id)
    return fake


def _row(desc, lane, query=""):
    return {"description": desc, "lane": lane, "query": query, "format": "",
            "source_offset": 0, "resolves_carryover": ""}


def test_identifier_embedded_digits_are_not_quantities():
    assert _number_tokens("Neo4j MP3 x402 B12 v2.47") == set()
    assert _number_tokens("24GB at 5pm for $1,420") == {"24", "5", "1420"}
    assert _number_tokens("2.47") == {"2.47"}
    assert _number_tokens("range 0.0118-0.0129") == {"0.0118", "0.0129"}


def test_t45_replay_identifier_prose_ships_instead_of_number_rejection(monkeypatch):
    """An observed claim whose only 'digits' are Neo4j's is DIGITLESS: it re-types
    unverified and ships wearing its mark — never 'rejected: unbound number'."""
    judge = _judge([_row("recommend a graph database", "compose")],
                   [{"obligation_id": "ob1", "text": "Neo4j fits graph workloads",
                     "type": "observed"},
                    {"obligation_id": "ob1", "text": "Happy to explain the tradeoffs.",
                     "type": "conversational"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "Which database fits graph workloads?", EffectRunner(mode="record"), None, None)
    assert "evidence numbers must travel by token" not in transcript, \
        "identifier-only prose is DIGITLESS — the numeric rejection must not fire"
    ans = repl._extract_answer(transcript) or ""
    assert "Neo4j fits graph workloads" in ans, f"the identifier prose must ship: {ans!r}"
    assert "unverified" in ans, f"it ships marked, not laundered: {ans!r}"


def test_identifier_digits_cannot_ground_a_fabricated_number():
    """Adversarial direction: a receipt saying only 'Neo4j' must NOT supply a
    phantom 4 to ground a fabricated standalone number."""
    receipts = {"ob1-web1": "Neo4j is a graph database written in Java"}
    with pytest.raises(EvidenceTypeError, match="none of its cited receipts"):
        validate_claims([TypedClaim(text="the answer is 4", ctype="observed",
                                    ref="ob1-web1")], receipts, {})


def test_unit_glued_number_still_grounds():
    """'24GB' in a receipt still grounds a '24 GB' claim — digit-led tokens keep
    their quantity."""
    receipts = {"ob1-web1": "spec sheet: 24GB RAM, ships Friday"}
    validate_claims([TypedClaim(text="it has 24 GB of RAM", ctype="observed",
                                ref="ob1-web1")], receipts, {})   # must not raise
