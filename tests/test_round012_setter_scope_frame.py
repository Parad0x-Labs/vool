"""ROUND-012 pins — structural prospective-scope setter recognition.

Frozen repair (council/round-012/FIX_PLAN.md + R1-COMPARISON, three-seat consensus):
the scoped-constraint mint decomposes the setter frame into STRUCTURAL ROLES —
prospective marker (next|following|subsequent|upcoming|coming as one ROLE), cardinality
(1..20), answer-noun class — and HOISTS the constraint clauses so they attach to any
valid scope rather than being chained to historical surface adjacency.

Overfire law (binding): a mint requires (A) a structural prospective answer scope AND
(B) a recognized constraint clause AND (C) no disqualifier (negation/veto, quoted
example, past-tense/descriptive framing). The kernel remains the sole owner of setter
recognition and state creation; the model is never delegated either.

Sabotage classes: S1 frame revert (literal `next` only), S2 hoist removal (clauses
chained to the frame guard), S3 over-broadening (noun or constraint requirement
dropped). Each must redden its own pins.
"""
from __future__ import annotations

import json

import core.kernel.repl as repl


def _mint(q: str) -> list[dict]:
    return repl._mint_ledger(q)


def _xw(q: str) -> dict | None:
    return next((e for e in _mint(q) if e["kind"] == "exact_words"), None)


# ================================================================ positive matrix
# (question, ttl, N) — brand-new mutations across the semantic class; the R0 working
# setter is included as the regression control.
POSITIVE = [
    # R0 control (must keep working byte-compatibly)
    ("Keep the next TWO real answers after this message to exactly five lowercase "
     "words each. This setup reply is only an acknowledgement and must not consume "
     "either slot.", 2, 5),
    # R0 failed setter (the round's repro)
    ("Once you acknowledge this, the following THREE substantive replies must each "
     "be exactly seven words long. The acknowledgement itself is outside that "
     "three-reply scope.", 3, 7),
    # mandated mutation shapes
    ("After this acknowledgement, make my following four replies exactly six words.", 4, 6),
    ("Acknowledge now; each of the subsequent two real answers must contain exactly "
     "eight words.", 2, 8),
    ("This confirmation is outside scope. The upcoming three responses must each use "
     "exactly five words.", 3, 5),
    ("For the following five substantive answers, use precisely seven words each.", 5, 7),
    # additional novel forms
    ("The coming two replies should be exactly nine lowercase words apiece.", 2, 9),
    ("Your subsequent 6 answers must each be exactly four words long.", 6, 4),
    ("Starting after this ack, the following three replies: precisely eleven words "
     "each please.", 3, 11),
    ("Once acknowledged, each of my upcoming two responses uses exactly twelve "
     "lowercase words.", 2, 12),
    # ALT-ONLY shapes: the scope's cardinality+noun is NOT adjacent to a marker word,
    # so the marker-adjacent frame cannot match; only the hoisted role-recognition
    # path (cardinality + answer-noun + prospective cue + constraint) can mint these.
    ("Reply with exactly four words for each of the three answers after this one.", 3, 4),
    ("Keep it to exactly ten words in the two replies that follow this message.", 2, 10),
]

import pytest


@pytest.mark.parametrize("q,ttl,n", POSITIVE)
def test_positive_mutations_mint(q, ttl, n):
    e = _xw(q)
    assert e is not None, f"failed to mint exact_words for: {q!r} -> {_mint(q)!r}"
    assert e["ttl"] == ttl, f"TTL must come from the SCOPE cardinality: {e!r}"
    assert e["arg"] == str(n), f"N must come from the CONSTRAINT clause: {e!r}"


def test_lowercase_still_co_mints():
    e = _mint("Keep the next TWO real answers after this message to exactly five "
              "lowercase words each.")
    kinds = sorted(x["kind"] for x in e)
    assert kinds == ["exact_words", "lowercase"]
    assert all(x["ttl"] == 2 for x in e)


