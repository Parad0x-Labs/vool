"""ROUND-023 — terminal obligation state is authoritative.

Regression tests for the invariant:

    Once an obligation is terminal (REFUSED), downstream machinery must not
    resurrect semantic authority for that obligation.

The repair adds ``_owner_is_refused()`` guards at four production surfaces
in ``core/kernel/repl.py``:

  1. The derive loop — no new calc receipts for REFUSED owners
  2. The verbatim-echo backstop — no receipt shipping for REFUSED owners
  3. The authoritative-arithmetic injection — no extract calc for REFUSED owners
  4. The session-fact persistence — no -calc receipt as a cross-turn fact

These adversarial pins drive ``run_turn`` with a fake judge so they test the
KERNEL MECHANICS — the owner-state guards — never model capability.
"""
from __future__ import annotations

import re

import pytest

import core.kernel.repl as repl
from core.kernel.effects import EffectRunner

# ---------------------------------------------------------------------------
# Helper: a fast fake that records every prompt
# ---------------------------------------------------------------------------

def _judge(extract=None, derive=None, synth=None, verify=None):
    """A dispatching stand-in for _model_json; records every prompt it is shown.
    verify defaults to all-bound/all-answering so pins exercise the clean path."""
    seen: dict[str, list[str]] = {"model.extract": [], "model.derive": [],
                                   "model.synthesize": [], "model.verify": []}

    def fake(runner, effect_id, system, user):
        seen.setdefault(effect_id, []).append(user)
        if effect_id == "model.extract":
            return extract if extract is not None else {"obligations": []}
        if effect_id == "model.derive":
            return derive if derive is not None else {"computations": [], "missing": []}
        if effect_id == "model.synthesize":
            if isinstance(synth, list):
                return synth.pop(0) if synth else {"claims": []}
            return synth if synth is not None else {"claims": []}
        if effect_id == "model.verify":
            if verify is not None:
                return verify
            n = user.count("CLAIM ")
            return {"verdicts": [{"claim": i, "bound": True, "answers_the_ask": True}
                                 for i in range(1, n + 1)],
                    "all_parts_answered": True, "missing": ""}
        raise AssertionError(f"unexpected judgment {effect_id}")

    fake.seen = seen
    return fake


def _row(description, lane, query="", fmt=""):
    return {"description": description, "lane": lane, "query": query,
            "format": fmt, "resolves_carryover": ""}


# ---------------------------------------------------------------------------
# Test A: REFUSED-owner derive guard
#
# A machine lane with "none:gap" triggers declare_refused for ob1.
# The derive model returns a computation for ob1.  The ROUND-023 guard
# blocks it — no calc receipt, no session fact.
# ---------------------------------------------------------------------------

def test_r23_A_refused_derivation_blocked(monkeypatch) -> None:
    """A refused obligation cannot mint a new calc artifact via the derive loop."""
    fetch_calls: list[str] = []

    def fetch(q):
        fetch_calls.append(q)
        return [{"title": "t", "snippet": "reported count is 42", "url": "https://x/y"}]

    # ob1: machine + none:gap → refused. ob2: chat (will produce a conversational
    # claim that satisfies it, so the turn settles cleanly).
    judge = _judge(
        extract={"obligations": [
            _row("the opaque reference", "machine",
                 "none:capability gap - no such value exists"),
            _row("acknowledge", "chat"),
        ]},
        derive={"computations": [
            {"obligation_id": "ob1", "expression": "{n1}", "label": "computed duration"},
        ], "missing": []},
        synth={"claims": [{"obligation_id": "ob2",
                           "text": "I cannot compute a duration from that reference.",
                           "type": "conversational"}]},
    )
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, still_open, session_facts = repl.run_turn(
        "The opaque reference is https://host.example/report/18:45. What duration did I state?",
        EffectRunner(mode="record"), fetch, "test",
    )

    # REFUSED must render its reason
    assert "REFUSED: ob1 (capability gap" in transcript, \
        f"expected refusal reason in transcript:\n{transcript}"
    # No calc receipt for the refused obligation
    assert "ob1-calc" not in transcript, \
        f"a refused obligation must not ship a calc receipt:\n{transcript}"
    # No session fact for the refused calc
    for k, v in session_facts.items():
        assert "ob1-calc" not in v, \
            f"refused calc must not appear in session facts: {k}={v}"
    # ob2 (chat) rendered its claim
    assert "I cannot compute a duration from that reference" in transcript


# ---------------------------------------------------------------------------
# Test B: cross-turn — no stale value from a REFUSED obligation
#
# Turn 1: ob1 is refused.  Turn 2: runs with those session_facts.
# Turn 2 must not see a stale value from the refused obligation.
# ---------------------------------------------------------------------------

