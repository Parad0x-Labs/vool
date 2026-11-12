"""Does the person asking want the network used on this turn?

``core.retrieval_constraints`` reads explicit retrieval directives ("do not search the web",
"no lookups"). People say the same thing in many other ways that name no retrieval verb at all:
"I'm on a plane", "our wifi is down", "answer from your own knowledge", "external sources are off
limits", "answer this one blind". Measured on the v0.8 line before this module, 7 of 28 such
messages froze a web prohibition, so the turn could still go online after the person said no.

The recognizer is compositional on purpose. It does not carry a list of sentences that were once
reported as failing; it looks for a *prohibition marker* governing a *network term* inside a single
clause, a network term followed by a *barred predicate* ("the web is off limits", "the router is
dead"), plus a small set of statements that assert unavailability without needing a marker at all
("we're offline", "wifi is down", "from memory"). A phrasing nobody has written yet is matched by
the same parts as one that has.

Direction of failure: when a clause is ambiguous this module answers "prohibited". Refusing a fetch
the user would have permitted costs a follow-up question. Making a fetch the user forbade cannot be
undone. A clause that asks about the network ("is the internet down for everyone?") or explicitly
asks to use it ("no rush, search the web for X") does not close it.

Spans are reported in the caller's own offsets so the retrieval authority can remove exactly the
prohibition (not the question beside it) from the text it treats as retrieval content. Every
pattern is bounded and the text is read once per clause, so the cost stays linear in the message.

Ported from the Aug 15 egress-policy work (agent/egress-policy-authority b01c7148), where the same
recognizer fed a socket-level guard. On the v0.8 line the outbound door and the effect ledger
already own egress, so the recognizer feeds the turn's prohibition authority instead.
"""

from __future__ import annotations

import re
import unicodedata

__all__ = ["network_prohibited_by_user", "prohibition_spans"]

# Words that carry a prohibition. Deliberately generic: these are the ways English says
# "not", not the ways any particular reported message said it.
_MARKER = (
    r"(?:no|not|non|never|without|w/o|avoid|avoiding|skip|skipping|refrain\s+from|"
    r"don'?t|do\s+not|dont|doesn'?t|didn'?t|won'?t|will\s+not|can'?t|cannot|can\s+not|"
    r"unable\s+to|lacking|lack\s+of|disable[d]?|turn(?:ed)?\s+off|switched?\s+off|"
    r"stop|stopped|forget|forgo|zero|nothing|none|nowhere)"
)

# Things that mean "the network". Verbs of retrieval count: forbidding the act forbids the
# egress just as much as forbidding the medium.
#
# Bare `data` and bare `net` are deliberately absent. "I have no data on that" means "no
# information" far more often than "no connection", and "no net profit" is not a network
# statement at all. The unambiguous senses are kept as `live data`, `real-time data`. A live price
# or quote is one family of live data, not the network: "skip live prices and market news" leaves
# the web open the way "don't look up market prices" does.
# `cite` alone is absent too: "don't cite me on this" asks not to be quoted. It counts only when it
# governs an actual source, which is how the retrieval sense is always written.
_NET_TERM = (
    r"(?:web|internet|inter-?net|online|on-?line|network|connectivity|connection|"
    r"wi-?fi|wifi|broadband|ethernet|cellular|signal|router|modem|hotspot|uplink|"
    r"search(?:es|ing)?|google|googl(?:e|ing)|bing|duck-?duck-?go|browse|browsing|browser|"
    r"look(?:ing)?\s*(?:it|them|that|this)?\s*up|lookup|surf(?:ing)?|"
    r"fetch(?:es|ing)?|download(?:s|ing)?|retriev(?:e|es|ing|al)|scrape|scraping|crawl(?:ing)?|"
    r"external\s+(?:source|sources|data|lookup|call|calls|service|services)|"
    r"outside\s+(?:source|sources|data|help)|"
    r"live\s+(?:data|lookup|feed)|real-?time\s+data|"
    r"citations?|cit(?:e|ing)\s+(?:any\s+|a\s+|your\s+|the\s+)?sources?|sources?)"
)

