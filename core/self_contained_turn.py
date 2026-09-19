"""A turn that supplies its own premises cannot be answered by the internet.

Measured on the served surface, 10:09::

    U: Variable X holds "Apple". Variable Y holds "Banana". Variable Z holds "Cherry".
       I swap the contents of X and Y. Then I swap the contents of Y and Z. Then I
       overwrite Z with "Grape". What is the current value of Y? Output exactly one word.

    Work log: web.search  query=Variable X holds "Apple". Variable Y holds "Banana"...

The runtime searched the WEB for the value of a variable the user had just defined in the same
sentence. No page on the internet knows what "Y" holds -- the turn is the only source that does --
and the query was the puzzle itself, which cannot match anything meaningful. It cost a search, a
research classification, and a deep-lane summarisation on a question that needed none of it.

WHY IT HAPPENED. The research gate trusts the classifier: `task_class=research` produced
`enabled=True, reason=research_task`, and nothing between that and the planner asked whether an
external source COULD know the answer.

THE INVARIANT, and it is about evidence rather than about puzzles:

    when the turn itself assigns the values the question asks about, the answer is derivable from
    the turn, and retrieval can only add noise

DELIBERATELY NARROW, because the opposite error is worse. Silently refusing to look something up
returns a stale or invented answer where the user asked for a current one, so this fires only on an
unmistakable shape: at least two LITERAL assignments in the turn (a quoted string or a number bound
to a name), and a question asking about one of those very names. "The dollar is a currency, what is
its current value?" assigns nothing and is untouched.
"""

from __future__ import annotations

import re

__all__ = ["stipulated_names", "turn_answers_itself", "turn_supplies_its_data"]

#: `X holds "Apple"`, `Y = 5`, `Z contains 'Cherry'`, `count is set to 12`. The VALUE must be a
#: literal: a category statement ("the dollar is a currency") binds nothing and must not count, or
#: a live-data question about a thing the turn merely described would stop reaching the network.
_ASSIGNMENT_RE = re.compile(
    r"\b(?:variable\s+|value\s+of\s+)?(?P<name>[A-Za-z_][A-Za-z0-9_]{0,19})\s*"
    r"(?:holds|contains|=|is\s+set\s+to|starts\s+(?:at|as|with)|initially\s+(?:holds|contains|is))\s*"
    r"(?P<value>\"[^\"]{0,60}\"|'[^']{0,60}'|\d+(?:\.\d+)?)",
    re.IGNORECASE,
)

#: The interrogative clause -- what the turn actually asks for.
_QUESTION_RE = re.compile(
    r"\bwhat\s+(?:is|are|was|were|does|do)\b[^?]{0,120}\??|"
    r"\bwhich\b[^?]{0,120}\??|"
    r"\bhow\s+(?:much|many)\b[^?]{0,120}\??",
    re.IGNORECASE,
)

#: Names too generic to treat as stipulated identifiers even when something is bound to them.
#: Without this, "the price is 5" plus "what is the price of X today" would read as self-contained.
_GENERIC_NAMES = frozenset(
    {
        "it", "this", "that", "value", "price", "cost", "total", "rate", "time", "date", "name",
        "answer", "result", "temperature", "weather", "score", "number", "amount", "the", "a", "an",
    }
)

#: Minimum literal assignments before a turn counts as stipulating its own world. One binding is a
#: passing mention ("my budget is 500, what is the going rate for X?"); two or more is a premise
#: set the question is meant to be derived from.
_MIN_ASSIGNMENTS = 2


def stipulated_names(text: str) -> set[str]:
    """Names the turn binds to a literal value, lowercased."""

    found: set[str] = set()
    for match in _ASSIGNMENT_RE.finditer(str(text or "")):
        raw = match.group("name").strip()
        name = raw.lower()
        if not name:
            continue
        # A single UPPERCASE letter is a variable, not an article. Without this, "A = 5. B = 7.
        # What is A?" lost its first binding to the generic filter -- "a" is both the commonest
        # article in English and the commonest variable name in a puzzle, and case is what tells
        # them apart.
        if len(raw) == 1 and raw.isupper():
            found.add(name)
            continue
        if name not in _GENERIC_NAMES:
            found.add(name)
    return found


