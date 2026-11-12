"""A delivery instruction binds the reply only where it scopes this turn's output.

`core/delivery_contract.py` is the scope analysis ported from the Aug 15 final-output contract work.
On the v0.8 line, before it, 16 of the 42 Aug 15 delivery instructions that ask for a non-empty
answer created no contract, and 7 of 46 ordinary turns that merely used the operator words did.

The families are the Aug 15 corpus, built from vocabulary the reported failures never used, plus two
sets of new wordings written for the port. The parser reads a grammatical relation -- a restriction
operator scoping this turn's output -- so it must survive when every noun is replaced.
"""

from __future__ import annotations

import os
import random
import re
import subprocess
import sys

import pytest

from core import delivery_contract as dc
from core.delivery_contract import parse_delivery_contract

# ------------------------------------------------------------------ the Aug 15 corpus


@pytest.mark.parametrize(
    "turn",
    [
        "Output only the letter Q.",
        "Reply with nothing but the ISO 4217 code for Japan.",
        "Return the result and nothing else.",
        "Give the answer only - no preamble, no units, no punctuation.",
        "What is 7 factorial? Output only the digits.",
        "Sort these words alphabetically: pear apple fig. Only the sorted list, nothing else.",
        "Name the capital of Peru. No commentary.",
        "Return the SHA of HEAD, nothing more.",
        "Translate 'good evening' into Finnish. Do not include any explanation.",
        "list the three primary colours, no headings",
        "Show me the report and nothing else.",
        "Print the flag value only.",
        "Can you explain, without any preamble, how TLS handshakes work?",
    ],
)
def test_an_exclusivity_instruction_becomes_a_contract(turn):
    contract = parse_delivery_contract(turn)
    assert contract is not None and contract.exclusive, turn


@pytest.mark.parametrize(
    "turn",
    [
        # Sloppy, unpunctuated, abbreviated -- how the instruction is actually typed.
        "wats 9*9 output only the number thx",
        "capital of norway. just the city name pls",
        "output ONLY the hex code no other text",
        "convert 3 miles to km — number only",
        "give me teh answer only",
        "reverse 'level' output only the result asap",
        "SORT THESE: c a b. ONLY THE SORTED LIST",
        "name 3 noble gases,nothing else",
        "what's 2^10\noutput only the value",
        "just the ISO code   for   brazil ,  nothing more",
        "translate hola to english. no notes",
        "tell me the year rome fell -- no context needed",
        "answer w/ only yes or no: is 91 prime",
        # Distant domains: no vocabulary from any reported failure survives here.
        "Return the CAS registry number for acetylsalicylic acid and nothing else.",
        "Which enzyme cleaves peptide bonds after lysine? Output only the enzyme name.",
        "Give the Köppen classification for Reykjavik, no explanation.",
        "State the atomic number of tungsten. No preamble.",
        "Emit the ISO 639-1 tag for Swahili only.",
        # The Aug 15 blind set, generated after that repair was green.
        "Which mountain range separates Europe from Asia? Reply with the range and nothing further.",
        "Transliterate 'кот' into Latin script, solely the transliteration.",
        "Identify the tuning of the lowest string on a standard bass. Skip the explanation.",
        "State the chemical formula for calcite. Do not append notes.",
        "What is the airspeed of an unladen swallow? Answer strictly the figure.",
        "How many moons does Neptune have? Give the count alone.",
        "now the same for Saturn, number only",
    ],
)
def test_a_delivery_instruction_is_read_however_it_is_typed(turn):
    assert parse_delivery_contract(turn) is not None, turn


@pytest.mark.parametrize(
    "turn",
    [
        "Can you explain how a heat pump works?",
        "Only you can decide that.",
        "I only have one question about the invoice.",
        "The word 'only' appears twice in that sentence, right?",
        "Nothing else worked, so I rebuilt the index.",
        "That's it for today, thanks for the help.",
        "Explain why the only child of that node is null.",
        "She just arrived from Lisbon.",
        "Zero characters were written to the buffer. What went wrong?",
        "Tell me about the empty set.",
        "My answer is that we should merely wait.",
        "Is an empty response valid HTTP?",
        "Give me a full walkthrough with plenty of context and examples.",
        # Each is one scoping decision away from a real contract.
        "Without context, that number is meaningless.",
        "I only just started.",
        "Show me the report, and nothing else in the folder is relevant.",
        "Answer this: is the only even prime number 2?",
        "Just so you know, the build passed.",
        "No notes were taken at the meeting, so summarise from the recording.",
        "The endpoint returns 0 characters when the cache is cold; why?",
        "Print statements only slow the loop down.",
        "Say what you like, I merely wanted the file.",
        "There is no explanation in the docs for this flag.",
        "Nothing else in the schema references that column.",
        "That's it -- the migration finished.",
        "Write a short essay on why nothing else in physics is as strange as entanglement.",
        "The report has no explanation for the drop in throughput -- can you work out why?",
        # `alone` and a clause-final focus adverb both have ordinary readings.
        "She works alone.",
        "It runs strictly on battery.",
        "The technician arrived alone.",
        "Leave the sensor alone.",
        "She works only.",
        "It runs only.",
        "I read only.",
        "This happens only.",
        "That matters only.",
        "We wait only.",
        "He listens only.",
        "The valve was closed only.",
        "Walk me through why the Urals count as a boundary at all.",
        "The manual skips the explanation entirely - is that normal for this vendor?",
        "Is a completely empty reply valid under the protocol?",
    ],
)
def test_the_operator_words_in_ordinary_prose_make_no_contract(turn):
    assert parse_delivery_contract(turn) is None, turn


