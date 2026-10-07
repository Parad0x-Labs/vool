"""The reader contract on a question over retrieved records: attribution, premise, linked records.

Measured on archived paid dev answers (no provider call in this file):
  * person swap phrased as a denial: the records held the asked fact, said by or about a different
    person than the one the question named, and the reader opened with "the records don't state
    this" before quoting it. A question over retrieved records that names a person, with no
    not-mentioned option to fall back on, now carries the attribution rule: give the fact first,
    then whom the records attribute it to; never open with a denial;
  * multiple choice with a false premise: with the earlier premise rule in the prompt, the reader
    still chose a factual option whose words appeared only in a record that asked about it, or
    chose it while noting the records attribute it to someone else. The premise rule now names
    both shapes;
  * abstaining while linkable evidence was delivered: a record stated on the same date as the
    matched event, or on the date the question names, held the answer and the reader said the
    records do not specify. A question over retrieved records now carries the linked-records rule:
    combine the records stated with the matched event or on the named date, marking an inference.

The rules are reader instructions only: they reach the first system message's "Answer format for
this request" block (and the one rewrite) for the question shapes above and for no other turn, and
they never touch the retrieved records, the user's turn, or any other message.

Which sentence asks the records something is read from its shape (see
`core.ordinary_chat_response_guard.record_question_sentences`): a pleasantry, a request to make or
do something, a telling request that points nowhere in the records ("Explain how tides work."), a
plain statement, or the harness's own answer instructions ask nothing, even with records attached.

Every name, record and question here is synthetic.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

import core.memory_first_router as mfr
import core.ordinary_chat_response_guard as guard
from core.memory_first_router import MemoryFirstRouter

READER_PREFIX = (
    "Answer from the lake club chats I imported. Use only what those chats support. If they do "
    "not support an answer, say you do not know. Give a concise final answer.\n"
)

# Retrieved records in the capsule's own shape: speaker-labelled dialogue turns with their dates.
RECORDS = (
    "<retrieved_context>\n"
    "Distilled local facts. Answer from these records: you may combine them and state a direct "
    "inference from them, marked as inferred, but never add a fact the records do not state.\n"
    "- user said: Session date: 9:10 am on 3 April, 2031\n"
    "Tamsin: Swimming is my reset - it makes me feel weightless and calm. "
    "(stated: Session date: 3 April, 2031; stated: 2031-04-03)\n"
    "- user said (stated 2031-04-03): Session date: 9:10 am on 3 April, 2031\n"
    "Bertil: I set up weekly rowing sessions on the lake with my cousins.\n"
    "- user said [reported source prefix \"Bertil:\"] (stated 2031-04-03): Session date: 9:10 am "
    "on 3 April, 2031\nBertil: Look at the boats we are fixing up right now.\n"
    "</retrieved_context>"
)
# Records with no speaker labels: a name in the question is only found by its capital letter.
UNLABELLED_RECORDS = (
    "<retrieved_context>\nDistilled local facts. Answer from these records.\n"
    "- user said: my neighbour baked rye loaves for the autumn fair (stated: 2030-10-02)\n"
    "</retrieved_context>"
)
# Speaker labels that only follow an attribution colon, the shape most imported records take.
AFTER_COLON_RECORDS = (
    "<retrieved_context>\nDistilled local facts. Answer from these records.\n"
    "- user said: Casimir: I baked plum dumplings for the harbour fair. (stated: 2030-09-14)\n"
    "- user said (stated 2030-09-14): Henrike: The fair band played until midnight.\n"
    "</retrieved_context>"
)
# Speaker names that open with a capital outside A-Z.
ACCENTED_RECORDS = (
    "<retrieved_context>\nDistilled local facts. Answer from these records.\n"
    "- user said: Session date: 8:00 am on 5 May, 2033\n"
    "Łucja: Sailing at dawn makes me feel unhurried. (stated: 2033-05-05)\n"
    "- user said (stated 2033-05-05): Session date: 8:00 am on 5 May, 2033\n"
    "Øyvind: I built a cedar dinghy for the fjord race.\n"
    "</retrieved_context>"
)
# Speakers whose names sit one typing slip from ordinary words.
NEAR_WORD_RECORDS = (
    "<retrieved_context>\nDistilled local facts. Answer from these records.\n"
    "- user said: Session date: 7:05 pm on 2 February, 2031\n"
    "Martin: I finally fixed the porch light. (stated: 2031-02-02)\n"
    "- user said (stated 2031-02-02): Session date: 7:05 pm on 2 February, 2031\n"
    "Cassie: The choir meets on Wednesdays now.\n"
    "- user said (stated 2031-02-02): Session date: 7:05 pm on 2 February, 2031\n"
    "Sandy: The dunes moved a lot this winter.\n"
    "</retrieved_context>"
)

ATTRIBUTION_MARKERS = (
    "If the records state the asked fact only for someone other than the person the question names",
    "give that fact first, then say whom the records attribute it to",
    "do not open with a denial",
)
LINKED_MARKERS = (
    "Before saying the records do not state it",
    "combine the records stated on the same date as the record matching the asked event",
    "or on a date the question names",
    "marking an inference as inferred",
)
PREMISE_MARKERS = (
    "One offered option says the information is not mentioned.",
    "about the exact person and the exact thing the question names",
    "attribute it to someone else",
    "only ask about it",
    "even if the option's words appear in a record",
    "never qualify a factual option as someone else's",
    "choose the not-mentioned option",
)

# --- the semantic family: a question over records that names a person ---------------------------

# The reported shape, recast in invented names and wording: the question names one person, and the
# records hold the asked fact for another.
REPORTED_PERSON_QUESTIONS = [
    "What does swimming do for Bertil's mood, by his own account?",
    "Which lake outings did Tamsin organise for her cousins?",
]
PARAPHRASED_PERSON_QUESTIONS = [
    "How does swimming make Bertil feel, by his own account?",
    "What feeling does Bertil say he gets from swimming?",
    "Which sessions did Tamsin set up with her cousins at the lake?",
    "In Bertil's words, swimming leaves him feeling what?",
    "Did Tamsin plan anything with her cousins on the lake?",
    "Tell me what Tamsin organised with her cousins on the water.",
]
# Lower-case names, typos, dropped words, fragments, shouting, no question mark: the name is found
# because the records label that speaker.
SLOPPY_PERSON_QUESTIONS = [
    "acording to bertil what does swiming help him feel",
    "what did tamsin n cousins arange on lake",
    "bertil swimming feel??",
    "how swimming make bertil feel",
    "WHAT DID TAMSIN ARRANGE ON THE LAKE",
    "tamsin's cousins + lake, what did they set up",
    # No question mark and no interrogative word: a fragment and a "tell me" request still ask.
    "bertil swiming feelings",
    "tell me about tamsins lake plans",
    "lake stuff tamsin set up w cousins",
]
# One typing slip in a recorded speaker's name: a dropped, added, swapped or wrong letter.
MISSPELT_SPEAKER_QUESTIONS = [
    "what does swiming do for bertl",
    "tasmin lake outings w cousins",
    "how does swimming make bretil feel",
    "what did tamzin set up on the lake",
    "berttil swimming feelings",
    "what did tamsni arrange on the lake",
]
# Names and topics the implementation has never seen, in records without speaker labels.
UNSEEN_PERSON_QUESTIONS = [
    "What did Okonkwo bake for the autumn fair?",
    "Which loaves did my neighbour Saoirse bring to the fair?",
    "When did Wieslawa's brother repaint the boathouse?",
]
# The same, with the name opening the sentence: it names a person because no sentence opens with it.
SENTENCE_OPENING_UNSEEN_NAMES = [
    "Okonkwo baked what for the autumn fair",
    "Saoirse brought which loaves to the fair?",
    "Wieslawa's brother repainted the boathouse when?",
    "Ségolène baked rye for the fair, right?",
]


def _reader_request(
    user_text: str,
    *,
    records: str | None = RECORDS,
    history: list[tuple[str, str]] | None = None,
    output_mode: str = "plain_text",
):
    """Build the provider request for one turn through the real reader seam."""
    wire = [
        {"role": "system", "content": "BASE SYSTEM"},
        *({"role": role, "content": content} for role, content in history or []),
        *([{"role": "system", "content": records}] if records else []),
        {"role": "user", "content": user_text},
    ]
    internal = SimpleNamespace(
        metadata={},
        temperature=0.2,
        max_output_tokens=256,
        context_summary="",
        trace_id="reader-contract-trace",
        attachments=(),
        messages=[SimpleNamespace(**message) for message in wire],
        system_prompt=lambda: "BASE SYSTEM",
        user_prompt=lambda: user_text,
        as_openai_messages=lambda: [dict(message) for message in wire],
    )
    interpretation = SimpleNamespace(
        raw_text=user_text, normalized_text=user_text, user_text="", understanding_confidence=0.9
    )
    router = MemoryFirstRouter.__new__(MemoryFirstRouter)
    with mock.patch.object(mfr, "normalize_prompt", return_value=internal):
        request = MemoryFirstRouter._build_request(
            router,
            task=None,
            classification={"task_class": "chat"},
            interpretation=interpretation,
            context_result=None,
            persona=None,
            output_mode=output_mode,
            task_kind="conversation",
            surface="openclaw",
            source_context={},
        )
    return request, wire


_TURN_CONTEXT_PREFIX = "Context for this turn:"


def _turn_directive_messages(request) -> list[dict]:
    # ae264ad6 (2026-10-06): the per-turn rules travel in a second system message ("Context for this turn: ...") placed
    # after the records and before the user's turn, so the leading system message stays byte-stable across turns
    return [dict(m) for m in request.messages[1:] if m.get("role") == "system" and str(m.get("content") or "").startswith(_TURN_CONTEXT_PREFIX)]


def _primary(request) -> str:
    """The served rule text: the leading system message plus the turn-directives system message."""
    parts = [str(request.messages[0]["content"])] + [str(m["content"]) for m in _turn_directive_messages(request)]
    return "\n".join(parts)


def _policy(request) -> dict:
    return dict(request.metadata["ordinary_chat_output_policy"])


def _assert_lane(request, *, attribution: bool, linked: bool, premise: bool) -> None:
    primary = _primary(request)
    policy = _policy(request)
    assert policy["person_attribution"] is attribution
    assert policy["linked_records"] is linked
    assert policy["not_mentioned_option_offered"] is premise
    for marker in ATTRIBUTION_MARKERS:
        assert (marker in primary) is attribution, marker
    for marker in LINKED_MARKERS:
        assert (marker in primary) is linked, marker
    assert ("One offered option says the information is not mentioned." in primary) is premise


def _assert_records_and_turn_untouched(request, wire) -> None:
    """The rules live in the leading system message and in exactly one turn-directives system message (ae264ad6);
    the records and the user's turn are byte-identical and the leading message is the base prompt."""
    directives = _turn_directive_messages(request)
    assert len(directives) <= 1, directives
    others = [dict(m) for m in request.messages[1:] if dict(m) not in directives]
    assert others == [dict(m) for m in wire[1:]]
    assert str(request.messages[0]["content"]).startswith("BASE SYSTEM")


