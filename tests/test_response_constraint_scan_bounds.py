"""Scan-bound contracts for the response-constraint shape scanners.

The shape patterns give every whitespace run exactly one greedy owner per branch
(``\\s+word\\s*|\\s+`` alternations, ``(?:[^\\S\\n]*\\n)+`` newline boundaries).
Their earlier ``\\s+X?\\s*`` / ``\\s*\\n\\s*`` forms re-split one whitespace run
O(k) ways and re-extended the trailing quantifier each time, so a turn or answer
made of a keyword head plus k newlines plus a near-miss tail spent O(k^2) inside
``_parse_response_constraint`` / ``_presentation_answer_has_format`` (CodeQL
polynomial-redos alerts 121-127 on main b5c57965, measured quadratic pre-repair).

Correction round (PR91 8efafe9 -> this tree): two OPTIONAL-group forms still put
two whitespace quantifiers over one run whenever the optional stayed empty — the
layout ``as(?:\\s+a\\s+|\\s+)qual?\\s*list`` arm and the date-line
``^\\s*marker?\\s*`` head. Both were re-expressed as whole alternation arms and
must stay span/group/finditer-equal to the PR91-head spec patterns below; the
frozen lead witnesses ('as'+k newlines+'notalist', k spaces+'notadate' through
the public timeline check) pin the linearity. No classified exceptions exist for
this delta: every difference between the PR91 spec and the shipped scanner is a
failure of this file.

Two contracts are pinned here:
* linearity — the frozen adversarial shapes must stay near-linear as input grows
  (x8 input may not cost x16 time; quadratic retry costs x64);
* spec equality — the pinned pre-repair patterns and the shipped scanners agree
  on span, groups and finditer spans for the deterministic corpus below. The one
  recorded difference: an anchored ``.match`` on a table behind leading blank
  lines no longer reaches across them; the table detector's only production
  contract is ``bool(.search)`` and that boolean never differs.
"""

from __future__ import annotations

import re
import time

import pytest

import core.response_constraints as rc

# Pinned pre-repair patterns (main b5c57965) kept as the differential spec.
_SPEC_SHORT_WORD = re.compile(
    r"(?:"
    r"\b(?:answer|respond|reply|say|return|give|use|write|end)"
    r"(?:\s+\w+){0,5}?\s+(?:in|with|using)?\s*"
    r"(?:exactly\s+)?(?P<verb_count>one|two|three|four|1|2|3|4|a single|single)"
    r"[ -]?words?\b"
    r"|"
    r"\b(?:exactly\s+)?(?P<noun_count>one|two|three|four|1|2|3|4|a single|single)"
    r"[ -]?word(?:s)?\s*(?::|(?:answer|response|reply|mood|description)\b)"
    r"|"
    r"^\s*(?P<standalone_count>one|two|three|four|1|2|3|4|a single|single)"
    r"[ -]?word(?:s)?[.!]?\s*$"
    r")",
    re.IGNORECASE,
)
_SPEC_SHORT_SENTENCE = re.compile(
    r"\b(?:answer|respond|reply|say|return|give|use|write|end|confirm|explain)"
    r"(?:\s+[\w-]+){0,7}?\s+(?:in|with|using)?\s*"
    r"(?:exactly\s+)?(?P<count>one|two|three|four|1|2|3|4|a single|single)"
    r"(?:\s+short)?\s+sentences?\b",
    re.IGNORECASE,
)
_SPEC_LAYOUT = re.compile(
    r"\b(?:numbered|bulleted|bullet[- ]point(?:ed)?)\s+(?:list|items?|points?)\b"
    r"|\bas\s+(?:a\s+)?(?:numbered|bulleted|bullet(?:ed)?)?\s*list\b"
    r"|\bin\s+bullet\s+points?\b"
    r"|\b(?:one|each)\s+(?:item|entry|point|line|colour|color|word|thing)?\s*"
    r"(?:per|on\s+(?:its|a|their)\s+own|on\s+(?:a\s+)?separate)\s+line\b"
    r"|\bone\s+per\s+line\b"
    r"|\beach\s+on\s+(?:its|a)\s+own\s+line\b",
    re.IGNORECASE,
)
_SPEC_ONE_PER_LINE = re.compile(
    r"\b(?:one|each)\s+(?:item|entry|point|colour|color|word|thing)?\s*"
    r"(?:per|on\s+(?:its|a|their)\s+own|on\s+(?:a\s+)?separate)\s+line\b"
    r"|\bone\s+per\s+line\b"
    r"|\beach\s+on\s+(?:its|a)\s+own\s+line\b",
    re.IGNORECASE,
)
_SPEC_TABLE = re.compile(r"^\s*\|[^\n]+\|\s*\n\s*\|[\s:|-]+\|(?:\s*\n\s*\|[^\n]+\|)*", re.MULTILINE)

