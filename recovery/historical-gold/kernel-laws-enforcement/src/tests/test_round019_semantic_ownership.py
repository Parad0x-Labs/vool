"""ROUND-019 — duplicate semantic ownership is structurally impossible.

Law under test (council/round-019/OX-FIX_PLAN.md):

    One requested semantic fact may have ONE committed semantic owner.

Three authority rules enforce it inside ``core/kernel/repl.py``:

  R1  A formatting-only instruction never mints as a semantic obligation —
      a compose-lane row whose content words are all delivery/format
      vocabulary is demoted to a turn-level format order (pre-mint).
  R2  A compound obligation whose descriptor strictly covers sibling
      obligations COORDINATES them: when a covered sibling already owns its
      own realization, the coordinator's overlapping draft is dropped, its
      recovery fallbacks are barred from re-authoring it, and it settles by
      citing what it composed — never as a competing author, never as a
      dishonest "unanswerable".

Every regression crosses the actual run_turn / final-render boundary and
inspects the exact committed answer bytes via ``_extract_answer``.

The sabotage pins S1-S3 live in test_round019_sabotage.py.
"""
from __future__ import annotations

import core.kernel.repl as repl
from core.kernel.effects import EffectRunner


def _judge(extract=None, synth=None, compose_calls=None):
    """Dispatching stand-in for _model_json; records raw model.compose calls."""
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


def _turn(monkeypatch, question, extract, synth):
    judge = _judge(extract=extract, synth=synth)
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

def test_r019_A_duration_percentage_each_fact_once(monkeypatch) -> None:
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
    # ownership arbitration named at the boundary, parent coordinated not co-authored
    assert "semantic ownership" in transcript
    assert "coordinates, it does not co-author" in transcript
    assert "settled by coordination" in transcript


# ---------------------------------------------------------------------------
# B. plain-text wrapper + capital of Italy: Rome exactly once, coherent commit.
# ---------------------------------------------------------------------------

def test_r019_B_rome_exactly_once_wrapper_demoted(monkeypatch) -> None:
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
    # the format constraint was demoted BEFORE minting — it never became an owner
    assert "formatting wrapper demoted to a format order" in transcript
    # settlement coherence: no slot declared unanswerable while its fact shipped
    assert "declared unanswerable" not in transcript, \
        f"a shipped fact must not read as unanswered:\n{transcript}"


# ---------------------------------------------------------------------------
# C. Return only the capital of France — one semantic owner.
# ---------------------------------------------------------------------------

def test_r019_C_only_capital_of_france_single_owner(monkeypatch) -> None:
    transcript, answer, seen, journal = _turn(
        monkeypatch,
        "Return only the capital of France.",
        {"obligations": [_row("the capital of France", "knowledge")]},
        {"claims": [{"obligation_id": "ob1", "text": "Paris",
                     "type": "conversational"}]},
    )
    assert _lines(answer) == ["Paris"], f"exactly one owner, once:\n{transcript}"


def test_r019_C2_strict_wrapper_never_second_owner(monkeypatch) -> None:
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
    assert "formatting wrapper demoted to a format order" in transcript


# ---------------------------------------------------------------------------
# D. What is 2 + 2? Nothing except the answer — 4 exactly once.
# ---------------------------------------------------------------------------

def test_r019_D_arithmetic_nothing_else(monkeypatch) -> None:
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

def test_r019_E_genuine_multipart_preserved(monkeypatch) -> None:
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
    assert "semantic ownership" not in transcript   # arbitration inert here


# ---------------------------------------------------------------------------
# F. One child succeeds while another refuses: success ships once, refusal
#    stays honest, recovery does not duplicate the successful child.
# ---------------------------------------------------------------------------

def test_r019_F_partial_refusal_no_duplication(monkeypatch) -> None:
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
    assert "REFUSED: ob3" in transcript, f"refusal stays honest:\n{transcript}"
    # recovery did not re-author for the coordinator
    assert "compose recovery skipped" in transcript or "semantic ownership" in transcript
    assert "model.compose" not in seen, \
        f"recovery must not re-author owned semantics: {seen.keys()}"


# ---------------------------------------------------------------------------
# G. Parent compose + children: non-overlapping ownership end-to-end, with
#    terminal transaction states proving it.
# ---------------------------------------------------------------------------

def test_r019_G_parent_coordinates_children_own(monkeypatch) -> None:
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
    assert "semantic ownership" in transcript and "settled by coordination" in transcript
    # the transaction itself records non-overlapping ownership: every slot
    # reaches a terminal state — children closed on their own answers, parent
    # closed by coordination (never open, never unanswerable).
    terminal = {t["id"]: t["status"] for t in journal._turn_terminal}
    assert terminal == {"ob1": "closed", "ob2": "closed", "ob3": "closed"}, \
        f"terminal ownership must be coherent: {terminal}"