# --- P1: a question over records that names a person gets the attribution rule -----------------


@pytest.mark.parametrize(
    "question",
    REPORTED_PERSON_QUESTIONS + PARAPHRASED_PERSON_QUESTIONS + SLOPPY_PERSON_QUESTIONS,
)
def test_a_record_question_naming_a_person_gets_the_attribution_rule(question: str) -> None:
    request, wire = _reader_request(READER_PREFIX + question)
    _assert_lane(request, attribution=True, linked=True, premise=False)
    _assert_records_and_turn_untouched(request, wire)


@pytest.mark.parametrize("question", SLOPPY_PERSON_QUESTIONS)
def test_a_lower_case_name_is_a_person_because_the_records_label_that_speaker(question: str) -> None:
    speakers = guard.retrieved_record_speakers([RECORDS])
    assert guard.names_a_person(question, speakers)
    # Without the records' speakers only a capitalised name counts, and these have none.
    if question.upper() == question or question.lower() == question:
        assert not guard.names_a_person(question, ())


@pytest.mark.parametrize("question", MISSPELT_SPEAKER_QUESTIONS)
def test_a_speaker_name_with_one_typing_slip_still_names_the_person(question: str) -> None:
    request, wire = _reader_request(question)
    _assert_lane(request, attribution=True, linked=True, premise=False)
    _assert_records_and_turn_untouched(request, wire)


