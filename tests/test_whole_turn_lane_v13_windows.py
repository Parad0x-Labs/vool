"""Whole-turn lane, v13 repairs (core/context_retrieval.py: _whole_turn_body, _whole_turn_lines).

1. Query-centred cut. A turn longer than its bound kept its PREFIX, so an answer past the bound never
   reached the reader however well the turn ranked. A longer turn now keeps the region the question
   asks about (its subject terms; the search phrases at half weight; a list position the question
   names), grown with contiguous neighbours up to the bound. Every cut end is marked "[...]", and a
   list item never loses its marker or its number. A turn carrying none of the subject terms keeps
   the prefix cut, and a turn under the bound is unchanged.
2. Same-occurrence dedup. The lane skipped a turn whenever its text appeared anywhere in the capsule,
   whoever said it and whenever: an assistant reply word-for-word equal to a user line vanished, and
   so did the same words said on another day. Only the same occurrence (speaker, day, text) is skipped.
3. Statement vs ingestion time. "stated <day>" fell back to the ingestion time. Now "stated" needs a real
   statement time; an ingestion time alone reads "recorded <day>"; with neither there is no date.

Everything below is invented for this contract: the kiln, the seed list, the canoe route, the chapel
roof, the glacier packing list, the ferry and the studio key appear nowhere in production code.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import core.context_retrieval as cr
from tests.test_question_date_time_leg_20261002 import _hash_backend, _profile
from tests.test_time_leg_follows_allowance_20261003 import _wide_capsule

_CAP = cr._TURN_LANE_UNIT_MAX_CHARS
_MARK = "[...]"


def _kept_lines(window: str) -> list[str]:
    """The source lines a window kept, without its cut marks."""
    text = window
    if text.startswith(_MARK + " "):
        text = text[len(_MARK) + 1:]
    if text.endswith(" " + _MARK):
        text = text[: -len(_MARK) - 1]
    return [line for line in text.split("\n") if line.strip()]


# ───────────────────────── 1. query-centred cut ─────────────────────────

_KILN_SENTENCE = ("Oh, and the kiln finally got fixed: the technician swapped the thermocouple and the relay, "
                  "and his bill came to 275 euros.")
_KILN_ANSWER = "his bill came to 275 euros"
_KILN_STORY = " ".join([
    "Last Saturday I took the early train to the coast with my sister.",
    "The carriage was packed with hikers and a choir on its way to a festival, so we stood most of the way.",
    "We walked along the harbour wall, watched the fishing boats unload, and ate chips on a bench while the "
    "gulls circled.",
    "My neighbour's spaniel escaped twice this month; both times he turned up at the bakery on the corner, "
    "waiting politely by the door for scraps.",
    "The baker now keeps a jar of biscuits for him behind the counter.",
    "Our book club is reading a slow Norwegian mystery, and half the group gave up after the second chapter.",
    "I keep going because the descriptions of the fjords are lovely, even if the plot barely moves.",
    "On Sunday I repainted the garden shed a pale sage colour, which took four coats because the old varnish "
    "kept bleeding through.",
    "The paint shop had run out of the brushes I like, so I used a cheap roller that shed fibres all over the "
    "door.",
    "It rained on Monday morning and the gutter over the back step overflowed again, splashing mud onto the "
    "clean shed.",
    "My sister thinks I should plant rosemary along the path so the bees have somewhere to go in summer.",
    "I ordered three small pots from a nursery that ships in recycled cardboard.",
    "The courier left them with the neighbour, and the spaniel had already chewed one label by the time I "
    "collected them.",
    "In the evening we watched an old film about a lighthouse keeper who talks to his radio every night.",
    "My sister fell asleep halfway through, and I finished the last of the ginger tea on my own.",
    "The next morning the street smelled of wet leaves and someone was practising scales on a trumpet.",
    _KILN_SENTENCE,
    "I fired a test batch of mugs that same evening and every one came out glossy.",
])

KILN_ASKS = [
    # original wording
    "How much did the technician charge to fix the kiln?",
    # clean paraphrases
    "What was the bill for the kiln repair?",
    "Remind me what the thermocouple replacement on my kiln cost.",
    "The kiln technician - what did he charge me in the end?",
    "Do you remember the price of getting the kiln fixed?",
    "What did I end up paying to have the relay and thermocouple swapped?",
    # sloppy / user-typed
    "kiln repair cost??",
    "how much was teh kiln fix",
    "what did the kiln guy charge",
    "KILN TECHNICIAN BILL",
    "thermocouple swap price what was it",
    # follow-up shaped (the subject rides in the follow-up itself)
    "and the kiln? what did that come to",
    "ok and how much for the thermocouple job then",
]


def test_the_late_answer_really_sits_past_the_bound():
    # Precondition of every family member below: the prefix cut cannot carry the answer.
    assert len(_KILN_STORY) > _CAP
    assert _KILN_STORY.index(_KILN_ANSWER) > _CAP
    assert _KILN_ANSWER not in cr._whole_turn_body(_KILN_STORY)


@pytest.mark.parametrize("question", KILN_ASKS)
def test_an_answer_past_the_bound_reaches_the_window(question):
    body = cr._whole_turn_body(_KILN_STORY, query=question)
    assert _KILN_ANSWER in body, body
    # the omitted head is marked, the window holds whole sentences of the source, the bound holds
    assert body.startswith(_MARK + " "), body
    kept = _kept_lines(body)
    assert len(kept) == 1 and kept[0] in _KILN_STORY
    assert _KILN_STORY[_KILN_STORY.index(kept[0]) - 2] in ".!?", kept[0][:60]
    assert len(body) <= _CAP + len(" " + _MARK)


@pytest.mark.parametrize("short", [
    "The kiln technician charged 275 euros.",
    "\n".join(f"{n}. Kiln shelf number {n}" for n in range(1, 13)),
    "x" * (_CAP - 7) + " kiln.",
])
def test_a_body_under_the_bound_is_unchanged(short):
    assert len(short) <= _CAP
    assert cr._whole_turn_body(short, query="How much did the kiln technician charge?") == short


@pytest.mark.parametrize("question", [
    "Which dentist pulled my wisdom tooth?",
    "",
    "   ",
])
def test_no_subject_term_in_the_turn_keeps_the_prefix_cut(question):
    body = cr._whole_turn_body(_KILN_STORY, query=question)
    assert body == cr._whole_turn_body(_KILN_STORY)
    assert body.startswith(_KILN_STORY[:60]) and body.endswith(" " + _MARK)
    assert _KILN_ANSWER not in body


def test_subject_terms_only_in_the_head_keep_a_prefix_shaped_window():
    body = cr._whole_turn_body(_KILN_STORY, query="What did the baker keep behind the counter for the spaniel?")
    assert body.startswith(_KILN_STORY[:60]), body[:80]
    assert "jar of biscuits" in body and body.endswith(" " + _MARK)
    assert _KILN_ANSWER not in body


@pytest.mark.parametrize("question", [
    "What did I tell you back then?",
    "what did you say about that back then",
    "Remember what I told you there?",
])
def test_frame_words_alone_never_move_the_window(question):
    # Adversarial near-miss: the turn's tail repeats the ask's own words ("tell you back then"), but
    # those are ask-frame vocabulary, not a subject. The cut stays the prefix.
    story = _KILN_STORY + " Anyway, that is what I was trying to tell you back then, there and then."
    body = cr._whole_turn_body(story, query=question)
    assert body == cr._whole_turn_body(story)
    assert not body.startswith(_MARK)


@pytest.mark.parametrize("question", [
    "What did the repair come to?",  # "repair" is nowhere in the turn
    # cross-turn follow-ups: the subject lives in the earlier turn, so only the search phrases the
    # model wrote for this turn carry it
    "how much was it in the end?",
    "and that one, what did it cost",
])
@pytest.mark.parametrize("phrases", [
    ("kiln technician bill",),
    ("thermocouple and relay replacement", "kiln bill"),
    ("cost of fixing the kiln",),
])
def test_search_phrases_move_the_window_when_the_question_cannot(question, phrases):
    assert _KILN_ANSWER not in cr._whole_turn_body(_KILN_STORY, query=question)
    assert _KILN_ANSWER in cr._whole_turn_body(_KILN_STORY, query=question, expansions=phrases)


def test_the_question_outweighs_a_search_phrase():
    # The question's own subject terms (bakery, spaniel) beat a phrase's two half-weight terms.
    body = cr._whole_turn_body(_KILN_STORY, query="Which bakery did the spaniel run to?",
                               expansions=("kiln bill",))
    assert "turned up at the bakery" in body and _KILN_ANSWER not in body


_SEED_ITEMS = [
    "Broad bean 'Aquadulce' - sow under cloches in late autumn",
    "Garlic 'Lautrec Wight' - plant cloves a hand apart",
    "Sweet pea 'Matucana' - soak overnight before sowing",
    "Shallot 'Red Sun' - push sets into firm soil",
    "Radish 'French Breakfast' - thin to two fingers apart",
    "Spinach 'Medania' - keep the bed moist",
    "Beetroot 'Chioggia' - pink and white rings when sliced",
    "Carrot 'Autumn King' - cover with fleece against root fly",
    "Parsnip 'Gladiator' - slow to germinate, mark the rows",
    "Lettuce 'Little Gem' - sow a short row every fortnight",
    "Pea 'Hurst Green Shaft' - give them hazel twigs to climb",
    "Leek 'Musselburgh' - trench and earth up for long white stems",
    "Onion 'Sturon' - weed often, they hate competition",
    "Kale 'Cavolo Nero' - net against pigeons",
    "Chard 'Bright Lights' - stems in yellow, orange and crimson",
    "Courgette 'Defender' - one plant feeds a family",
    "Runner bean 'Firestorm' - self-setting, good in hot spells",
    "Tomato 'Gardener's Delight' - pinch out the side shoots",
    "Cucumber 'Marketmore' - outdoor type, tolerates cool nights",
    "Squash 'Crown Prince' - blue-grey skin, stores until spring",
    "Sweetcorn 'Swift' - plant in blocks for pollination",
    "Pumpkin 'Jack Be Little' - palm-sized fruit for the windowsill",
    "Basil 'Genovese' - only outside after the last frost",
    "French bean 'Cobra' - climbing, purple flowers",
    "Celeriac 'Prinz' - needs a long season and plenty of water",
    "Florence fennel 'Finale' - bolts if the roots are disturbed",
    "Sea kale 'Lily White' - copes with salty coastal wind",
    "Winter purslane - a mild salad leaf under glass",
    "Corn salad 'Vit' - hardy, sow in late summer",
    "Green manure 'Phacelia' - dig in before it sets seed",
]
_SEED_LIST = ("Here are thirty seed varieties for the allotment, roughly in sowing order, each with a growing note:\n"
              + "\n".join(f"{n}. {item}" for n, item in enumerate(_SEED_ITEMS, 1)))

SEED_ASKS = [
    # original wording, then clean paraphrases (ordinal and lexical), then sloppy variants
    ("In the seed list you gave me, what was the 27th variety?", 27),
    ("Which variety did you put 29th on the allotment list?", 29),
    ("What was the 26th entry in your seed list?", 26),
    ("Remind me which seed you listed for salty coastal wind.", 27),
    ("What was the mild salad leaf you suggested for under glass?", 28),
    ("The 30th variety on that list - which was it?", 30),
    ("seed list 28th one??", 28),
    ("what was 27th on teh list you gave", 27),
    ("wich seed was for salty wind by the coast", 27),
    ("HARDY CORN SALAD WHICH ONE", 29),
    ("list u gave, 29th seed", 29),
]


@pytest.mark.parametrize("question,number", SEED_ASKS)
def test_a_named_list_position_past_the_bound_reaches_the_window_whole(question, number):
    item = f"{number}. {_SEED_ITEMS[number - 1]}"
    assert _SEED_LIST.index(item) > _CAP  # the prefix cut cannot carry it
    body = cr._whole_turn_body(_SEED_LIST, query=question)
    source = set(_SEED_LIST.split("\n"))
    kept = _kept_lines(body)
    assert item in kept, body
    # every kept line is a whole line of the source: no item lost its number or its tail
    assert all(line in source for line in kept), [line for line in kept if line not in source]
    assert body.startswith(_MARK + " ")


def test_a_position_beyond_the_list_keeps_the_head_of_the_list():
    # Negative control: no 45th item exists, so nothing binds; "varieties" sits in the heading line.
    body = cr._whole_turn_body(_SEED_LIST, query="What was the 45th variety in the list?")
    assert body.startswith("Here are thirty seed varieties") and body.endswith(" " + _MARK)
    assert "1. Broad bean 'Aquadulce' - sow under cloches in late autumn" in body


def test_a_list_under_the_bound_is_unchanged_by_a_position():
    short = "\n".join(_SEED_LIST.split("\n")[:12])
    assert cr._whole_turn_body(short, query="What was the 9th variety in your list?") == short


def test_a_decimal_quantity_is_not_a_list_position():
    # Adversarial near-miss: "3.5 cubic metres ..." reads like item 3 and repeats the ask's words; the
    # third step is "3. Screw the corner posts", near the head of the list.
    steps = ["Level the ground", "Lay weed membrane", "Screw the corner posts"] + [
        f"Fix side board number {n} with galvanised screws" for n in range(4, 41)]
    body_text = ("Here is the order for building the raised bed:\n"
                 + "\n".join(f"{n}. {step}" for n, step in enumerate(steps, 1))
                 + "\n3.5 cubic metres of topsoil fill the raised bed at the last step of the list.")
    assert body_text.index("3.5 cubic metres") > _CAP
    body = cr._whole_turn_body(body_text, query="What was the 3rd step in the raised bed list?")
    assert "3. Screw the corner posts" in body
    assert "3.5 cubic metres" not in body


def _canoe_route() -> str:
    lines = ["Route plan for the canoe trip, one stage per entry:"]
    for number in range(1, 26):
        if number == 22:
            lines += ["22. Camp on the heron island", "   - Pitch the tents on the gravel spit",
                      "   - Hang the food bag from the alder branch"]
        else:
            lines += [f"{number}. Paddle stage {number}", f"   - Check the map at lock {number}",
                      f"   - Refill water at jetty {number}"]
    return "\n".join(lines)


@pytest.mark.parametrize("question", [
    "Where did you say to hang the food bag on the heron island?",
    "On the heron island stage, what was the plan for the food?",
    "heron island food bag where",
    "where do i hang food on heron isle",
])
def test_a_window_never_opens_inside_an_item_or_cuts_one(question):
    route = _canoe_route()
    block = ("22. Camp on the heron island\n   - Pitch the tents on the gravel spit\n"
             "   - Hang the food bag from the alder branch")
    assert route.index(block) > _CAP
    body = cr._whole_turn_body(route, query=question)
    assert block in body, body
    kept = _kept_lines(body)
    assert re.match(r"\d+\. ", kept[0]), kept[0]  # opens on an item, never on its sub-line
    assert all(line in route.split("\n") for line in kept)


_ROOF_SENTENCES = [
    "The east wall has a long crack above the vestry door.", "Two slates slipped near the north valley.",
    "Moss is thick on the shaded side of the nave.", "The ridge tiles are sound but need repointing.",
    "Pigeons have been nesting behind the louvres.", "The downpipe by the porch is cracked at the shoe.",
    "Water stains show on the plaster in the side aisle.", "The weather vane is seized and points west.",
    "The parapet coping stones are loose in two places.", "Bats roost in the south transept, so no work in summer.",
    "The scaffold firm can start on the twelfth.", "Insurance wants photographs before and after.",
    "The churchwardens prefer natural slate to fibre cement.", "A survey of the timbers found no beetle damage.",
    "The hopper heads are cast iron and worth saving.", "Snow guards would help over the main entrance.",
    "The lightning conductor tape is intact.", "Some battens are soft near the eaves.",
    "The vestry roof drains onto the path in heavy rain.", "Ivy on the west gable should come off first.",
]
_ROOF_NOTES = "\n".join([
    "Notes from the survey of the old chapel roof:",
    "1. Gutters cleared in March",
    "2. " + " ".join(_ROOF_SENTENCES) + " The roofer quoted 1,180 pounds for the new lead flashing.",
    "3. Bell tower left for next year",
])


@pytest.mark.parametrize("question", [
    "What did the roofer quote for the flashing?",
    "roofer quote flashing??",
])
def test_an_item_longer_than_the_bound_keeps_its_number(question):
    body = cr._whole_turn_body(_ROOF_NOTES, 600, query=question)
    assert "The roofer quoted 1,180 pounds for the new lead flashing." in body
    # the window opens inside item 2, so the item's number rides in front of what it kept
    assert body.startswith(f"{_MARK} 2. {_MARK} "), body[:40]
    assert len(body) <= 600 + len(" " + _MARK)


def test_a_cut_never_separates_a_secret_from_its_label(monkeypatch):
    # A window may open mid-turn, so the lane redacts the whole turn before any cut. Simulated here
    # by a cut that opens right after a secret's label: the value must not ride the lane line.
    secret = "hunter2-glacier"
    monkeypatch.setattr(cr, "_whole_turn_body",
                        lambda text, max_chars=None, **_kw: text.split("password:", 1)[-1].strip())
    turn = _occ("user", f"For the shed alarm my password: {secret} and the spare fob is on the hook.",
                stated=_noon(2026, 3, 14))
    lines = _lane([turn], "")
    assert lines and secret not in lines[0], lines


# ───────────────────────── 2. same-occurrence dedup ─────────────────────────

def _noon(year: int, month: int, day: int) -> float:
    return datetime(year, month, day, 12, tzinfo=timezone.utc).timestamp()


def _occ(role: str, body: str, *, stated: float | None = None, recorded: float | None = None,
         authority: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        occurrence_id=f"{role}-{len(body)}-{stated}", role=role, body=body, statement_at=stated,
        recorded_at=recorded if recorded is not None else _noon(2026, 9, 30),
        authority=authority or ("assistant-output" if role == "assistant" else "observed-user-statement"))


def _evidence_line(occurrence: SimpleNamespace, text: str | None = None, note: str = "") -> str:
    """A capsule evidence line for *occurrence*, labelled by the capsule's own labellers."""
    time_label = cr._occurrence_time_label(occurrence)
    return "- {}{}{}: {}".format(cr._occurrence_authority_label(occurrence), note,
                                 f" ({time_label})" if time_label else "", occurrence.body if text is None else text)


