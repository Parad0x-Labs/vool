"""The no-web check reads every turn, so reading a message must stay linear in its length.

Two scans in core.retrieval_constraints read far past what they were deciding about. Every negative
directive searched the whole rest of the message for the end of its clause, and the reported-example
frame that may follow a stop ("...\\n\\nsay a user types ...") was retried after every newline of a
run. On the v0.8 line a 116k-character message of short clauses took 12 s and 20000 blank lines
took 11 s, on every turn that carried them.

The rewrites are exact. The tests below hold them to the original single-pattern definitions over a
seeded corpus built from the words those patterns care about.
"""

from __future__ import annotations

import os
import random
import re
import subprocess
import sys

import pytest

from core import retrieval_constraints as rc


@pytest.mark.parametrize("script", [
    # Every clause holds a directive whose end is a stop close by; each one searched the rest of
    # the message for transition keywords anyway (116k characters took 12 s).
    "from core.retrieval_constraints import analyze_retrieval_constraints; "
    "analyze_retrieval_constraints(\"I'm on a plane, no markdown, for example you said no web; \" * 20000)",
    "from core.retrieval_constraints import analyze_retrieval_constraints; "
    "analyze_retrieval_constraints('Do not search the web for this.\\n' * 30000)",
    "from core.retrieval_constraints import analyze_retrieval_constraints; "
    "analyze_retrieval_constraints('no web, explain x ' * 40000)",
    # A frame may start after any newline, and each start rescanned the run (20000 took 11 s).
    "from core.retrieval_constraints import analyze_retrieval_constraints; "
    "analyze_retrieval_constraints('hello' + '\\n' * 200000 + 'world')",
    "from core.retrieval_constraints import analyze_retrieval_constraints; "
    "analyze_retrieval_constraints('x.\\n\\n say a user typed y. ' * 20000)",
])
def test_a_long_message_is_read_in_linear_time(script):
    subprocess.run([sys.executable, "-c", script], check=True, timeout=10,
                   env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})


# ------------------------------------------------------- the original definitions, verbatim


def _original_clause_end(text: str, start: int) -> int:
    candidates = [
        match.start()
        for pattern in (rc._HARD_CLAUSE_END_RE, rc._CONTRASTIVE_END_RE)
        if (match := pattern.search(text, start))
    ]
    explanation = rc._EXPLANATION_TRANSITION_RE.search(text, start)
    if explanation is not None and explanation.start() > start:
        candidates.append(explanation.start())
    return min(candidates) if candidates else len(text)


_ORIGINAL_REPORTED_EXAMPLE_RE = re.compile(
    rf"\b(?:for\s+example|for\s+instance|e\.g\.)[,:]?\s+{rc._REPORTED_SPEAKER}\s+{rc._REPORTING_VERB}"
    rf"|(?:^|(?<=[.!?;\n]))\s*(?:say|imagine|suppose|picture\s+this)[,:]?\s+{rc._REPORTED_SPEAKER}\s+{rc._REPORTING_VERB}"
    r"|\b(?:an?|the)\s+(?:example|sample|typical|hypothetical)\s+"
    r"(?:request|prompt|query|question|message|input|utterance|sentence)\s+"
    r"(?:(?:would|might|could|may)\s+)?(?:be|is|was|reads?|looks?\s+like|goes)\b"
    r"|\b(?:you|i|we)\s+(?:just\s+|earlier\s+|then\s+)?(?:asked|said|told\s+me|wrote|typed|meant|wanted|requested)\b",
    re.IGNORECASE,
)


def _original_reported_example_spans(text: str) -> tuple[tuple[int, int], ...]:
    spans: list[tuple[int, int]] = []
    cursor = 0
    while match := _ORIGINAL_REPORTED_EXAMPLE_RE.search(text, cursor):
        start, end = match.start(), match.end()
        tail = re.match(r"\s*(?:like|such\s+as)?\s*[,:]?\s*", text[end:])
        content = end + (tail.end() if tail else 0)
        if content < len(text) and text[content] in rc._QUOTE_PAIRS:
            close = text.find(rc._QUOTE_PAIRS[text[content]], content + 1)
            end = close + 1 if close != -1 else len(text)
        else:
            sentence_end = re.search(r"[.!?\n]", text[content:])
            end = content + sentence_end.end() if sentence_end else len(text)
        spans.append((start, end))
        cursor = max(end, match.end())
    return tuple(spans)


# ------------------------------------------------------------------------- the corpus

_WORDS = [
    # directives and cancellations
    "no web", "don't search", "dont search", "without using the web", "skip lookups", "never browse",
    "do not search", "no need to search", "avoid searching", "from your own knowledge", "offline only",
    "never mind", "cancel that", "search the web for", "look up",
    # transitions, overlapping ones and the explanation chain
    "but", "however", "instead", "rather", "yet", "then", "and then", "And\tthen", "BUT",
    "explain", "define", "describe", "tell me", "tell\nme", "answer", "just", "only", "pls", "please",
    "Please", "adjust", "lonely", "pleasepls", "justexplain", "abut", "thenceforth", "Ansewr",
    # stops, commas and every kind of whitespace
    ",", ", ", " ,", ".", "!", "?", ";", ":", "...", "\n", "\n\n", "\n \n", "\t", "  ", " ", "\r\n",
    " ", " ", "　", "\x0b", "\x1c",
    # reported-example frames and what they report
    "for example", "for instance", "e.g.", "say", "imagine", "suppose", "picture this", "a user",
    "someone", "the user", "one", "they", "typed", "asked", "says", "might type", "could ask",
    "an example request would be", "the sample prompt is", "you asked", "I said", "we wrote",
    "you just said", "essay", "ſay", '"', "'", "“", "”", "`", "like", "such as",
    "x", "weather", "price", "news", "it", "the",
]


def _texts(count: int, seed: int) -> list[str]:
    rng = random.Random(seed)
    texts = []
    for _ in range(count):
        parts = []
        for _ in range(rng.randint(0, 18)):
            parts.append(rng.choice(_WORDS) + rng.choice(["", " ", " ", " ", "  ", "\n", ", ", "\t"]))
        texts.append("".join(parts))
    return texts


_KNOWN_SHAPES = [
    "no web and then explain it",  # "and then" and its own "then" both end the clause
    "no web,  pls just explain it",  # the explanation chain, comma included
    "no web adjust explain it",  # "just" inside a word is not a chain word
    "no web.\n\nbut search later",
    "x.\n\n say a user typed y. no web",  # the frame starts right after the stop
    "x\n \n say someone asked z",  # ... or right after the first newline of the run
    "an essay a user typed",  # "say" inside a word is not a frame
    "for example, you said 'no web' and then I said hi.",
]


@pytest.mark.parametrize("seed", [1, 2])
def test_clause_ends_match_the_original_patterns(seed):
    for text in _KNOWN_SHAPES + _texts(1500, seed):
        boundaries = rc._ClauseBoundaries(text)
        for start in range(len(text) + 1):
            assert rc._negative_clause_end(text, start, boundaries) == _original_clause_end(text, start), (
                text, start,
            )
        # without a precomputed index the answer is the same
        assert rc._negative_clause_end(text, 0) == _original_clause_end(text, 0), text


@pytest.mark.parametrize("seed", [3, 4])
def test_reported_examples_match_the_original_pattern(seed):
    for text in _KNOWN_SHAPES + _texts(4000, seed):
        assert rc._reported_example_spans(text) == _original_reported_example_spans(text), text