# Pinned PR91-head (8efafe9) patterns for the correction-round delta: the shipped
# scanners must be EXACTLY equal to these (span, groups, finditer) — the
# re-expression into whole alternation arms changed no accepted language.
_SPEC_LAYOUT_PR91 = re.compile(
    r"\b(?:numbered|bulleted|bullet[- ]point(?:ed)?)\s+(?:list|items?|points?)\b"
    r"|\bas(?:\s+a\s+|\s+)(?:numbered|bulleted|bullet(?:ed)?)?\s*list\b"
    r"|\bin\s+bullet\s+points?\b"
    r"|\b(?:one|each)(?:\s+(?:item|entry|point|line|colour|color|word|thing)\s*|\s+)"
    r"(?:per|on\s+(?:its|a|their)\s+own|on(?:\s+a\s+|\s+)separate)\s+line\b"
    r"|\bone\s+per\s+line\b"
    r"|\beach\s+on\s+(?:its|a)\s+own\s+line\b",
    re.IGNORECASE,
)
_SPEC_DATE_LINE_PR91 = re.compile(
    r"^\s*(?:[-*•]|\d+[.)])?\s*(?:\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}|\d{3,4}s?|"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2})",
    re.IGNORECASE,
)
_DELTA_PAIRS = [
    ("_LAYOUT_SHAPE_RE", _SPEC_LAYOUT_PR91, rc._LAYOUT_SHAPE_RE),
    ("_DATE_LINE_RE", _SPEC_DATE_LINE_PR91, rc._DATE_LINE_RE),
]

# (name, pinned spec, shipped scanner, adversarial builder)
_SCANNERS = [
    (
        "_SHORT_WORD_SHAPE_RE",
        _SPEC_SHORT_WORD,
        rc._SHORT_WORD_SHAPE_RE,
        lambda k: "answer" + "\n" * k + "five words",
    ),
    (
        "_SHORT_SENTENCE_SHAPE_RE",
        _SPEC_SHORT_SENTENCE,
        rc._SHORT_SENTENCE_SHAPE_RE,
        lambda k: "explain" + "\n" * k + "five sentences",
    ),
    (
        "_LAYOUT_SHAPE_RE",
        _SPEC_LAYOUT,
        rc._LAYOUT_SHAPE_RE,
        # The correction-round witness: the optional qualifier arm. The prior
        # 'one'+nl+'zzz' builder never entered the broken `as...list` arm.
        lambda k: "as" + "\n" * k + "notalist",
    ),
    (
        "_ONE_PER_LINE_RE",
        _SPEC_ONE_PER_LINE,
        rc._ONE_PER_LINE_RE,
        lambda k: "each" + "\n" * k + "zzz",
    ),
    (
        "_MARKDOWN_TABLE_RE",
        _SPEC_TABLE,
        rc._MARKDOWN_TABLE_RE,
        lambda k: "|a|" + "\n" * k + "|b",
    ),
]

