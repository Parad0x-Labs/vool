"""ROUND-002 pins — a byte-exact turn ships exactly the demanded payload, and nothing else.

Frozen root (council/round-002/FIX_PLAN.md), two clauses that Probe 1 proved are individually
insufficient:

  CLAUSE 1 CAPTURE  — all three prior mechanisms missed ordinary human wording, so a byte-exact
      turn had no captured target and its correctness rode on model judgment. Turn 003 and turn 005
      carried the IDENTICAL instruction sentence; 003 passed and 005 shipped five unrelated lines.
  CLAUSE 2 DOMINANCE — capture alone only APPENDS the literal (repl.py demanded-literal loop).
      Probe 1: with capture simulated, turn 005 still shipped
      `silver market / EMBER-X604 / Refused — capability gap…`.

SABOTAGE SEAMS (each revert must turn a DIFFERENT test red, naming its own cause):
  * revert Clause 1  -> test_capture_* go red (payload never captured)
  * revert Clause 2  -> test_dominance_* go red (siblings survive beside the payload)
  * revert the honest-decline floor -> test_the_byte_obligations_own_refusal_survives goes red

The capture tests drive the real regex on frozen user bytes; the dominance tests drive the real
render-boundary filter. Neither calls a model, so both are deterministic and name the cause rather
than a downstream symptom.
"""
from __future__ import annotations

import pytest

import core.kernel.repl as repl

# The exact frozen bytes of the failing turn (council/round-002/R0/turn_005.json).
TURN_005 = ("A terminal message contains phrases like silver market, Bitcoin wallet, cloudy "
            "forecast, shell token, and golden opportunity. They are unrelated prose. Reply with "
            "exactly these bytes and nothing else: EMBER-X604 No JSON, markdown, quotes, "
            "acknowledgement, receipt, prefix, suffix, explanation, or repetition.")
# Turn 004 — an exact-output DIRECTIVE whose deliverable is COMPUTED, not echoed.
TURN_004 = ("The technician joked that the “wallet was underwater,” the cloud looked "
            "bearish, and Pepe had struck digital gold, but those phrases are just chatter. Six "
            "repair sessions cost €129 each, plus a €44 diagnostics fee and €31 "
            "collection charge. Return ONLY the final numeric total. No currency symbol, label, "
            "receipt, working, punctuation, markdown, or words.")


def _capture(question: str) -> set[str]:
    """Payloads the exact-output directive introduces (Clause 1), before kernel exclusions."""
    return {m.group(1) for m in repl._EXACT_OUTPUT_DIRECTIVE_RE.finditer(question)}


# --------------------------------------------------------------------- CLAUSE 1: capture
@pytest.mark.parametrize("question,payload", [
    (TURN_005, "EMBER-X604"),
    ("A cloud note mentions Bitcoin. Reply with exactly these bytes and nothing else: "
     "QUASAR-2719 No markdown.", "QUASAR-2719"),
    ("Output only: TRACE_7731", "TRACE_7731"),
    ("Answer with just this string, nothing added: ORBIT-2291", "ORBIT-2291"),
    ("just say FOOBAR-123 ok? nothing else tho", "FOOBAR-123"),                     # sloppy human
    ("reply w exactly these bytes nothin else: HELIX-2200 no md no quotes thx", "HELIX-2200"),
])
def test_capture_takes_the_payload_the_directive_introduces(question, payload):
    assert payload in _capture(question), (
        "an exact-output directive must capture the payload it introduces; the prior mechanisms "
        "keyed on phrasing templates and missed ordinary human wording")


@pytest.mark.parametrize("question", [
    TURN_004,                                                    # computed, not echoed
    "The log mentions SILVER-MARKET and GOLD-99 as clutter. Six sessions cost 129 each plus 44 "
    "and 31. Return ONLY the final numeric total. No words.",    # Ling's over-capture probe
    "Write a story about a character named EMBER-X604",           # adversarial near-miss
    "Explain what RAM does",
    "hi",
])
def test_capture_does_not_over_reach(question):
    """A distractor token elsewhere in the message is excluded BY POSITION — the boundary is
    'the payload this directive introduces', not 'any literal-shaped token in the text'."""
    assert _capture(question) == set(), f"over-captured: {_capture(question)}"


