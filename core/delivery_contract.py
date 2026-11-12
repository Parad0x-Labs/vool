"""Whether a turn's own words restrict what its visible answer may contain.

`core/raw_output_contract.py` owns the final-output contract: once a turn has one, the model is told
to return only the deliverable, with no explanation, analysis or follow-up, and the final boundary
strips labels, scratchpads and wrappers. What it did not have was a way to tell an instruction ABOUT
THE REPLY from the same words used about the world. Its exclusivity triggers matched anywhere in a
turn, so on the v0.8 line "Nothing else worked, so I rebuilt the index.", "Which sections have no
intro?" and "How do I return only unique rows in SQL?" all created a contract and the model was told
not to explain; while "Print the flag value only.", "Give the count alone.", "Name the capital of
Peru. No commentary." and "capital of norway. just the city name pls" created none.

The mechanism is a scope analysis, ported from the Aug 15 final-output contract work. Three operator
families are read off the turn -- exclusive focus ("only", "just", "alone"), a restrictive coda ("and
nothing else", "that's it"), and a negated reply component ("no commentary", "skip the
explanation") -- and each restricts the reply only when it is ATTACHED to it:

  * a clause is an output request when its HEAD is a request verb or a question opener, never
    because the word "answer" appears somewhere inside it ("My answer is that we should merely
    wait" is not a delivery instruction);
  * an exclusive adverb restricts the reply where it leads an elliptical demand ("just the city
    name"), closes a bare focus phrase ("number only"), follows an output or naming verb said as a
    request ("output only the digits"), or closes a request whose object is the reply ("Print the
    flag value only."). Anywhere else it is inside what the turn is about: "why does only the first
    test fail?", "explain only the first error", "just tell me" (a softener);
  * a restrictive coda is TERMINAL -- it closes a request and nothing follows it but courtesy, or a
    colon and the material ("...and nothing else." restricts; "...why nothing else in physics is as
    strange..." is a subordinate clause);
  * a negated reply component restricts the reply when its clause is bare ("No commentary."), or
    when nothing between the request's head and the prohibition opens an embedded clause or a
    possessive: "translate it without any explanation" does; "list the pages that have no
    headings", "find documents with no title" and "which sections have no intro?" do not.

The Aug 15 analysis bound an operator anywhere inside a clause that asked for output, so "Why does
only the first test fail?" was a contract; attachment is the repair, made when it was ported. The
vocabularies are closed classes -- exclusive-focus adverbs, the nouns that name parts of a reply,
the verbs whose object is the reply -- not a list of reported prompts.

Every check reads the turn once. Clause membership, the request clauses before an operator and the
stops between them are indexed up front, each clause's head and first embedding word are read once,
a whitespace run is read once however many clause starts it holds, and the output verb before a
focus adverb is read backwards from the adverb rather than by searching the clause again; so the
cost stays linear in the message however many operators it carries.
"""

from __future__ import annotations

import bisect
import itertools
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import NamedTuple

_WORD_RE = re.compile(r"\b[\w'-]+\b", re.UNICODE)

# The verbs that name the act of producing this reply.
_OUTPUT_VERBS = frozenset(
    {
        "output", "reply", "respond", "answer", "return", "print", "write", "say", "give",
        "show", "emit", "produce", "send", "provide",
    }
)
# Verbs that make a clause a request without being about output as such. A restriction may attach
# to one of these ("Explain X, nothing else"), so they count as request heads -- but only as heads,
# which is what keeps the noun readings out.
_REQUEST_VERBS = _OUTPUT_VERBS | frozenset(
    {
        "add", "analyse", "analyze", "build", "calculate", "check", "choose", "compare", "compute",
        "confirm", "convert", "count", "create", "define", "describe", "draft", "explain", "find",
        "fix", "generate", "identify", "list", "make", "name", "pick", "read", "recommend",
        "rename", "rewrite", "review", "run", "search", "sort", "suggest", "summarise",
        "summarize", "tell", "translate", "use",
        # Imperatives whose object is a short produced text -- the asks that most often carry an
        # exact-output instruction ("Spell X backwards, nothing else").
        "abbreviate", "capitalise", "capitalize", "classify", "decode", "encode", "estimate",
        "evaluate", "extract", "format", "guess", "paraphrase", "quote", "rank", "rephrase",
        "repeat", "reproduce", "reverse", "round", "shorten", "simplify", "solve", "spell", "state",
        "transliterate",
    }
)
_QUESTION_OPENERS = frozenset(
    {
        "what", "which", "how", "when", "where", "who", "whose", "whom", "why",
        "is", "are", "was", "were", "do", "does", "did", "can", "could", "will",
        "would", "should", "has", "have", "had", "may", "might", "must",
    }
)
# Request verbs whose object goes into the reply as it stands: a focus adverb right after one of
# these, or after an output verb, focuses the reply itself ("list only the colours", "tell me just
# the year").
_NAMING_VERBS = frozenset(
    {"extract", "identify", "list", "name", "quote", "repeat", "reproduce", "spell", "state", "tell"}
)
# Request verbs whose reply is the result of working on their object. "Translate the greeting only"
# restricts the reply; "translate only the first paragraph" restricts the work. So these count when
# the adverb closes the request, never when it follows the verb.
_RESULT_VERBS = frozenset(
    {
        "abbreviate", "calculate", "capitalise", "capitalize", "compute", "convert", "count", "decode",
        "encode", "estimate", "format", "paraphrase", "rephrase", "reverse", "round", "shorten",
        "simplify", "solve", "translate", "transliterate",
    }
)
_FOCUS_VERBS = _OUTPUT_VERBS | _NAMING_VERBS
_CLOSING_FOCUS_VERBS = _FOCUS_VERBS | _RESULT_VERBS
_FOCUS_VERB_PATTERN = "|".join(sorted(_FOCUS_VERBS))