@pytest.mark.parametrize(
    "question",
    [
        "what goes into a dry martini",  # the name plus one final letter is another word
        "when did the lassie get her collar",  # a slip keeps the first letter
        "how did the sands shift after the storm",  # a name under six letters takes no slip
    ],
)
def test_a_word_one_letter_from_a_speaker_is_not_that_speaker(question: str) -> None:
    request, _ = _reader_request(question, records=NEAR_WORD_RECORDS)
    _assert_lane(request, attribution=False, linked=True, premise=False)


@pytest.mark.parametrize("question", ["what did martn fix on the porch", "when does casie's choir meet"])
def test_a_slip_in_a_near_word_speaker_still_names_the_speaker(question: str) -> None:
    request, _ = _reader_request(question, records=NEAR_WORD_RECORDS)
    _assert_lane(request, attribution=True, linked=True, premise=False)


@pytest.mark.parametrize("question", UNSEEN_PERSON_QUESTIONS + SENTENCE_OPENING_UNSEEN_NAMES)
def test_an_unseen_capitalised_name_in_unlabelled_records_gets_the_attribution_rule(question: str) -> None:
    request, wire = _reader_request(question, records=UNLABELLED_RECORDS)
    _assert_lane(request, attribution=True, linked=True, premise=False)
    _assert_records_and_turn_untouched(request, wire)