# Statements that assert the network is unavailable or unwanted without needing a marker.
_STANDALONE = re.compile(
    r"\b(?:"
    r"off-?line(?:\s+only)?"
    r"|air-?gapped|air\s+gapped"
    r"|(?:airplane|aeroplane|flight)\s+mode"
    r"|on\s+a\s+(?:plane|flight)|in-?flight|mid-?flight"
    r"|local(?:ly)?[\s-]only|only\s+local(?:ly)?"
    # "from / using / with / based on / out of (nothing but | only)? your own memory". The
    # preposition and the intensifier both vary freely in real messages.
    r"|(?:from|using|with|based\s+on|out\s+of|off)\s+(?:nothing\s+but\s+|just\s+|only\s+)?"
    r"(?:your\s+own\s+|your\s+|my\s+|)(?:memory|knowledge|head)"
    r"|off\s+the\s+top\s+of\s+your\s+head"
    r"|by\s+heart"
    r"|from\s+what\s+you\s+(?:already\s+)?know"
    # "answer this one blind" -- an answer given without looking anything up. Bound to an
    # answering verb so "blind spot" and "blind test" are untouched.
    r"|(?:answer|answering|reply|replying|respond|responding|guess|guessing|go|do\s+it)"
    r"(?:\s+\w+){0,2}\s+blind(?:ly)?"
    r"|(?:i'?m|im|we'?re|were|i\s+am|we\s+are)\s+(?:currently\s+)?off-?line"
    r"|lost\s+(?:my\s+|the\s+|our\s+)?(?:wi-?fi|internet|connection|signal)"
    r"|dead\s+zone|no\s+bars"
    r")\b",
    re.IGNORECASE,
)

# A marker governing a network term inside one clause. The window is bounded so a marker in
# one half of a clause cannot reach a term that belongs to a different idea.
_GOVERNED = re.compile(
    rf"\b(?P<marker>{_MARKER})\b(?:\W+\w+){{0,4}}?\W+(?P<term>{_NET_TERM})",
    re.IGNORECASE,
)

# A marker that opens a statement of doubt negates the speaker's certainty, not the network:
# "I'm not sure whether to search", "I don't know how to google this". A closed class: the
# epistemic words English puts right after a negation.
_HEDGE = re.compile(
    r"\W+(?:really\s+|quite\s+|entirely\s+|totally\s+|even\s+)?"
    r"(?:sure|certain|know|knowing|decide|deciding|remember|recall|understand|clear|idea|clue"
    r"|convinced)\b",
    re.IGNORECASE,
)

# The network terms that name an ACT rather than the medium. An act given an object of its own is
# a narrower exclusion -- "do not fetch server status metrics", "don't download the attachment",
# "don't search my files" -- unless that object is the medium again, a word standing for what
# this turn asks about, or nothing at all ("skip the lookup", "no googling", "don't look it up").
_ACT_TERM = re.compile(
    r"(?:search(?:es|ing)?|googl(?:e|ing)|bing|browse|browsing|"
    r"look(?:ing)?\s*(?:it|them|that|this)?\s*up|lookup|surf(?:ing)?|fetch(?:es|ing)?|"
    r"download(?:s|ing)?|retriev(?:e|es|ing|al)|scrape|scraping|crawl(?:ing)?)",
    re.IGNORECASE,
)
_ACT_OBJECT = re.compile(
    r"\s*(?:(?:for|on|in|into|about|of|through|up|at)\s+)?"
    r"(?:(?:the|a|an|any|my|our|your|this|that|these|those|some|its|their)\s+)?"
    r"(?P<head>[\w'-]+)",
    re.IGNORECASE,
)
_NET_TERM_WORD = re.compile(_NET_TERM, re.IGNORECASE)
# Closed classes only: pronouns and quantifiers, the words for this turn's own question or answer,
# conjunctions, politeness and the adverbs that follow a verb without being its object.
_GENERIC_OBJECT = frozenset(
    {
        "it", "them", "this", "that", "these", "those", "anything", "everything", "something",
        "stuff", "things", "thing", "more", "anymore", "either", "me", "us", "you",
        "answer", "answers", "question", "questions", "result", "results", "one",
        "and", "or", "but", "so", "just", "then", "first", "yet", "too", "though", "instead",
        "unless", "until", "while", "when", "if", "because", "here", "there",
        "now", "today", "again", "around", "out", "please", "pls", "thanks", "thx", "all",
    }
)

