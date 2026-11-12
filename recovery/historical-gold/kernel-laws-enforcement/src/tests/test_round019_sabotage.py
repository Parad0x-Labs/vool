"""ROUND-019 — sabotage pins S1-S3.

Each pin names ONE authority violation. Restoring the violation in
``core/kernel/repl.py`` must turn the corresponding pin RED; the repair keeps
it GREEN. They are deliberately independent of each other's mechanics:

  S1  restore overlapping parent + child semantic ownership (the coordinator's
      covering draft ships beside its children's realizations again)
  S2  allow a formatting-only instruction to become a semantic owner again
      (the pre-mint demotion removed)
  S3  allow recovery to author a fact already owned by another slot (the
      claimless-compose recovery guard removed)
"""
from __future__ import annotations

import core.kernel.repl as repl
from core.kernel.effects import EffectRunner


def _judge(extract=None, synth=None):
    seen: dict[str, list[str]] = {}

    def fake(runner, effect_id, system, user):
        seen.setdefault(effect_id, []).append(user)
        if effect_id == "model.extract":
            return extract
        if effect_id == "model.derive":
            return {"computations": [], "missing": []}
        if effect_id == "model.synthesize":
            return synth
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
    transcript, _, _, _ = repl.run_turn(
        question, EffectRunner(mode="record"), None, "test")
    return transcript, repl._extract_answer(transcript), judge.seen


def _lines(answer):
    return [ln for ln in (answer or "").splitlines() if ln.strip()]


# S1 — overlapping parent + child semantic ownership must be impossible.
def test_r019_S1_parent_child_overlap_red(monkeypatch) -> None:
    transcript, answer, _ = _turn(
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
    # the parent's covering draft must NOT ship beside its children
    assert "and the percentage is 45%" not in (_lines(answer)[0] if _lines(answer) else ""), \
        f"parent co-authored with children:\n{transcript}"
    assert len(_lines(answer)) == 2, \
        f"exactly the two atomic facts may ship:\n{transcript}"
    assert "coordinates, it does not co-author" in transcript


# S2 — a formatting-only instruction must never become a semantic owner.
def test_r019_S2_format_constraint_not_owner_red(monkeypatch) -> None:
    transcript, answer, _ = _turn(
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
    assert _lines(answer) == ["Rome"], f"one owner only:\n{transcript}"
    assert "formatting wrapper demoted to a format order" in transcript
    assert "declared unanswerable" not in transcript


# S3 — recovery must not re-author a fact another slot already owns.
def test_r019_S3_recovery_no_reauthoring_red(monkeypatch) -> None:
    transcript, answer, seen = _turn(
        monkeypatch,
        "Give the year and the city.",
        {"obligations": [
            _row("give the year and the city together", "compose"),
            _row("the year", "knowledge"),
            _row("the city", "knowledge"),
        ]},
        {"claims": [   # coordinator produced NOTHING; the children own their facts
            {"obligation_id": "ob2", "text": "The year is 1789", "type": "conversational"},
            {"obligation_id": "ob3", "text": "The city is Paris", "type": "conversational"},
        ]},
    )
    lines = _lines(answer)
    assert lines == ["The year is 1789", "The city is Paris"], \
        f"children's owned facts ship untouched:\n{transcript}"
    assert "claimless-compose recovery skipped" in transcript, \
        f"recovery re-authored owned semantics:\n{transcript}"
    assert "model.compose" not in seen, \
        f"recovery ran a raw authoring call for an owned fact: {seen.keys()}"