# English exclusive-focus adverbs. A closed class: these are the words the language has for "and
# nothing besides", and it does not grow with new reports. `alone` is a member in its post-nominal
# use ("the count alone"); its other reading ("she works alone") is excluded by the scoping test,
# not by leaving it out of the class.
_EXCLUSIVE_ADVERBS = ("only", "just", "solely", "merely", "purely", "strictly", "exclusively", "alone")
_EXCLUSIVE_ADVERB_RE = re.compile(r"\b(?:" + "|".join(_EXCLUSIVE_ADVERBS) + r")\b", re.IGNORECASE)

# A restrictive coda closes a request by ruling out everything not already named. `op` is the
# operator itself: the anchor used for scoping, so a leading "and" or comma cannot push the anchor
# into the gap between two clauses where no clause contains it.
_RESTRICTIVE_CODA_RE = re.compile(
    r"(?:^|[\s,;:—–-])(?:and\s+)?(?P<op>"
    r"nothing\s+(?:else|more|further|besides)"
    r"|that(?:'|’)?s\s+(?:it|all)"
    r"|no\s+(?:more|extras?)"
    r")\b",
    re.IGNORECASE,
)
# What may follow a coda and still leave it terminal: punctuation and politeness, never new content.
# Whitespace of every kind counts as punctuation here.
_CODA_LEAD_PUNCTUATION = frozenset(".,;:!?)]\"'’”—–-")
_CODA_TRAIL_PUNCTUATION = frozenset(".,;:!?")
_CODA_LEAD_BEFORE_COLON = _CODA_LEAD_PUNCTUATION - {":"}
# `okay` before `ok`: the first alternative that matches is the one the tail is read after.
_CODA_COURTESY_RE = re.compile(r"please|pls|thanks|thank\s+you|thx|okay|ok", re.IGNORECASE)
_COURTESY_PHRASE_RE = re.compile(r"\b(?:please|pls|thanks|thank\s+you|thx|okay|ok)\b", re.IGNORECASE)

_OUTPUT_VERB_PATTERN = "|".join(sorted(_OUTPUT_VERBS))
# `output/reply/... nothing except X` -- exclusivity expressed as a negation plus an exception.
_NOTHING_EXCEPT_RE = re.compile(
    rf"\b(?P<op>(?:{_OUTPUT_VERB_PATTERN})(?:\s+(?:me|back|with|as))?\s+"
    r"nothing\s+(?:except|but|other\s+than))\b",
    re.IGNORECASE,
)

# The parts a reply can be made of. Negating one of these is a statement about a reply -- but only a
# reply that this turn is actually asking for, which the scoping test decides.
_REPLY_COMPONENT = (
    r"explanations?|preambles?|prefaces?|intros?|introductions?|commentary|comments?"
    r"|prose|notes?|caveats?|disclaimers?|justifications?|rationale|elaborations?"
    r"|context|background"
    r"|greetings?|pleasantries|apolog(?:y|ies)|follow-?ups?|sign-?offs?"
    r"|extra\s+text|extra\s+words|additional\s+text|filler|padding|boilerplate"
    r"|labels?|headings?|headers?|quotes|quotation\s+marks|units|punctuation|formatting"
    r"|decorations?|emojis?"
)
_REPLY_COMPONENT_NAME_RE = re.compile(_REPLY_COMPONENT, re.IGNORECASE)
# "don't" is how the negative imperative is usually typed, with or without its apostrophe.
_NEGATIVE_IMPERATIVE = r"(?:do\s+not|don['’]?t)\s+(?:add|include|use|write|output|return|give|prepend|append)"
_NEGATIVE_IMPERATIVE_RE = re.compile(_NEGATIVE_IMPERATIVE + r"\b", re.IGNORECASE)
_NO_COMPONENT_RE = re.compile(
    rf"\b(?P<op>(?:no|without|omit|skip|drop|exclude)\s+(?:any\s+|the\s+|all\s+)?"
    rf"(?P<component>{_REPLY_COMPONENT})"
    rf"|{_NEGATIVE_IMPERATIVE}\s+"
    rf"(?:any\s+|a\s+|the\s+)?(?P<component_alt>{_REPLY_COMPONENT}))\b",
    re.IGNORECASE,
)
# The words a bare prohibition may carry besides the prohibition itself: courtesy, articles, and the
# predicate of necessity or permission that only restates it ("No explanation needed.", "No headings
# allowed.").
_BARE_FILLER = frozenset(
    {
        "please", "pls", "just", "and", "but", "also", "with", "the", "a", "an", "thanks", "thx",
        "is", "are", "needed", "necessary", "required", "allowed", "wanted",
    }
)
# The word that makes a span a prohibition, wherever a coarser reader matched the span.
_PROHIBITION_WORD_RE = re.compile(r"\b(?:no|without|do\s+not|don['’]?t|never)\b", re.IGNORECASE)