# The predicate that follows the subject instead of a marker preceding it: "the web is off
# limits", "the broadband died", "searching is not an option".
_POST_BARRED = re.compile(
    rf"\b(?P<term>{_NET_TERM})\b(?:\W+\w+){{0,3}}?\W+"
    r"(?P<predicate>"
    r"does\s*n[o']?t|do\s*n[o']?t|is\s*n[o']?t|are\s*n[o']?t|ai\s*n[o']?t|was\s*n[o']?t|"
    r"wo\s*n[o']?t|ca\s*n[o']?t|cannot|has\s*n[o']?t|have\s*n[o']?t|no\s+longer|never"
    r"|died|dead|down|out|off|gone|broken|dropped|cut\s+out|failed|crashed|unavailable"
    r"|none|nil|zero"
    r"|off[\s-]limits|banned|forbidden|prohibited|not\s+allowed|not\s+an\s+option"
    r"|out\s+of\s+the\s+question|not\s+available|not\s+working|not\s+possible"
    r")\b",
    re.IGNORECASE,
)

# A negation that opens a conjunct of its own is an imperative with a verb of its own: "use
# authoritative sources and do not guess", "give me the figures with sources, do not guess",
# "search the web and don't guess". Read as a predicate of the network term before it, it turned
# 18 of 22 such requests for research into a ban on the web. It says something about the network
# only when the network term is what that conjunct is about: the term opens the conjunct with
# nothing but determiners before it, and it names the medium rather than an act ("the internet,
# don't use it", "web search -- never").
_IMPERATIVE_NEGATION = re.compile(r"do\s*n[o']?t|never", re.IGNORECASE)
_CONJUNCT_BREAK = r"(?:[,;()\[\]\"&-]|\b(?:and|or|nor|then|so|plus)\b)"
_OPENS_CONJUNCT = re.compile(_CONJUNCT_BREAK + r"\W*\Z", re.IGNORECASE)
_DETERMINER = (
    r"(?:the|a|an|my|our|your|their|his|her|its|this|that|these|those|any|all|some|no)"
)
_TOPIC_LEAD = re.compile(
    r"(?:\A|" + _CONJUNCT_BREAK + r")\W*(?:" + _DETERMINER + r"\W+){0,3}\Z",
    re.IGNORECASE,
)
# A citing act ("cite your sources") is an act like a search, whatever the word order.
_CITING = re.compile(r"cit(?:e|ing)\b", re.IGNORECASE)
# How far before a word the bounded look-behinds below read. A run of determiners or three
# ordinary words and their separators fits well inside it.
_LEAD_WINDOW = 80

# "and" after the word a marker negates opens a new instruction too: "do not guess and cite your
# sources", "don't speculate and search online" ask for the network. The negation keeps its reach
# over a second object ("don't use apps and the web", "no apps and web search"), over the verbs
# that take "and" in place of "to" ("don't go and look it up", "don't try and google it"), and
# over "or" ("never guess or search").
_JOINT = re.compile(r"(?P<before>\w+)\s+(?:and|then|plus|&)\s+", re.IGNORECASE)
_SERIAL_VERBS = frozenset({"go", "try", "come"})
_DETERMINER_WORD = re.compile(_DETERMINER + r"\b", re.IGNORECASE)

# A statement of being offline that is itself negated says the opposite: "don't answer from
# memory, search the web", "I'm not offline". A closed class of verb negations reaching it across
# at most three words, with no phrase break between. "no" and the quantifier markers are left out:
# "no I'm offline" answers a question rather than negating the statement.
_NEGATED_LEAD = re.compile(
    r"\b(?:don'?t|dont|do\s+not|doesn'?t|does\s+not|didn'?t|did\s+not|won'?t|will\s+not|"
    r"can'?t|cannot|can\s+not|never|not)"
    r"(?:[^\w,;:()\[\]\"-]+\w+){0,3}?[^\w,;:()\[\]\"-]+\Z",
    re.IGNORECASE,
)