# Deterministic differential corpus: valid phrasings, near-miss failures,
# repeated/nested shape words, end-of-input truncations, mixed whitespace runs.
_CORPUS = [
    "Answer in one word: is it ready?",
    "Give me a single-word response.",
    "Exactly one word: calm.",
    "Answer in two words.",
    "Give me a three-word mood.",
    "Exactly four words: describe a clean workspace.",
    "Put that in exactly three words.",
    "Can you put that in exactly three words?",
    "answer this in two words please",
    "answer\n\n\none word",
    "answer\n\n\nfive words",
    "answer in\n\n\none word",
    "answer\n\n\nin\n\n\none word",
    "answer \t \n one word",
    "respond using exactly 3 words",
    "say MARIGOLD-8342 in a single word",
    "write a one-word answer:",
    "one word:",
    "one-word response",
    "one words",
    "2-word: describe it",
    "Explain photosynthesis in one sentence",
    "Tell me in one sentence: why?",
    "answer in two short sentences",
    "confirm the order id MARIGOLD-8342 in 3 sentences",
    "explain\n\n\nfive sentences",
    "explain\n\n\none sentence",
    "answer that, with the file name, in one short sentence",
    "reply in\n\n\n2 sentences",
    "as a numbered list",
    "as a bulleted list",
    "bullet-point list please",
    "in bullet points",
    "one per line",
    "each item on its own line",
    "each on a separate line",
    "put every entry per line",
    "one\n\n\nper line",
    "one\n\n\nzzz",
    "each\n\n\nzzz",
    "each item\n\n\non its own line",
    "each item on\n\n\nits own line",
    "as\n\n\na numbered list",
    "as a\n\n\nlist",
    "as\n\n\nlist",
    "answer as a markdown list, one item per line",
    "numbered list of 5 items",
    "|a|b|\n|---|---|\n|1|2|\n|3|4|",
    "|a|\n|---|\n|b|",
    "|a|\n\n\n|---|\n|b|",
    "|a|\r\n|---|\r\n|b|",
    "|a|\n|:-|\n|b|",
    "|a|\n|:-|\n|b| trailing text",
    "|a|" + "\n" * 12 + "|b",
    "|a|" + "\n" * 12 + "|:-" + "-" * 12,
    "|a|\n|---|\n|b|" + "\n" * 12,
    "\n" * 12 + "|a|\n|---|\n|b|",
    "|a|\n|---|\n\n|b|",
    "|a|\n|---|",
    "|a|\n|",
    "|a|\n|--",
    "|",
    "||\n|---|",
    "|a|",
    "no shape language at all here",
    "",
    " ",
    "\n",
    "\n\n\n",
    "\t \n \t",
    "word",
    "one",
    "sentence",
    "one words sentences",
    "one one one one word word",
    "answer answer in in one word",
    "exactly exactly one word",
    "per per line",
    "on on its own line",
    "as as a list",
    "one item per line item per line",
    # correction-round corpus: the layout as-arm and the date-line head
    "as bulleted list",
    "as bullet list",
    "as numberedlist",
    "as a numberedlist",
    "as  a  list",
    "as" + "\n" * 12 + "notalist",
    "as a" + "\n" * 12 + "notalist",
    "as" + "\n" * 12 + "numbered" + "\n" * 12 + "notalist",
    "as a numbered" + " " * 12 + "notalist",
    "answer as a list of parts",
    "  2023-01-05",
    "- Jan 5",
    "* 1960s",
    "  12. Mar 3",
    "   Jan 5",
    "1. 2023-01-05",
    "\t- 2023-01-05",
    "  20236",
    "20230105",
    "sept. 3",
    "  january 31",
    " " * 12 + "notadate",
    "\n" * 12 + "notadate",
    "  notadate",
    "- " + " " * 12 + "notadate",
    "1." + " " * 12 + "notadate",
    "\t" * 12 + "notadate",
    "\xa0" * 4 + "notadate",
]


@pytest.mark.parametrize(("name", "spec", "live", "grow"), _SCANNERS, ids=[s[0] for s in _SCANNERS])
def test_scanner_agrees_with_the_pinned_spec(name, spec, live, grow) -> None:
    for text in _CORPUS:
        expected = spec.search(text)
        got = live.search(text)
        if name == "_MARKDOWN_TABLE_RE":
            assert bool(expected) == bool(got), text
            if expected is not None and got is not None:
                assert expected.end() == got.end(), text
                prefix = text[expected.start() : got.start()]
                assert got.start() >= expected.start()
                assert not prefix or prefix.isspace(), text
            continue
        if expected is None:
            assert got is None, (text, name)
        else:
            assert got is not None, (text, name)
            assert got.span() == expected.span(), (text, name)
            assert got.groupdict() == expected.groupdict(), (text, name)
        expected_finditer = [m.span() for m in spec.finditer(text)]
        got_finditer = [m.span() for m in live.finditer(text)]
        assert expected_finditer == got_finditer, (text, name)


def test_anchored_table_match_no_longer_crosses_leading_blank_lines() -> None:
    # Recorded, intentional: only ``bool(.search)`` is a production contract of
    # the table detector; an anchored ``.match`` used to reach across leading
    # blank lines and no longer does. The search boolean stays identical.
    behind_blanks = "\n\n\n|a|\n|---|\n|b|"
    assert _SPEC_TABLE.match(behind_blanks) is not None
    assert rc._MARKDOWN_TABLE_RE.match(behind_blanks) is None
    assert bool(rc._MARKDOWN_TABLE_RE.search(behind_blanks)) is True