def _distilled_line(occurrence: SimpleNamespace) -> str:
    """A distilled capsule line: speaker label, text, UTC provenance suffix (_provenance_suffix)."""
    stated = occurrence.statement_at
    stamp, kind = (stated, "stated") if stated is not None else (occurrence.recorded_at, "recorded")
    day = datetime.fromtimestamp(stamp, tz=timezone.utc).strftime("%Y-%m-%d")
    return f"- {occurrence.role} said: {occurrence.body} ({kind}: {day})"


def _capsule(*lines: str) -> str:
    return "<retrieved_context>\nRecords from earlier in this chat.\n" + "\n".join(lines) + "\n</retrieved_context>"


def _lane(occurrences: list[SimpleNamespace], delivered: str, **kwargs) -> list[str]:
    lines, _tokens = cr._whole_turn_lines([[o] for o in occurrences], delivered_text=delivered, **kwargs)
    return lines


_DAY = _noon(2026, 3, 14)

#: (shape name, text, how the capsule carries the first speaker's occurrence of it)
DEDUP_SHAPES = [
    ("evidence line", "The ferry to the island leaves at 07:40 from pier six.", "evidence"),
    ("imported history", "Water the orchids every ten days in winter.", "imported"),
    ("reported prefix", "Mara: the choir rehearsal moved to the crypt.", "prefix"),
    ("distilled line", "The allotment gate code changed to 4471.", "distilled"),
    ("multi-line list", "Bring these to the dig:\n1. Trowel\n2. Kneeling pad\n3. Finds bags", "evidence"),
    ("case and spacing", "Our van insurance renews on the first of May.", "sloppy"),
]


