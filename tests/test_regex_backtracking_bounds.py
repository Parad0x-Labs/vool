"""Patterns that ran on user text or model output must stay fast on hostile shapes and keep their matches.

Each case pairs a timing check on the input shape that used to backtrack (seconds to minutes before the
fix) with a seeded differential check against the original pattern, so the speed fix cannot quietly
change what the pattern matches.
"""

from __future__ import annotations

import random
import re
import time

from core import context_retrieval, instructional_request, memory_receipts, model_output_guard, plain_task_routing
from core.memory import admission

# Generous: every fixed shape finishes in milliseconds; before the fix each took 5 s to over a minute.
BUDGET_SECONDS = 2.0


def _elapsed(fn, text: str) -> float:
    started = time.perf_counter()
    fn(text)
    return time.perf_counter() - started


def _fuzz(alphabet: list[str], count: int = 4000, max_parts: int = 14, seed: int = 7) -> list[str]:
    rng = random.Random(seed)
    return ["".join(rng.choice(alphabet) for _ in range(rng.randint(0, max_parts))) for _ in range(count)]


def _spans(pattern: re.Pattern[str], text: str) -> list[tuple[tuple[int, int], dict[str, str | None]]]:
    return [(m.span(), m.groupdict()) for m in pattern.finditer(text)]


ORIGINAL_PROVENANCE = re.compile(r"\s*\((?:stated|recorded):[^()]*?(?:;\s*(?:stated|recorded):[^()]*?)*\)$")
ORIGINAL_TOOL_CALL = re.compile(
    r"^(?:[A-Za-z_][A-Za-z0-9]*[_.][A-Za-z0-9_.]{0,47}\((?:[^()]|\([^()]*\))*\)"
    r"|[A-Za-z_][A-Za-z0-9_.]{1,48}\((?:\s*(?:\"[^\"]*\"|'[^']*'|[A-Za-z_]\w*\s*=\s*[^,()]+)\s*,?)*\)"
    r"|[A-Za-z_][A-Za-z0-9_.]{1,48}\(\s*[{\[].*[}\]]\s*\))[.;]?$",
    re.DOTALL,
)
ORIGINAL_ACTION_BREAK = re.compile(
    r",\s*(?:(?:and\s+)?then|and|but|also|next)?\s*|\s+\b(?:and(?:\s+then)?|then|but)\b\s+", re.IGNORECASE
)
ORIGINAL_HEDGE_SPLIT = re.compile(r"[,;:]\s+|\s+(?:and|but|while|then|although|though|so)\s+", re.IGNORECASE)
ORIGINAL_CLAUSE_BOUNDARY = re.compile(
    r",?\s+(?:and|but|while|then|after which|before that)\s+"
    r"(?=(?:i|we|my|then|just|also|afterwards?|recently|yesterday|last)\b)|;\s+",
    re.IGNORECASE,
)
ORIGINAL_THIRD_PARTY_SPLIT = (
    r",\s*(?:and|but|so|while|whereas)\s+|;\s+|\s+(?:and|but|whereas|while)\s+(?=i\b|we\b|so\s+(?:am|do)\s+i)"
)
NEW_THIRD_PARTY_SPLIT = (
    r",\s*(?:and|but|so|while|whereas)\s+|;\s+|(?<!\s)\s+(?:and|but|whereas|while)\s+(?=i\b|we\b|so\s+(?:am|do)\s+i)"
)
ORIGINAL_FOCUS = re.compile(r",\s*((?:what|which)\s+.+)$", re.IGNORECASE)


def test_provenance_suffix_is_fast_on_an_unclosed_stated_chain_and_matches_as_before():
    hostile = "(stated:" + ";stated:" * 40 + "x"
    assert _elapsed(context_retrieval._PROVENANCE_SUFFIX_RE.search, hostile) < BUDGET_SECONDS
    for text in _fuzz(["(", ")", "stated:", "recorded:", ";", " ", "x", "; stated:"]):
        new, old = context_retrieval._PROVENANCE_SUFFIX_RE.search(text), ORIGINAL_PROVENANCE.search(text)
        assert (new and new.span()) == (old and old.span()), text