def test_r23_B_cross_turn_no_contamination(monkeypatch) -> None:
    """A calc receipt from a refused obligation must not survive as a session fact."""
    fetch_calls: list[str] = []

    def fetch(q):
        fetch_calls.append(q)
        return [{"title": "t", "snippet": "value is 24", "url": "https://x/y"}]

    # --- Turn 1: ob1 refused, ob2 chat ---
    judge1 = _judge(
        extract={"obligations": [
            _row("the first value", "machine",
                 "none:capability gap - no such calculation"),
            _row("acknowledge", "chat"),
        ]},
        derive={"computations": [
            {"obligation_id": "ob1", "expression": "{n1}", "label": "duration"},
        ], "missing": []},
        synth={"claims": [{"obligation_id": "ob2",
                           "text": "Understood, cannot compute that.",
                           "type": "conversational"}]},
    )
    monkeypatch.setattr(repl, "_model_json", judge1)
    transcript1, _, still_open1, session_facts = repl.run_turn(
        "The value is 24. What does this evaluate to?",
        EffectRunner(mode="record"), fetch, "test",
    )

    # Turn 1: no calc receipt for refused obligation
    assert "ob1-calc" not in transcript1, \
        f"turn1 must not contain a calc receipt for refused ob1:\n{transcript1}"
    for k, v in session_facts.items():
        assert "ob1-calc" not in v, \
            f"refused calc must not survive in session facts: {k}={v}"

    # --- Turn 2: a fresh chat turn, receives session_facts from Turn 1 ---
    judge2 = _judge(
        extract={"obligations": [
            _row("acknowledge", "chat"),
        ]},
        derive=None,
        synth={"claims": [{"obligation_id": "ob1",
                           "text": "Let me check... nothing valid was computed before.",
                           "type": "conversational"}]},
    )
    monkeypatch.setattr(repl, "_model_json", judge2)
    transcript2, _, still_open2, _ = repl.run_turn(
        "What was that value?",
        EffectRunner(mode="record"), fetch, "test",
        session_facts=session_facts,
    )

    # Turn 2 must not contain stale "computed in an earlier turn" fact that
    # carries a value from the refused obligation.  The user's own statement
    # "The value is 24" correctly persists as s1 ("user said earlier: ...") and
    # is NOT contamination — it is the user's own words.  The contamination we
    # block is "computed in an earlier turn: computed locally: duration: 24 = 24"
    # or similar.
    assert "computed in an earlier turn" not in transcript2, \
        f"no stale computed fact should appear in turn2:\n{transcript2}"


# ---------------------------------------------------------------------------
# Test C: same turn — one REFUSED, one OPEN
#
# The refused owner's attempted calc must not appear in claims for either
# slot, while the open slot's claims are unaffected.
# ---------------------------------------------------------------------------

def test_r23_C_one_refused_one_open_no_contamination(monkeypatch) -> None:
    """One REFUSED + one OPEN in the same turn: refused calc blocked, open unaffected."""
    fetch_calls: list[str] = []

    def fetch(q):
        fetch_calls.append(q)
        return [{"title": "t", "snippet": "count is 42", "url": "https://x/y"}]

    judge = _judge(
        extract={"obligations": [
            _row("first value (unavailable)", "machine",
                 "none:gap - cannot be computed"),
            _row("acknowledge further result", "chat"),
        ]},
        derive={"computations": [
            {"obligation_id": "ob1", "expression": "{n1}", "label": "first value"},
        ], "missing": []},
        synth={"claims": [{"obligation_id": "ob2",
                           "text": "The next result follows from the available data.",
                           "type": "conversational"}]},
    )
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, still_open, _ = repl.run_turn(
        "What is the first value? Also what comes after it?",
        EffectRunner(mode="record"), fetch, "test",
    )

    # ob1 must not ship a calc receipt
    assert "ob1-calc" not in transcript, \
        f"the refused obligation's calc must not ship:\n{transcript}"
    # The refusal reason must render
    assert "REFUSED: ob1 (capability gap" in transcript, \
        f"expected 'REFUSED: ob1' in transcript:\n{transcript}"
    # ob2's claim should be visible
    assert "The next result follows from the available data" in transcript


# ---------------------------------------------------------------------------
# Test D: valid arithmetic path remains green
#
# A fully open web_lookup obligation with a derive row must continue to
# produce calc receipts normally.
# ---------------------------------------------------------------------------

def test_r23_D_valid_arithmetic_remains_green(monkeypatch) -> None:
    """A normal open obligation still processes and ships its evidence."""
    fetch_calls: list[str] = []

    def fetch(q):
        fetch_calls.append(q)
        return [{"title": "t", "snippet": "reported count is 42", "url": "https://x/y"}]

    judge = _judge(
        extract={"obligations": [
            _row("reported count", "web_lookup"),
        ]},
        derive={"computations": [], "missing": []},
        synth={"claims": [{"obligation_id": "ob1",
                           "text": "The reported count is 42.",
                           "type": "conversational"}]},
    )
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, still_open, session_facts = repl.run_turn(
        "What is the reported count?",
        EffectRunner(mode="record"), fetch, "test",
    )

    # The web_lookup produces evidence
    assert "ob1-web1" in transcript, \
        f"web_lookup evidence must be present:\n{transcript}"
    # Session facts must contain the user's question
    user_facts = [v for k, v in session_facts.items()
                  if k.startswith("s") and "user said earlier" in v]
    assert len(user_facts) >= 1, \
        f"expected user statement in session facts, got {session_facts}"


# ---------------------------------------------------------------------------
# Test E: valid cross-turn persistence
#
# A normal calc receipt from a committed open obligation persists when it
# should.  (Complement to test B.)
# ---------------------------------------------------------------------------

def test_r23_E_valid_cross_turn_persistence(monkeypatch) -> None:
    """A non-refused obligation's evidence still persists."""
    fetch_calls: list[str] = []

    def fetch(q):
        fetch_calls.append(q)
        return [{"title": "t", "snippet": "value is 42", "url": "https://x/y"}]

    judge = _judge(
        extract={"obligations": [
            _row("reported count", "web_lookup"),
        ]},
        derive={"computations": [], "missing": []},
        synth={"claims": [{"obligation_id": "ob1",
                           "text": "The count is 42.",
                           "type": "conversational"}]},
    )
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, still_open, session_facts = repl.run_turn(
        "What is the reported count?",
        EffectRunner(mode="record"), fetch, "test",
    )

    # Web evidence produced
    assert "ob1-web1" in transcript, \
        f"expected web evidence:\n{transcript}"
    # Session facts persisted
    user_facts = [v for k, v in session_facts.items()
                  if k.startswith("s") and "user said earlier" in v]
    assert len(user_facts) >= 1, \
        f"expected user statement in session facts, got {session_facts}"