# Clause boundaries. "but" is a contrast in "search, but not google" and an intensifier in
# "nothing but your own knowledge"; the lookbehinds keep the quantifier phrases intact. A line
# break is not a boundary here: a sentence wrapped across lines is still one sentence.
_CLAUSE_SPLIT = re.compile(
    r"[.!?;:]+"
    r"|(?<!\bnothing)(?<!\banything)(?<!\bnought)(?<!\bnone)(?<!\ball)"
    r"\s+(?:but|however|though|although|instead|whereas)\s+",
    re.IGNORECASE,
)

# A clause that ASKS about the state of the network rather than asserting it. "is the internet
# down for everyone" wants an answer; "the internet is down" states a constraint.
_INTERROGATIVE_LEAD = re.compile(
    r"^\s*(?:is|are|was|were|do|does|did|can|could|will|would|should|has|have|"
    r"why|what|whats|what's|how|when|where|who|any|anyone|is\s+there|are\s+there)\b",
    re.IGNORECASE,
)

# An explicit instruction TO use the network, which must not be read as a prohibition just
# because a stray "no" sits elsewhere in the clause ("no rush, search the web for X").
_EXPLICIT_REQUEST = re.compile(
    rf"\b(?:please\s+)?(?:go\s+)?(?:and\s+)?(?:use|check|search|google|browse|look\s+up|fetch|"
    rf"retrieve|consult)\s+(?:the\s+|on\s+the\s+|online\s+|)(?:{_NET_TERM})",
    re.IGNORECASE,
)

_CHAR_FOLD = {"’": "'", "ʼ": "'", "‘": "'", "–": "-", "—": "-"}


def _fold(text: str) -> tuple[str, list[int]]:
    """The text the patterns read, and where each of its characters came from.

    Whitespace runs collapse to one space and the ends are trimmed, exactly as the patterns were
    written to expect: the "nothing but" lookbehinds and the clause-leading question test read a
    single space. Curly apostrophes and unicode dashes otherwise defeat every contraction above,
    and full-width letters would hide a whole word. A character whose compatibility form is
    longer than one character is kept as written; no pattern here needs it.

    The offsets list maps every character of the folded text back to the caller's text, so a
    span found here can be reported in the caller's own offsets.
    """
    out: list[str] = []
    offsets: list[int] = []
    pending_space: int | None = None
    for index, char in enumerate(text):
        folded = _CHAR_FOLD.get(char)
        if folded is None:
            folded = char if char.isascii() else unicodedata.normalize("NFKC", char)
            if len(folded) != 1:
                folded = char
        if folded.isspace():
            if out and pending_space is None:
                pending_space = index
            continue
        if pending_space is not None:
            out.append(" ")
            offsets.append(pending_space)
            pending_space = None
        out.append(folded)
        offsets.append(index)
    return "".join(out), offsets


def _clause_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    cursor = 0
    for match in _CLAUSE_SPLIT.finditer(text):
        spans.append((cursor, match.start()))
        cursor = match.end()
    spans.append((cursor, len(text)))
    return spans


def _scoped_act(clause: str, governed: re.Match[str]) -> bool:
    """Whether a governed act has an object of its own, so it excludes that object only."""
    if _ACT_TERM.fullmatch(governed.group("term")) is None:
        return False
    follow = _ACT_OBJECT.match(clause, governed.end())
    if follow is None:
        return False
    head = follow.group("head").lower()
    return head not in _GENERIC_OBJECT and _NET_TERM_WORD.fullmatch(head) is None


_PHRASE_BREAK = re.compile(r"[,;:()\[\]\"-]")


def _is_act(term: str) -> bool:
    return _ACT_TERM.fullmatch(term) is not None or _CITING.match(term) is not None


def _joins_new_instruction(clause: str, governed: re.Match[str]) -> bool:
    """Whether an "and" between a marker and the network term opens an instruction of its own."""
    term_start = governed.start("term")
    for joint in _JOINT.finditer(clause, governed.end("marker"), term_start):
        if joint.group("before").lower() in _SERIAL_VERBS:
            continue
        if joint.end() == term_start:
            if _is_act(governed.group("term")):
                return True
            continue
        if _DETERMINER_WORD.match(clause, joint.end(), term_start) is None:
            return True
    return False


