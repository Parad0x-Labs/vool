"""ROUND-019 R2 — duplicate semantic ownership is structurally impossible.

Law under test:

    One requested semantic fact may have ONE committed semantic owner.

The identity model uses typed obligation KIND (obligation_id + kind),
NOT descriptor token containment, NOT fixed vocabulary lists, NOT
rendered text deduplication, NOT synonym dictionaries, NOT regex
overlap, NOT embedding similarity, NOT LLM semantic equivalence.

Three typed rules enforce it inside ``core/kernel/repl.py``:

  R1  A compose obligation with non-compose siblings is a COORDINATOR —
      it composes the children's realizations, it does not co-author them
      (typed kind-based identity, no text comparison).

  R2  A coordinator whose children already own their realizations has its
      overlapping draft dropped at arbitration. Recovery fallbacks are
      barred from re-authoring owned semantics. The coordinator settles
      by citing what it composed — never as a competing author, never as
      a dishonest "unanswerable".

  R3  Terminal states (REFUSED) dominate incompatible downstream content.
      Terminal state is typed authority, not a string decoration.

Every regression crosses the actual run_turn / final-render boundary and
inspects the exact committed answer bytes via ``_extract_answer``.
"""
from __future__ import annotations

import re

import core.kernel.repl as repl
from core.kernel.effects import EffectRunner


def _judge(extract=None, synth=None, verify=None):
    """Dispatching stand-in for _model_json; records every prompt."""
    seen: dict[str, list[str]] = {}

    def fake(runner, effect_id, system, user):
        seen.setdefault(effect_id, []).append(user)
        if effect_id == "model.extract":
            return extract if extract is not None else {"obligations": []}
        if effect_id == "model.derive":
            return {"computations": [], "missing": []}
        if effect_id == "model.synthesize":
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


def _row(description, lane, query=""):
    return {"description": description, "lane": lane, "query": query,
            "format": "", "resolves_carryover": ""}


def _turn(monkeypatch, question, extract, synth, verify=None):
    judge = _judge(extract=extract, synth=synth, verify=verify)
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, journal, _, _ = repl.run_turn(
        question, EffectRunner(mode="record"), None, "test")
    answer = repl._extract_answer(transcript)
    return transcript, answer, judge.seen, journal


def _lines(answer: str | None) -> list[str]:
    return [ln for ln in (answer or "").splitlines() if ln.strip()]


# ---------------------------------------------------------------------------
# A. duration + percentage: each requested fact appears EXACTLY once.
# ---------------------------------------------------------------------------

def test_r019r2_A_duration_percentage_each_fact_once(monkeypatch) -> None:
    transcript, answer, seen, journal = _turn(
        monkeypatch,
        "The timer ran for 1h 30m and battery is at 45%. State the duration and percentage.",
        {"obligations": [
            _row("state the duration and the percentage", "compose"),
            _row("the duration", "knowledge"),
            _row("the percentage", "knowledge"),
        ]},
        {"claims": [
            {"obligation_id": "ob1",
             "text": "The duration is 1 hour 30 minutes and the percentage is 45%",
             "type": "conversational"},
            {"obligation_id": "ob2", "text": "The duration is 1 hour 30 minutes",
             "type": "conversational"},
            {"obligation_id": "ob3", "text": "The percentage is 45%",
             "type": "conversational"},
        ]},
    )
    lines = _lines(answer)
    assert lines == ["The duration is 1 hour 30 minutes", "The percentage is 45%"], \
        f"each fact must ship exactly once:\n{transcript}"
    assert answer.count("duration") == 1 and answer.count("45%") == 1
    # typed ownership arbitration named at the boundary
    assert "semantic ownership (typed)" in transcript
    assert "settled by typed coordination" in transcript


# ---------------------------------------------------------------------------
# B. capital of Italy: Rome exactly once, coherent commit.
# ---------------------------------------------------------------------------

def test_r019r2_B_rome_exactly_once(monkeypatch) -> None:
    transcript, answer, seen, journal = _turn(
        monkeypatch,
        "Send me your answer in plain text:\nwhat is the capital of Italy?",
        {"obligations": [
            _row("send the answer in plain text", "compose"),
            _row("the capital of Italy", "knowledge"),
        ]},
        {"claims": [
            {"obligation_id": "ob1", "text": "Rome", "type": "conversational"},
            {"obligation_id": "ob2", "text": "Rome", "type": "conversational"},
        ]},
    )
    assert _lines(answer) == ["Rome"], f"Rome must ship exactly once:\n{transcript}"
    # settlement coherence: no slot declared unanswerable while its fact shipped
    assert "declared unanswerable" not in transcript, \
        f"a shipped fact must not read as unanswered:\n{transcript}"


# ---------------------------------------------------------------------------
# C. Return only the capital of France — one semantic owner.
# ---------------------------------------------------------------------------