# Clauses are split by punctuation and coordinators, no grammar: the pattern
# `[;\n?!]|\.(?=\s|$)|,\s+|\s+(?:and\s+then|and\s+also|and|then|also|plus|--|—)\s+`, matched left to
# right. As one regex every start inside a whitespace run read the rest of the run again; where a
# coordinator can follow a run depends only on where the run ends, so `_clause_splits` reads each run
# once.
_CLAUSE_STOP_CHARS = frozenset(";\n?!")
_COORDINATOR_RE = re.compile(r"(?:and\s+then|and\s+also|and|then|also|plus|--|—)\s+", re.IGNORECASE)
_SENTENCE_END_RE = re.compile(r"[.!?\n]")
_SENTENCE_END_CHARS = frozenset(".!?\n")
_LEAD_FILLER = (
    r"(?:please\s+|pls\s+|now\s+|next\s+|first\s+|finally\s+|lastly\s+|just\s+|simply\s+"
    r"|(?:can|could|would|will)\s+you\s+(?:please\s+)?|i\s+(?:want|need)\s+you\s+to\s+"
    r"|\d{1,2}[.)]\s*|[-*•]\s*)+"
)
_CLAUSE_LEAD_FILLER_RE = re.compile("^" + _LEAD_FILLER, re.IGNORECASE)
_LEAD_FILLER_AT_RE = re.compile(_LEAD_FILLER, re.IGNORECASE)
# A bare elliptical demand: no verb, the exclusive adverb doing all the work ("just the digits",
# "only the sorted list"). The determiner is what makes it a noun phrase being demanded rather than
# an adverb modifying a verb elsewhere.
_ELLIPTICAL_DEMAND_RE = re.compile(
    r"^\s*(?P<courtesy>(?:please|pls|plz)[\s,]+)?(?P<adverb>" + "|".join(_EXCLUSIVE_ADVERBS) + r")\s+"
    r"(?:the|a|an|your|its|his|her|their|these|those|first|last|final|\d)\b",
    re.IGNORECASE,
)
# ... unless the words after the determiner hold a finite verb of the closed classes, or a locative
# that only a clause takes: then "only the N" is the subject of a statement ("only the first draft
# was saved", "only the number of votes matters here").
_ELLIPSIS_BREAKER_RE = re.compile(
    r"\b(?:is|are|am|was|were|be|been|being|do|does|did|has|have|had|can|could|will|would|shall"
    r"|should|may|might|must)(?:n['’]?t)?\b|\b(?:there|here|won['’]?t|can['’]?t)\b",
    re.IGNORECASE,
)
# A request closed with a courtesy word ("just the number please").
_COURTESY_CLOSE_RE = re.compile(r"\b(?:please|pls|plz|thanks|thank\s+you|thx|ty)[\s.!,;:]*$", re.IGNORECASE)
# The post-nominal mirror: the focused noun phrase first, the adverb closing the clause -- "number
# only", "digits only". It is how a terse follow-up carries its instruction ("now the same for
# Saturn, number only").
#
# `only` and no other adverb. This rule licenses a clause with no request verb at all, so it is the
# one place where an ambiguous adverb costs a false contract rather than a missed one -- `alone`
# clause-final is far more often "by oneself" ("the technician arrived alone") than restrictive.
_POSTNOMINAL_DEMAND_RE = re.compile(
    r"^\s*(?P<phrase>[\w'-]+(?:\s+[\w'-]+){0,3}?)\s+(?P<adverb>only)"
    r"(?:[\s,]+(?:please|pls|plz|thanks|thank\s+you|thx|ty))?\s*[.!?,;:]*\s*$",
    re.IGNORECASE,
)
# An instruction ends at a colon that introduces the material it is about ("Final answer only: what
# is 6*7?", "Respond with yes or no only: is the earth flat?").
_MATERIAL_COLON_RE = re.compile(r":(?=\s|$)")
# Words whose presence means the phrase is a CLAUSE with a subject and a verb, not a bare noun
# phrase. Closed grammatical classes -- personal pronouns, copulas, auxiliaries -- and the reason
# "she works alone" and "it runs strictly" are not delivery instructions.
_CLAUSAL_MARKERS = frozenset(
    {
        "i", "you", "he", "she", "it", "we", "they", "me", "him", "her", "us", "them",
        "is", "are", "am", "was", "were", "be", "been", "being",
        "do", "does", "did", "has", "have", "had",
        "can", "could", "will", "would", "shall", "should", "may", "might", "must",
        "not", "there", "here", "this", "that",
    }
)
# The restrictive-object construction: an output or naming verb, then the focus adverb, then the
# noun phrase being demanded -- "output only the number", "return only 512". This carries a delivery
# instruction in a run-on turn where the clause head belongs to the question instead ("wats 9*9
# output only the number thx"). The pattern is `\b<verb>(?:\s+<particle>)*\s*$` before the adverb,
# written backwards so it is matched from the adverb towards the start of its clause.
_VERB_PARTICLES = ("me", "us", "it", "back", "out", "with", "as", "w/")


def _backwards(words: frozenset[str] | tuple[str, ...]) -> str:
    return "|".join(re.escape(word[::-1]) for word in sorted(words))