def _carried(occurrence: SimpleNamespace, how: str) -> str:
    if how == "imported":
        return _evidence_line(SimpleNamespace(**{**vars(occurrence), "authority": "imported-historical"}))
    if how == "prefix":
        return _evidence_line(occurrence, note=' [reported source prefix "Mara:"]')
    if how == "distilled":
        return _distilled_line(occurrence)
    if how == "sloppy":
        return _evidence_line(occurrence, text="  " + occurrence.body.upper().replace(" ", "  ") + " ")
    return _evidence_line(occurrence)


@pytest.mark.parametrize("first", ["user", "assistant"])
@pytest.mark.parametrize("shape,text,how", DEDUP_SHAPES)
def test_identical_words_from_the_other_speaker_stay_in_the_lane(shape, text, how, first):
    other = "assistant" if first == "user" else "user"
    carried = _occ(first, text, stated=_DAY)
    echoed = _occ(other, text, stated=_DAY)
    lines = _lane([echoed], _capsule(_carried(carried, how)))
    assert lines == [f"- {other} said (stated 2026-03-14): {text}"], (shape, lines)


@pytest.mark.parametrize("first", ["user", "assistant"])
@pytest.mark.parametrize("shape,text,how", DEDUP_SHAPES)
def test_the_same_occurrence_is_not_repeated(shape, text, how, first):
    occurrence = _occ(first, text, stated=_DAY)
    assert _lane([occurrence], _capsule(_carried(occurrence, how))) == [], shape