def turn_answers_itself(text: str) -> bool:
    """Whether this turn defines the very thing it asks about.

    True only when the turn binds at least two names to literal values AND the interrogative clause
    names one of them. Both halves are required: the assignments alone could be context for a
    genuine lookup, and a question about a name alone could be about anything.
    """

    body = " ".join(str(text or "").split())
    if not body:
        return False
    names = stipulated_names(body)
    if len(names) < _MIN_ASSIGNMENTS:
        return False
    for question in _QUESTION_RE.finditer(body):
        clause = question.group(0).lower()
        asked = set(re.findall(r"[a-z_][a-z0-9_]{0,19}", clause))
        if names & asked:
            return True
    return False


#: A Markdown table row: at least two `|`-separated cells with content on a line that starts
#: (after optional whitespace) with `|`. The separator row (`|---|---:|`) is deliberately NOT
#: data -- it is shape, and counting it would let a header-only fragment qualify.
_TABLE_ROW_RE = re.compile(r"^\s*\|.+\|\s*$", re.MULTILINE)
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|[\s:|-]+\|\s*$", re.MULTILINE)

#: A directed edge the user drew: `Research --> Design`, `Build -> Test`, `A -[blocked]-> B`.
_EDGE_RE = re.compile(r"^\s*.{1,60}?-{1,2}>\s*.{1,60}$", re.MULTILINE)

#: A labeled measurement: `North = 18`, `latency: 184 ms`, `req-8821: 8420`. The VALUE must be a
#: scalar (a number with an optional short unit, or one hyphenated token) -- "Note: keep this in
#: mind" carries a sentence, not a datum, and prose colons must not turn a paragraph into a dataset.
_LABELED_VALUE_RE = re.compile(
    r"^\s*(?:[-*•]\s*)?[A-Za-z0-9_][A-Za-z0-9_.\- ]{0,40}?\s*(?:=|:)\s*"
    r"(?:[-+~]?[\d.,]+\s*[a-zA-Z°%]{0,8}|[\w./-]{1,40})\s*$",
    re.MULTILINE,
)

#: Minimum data rows before a message counts as carrying a data block. Two is the same premise-set
#: threshold `_MIN_ASSIGNMENTS` uses: one row can be a passing mention, two is a dataset.
_MIN_DATA_ROWS = 2


def _count_table_rows(text: str) -> int:
    rows = [match.group(0) for match in _TABLE_ROW_RE.finditer(text)]
    return sum(1 for row in rows if not _TABLE_SEPARATOR_RE.match(row))


def turn_supplies_its_data(text: str) -> bool:
    """Whether the message itself carries the data the request works on.

    The invariant this module owns, one lane over: ``turn_answers_itself`` covers a turn that
    ASSIGNS the values a question asks about; this covers a turn that SUPPLIES a data block (a
    table, labeled measurements, or a drawn graph) for calculation, transformation or
    re-presentation. Measured live 2026-09-18: a pasted deployment-results table asking for pass
    rates was widened to current-information by a retrieval lane, four irrelevant web pages were
    bound, and the computed answer was then refused for lacking support from those pages. No page
    on the internet knows the user's own deployment table.

    SHAPE-based, never topic-based: any subject may arrive as rows. A genuine live lookup is not
    blocked by this predicate -- the requirements authority keeps its own price/weather/current
    arms, and only LANE-widening behind the supplied material declines, exactly as it already does
    for attached documents.
    """

    raw = str(text or "")
    if not raw.strip():
        return False
    table_rows = _count_table_rows(raw)
    if table_rows >= _MIN_DATA_ROWS:
        return True
    edges = len(_EDGE_RE.findall(raw))
    if edges >= _MIN_DATA_ROWS:
        return True
    labeled = len(_LABELED_VALUE_RE.findall(raw))
    return labeled >= _MIN_DATA_ROWS
