"""Arithmetic is computed by VOOL whatever words are around it (installed 0.8, 2026-11-22).

Gate v3 on 07803d62d asks every run in fresh wording. "42+33?", "ok whtas 42+33?",
"quick q: calculate 8616 * 37 :)", "quick q: 38 + 71 = ?" and "mulitply 8835 by 73" all missed the
calculator, whose recognizer stripped a fixed list of openers and fillers, and reached local
qwen3:8b: 248,792 for 318,792, 644,205 for 644,955, once only an emoji. The sum is now found by its
shape. These tests build the wording from parts at random, so no fixed sentence is what passes.
"""
from __future__ import annotations

import random

import pytest

import core.agent_runtime.previous_reply_count as followup_lane
from core.task_router import evaluate_direct_math_request

OPENERS = ["", "hi ", "hey, ", "ok ", "quick q: ", "um ", "so ", "yo ", "hello! ", "btw ", "so quick maths: ", "right, "]
ASKS = ["", "what is ", "whats ", "what's ", "calculate ", "compute ", "can you do ", "can u do ",
        "how much is ", "tell me ", "work out ", "just give me "]
TYPOED_ASKS = ["whtas ", "waht is ", "calcualte ", "cmopute ", "whta's "]
ENDINGS = ["", "?", " ?", " = ?", "=", " for me", " pls", " please :)", " thx", " :)", " lol", "!!",
           " asap", " :D", " ;)", " for me thanks"]
OPERATORS = [("+", lambda a, b: a + b), ("-", lambda a, b: a - b), ("*", lambda a, b: a * b),
             (" x ", lambda a, b: a * b), (" times ", lambda a, b: a * b),
             (" plus ", lambda a, b: a + b), (" minus ", lambda a, b: a - b)]


def _value(reply: str | None) -> float:
    assert reply, "the runtime did not compute it"
    return float(reply.rsplit("=", 1)[1].strip().rstrip(".").replace(",", ""))


def _case(rng: random.Random) -> tuple[str, int]:
    a, b = rng.randint(2, 9999), rng.randint(2, 999)
    symbol, op = rng.choice(OPERATORS)
    spacing = rng.choice(["", " "]) if symbol.strip() in "+-*" else ""
    body = f"{a}{spacing}{symbol}{spacing}{b}"
    ask = rng.choice(ASKS + TYPOED_ASKS)
    text = f"{rng.choice(OPENERS)}{ask}{body}{rng.choice(ENDINGS)}"
    if rng.random() < 0.3:
        text = text.upper() if rng.random() < 0.5 else text.capitalize()
    return text, op(a, b)


@pytest.mark.parametrize("seed", range(12))
def test_a_sum_in_any_wording_is_computed_by_the_runtime(seed):
    rng = random.Random(f"shape-{seed}")
    for _ in range(25):
        text, expected = _case(rng)
        assert _value(evaluate_direct_math_request(text)) == expected, text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("mulitply 8835 by 73", 644955),
        ("multiply 8835 by 73 please", 644955),
        ("divide 90 by 6", 15),
        ("add 38 and 71", 109),
        ("subtract 3 from 10 pls", 7),
        ("what is (3+4)*2?", 14),
        ("-5 + 3?", -2),
        ("how much is 1,234 + 5?", 1239),
    ],
)
def test_the_operation_said_as_a_verb_is_computed(text, expected):
    assert _value(evaluate_direct_math_request(text)) == expected


@pytest.mark.parametrize(
    "text",
    [
        "I have 3 + 4 apples",
        "what's 2+2 in binary",
        "what is 3 + 4 in hex",
        "is 2+2 = 5",
        "2+2=4?",
        "is 3+4 more than 6",
        "explain 0.1+0.2",
        "what is 10/2 rounded",
        "whats 7 * 8 !",
        "3 x 4 cm",
        "windows 10 + office 365",
        "solve 2x+3=7",
        "my code: a = 3 + 4",
        "add 3 apples and 4",
        "call 555-0123",
        "remind me at 5-6pm",
        "provide 5 by 3",
        "3 plums 4",
    ],
)
def test_a_message_that_asks_something_else_is_left_to_the_model(text):
    assert evaluate_direct_math_request(text) is None


# --- a follow-up on the last computed result ------------------------------------------------------
# Gate v3 on 07803d62d: "now divide that by 8" and "that over 5?" missed the one sentence shape the
# follow-up lane knew; the model divided the wrong number, or asked what was meant.

FOLLOWUP_OPENERS = ["", "now ", "ok ", "and ", "ok now ", "so ", "hmm "]
FOLLOWUP_ENDINGS = ["", "?", " pls", " please", " :)", "!!", " for me", " thx"]
FOLLOWUP_SHAPES = [
    ("divide {ref} by {n}", lambda v, n: v / n),
    ("{ref} over {n}", lambda v, n: v / n),
    ("{ref} divided by {n}", lambda v, n: v / n),
    ("multiply {ref} by {n}", lambda v, n: v * n),
    ("mulitply {ref} by {n}", lambda v, n: v * n),
    ("{ref} times {n}", lambda v, n: v * n),
    ("add {n} to {ref}", lambda v, n: v + n),
    ("subtract {n} from {ref}", lambda v, n: v - n),
    ("what's {ref} over {n}", lambda v, n: v / n),
    ("divided by {n}", lambda v, n: v / n),
    ("times {n}", lambda v, n: v * n),
]
# "the reuslt": gate v3 on a7b184037 typed the pointer with a typo
REFS = ["that", "it", "the result", "the answer", "this", "the reuslt", "the anwser"]


@pytest.mark.parametrize("seed", range(6))
def test_a_follow_up_on_the_last_result_in_any_wording_is_computed(monkeypatch, seed):
    rng = random.Random(f"followup-{seed}")
    for _ in range(20):
        base = rng.randint(100, 99999)
        monkeypatch.setattr(followup_lane, "_previous_reply", lambda _sid, b=base: f"{b // 7} * 7 = {b}.")
        shape, op = rng.choice(FOLLOWUP_SHAPES)
        n = rng.choice([2, 3, 4, 5, 8, 10, 25])
        question = f"{rng.choice(FOLLOWUP_OPENERS)}{shape.format(ref=rng.choice(REFS), n=n)}{rng.choice(FOLLOWUP_ENDINGS)}"
        reply = followup_lane.previous_result_arithmetic(question, session_id="s")
        assert reply, question
        assert float(reply.rsplit("=", 1)[1]) == pytest.approx(op(base, n), abs=1e-3), question


@pytest.mark.parametrize(
    "question",
    ["is that over 50?", "was it over 5", "I have 3 apples over 5", "divide 10 by 2", "what is 5 over 3",
     "send 5 to that", "that over 5 and 6", "what is the weather over 5 days", "tell me a joke",
     "divide the theme by 2"],
)
def test_a_new_question_is_not_read_as_a_follow_up(monkeypatch, question):
    monkeypatch.setattr(followup_lane, "_previous_reply", lambda _sid: "8616 * 37 = 318792.")
    assert followup_lane.previous_result_arithmetic(question, session_id="s") is None