@pytest.mark.parametrize("shape,text,how", DEDUP_SHAPES)
def test_the_same_words_on_another_day_stay(shape, text, how):
    earlier = _occ("user", text, stated=_noon(2026, 2, 3))
    later = _occ("user", text, stated=_DAY)
    assert _lane([later], _capsule(_carried(earlier, how))) == [f"- user said (stated 2026-03-14): {text}"], shape


def test_an_ingestion_dated_occurrence_is_matched_by_its_recorded_day():
    occurrence = _occ("assistant", "The spare paddle is in the boathouse rafters.", recorded=_noon(2026, 5, 2))
    assert _evidence_line(occurrence).startswith("- assistant said (recorded 2026-05-02): ")
    assert _lane([occurrence], _capsule(_evidence_line(occurrence))) == []


def test_an_unattributed_copy_is_not_the_same_occurrence():
    text = "The ferry to the island leaves at 07:40 from pier six."
    lines = _lane([_occ("user", text, stated=_DAY)], _capsule(f"- relevant context: {text}"))
    assert lines == [f"- user said (stated 2026-03-14): {text}"]


@pytest.mark.parametrize("quote", [
    'You told me: "{text}" I will remind you an hour before.',
    "Noted - {text}",
    "So, to repeat what you said: {text} Anything else?",
])
def test_a_quote_inside_the_other_speakers_line_does_not_hide_a_turn(quote):
    # Adversarial near-miss: the assistant line carries the user's words verbatim on the same day.
    # Speaker identity is the seam: the user's own statement still reaches the reader as "user said".
    text = "The ferry to the island leaves at 07:40 from pier six."
    capsule = _capsule(_evidence_line(_occ("assistant", quote.format(text=text), stated=_DAY)))
    assert _lane([_occ("user", text, stated=_DAY)], capsule) == [f"- user said (stated 2026-03-14): {text}"]


