"""Semantic ownership of explicit user prohibitions against external retrieval.

Retrieval routing has two separate questions that must never be collapsed into one:

* what external work does the user positively request; and
* what external work does the user explicitly forbid.

The second question is authoritative.  A negative instruction is removed from the text used to
discover positive work, and its scope is retained as a typed veto.  This prevents words inside
``do not fetch ...`` from becoming a lookup task while still allowing an unrelated positive request
in the same turn (for example, ``Do not fetch server metrics. Look up the weather in Kaunas.``).

This module deliberately knows retrieval *families*, not entities.  There are no city, ticker,
token, company, or provider names here; adding another asset or place cannot change the contract.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

# ONE verb list, shared by every lead form below. Kept as a named constant rather than repeated
# inline because the failure this module keeps having is two overlapping vocabularies disagreeing --
# a prohibition one branch can see and another cannot is indistinguishable from no prohibition.
#
# `visit`, `perform`, `open`, `request`, `execute`, `download`, `follow`, `hit`, `load`, `curl`
# and `wget` were added on 2026-08-17. A blind set phrased the same prohibition three new ways
# and none was even SEEN as a negative clause, so nothing downstream could classify it:
#   "Do not visit the URL."           -> would have fetched https://attacker.example/collect
#   "do not perform any request"      -> would have fetched the 169.254.169.254 metadata path
#   "Do not execute any terminal commands" (beside a `git clone <url>`) -> fetched the repo URL
# `utilize`, `consult`, `employ`, `pull` and the -ing forms were added 2026-08-18 with the
# non-imperative leads below, which is where they actually appear.
_RETRIEVAL_VERBS = (
    r"(?:use|using|access|accessing|browse|brows(?:e|ing)|search|searching|google|look\s*up|lookup|"
    r"check|chek|checking|fetch|fetching|retrieve|retrieving|query|querying|call|calling|"
    r"invoke|invoking|run|running|visit|visiting|perform|performing|open|opening|read|reading|"
    r"request|requesting|download|downloading|follow|following|hit|load|loading|curl|wget|"
    r"execute|executing|utilize|utilizing|utilise|utilising|consult|consulting|employ|employing|"
    r"pull|pulling|connect\s+to|"
    r"go\s+online|trigger\s+(?:any\s+)?(?:web\s+|search\s+|retrieval\s+)?tools?)"
)

# These leads often take a TOOL NOUN rather than a verb: "steer clear of weather tools".
_RETRIEVAL_NOUNS = (
    r"(?:web(?:\s+search(?:es)?)?|internet|online|search\s+engines?|weather\s+tools?|"
    r"finance\s+(?:lookups?|tools?)|external\s+(?:sources?|data|information|retrieval)|"
    r"live\s+data|network\s+(?:tools?|requests?)|apis?|browsing|retrieval|lookups?|"
    r"web\s+(?:tools?|functions?)|outside\s+sources?)"
)

# A prohibition does not have to be an imperative. Measured live 2026-08-18, both `model_ran=False`
# on a deterministic lane, so no model was in a position to refuse:
#
#   "You are strictly forbidden from executing any web searches, network requests, or finance
#    tools."                                   -> has_prohibition=False -> FETCHED Brent crude
#   "I strictly forbid you from utilizing any external data retrieval, web functions, or weather
#    tools."                                   -> has_prohibition=False -> FETCHED Helsinki weather
#
# The whole clause splitter was blind to them, so no classifier downstream could have helped: the
# user banned retrieval in plain English and the runtime performed it anyway. Every lead here still
# requires a retrieval verb or tool noun next to it, which is what keeps "the Forbidden City",
# "it may not rain", "steer clear of credit card debt" and "a refrain in music" untouched.
# The lead must be ADDRESSED TO THE ASSISTANT -- an explicit `you`/`yourself`, or an imperative,
# which by definition has no subject and is clause-initial.
#
# The first version of this constant matched any subject, and that was a live regression, measured
# on the shipped runtime before it reached anyone:
#
#   "He may not use the internet at work, but what's the bitcoin price?"
#       -> forbids_external_retrieval=True, the price turn REFUSED. The user restricted nobody.
#   "I'm not permitted to use Bloomberg, so what's the gold price?"
#       -> eligible_text collapses to "I'm ?" and reason_codes to ('stable_knowledge',) -- the
#          QUESTION IS DELETED and answered from training data with no refusal signal at all.
#
# Both are ordinary English describing a constraint on a PERSON. A statement about what someone
# else may do is not an instruction to this runtime, and the second shape is the worse of the two
# because nothing tells the user a refusal happened.
#
# `it is not permitted to`, `refrain from`, `steer clear of` and `under no circumstances` keep no
# explicit subject because they are imperatives; they are anchored to a clause boundary instead, so
# "How do I steer clear of online scams?" and "Am I not permitted to use the internet here?" -- both
# measured false positives -- cannot reach them.
_NON_IMPERATIVE_PROHIBITION_LEAD = (
    r"(?:"
    r"you\s+(?:are\s+|were\s+)?(?:strictly\s+|expressly\s+|absolutely\s+)?forbidden\s+(?:from\s+)?"
    r"|(?:strictly\s+|expressly\s+)?forbid\s+(?:you|yourself)\s+(?:from\s+)?"
    r"|you\s+may\s+not\s+"
    r"|you\s+are\s+not\s+(?:permitted|allowed)\s+to\s+"
    r"|(?:^|(?<=[.!?;\n])|(?<=,\sbut\s)|(?<=\sbut\s))\s*"
    r"(?:it\s+is\s+not\s+permitted\s+to\s+|refrain\s+from\s+|steer\s+clear\s+of\s+"
    r"|under\s+no\s+circumstances\s+(?:should\s+you\s+|may\s+you\s+)?(?:do\s+not\s+)?)"
    r")"
)

# Cancelling an operation is a prohibition on it. The cue list in `_CANCEL_PRIOR_RETRIEVAL_RE`
# below required the cancellation to TRAIL the verb ("look it up ... never mind"), so the ordinary
# imperative form was invisible and the live-info lane claimed the very turn that called it off.
# Measured 2026-08-18, `live_info_mode()` on the clause alone:
#
#   "ACTUALLY, cancel the live rate lookup."      -> "fresh_lookup"
#   "WAIT. Abort the product lookup completely."  -> "fresh_lookup"
#   "skip the web search"                         -> "fresh_lookup"
#   "forget the weather lookup"                   -> "fresh_lookup"
#
# The cancelled THING must be a retrieval noun, never a verb: "ignore case when searching" is an
# instruction about how to search, not a withdrawal of it, and matching the verb form would read
# it as a prohibition. Nouns and verbs both come from the shared constants above so this cannot
# drift away from the vocabulary the positive directives use.
_CANCEL_VERBS = r"(?:cancel|abort|skip|drop|disregard|forget|call\s+off|scrap)"
_IMPERATIVE_CANCEL_RE = (
    rf"{_CANCEL_VERBS}\s+(?:the\s+|that\s+|any\s+|all\s+|my\s+|your\s+)?"
    rf"(?:\w+\s+){{0,2}}?{_RETRIEVAL_NOUNS}"
)

_NEGATED_RETRIEVAL_DIRECTIVE_RE = re.compile(
    r"\b(?:"
    r"(?:do\s+not|don['’]?t|dont|never|must\s+not|mustn['’]?t|avoid|skip)\s+"
    r"(?:please\s+|pls\s+)?"
    + _RETRIEVAL_VERBS
    + r"|(?:no\s+need\s+to|without)\s+"
    # A determiner does not change the directive: "without using ANY tools" and "no need to use any
    # tools" are the same prohibitions as "without using tools" (revision 6: the determiner form was
    # never seen as a negative clause, so the turn kept every tool it had just ruled out).
    r"(?:using\s+|use\s+|doing\s+)?"
    r"(?:the\s+|any\s+)?(?:web|internet|online|tools?|browsing|searching|looking\s+it\s+up|"
    r"lookups?|fetching|retrieval|retrieving)"
    r"|no\s+(?:web|internet|online|tools?|browsing|search|searching|searches|lookups?|"
    r"fetching|retrieval|live\s+(?:data|lookup|lookups|search|searches|fetch|fetching))\b"
    r"|from\s+(?:your|memory|existing)\s+(?:own\s+)?knowledge\b"
    r"|offline\s+only\b"
    + rf"|\b{_NON_IMPERATIVE_PROHIBITION_LEAD}(?:any\s+|the\s+)?(?:{_RETRIEVAL_VERBS}|{_RETRIEVAL_NOUNS})"
    + rf"|\b{_IMPERATIVE_CANCEL_RE}"
    + r")",
    re.IGNORECASE,
)

# A negative instruction normally ends at a sentence boundary.  Contrastive transitions are also
# boundaries because they introduce independent positive work: "don't search X, but fetch Y".
_HARD_CLAUSE_END_RE = re.compile(r"[.!?;\n]+")
_CONTRASTIVE_END_RE = re.compile(
    r",?\s+\b(?:but|however|instead|rather|yet|then|and\s+then)\b",
    re.IGNORECASE,
)
_EXPLANATION_TRANSITION_RE = re.compile(
    r",?\s+\b(?:pls\s+|please\s+)?(?:just\s+|only\s+)?"
    r"(?:explain|define|describe|tell\s+me|answer)\b",
    re.IGNORECASE,
)
# The verb that opened the cancelled request comes from the SHARED vocabulary, not a private
# seven-word list. That list omitted `find`, so "Find me the best mechanical keyboard under $100 on
# the web. WAIT. Abort the product lookup completely." kept its opening clause in the eligible text
# and still searched. `find` is added here rather than to `_RETRIEVAL_VERBS` because it only reads
# as retrieval in this cancelling context -- "find the bug in this function" is not a web lookup.
_CANCEL_PRIOR_RETRIEVAL_RE = re.compile(
    rf"\b(?:{_RETRIEVAL_VERBS}|find|finding|locate|locating)\b"
    rf"[\s\S]{{0,180}}?(?:\b(?:never\s*mind|nevermind|cancel\s+that|scratch\s+that|forget\s+that)\b"
    rf"|\b{_IMPERATIVE_CANCEL_RE})",
    re.IGNORECASE,
)

#: How the answer must LOOK, which is never what the turn is about. Live evidence (set6-24,
#: 2026-08-13): "Explain quantum tunneling using exactly three words. NO JSON. NO markdown. NO
#: punctuation." performed ten web calls, because "json" is a specific-troubleshooting marker and
#: the format directive left it sitting in the retrieval-eligible text. A format word is not a
#: topic: it can neither create retrieval intent nor belong in a search query. The vocabulary is a
#: closed list of output formats -- domain authority, not prompt tokens.
_OUTPUT_FORMAT_DIRECTIVE_RE = re.compile(
    r"(?:\b(?:no|without|avoid|never\s+use|do\s*n[o']?t\s+use|don'?t\s+use)\s+(?:any\s+)?"
    r"(?:json|markdown|md|punctuation|curly\s+braces?|braces?|code\s*(?:blocks?|fences?)|"
    r"bullet\s*(?:points?|lists?)|headings?|bold|italics?|emojis?|tables?|quotes?|yaml|xml|html|"
    r"latex|formatting)\b"
    r"|\b(?:raw|plain)\s+text\s+only\b)",
    re.IGNORECASE,
)

_ALL_TOOL_SCOPE_RE = re.compile(
    r"\b(?:no|without)\s+(?:using\s+)?(?:any\s+)?tools?\b"
    r"|\b(?:do\s+not|don['’]?t|dont|never|must\s+not|mustn['’]?t)\s+"
    r"(?:use|call|invoke|run)\s+(?:any\s+)?tools?\b"
    r"|\bno\s+need\s+to\s+(?:use|call|invoke|run)\s+(?:any\s+)?tools?\b",
    re.IGNORECASE,
)
# Only a bare list item broadens a negative clause to all tools; "web tools"
# and other qualified families retain their narrower scope.
_ALL_TOOL_LIST_ITEM_RE = re.compile(
    r"(?:,|\b(?:and|or)\b)\s*(?:(?:and|or)\s+)?(?:any\s+)?tools?"
    r"(?=\s*(?:,|\b(?:and|or)\b|$))",
    re.IGNORECASE,
)
_GLOBAL_EXTERNAL_SCOPE_RE = re.compile(
    # Same non-imperative leads as the directive recogniser, reusing the SAME constant. This regex
    # kept its own private copy of the imperative lead list, which is why "Do not use the internet"
    # set forbids_external_retrieval and "Under no circumstances should you consult the internet"
    # did not -- a third overlapping vocabulary, disagreeing with the other two. The filler is
    # bounded and non-greedy so "accessing or referencing anything online" is reached without the
    # pattern being able to wander across a sentence.
    rf"\b{_NON_IMPERATIVE_PROHIBITION_LEAD}[\w\s,]{{0,40}}?"
    r"(?:web(?:\s+search(?:es)?)?|internet|online|external\s+(?:sources?|data|information|retrieval|tools?)|"
    r"live\s+(?:sources?|data))\b"
    # The same determiner law as the directive recogniser: "without using ANY web search" scopes the web.
    r"|\b(?:no|without)\s+(?:using\s+)?(?:the\s+|any\s+)?(?:web|internet|online)\b"
    r"|\b(?:do\s+not|don['’]?t|dont|never|must\s+not|mustn['’]?t|avoid)\s+"
    r"(?:use|access|browse|search)\s+(?:the\s+)?(?:web|internet|online)\b"
    r"|\bno\s+live\s+(?:data|lookup|lookups|search|searches|fetch|fetching)\b"
    r"|\bwithout\s+(?:looking(?:\s+it)?\s+up|searching|browsing|fetching|retrieving)\b"
    r"|\b(?:do\s+not|don['’]?t|dont|never|must\s+not|mustn['’]?t)\s+"
    r"(?:trigger|invoke|run|use)\s+(?:any\s+)?(?:web\s+|internet\s+|online\s+|search\s+|external\s+)?tools?\b"
    # A DISJOINED imperative: "Do not browse or use external tools." The lead governs every
    # disjunct of its clause, so the tools noun still binds to it with another retrieval verb
    # in between. Measured 2026-09-16 live: this exact sentence left
    # `forbids_external_retrieval` False, the grounding lane ran two failed web retrievals
    # behind the user's back, and the finished answer was then REFUSED for lacking the very
    # retrieval the user had prohibited. The filler stays inside the clause (no sentence
    # enders) and the noun keeps its retrieval qualifiers, so a non-retrieval "use X tools"
    # ("do not use Python tools") does not match.
    r"|\b(?:do\s+not|don['’]?t|dont|never|must\s+not|mustn['’]?t|avoid|skip)\b[^.;!?]{0,60}?\b"
    r"(?:use|using|run|running|invoke|invoking|trigger|triggering|call|calling)\s+(?:any\s+)?"
    r"(?:web\s+|internet\s+|online\s+|search\s+|external\s+|network\s+)?tools?\b"
    r"|\b(?:do\s+not|don['’]?t|dont|never|must\s+not|mustn['’]?t|avoid|skip)\s+"
    r"(?:fetch|retrieve|query)\b[^.!?;\n]{0,40}\bexternally\b"
    r"|\bfrom\s+(?:your|memory|existing)\s+(?:own\s+)?knowledge\b"
    r"|\boffline\s+only\b",
    re.IGNORECASE,
)
_ACTION_WITHOUT_TARGET_RE = re.compile(
    r"\b(?:do\s+not|don['’]?t|dont|never|must\s+not|mustn['’]?t|avoid|skip)\s+"
    r"(?:please\s+|pls\s+)?"
    r"(?:browse|search|trigger(?:\s+(?:any\s+)?search\s+tools?)?|google|look\s*up|lookup|check|chek|fetch|retrieve|query)"
    r"(?:\s+(?:anything|anything\s+live|data|information|online|live))?"
    r"(?:\s+externally)?\s*[.!?;]?\s*$"
    # A CONJOINED prohibition: "Do not browse and do not invent any additional test results."
    # The verb's own clause continues into another prohibition, so the end-of-clause anchor above
    # does not fire -- and until this arm existed the prohibition mapped only to the narrow
    # web_fetch family while a web SEARCH still ran behind the user's back (measured live
    # 2026-09-18: a supplied deployment-results table that said "Do not browse" was searched,
    # four irrelevant pages were bound, and the computed answer was then withheld for lacking
    # support from those pages). The lead governs every conjoined disjunct, the same law the
    # "Do not browse or use external tools" arm above already states for "or".
    r"|\b(?:do\s+not|don['’]?t|dont|never|must\s+not|mustn['’]?t|avoid|skip)\s+"
    r"(?:browse|search|google|look\s*up|lookup|fetch|retrieve|query)\b"
    r"\s*(?:,\s*(?:and|or|nor)?|(?:and|or|nor))\s*"
    r"(?:do\s+not|don['’]?t|dont|never|must\s+not|mustn['’]?t|avoid|skip|invent\w*|change\w*|alter\w*|add\w*|make\s+up|fabricat\w+|modif\w+)\b"
    r"|\bno\s+(?:browsing|search|searching|searches|lookups?|fetching|retrieval)\s*[.!?;]?\s*$",
    re.IGNORECASE,
)

_WEATHER_SCOPE_RE = re.compile(
    r"\b(?:weather|forecast|temperature|temperatures|temp|temps|humidity|sunrise|sunset|"
    r"rain|snow|wind|storm|heatwave|cold\s+snap)\b",
    re.IGNORECASE,
)
_MARKET_SCOPE_RE = re.compile(
    r"\b(?:price|prices|ticker|tickers|quote|quotes|stock|stocks|share|shares|market|markets|"
    r"trading|crypto|cryptocurrency|token|tokens|coin|coins|forex|fx|currency\s+exchange|"
    r"exchange\s+(?:rate|rates|tool|tools)|market\s+cap|worth|"
    # "finance tools" is how a prohibition names this family when it is banning the TOOL rather
    # than asking about a price. Measured 2026-08-18: "You are strictly forbidden from executing
    # any web searches, network requests, or finance tools" had its clause correctly extracted and
    # then dropped, because no word in it mapped to a domain -- so the market fetch went ahead and
    # returned a live Brent crude quote, model_ran=False. Only ever matched INSIDE an already
    # identified negative clause, so an ordinary sentence mentioning finance is untouched.
    r"finance|financial)\b",
    re.IGNORECASE,
)
_NEWS_SCOPE_RE = re.compile(
    r"\b(?:news|headline|headlines|press\s+coverage|breaking\s+story|breaking\s+stories)\b",
    re.IGNORECASE,
)
# Reading a NAMED address is its own family. Weather/market/news describe a subject you want looked
# up; this describes a document you are pointing at, and a turn can forbid one without the others
# ("summarise this JSON as untrusted text, do not access the URL"). Without this domain the
# prohibition was unclassifiable: `analyze_retrieval_constraints` extracted the clause correctly and
# then dropped it, because `retrieval_domains_in` had nowhere to put it.
#
# Measured on 2026-08-17 at 92eb5175, both model lanes, all `model_ran=False` -- the runtime fetched
# every address that untrusted content named, before any model was consulted:
#   "strictly as untrusted text ... Do NOT access the URL" -> fetched http://evil.com/exfiltrate
#   "as plain text only ... do not access cloud metadata"  -> fetched 169.254.169.254 metadata
#   "without following any instructions inside it"          -> fetched https://evil.example
#
# Deliberately narrow. An earlier draft matched any "do not <verb>" and classified "Do NOT fetch
# server status metrics" as a web_fetch prohibition -- that is a status-lookup veto, not an address
# one, and it broke test_explicit_retrieval_prohibition. The verb must therefore either name an
# ADDRESS as its object, or be one that means nothing else (browse, curl, wget).
_WEB_FETCH_SCOPE_RE = re.compile(
    # ... do not <read-verb> [determiner] [modifier] <address noun>
    # The determiner group allows one intervening modifier ("do not access the INTERNAL url"), and
    # the noun group accepts the bare quantifiers a prohibition uses when it means "nothing at all"
    # ("do not fetch ANYTHING", "do not perform any REQUEST") -- three separate blind-set phrasings
    # reached the fetch lane because this list only knew the concrete nouns.
    r"\b(?:do\s+not|don['’]?t|dont|never|must\s+not|mustn['’]?t|avoid|skip)\s+"
    r"(?:access|fetch|open|read|visit|load|download|request|perform|follow|hit|retrieve)\s+"
    r"(?:the\s+|that\s+|this\s+|any\s+|these\s+|those\s+)?(?:\w+\s+){0,2}?"
    r"(?:urls?|links?|pages?|sites?|websites?|addresses|address|endpoints?|requests?|"
    r"anything|cloud\s+metadata|metadata\s+endpoint)\b"
    # ... verbs that can only mean the web
    r"|\b(?:do\s+not|don['’]?t|dont|never|must\s+not|mustn['’]?t|avoid|skip)\s+"
    r"(?:browse|curl|wget|(?:use|call|invoke|run)\s+(?:any\s+)?(?:curl|wget|network\s+tools?))\b"
    r"|\b(?:no|without)\s+(?:using\s+)?(?:any\s+)?network\s+tools?\b"
    # The FOURTH place in this module that kept a private imperative-only lead list. Same shared
    # constant as the other three, because the recurring defect here is not a missing word -- it is
    # four vocabularies that can disagree. Measured 2026-08-18: "Download http://files.local/
    # agent.tar.gz into Downloads. You are strictly forbidden from performing network downloads."
    # was SEEN as a prohibition and mapped to no domain, so the download lane wrote it to disk.
    + rf"|\b{_NON_IMPERATIVE_PROHIBITION_LEAD}(?:any\s+|the\s+)?(?:\w+\s+){{0,2}}?"
    + r"(?:urls?|links?|pages?|sites?|websites?|addresses|address|endpoints?|requests?|"
    r"downloads?|downloading|network\s+(?:tools?|downloads?|requests?)|curl|wget|"
    r"anything|cloud\s+metadata|metadata\s+endpoint)\b"
    # And the imperative form of the same download nouns, which was equally absent.
    r"|\b(?:do\s+not|don['’]?t|dont|never|must\s+not|mustn['’]?t|avoid|skip)\s+"
    r"(?:perform|do|make)\s+(?:any\s+)?(?:network\s+)?downloads?\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RetrievalConstraints:
    """The negative retrieval contract plus text that contains only candidate positive work."""

    eligible_text: str
    has_prohibition: bool
    forbids_all_tools: bool
    forbids_external_retrieval: bool
    prohibited_toolsets: frozenset[str]
    negative_clauses: tuple[str, ...]

    def forbids(self, toolset: str) -> bool:
        """Whether a retrieval family is explicitly unavailable for this turn."""

        normalized = str(toolset or "").strip().lower()
        if self.forbids_all_tools:
            return True
        if normalized in {"web", "web_search", "web_fetch", "fresh_lookup", "external_retrieval"}:
            # A turn may forbid reading a NAMED address without forbidding external retrieval at
            # large ("do not access the URL" while still asking for a weather lookup), so an
            # explicitly prohibited family counts here too. Reading only the global flag made the
            # `web_fetch` domain inert.
            return self.forbids_external_retrieval or normalized in self.prohibited_toolsets
        if self.forbids_external_retrieval:
            return True
        return normalized in self.prohibited_toolsets

    def forbids_candidate(self, text: str) -> bool:
        """Whether the candidate text belongs only to a specifically prohibited live family."""

        if self.forbids_all_tools or self.forbids_external_retrieval:
            return True
        domains = retrieval_domains_in(text)
        return bool(domains and domains.issubset(self.prohibited_toolsets))


@dataclass(frozen=True)
class RetrievalRequestAuthority:
    """The request text allowed to create retrieval work and its negative authority.

    A chat JSON object is still user text, not a privileged system message.  When it uses the
    narrow ``task``/``intent`` envelope, though, only that field describes the work.  Presentation
    siblings such as ``role``, ``format``, ``NO JSON`` and an arbitrary ``command`` must never turn
    into search intent.  Negative rules remain authoritative independently of presentation.
    """

    candidate_text: str
    constraints: RetrievalConstraints
    structured: bool = False


def _negative_clause_end(text: str, start: int) -> int:
    tail = text[start:]
    candidates = [match.start() for pattern in (_HARD_CLAUSE_END_RE, _CONTRASTIVE_END_RE) if (match := pattern.search(tail))]
    explanation = _EXPLANATION_TRANSITION_RE.search(tail)
    if explanation is not None and explanation.start() > 0:
        candidates.append(explanation.start())
    return start + min(candidates) if candidates else len(text)


def _negative_clause_spans(text: str) -> tuple[tuple[int, int], ...]:
    spans: list[tuple[int, int]] = []
    cursor = 0
    while match := _NEGATED_RETRIEVAL_DIRECTIVE_RE.search(text, cursor):
        directive_start = match.start()
        start = directive_start
        end = _negative_clause_end(text, directive_start)
        if end <= directive_start:
            end = match.end()
        # A cancellation withdraws the request it cancels, and that request sits BEFORE it. The
        # window searched here therefore has to include the cancelling clause itself: when the
        # cancellation IS the directive that opened this span, looking only at `text[:start]`
        # cannot see it, and the retrieval it called off stayed in the eligible text.
        #
        #   "Search the web for local coffee shops. Abort lookup. Output ONLY `SEARCH_ABORTED`."
        #       -> eligible text kept "Search the web for local coffee shops", live_info_mode()
        #          returned "fresh_lookup", and the turn searched anyway.
        #
        # `text[:end]` is a superset of `text[:start]`, so every span the trailing-cue form already
        # extended to 0 still does.
        if not spans and _CANCEL_PRIOR_RETRIEVAL_RE.search(text[:end]):
            start = 0
        spans.append((start, end))
        cursor = max(end, match.end())
    return tuple(spans)


def _remove_spans(text: str, spans: tuple[tuple[int, int], ...]) -> str:
    pieces: list[str] = []
    cursor = 0
    for start, end in spans:
        pieces.append(text[cursor:start])
        cursor = end
    pieces.append(text[cursor:])
    # Entity extraction deliberately consumes the original paragraph/list boundaries.  Only
    # normalize horizontal spacing introduced where a clause was removed; collapsing all
    # whitespace here would weld newline-separated cities back into one contaminated entity.
    clean = re.sub(r"[\t\f\v ]+", " ", "".join(pieces))
    clean = re.sub(r" *\r?\n *", "\n", clean)
    return clean.strip(" ,;:-—\t\r\n")


def retrieval_domains_in(text: str) -> frozenset[str]:
    """Broad live-data families named by text; intentionally contains no concrete entities."""

    domains: set[str] = set()
    if _WEATHER_SCOPE_RE.search(str(text or "")):
        domains.add("weather")
    if _MARKET_SCOPE_RE.search(str(text or "")):
        domains.add("market_prices")
    if _NEWS_SCOPE_RE.search(str(text or "")):
        domains.add("news")
    if _WEB_FETCH_SCOPE_RE.search(str(text or "")):
        domains.add("web_fetch")
    return frozenset(domains)


# A REPORTED EXAMPLE UTTERANCE is someone else's words presented as an illustration -- "for example, a
# user might type pull up my latest email", "an example request would be: check the latest weather" --
# and is not candidate positive work of this turn, exactly as a quoted sentence is not (2026-09-14: the
# example's `latest` read as the user's own request for current information, the requirements authority
# demanded web evidence and the planner searched the web for an example sentence). The frame needs a
# third-person speaker with a reporting verb, or an explicit "an example request would be"; the reported
# words run to the end of their sentence, or to the closing quote when they are quoted. A user's own
# question that happens to open with "for example" has no reported speaker and stays eligible.
_REPORTED_SPEAKER = (
    r"(?:(?:a|an|the|some|one|any|another|your|our)\s+)?"
    r"(?:users?|someone|somebody|persons?|people|customers?|clients?|operators?|callers?|visitors?|one|they|he|she)"
)
_REPORTING_VERB = (
    r"(?:(?:might|could|would|may|can|will|often|sometimes|typically|usually|just)\s+)?"
    r"(?:types?|typed|says?|said|asks?|asked|writes?|wrote|enters?|entered|sends?|sent|requests?|requested|"
    r"puts?|phrases?|phrased|words?|worded)\b"
)
_REPORTED_EXAMPLE_RE = re.compile(
    rf"\b(?:for\s+example|for\s+instance|e\.g\.)[,:]?\s+{_REPORTED_SPEAKER}\s+{_REPORTING_VERB}"
    rf"|(?:^|(?<=[.!?;\n]))\s*(?:say|imagine|suppose|picture\s+this)[,:]?\s+{_REPORTED_SPEAKER}\s+{_REPORTING_VERB}"
    r"|\b(?:an?|the)\s+(?:example|sample|typical|hypothetical)\s+"
    r"(?:request|prompt|query|question|message|input|utterance|sentence)\s+"
    r"(?:(?:would|might|could|may)\s+)?(?:be|is|was|reads?|looks?\s+like|goes)\b",
    re.IGNORECASE,
)
_QUOTE_PAIRS = {'"': '"', "'": "'", "“": "”", "‘": "’", "`": "`"}


def _reported_example_spans(text: str) -> tuple[tuple[int, int], ...]:
    """The spans of reported example utterances: each frame plus the words it reports."""
    spans: list[tuple[int, int]] = []
    cursor = 0
    while match := _REPORTED_EXAMPLE_RE.search(text, cursor):
        start, end = match.start(), match.end()
        # Skip the connective that may introduce the words ("like", "such as", ":", ",").
        tail = re.match(r"\s*(?:like|such\s+as)?\s*[,:]?\s*", text[end:])
        content = end + (tail.end() if tail else 0)
        if content < len(text) and text[content] in _QUOTE_PAIRS:
            close = text.find(_QUOTE_PAIRS[text[content]], content + 1)
            end = close + 1 if close != -1 else len(text)
        else:
            sentence_end = re.search(r"[.!?\n]", text[content:])
            end = content + sentence_end.end() if sentence_end else len(text)
        spans.append((start, end))
        cursor = max(end, match.end())
    return tuple(spans)


def analyze_retrieval_constraints(text: str) -> RetrievalConstraints:
    """Parse explicit negative retrieval clauses without treating ordinary negation as a veto."""

    raw = str(text or "")
    spans = _negative_clause_spans(raw)
    clauses = tuple(raw[start:end].strip(" ,;:-—") for start, end in spans)
    prohibited_toolsets: set[str] = set()
    forbids_all_tools = any(
        _ALL_TOOL_SCOPE_RE.search(clause) or _ALL_TOOL_LIST_ITEM_RE.search(clause)
        for clause in clauses
    )
    forbids_external = any(_GLOBAL_EXTERNAL_SCOPE_RE.search(clause) for clause in clauses)

    for clause in clauses:
        domains = retrieval_domains_in(clause)
        prohibited_toolsets.update(domains)
        if _ACTION_WITHOUT_TARGET_RE.search(" ".join(clause.split())):
            # A prohibition that is ONLY the verb ("Do not browse.", "No lookups.") closes
            # external retrieval at large, not just the one family the verb maps to -- the
            # old `not domains` guard let "browse" narrow it to a web_fetch veto and a web
            # search still ran. The recognizer is already end-of-clause narrow, so this
            # cannot widen an ordinary negation into a veto.
            forbids_external = True

    # Format directives and reported example utterances are removed from the ELIGIBLE TEXT only. They
    # are not retrieval prohibitions, so `has_prohibition` and the toolset scopes stay derived from
    # `spans` alone -- saying "no markdown" must not be mistaken for saying "no web".
    eligible_spans = tuple(
        sorted(spans + tuple((m.start(), m.end()) for m in _OUTPUT_FORMAT_DIRECTIVE_RE.finditer(raw))
               + _reported_example_spans(raw))
    )
    return RetrievalConstraints(
        eligible_text=_remove_spans(raw, eligible_spans),
        has_prohibition=bool(spans),
        forbids_all_tools=forbids_all_tools,
        forbids_external_retrieval=forbids_external or forbids_all_tools,
        prohibited_toolsets=frozenset(prohibited_toolsets),
        negative_clauses=clauses,
    )


def analyze_retrieval_request_authority(text: str) -> RetrievalRequestAuthority:
    """Separate positive retrieval authority from structured presentation metadata.

    The parser is deliberately data-only: the value of ``role`` grants no authority, and unknown
    siblings are ignored.  A strict, whole JSON object with a string ``task`` or ``intent`` gets a
    semantic work surface; constraints/rules can only remove retrieval authority.  Ordinary prose,
    malformed JSON, and arbitrary JSON data retain the established prose parser behavior.
    """

    raw = str(text or "")
    stripped = raw.strip()
    payload: object = None
    if stripped.startswith("{"):
        try:
            payload, end = json.JSONDecoder().raw_decode(stripped)
        except (TypeError, ValueError):
            payload, end = None, 0
        if end and stripped[end:].strip():
            payload = None
    if isinstance(payload, dict):
        task_value = payload.get("intent", payload.get("task"))
        task_text = task_value.strip() if isinstance(task_value, str) else ""
        if task_text:
            rule_values: list[str] = []
            for key in ("constraints", "rule", "rules"):
                value = payload.get(key)
                if isinstance(value, str):
                    rule_values.append(value)
                elif isinstance(value, (list, tuple)):
                    rule_values.extend(item for item in value if isinstance(item, str))
            authority_surface = ". ".join((task_text, *rule_values))
            constraints = analyze_retrieval_constraints(authority_surface)
            task_constraints = analyze_retrieval_constraints(task_text)
            return RetrievalRequestAuthority(
                candidate_text=task_constraints.eligible_text,
                constraints=constraints,
                structured=True,
            )

    constraints = analyze_retrieval_constraints(raw)
    return RetrievalRequestAuthority(
        candidate_text=constraints.eligible_text,
        constraints=constraints,
        structured=False,
    )


def retrieval_candidate_text(text: str) -> str:
    """Text eligible to create retrieval intent or tool arguments."""

    return analyze_retrieval_constraints(text).eligible_text
