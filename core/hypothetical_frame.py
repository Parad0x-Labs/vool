"""A turn that supplies its own premises, and what the runtime owes it.

The live defect
---------------
Asked::

    Assume today is January 1st, 2035. The US President is a golden retriever named Buster. The
    currency of France is the 'Baguette'. Based ONLY on these new facts, if I travel from Paris to
    Washington D.C. to sell a toy to the President, what currency will I be paid in, and who am I
    meeting? After answering, explicitly explain why your internal web search tools would fail to
    verify this transaction.

VOOL answered::

    Your name is Bender — that's the name saved in your settings.

The identity fast path swallowed the whole turn. That half is fixed in
:mod:`core.user_identity_authority`, which had no business claiming "who am I meeting?" whether or
not a frame was present. This module owns the other half: once the turn does reach a lane, the lane
must be the right one, and the model must be told what to do with premises the user invented.

Two things go wrong to a turn like this if nothing here exists:

* it routes to ``research``. Measured on the base 03b04b39, ``classify`` claims it at
  ``looks_like_explicit_lookup_request`` on the substring pair "search" + "web" -- taken from the
  clause that asks the runtime to explain why searching would NOT work. ``research`` maps to
  ``provider_role=queen`` with ``allow_paid_fallback=True``, and
  ``core.execution.planner.should_attempt_tool_intent`` returns True for it unconditionally, so the
  turn is handed a web-search catalog to go and check whether a golden retriever is president;
* nothing tells the model the premises are binding. A stipulated fact that contradicts the real
  world reads, to a model with a search tool in hand, as a user error to correct.

What is detected, and what each signal means
--------------------------------------------
Three independent signals, because they license different things and conflating them
over-suppresses:

``stipulated``
    The user is supplying premises: a clause-opening ``assume`` / ``suppose`` / ``imagine`` /
    ``pretend``, or a fiction marker ("in a fictional world", "hypothetically"). The opener has to
    open a clause -- "I assume you know" and "I imagine so" are ordinary English, not frames.

``frame_only``
    The answer is closed to those premises: "based ONLY on these new facts", "using only the
    information above", "ignore the real world".

``search_refused``
    The user said not to look it up: "do not search the web", "without searching", "no web search".

``supplies_premises`` (``stipulated or frame_only``) is what makes an identity word inside the turn
belong to the fiction rather than to settings. ``active`` adds ``search_refused``, which is a
routing fact and not a premise -- "don't search the web, what's my name?" is still a settings
question.

Deliberately NOT detected: a bare "in this scenario". It is ordinary engineering English ("in this
scenario the pod restarts") and treating it as fiction would demote real work.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "HypotheticalFrame",
    "detect_hypothetical_frame",
    "hypothetical_frame_context_lines",
]


@dataclass(frozen=True)
class HypotheticalFrame:
    """Which frame signals a message carries, and the marker text that proved each one."""

    stipulated: bool = False
    frame_only: bool = False
    search_refused: bool = False
    markers: tuple[str, ...] = ()

    @property
    def supplies_premises(self) -> bool:
        """The user has put facts on the table for this answer to run on."""
        return self.stipulated or self.frame_only

    @property
    def active(self) -> bool:
        return self.supplies_premises or self.search_refused


# A clause boundary. The stipulating verbs are only frames when they OPEN one: "assume the year is
# 2035" is a premise, "I assume you know my name" is a hedge, and the difference is entirely
# positional.
_CLAUSE_SPLIT_RE = re.compile(r"[.!?;:\n]+|\s+[-–—]{1,2}\s+")

# A short conversational run-up is allowed ahead of the verb, and so is "let's". Anything else in
# front of it and the verb is not opening the clause.
_FRAME_OPENER_RE = re.compile(
    r"^\s*(?:(?:ok(?:ay)?|so|now|also|and|but|first|please|hey|yo|alright|right|just)\b[\s,]*)*"
    r"(?:let'?s\s+)?"
    r"(?P<verb>assum(?:e|ing)|imagin(?:e|ing)|suppos(?:e|ing)|pretend(?:ing)?|stipulat(?:e|ing)"
    r"|postulat(?:e|ing)|hypothesiz(?:e|ing))\b",
    re.IGNORECASE,
)

# Fiction markers carry their frame wherever they sit in the sentence -- there is no non-frame
# reading of "in a fictional world" the way there is of "I assume".
_FICTION_MARKER_RE = re.compile(
    r"\bfiction(?:al|ally)?\b"
    r"|\bfictitious\b"
    r"|\bhypothetical(?:ly)?\b"
    r"|\bcounterfactual\b"
    r"|\bstipulated\b"
    r"|\bmake[-\s]believe\b"
    r"|\bthought\s+experiment\b"
    r"|\balternat(?:e|ive)\s+(?:universe|reality|timeline|history)\b"
    r"|\bimaginary\s+(?:world|universe|country|scenario|city|planet)\b"
    r"|\b(?:in|under|for)\s+(?:this|that|the|a)\s+"
    r"(?:imagined|hypothetical|fictional|stipulated|future|story|timeline|world)\b"
    r"|\b(?:scenario|story|timeline|world)\s+(?:where|in\s+which)\b"
    r"|\bfast[-\s]?forward\s+to\s+(?:the\s+year\s+)?\d{4}\b"
    r"|\blet'?s\s+say\b"
    r"|\bfor\s+the\s+sake\s+of\s+argument\b",
    re.IGNORECASE,
)

# A premise HEADER: the stipulating verb as a labelled list head, colon attached ("Assume:",
# "Given:"). The clause-opener test above cannot see this shape -- measured live 2026-09-16, the
# message "... the remainder to cash Assume: - EUR/USD = 1.175 - FX fee = 0.6% ..." supplies every
# constant it needs, but after whitespace normalization "cash Assume" is one clause with the verb
# at its END, the frame read as absent, a freshness signal fired on the given constants, two web
# retrievals ran behind the user's explicit prohibition, and the computed answer was then refused
# for lacking the retrieval the user had forbidden. A colon makes the reading unambiguous: a
# hedge ("I assume you know") never carries one.
_FRAME_HEADER_RE = re.compile(
    r"\b(?:assum(?:e|ing)|imagin(?:e|ing)|suppos(?:e|ing)|pretend(?:ing)?|stipulat(?:e|ing)"
    r"|postulat(?:e|ing)|hypothesiz(?:e|ing)|given)\s*:",
    re.IGNORECASE,
)

# A DATA BLOCK the computation runs on: a compute ask plus at least three LABELLED numeric
# premises ("Input tokens: 184,500", "EUR/USD = 1.175", "Gold = $3,680"). The label-plus-value
# shape is what makes them premises rather than a live question's subject matter -- a market ask
# quotes AT most one named current value ("bitcoin at $64,000, overvalued?"), never a block of
# labelled inputs. Measured live 2026-09-16: "A test run produced: Provider North: Input tokens:
# 184,500 ... Calculate: - total tokens - cost per 1 million tokens" was read as a market lookup
# (its ALL-CAPS labels parsed as tickers beside "cost"), the runtime looked up the 'INPUT',
# 'OUTPUT' and 'TOTAL' tickers, and the user got a Markets table of unresolvable symbols instead
# of the arithmetic their message had already supplied every input for.
_COMPUTE_ASK_RE = re.compile(
    r"\b(?:calculat\w*|comput\w*|work\s+out|solve\b|determin\w*|derivative\w*|percent\w*)\b",
    re.IGNORECASE,
)
_LABELLED_PREMISE_RE = re.compile(
    r"\b[\w][\w ./()%'-]{0,30}?\s*(?:=|:)\s*[$€£]?\s?-?\d[\d,\s]*\.?\d*\s*"
    r"(?:%|[a-zA-Z]{1,10}\b)?",
)
_PREMISE_FLOOR = 3


def supplies_computation_premises(text: str) -> bool:
    """Whether the message asks for a computation and supplies the labelled inputs for it.

    One recognizer, consumed by both the frame detector (the block IS a stipulation: the user
    owns the numbers) and the live-data classifier (a turn that carries its own inputs is not a
    live lookup, whatever vocabulary its nouns carry). Kept structural on purpose -- labels with
    values, never a domain word list.
    """
    clean = _normalize(text)
    if not _COMPUTE_ASK_RE.search(clean):
        return False
    return len(_LABELLED_PREMISE_RE.findall(clean)) >= _PREMISE_FLOOR

# The answer is closed to what the user supplied.
_FRAME_ONLY_RE = re.compile(
    r"\bbased\s+(?:only|solely|purely|strictly|exclusively)\s+on\b"
    r"|\b(?:using|use)\s+only\s+(?:these|this|the|those)\b"
    r"|\bonly\s+(?:on\s+)?(?:these|this|those|the)\s+(?:new\s+)?"
    r"(?:facts?|premises?|assumptions?|scenario|information|details?|rules?)\b"
    r"|\bgiven\s+only\s+(?:these|this|those|the)\b"
    r"|\bignor(?:e|ing)\s+(?:the\s+)?real[-\s]world\b"
    r"|\bignor(?:e|ing)\s+what\s+you\s+know\s+about\s+the\s+real\s+world\b"
    r"|\bregardless\s+of\s+(?:the\s+)?real[-\s]world\b",
    re.IGNORECASE,
)

# The user said not to look it up. This is a routing instruction, never a premise.
_SEARCH_REFUSAL_RE = re.compile(
    r"\b(?:do\s+not|don'?t|dont|never)\s+"
    r"(?:search|google|browse|look\s+(?:it|this|that|them|these)\s+up|"
    r"use\s+the\s+(?:web|internet)|go\s+online|verify\s+(?:it\s+)?online|fetch)\b"
    r"|\bwithout\s+(?:searching|a\s+web\s+search|the\s+web|the\s+internet|looking\s+it\s+up)\b"
    r"|\bno\s+(?:web\s+)?(?:search(?:es|ing)?|lookups?|internet|browsing)\b"
    r"|\bskip\s+the\s+(?:web\s+)?search\b"
    r"|\boffline\s+only\b",
    re.IGNORECASE,
)


def _normalize(text: str) -> str:
    return " ".join(str(text or "").replace("’", "'").split())


def detect_hypothetical_frame(text: str) -> HypotheticalFrame:
    """Which frame signals ``text`` carries.

    Cheap and total: three regex passes over a whitespace-normalized copy, no model, no history.
    Callers on the hot path of every chat turn depend on that.
    """
    normalized = _normalize(text)
    if not normalized:
        return HypotheticalFrame()

    markers: list[str] = []

    stipulated = False
    for clause in _CLAUSE_SPLIT_RE.split(normalized):
        # A comma-led continuation is still a clause of its own ("in 2035, suppose the euro is
        # gone"), so each comma-separated run is offered to the opener test as well.
        for run in clause.split(","):
            opener = _FRAME_OPENER_RE.match(run)
            if opener:
                stipulated = True
                markers.append(opener.group("verb").lower())
    fiction = _FICTION_MARKER_RE.search(normalized)
    if fiction:
        stipulated = True
        markers.append(fiction.group(0).lower())
    header = _FRAME_HEADER_RE.search(normalized)
    if header:
        stipulated = True
        markers.append(header.group(0).lower().strip(": "))
    if supplies_computation_premises(normalized):
        # The computation's inputs are IN the message, labelled: the user stipulated them the
        # same way an "Assume:" list does, just without the verb.
        stipulated = True
        markers.append("data block")

    frame_only_match = _FRAME_ONLY_RE.search(normalized)
    if frame_only_match:
        markers.append(frame_only_match.group(0).lower())
    refusal_match = _SEARCH_REFUSAL_RE.search(normalized)
    if refusal_match:
        markers.append(refusal_match.group(0).lower())

    return HypotheticalFrame(
        stipulated=stipulated,
        frame_only=bool(frame_only_match),
        search_refused=bool(refusal_match),
        markers=tuple(dict.fromkeys(markers)),
    )


#: Why a lookup cannot settle a stipulated fact, stated once so the answer path and the tests read
#: the same sentence. It is a statement about what the tools return, not an apology for not having
#: run one: a search backend serves real, currently indexed pages about the actual world, and a
#: premise the user invented for this turn has no such page.
SEARCH_MISMATCH_EXPLANATION = (
    "A web search cannot verify a premise the user stipulated: search tools return real, currently "
    "indexed information about the actual world, so a lookup would return either nothing at all or "
    "the contradicting real-world fact. Neither outcome confirms or refutes a fact that exists only "
    "in this turn's premises. Say that, rather than reporting a tool failure or an outage."
)


def hypothetical_frame_context_lines(frame: HypotheticalFrame | None) -> list[str]:
    """Prompt grounding for a turn that supplies its own premises.

    Emitted as a must-keep bootstrap item so the instruction cannot be trimmed away by the context
    budgeter on a long turn -- which is the same failure mode that once clipped the identity
    separation clause off the end of :func:`core.user_identity_authority.identity_context_lines`.
    """
    if frame is None or not frame.active:
        return []
    lines: list[str] = []
    if frame.supplies_premises:
        lines.append(
            "HYPOTHETICAL FRAME: this message supplies its own premises. Treat every fact the user "
            "stated as true for this answer, including where it contradicts the real world, a real "
            "date, or anything you know. Do not refuse it, do not correct it, and do not hedge it "
            "as false — answer inside the frame."
        )
    if frame.frame_only:
        lines.append(
            "The user closed the answer to those premises. Answer from them alone. Where the "
            "premises are silent on something the question needs, do NOT fill the gap from the real "
            "world and do NOT extend one stated fact to cover another — name the gap and state the "
            "assumption you are making instead."
        )
    if frame.active:
        lines.append(
            "Do not run a web search, fetch or any external lookup to check these premises. "
            + SEARCH_MISMATCH_EXPLANATION
        )
    if frame.supplies_premises:
        lines.append(
            "A name inside the premises — a person, animal, character, app, file or project — is "
            "not the user's name and not yours. Never answer with a saved name because a premise, "
            "a quote or an example contains a name."
        )
    return lines