def test_a_partial_copy_does_not_hide_the_whole_turn():
    # Negative control (unchanged law): the capsule carries a window of the turn, the lane the turn.
    text = "Bring these to the dig:\n1. Trowel\n2. Kneeling pad\n3. Finds bags"
    occurrence = _occ("assistant", text, stated=_DAY)
    lines = _lane([occurrence], _capsule(_evidence_line(occurrence, text="Bring these to the dig: 1. Trowel")))
    assert lines == [f"- assistant said (stated 2026-03-14): {text}"]


def test_with_nothing_delivered_every_turn_is_kept():
    occurrences = [_occ("user", "The ferry leaves at 07:40.", stated=_DAY),
                   _occ("assistant", "The ferry leaves at 07:40.", stated=_DAY)]
    assert _lane(occurrences, "") == ["- user said (stated 2026-03-14): The ferry leaves at 07:40.",
                                      "- assistant said (stated 2026-03-14): The ferry leaves at 07:40."]


# ───────────────────────── 3. statement vs ingestion time labels ─────────────────────────

@pytest.mark.parametrize("role", ["user", "assistant"])
@pytest.mark.parametrize("stated,recorded,day", [
    (_noon(2023, 5, 20), _noon(2026, 10, 1), "2023-05-20"),
    (_noon(2024, 2, 29), _noon(2024, 3, 1), "2024-02-29"),
    (_noon(2026, 1, 1), _noon(2026, 1, 1), "2026-01-01"),
])
def test_a_statement_time_is_labelled_stated(role, stated, recorded, day):
    lines = _lane([_occ(role, "Moved the hives to the clover field.", stated=stated, recorded=recorded)], "")
    assert lines == [f"- {role} said (stated {day}): Moved the hives to the clover field."]