def test_the_same_words_bind_when_they_address_the_reply():
    """The near-miss pairs side by side: only position changes."""
    assert parse_delivery_contract("Without context, that number is meaningless.") is None
    assert parse_delivery_contract("Explain the spike, without context.") is not None

    assert parse_delivery_contract("Print statements only slow the loop down.") is None
    assert parse_delivery_contract("Print only the statement count.") is not None

    assert parse_delivery_contract("Nothing else in the schema references that column.") is None
    assert parse_delivery_contract("Name the column and nothing else.") is not None


def test_a_prior_turns_contract_does_not_bind_the_next_turn():
    assert parse_delivery_contract("What is 12 squared? Output only the number.") is not None
    for follow_up in ("and 13?", "what about 13", "now do 13 please", "again"):
        assert parse_delivery_contract(follow_up) is None, follow_up


@pytest.mark.parametrize("turn", ["", "   ", "\n\n", "???", "🙂"])
def test_degenerate_input_is_not_a_contract(turn):
    assert parse_delivery_contract(turn) is None


# ------------------------------------------------------------------ new wordings, written for the port


@pytest.mark.parametrize(
    "turn",
    [
        "What's the boiling point of nitrogen in kelvin? Just the figure.",
        "Spell 'necessary' backwards, nothing else",
        "Give me the postcode for the Louvre and that's all",
        "convert 0x1F to decimal, only the result pls",
        "Which planet has the longest day? Planet name only.",
        "Tell me the chemical symbol for gold. No explanation needed.",
        "Write the SQL to count rows in users. Don't add any commentary.",
        "how many legs does a spider have -- number only",
        "Reply with the year the Berlin Wall fell, nothing more.",
        "Name one prime between 20 and 30. Skip the preamble.",
        "Output just the first word of the sentence 'Cats sleep a lot'.",
        "List the RGB values for pure magenta, without any labels.",
        "Round pi to four decimals. Digits only.",
        "Which country hosted the 1998 World Cup? Country name only, thanks.",
        "Solve 3x + 5 = 20 for x, nothing else please",
        "Abbreviate 'Massachusetts' the USPS way. No explanation required.",
        "what's the plural of 'cactus'?? just the word",
        "Give the hex colour for teal -- no extra words",
        "Reverse the string abc123 and output nothing but the result",
        "Estimate the population of Iceland. One number, no commentary.",
        "extract the domain from https://example.org/a/b only the domain",
        "Translate 'thank you' to Japanese romaji, solely the romaji.",
        "Rank these by size: moon, earth, sun. Just the ranked list, no notes.",
        "Quote only the first line of the poem I pasted.",
    ],
)
def test_a_new_wording_of_a_delivery_instruction_is_read(turn):
    assert parse_delivery_contract(turn) is not None, turn


@pytest.mark.parametrize(
    "turn",
    [
        "I just wanted to say thanks for yesterday.",
        "The only thing that helped was restarting the router.",
        "Nothing else on the list matters until the audit is done.",
        "We had no context for the outage until the logs came in.",
        "That's all the data we have, so what trend do you see?",
        # An adverb before a word that only begins like a determiner is adverbial, not a focus.
        "Only after the update did the fan start spinning loudly. Why would that be?",
        "Explain just about everything I should know before buying a used car.",
        "Answer only after you've checked the logs, please.",
        "My manager merely forwarded the email without comment.",
        "Is it normal that the tests only run on Linux?",
        "There were no headings in the original document, can you add some?",
        "If they ask, just say you're busy -- is that rude?",
        "Round two only made things worse, what should we try next?",
        "The file name only changed after the rename script ran.",
        "No explanation was ever given for the delay.",
        "State law alone doesn't settle it, so what does the federal rule say?",
        "Nothing more could be done for the old laptop.",
        "Solve for x was the only instruction on the sheet, and I am stuck.",
        "Our team has no headings policy for internal memos.",
        "Explain only if you're sure; otherwise say you don't know.",
        "Spell check only caught half of the typos in my essay.",
        "That's all I know about the incident so far.",
        "No commentary from the vendor yet. What should I tell the customer?",
    ],
)
def test_a_new_wording_of_ordinary_prose_makes_no_contract(turn):
    assert parse_delivery_contract(turn) is None, turn