@pytest.mark.parametrize(
    ("question", "records"),
    [
        ("What does sailing make Łucja feel?", ACCENTED_RECORDS),
        ("what did øyvind build for the fjord race", ACCENTED_RECORDS),
        ("Øyvind built what for the race", ACCENTED_RECORDS),
        ("Which loaves did Ségolène bring to the fair?", UNLABELLED_RECORDS),
        ("When did Ömer repaint the boathouse?", UNLABELLED_RECORDS),
    ],
)
def test_a_name_opening_with_a_capital_outside_a_to_z_is_a_person(question: str, records: str) -> None:
    request, wire = _reader_request(question, records=records)
    _assert_lane(request, attribution=True, linked=True, premise=False)
    _assert_records_and_turn_untouched(request, wire)


def test_speaker_labels_outside_a_to_z_are_read_from_the_records() -> None:
    assert guard.retrieved_record_speakers([ACCENTED_RECORDS]) == ("Łucja", "Øyvind")


def test_a_speaker_label_after_an_attribution_colon_is_read() -> None:
    """"- user said: <Name>: ..." is how most imported records carry their speaker."""
    assert guard.retrieved_record_speakers([AFTER_COLON_RECORDS]) == ("Casimir", "Henrike")
    request, wire = _reader_request(
        "what did casimir bake for the harbour fair", records=AFTER_COLON_RECORDS
    )
    _assert_lane(request, attribution=True, linked=True, premise=False)
    _assert_records_and_turn_untouched(request, wire)


def test_the_attribution_rule_states_the_fact_first_and_its_owner_without_a_denial() -> None:
    instruction = guard.person_attribution_instruction({"person_attribution": True})
    for marker in ATTRIBUTION_MARKERS:
        assert marker in instruction
    assert guard.person_attribution_instruction({"person_attribution": False}) == ""
    assert guard.person_attribution_instruction(None) == ""


# --- P3: a question over records gets the linked-records rule -----------------------------------


@pytest.mark.parametrize(
    "question",
    [
        "What did I arrange on the lake?",
        "What cool stuff got fixed at the boathouse on 3 April, 2031?",
        "what kind of boats were being fixed up at the boatyard",
        "Which sessions were set up that week?",
        "was the rowing weekly or monthly",
    ],
)
def test_a_record_question_gets_the_linked_records_rule(question: str) -> None:
    request, wire = _reader_request(READER_PREFIX + question)
    policy = _policy(request)
    assert policy["linked_records"] is True
    for marker in LINKED_MARKERS:
        assert marker in _primary(request), marker
    _assert_records_and_turn_untouched(request, wire)


