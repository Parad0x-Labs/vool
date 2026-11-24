"""Every prompt of one turn says the same time, so a later tool round reuses what the model already read.

Intel smoke on 94b9dad5d (2026-11-24, qwen3:8b on a CPU that reads about 10 prompt tokens a second): "What is my
locker number?" built its first tool round at 11:57 and its third at 12:05. Each prompt read the clock again; the
clock line sits about 830 tokens in, so only 831 of the third round's 6,677 tokens were reused. Reading the rest
took over 11 minutes and the turn ran past the smoke's 15-minute limit. A turn is asked at one time: its prompts
now say the time the turn's first prompt was built. Another turn reads the clock again.
"""
from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

import core.prompt_normalizer as pn
from core.context_scope import ContextAccessPolicy

_RNG = random.Random()


class _Clock:
    def __init__(self, start: datetime) -> None:
        self.at = start

    def make(self):
        clock = self

        class _DateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return clock.at

        return _DateTime


@pytest.fixture
def clock(monkeypatch):
    start = datetime(2026, _RNG.randint(1, 12), _RNG.randint(1, 28), _RNG.randint(0, 22), 59, 30).astimezone()
    held = _Clock(start)
    monkeypatch.setattr(pn, "datetime", held.make())
    return held


def _prompt(session: str, turn: str, text: str):
    source_context = {"surface": "openclaw", "platform": "openclaw", "runtime_session_id": session,
                      "cancel_turn_id": turn}
    ContextAccessPolicy.for_request(session_id=session, source_context=source_context)
    request = pn.normalize_prompt(
        task=SimpleNamespace(task_id="task-1", task_summary=text),
        classification={"task_class": "unknown", "risk_flags": []},
        interpretation=SimpleNamespace(reconstructed_text=text, normalized_text=text, topic_hints=[],
                                       understanding_confidence=0.8),
        context_result=SimpleNamespace(
            local_candidates=[], swarm_metadata=[], retrieval_confidence_score=0.4,
            assembled_context=lambda: "", context_snippets=lambda: [],
            report=SimpleNamespace(retrieval_confidence=0.4, total_tokens_used=lambda: 0,
                                   to_dict=lambda: {"external_evidence_attachments": []}),
        ),
        persona=SimpleNamespace(persona_id="default", display_name="VOOL", tone="calm"),
        output_mode="plain_text", task_kind="conversation", trace_id="t", surface="openclaw",
        source_context=source_context,
    )
    return "\n".join(str(m.content) for m in request.messages)


def _ask() -> str:
    return _RNG.choice(("what is my {w} number?", "which {w} did I pick", "remind me of my {w} code")).format(
        w=_RNG.choice(("locker", "gate", "seat", "door")))


def test_a_later_round_of_the_same_turn_says_the_same_time(clock):
    session, turn, text = f"openclaw:{uuid.uuid4().hex[:12]}", uuid.uuid4().hex, _ask()
    first = _prompt(session, turn, text)
    clock.at += timedelta(minutes=_RNG.randint(2, 40))
    later = _prompt(session, turn, text)
    assert later == first
    assert clock.at.strftime("%H:%M") not in later


def test_the_next_turn_reads_the_clock_again(clock):
    session, text = f"openclaw:{uuid.uuid4().hex[:12]}", _ask()
    _prompt(session, uuid.uuid4().hex, text)
    clock.at += timedelta(minutes=_RNG.randint(2, 40))
    assert clock.at.strftime("%H:%M") in _prompt(session, uuid.uuid4().hex, text)


def test_a_prompt_with_no_turn_named_reads_the_clock(clock):
    first = pn.turn_reference_now({})
    clock.at += timedelta(minutes=3)
    assert pn.turn_reference_now({}) == first + timedelta(minutes=3)


def test_a_turn_held_past_an_hour_reads_the_clock_again(clock):
    context = {"runtime_session_id": "s", "cancel_turn_id": uuid.uuid4().hex}
    first = pn.turn_reference_now(context)
    clock.at += timedelta(minutes=30)
    assert pn.turn_reference_now(context) == first
    clock.at += timedelta(minutes=31)
    assert pn.turn_reference_now(context) == clock.at
