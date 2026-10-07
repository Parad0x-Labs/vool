"""Whole turns of both speakers reach the reader beside the capsule (core/context_retrieval.py).

The capsule delivers term-anchored sentence windows bounded at 512 characters, so an answer that sits
outside the window - item 9 of a numbered list in an assistant reply, an amount at the end of a long
user statement - reached the reader cut or not at all (measured 2026-10-06 on the official
LongMemEval_S run 1: the answering record arrived whole on 23 of the 47 questions VOOL missed and a
rival answered). The whole-turn lane ranks layer-1 turns of both roles and delivers the best ones
whole, in the capsule's record format, under its own token allowance; a turn the capsule already
carries whole is not repeated. Everything below is invented for this contract.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import pytest

import core.context_retrieval as cr
from tests.test_question_date_time_leg_20261002 import _hash_backend, _profile
from tests.test_time_leg_follows_allowance_20261003 import _wide_capsule

_CABIN_LIST = (
    "Here is a list of weekend projects for the cabin, roughly in order of effort:\n"
    "1. Clear the gutters along the north roof\n2. Replace the cracked window latch in the loft\n"
    "3. Oil the hinges on the woodshed door\n4. Patch the screen on the back porch\n"
    "5. Rehang the wobbly coat rack by the stove\n6. Sweep the chimney before the cold sets in\n"
    "7. Seal the gap under the front door\n8. Restack the firewood under the lean-to\n"
    "9. Re-stain the porch railing with the cedar tone\n10. Tighten the dock cleats\n"
    "11. Label the fuse box circuits\n12. Swap the smoke alarm batteries"
)
_FILLER = [
    ("The tram was late again this morning.", "That sounds frustrating; maybe an earlier tram helps."),
    ("I repotted the fern on the balcony.", "Ferns like indirect light, so that spot should suit it."),
    ("My cousin visits next month from Porto.", "A visit is a good excuse to try new restaurants."),
    ("The gym raised its fees again.", "Some gyms offer off-peak plans that cost less."),
]


def _ingest_pairs(profile: Path, chat: str, sessions: list[list[tuple[str, str]]]) -> None:
    from datetime import datetime, timezone

    from core.context_namespace import ensure_chat_namespace
    from core.memory.entries import resolve_memory_access_policy
    from core.persistent_memory import append_conversation_event

    ensure_chat_namespace(chat, grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id=chat)
    for index, session in enumerate(sessions):
        stated = datetime(2026, 1 + index, 10, 10, tzinfo=timezone.utc).timestamp()
        for user, assistant in session:
            append_conversation_event(
                session_id=chat, user_input=user, assistant_output=assistant,
                source_context={"surface": "api", "platform": "api", "chat_id": chat,
                                "runtime_home": str(profile), "statement_at": stated},
                access_policy=policy,
            )


def _store(tmp_path: Path, chat: str) -> Path:
    profile = _profile(tmp_path)
    sessions = [[_FILLER[i % 4], _FILLER[(i + 1) % 4]] for i in range(5)]
    sessions[2].insert(1, ("Can you suggest some weekend projects for the cabin?", _CABIN_LIST))
    sessions[3].insert(0, (
        "Long week. The boiler made that knocking noise again on Tuesday, so I called the plumber we "
        "used last winter; he bled the radiators, swapped a valve and checked the pressure tank, "
        "then sat with me for a coffee and told me about his daughter's swimming gala, which ran "
        "late because of a fire alarm, and in the end the plumber's invoice came to $384.",
        "Glad the boiler is sorted; keep the invoice for the warranty."))
    _ingest_pairs(profile, chat, sessions)
    return profile


LIST_ASKS = [
    "You gave me a list of weekend projects for the cabin. What was the 9th one?",
    "Remind me what the ninth cabin project on your list was.",
    "what was number 9 on that cabin projects list you made",
    "cabin weekend projects list, item 9?",
    "Which project came ninth in the cabin list you suggested?",
]


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("question", LIST_ASKS)
def test_a_numbered_item_in_an_assistant_reply_reaches_the_reader_whole(tmp_path, question):
    profile = _store(tmp_path, "cabin")
    capsule, telemetry = _wide_capsule(profile, "cabin", question, target_tokens=2048)
    assert "9. Re-stain the porch railing with the cedar tone" in capsule, (telemetry.get("whole_turn_lines"), capsule)


_LONG_STORY = (
    "I finally called the plumber about the boiler repair after it kept knocking all week. "
    + " ".join([
        "He came round on Tuesday afternoon with his apprentice, who had just moved here from the coast "
        "and talked the whole time about surfing and the price of wetsuits.",
        "They bled every radiator upstairs, swapped the old valve under the stairs, checked the pressure "
        "tank twice and then drained the expansion vessel because the gauge looked odd.",
        "Afterwards we had coffee in the kitchen and he told me about his daughter's swimming gala, which "
        "ran two hours late because the fire alarm went off during the relay heats.",
        "The apprentice fixed the dripping garden tap while they were at it, and they took away the old "
        "valve and a bag of rusty fittings from the cellar so I would not have to.",
    ])
    + " When the bill arrived on Friday, the total for everything was $384."
)


def _story_store(tmp_path: Path, chat: str) -> Path:
    profile = _profile(tmp_path)
    sessions = [[_FILLER[i % 4], _FILLER[(i + 1) % 4]] for i in range(5)]
    sessions[3].insert(0, (_LONG_STORY, "Glad the boiler is sorted; keep the bill for the warranty."))
    _ingest_pairs(profile, chat, sessions)
    return profile


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("question", [
    "How much did the plumber charge for the boiler repair?",
    "what did the boiler repair by the plumber cost me",
    "boiler repair plumber cost?",
])
def test_a_value_far_from_the_question_words_reaches_the_reader(tmp_path, question):
    profile = _story_store(tmp_path, "story")
    capsule, telemetry = _wide_capsule(profile, "story", question, target_tokens=2048)
    assert "$384" in capsule, (telemetry.get("whole_turn_lines"), capsule)


@pytest.mark.usefixtures("_hash_backend")
def test_with_no_lane_allowance_no_lane_is_rendered(tmp_path, monkeypatch):
    # Mechanical off-switch check. In these small stores the capsule alone also finds the value, so
    # the lane's necessity is measured on the real run-1 seeds instead (instruments/
    # lme_capsule_replay.py: the answering turn arrived whole on 18 of 149 questions from the
    # capsule alone and on 89 with the lane).
    monkeypatch.setattr(cr, "_TURN_LANE_MAX_TOKENS", 0)
    profile = _story_store(tmp_path, "story")
    capsule, telemetry = _wide_capsule(profile, "story", "How much did the plumber charge for the boiler repair?",
                                       target_tokens=420)
    assert cr._TURN_LANE_HEADER not in capsule
    assert int(telemetry.get("whole_turn_lines") or 0) == 0


@pytest.mark.usefixtures("_hash_backend")
def test_at_the_smallest_allowance_the_lane_still_delivers_the_far_value(tmp_path):
    profile = _story_store(tmp_path, "story")
    capsule, telemetry = _wide_capsule(profile, "story", "How much did the plumber charge for the boiler repair?",
                                       target_tokens=420)
    assert "$384" in capsule.split(cr._TURN_LANE_HEADER, 1)[-1], (telemetry.get("whole_turn_lines"), capsule)


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("question", [
    "How much was the plumber's invoice?",
    "what did the plumber charge me",
    "The plumber who bled the radiators - how much was his bill?",
])
def test_an_amount_at_the_end_of_a_long_statement_reaches_the_reader(tmp_path, question):
    profile = _store(tmp_path, "boiler")
    capsule, telemetry = _wide_capsule(profile, "boiler", question, target_tokens=2048)
    assert "$384" in capsule, (telemetry.get("whole_turn_lines"), capsule)


@pytest.mark.usefixtures("_hash_backend")
def test_lane_lines_use_the_record_format_and_stay_within_their_allowance(tmp_path):
    profile = _store(tmp_path, "cabin")
    capsule, telemetry = _wide_capsule(profile, "cabin", LIST_ASKS[0], target_tokens=2048)
    lane = capsule.split(cr._TURN_LANE_HEADER, 1)[1] if cr._TURN_LANE_HEADER in capsule else ""
    assert lane, capsule
    heads = [line for line in lane.splitlines() if line.startswith("- ")]
    assert heads and all(line.startswith(("- user said (stated 2026-", "- assistant said (stated 2026-")) for line in heads)
    assert 0 < int(telemetry.get("whole_turn_tokens") or 0) <= cr._TURN_LANE_MAX_TOKENS


@pytest.mark.usefixtures("_hash_backend")
def test_a_turn_the_capsule_already_carries_whole_is_not_repeated(tmp_path):
    # One occurrence = one speaker, one day, one text: each reaches the reader at most once. The store
    # holds this sentence twice, said in February and in March; until v13 this asserted the TEXT
    # appeared at most once in all, which encoded the body-only dedup that also dropped the other
    # day's statement (tests/test_whole_turn_lane_v13_windows.py).
    profile = _store(tmp_path, "cabin")
    capsule, _ = _wide_capsule(profile, "cabin", "Did my cousin say when the visit from Porto is?", target_tokens=2048)
    seen = Counter()
    for line in capsule.splitlines():
        if "My cousin visits next month from Porto." in line:
            speaker = re.match(r"-\s*(user|assistant) said", line)
            day = re.search(r"\d{4}-\d{2}-\d{2}", line)
            seen[(speaker.group(1) if speaker else "", day.group(0) if day else "")] += 1
    assert seen and all(count == 1 for count in seen.values()), (seen, capsule)


def test_a_long_turn_is_cut_at_an_item_boundary_never_inside_an_item():
    text = "\n".join(f"{i}. Item number {i} with some words to fill the line out" for i in range(1, 60))
    body = cr._whole_turn_body(text, max_chars=400)
    assert body.endswith("[...]")
    kept = body[: -len("[...]")].rstrip().splitlines()
    assert all(line.endswith("fill the line out") for line in kept)


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("question", [
    "Which cabin project did you put ninth on the list?",
    "how much did the plumber charge",
    # paraphrase class: shares no word with any stored turn, which is exactly what phrases are for
    "Which trumpet mouthpiece did I order?",
])
def test_search_phrases_are_wanted_in_a_chat_that_holds_records(tmp_path, question):
    from core.context_namespace import ensure_chat_namespace

    profile = _store(tmp_path, "cabin")
    ensure_chat_namespace("cabin", grant_current_receipts=False)
    assert cr.search_expansion_wanted("cabin", question,
                                      source_context={"chat_id": "cabin", "runtime_home": str(profile)}) is True


@pytest.mark.usefixtures("_hash_backend")
def test_no_phrase_call_in_a_chat_without_records(tmp_path):
    from core.context_namespace import ensure_chat_namespace

    profile = _store(tmp_path, "cabin")
    ensure_chat_namespace("empty-chat", grant_current_receipts=False)
    assert cr.search_expansion_wanted("empty-chat", "How much did the plumber charge?",
                                      source_context={"chat_id": "empty-chat", "runtime_home": str(profile)}) is False
    assert cr.search_expansion_wanted("cabin", "   ", source_context={"chat_id": "cabin", "runtime_home": str(profile)}) is False