def test_a_first_person_record_question_gets_no_attribution_rule() -> None:
    request, _ = _reader_request("What did I arrange on the lake?", records=UNLABELLED_RECORDS)
    _assert_lane(request, attribution=False, linked=True, premise=False)


@pytest.mark.parametrize(
    "question",
    [
        "What did I fix on Friday?",
        "Which boats did we repaint in March?",
        "Friday's session at the lake, what did I do there?",
    ],
)
def test_a_calendar_word_never_names_a_person(question: str) -> None:
    request, _ = _reader_request(question, records=UNLABELLED_RECORDS)
    _assert_lane(request, attribution=False, linked=True, premise=False)


@pytest.mark.parametrize("question", ["Swimming makes me feel what?", "Recently, what did I fix?"])
def test_a_word_sentences_open_with_is_not_a_name(question: str) -> None:
    request, _ = _reader_request(question, records=UNLABELLED_RECORDS)
    _assert_lane(request, attribution=False, linked=True, premise=False)


# A question in fragment shape: the interrogative word sits inside the sentence, and no question
# mark or person is there to mark it.
FRAGMENT_QUESTIONS_WITHOUT_A_PERSON = [
    "swimming makes you feel what",
    "and how did the boat repairs go",
    "the lake sessions were held where",
    "rowing sessions on the lake - weekly or what",
    "that boat from the regatta, which colour was it",
    # a mistyped interrogative word
    "wat happend at the boathouse on 3 april",
    "whn did the rowing start",
]


@pytest.mark.parametrize("question", FRAGMENT_QUESTIONS_WITHOUT_A_PERSON)
def test_an_interrogative_word_inside_a_fragment_asks(question: str) -> None:
    request, wire = _reader_request(question, records=UNLABELLED_RECORDS)
    _assert_lane(request, attribution=False, linked=True, premise=False)
    _assert_records_and_turn_untouched(request, wire)


# --- P2: an offered not-mentioned option keeps the premise rule, now naming both measured shapes --

PREMISE_QUESTIONS = [
    "Which boat replaced Tamsin's sunken kayak? Select the correct answer: "
    "(a) a canoe (b) Not mentioned in the conversation.",
    "Bertil wants to take up which sport once the regatta is over? Select the correct answer: "
    "(a) Not mentioned in the conversation (b) Fencing.",
]
# A not-mentioned option typed with a slip, a contraction, a hedge, or as "not in the records".
TYPED_NOT_MENTIONED_TURNS = [
    "which boat did tamsin get after the kayak sank (a) canoe (b) not mentiond",
    "What did Bertil name his boat? (a) Not mentoined in the chats (b) Heron",
    "bertil's new hobby?? a) fencing b) its not stated",
    "Which lake did Tamsin swim in? 1) Lake Vann 2) isnt mentioned",
    "What colour is Bertil's boat? (a) green (b) not explicitly mentioned",
    "Who taught Tamsin to row? (a) her aunt (b) Not in the records",
    "tamsin's boat club? a) Heron Club b) not specifed",
    "bertil hobby after the regatta a) fencing b) no info",
    "Who repainted the boathouse? (a) Bertil (b) nowhere in the chats",
    "tamsins first boat?? a) a canoe b) dont know",
]


@pytest.mark.parametrize(
    "question",
    PREMISE_QUESTIONS
    + TYPED_NOT_MENTIONED_TURNS
    + ["Who set up the rowing sessions? 1) Bertil 2) Tamsin 3) It is not mentioned"],
)
def test_an_offered_not_mentioned_option_gets_only_the_premise_rule(question: str) -> None:
    request, wire = _reader_request(READER_PREFIX + question)
    _assert_lane(request, attribution=False, linked=False, premise=True)
    for marker in PREMISE_MARKERS:
        assert marker in _primary(request), marker
    _assert_records_and_turn_untouched(request, wire)