def test_the_named_parts_of_the_reply_are_kept():
    contract = parse_delivery_contract("Write the SQL to count rows in users. Don't add any commentary, no headings.")
    assert contract is not None
    assert contract.forbidden_components == ("commentary", "headings")


# ------------------------------------------------------------------ cost


@pytest.mark.parametrize(
    "text",
    [
        # A clause may start after every line break of a whitespace run; as one regex the clause
        # split read the rest of the run from each of them (20k characters took 6 s).
        "'x' + '\\n ' * 150000 + 'x'",
        "'give ' + '\\u00a0' * 300000 + 'x'",
        # Every adverb searched its clause again for an output verb before it (20k took 10 s).
        "'only ' * 100000",
        "'give ' + 'me ' * 100000 + 'only the x'",
        "'give me me me only ' * 30000",
        "'nothing else ' * 60000",
        "('nothing else' + '.' * 50 + 'x ') * 6000",
        "'no commentary ' * 60000",
        "'Without context. ' * 40000",
        "'only ' + '-' * 300000 + ' x'",
    ],
)
def test_a_long_message_is_read_in_linear_time(text):
    script = f"from core.delivery_contract import parse_delivery_contract; parse_delivery_contract({text})"
    subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        timeout=10,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )


# ------------------------------------------------------------------ the rewritten scans, against their patterns

_ORIGINAL_CLAUSE_SPLIT_RE = re.compile(
    r"[;\n?!]|\.(?=\s|$)|,\s+|\s+and\s+then\s+|\s+and\s+also\s+|\s+and\s+|\s+then\s+|\s+also\s+"
    r"|\s+plus\s+|\s+--\s+|\s+—\s+",
    re.IGNORECASE,
)
_ORIGINAL_VERB_BEFORE_FOCUS_RE = re.compile(
    rf"\b(?:{dc._FOCUS_VERB_PATTERN})(?:\s+(?:me|us|it|back|out|with|as|w/))*\s*$",
    re.IGNORECASE,
)
# What may follow a coda, as one pattern: punctuation and one courtesy word to the end of the clause,
# or a colon before the material the request is about.
_ORIGINAL_CODA_TAIL_RE = re.compile(
    r"^(?:[\s.,;!?)\]\"'’”—–-]*:"
    r"|[\s.,;:!?)\]\"'’”—–-]*(?:please|thanks|thank\s+you|thx|pls|ok|okay)?[\s.,;:!?]*$)",
    re.IGNORECASE,
)

_WORDS = [
    "only", "just", "solely", "alone", "nothing else", "nothing more", "that's it", "that’s all",
    "no more", "and", "and then", "and also", "then", "also", "plus", "--", "—", "-", "output", "give",
    "give me", "return", "print", "say", "send back", "show", "reply", "answer", "tell", "explain",
    "please", "pls", "thanks", "thank you", "ok", "okay", "thx", "no commentary", "without context",
    "don't add notes", "the", "about", "number", "with", "me", "it", "back", "out", "as", "us", "she",
    "works", "nothing", "else", ".", ",", ";", "?", "!", "\n", "\n\n", " ", "  ", "\t", " ", "\x1c",
    "'", '"', ")", "ſay", "K", "ONLY", "OUTPUT", "OK", "w/", "w/o", "list", "name",
    "names", "telling",
]
_SEPARATORS = ["", " ", " ", " ", "  ", "\n", ", ", ". ", "\t", "-", "'", ": ", ":", " :"]


def _texts(count, seed):
    rng = random.Random(seed)
    return [
        "".join(rng.choice(_WORDS) + rng.choice(_SEPARATORS) for _ in range(rng.randint(0, 16)))
        for _ in range(count)
    ]


@pytest.mark.parametrize("seed", [1, 2])
def test_the_rewritten_scans_match_the_patterns_they_replace(seed):
    for text in _texts(3000, seed):
        assert list(dc._clause_splits(text)) == [
            (match.start(), match.end()) for match in _ORIGINAL_CLAUSE_SPLIT_RE.finditer(text)
        ], text
        surface = dc.delivery_surface(text)
        scope = dc.DeliveryScope(surface)
        for adverb in dc._EXCLUSIVE_ADVERB_RE.finditer(surface):
            host = scope.clause_at(adverb.start())
            clause_start = scope.clauses[host][0] if host is not None else 0
            expected = bool(_ORIGINAL_VERB_BEFORE_FOCUS_RE.search(surface[clause_start : adverb.start()]))
            assert scope.verb_before_focus(clause_start, adverb.start()) == expected, (surface, adverb.start())
        for coda in dc._RESTRICTIVE_CODA_RE.finditer(surface):
            host = scope.clause_at(coda.start("op"))
            end = scope.clauses[host][1] if host is not None else len(surface)
            expected = bool(_ORIGINAL_CODA_TAIL_RE.match(surface[coda.end("op") : end]))
            assert scope.coda_is_terminal(coda.end("op"), host) == expected, (surface, coda.start("op"))