_VERB_BEFORE_FOCUS_BACKWARDS_RE = re.compile(
    rf"\s*(?:(?:{_backwards(_VERB_PARTICLES)})\s+)*(?:{_backwards(_FOCUS_VERBS)})\b",
    re.IGNORECASE,
)
# The particles a request verb carries before its object ("give me", "reply with", "answer w/").
_PARTICLES_AFTER_HEAD_RE = re.compile(
    r"(?:\s+(?:" + "|".join(re.escape(word) for word in _VERB_PARTICLES) + r")(?![\w/]))*",
    re.IGNORECASE,
)
# Words that open an embedded clause or a phrase about something else: a wh-word, a complementizer or
# subordinator, a subject pronoun, a finite auxiliary or copula, possessive `has`/`with`. Between a
# request's head and an operator, one of these puts the operator inside what the request is ABOUT
# ("explain why only the strong survive", "list the pages that have no headings", "find documents
# with no title") instead of on the reply.
_EMBEDDING_RE = re.compile(
    r"\b(?:what|which|who|whom|whose|why|how|where|when|whether|if|that|because|since|although"
    r"|though|while|unless|until|whereas|i|you|he|she|we|they|there|is|are|am|was|were|be|been"
    r"|being|has|have|had|having|do|does|did|can|could|will|would|shall|should|may|might|must"
    r"|with)\b",
    re.IGNORECASE,
)
# The auxiliaries that invert a question ("why DO teachers give ...", "does it print ..."): a verb
# after one belongs to the question, not to a request.
_INVERTING_AUXILIARY_RE = re.compile(
    r"\b(?:do|does|did|can|could|will|would|shall|should|may|might|must)\b"
    r"|\b(?:don|doesn|didn|can|couldn|won|wouldn|shouldn|mustn)['’]?t\b",
    re.IGNORECASE,
)
# The words that open a subordinate clause: inside one, "only the result" is part of what the
# sentence is about ("explain why only the result is cached").
_SUBORDINATOR_RE = re.compile(
    r"\b(?:what|which|who|whom|whose|why|how|where|when|whether|if|that|because|since|although"
    r"|though|while|unless|until|whereas)\b",
    re.IGNORECASE,
)
# What makes a question's closing "without X" part of the question rather than an instruction on its
# answer: a subject pronoun ("how do I write a README without a preamble?"), a possessive ("which
# sections have no intro?"), or an inverting auxiliary ("why do people send emails without notes?").
_QUESTION_CONTENT_RE = re.compile(
    r"\b(?:i|you|we|they|he|she|it|has|have|had|having|with|do|does|did|can|could|will|would|shall"
    r"|should|may|might|must)\b|\b(?:don|doesn|didn|can|couldn|won|wouldn|shouldn|mustn)['’]?t\b",
    re.IGNORECASE,
)
# A word that cannot stand right before an imperative verb: the infinitive marker, a determiner, a
# subject pronoun, a wh-word or complementizer, an auxiliary or copula. "Explain how TO output only
# the diff", "THE list only the admin sees", "why IT prints only the header".
_NOT_BEFORE_IMPERATIVE = frozenset(
    {
        "to", "the", "a", "an", "this", "that", "these", "those", "my", "your", "his", "her", "its",
        "our", "their", "i", "you", "he", "she", "we", "they", "it", "who", "which", "what", "why",
        "how", "where", "when", "whether", "if", "because", "is", "are", "am", "was", "were", "be",
        "been", "has", "have", "had", "do", "does", "did", "can", "could", "will", "would",
        "shall", "should", "may", "might", "must",
    }
)
# The noun phrase a focus adverb is licensed by. Each word ends at a word boundary: "only about",
# "only after" and "only then" are adverbial, not a determiner followed by a noun.
_FOCUSED_NOMINAL_RE = re.compile(
    r"\s*(?:(?:the|a|an|your|its|his|her|their|these|those|this|that|first|last|final)\b|"
    r"\d|[\"'“‘]|[A-Z]{2,})",
)
# Prepositions that introduce the focused complement of an output verb ("answer only with yes").
_FOCUS_PREPOSITIONS = frozenset({"with", "in", "as", "of", "to", "for", "using", "by"})
# `only yes or no`, `only true/false`: the answer restricted to a stated alternation.
_BARE_ALTERNATION_RE = re.compile(r"\s*[\w'-]+\s*(?:/|\bor\b|\bvs\.?\b)\s*[\w'-]+", re.IGNORECASE)
# What a focus tail is when it carries nothing: spacing and punctuation.
_TAIL_CONTENT_RE = re.compile(r"[^ \t,.;:!?\-—–]")
# `only` as a determiner of a subject ("the only way", "my only question"), or attached to a pronoun
# subject ("only you can decide"), is never a delivery instruction.
_ADNOMINAL_EXCLUSIVE_RE = re.compile(
    r"\b(?:the|my|his|her|its|our|their|your|one|a|an)\s+(?:"
    + "|".join(_EXCLUSIVE_ADVERBS)
    + r")\b"
    r"|\b(?:"
    + "|".join(_EXCLUSIVE_ADVERBS)
    + r")\s+(?:i|you|we|they|he|she|it|this|that|these|those|"
    r"someone|somebody|anyone|anybody|everyone|everybody|nobody)\b",
    re.IGNORECASE,
)
_HORIZONTAL_SPACE_RE = re.compile(r"[ \t\r\f\v]+")


@dataclass(frozen=True)
class DeliveryContract:
    """What the visible answer must be, beyond how long it is and how it is formatted."""

    exclusive: bool = False
    forbidden_components: tuple[str, ...] = ()

    def declared(self) -> bool:
        return bool(self.exclusive or self.forbidden_components)


def delivery_surface(text: str) -> str:
    """The turn as the scope analysis reads it: horizontal spacing folded, line breaks kept, since a
    line break ends a clause and a sentence."""
    return _HORIZONTAL_SPACE_RE.sub(" ", str(text or "")).strip()


def is_reply_component(name: str) -> bool:
    """Whether `name` names a part of a reply, spelled the way `delivery_contract_in` reports it. A
    component read back from stored metadata is checked against this before it reaches a prompt."""
    return bool(_REPLY_COMPONENT_NAME_RE.fullmatch(str(name or "")))


def _clause_splits(text: str) -> Iterator[tuple[int, int]]:
    """The separators between clauses, left to right, as `(start, end)`."""
    length = len(text)
    position = 0
    run_end = -1  # where the whitespace run holding `position` ends, once read
    run_split: int | None = None  # where a coordinator after that run ends, if one follows it
    while position < length:
        char = text[position]
        if char in _CLAUSE_STOP_CHARS or (
            char == "." and (position + 1 == length or text[position + 1].isspace())
        ):
            yield position, position + 1
            position += 1
        elif char == "," and position + 1 < length and text[position + 1].isspace():
            end = position + 1
            while end < length and text[end].isspace():
                end += 1
            yield position, end
            position = end
        elif char.isspace():
            if position >= run_end:
                run_end = position + 1
                while run_end < length and text[run_end].isspace():
                    run_end += 1
                coordinator = _COORDINATOR_RE.match(text, run_end)
                run_split = coordinator.end() if coordinator else None
            if run_split is not None:
                yield position, run_split
                position = run_split
            else:
                position += 1
        else:
            position += 1


# A head contracted with the verb after it ("what's", "who're") is read as the word it contracts.
_CONTRACTED_HEAD_RE = re.compile(r"(?P<word>\w+)['’](?:s|re|ll|d|ve|m)", re.IGNORECASE)


def _head_word(word: str) -> str:
    contracted = _CONTRACTED_HEAD_RE.fullmatch(word)
    return (contracted.group("word") if contracted else word).lower()


def _clause_head(clause: str) -> str:
    body = _CLAUSE_LEAD_FILLER_RE.sub("", clause.strip()).strip()
    head = _WORD_RE.match(body)
    return _head_word(head.group(0)) if head else ""


def _instruction_part(clause: str) -> str:
    colon = _MATERIAL_COLON_RE.search(clause)
    return clause if colon is None else clause[: colon.start()]


def _bare_focus_match(clause: str) -> re.Match[str] | None:
    match = _POSTNOMINAL_DEMAND_RE.match(_instruction_part(clause))
    if match is None:
        return None
    words = [word.lower() for word in _WORD_RE.findall(match.group("phrase"))]
    return match if words and not any(word in _CLAUSAL_MARKERS for word in words) else None