def test_tool_call_shape_is_fast_on_keyword_runs_and_matches_as_before():
    for hostile in ("A.(A=" + "'A=" * 40, 'A.(""' + ' ""' * 40):
        assert _elapsed(model_output_guard._TOOL_CALL_EXPRESSION_RE.match, hostile) < BUDGET_SECONDS
    alphabet = ["f(", "a.b(", ")", "(", '"', "'", "x", "k=", " ", ",", "{", "}", "1", ".", ";"]
    for text in _fuzz(alphabet, count=6000):
        new = model_output_guard._TOOL_CALL_EXPRESSION_RE.match(text)
        old = ORIGINAL_TOOL_CALL.match(text)
        assert bool(new) == bool(old), text


def test_action_clause_breaks_are_fast_on_a_space_run_and_split_as_before():
    hostile = "a" + " " * 40_000 + "x"
    assert _elapsed(lambda s: list(instructional_request._ACTION_CLAUSE_BREAK_RE.finditer(s)), hostile) < BUDGET_SECONDS
    for text in _fuzz(["a", " ", "  ", ",", "and", "then", "but", "also", "x", "\n"]):
        assert _spans(instructional_request._ACTION_CLAUSE_BREAK_RE, text) == _spans(ORIGINAL_ACTION_BREAK, text), text


def test_receipt_clause_splitters_are_fast_on_a_space_run_and_split_as_before():
    hostile = "a" + " " * 40_000 + "x"
    assert _elapsed(memory_receipts._blank_hedged_clauses, hostile) < BUDGET_SECONDS
    assert _elapsed(lambda s: memory_receipts._clause_event_days(s, None), hostile) < BUDGET_SECONDS
    assert _elapsed(memory_receipts.third_party_statement, "He said" + " " * 40_000 + "x") < BUDGET_SECONDS
    alphabet = ["a", " ", "  ", ",", ";", ":", "and", "but", "while", "then", "so", "i", "we", "my", "x", "\t"]
    for text in _fuzz(alphabet):
        assert _spans(memory_receipts._HEDGE_CLAUSE_SPLIT_RE, text) == _spans(ORIGINAL_HEDGE_SPLIT, text), text
        assert _spans(memory_receipts._CLAUSE_BOUNDARY_RE, text) == _spans(ORIGINAL_CLAUSE_BOUNDARY, text), text
        assert re.split(NEW_THIRD_PARTY_SPLIT, text, flags=re.I) == re.split(ORIGINAL_THIRD_PARTY_SPLIT, text, flags=re.I), text


def test_quote_spans_are_fast_on_unclosed_quotes_and_match_finditer():
    for quote in ('"', "“", "'", "‘"):
        hostile = (" " + quote + "a") * 40_000
        assert _elapsed(lambda s: list(admission._iter_quote_spans(s)), hostile) < BUDGET_SECONDS
    alphabet = ['"', "“", "”", "'", "‘", "’", "a", " ", "b", ".", "\n"]
    for text in _fuzz(alphabet, count=6000, max_parts=16):
        new = [(m.span(), m.groupdict()) for m in admission._iter_quote_spans(text)]
        assert new == _spans(admission._QUOTE_SPAN_RE, text), text


def test_interrogative_focus_is_fast_across_many_lines_and_finds_the_same_match():
    hostile = ",what x" * 30_000 + "\nx"
    floor = plain_task_routing._interrogative_focus_floor
    assert _elapsed(lambda s: plain_task_routing._INTERROGATIVE_FOCUS_RE.search(s, floor(s)), hostile) < BUDGET_SECONDS
    for text in _fuzz([",", " ", "what", "which", "x", "\n", "\n\n", "?"], count=6000):
        new = plain_task_routing._INTERROGATIVE_FOCUS_RE.search(text, floor(text))
        old = ORIGINAL_FOCUS.search(text)
        assert (new and (new.span(), new.group(1))) == (old and (old.span(), old.group(1))), text