@pytest.mark.parametrize("role", ["user", "assistant"])
@pytest.mark.parametrize("recorded,day", [
    (_noon(2026, 10, 6), "2026-10-06"),
    (_noon(2019, 7, 4), "2019-07-04"),
    (_noon(2025, 12, 31), "2025-12-31"),
])
def test_an_ingestion_time_alone_is_labelled_recorded(role, recorded, day):
    lines = _lane([_occ(role, "Moved the hives to the clover field.", recorded=recorded)], "")
    assert lines == [f"- {role} said (recorded {day}): Moved the hives to the clover field."]
    assert "stated" not in lines[0]


def test_with_no_time_at_all_there_is_no_date():
    occurrence = SimpleNamespace(role="user", body="Moved the hives.", statement_at=None, recorded_at=None)
    assert _lane([occurrence], "") == ["- user said: Moved the hives."]


def test_an_epoch_statement_time_is_still_a_statement_time():
    # Adversarial near-miss: a falsy but real statement time. The old fallback ("statement_at or
    # recorded_at") replaced it with the ingestion day and still called that day "stated".
    lines = _lane([_occ("user", "Planted the quince.", stated=0.0, recorded=_noon(2026, 10, 6))], "")
    assert lines == ["- user said (stated 1970-01-01): Planted the quince."]


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 1e20, "yesterday"])
def test_an_unreadable_statement_time_falls_back_to_recorded(bad):
    lines = _lane([_occ("user", "Planted the quince.", stated=bad, recorded=_noon(2026, 10, 6))], "")
    assert lines == ["- user said (recorded 2026-10-06): Planted the quince."]


# ───────────────────────── end to end through the capsule ─────────────────────────

