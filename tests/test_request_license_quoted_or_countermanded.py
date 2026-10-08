"""A license phrase the user quotes, or takes back in the same sentence, licenses nothing.

Measured on HEAD 058cddbf: core.model_output_guard._request_licenses_statement_time checked only
for a negation in front of the phrase. A request that QUOTES someone else's instruction ("My friend
said "Use DATE of CONVERSATION to answer with an approximate date" but give me the exact day.") or
countermands it after the phrase ("The old template said 'use the date of the conversation ...',
ignore that.") still opened the evidence span of every record, so a date bound to no record shipped
where strict mode withdraws it.

Contract: a license phrase inside quotation marks (straight or curly, double or single, guillemets;
an unclosed double quote runs to the end of the turn) does not license. Nor does one followed in its
own sentence by an instruction that takes it back (ignore / disregard / exact / instead), unless that
word names the alternative the request turns down ("an approximate date, not the exact day"). An
apostrophe inside a word is not a quotation mark. Names, events and dates are synthetic.
"""

from __future__ import annotations

import pytest

from core.model_output_guard import _request_licenses_statement_time, replace_unsupported_past_time_claims


def guard(answer, question, evidence):
    return replace_unsupported_past_time_claims(answer, question=question, evidence_texts=evidence)


def line(iso: str, speaker: str, text: str) -> str:
    return f"- user said (stated {iso}): {speaker}: {text}"


ASK = "When did Teodor tune the old church organ?"
# No record names a time for the tuning; the answer's day sits one day from a statement time, so it
# ships only when the request licenses the evidence span.
E = [
    line("2024-09-14", "Marit", "The bakery on the corner closed for the season."),
    line("2024-07-02", "Marit", "We repainted the shed."),
]
ANSWER = "Around 13 September 2024."
PHRASE = "Use DATE of CONVERSATION to answer with an approximate date"


QUOTED = [
    # the reported wordings
    f'My friend said "{PHRASE}" but give me the exact day.',
    "The old template said 'use the date of the conversation to answer with an approximate date', ignore that.",
    f"Someone wrote “{PHRASE}”; I want the real date only.",
    # paraphrases
    f"A note on the fridge reads “{PHRASE}.” Please answer from the records.",
    "The previous assistant was told ‘use the date of the session for an approximate date’ and that was a mistake.",
    f"The FAQ quotes «{PHRASE}» as an example of a bad instruction.",
    'Our old prompt, "Answer the question. Use the date of the conversation to answer with an approximate date.", is retired.',
    f'Quoting the brief: "{PHRASE}" -- that rule no longer applies.',
    # sloppy
    f'my frend said "{PHRASE.lower()}" lol just give me the day',
    f"template said '{PHRASE.lower()}' ignore it pls",
    f'the old prompt was like "{PHRASE}',  # never closed
    "He told me “use date of conversation to answer w/ an approximate date” but no",
    f'"{PHRASE}" - thats what the old bot said',
]

COUNTERMANDED = [
    "Use the date of the conversation to answer with an approximate date, ignore that, I need the day it happened.",
    "Use DATE of CONVERSATION to answer with an approximate date... actually disregard that.",
    "Use the date of the conversation to answer with an approximate date but give me the exact day.",
    "use date of conversation for an approximate date no wait, give the real day instead",
    "Answer with an approximate date based on when it was said, or rather ignore it and say only what the records state.",
]

LICENSED = [
    "Use the date of the conversation to answer with an approximate date.",
    "use DATE of CONVERSATION to answer with an approximate date",
    "Please give an approximate date based on when it was said.",
    # apostrophes inside words are not quotation marks
    "Teodor's notes are messy, so use the date of the conversation to answer with an approximate date.",
    "The singers' notes are messy; use the date of the conversation to answer with an approximate date.",
    "Teodor's request: use the date of the conversation to answer with an approximate date, as in the singers' notes.",
    "Don't worry about precision. Use the date of the conversation to answer with an approximate date.",
    # a quoted title elsewhere in the turn does not swallow the license
    'He called it "the big tune-up". Use the date of the conversation to answer with an approximate date.',
    "My friend said 'just guess', but use the date of the conversation to answer with an approximate date.",
    # the countermand word names the alternative the request turns down
    "Use the date of the conversation to answer with an approximate date, not the exact day.",
    "Use the date of the conversation to answer with an approximate date instead of the exact one.",
    "Use the date of the conversation to answer with an approximate date rather than an exact day.",
]


@pytest.mark.parametrize("request_text", QUOTED)
def test_a_quoted_license_phrase_does_not_license(request_text):
    question = f"{ASK} {request_text}"
    assert not _request_licenses_statement_time(question), request_text
    assert guard(ANSWER, question, E) != ANSWER, request_text


@pytest.mark.parametrize("request_text", COUNTERMANDED)
def test_a_license_taken_back_in_its_own_sentence_does_not_license(request_text):
    question = f"{ASK} {request_text}"
    assert not _request_licenses_statement_time(question), request_text
    assert guard(ANSWER, question, E) != ANSWER, request_text


@pytest.mark.parametrize("request_text", LICENSED)
def test_an_unquoted_license_still_licenses(request_text):
    question = f"{ASK} {request_text}"
    assert _request_licenses_statement_time(question), request_text
    assert guard(ANSWER, question, E) == ANSWER, request_text


def test_a_quoted_phrase_does_not_hide_a_second_unquoted_license():
    question = (f'{ASK} The old bot said "{PHRASE}" and that is fine. '
                "Use the date of the conversation to answer with an approximate date.")
    assert _request_licenses_statement_time(question)
    assert guard(ANSWER, question, E) == ANSWER


def test_a_countermand_in_a_later_sentence_does_not_reach_back():
    # Same-sentence rule: a later sentence's "exact" is about something else.
    question = (f"{ASK} Use the date of the conversation to answer with an approximate date. "
                "Quote the exact words Teodor used, too.")
    assert _request_licenses_statement_time(question)


def test_without_any_license_the_in_span_day_is_withdrawn():
    assert guard(ANSWER, ASK, E) != ANSWER