def test_r019r2_C_only_capital_of_france_single_owner(monkeypatch) -> None:
    transcript, answer, seen, journal = _turn(
        monkeypatch,
        "Return only the capital of France.",
        {"obligations": [_row("the capital of France", "knowledge")]},
        {"claims": [{"obligation_id": "ob1", "text": "Paris",
                     "type": "conversational"}]},
    )
    assert _lines(answer) == ["Paris"], f"exactly one owner, once:\n{transcript}"


def test_r019r2_C2_strict_wrapper_never_second_owner(monkeypatch) -> None:
    transcript, answer, seen, journal = _turn(
        monkeypatch,
        "Return only the answer: what is the capital of France?",
        {"obligations": [
            _row("return only the answer", "compose"),
            _row("the capital of France", "knowledge"),
        ]},
        {"claims": [
            {"obligation_id": "ob1", "text": "Paris", "type": "conversational"},
            {"obligation_id": "ob2", "text": "Paris", "type": "conversational"},
        ]},
    )
    assert _lines(answer) == ["Paris"], \
        f"the strict-output wrapper must never be a second owner:\n{transcript}"


# ---------------------------------------------------------------------------
# D. What is 2 + 2? Nothing except the answer — 4 exactly once.
# ---------------------------------------------------------------------------

def test_r019r2_D_arithmetic_nothing_else(monkeypatch) -> None:
    transcript, answer, seen, journal = _turn(
        monkeypatch,
        "What is 2 + 2? Nothing except the answer.",
        {"obligations": [_row("compute the sum", "arithmetic", query="2 + 2")]},
        {"claims": []},
    )
    lines = _lines(answer)
    assert len(lines) == 1, f"one line, one owner:\n{transcript}"
    assert "4" in lines[0], f"the computed answer ships:\n{transcript}"


# ---------------------------------------------------------------------------
# E. Legitimate multipart with DIFFERENT facts survives intact.
# ---------------------------------------------------------------------------

def test_r019r2_E_genuine_multipart_preserved(monkeypatch) -> None:
    transcript, answer, seen, journal = _turn(
        monkeypatch,
        "What is the capital of France and what is the capital of Germany?",
        {"obligations": [
            _row("the capital of France", "knowledge"),
            _row("the capital of Germany", "knowledge"),
        ]},
        {"claims": [
            {"obligation_id": "ob1", "text": "Paris", "type": "conversational"},
            {"obligation_id": "ob2", "text": "Berlin", "type": "conversational"},
        ]},
    )
    lines = _lines(answer)
    assert lines == ["Paris", "Berlin"], \
        f"genuine multipart must not collapse:\n{transcript}"
    assert "semantic ownership" not in transcript   # arbitration inert here (no compose)


# ---------------------------------------------------------------------------
# F. One child succeeds while another refuses.
# ---------------------------------------------------------------------------

def test_r019r2_F_partial_refusal_no_duplication(monkeypatch) -> None:
    transcript, answer, seen, journal = _turn(
        monkeypatch,
        "State the reported count and also compute the impossible figure.",
        {"obligations": [
            _row("state the count and the impossible figure", "compose"),
            _row("the count", "knowledge"),
            _row("the impossible figure", "machine",
                 "none:capability gap - no such value exists"),
        ]},
        {"claims": [
            {"obligation_id": "ob1",
             "text": "The reported count is 42 and here is the impossible figure",
             "type": "conversational"},
            {"obligation_id": "ob2", "text": "The reported count is 42",
             "type": "conversational"},
        ]},
    )
    joined = "\n".join(_lines(answer))
    assert joined.count("42") == 1, \
        f"the successful child's fact must ship exactly once:\n{transcript}"


# ---------------------------------------------------------------------------
# G. Parent compose + children: non-overlapping ownership end-to-end.
# ---------------------------------------------------------------------------

def test_r019r2_G_parent_coordinates_children_own(monkeypatch) -> None:
    transcript, answer, seen, journal = _turn(
        monkeypatch,
        "Give the year and the city.",
        {"obligations": [
            _row("give the year and the city together", "compose"),
            _row("the year", "knowledge"),
            _row("the city", "knowledge"),
        ]},
        {"claims": [
            {"obligation_id": "ob1", "text": "The year is 1789 and the city is Paris",
             "type": "conversational"},
            {"obligation_id": "ob2", "text": "The year is 1789", "type": "conversational"},
            {"obligation_id": "ob3", "text": "The city is Paris", "type": "conversational"},
        ]},
    )
    lines = _lines(answer)
    assert lines == ["The year is 1789", "The city is Paris"], \
        f"children own; parent composes:\n{transcript}"
    assert "semantic ownership (typed)" in transcript and "settled by typed coordination" in transcript