_SMALL_TALK = [
    ("The tide was very low at the slipway today.", "Low tides are a good time to look for crabs."),
    ("I started learning the accordion again.", "Short daily practice tends to stick best."),
    ("Our street has new lamp posts going up.", "Brighter lamps should make evening walks nicer."),
    ("The library now opens late on Thursdays.", "Late opening is handy after work."),
]


def _ingest(profile: Path, chat: str, sessions: list[list[tuple[str, str]]], *, stated: bool = True) -> None:
    from core.context_namespace import ensure_chat_namespace
    from core.memory.entries import resolve_memory_access_policy
    from core.persistent_memory import append_conversation_event

    ensure_chat_namespace(chat, grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id=chat)
    for index, session in enumerate(sessions):
        context = {"surface": "api", "platform": "api", "chat_id": chat, "runtime_home": str(profile)}
        if stated:
            context["statement_at"] = _noon(2026, 1 + index, 12)
        for user, assistant in session:
            append_conversation_event(session_id=chat, user_input=user, assistant_output=assistant,
                                      source_context=context, access_policy=policy)


def _capsule_with_phrases(profile: Path, chat: str, question: str, phrases: tuple[str, ...],
                          target_tokens: int = 2048) -> str:
    from core.context_capsule_v2 import resolve_budget
    from core.context_namespace import ensure_chat_namespace
    from core.context_retrieval import inject_retrieved
    from core.memory.entries import resolve_memory_access_policy

    ensure_chat_namespace(chat, grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id=chat)
    budget = replace(
        resolve_budget(bucket="D", role="heavy_reasoning", output_reserve_tokens=2048,
                       evidence_target_tokens=target_tokens, retrieval_ceiling_tokens=8192),
        min_score=0.25)
    messages = inject_retrieved(
        chat, question, [{"role": "user", "content": question}], access_policy=policy,
        source_context={"chat_id": chat, "runtime_home": str(profile), "search_expansions": list(phrases)},
        env={"VOOL_CONTEXT_CAPSULE_V2": "1"}, budget=budget)
    return next((str(m.get("content") or "") for m in messages
                 if "<retrieved_context>" in str(m.get("content") or "")), "")


def _lane_section(capsule: str) -> str:
    return capsule.split(cr._TURN_LANE_HEADER, 1)[1] if cr._TURN_LANE_HEADER in capsule else ""


def _kiln_store(tmp_path: Path, chat: str) -> Path:
    profile = _profile(tmp_path)
    sessions = [[_SMALL_TALK[i % 4], _SMALL_TALK[(i + 1) % 4]] for i in range(4)]
    sessions[2].insert(1, (_KILN_STORY, "Glad the kiln is working again."))
    _ingest(profile, chat, sessions)
    return profile


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("question", [KILN_ASKS[0], KILN_ASKS[1], KILN_ASKS[6], KILN_ASKS[9], KILN_ASKS[11]])
def test_lane_delivers_the_late_answer_of_a_long_user_turn(tmp_path, question):
    profile = _kiln_store(tmp_path, "kiln")
    capsule, telemetry = _wide_capsule(profile, "kiln", question, target_tokens=2048)
    lane = _lane_section(capsule)
    assert _KILN_ANSWER in lane, (telemetry.get("whole_turn_lines"), capsule)


@pytest.mark.usefixtures("_hash_backend")
def test_lane_window_follows_the_search_phrases(tmp_path):
    profile = _kiln_store(tmp_path, "kiln")
    question = "What did my sister and I pay for the repair?"  # only "sister" is in the turn: its head
    plain = _capsule_with_phrases(profile, "kiln", question, ())
    phrased = _capsule_with_phrases(profile, "kiln", question, ("kiln technician bill", "thermocouple relay"))
    assert _KILN_ANSWER not in _lane_section(plain), plain
    assert _KILN_ANSWER in _lane_section(phrased), phrased


_GLACIER_ITEMS = [
    "Crampons with anti-balling plates", "Ice axe with a wrist leash", "Harness and two screwgate karabiners",
    "Helmet with a headlamp clip", "Glacier glasses, category four", "Spare prescription lenses",
    "Merino base layers, two sets", "Fleece mid layer", "Insulated belay jacket", "Hardshell trousers",
    "Gaiters", "Liner gloves", "Mitts on idiot cords", "Buff and a sun hat", "Factor fifty lip balm",
    "Head torch and spare batteries", "Thermos flask", "Two litres of water bottles", "Water filter straws",
    "Freeze-dried meals for four days", "Stove and a gas canister", "Long spoon", "Map in a waterproof case",
    "Compass with a mirror", "Satellite messenger", "First-aid kit with blister plasters", "Whistle",
    "Prusik loops", "Ice screws, three of them", "A pulley for crevasse rescue", "Snow stakes",
    "Thin cord for the tent guys", "Down sleeping bag rated to minus ten", "Inflatable mat and its patch kit",
    "Earplugs for the hut", "Paper copies of the permits", "Cheese and oatcakes for the summit",
    "A small notebook and a pencil", "Duct tape wrapped round a pole", "Cash for the hut warden",
]
_GLACIER_BAGS = ["goes in the red duffel", "goes in the blue rucksack", "rides in the lid pocket",
                 "clips to the hip belt"]
