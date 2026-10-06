"""Records stated in one sitting reach the reader together, in the order they were said.

Measured on 200 archived dev capsules (zero provider calls): the packer renders capsule lines by
value, so one imported sitting's records were scattered over the capsule -- 2,666 of the 2,754
dates that had two or more lines were split, in every capsule. A question about an event met the
event's record dozens of lines away from the reply that answered it, and the reader said the
records do not say. The capsule now groups the lines whose delivered source receipt carries the
same statement time at the place of the group's best-valued line, in capture order.

Laws (core/context_retrieval.py, `_session_ordered_capsule_texts` at the capsule render):
* pure reordering: the same lines, the same header, the same characters;
* a group sits where its best-valued line sat; groups keep the packer's order among themselves;
* inside a group, capture order (recorded time, then write sequence) when every member has one,
  else the packer's order;
* a line with no statement time (a live chat turn) keeps its own place, so a live chat renders
  exactly as before;
* opt-in: the order applies only with VOOL_CAPSULE_SITTING_ORDER on. Off (the default) the capsule
  renders exactly in the packer's order. On the same 200 dev capsules the order moves 11,147 of
  11,533 lines, and for questions answered right with the packer's order 72 of 168 evidence lines
  move deeper, so it stays off until a paid A/B shows those answers stay right.

All names and sentences are synthetic.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest

import core.context_retrieval as cr
from tests.test_envelope_provenance_not_content_20261003 import _items
from tests.test_question_date_time_leg_20261002 import (  # noqa: F401
    _hash_backend,
    _ingest,
    _profile,
)

UTC = timezone.utc


def _ts(text: str) -> float:
    return datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp()


def _session(stamp: str, header: str, turns: list[str]) -> list[tuple[float, str]]:
    return [(_ts(stamp), f"Session date: {header}\n{turn}") for turn in turns]


_SMALL_TALK = [
    ("Ines", "The harbour wall got a fresh coat of paint this week."),
    ("Marek", "The bakery on the corner finally reopened after the flood."),
    ("Ines", "I repotted the fern on the balcony, it was root-bound."),
    ("Marek", "I switched my commute to the early tram."),
    ("Ines", "The library extended its opening hours on Thursdays."),
    ("Marek", "My brother started a podcast about chess openings."),
    ("Ines", "I tried a lemon tart recipe and it came out runny."),
    ("Marek", "I watched a documentary about deep sea vents."),
    ("Ines", "The ferry was late again because of the fog."),
    ("Marek", "I planted tomatoes, though it may be too late."),
]


# The sitting that holds the answer: the event is named in one turn, the answer is a later reply.
_RESIDENCY = [
    "Marek: The new tram line opened by the harbour today.",
    "Ines: Big news - I was chosen for the glassblowing residency at the harbour studio!",
    "Marek: Congratulations! Which piece are you working on there?",
    "Ines: A cobalt fishing float with a spiral inside. It's tricky, but I love it.",
    "Marek: Send me a picture of the residency piece when it is done.",
    "Ines: I will, the kiln for the residency opens on Friday.",
]
# Sittings before and after it talk about the same studio, so the packer's value order mixes them.
_BEFORE = [
    "Ines: I watched a glassblowing demo at the harbour studio.",
    "Marek: Glassblowing looks hard, all that heat.",
    "Ines: The studio offers a residency every spring, I might apply.",
]
_AFTER = [
    "Marek: How is the glassblowing residency going?",
    "Ines: The residency ends next week, I'm tired but happy.",
    "Marek: Will the harbour studio show your work?",
    "Ines: Yes, the studio exhibition opens in April.",
]
_TRANSCRIPT = (
    _session("2026-03-02T10:00:00", "10:00 am on 2 March, 2026",
             _BEFORE + [f"{name}: {text}" for name, text in _SMALL_TALK])
    + _session("2026-03-09T18:00:00", "6:00 pm on 9 March, 2026", _RESIDENCY)
    + _session("2026-03-20T10:00:00", "10:00 am on 20 March, 2026",
               _AFTER + [f"{name}: {text}" for name, text in _SMALL_TALK])
)
_SITTING = "9 March, 2026"


def _identity(texts, _times):
    return list(texts)


def _sitting_capsule(profile, chat: str, question: str, *, sitting_order: bool | None):
    """The capsule for one question through the real `inject_retrieved`, Capsule V2 on, with the
    sitting order switched on, off, or left unset (None, the default)."""
    from dataclasses import replace

    from core.context_capsule_v2 import resolve_budget
    from core.context_namespace import ensure_chat_namespace
    from core.memory.entries import resolve_memory_access_policy

    ensure_chat_namespace(chat, grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id=chat)
    budget = replace(
        resolve_budget(bucket="D", role="heavy_reasoning", output_reserve_tokens=2048,
                       evidence_target_tokens=2048, retrieval_ceiling_tokens=8192),
        min_score=0.25)
    env = {"VOOL_CONTEXT_CAPSULE_V2": "1"}
    if sitting_order is not None:
        env["VOOL_CAPSULE_SITTING_ORDER"] = "1" if sitting_order else "0"
    messages = cr.inject_retrieved(
        chat, question, [{"role": "user", "content": question}],
        access_policy=policy,
        source_context={"chat_id": chat, "runtime_home": str(profile)},
        env=env,
        budget=budget,
    )
    capsule = next((str(m.get("content") or "") for m in messages
                    if "<retrieved_context>" in str(m.get("content") or "")), "")
    return capsule, dict(cr.get_last_retrieval_telemetry())


def _sitting_positions(capsule: str) -> list[int]:
    return [index for index, item in enumerate(_items(capsule)) if _SITTING in item]


def _stated_day(item: str) -> str:
    match = re.search(r"stated:? (\d{4}-\d{2}-\d{2})", item)
    return match.group(1) if match else ""


def _first_appearance(days: list[str]) -> list[str]:
    return list(dict.fromkeys(day for day in days if day))


def _spoken_index(item: str) -> int:
    return next(index for index, turn in enumerate(_RESIDENCY) if turn.split(": ", 1)[1][:30] in item)


# --- the ordering law, unit level ---------------------------------------------------------------


def test_same_sitting_lines_gather_at_their_best_line_in_spoken_order() -> None:
    texts = ["A1", "B1", "A2", "C", "B2", "A3"]
    times = {
        "A1": (100.0, 5.0, 5), "A2": (100.0, 3.0, 3), "A3": (100.0, 4.0, 4),
        "B1": (200.0, 9.0, 9), "B2": (200.0, 8.0, 8),
    }
    assert cr._session_ordered_capsule_texts(texts, times) == ["A2", "A3", "A1", "B2", "B1", "C"]


def test_a_line_without_a_statement_time_keeps_its_place() -> None:
    texts = ["live1", "A1", "live2", "A2"]
    times = {"A1": (100.0, 1.0, 7), "A2": (100.0, 1.0, 6),
             "live1": (None, 50.0, 1), "live2": (None, 60.0, 2)}
    assert cr._session_ordered_capsule_texts(texts, times) == ["live1", "A2", "A1", "live2"]


def test_lines_of_a_live_chat_render_in_the_packer_order() -> None:
    texts = ["third", "first", "second"]
    times = {"first": (None, 1.0, 1), "second": (None, 2.0, 2), "third": (None, 3.0, 3)}
    assert cr._session_ordered_capsule_texts(texts, times) == texts


def test_a_sitting_with_an_unsequenced_member_keeps_the_packer_order() -> None:
    texts = ["A1", "A2", "A3"]
    times = {"A1": (100.0, 1.0, 9), "A2": (100.0, 1.0, None), "A3": (100.0, 1.0, 2)}
    assert cr._session_ordered_capsule_texts(texts, times) == texts


def test_spans_of_one_record_keep_their_packer_order_inside_the_sitting() -> None:
    texts = ["span-b", "other", "span-a", "earlier"]
    times = {"span-b": (100.0, 1.0, 5), "span-a": (100.0, 1.0, 5), "earlier": (100.0, 1.0, 4),
             "other": (200.0, 1.0, 1)}
    assert cr._session_ordered_capsule_texts(texts, times) == ["earlier", "span-b", "span-a", "other"]


@pytest.mark.parametrize("seed", range(6))
def test_the_ordering_is_a_permutation(seed: int) -> None:
    import random

    rng = random.Random(seed)
    texts = [f"line-{n}" for n in range(30)]
    rng.shuffle(texts)
    times = {
        text: (rng.choice([None, 100.0, 200.0, 300.0]), float(rng.randint(0, 3)), rng.randint(1, 40))
        for text in texts if rng.random() < 0.8
    }
    ordered = cr._session_ordered_capsule_texts(texts, times)
    assert sorted(ordered) == sorted(texts)
    assert len(ordered) == len(texts)


# --- the ordering law at the real capsule seam --------------------------------------------------


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("question", [
    "What was Ines working on during the glassblowing residency at the harbour studio?",
    "what was ines working on at the residency",
    "Which piece did Ines blow at the harbour studio residency?",
    "ines residency piece what was it",
])
def test_a_sitting_reaches_the_reader_together_and_in_spoken_order(tmp_path, monkeypatch, question) -> None:
    profile = _profile(tmp_path)
    _ingest(profile, "sitting", _TRANSCRIPT)
    capsule, telemetry = _sitting_capsule(profile, "sitting", question, sitting_order=True)
    packer_capsule, packer_telemetry = _sitting_capsule(profile, "sitting", question, sitting_order=False)
    assert packer_telemetry["session_order"] is False
    assert packer_telemetry["session_ordered_lines_moved"] == 0

    # The answer's sitting is in the capsule with other sittings, and the packer alone scatters it
    # or tells it out of order.
    before = _sitting_positions(packer_capsule)
    packer_items = _items(packer_capsule)
    assert len(before) >= 3 and len(packer_items) > len(before), packer_capsule
    packer_spoken = [_spoken_index(packer_items[index]) for index in before]
    assert (before != list(range(before[0], before[0] + len(before)))
            or packer_spoken != sorted(packer_spoken)), packer_capsule
    assert capsule != packer_capsule

    # Now: one contiguous run, in the order the turns were said, at the best line's place.
    after = _sitting_positions(capsule)
    assert after == list(range(after[0], after[0] + len(after))), capsule
    items = _items(capsule)
    spoken = [_spoken_index(items[index]) for index in after]
    assert spoken == sorted(spoken), capsule
    # Sittings keep the packer's order among themselves: each sits where its best line came first.
    ordered_days = [_stated_day(item) for item in items]
    assert _first_appearance(ordered_days) == _first_appearance(
        [_stated_day(item) for item in packer_items]), capsule

    # A pure reorder: the same header first, the same lines, the same characters.
    assert capsule.splitlines()[1] == packer_capsule.splitlines()[1]
    assert capsule.splitlines()[1].startswith("Distilled local facts.")
    assert sorted(_items(capsule)) == sorted(_items(packer_capsule))
    assert len(capsule) == len(packer_capsule)
    assert telemetry["session_order"] is True
    assert telemetry["session_ordered_lines_moved"] > 0


@pytest.mark.usefixtures("_hash_backend")
def test_a_live_chat_capsule_renders_exactly_as_the_packer_ordered_it(tmp_path, monkeypatch) -> None:
    profile = _profile(tmp_path)
    live = [(None, f"{name}: {text}") for name, text in _SMALL_TALK] + [
        (None, "Ines: I was chosen for the glassblowing residency at the harbour studio!"),
        (None, "Ines: The residency piece is a cobalt fishing float with a spiral inside."),
    ]
    _ingest(profile, "live", live)
    question = "What did Ines make during the glassblowing residency?"
    capsule, telemetry = _sitting_capsule(profile, "live", question, sitting_order=True)
    with monkeypatch.context() as patched:
        patched.setattr(cr, "_session_ordered_capsule_texts", _identity)
        packer_capsule, _ = _sitting_capsule(profile, "live", question, sitting_order=True)
    assert "residency" in capsule, capsule
    assert capsule == packer_capsule
    assert telemetry["session_order"] is True
    assert telemetry["session_ordered_lines_moved"] == 0


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("sitting_order", [None, False])
def test_the_sitting_order_is_off_unless_switched_on(tmp_path, monkeypatch, sitting_order) -> None:
    """Default and explicit off: the capsule is the packer's order, byte for byte."""
    monkeypatch.delenv("VOOL_CAPSULE_SITTING_ORDER", raising=False)
    profile = _profile(tmp_path)
    _ingest(profile, "sitting", _TRANSCRIPT)
    question = "What was Ines working on during the glassblowing residency at the harbour studio?"
    capsule, telemetry = _sitting_capsule(profile, "sitting", question, sitting_order=sitting_order)
    with monkeypatch.context() as patched:
        patched.setattr(cr, "_session_ordered_capsule_texts", _identity)
        packer_capsule, _ = _sitting_capsule(profile, "sitting", question, sitting_order=True)
    ordered_capsule, _ = _sitting_capsule(profile, "sitting", question, sitting_order=True)
    assert len(_sitting_positions(capsule)) >= 3, capsule
    assert capsule == packer_capsule
    assert ordered_capsule != capsule
    assert telemetry["session_order"] is False
    assert telemetry["session_ordered_lines_moved"] == 0