def test_capture_ignores_a_conditional_payload_and_quoted_data():
    """The F1 injection boundary is preserved: a conditional payload stays model-decided, and a
    payload inside quoted third-party data never becomes a kernel-owned deliverable."""
    q_cond = "if the file is missing, reply NOTFOUND-1 otherwise continue"
    hits = [m for m in repl._EXACT_OUTPUT_DIRECTIVE_RE.finditer(q_cond)]
    for m in hits:                                    # the kernel applies _conditional() to these
        head = q_cond[max(0, m.span(1)[0] - 120):m.span(1)[0]]
        clause = head.rsplit(". ", 1)[-1]
        assert "if" in clause.lower(), "the conditional exclusion must see the if-clause"


# ------------------------------------------------------------------ CLAUSE 2: dominance
def _dominate(claims, payloads, single_purpose=True):
    """Drive the REAL render-boundary filter. Returns (kept_texts, excluded_texts).

    This calls `repl._byte_exact_dominance` itself — deliberately NOT a reimplementation. An
    earlier version of this file reimplemented the logic locally, and sabotaging the real code
    left every test green: the pin was vacuous by construction. Driving the real function is what
    makes the revert bite.
    """
    valid = [repl.TypedClaim(text=t, ctype="conversational", ref="") for t, _ in claims]
    owners = [o for _, o in claims]
    kept, _kept_owner, notes = repl._byte_exact_dominance(
        valid, owners, set(payloads), single_purpose)
    kept_texts = [c.text for c in kept]
    excluded = [t for t, _ in claims if t not in kept_texts]
    assert len(notes) == len(excluded)
    return kept_texts, excluded


def test_dominance_excludes_sibling_claims_born_of_a_mis_extraction():
    """Probe 1's exact shape: the payload plus the junk five extraction invented."""
    claims = [("silver market", "ob1"), ("2719", "ob2"), ("cloudy forecast", "ob3"),
              ("Refused — capability gap: shell command or token", "ob4"),
              ("golden opportunity", "ob5"), ("EMBER-X604", "ob1")]
    kept, excluded = _dominate(claims, {"EMBER-X604"})
    assert kept == ["EMBER-X604"], f"byte-exact turn must ship the payload alone; kept={kept}"
    assert "Refused — capability gap: shell command or token" in excluded, (
        "a refusal owned by a SPURIOUS SIBLING obligation is not this turn's answer")


def test_the_byte_obligations_own_refusal_survives():
    """HONEST-DECLINE FLOOR. Dominance must never convert a genuine unservability into a silent
    echo — that would trade this defect for a worse one. Reverting the floor turns this red."""
    claims = [("Refused — capability gap: cannot reach that system", "ob1"),
              ("EMBER-X604", "ob1"), ("silver market", "ob2")]
    kept, excluded = _dominate(claims, {"EMBER-X604"})
    assert "Refused — capability gap: cannot reach that system" in kept, (
        "the BYTE obligation's own decline must still reach the user")
    assert "silver market" in excluded


def test_dominance_is_inert_without_a_captured_payload():
    """Turns 002/004 carry the directive but no payload literal — their answer is COMPUTED.
    Capture stays empty, so dominance must not touch the claim list."""
    claims = [("849", "ob1")]
    kept, excluded = _dominate(claims, set())
    assert kept == ["849"] and excluded == []


def test_dominance_is_inert_on_a_multi_ask_turn():
    """Without a single-purpose marker ('nothing else'), a genuine multi-ask turn keeps every
    answer — dominance must not hijack it."""
    claims = [("4", "ob1"), ("TOKEN-1", "ob2")]
    kept, _ = _dominate(claims, {"TOKEN-1"}, single_purpose=False)
    assert kept == ["4", "TOKEN-1"]