def test_two_cardinalities_ttl_from_scope_n_from_constraint():
    # the sharpest pin: THREE replies (scope/TTL) but exactly seven words (N)
    e = _xw("Once you acknowledge this, the following THREE substantive replies must "
            "each be exactly seven words long.")
    assert e["ttl"] == 3 and e["arg"] == "7"


# ================================================================ negative matrix
NEGATIVE = [
    "The following three replies used seven words.",                       # past tense
    "There are the following two reasons the plan failed.",                # non-answer noun
    "Consider the next three examples in the textbook.",                   # non-answer noun
    "We discussed the following two events at length.",                    # non-answer noun
    "My next three replies will be short.",                                # no constraint clause
    "Keep your next two answers to no more than five words.",              # ceiling, not exact
    "Use at least six words in the following two answers.",                # floor
    "Do not apply any word limits to my following three replies; answer in "
    "exactly seven words freely.",                                         # negated/vetoed
    "I was reading about how the following two answers might be exactly "
    "seven words each in some other system.",                              # discussion about
    "The three answers I gave yesterday were long.",                       # past reference
    "Answer this one in exactly six words.",                               # unscoped current-turn
]


@pytest.mark.parametrize("q", NEGATIVE)
def test_negative_controls_never_mint(q):
    assert _mint(q) == [], f"overfire: ordinary prose minted a ledger: {q!r} -> {_mint(q)!r}"


# ============================================================ end-to-end lifecycle
def _judge_ack_then_exact():
    state = {"round": 0}

    def fake(runner, effect_id, system, user):
        if effect_id == "model.extract":
            return {"obligations": [{"description": "the ask", "lane": "compose",
                                     "query": "", "format": "", "source_offset": 0,
                                     "resolves_carryover": ""}]}
        if effect_id == "model.derive":
            return {"computations": [], "missing": []}
        if effect_id == "model.synthesize":
            state["round"] += 1
            # setter turn: ack-ish prose (displaced by the deterministic ack anyway)
            return {"claims": [{"obligation_id": "ob1", "text": "Okay noted.",
                                "type": "conversational"}]}
        raise AssertionError(effect_id)
    return fake


def _drive_setter(monkeypatch, setter_q):
    monkeypatch.setattr(repl, "_model_json", _judge_ack_then_exact())
    tr, _j, _l, facts = repl.run_turn(
        setter_q, repl.EffectRunner(mode="record"), None, None, session_facts=None)
    return tr, repl._extract_answer(tr) or "", facts


def test_failed_r0_setter_now_arms_and_acks_end_to_end(monkeypatch):
    tr, ans, facts = _drive_setter(
        monkeypatch,
        "Once you acknowledge this, the following THREE substantive replies must "
        "each be exactly seven words long. The acknowledgement itself is outside "
        "that three-reply scope.")
    ledger = json.loads(facts.get(repl._LEDGER_KEY, "[]"))
    assert any(e["kind"] == "exact_words" and e["arg"] == "7" and e["ttl"] == 3
               for e in ledger), f"scope not armed end-to-end: {ledger!r}"
    assert "applies to your next 3 answer(s)" in ans, (
        f"deterministic setter ack must ship: {ans!r}")
    assert "constraint setter" in tr


def test_setter_consumes_zero_ttl(monkeypatch):
    tr, ans, facts = _drive_setter(
        monkeypatch,
        "The upcoming three responses must each use exactly five words.")
    ledger = json.loads(facts.get(repl._LEDGER_KEY, "[]"))
    assert any(e["kind"] == "exact_words" and e["ttl"] == 3 for e in ledger)


# ============================================================ sabotage seams doc
# S1: revert _FRAME_RE's marker alternation to literal `next` AND delete the alt-path
#     block -> every non-`next` positive mutation reds; R0 control stays green.
# S2: delete the alt-path block only (hoist part 1) -> m2/m3-style positives red
#     (their scope phrase lacks the marker-adjacent frame shape).
# S3: drop the answer-noun requirement from the frame/alt -> "the following two
#     reasons" reds as a false mint.