@pytest.mark.parametrize(("name", "spec", "live", "grow"), _SCANNERS, ids=[s[0] for s in _SCANNERS])
def test_scanner_is_linear_on_its_adversarial_shape(name, spec, live, grow) -> None:
    small = 1500
    large = 8 * small

    def cost(size: int) -> float:
        text = grow(size)
        best = float("inf")
        for _ in range(3):
            start = time.perf_counter()
            live.search(text)
            best = min(best, time.perf_counter() - start)
        return best

    small_cost = cost(small)
    large_cost = cost(large)
    assert large_cost / max(small_cost, 1e-6) < 16.0, (
        f"x8 size grew x{large_cost / max(small_cost, 1e-6):.1f} — super-linear whitespace re-splitting is back"
    )


def test_parse_entrypoint_is_linear_on_newline_runs() -> None:
    # The public parser collapses horizontal runs but keeps newlines, so the
    # frozen user-turn shapes use newline runs.
    small = 400
    large = 8 * small

    def cost(size: int) -> float:
        best = float("inf")
        for text in (
            "answer" + "\n" * size + "five words",
            "explain" + "\n" * size + "five sentences",
            "one" + "\n" * size + "zzz",
            "each" + "\n" * size + "zzz",
        ):
            start = time.perf_counter()
            rc.parse_response_constraint(text)
            best = min(best, time.perf_counter() - start)
        return best

    assert cost(large) / max(cost(small), 1e-6) < 16.0


def test_check_entrypoint_is_linear_on_table_whitespace_runs() -> None:
    constraint = rc.parse_response_constraint("show it as a table")
    assert constraint is not None and constraint.requested_formats == ("table",)
    small = 1500
    large = 8 * small

    def cost(size: int) -> float:
        text = "|a|" + "\n" * size + "|b"
        best = float("inf")
        for _ in range(3):
            start = time.perf_counter()
            rc.check_response_constraint(text, constraint)
            best = min(best, time.perf_counter() - start)
        return best

    assert cost(large) / max(cost(small), 1e-6) < 16.0


# --- correction round: the PR91-head delta must be exact, and the lead's frozen
# --- witnesses must stay linear through the public functions.


@pytest.mark.parametrize(("name", "spec", "live"), _DELTA_PAIRS, ids=[p[0] for p in _DELTA_PAIRS])
def test_correction_delta_is_exactly_equal_to_the_pr91_spec(name, spec, live) -> None:
    for text in _CORPUS:
        for op in ("search", "match"):
            expected = getattr(spec, op)(text)
            got = getattr(live, op)(text)
            if expected is None:
                assert got is None, (op, text, name)
            else:
                assert got is not None, (op, text, name)
                assert got.span() == expected.span(), (op, text, name)
                assert got.groupdict() == expected.groupdict(), (op, text, name)
        assert [m.span() for m in spec.finditer(text)] == [m.span() for m in live.finditer(text)], (
            text,
            name,
        )


def test_timeline_check_is_linear_on_leading_whitespace_runs() -> None:
    # The decisive public witness from the correction mission: k spaces in front
    # of a non-date on a timeline-checked answer (the check splits lines and
    # matches _DATE_LINE_RE per line, so the spaces reach the scanner intact).
    constraint = rc.parse_response_constraint("present this as a timeline")
    assert constraint is not None and constraint.requested_formats == ("timeline",)
    small = 1500
    large = 8 * small

    def cost(size: int) -> float:
        best = float("inf")
        for text in (
            " " * size + "notadate",
            "\n" * size + "notadate",
            "- " + " " * size + "notadate",
            "1." + " " * size + "notadate",
        ):
            start = time.perf_counter()
            rc.check_response_constraint(text, constraint)
            best = min(best, time.perf_counter() - start)
        return best

    assert cost(large) / max(cost(small), 1e-6) < 16.0


def test_parse_entrypoint_is_linear_on_the_optional_qualifier_arm() -> None:
    # 'as' + k newlines + a near-miss word: the optional list qualifier stayed
    # empty and left \s+ and \s* over one run (quadratic until the correction).
    small = 400
    large = 8 * small

    def cost(size: int) -> float:
        best = float("inf")
        for text in (
            "as" + "\n" * size + "notalist",
            "as a" + "\n" * size + "notalist",
        ):
            start = time.perf_counter()
            rc.parse_response_constraint(text)
            best = min(best, time.perf_counter() - start)
        return best

    assert cost(large) / max(cost(small), 1e-6) < 16.0