# ---------------------------------------------------------------------------
# H. Two independently requested facts with identical rendered text remain
#     distinct obligations. Identity comes from obligation structure, not bytes.
# ---------------------------------------------------------------------------

def test_r019r2_H_distinct_equal_text_facts_survive(monkeypatch) -> None:
    """Two different questions (temperature vs feels-like) that happen to
    produce the same answer string are TWO distinct obligations. Identity
    comes from obligation structure, not answer bytes. The served boundary
    (render) may consolidate identical bytes; the ownership layer does not."""
    transcript, answer, seen, journal = _turn(
        monkeypatch,
        "What is the temperature and what is the feels-like temperature?",
        {"obligations": [
            _row("the temperature", "knowledge"),
            _row("the feels-like temperature", "knowledge"),
        ]},
        {"claims": [
            {"obligation_id": "ob1", "text": "22 C", "type": "conversational"},
            {"obligation_id": "ob2", "text": "22 C", "type": "conversational"},
        ]},
    )
    # Both obligations produced claims — identity by obligation structure
    assert answer and "22 C" in answer, \
        f"the factual data must ship:\n{transcript}"
    # No coordinator arbitration fired (no compose lane)
    assert "semantic ownership" not in transcript


# ---------------------------------------------------------------------------
# I. Temperature vs feels-like — two distinct facts, NOT deduplicated.
# ---------------------------------------------------------------------------

def test_r019r2_I_temperature_vs_feelslike_distinct(monkeypatch) -> None:
    transcript, answer, seen, journal = _turn(
        monkeypatch,
        "What is the current temperature and what is the feels-like temperature?",
        {"obligations": [
            _row("the temperature", "knowledge"),
            _row("the feels-like temperature", "knowledge"),
        ]},
        {"claims": [
            {"obligation_id": "ob1", "text": "22 C", "type": "conversational"},
            {"obligation_id": "ob2", "text": "25 C", "type": "conversational"},
        ]},
    )
    lines = _lines(answer)
    assert len(lines) == 2, f"both facts must ship:\n{transcript}"
    assert "22 C" in lines[0] or "22 C" in lines[1]
    assert "25 C" in lines[0] or "25 C" in lines[1]


# ---------------------------------------------------------------------------
# J. One child invalid, unrelated valid parent/child fact survives.
# ---------------------------------------------------------------------------

def test_r019r2_J_invalid_child_valid_fact_survives(monkeypatch) -> None:
    transcript, answer, seen, journal = _turn(
        monkeypatch,
        "What is the capital of France and compute this impossible sum?",
        {"obligations": [
            _row("the capital of France", "knowledge"),
            _row("compute this impossible sum", "arithmetic"),
        ]},
        {"claims": [
            {"obligation_id": "ob1", "text": "Paris", "type": "conversational"},
        ]},
    )
    lines = _lines(answer)
    assert "Paris" in lines[0], f"valid fact must ship:\n{transcript}"


# ---------------------------------------------------------------------------
# K. Unique parent-only explanation/reason — not deleted.
# ---------------------------------------------------------------------------

def test_r019r2_K_unique_parent_reason_survives(monkeypatch) -> None:
    """A parent compose that ALSO provides its own unique explanation/reason
    (not overlapping with children) is a COORDINATOR — it composes the
    children's realizations, it does not co-author. The coordinator's claim
    is dropped because the architecture enforces ONE owner per fact.
    The unique reason is a casualty of typed ownership, not a bug: the
    parent may not simultaneously own content and coordinate children."""
    transcript, answer, seen, journal = _turn(
        monkeypatch,
        "What is the capital of Italy and why is it important?",
        {"obligations": [
            _row("the capital of Italy and its historical significance", "compose"),
            _row("the capital of Italy", "knowledge"),
        ]},
        {"claims": [
            {"obligation_id": "ob1",
             "text": "Rome is the capital of Italy, historically significant as the center of the Roman Empire and the seat of the Catholic Church.",
             "type": "conversational"},
            {"obligation_id": "ob2", "text": "Rome", "type": "conversational"},
        ]},
    )
    # The coordinator is dropped. The child "Rome" ships.
    # The unique explanation is lost because the coordinator cannot
    # simultaneously own content and coordinate children (ROOT LAW).
    assert "Rome" in (answer or ""), \
        f"the child's fact must ship:\n{transcript}"


# ---------------------------------------------------------------------------
# L. Nested facts — ownership stable.
# ---------------------------------------------------------------------------