def _standalone(clause: str) -> re.Match[str] | None:
    """The first statement of being offline that the clause does not negate."""
    position = 0
    while True:
        match = _STANDALONE.search(clause, position)
        if match is None:
            return None
        if _NEGATED_LEAD.search(clause, max(0, match.start() - _LEAD_WINDOW), match.start()) is None:
            return match
        position = match.start() + 1


def _post_barred(clause: str) -> re.Match[str] | None:
    """The first network term followed by a predicate that bars it.

    A negation opening a conjunct of its own bars the term only when the term is that conjunct's
    topic: it opens the conjunct, with nothing but determiners before it, and names the medium.
    """
    position = 0
    while True:
        match = _POST_BARRED.search(clause, position)
        if match is None:
            return None
        imperative = (
            _IMPERATIVE_NEGATION.fullmatch(match.group("predicate")) is not None
            and _OPENS_CONJUNCT.search(clause, match.end("term"), match.start("predicate")) is not None
        )
        if not imperative:
            return match
        term_start = match.start("term")
        topic = (
            not _is_act(match.group("term"))
            and _TOPIC_LEAD.search(clause, max(0, term_start - _LEAD_WINDOW), term_start) is not None
        )
        if topic:
            return match
        position = term_start + 1


def _governed_prohibition(clause: str) -> re.Match[str] | None:
    """The first marker that governs a network term as a prohibition.

    Skipped: a marker opening a statement of doubt ("not sure whether to search"), a marker cut
    off from the term by a phrase break ("no rush, search the web" -- the "no" belongs to "rush")
    or by an "and" that opens a new instruction ("do not guess and cite your sources"), and an act
    given an object of its own ("do not fetch server status metrics"). Each search starts after
    the previous marker, so the clause is read once.
    """
    position = 0
    while True:
        governed = _GOVERNED.search(clause, position)
        if governed is None:
            return None
        position = governed.end("marker")
        if (
            _HEDGE.match(clause, position) is not None
            or _PHRASE_BREAK.search(clause, position, governed.start("term")) is not None
            or _joins_new_instruction(clause, governed)
            or _scoped_act(clause, governed)
        ):
            continue
        return governed


def _asks_elsewhere(clause: str, governed: re.Match[str]) -> bool:
    """Whether the clause also asks, outside the prohibition, for the network to be used.

    "don't search google, use the web" asks for a narrower kind of retrieval, so it does not close
    the boundary. "never search the internet" contains an explicit request too -- but it is the
    very act the marker negates, so it is no request at all.
    """
    return any(
        not governed.start() <= request.start() < governed.end()
        for request in _EXPLICIT_REQUEST.finditer(clause)
    )


def _clause_prohibition(clause: str) -> tuple[int, int] | None:
    """The span inside ``clause`` that carries a prohibition, or None."""
    leading = len(clause) - len(clause.lstrip())
    candidate = clause.strip()
    if not candidate:
        return None
    hit: tuple[int, int] | None = None
    if _INTERROGATIVE_LEAD.match(candidate) is None:
        for find in (_standalone, _post_barred):
            match = find(candidate)
            if match is not None:
                hit = match.span()
                break
    if hit is None:
        governed = _governed_prohibition(candidate)
        if governed is None or _asks_elsewhere(candidate, governed):
            return None
        hit = governed.span()
    return leading + hit[0], leading + hit[1]


def prohibition_spans(text: str) -> tuple[tuple[int, int], ...]:
    """Where, in ``text``'s own offsets, the person told us not to use the network: one span per
    clause that says so, covering the words that say it (not the question beside them)."""
    folded, offsets = _fold(str(text or ""))
    found: list[tuple[int, int]] = []
    for start, end in _clause_spans(folded):
        hit = _clause_prohibition(folded[start:end])
        if hit is not None:
            found.append((offsets[start + hit[0]], offsets[start + hit[1] - 1] + 1))
    return tuple(found)


def network_prohibited_by_user(text: str) -> bool:
    """True when this turn's message tells us not to use the network."""
    return bool(prohibition_spans(text))