def _is_bare_focus_phrase(clause: str) -> bool:
    """Whether a clause is nothing but a focused noun phrase ("number only").

    A clause with no subject and no verb in it cannot be about anything except what to output, so it
    needs no request verb to scope it. Subject and verb are detected by the closed function-word
    classes in `_CLAUSAL_MARKERS`, which is what keeps "she works alone" and "it runs strictly" out.
    An open-class finite verb ("the pump cycles only") still reads as a bare phrase, and so does a
    noun that is spelled like a request verb ("planet name only", "word count only"): the cost of
    that direction is wrapper stripping on an ordinary reply, never a lost deliverable.
    """
    return _bare_focus_match(clause) is not None


def _asks_by_its_head(clause: str) -> bool:
    """Whether this clause is asking for a reply, rather than describing the world. Decided by the
    clause HEAD: a substring search would convict "My answer is ..." on the noun `answer`."""
    if _is_bare_focus_phrase(clause):
        return True
    head = _clause_head(clause)
    return bool(head) and (head in _REQUEST_VERBS or head in _QUESTION_OPENERS)


def _elliptical_match(clause: str) -> re.Match[str] | None:
    part = _instruction_part(clause)
    match = _ELLIPTICAL_DEMAND_RE.match(part)
    return match if match is not None and not _ELLIPSIS_BREAKER_RE.search(part) else None


def _only_spacing_or(text: str, start: int, end: int, punctuation: frozenset[str]) -> int:
    """The first position in [start, end) that is neither whitespace nor in `punctuation`."""
    while start < end and (text[start].isspace() or text[start] in punctuation):
        start += 1
    return start


_NO_PUNCTUATION: frozenset[str] = frozenset()
_ELLIPTICAL = "elliptical"
_BARE = "bare"


class _Head(NamedTuple):
    """A clause's first word after its lead filler, and where its object starts: after the particles
    the word carries ("give me", "reply with")."""

    start: int
    end: int
    word: str
    object_start: int


def _clause_form(clause: str) -> tuple[str | None, int]:
    """Whether the clause is a demand by its form alone -- an elliptical "just the N", or a bare "N
    only" -- and where in the clause the adverb doing the demanding is."""
    elliptical = _elliptical_match(clause)
    if elliptical is not None:
        return _ELLIPTICAL, elliptical.start("adverb")
    bare = _bare_focus_match(clause)
    if bare is not None:
        return _BARE, bare.start("adverb")
    return None, -1


def _is_word_char(char: str) -> bool:
    return char.isalnum() or char == "_"