@pytest.mark.parametrize(
    "question",
    [
        # "not started" is no slip of "not stated": a short participle must be exact.
        "Has Tamsin's course begun? (a) not started yet (b) started in May",
        "How is Bertil doing at the club? (a) not mentoring anyone (b) mentoring two rowers",
        # "no data" followed by a noun is a factual option, not "no information".
        "Which plan did Bertil pick? (a) no data plan (b) the unlimited plan",
    ],
)
def test_an_option_that_only_resembles_not_mentioned_keeps_the_open_question_rules(question: str) -> None:
    request, _ = _reader_request(READER_PREFIX + question)
    _assert_lane(request, attribution=True, linked=True, premise=False)


def test_the_premise_rule_names_the_asked_only_and_qualified_option_shapes() -> None:
    instruction = guard.not_mentioned_option_instruction({"not_mentioned_option_offered": True})
    for marker in PREMISE_MARKERS:
        assert marker in instruction, marker


# --- negative controls and near-misses: no new rule ---------------------------------------------


@pytest.mark.parametrize("question", [*REPORTED_PERSON_QUESTIONS, "What did I arrange on the lake?"])
def test_without_retrieved_records_no_record_rule_is_sent(question: str) -> None:
    request, wire = _reader_request(question, records=None)
    _assert_lane(request, attribution=False, linked=False, premise=False)
    assert _primary(request) == "BASE SYSTEM"
    assert [dict(m) for m in request.messages[1:]] == [dict(m) for m in wire[1:]]


ASKS_NOTHING_TURNS = [
    "Write a poem about rowing on a lake.",
    "Bertil, write me a haiku about the boathouse.",
    "Remind Tamsin to book the lake sessions.",
    "Draft a thank-you note to the rowing club.",
    "thanks bertil!",
    "hi",
    "Please draft a note for Tamsin about Friday.",
    "can you remind Bertil to bring the oars",
]
# Turns unrelated to the records, sent with records attached: pleasantries and small talk (with a
# vocative or a question mark), requests to make something that carry interrogative words or a
# recorded name, telling requests that point nowhere in the records, plain statements, an
# instruction not to answer, and an exclamation.
UNRELATED_TURNS = [
    "great, thanks!",
    "ok sounds good",
    "cheers bertil, talk soon",
    "Thanks so much, Tamsin!",
    "hey, how are you?",
    "will do, thanks",
    "that's great, thank you!",
    "Write a short poem about why the lake freezes.",
    "Draft a note to the club about when the boathouse opens.",
    "compose a song on how swimming feels",
    "Can you write a limerick about what Bertil fixed?",
    "Make a list of where we could go rowing next summer.",
    "Explain how tides work.",
    "Summarize the plot of a famous play in two sentences.",
    "Describe a perfect day at the beach.",
    "Tell me a joke about kayaks.",
    "give me three tips for sleeping better",
    "I went to the lake yesterday.",
    "That's what I thought.",
    "Don't answer yet.",
    "What a sunny morning!",
    "Plan a picnic for Saturday.",
    "Update the club calendar.",
    "List five rivers in Europe.",
    "Bertil sounds like a great guy.",
    "tamsin seems really kind",
    "Tamsin is so talented.",
    "Tamsin's boat was green.",
    "bertil fixed the boats yesterday",
    "This is what I meant.",
    "Remind me to call Tamsin tomorrow.",
    "remind me at five to bring the oars",
]


@pytest.mark.parametrize("turn", ASKS_NOTHING_TURNS + UNRELATED_TURNS)
def test_a_request_that_asks_nothing_gets_no_record_rule(turn: str) -> None:
    request, wire = _reader_request(turn)
    _assert_lane(request, attribution=False, linked=False, premise=False)
    # A length contract the turn states ("in two sentences") may still be there; no record rule is.
    _assert_records_and_turn_untouched(request, wire)