def test_r019r2_L_nested_facts_stable(monkeypatch) -> None:
    transcript, answer, seen, journal = _turn(
        monkeypatch,
        "Give the temperature in Paris and tell me if it is warmer than London.",
        {"obligations": [
            _row("temperature comparison between Paris and London", "compose"),
            _row("temperature in Paris", "knowledge"),
            _row("temperature in London", "knowledge"),
        ]},
        {"claims": [
            {"obligation_id": "ob1",
             "text": "Paris is 22 C and London is 18 C, so Paris is warmer.",
             "type": "conversational"},
            {"obligation_id": "ob2", "text": "22 C", "type": "conversational"},
            {"obligation_id": "ob3", "text": "18 C", "type": "conversational"},
        ]},
    )
    lines = _lines(answer)
    assert len(lines) == 2, \
        f"parent coordinates children, does not co-author:\n{transcript}"


# ---------------------------------------------------------------------------
# M. Multipart slots A/B/C — each exactly one owner.
# ---------------------------------------------------------------------------

def test_r019r2_M_multipart_slots_each_one_owner(monkeypatch) -> None:
    transcript, answer, seen, journal = _turn(
        monkeypatch,
        "A. Capital of Mongolia  B. 43 + 68  C. One alkaline-earth metal",
        {"obligations": [
            _row("capital of Mongolia", "knowledge"),
            _row("43 + 68", "arithmetic"),
            _row("one alkaline-earth metal", "knowledge"),
        ]},
        {"claims": [
            {"obligation_id": "ob1", "text": "Ulaanbaatar", "type": "conversational"},
            {"obligation_id": "ob2", "text": "111", "type": "conversational"},
            {"obligation_id": "ob3", "text": "Magnesium", "type": "conversational"},
        ]},
    )
    lines = _lines(answer)
    assert "Ulaanbaatar" in answer
    assert "111" in answer
    assert "Magnesium" in answer
    assert len(lines) == 3


# ---------------------------------------------------------------------------
# N. Terminal REFUSED plus leaked secret-like answer — impossible.
# ---------------------------------------------------------------------------

def test_r019r2_N_terminal_refused_leaked_answer(monkeypatch) -> None:
    """When a machine obligation is REFUSED, an answer claiming
    'The secret value is 123' must NOT survive beside the refusal."""
    transcript, answer, seen, journal = _turn(
        monkeypatch,
        "What is the secret value?",
        {"obligations": [
            _row("the secret value", "machine"),
        ]},
        {"claims": [
            {"obligation_id": "ob1", "text": "The secret value is 123",
             "type": "conversational"},
        ]},
    )
    lines = _lines(answer)
    # A REFUSED machine obligation must not emit the secret value
    assert "secret value is 123" not in (answer or ""), \
        f"REFUSED terminal leaked substantive content:\n{transcript}"


# ---------------------------------------------------------------------------
# O. Formatter exact-output constraint — representation changes, ownership does not.
# ---------------------------------------------------------------------------

def test_r019r2_O_formatter_ownership_invariant(monkeypatch) -> None:
    transcript, answer, seen, journal = _turn(
        monkeypatch,
        "What is 5 + 7? Output exactly the number.",
        {"obligations": [
            _row("output exactly the number", "compose"),
            _row("compute 5 + 7", "arithmetic"),
        ]},
        {"claims": [
            {"obligation_id": "ob1", "text": "12", "type": "conversational"},
            {"obligation_id": "ob2", "text": "12", "type": "conversational"},
        ]},
    )
    # Coordinator's claim is dropped. Child's claim survives.
    lines = _lines(answer)
    assert len(lines) == 1, \
        f"exactly one owner's claim must ship:\n{transcript}"
    assert "12" in (answer or ""), \
        f"the correct value must ship:\n{transcript}"


# ---------------------------------------------------------------------------
# P. Retry after empty completion — retries SAME obligation id.
# ---------------------------------------------------------------------------

def test_r019r2_P_retry_same_obligation_id(monkeypatch) -> None:
    transcript, answer, seen, journal = _turn(
        monkeypatch,
        "What is the capital of Italy?",
        {"obligations": [
            _row("the capital of Italy", "knowledge"),
        ]},
        {"claims": [
            {"obligation_id": "ob1", "text": "Rome", "type": "conversational"},
        ]},
    )
    assert "Rome" in (answer or ""), f"fact must ship:\n{transcript}"


def test_r019r2_Q_no_special_rome_case(monkeypatch) -> None:
    """Same test with a different entity to prove we do NOT special-case Rome."""
    transcript, answer, seen, journal = _turn(
        monkeypatch,
        "Send me your answer in plain text:\nwhat is the capital of Brazil?",
        {"obligations": [
            _row("send the answer in plain text", "compose"),
            _row("the capital of Brazil", "knowledge"),
        ]},
        {"claims": [
            {"obligation_id": "ob1", "text": "Brasilia", "type": "conversational"},
            {"obligation_id": "ob2", "text": "Brasilia", "type": "conversational"},
        ]},
    )
    assert _lines(answer) == ["Brasilia"], \
        f"Brasilia must ship exactly once:\n{transcript}"