class DeliveryScope:
    """One turn, indexed once, answering where an operator restricts the reply."""

    def __init__(self, text: str) -> None:
        self.text = text
        clauses: list[tuple[int, int]] = []
        cursor = 0
        for start, end in _clause_splits(text):
            clauses.append((cursor, start))
            cursor = end
        clauses.append((cursor, len(text)))
        self.clauses = [(start, end) for start, end in clauses if text[start:end].strip()]
        self._starts = [start for start, _end in self.clauses]
        self._ends = [end for _start, end in self.clauses]
        self._stops = [match.start() for match in _SENTENCE_END_RE.finditer(text)]
        self._adnominal: dict[int, bool] = {}
        self._first_prohibition: dict[int, int | None] = {}
        self._heads: dict[int, _Head | None] = {}
        self._embedding: dict[int, int] = {}
        self._inversion: dict[int, int] = {}
        self._restated: dict[int, int] = {}
        self._question_content: dict[int, int] = {}
        self._subordinator: dict[int, int] = {}
        self._backwards_text: str | None = None
        forms = [_clause_form(text[start:end]) for start, end in self.clauses]
        self._forms = [form for form, _offset in forms]
        # Where the adverb of a demand-by-form clause stands, as a position in the turn.
        self._form_anchors = [start + offset for (_form, offset), (start, _end) in zip(forms, self.clauses, strict=True)]
        self._asks = [
            form is not None or _asks_by_its_head(text[start:end])
            for form, (start, end) in zip(self._forms, self.clauses, strict=True)
        ]
        # A turn that OPENS with "only the N ..." or "just the N ..." is usually saying something
        # about that subject ("only the number of votes matters here", "just the two of us went").
        # The demand reading needs something to restrict: an ask before it, a restriction after it
        # in the same sentence ("just the ISO code for brazil, nothing more"), or the courtesy that
        # closes a request ("just the number please").
        if self._asks and self._asks[0]:
            start, end = self.clauses[0]
            elliptical = _elliptical_match(text[start:end])
            if not (
                _asks_by_its_head(text[start:end])
                or self._restriction_follows(0)
                or (elliptical is not None and elliptical.group("courtesy"))
                or _COURTESY_CLOSE_RE.search(text, start, self._sentence_end_after(end))
            ):
                self._asks[0] = False
        # How many of the first k clauses ask for output, for every k.
        self._asking = [0, *itertools.accumulate(int(flag) for flag in self._asks)]

    def clause_at(self, position: int) -> int | None:
        index = bisect.bisect_right(self._starts, position) - 1
        if index >= 0 and position < self._ends[index]:
            return index
        return None

    def _sentence_end_after(self, position: int) -> int:
        index = bisect.bisect_left(self._stops, position)
        return self._stops[index] if index < len(self._stops) else len(self.text)

    def _restriction_follows(self, host: int) -> bool:
        """Whether a later clause of this clause's sentence restricts what is output: a terminal coda
        or a negated reply component."""
        end = self.clauses[host][1]
        limit = self._sentence_end_after(end)
        if _NO_COMPONENT_RE.search(self.text, end, limit):
            return True
        return any(
            self.coda_is_terminal(match.end("op"), self.clause_at(match.start("op")))
            for match in _RESTRICTIVE_CODA_RE.finditer(self.text, end, limit)
        )

    def _first_in_sentence(self, host: int) -> bool:
        last_stop = bisect.bisect_left(self._stops, self._starts[host]) - 1
        boundary = self._stops[last_stop] if last_stop >= 0 else -1
        return bisect.bisect_right(self._ends, boundary) == host

    def _sentence_says_more(self, host: int) -> bool:
        """Whether the rest of this clause's sentence says something: courtesy and further negated
        reply components are not something."""
        end = self.clauses[host][1]
        rest = self.text[end : self._sentence_end_after(end)]
        rest = _COURTESY_PHRASE_RE.sub(" ", _NO_COMPONENT_RE.sub(" ", rest))
        return any(word not in _BARE_FILLER for word in _WORD_RE.findall(rest.lower()))

    def scopes_output(self, position: int) -> bool:
        """Whether an operator at `position` restricts the answer or merely a fact in the sentence.

        It governs the reply when its own clause asks for output, or -- for a coda such as ",
        nothing else" -- when a clause earlier in the SAME sentence did. A stop between them ends the
        search: a request two sentences back cannot license a restriction that was never about it.
        """
        host = self.clause_at(position)
        if host is None:
            return False
        if self._asks[host]:
            return True
        last_stop = bisect.bisect_left(self._stops, self._starts[host]) - 1
        boundary = self._stops[last_stop] if last_stop >= 0 else -1
        # The earlier clauses with no stop between their end and this clause: those ending after it.
        first = bisect.bisect_right(self._ends, boundary)
        return first < host and self._asking[host] - self._asking[first] > 0

    def _head(self, host: int) -> _Head | None:
        if host in self._heads:
            return self._heads[host]
        text = self.text
        start, end = self.clauses[host]
        position = _only_spacing_or(text, start, end, _NO_PUNCTUATION)
        filler = _LEAD_FILLER_AT_RE.match(text, position, end)
        if filler is not None:
            position = _only_spacing_or(text, filler.end(), end, _NO_PUNCTUATION)
        word = _WORD_RE.match(text, position, end)
        head = None
        if word is not None:
            particles = _PARTICLES_AFTER_HEAD_RE.match(text, word.end(), end)
            head = _Head(word.start(), word.end(), _head_word(word.group(0)), particles.end() if particles else word.end())
        self._heads[host] = head
        return head

    def _first_embedding(self, host: int) -> int:
        """Where the first embedding word of a clause is -- after its head and the head's particles
        when the head is a request verb, else from the clause start -- or the clause end."""
        known = self._embedding.get(host)
        if known is None:
            start, end = self.clauses[host]
            head = self._head(host)
            if head is not None and head.word in _REQUEST_VERBS:
                start = head.object_start
            found = _EMBEDDING_RE.search(self.text, start, end)
            known = self._embedding[host] = found.start() if found else end
        return known

    def _first_inversion(self, host: int) -> int:
        known = self._inversion.get(host)
        if known is None:
            start, end = self.clauses[host]
            found = _INVERTING_AUXILIARY_RE.search(self.text, start, end)
            known = self._inversion[host] = found.start() if found else end
        return known

    def _imperative_at(self, host: int, verb_start: int) -> bool:
        """Whether the verb at `verb_start` is said as a request: it heads its clause, or nothing
        before it in the clause inverts a question and the word right before it can precede an
        imperative ("wats 9*9 output only the number", not "why do teachers give only ...")."""
        head = self._head(host)
        if head is not None and head.start == verb_start:
            return True
        if self._first_inversion(host) < verb_start:
            return False
        text = self.text
        start = self.clauses[host][0]
        before = verb_start
        while before > start and text[before - 1].isspace():
            before -= 1
        word_start = before
        while word_start > start and (_is_word_char(text[word_start - 1]) or text[word_start - 1] in "'’-"):
            word_start -= 1
        return text[word_start:before].lower() not in _NOT_BEFORE_IMPERATIVE

    def _restates_object(self, host: int, adverb_start: int) -> bool:
        """Whether the phrase an adverb opens, running to the end of the clause, ends with a word the
        request already named after its head: "extract the domain from <url> only the domain"."""
        known = self._restated.get(host)
        if known is None:
            end = self.clauses[host][1]
            head = self._head(host)
            known = end + 1
            words = list(_WORD_RE.finditer(self.text, head.object_start, end)) if head is not None else []
            if len(words) > 1:
                last = words[-1].group(0).lower()
                known = next((word.end() for word in words[:-1] if word.group(0).lower() == last), known)
            self._restated[host] = known
        return known <= adverb_start

    def focus_restricts_reply(self, host: int, adverb_start: int, adverb_end: int) -> bool:
        """Whether an exclusive adverb, already licensed by its focus, restricts the reply.

        Four positions attach it to the reply: leading an elliptical demand ("just the city name"),
        closing a bare focus phrase ("number only"), right after an output or naming verb said as a
        request ("output only the digits", "answer w/ only yes or no"), and closing a request whose
        object is the reply ("Print the flag value only.", "extract the domain from <url> only the
        domain"). Anywhere else the adverb sits inside what the request or question is about: "why
        does only the first test fail?", "explain only the first error", "list the files where only
        the owner has write access".
        """
        start, end = self.clauses[host]
        if self._forms[host] is not None and self._form_anchors[host] == adverb_start:
            return self._asks[host]
        verb = self.focus_verb_at(start, adverb_start)
        if verb is not None and self._imperative_at(host, verb):
            return True
        head = self._head(host)
        if head is None or head.word not in _CLOSING_FOCUS_VERBS or self._first_embedding(host) < adverb_start:
            return False
        if self.coda_is_terminal(adverb_end, host):
            return True
        return self._first_embedding(host) >= end and self._restates_object(host, adverb_start)

    def attaches_to_reply(self, position: int) -> bool:
        """Whether a prohibition at `position`, in a clause that says more than the prohibition, is
        said about the reply.

        It is when it sits in a request whose head is a request verb with nothing embedding it on the
        way ("translate it without any explanation", "reply with no preamble"), in an elliptical
        demand ("just the number without commentary"), or in a clause after a request in the same
        sentence with nothing embedding it ("..., ideally no notes"). It is not when it sits inside
        what a request or a question is about: "which sections have no intro?", "list the pages that
        have no headings", "find documents with no title".

        A negative imperative that opens its clause is an instruction whatever the rest of its
        sentence says ("Do not add a heading, footer, attribution or any other text."): its "do" is
        the imperative's, not a question's.
        """
        host = self.clause_at(position)
        if host is None:
            return False
        head = self._head(host)
        if head is not None and head.start == position and _NEGATIVE_IMPERATIVE_RE.match(self.text, position):
            return True
        if head is not None and head.word in _REQUEST_VERBS:
            return head.end <= position and self._first_embedding(host) >= position
        if self._asks[host]:
            return self._forms[host] is not None and self._first_embedding(host) >= position
        return self.scopes_output(position) and self._first_embedding(host) >= position

    def verb_restricts_reply(self, verb_start: int, adverb_start: int | None = None) -> bool:
        """Whether an output verb carrying its own restriction ("output nothing but X", "return
        only", "just say") is said as a request rather than reported: "why does it output nothing
        but zeros?", "how do I return only unique rows?", "I just say what I think". An adverb in
        front of the verb must be able to open a request as well."""
        host = self.clause_at(verb_start)
        if host is None or not self._imperative_at(host, verb_start):
            return False
        head = self._head(host)
        if adverb_start is None or (head is not None and head.start == verb_start):
            return True
        return self.clause_at(adverb_start) == host and self._imperative_at(host, adverb_start)

    def reply_noun_focus_restricts_reply(self, adverb_start: int, adverb_end: int, *, after_noun: bool) -> bool:
        """Whether "only" next to a noun that names the reply itself ("answer only", "only the final
        answer", "result only") restricts this reply. The noun settles what is focused; what is left
        is whether the adverb focuses it -- "the answer only partially" does not -- and whether the
        phrase is the subject of a question or a subordinate clause ("why does only the result
        change?", "explain why only the result is cached")."""
        host = self.clause_at(adverb_start)
        if host is None:
            return False
        end = self.clauses[host][1]
        if after_noun and not self.focus_is_licensed(adverb_end, end):
            return False
        head = self._head(host)
        if self._forms[host] is None and head is not None and head.word in _QUESTION_OPENERS:
            return False
        known = self._subordinator.get(host)
        if known is None:
            found = _SUBORDINATOR_RE.search(self.text, self.clauses[host][0], end)
            known = self._subordinator[host] = found.start() if found else end
        return known > adverb_start

    def adnominal(self, host: int) -> bool:
        known = self._adnominal.get(host)
        if known is None:
            start, end = self.clauses[host]
            known = self._adnominal[host] = bool(_ADNOMINAL_EXCLUSIVE_RE.search(self.text, start, end))
        return known

    def coda_is_terminal(self, op_end: int, host: int | None) -> bool:
        """A restrictive coda closes a request; a coda with content after it is a subordinate clause.
        Only punctuation and one courtesy word may follow it in its clause -- or a colon, after which
        comes the material the request is about ("Repeat after me, nothing else: hello world")."""
        return self._closes_clause(op_end, self.clauses[host][1] if host is not None else len(self.text))

    def _closes_clause(self, position: int, end: int) -> bool:
        position = _only_spacing_or(self.text, position, end, _CODA_LEAD_BEFORE_COLON)
        if position >= end or self.text[position] == ":":
            return True
        courtesy = _CODA_COURTESY_RE.match(self.text, position, end)
        return courtesy is not None and _only_spacing_or(
            self.text, courtesy.end(), end, _CODA_TRAIL_PUNCTUATION
        ) == end

    def focus_is_licensed(self, adverb_end: int, clause_end: int) -> bool:
        """Whether the adverb focuses a THING being asked for, rather than modifying a verb.

        Four licensed shapes, each a way English marks a focused complement: a nominal opener
        ("only the digits", "only 512"), a preposition introducing the object ("only with yes"),
        clause-final position ("the flag value only.", "number only please"), and a bare alternation
        ("only yes or no").
        """
        text = self.text
        if _TAIL_CONTENT_RE.search(text, adverb_end, clause_end) is None or self._closes_clause(adverb_end, clause_end):
            return True
        start = adverb_end
        while start < clause_end and text[start].isspace():
            start += 1
        if _FOCUSED_NOMINAL_RE.match(text, start, clause_end):
            return True
        # Read as a word of its own: one that starts right at the adverb ("only-x") is not one.
        word = None
        if start < clause_end and (text[start].isalnum() or text[start] == "_"):
            word = _WORD_RE.match(text, start, clause_end)
        if word is None:
            return True
        if word.group(0).lower() in _FOCUS_PREPOSITIONS:
            return True
        return bool(_BARE_ALTERNATION_RE.match(text, start, clause_end))

    def focus_verb_at(self, clause_start: int, adverb_start: int) -> int | None:
        """Where the output or naming verb starts that, with nothing but its particles after it,
        stands right before the adverb in its clause ("output only", "give me only")."""
        if self._backwards_text is None:
            self._backwards_text = self.text[::-1]
        length = len(self.text)
        match = _VERB_BEFORE_FOCUS_BACKWARDS_RE.match(
            self._backwards_text, length - adverb_start, length - clause_start
        )
        return None if match is None else length - match.end()

    def verb_before_focus(self, clause_start: int, adverb_start: int) -> bool:
        return self.focus_verb_at(clause_start, adverb_start) is not None

    def is_bare_prohibition(self, host: int, op_start: int, op_end: int) -> bool:
        """Whether a clause is nothing but its prohibition ("No commentary.")."""
        start, end = self.clauses[host]
        if op_start < start or op_end > end:
            return False
        remainder = self.text[start:op_start] + " " + self.text[op_end:end]
        return not any(word not in _BARE_FILLER for word in _WORD_RE.findall(remainder.lower()))

    def is_fronted_adverbial(self, host: int) -> bool:
        """Whether a bare prohibition opens a sentence that then says something else.

        "Without context, that number is meaningless" negates a reply component and is not addressed
        to the reply: it is a fronted adverbial. "No commentary." and "..., no headings" are the same
        words where they DO address the reply -- the end of their own sentence. So are "No
        commentary, please." and "Don't add any commentary, no headings.": courtesy and further
        prohibitions are not something else.
        """
        start = self.clauses[host][0]
        text = self.text
        before = start
        while before > 0 and text[before - 1].isspace():
            before -= 1
        if before > 0 and text[before - 1] not in _SENTENCE_END_CHARS:
            return False
        return self._sentence_says_more(host)

    def prohibition_restricts_reply(self, op_start: int, op_end: int, *, alone_in_clause: bool = True) -> bool:
        """Whether a negated reply component at [op_start, op_end) is addressed to the reply."""
        host = self.clause_at(op_start)
        bare = (
            host is not None
            and alone_in_clause
            and self.is_bare_prohibition(host, op_start, op_end)
            and not self.is_fronted_adverbial(host)
        )
        return bare or self.attaches_to_reply(op_start)

    def prohibition_in_span_restricts_reply(self, start: int, end: int) -> bool:
        """Whether a span a coarser reader matched as a prohibition ("no intro or notes", "without
        any preamble") holds a prohibition addressed to the reply.

        Each prohibition word in the span is scoped with its own clause, the prohibition running to
        the end of the span or of that clause. Only the first prohibition word of a clause can leave
        the clause bare -- any later one leaves the earlier one behind as content -- so a clause is
        read for bareness at most once.
        """
        for word in _PROHIBITION_WORD_RE.finditer(self.text, start, end):
            if self.attaches_to_reply(word.start()) or self._closes_question(word.start(), word.group(0), end):
                return True
            host = self.clause_at(word.start())
            if host is None or self._first_prohibition_in(host) != word.start():
                continue
            if self.is_bare_prohibition(host, word.start(), min(end, self.clauses[host][1])) and not (
                self.is_fronted_adverbial(host)
            ):
                return True
        return False

    def _closes_question(self, word_start: int, word: str, span_end: int) -> bool:
        """Whether "without X" closes a plain question as an instruction on its answer ("What's 2+2
        without explanation?"): the prohibition ends the question, and nothing before it makes the
        question about someone's text or habits ("how do I write a README without a preamble?")."""
        if word.lower() != "without":
            return False
        host = self.clause_at(word_start)
        if host is None or not self._asks[host] or self._forms[host] is not None:
            return False
        head = self._head(host)
        if head is None or head.word not in _QUESTION_OPENERS or not self.coda_is_terminal(span_end, host):
            return False
        known = self._question_content.get(host)
        if known is None:
            start, end = self.clauses[host]
            found = _QUESTION_CONTENT_RE.search(self.text, start, end)
            known = self._question_content[host] = found.start() if found else end
        return known > word_start

    def _first_prohibition_in(self, host: int) -> int | None:
        if host not in self._first_prohibition:
            start, end = self.clauses[host]
            found = _PROHIBITION_WORD_RE.search(self.text, start, end)
            self._first_prohibition[host] = found.start() if found else None
        return self._first_prohibition[host]

    def coda_restricts_reply(self, op_start: int, op_end: int, *, anchored: bool = False) -> bool:
        """Whether a restrictive coda at [op_start, op_end) closes a request for this reply.

        A sentence that is nothing but the coda ("Name the capital of Peru. Nothing else.") closes
        the request the turn made before it -- or, where the caller knows the turn stated an output
        shape it can close, that shape: `anchored`, in a sentence of its own or after the shape in
        the same sentence ("Exactly three bullet points. Nothing else.", "One word, nothing else.").
        After a statement it closes nothing ("I changed the config. Nothing else. Why does it still
        fail?").
        """
        host = self.clause_at(op_start)
        if not self.coda_is_terminal(op_end, host):
            return False
        if self.scopes_output(op_start):
            return True
        if host is None or not (anchored or self._asking[host] > 0):
            return False
        start = self.clauses[host][0]
        before = _WORD_RE.findall(self.text[start:op_start].lower())
        return (
            all(word in _BARE_FILLER for word in before)
            and (anchored or self._first_in_sentence(host))
            and not self._sentence_says_more(host)
        )