@pytest.mark.parametrize(
    "turn", ["Write a haiku about the boathouse.", "Thanks!", "Describe a perfect day at the beach."]
)
def test_the_harness_answer_instructions_ask_nothing_by_themselves(turn: str) -> None:
    """"Answer from ...", "Use only what ...", "If they do not ..., say ..." and "Give a concise
    final answer." instruct; they do not ask the records anything."""
    request, _ = _reader_request(READER_PREFIX + turn)
    _assert_lane(request, attribution=False, linked=False, premise=False)
    speakers = guard.retrieved_record_speakers([RECORDS])
    assert guard.record_question_sentences(
        READER_PREFIX + "What did Tamsin arrange on the lake?", speakers
    ) == ("What did Tamsin arrange on the lake?",)


# Requests with no question form that still ask the records: a telling verb that points at them (a
# recorded speaker, a record word, a date), a recall form, a statement with an asking verb.
RECORD_REQUESTS_WITHOUT_A_QUESTION_FORM = [
    ("Tell me what Bertil said about swimming.", True),
    ("Explain why Tamsin set up the rowing sessions.", True),
    ("Summarize what we talked about in our chats.", False),
    ("Describe what happened on 3 April, 2031.", False),
    ("remind me what tamsin set up on the lake", True),
    ("remind me wat bertil fixed", True),
    ("Remember when Bertil fixed up the boats?", True),
    ("recall the boats bertil was fixing", True),
    ("I wonder what Tamsin arranged on the lake", True),
    ("I can't remember how swimming makes Bertil feel", True),
    ("let me know what bertil said about the boats", True),
]


@pytest.mark.parametrize(("turn", "names_someone"), RECORD_REQUESTS_WITHOUT_A_QUESTION_FORM)
def test_a_request_pointing_at_the_records_asks_them(turn: str, names_someone: bool) -> None:
    request, wire = _reader_request(turn)
    _assert_lane(request, attribution=names_someone, linked=True, premise=False)
    _assert_records_and_turn_untouched(request, wire)


@pytest.mark.parametrize(
    ("turn", "names_someone"),
    [
        ("update on bertil's boat repairs?", True),
        ("call with tamsin last week, what did we agree", True),
        ("list of the boats bertil fixed", True),
        ("plan for the lake trip - when is it", False),
    ],
)
def test_a_verb_that_is_also_a_noun_opens_a_fragment_before_a_preposition(
    turn: str, names_someone: bool
) -> None:
    """"update on ...", "call with ...", "list of ..." are record fragments, not instructions."""
    request, wire = _reader_request(turn)
    _assert_lane(request, attribution=names_someone, linked=True, premise=False)
    _assert_records_and_turn_untouched(request, wire)


@pytest.mark.parametrize(
    "turn",
    ["bertil swiming feelings", "tell me about tamsins lake plans", "lake stuff tamsin set up w cousins"],
)
def test_a_cue_less_record_question_reaches_the_reader_with_both_rules(turn: str) -> None:
    """No prefix, no question mark, no interrogative word: the turn itself must read as asking."""
    request, wire = _reader_request(turn)
    _assert_lane(request, attribution=True, linked=True, premise=False)
    _assert_records_and_turn_untouched(request, wire)


@pytest.mark.parametrize("turn", [*SLOPPY_PERSON_QUESTIONS, "did the rowing sessions start in april"])
def test_a_typed_question_without_a_question_mark_still_asks(turn: str) -> None:
    assert guard.asks_a_question(turn, guard.retrieved_record_speakers([RECORDS]))


@pytest.mark.parametrize("turn", ASKS_NOTHING_TURNS + UNRELATED_TURNS)
def test_a_pleasantry_or_a_create_or_do_request_asks_nothing(turn: str) -> None:
    assert not guard.asks_a_question(turn, guard.retrieved_record_speakers([RECORDS]))


def test_a_non_chat_output_mode_gets_no_record_rule() -> None:
    policy = guard.ordinary_chat_output_policy(
        prompt_profile="chat_minimal",
        output_mode="json_object",
        user_text=REPORTED_PERSON_QUESTIONS[0],
        memory_records_supplied=True,
        record_speakers=("Bertil",),
    )
    assert policy["person_attribution"] is False
    assert policy["linked_records"] is False


def test_options_without_a_not_mentioned_choice_stay_an_open_record_question() -> None:
    """Near-miss of the premise lane: labelled options, none of them a not-mentioned choice."""
    request, _ = _reader_request(
        READER_PREFIX + "Which of these did Bertil arrange: (a) rowing sessions (b) a picnic?"
    )
    _assert_lane(request, attribution=True, linked=True, premise=False)