_GLACIER_LIST = ("Here is my packing list for the glacier trek, keep it safe for me:\n"
                 + "\n".join(f"{n}. {item} - {_GLACIER_BAGS[n % 4]}" for n, item in enumerate(_GLACIER_ITEMS, 1)))


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("question", [
    "In my packing list, what was the 37th item?",
    "what was 37th on my glacier packing list",
    "Which thing came 37th in the packing list I gave you?",
])
def test_lane_delivers_a_late_item_of_a_long_user_list(tmp_path, question):
    item = f"37. {_GLACIER_ITEMS[36]} - {_GLACIER_BAGS[37 % 4]}"
    assert _GLACIER_LIST.index(item) > _CAP
    profile = _profile(tmp_path)
    sessions = [[_SMALL_TALK[i % 4]] for i in range(3)]
    sessions[1].insert(0, (_GLACIER_LIST, "Saved - I will keep the glacier packing list for you."))
    _ingest(profile, "glacier", sessions)
    capsule, telemetry = _wide_capsule(profile, "glacier", question, target_tokens=2048)
    assert item in _lane_section(capsule), (telemetry.get("whole_turn_lines"), capsule)


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("question", [
    "Where is the spare key for the pottery studio?",
    "spare key pottery studio where",
])
def test_identical_user_and_assistant_words_both_reach_the_reader(tmp_path, question):
    # Authority seam: the user's statement and the assistant's identical reply are two statements by
    # two speakers; neither identity is merged into the other, and neither is delivered twice.
    sentence = "The spare key for the pottery studio is taped under the blue flowerpot."
    profile = _profile(tmp_path)
    sessions = [[_SMALL_TALK[i % 4]] for i in range(3)]
    sessions[1].append((sentence, sentence))
    _ingest(profile, "studio", sessions)
    capsule, telemetry = _wide_capsule(profile, "studio", question, target_tokens=2048)
    seen: Counter = Counter()
    for line in capsule.splitlines():
        if sentence in line:
            speaker = re.match(r"-\s*(user|assistant) said", line)
            day = re.search(r"\d{4}-\d{2}-\d{2}", line)
            seen[(speaker.group(1) if speaker else "", day.group(0) if day else "")] += 1
    assert {speaker for speaker, _day in seen} >= {"user", "assistant"}, (telemetry.get("whole_turn_lines"), capsule)
    assert all(count == 1 for count in seen.values()), seen


_HIVE_STORY = (
    "I spent the whole afternoon moving the beehive. The old spot by the fence got too much shade once the "
    "neighbour's walnut tree filled out, and the colony had been sluggish since June. I waited until dusk when "
    "the foragers were home, closed the entrance with a strip of foam, strapped the boxes together and carried "
    "them on a sack barrow across the lawn. My brother held the torch and kept the dog inside. We set the hive "
    "on two concrete blocks in the orchard corner, facing south-east so the entrance catches the morning sun, "
    "and I put a leafy branch across the entrance so the bees would reorient when they came out. By the next "
    "morning they were flying normally and bringing in pale yellow pollen.")


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("question", ["Where did I move the beehive to?", "beehive new spot?"])
def test_a_live_chat_without_statement_times_is_labelled_recorded(tmp_path, question):
    # A live turn keeps statement time unknown (store_turn): its lane line says when it was recorded.
    assert len(_HIVE_STORY) > cr._COMPLETE_SOURCE_MAX_CHARS  # the capsule cannot carry it whole
    profile = _profile(tmp_path)
    _ingest(profile, "hive", [[_SMALL_TALK[0], (_HIVE_STORY, "A sunny corner should suit them.")]], stated=False)
    capsule, telemetry = _wide_capsule(profile, "hive", question, target_tokens=2048)
    heads = [line for line in _lane_section(capsule).splitlines() if line.startswith("- ")]
    assert heads, (telemetry.get("whole_turn_lines"), capsule)
    assert all(re.match(r"- (user|assistant) said \(recorded \d{4}-\d{2}-\d{2}\): ", line) for line in heads), heads