def _exclusive(scope: DeliveryScope) -> bool:
    text = scope.text
    for match in _EXCLUSIVE_ADVERB_RE.finditer(text):
        host = scope.clause_at(match.start())
        if host is None or scope.adnominal(host):
            continue
        if not scope.focus_is_licensed(match.end(), scope.clauses[host][1]):
            continue
        if scope.focus_restricts_reply(host, match.start(), match.end()):
            return True
    for match in _RESTRICTIVE_CODA_RE.finditer(text):
        if scope.coda_restricts_reply(match.start("op"), match.end("op")):
            return True
    return any(scope.verb_restricts_reply(match.start("op")) for match in _NOTHING_EXCEPT_RE.finditer(text))


def parse_delivery_contract(user_text: str) -> DeliveryContract | None:
    """Read the delivery instructions this turn puts on its own visible answer."""
    text = delivery_surface(user_text)
    if not text:
        return None
    return delivery_contract_in(DeliveryScope(text))


def delivery_contract_in(scope: DeliveryScope) -> DeliveryContract | None:
    """The same reading, of a turn the caller already indexed."""
    exclusive = _exclusive(scope)
    matches = list(_NO_COMPONENT_RE.finditer(scope.text))
    # Two prohibitions in one clause leave neither of them bare: each is the other's residue.
    per_clause: dict[int | None, int] = {}
    for match in matches:
        host = scope.clause_at(match.start("op"))
        per_clause[host] = per_clause.get(host, 0) + 1
    components: list[str] = []
    for match in matches:
        host = scope.clause_at(match.start("op"))
        if not scope.prohibition_restricts_reply(
            match.start("op"), match.end("op"), alone_in_clause=per_clause.get(host, 0) == 1
        ):
            continue
        component = " ".join((match.group("component") or match.group("component_alt") or "").lower().split())
        if component and component not in components:
            components.append(component)
    contract = DeliveryContract(exclusive=bool(exclusive or components), forbidden_components=tuple(components))
    return contract if contract.declared() else None


__all__ = [
    "DeliveryContract",
    "DeliveryScope",
    "delivery_contract_in",
    "delivery_surface",
    "is_reply_component",
    "parse_delivery_contract",
]