# --- cross-turn ----------------------------------------------------------------------------------

HISTORY = [
    ("user", "What did Tamsin arrange on the lake?"),
    ("assistant", "Weekly rowing sessions with her cousins."),
]


@pytest.mark.parametrize(
    "turn",
    ["Thanks! Now write a haiku about autumn leaves.", "great, thanks for that!", "ok cheers"],
)
def test_an_earlier_person_question_does_not_mark_a_later_request(turn: str) -> None:
    request, _ = _reader_request(turn, history=HISTORY)
    _assert_lane(request, attribution=False, linked=False, premise=False)
    assert _primary(request) == "BASE SYSTEM"


@pytest.mark.parametrize(
    "turn", ["and what does bertil say swimming does for him", "and tamsn?"]
)
def test_a_follow_up_that_names_a_recorded_speaker_in_lower_case_gets_the_rule(turn: str) -> None:
    request, wire = _reader_request(turn, history=HISTORY)
    _assert_lane(request, attribution=True, linked=True, premise=False)
    _assert_records_and_turn_untouched(request, wire)


def test_a_pronoun_follow_up_gets_the_linked_rule_but_names_no_person() -> None:
    request, _ = _reader_request("and how did she feel about it?", history=HISTORY)
    _assert_lane(request, attribution=False, linked=True, premise=False)


# --- authority seams -----------------------------------------------------------------------------


def test_role_and_heading_labels_are_never_people() -> None:
    """User and assistant are roles, not people the question can name; neither is merged."""
    records = (
        "<retrieved_context>\nDistilled local facts. Answer from these records.\n"
        "- user said: USER: I booked the lake sessions.\nASSISTANT: Noted, Thursday at six.\n"
        "- assistant said: Note: the club closes in winter. (stated: 2031-01-02)\n"
        "- user said: Session date: 9:10 am on 3 April, 2031\nTamsin: Swimming calms me.\n"
        "</retrieved_context>"
    )
    assert guard.retrieved_record_speakers([records]) == ("Tamsin",)
    assert not guard.names_a_person("what did the assistant say about the sessions", ("Tamsin",))
    assert not guard.names_a_person("what did the user book?", ("Tamsin",))


def test_the_one_rewrite_repeats_exactly_the_rules_the_first_call_carried() -> None:
    person, _ = _reader_request(READER_PREFIX + REPORTED_PERSON_QUESTIONS[0])
    premise, _ = _reader_request(READER_PREFIX + PREMISE_QUESTIONS[0])
    plain, _ = _reader_request("Write a poem about rowing on a lake.")
    person_retry = guard.ordinary_chat_retry_instruction(_policy(person))
    premise_retry = guard.ordinary_chat_retry_instruction(_policy(premise))
    plain_retry = guard.ordinary_chat_retry_instruction(_policy(plain))
    assert ATTRIBUTION_MARKERS[0] in person_retry and LINKED_MARKERS[0] in person_retry
    assert ATTRIBUTION_MARKERS[0] not in premise_retry and LINKED_MARKERS[0] not in premise_retry
    assert PREMISE_MARKERS[-2] in premise_retry
    for marker in (ATTRIBUTION_MARKERS[0], LINKED_MARKERS[0], PREMISE_MARKERS[0]):
        assert marker not in plain_retry


def test_the_rules_carry_no_names_or_answer_text_and_stay_small() -> None:
    attribution = guard.person_attribution_instruction({"person_attribution": True})
    linked = guard.linked_records_instruction({"linked_records": True})
    premise = guard.not_mentioned_option_instruction({"not_mentioned_option_offered": True})
    for instruction in (attribution, linked, premise):
        assert "(a)" not in instruction and "(b)" not in instruction
        for name in ("Bertil", "Tamsin", "Okonkwo", "Casimir", "Łucja"):
            assert name not in instruction
    # The open-question pair costs under ~100 tokens on one prompt (4 characters per token).
    assert len(attribution) + len(linked) < 400
