"""Origin admission for user-role text: authored declarations vs third-party source material.

A ``role=user`` message may carry somebody else's words — a pasted article, a quoted
prompt, a copied transcript.  Role alone is not proof that an embedded assertion is the
user's own declaration, and the memory writers (fact candidates, profile heuristics,
shorthand) used to scan the raw text, which promoted quoted third-party preferences into
``scope=user_profile`` rows labeled ``confirmed_memory`` / ``direct_user_observation``.

This module owns that admission decision.  It is deterministic, side-effect free, and
conservative in one direction only:

* content inside a *structural source boundary* — markdown blockquotes, fenced code
  (delimiter-identity aware, incl. unclosed fences), pasted transcript speaker runs
  (role headers plus their continuation lines), bracketed fake role labels, and
  line-initial injection-directive lines outside those structures — is SOURCE
  material, never admitted as the user's own;
* a quotation whose *content* is itself an utterance — a first-person declaration or
  a behavioral instruction of the kind the writers harvest — is SOURCE material;
  a quotation without such content (e.g. 'short and practical') is a VALUE inside
  the user's own sentence and stays authored.  The utterance test consults the
  writers' own marker vocabulary (late import) so the boundary and the harvest
  cannot drift apart;
* an authored sentence governed by a hypothetical frame ("suppose my name is Alice")
  is HYPOTHETICAL — retained in chat recall, never admitted as a declaration;
* everything else is AUTHORED and keeps the existing product behavior for direct
  declarations.

Explicit adoption is honoured only when affirmed: an authored adoption directive
that is not negated, questioned or hedged, plus exactly one declarative statement
inside the quoted material, admits that single statement through the legitimate
writer path.  Anything less specific withholds promotion.

Honest limits (covered by tests, not hidden): natural-language authorship cannot be
established from typography.  A bare pasted declaration with no delimiters, no role
labels and no injection markers is indistinguishable from a direct declaration and
is still admitted.  An unclosed quotation pair is not recognizable as a quotation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

AUTHORED = "authored"
SOURCE = "source"
HYPOTHETICAL = "hypothetical"

# ---------------------------------------------------------------------------
# Structural line classification
# ---------------------------------------------------------------------------

#: Fence openers: three or more backticks or tildes, optionally with an info
#: string.  The delimiter CHARACTER and its LENGTH are captured — only a bare
#: fence line of the same character and at least the same length may close the
#: block (a ``` line inside a ~~~ block stays content).
_FENCE_OPEN_RE = re.compile(r"^\s*(?P<marker>`{3,}|~{3,})(?P<info>[^`]*)$")

#: Line-initial role labels used by pasted transcripts and prompt templates:
#: "user:", "Assistant> ...", "agent | ...".  Content after the separator is
#: OPTIONAL — an empty role header still opens/continues a speaker run.
_TRANSCRIPT_ROLE_LINE_RE = re.compile(
    r"^\s*(?:user|assistant|system|ai|human|agent|bot|model|customer|operator|"
    r"interviewer|candidate)\s*[>:\|]",
    re.IGNORECASE,
)

#: Line-initial bracketed role labels — "[system] You are …", "[INST] …", "<<SYS>>".
_BRACKETED_ROLE_LINE_RE = re.compile(
    r"^\s*[\[<]{1,2}\s*(?:system|sys|inst|instruction|assistant|ai|bot|user|human|"
    r"role|end|begin)\b",
    re.IGNORECASE,
)

#: Explicit end-of-transcript boundary markers.
_TRANSCRIPT_END_RE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,}|={3,})\s*$")

_BLOCKQUOTE_LINE_RE = re.compile(r"^\s*>")

#: Line-initial STRICT instruction-override directives — whole-line shape only
#: (``$``-anchored).  A genuine authored declaration does not share a message with
#: a standalone directive line, so one such line OUTSIDE a structural source span
#: classifies the whole message as pasted material.  Lines already inside
#: fences/blockquotes/transcripts are already source and are not re-interpreted.
_INJECTION_DIRECTIVE_LINE_RE = re.compile(
    r"^\s*(?:ignore|disregard|forget)\s+"
    r"(?:(?:all|any|the|your|all\s+of\s+the)\s+)*"
    r"(?:previous|prior|above|earlier|initial|all|any)\s+"
    r"(?:instructions?|contexts?|rules?|prompts?|directions?|messages?|outputs?|"
    r"constraints?)\b\s*[.!]?\s*$",
    re.IGNORECASE,
)
#: Line-initial identity/behavior overrides ("you are now a pirate", "act as …").
#: Unlike strict directives these OPEN a speaker-scaffolding run: the override
#: line and its non-blank continuations are source, but a blank line ends the
#: run so the user's own prose around the paste survives.
_INJECTION_IDENTITY_LINE_RE = re.compile(
    r"^\s*(?:you are now|you will now|act as|behave as|from now on you|"
    r"you must now|new instructions?)\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Utterance content tests (what a quoted span must contain to be SOURCE)
# ---------------------------------------------------------------------------

#: First-person declaration and standing-instruction markers.
_DECLARATION_UTTERANCE_RE = re.compile(
    r"\b(?:my name is|call me|i go by|i prefer|i like|i want you to|"
    r"from now on|my timezone|my time zone|my pronouns|my preferred|my preference|"
    r"my role is|my job is|my location is|i live in|i use|i work on|my project|"
    r"my setup|i maintain)\b",
    re.IGNORECASE,
)

#: Behavioral-instruction SHAPES built from an adjective class rather than an
#: enumerated phrase list: any be/stay/keep/answer/respond/reply/write frame
#: governing a style adjective is an instruction somebody issued, whatever the
#: exact adjective.  Covers the full style vocabulary the heuristic writer
#: harvests (concise/brief/direct/blunt/honest/clear + verbosity words) without
#: enumerating phrases.
_UTTERANCE_SHAPE_RE = re.compile(
    r"\b(?:be|stay)\s+(?:more\s+|very\s+|super\s+)?"
    r"(?:concise|brief|direct|blunt|honest|clear|verbose|short|long|succinct|"
    r"straightforward|terse|punchy|fluffy)\b"
    r"|\bkeep\s+(?:the\s+|your\s+|it\s+|answers?|responses?|replies?|reply|tone|style)\s*"
    r"[^.!?]{0,30}?\b(?:concise|brief|direct|blunt|honest|clear|verbose|short|long|"
    r"succinct|straightforward|terse|punchy)\b"
    r"|\bno\s+(?:fluff|hopium|copium|bullshit|nonsense|filler|platitudes|hedging)\b"
    r"|\bbrutal(?:ly)?\s+honest\b"
    r"|\b(?:always|never)\b[^.!?]{0,40}\b(?:answer|respond|reply|use|remember|be|write|adopt)\b"
    r"|\b(?:answer|respond|reply|write)\s+[^.!?]{0,40}\b"
    r"(?:concise|brief|direct|blunt|honest|clear|verbose|short|long|succinct|"
    r"straightforward|terse|punchy)\b",
    re.IGNORECASE,
)

_WRITER_MARKER_PHRASES: tuple[str, ...] | None = None


def _writer_marker_phrases() -> tuple[str, ...]:
    """Multi-word marker phrases from the heuristic writers' OWN tables.

    The quote-utterance boundary and the heuristic harvest must not be two
    independently maintained lists.  Every multi-word style/source/project/
    autonomy marker the writers scan for is therefore also an utterance marker
    here, imported lazily to avoid an import cycle.  Single-word markers
    ("honest", "python") are deliberately excluded: alone they name VALUES the
    user may legitimately quote, and they cannot speak.
    """
    global _WRITER_MARKER_PHRASES
    if _WRITER_MARKER_PHRASES is None:
        phrases: set[str] = set()
        try:
            from core.memory import learning

            for table in (
                learning._HEURISTIC_STYLE_MARKERS,
                learning._HEURISTIC_SOURCE_MARKERS,
                learning._HEURISTIC_PROJECT_MARKERS,
                learning._HEURISTIC_AUTONOMY_MARKERS,
            ):
                for markers in table.values():
                    for marker in markers:
                        clean = str(marker).strip().lower()
                        if " " in clean:
                            phrases.add(clean)
        except Exception:
            pass
        _WRITER_MARKER_PHRASES = tuple(sorted(phrases))
    return _WRITER_MARKER_PHRASES


def _is_utterance(content: str) -> bool:
    lowered = str(content or "").lower()
    if _DECLARATION_UTTERANCE_RE.search(lowered):
        return True
    if _UTTERANCE_SHAPE_RE.search(lowered):
        return True
    return any(phrase in lowered for phrase in _writer_marker_phrases())


# ---------------------------------------------------------------------------
# Hypothetical frames
# ---------------------------------------------------------------------------

#: Hypothetical-frame verbs.  When one of these governs the clause carrying the
#: declaration, the sentence proposes a fiction.  "For example" is NOT here:
#: "For example, I prefer concise replies" is still the user's own preference.
_HYPOTHETICAL_FRAME_RE = re.compile(
    r"\b(?:suppose|supposing|pretend|pretending|imagine|let'?s say|"
    r"say that|what if|hypothetically|for the sake of argument|in a world where)\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Quotation spans
# ---------------------------------------------------------------------------

#: Quote pairs.  Apostrophe-family quotes carry word-boundary guards on BOTH
#: sides so contractions ("don't", "it's") can never open or close a span;
#: double-quote closers normally follow a word char ("…replies\""), so only the
#: opener is guarded there.  Smart-quote pairs differ between opener and closer.
_QUOTE_SPAN_RE = re.compile(
    r'(?<!\w)"(?P<dbody>.+?)"(?!\w)'
    r"|(?<!\w)\u201c(?P<ddbody>.+?)\u201d(?!\w)"
    r"|(?<!\w)'(?P<sbody>.+?)'(?!\w)"
    r"|(?<!\w)\u2018(?P<ssbody>.+?)\u2019(?!\w)",
    re.DOTALL,
)

#: VALUE SLOT: a quote whose immediately preceding sentence text ends with a
#: copula or naming connective occupies the value position of an authored
#: declaration ("my preferred motto is \"Be honest\"", "…described as 'short
#: and practical'").  Such a quote is a VALUE even when its content is
#: utterance-shaped; it is never separated from its owning declaration.
_QUOTE_VALUE_SLOT_RE = re.compile(
    r"(?:\b(?:is|are|was|were|am|be|being|been|as|like|called|named|"
    r"call|calls|term|terms|label|labels|describe|describes)\s*"
    r"|[:\u2014]\s*)$",
    re.IGNORECASE,
)
#: EXECUTABLE OWNERSHIP RULE (quoted-value admission).  A quote in a value
#: slot belongs to the USER only when the user possesses the declaration it
#: fills; a first-person token ANYWHERE in the prefix is not enough ("I think
#: the editor's motto is …" — I owns "think", the editor owns "motto").  The
#: RIGHTMOST possessive in the clause window before the quote governs the
#: subject's head noun:
#:   first-person possessives: my / our / mine
#:   third-person possessives: his / her / their / its / <noun>'s
#: A1. possessives present  -> user-owned iff the RIGHTMOST one is
#:     first-person ("my motto", "our team motto" yes; "the editor's motto",
#:     "my summary of the editor's motto" no).
#: A2. no possessive at all -> user-owned iff the window carries a
#:     first-person DECLARATION marker from the product's own declaration
#:     vocabulary ("I prefer the tone you call 'X'", "I like the format
#:     called 'X'").  A bare cognition frame ("I think the motto is …")
#:     attributes nothing and withholds.
_FIRST_PERSON_POSSESSIVE_RE = re.compile(r"\b(?:my|our|mine)\b", re.IGNORECASE)
# Possessive analysis accepts singular 's and plural s'. Normalize equivalent
# apostrophes only in the analysis copy; never rewrite stored user text.
_THIRD_PERSON_POSSESSIVE_RE = re.compile(
    r"\b(?:his|her|their|its|your)\b|\b\w+'s\b|\b\w+s'(?!\w)",
    re.IGNORECASE,
)
_POSSESSIVE_APOSTROPHES = str.maketrans(
    {"\u2018": "'", "\u2019": "'", "\u02bc": "'", "\uff07": "'"}
)


def _normalize_possessive_typography(text: str) -> str:
    """Normalize apostrophe equivalents in an analysis copy only."""
    return str(text or "").translate(_POSSESSIVE_APOSTROPHES)


#: Attribution/reporting near the quote: when the prefix names the material's
#: origin, the quote is the OBJECT of a report, not a value being declared —
#: even behind a copula ("my favorite quote from the doc is \"…\"").  Two
#: structural signals, not one long noun list: an origin PREPOSITIONAL PHRASE
#: ("from the doc", "lines from the chat", "in the interview") ending the
#: prefix, and the classic reporting nouns/verbs for sources that name
#: themselves.
_QUOTE_ATTRIBUTION_TAIL_RE = re.compile(
    r"(?:\b(?:from|in)\s+(?:the|this|that|those|these|a|an|my|our|his|her|their)\b[^.!?]{0,30}$"
    r"|\b(?:article|excerpt|quote|quotation|passage|snippet|post|note|text|"
    r"template|reply|memo|letter|email|message|comment|blog|doc|document|"
    r"page|site|url|interview|transcript|log|"
    r"said|says|wrote|writes|claim|claims|claimed|state|states|stated|"
    r"tell|tells|told|reads|read|announce|announces|reported|reports|"
    r"instructions?|prompt|persona)\b[^.!?]{0,40}$)",
    re.IGNORECASE,
)


def _quote_occupies_value_slot(prefix: str) -> bool:
    """Whether an utterance-shaped quote is a USER-OWNED value in a declaration.

    ``prefix`` is the sentence text immediately before the quote's opening
    mark.  All three gates must hold: the prefix ends in a value-slot filler
    (copula / naming connective / colon / dash), no attribution reports the
    quote's origin, and the EXECUTABLE OWNERSHIP RULE above attributes the
    declaration to the user.
    """
    clean = _normalize_possessive_typography(prefix.rstrip())
    if not clean or not _QUOTE_VALUE_SLOT_RE.search(clean + " "):
        return False
    if _QUOTE_ATTRIBUTION_TAIL_RE.search(clean[-80:]):
        return False
    first_positions = [m.start() for m in _FIRST_PERSON_POSSESSIVE_RE.finditer(clean)]
    third_positions = [m.start() for m in _THIRD_PERSON_POSSESSIVE_RE.finditer(clean)]
    if first_positions or third_positions:
        return max(first_positions, default=-1) > max(third_positions, default=-1)
    return bool(_DECLARATION_UTTERANCE_RE.search(clean))

# ---------------------------------------------------------------------------
# Adoption (explicit, affirmed, unambiguous)
# ---------------------------------------------------------------------------

#: Authored adoption of quoted material.  Every phrase carries an anaphoric
#: reference to the quoted material ("that", "this", "it", "same") — bare
#: agreement with the world at large is not an adoption of the quote.
_ADOPTION_RE = re.compile(
    r"\b(?:same (?:here|for me|applies to me)"
    r"|that (?:also )?(?:applies|describes) (?:to )?me"
    r"|i (?:also )?agree with (?:that|this|it)"
    r"|i (?:also )?feel the same"
    r"|that(?:'s| is)? (?:also )?(?:my|mine)"
    r"|adopt (?:this|that|it)"
    r"|count me in"
    r"|me too)\b"
    r"|\bremember (?:it|this|that) for me\b",
    re.IGNORECASE,
)
#: Negation CLASS governing an adoption directive (do/does not, don't,
#: cannot, can't, won't, will not, never, no, stop, refuse, decline).
_NEGATION_CLASS_RE = re.compile(
    r"\b(?:do\s+not|does\s+not|don'?t|cannot|can'?t|can\s+not|won'?t|"
    r"will\s+not|never|no|stop|refuse[sd]?|decline[sd]?)\b",
    re.IGNORECASE,
)
#: Uncertainty CLASS: modal verbs and adverbs of contingency that make a
#: directive tentative rather than asserted ("would", "could", "might",
#: "possibly", "potentially", "probably", …).  "will" is deliberately absent:
#: a future assertion is still an assertion.
_UNCERTAINTY_CLASS_RE = re.compile(
    r"\b(?:would|could|might|may|possibly|potentially|perhaps|probably|"
    r"maybe|conceivably|plausibly)\b",
    re.IGNORECASE,
)
#: Conditional subordinators.  A clause that merely CONSIDERS adoption ("If I
#: adopt this, …") is not a directive; neither is a hypothetical frame
#: ("Suppose I adopt…").  Checked at clause start and sentence-wide.
_CONDITIONAL_CLAUSE_START_RE = re.compile(
    r"^\s*(?:if|unless|when|whenever|in\s+case|provided|providing|assuming|"
    r"assume|suppose|supposing|pretend|imagine|had\s+(?:i|we)|"
    r"should\s+(?:i|we))\b",
    re.IGNORECASE,
)
_CONDITIONAL_MARKER_RE = re.compile(r"\b(?:if|unless)\b", re.IGNORECASE)
#: Clause separators.  Commas deliberately do NOT separate clauses: they carry
#: intra-clause emphasis ("never, ever") and discourse markers ("Yes, count me
#: in"), and splitting on them orphaned governing negations from their verb.
#: Semicolons, colons, dashes and newlines end a clause.
_CLAUSE_SPLIT_RE = re.compile(r"[;:\u2014]|\n")

# Adoption assertions must bind the whole prefix to the predicate. An embedded
# "I will" in reported speech is not the user's declaration. Supported subject
# shapes are I/we and a simple co-subject ("the trainer and I").
_ADOPTION_FILLER_TOKENS = frozenset(
    {
        "please", "yes", "yeah", "yep", "ok", "okay", "well", "just", "also",
        "and", "so", "now", "then", "oh", "hey", "sure", "indeed",
        "certainly", "definitely", "absolutely", "honestly", "seriously",
        "obviously", "clearly", "right",
    }
)
_ADOPTION_FILLER_PREFIX_RE = re.compile(
    r"(?:(?:" + "|".join(sorted(_ADOPTION_FILLER_TOKENS)) + r")\b[\s,]+)*",
    re.IGNORECASE,
)
_ADOPTION_ASSERTION_PREFIX_RE = re.compile(
    r"(?:i|we|(?:the\s+[\w-]+|[\w-]+)\s+and\s+i)"
    r"(?:'ll|\s+(?:will|do))?"
    r"(?:\s+(?:also|now|hereby|definitely|certainly|gladly|happily))*"
    r"(?:\s+(?:(?:have\s+)?(?:decided|chosen)|choose|decide|want)\s+to"
    r"|\s+(?:am|are)\s+going\s+to)?"
    r"(?:\s+(?:also|now|hereby|definitely|certainly|gladly|happily))*",
    re.IGNORECASE,
)

#: Sentence shapes that make an adoption directive a question, not an assertion.
#: Interrogative inversion only (question verb + pronoun); a leading imperative
#: "Do not …" / "Do adopt …" is not a question.
_QUESTION_SENTENCE_START_RE = re.compile(
    r"^\s*(?:does|do|did|is|are|was|can|could|should|would|will)\s+"
    r"(?:that|this|it|you|i|we|they)\b",
    re.IGNORECASE,
)
#: Hedges and retractions that make an apparent adoption uncertain.
_HEDGE_RE = re.compile(
    r"\b(?:not sure|unsure|maybe|perhaps|possibly|might not|i might|"
    r"no wait|scratch that|not really|on second thought)\b",
    re.IGNORECASE,
)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Segment:
    """One contiguous span of the original text with its origin classification."""

    text: str
    kind: str


@dataclass(frozen=True)
class UserTextOrigin:
    raw: str
    segments: tuple[Segment, ...] = field(default=())

    @property
    def has_source_material(self) -> bool:
        return any(seg.kind == SOURCE for seg in self.segments)

    @property
    def source_text(self) -> str:
        return "\n".join(seg.text for seg in self.segments if seg.kind == SOURCE)

    @property
    def authored_text(self) -> str:
        """All user-authored prose (hypothetical sentences excluded)."""
        return "\n".join(
            _strip_hypothetical_sentences(seg.text)
            for seg in self.segments
            if seg.kind == AUTHORED
        ).strip()

    @property
    def adoption_directive(self) -> bool:
        """Whether the user AFFIRMED adoption of quoted material.

        A directive negated in its own clause ("Do not adopt this"), asked as a
        question ("Does that describe me?"), or hedged ("maybe that's me, not
        sure") is not an adoption.  Only the user's authored prose is searched.
        """
        authored = self.authored_text
        if not authored:
            return False
        for sentence in _SENTENCE_SPLIT_RE.split(authored):
            for match in _ADOPTION_RE.finditer(sentence):
                if _adoption_is_affirmed(match, sentence):
                    return True
        return False

    def adoptable_source_statements(self) -> list[str]:
        """Declarative statements inside source material an adoption could promote.

        More than one candidate means the adoption is ambiguous about WHICH
        statement is adopted; callers must then withhold promotion.
        """
        if not self.adoption_directive:
            return []
        return _ADOPTABLE_STATEMENT_RE.findall(self.source_text)

    def __bool__(self) -> bool:  # pragma: no cover - trivial
        return bool(self.raw.strip())


#: A single declarative statement inside source material that an affirmed
#: adoption could promote: first-person identity/preference declarations only.
_ADOPTABLE_STATEMENT_RE = re.compile(
    r"(?:my name is\s+[a-z0-9][a-z0-9 '\-]{1,40}"
    r"|call me\s+[a-z0-9][a-z0-9 '\-]{1,40}"
    r"|i go by\s+[a-z0-9][a-z0-9 '\-]{1,40}"
    r"|i prefer\s+[^\n.!?]{1,120}"
    r"|i like\s+[^\n.!?]{1,120}"
    r"|i live in\s+[^\n.!?]{1,60}"
    r"|my timezone is\s+[^\n.!?]{1,40})",
    re.IGNORECASE,
)


def _adoption_is_affirmed(match: re.Match[str], sentence: str) -> bool:
    """Accept an explicit imperative or a bounded user-owned assertion.

    Match the entire prefix, not a nearby pronoun or an unordered bag of
    linking words. A chained affirmative inherits permission only from a
    prior phrase that independently satisfies the same rule.
    """
    stripped = sentence.strip()
    if stripped.endswith("?") or _QUESTION_SENTENCE_START_RE.match(stripped):
        return False
    if _HEDGE_RE.search(sentence) or _CONDITIONAL_MARKER_RE.search(sentence):
        return False
    clause_start = 0
    for separator in _CLAUSE_SPLIT_RE.finditer(sentence[:match.start()]):
        clause_start = separator.end()
    clause = sentence[clause_start:]
    if _CONDITIONAL_CLAUSE_START_RE.match(clause):
        return False
    governed = sentence[clause_start:match.end()]
    if _NEGATION_CLASS_RE.search(governed) or _UNCERTAINTY_CLASS_RE.search(governed):
        return False
    pre = sentence[clause_start:match.start()].strip().rstrip(",;:!?").strip()
    if not pre:
        return True
    tokens = {piece.strip("',.!?\u2019").lower() for piece in pre.split()}
    if tokens and tokens <= _ADOPTION_FILLER_TOKENS:
        return True
    for prior in _ADOPTION_RE.finditer(pre):
        if prior.end() == len(pre) and _adoption_is_affirmed(prior, pre):
            return True
    pre = _normalize_possessive_typography(pre)
    pre = _ADOPTION_FILLER_PREFIX_RE.sub("", pre, count=1)
    return _ADOPTION_ASSERTION_PREFIX_RE.fullmatch(pre) is not None


def _strip_hypothetical_sentences(text: str) -> str:
    """Drop sentences governed by a hypothetical frame; keep the rest verbatim."""
    kept: list[str] = []
    for sentence in _SENTENCE_SPLIT_RE.split(text):
        if not sentence.strip():
            continue
        frame = _HYPOTHETICAL_FRAME_RE.search(sentence)
        if frame is not None:
            rest = sentence[frame.end():]
            if _is_utterance(rest) or _ADOPTABLE_STATEMENT_RE.search(rest):
                continue
        kept.append(sentence)
    return "\n".join(kept)


def _quote_spans_are_utterances(text: str) -> tuple[str, list[str]]:
    """Split authored text into unquoted prose and quoted utterance spans.

    A quotation whose content is an utterance (a declaration or behavioral
    instruction somebody could have spoken) is source material — UNLESS it
    occupies the value slot of an authored declaration ("my preferred motto is
    \"Be honest\""), in which case it is the user's own value and stays inline
    with its declaration.  A quotation without utterance content (e.g.
    'short and practical') also stays.
    """
    source_spans: list[str] = []
    remove_ranges: list[tuple[int, int]] = []
    for match in _QUOTE_SPAN_RE.finditer(text):
        body = next(
            group
            for key, group in match.groupdict().items()
            if group is not None and key.endswith("body")
        )
        if not _is_utterance(body):
            continue
        prefix = text[: match.start()]
        last_break = max(prefix.rfind("."), prefix.rfind("!"), prefix.rfind("?"))
        if last_break != -1:
            prefix = prefix[last_break + 1 :]
        if _quote_occupies_value_slot(prefix):
            continue
        source_spans.append(body)
        remove_ranges.append((match.start(), match.end()))
    remainder = text
    for start, end in reversed(remove_ranges):
        remainder = remainder[:start] + " " + remainder[end:]
    return remainder, source_spans


def _structural_line_kinds(lines: list[str]) -> list[str | None]:
    """Per-line structural classification, fence/transcript state machines first.

    Kinds: ``'fence'`` (fence lines and content), ``'block'`` (blockquote),
    ``'role'`` (transcript speaker header), ``'transcript'`` (continuation of a
    speaker's turn), ``'boundary'`` (end-of-transcript marker), ``None`` (plain
    authored line) — blanks are ``None``.
    """
    kinds: list[str | None] = [None] * len(lines)
    fence_char = ""
    fence_len = 0
    in_transcript = False
    pending_blank = False

    for index, line in enumerate(lines):
        if fence_char:
            kinds[index] = "fence"
            closer = re.match(r"^\s*(%s{%d,})\s*$" % (fence_char, fence_len), line)
            if closer:
                fence_char = ""
            continue
        opener = _FENCE_OPEN_RE.match(line)
        if opener:
            fence_char = opener.group("marker")[0]
            fence_len = len(opener.group("marker"))
            kinds[index] = "fence"
            continue

        if _BLOCKQUOTE_LINE_RE.match(line):
            kinds[index] = "block"
            continue

        if _TRANSCRIPT_ROLE_LINE_RE.match(line) or _BRACKETED_ROLE_LINE_RE.match(line):
            kinds[index] = "role"
            in_transcript = True
            pending_blank = False
            continue

        if _INJECTION_IDENTITY_LINE_RE.match(line):
            # Identity/behavior override: speaker scaffolding, same continuation
            # rules as a transcript header (blank line or end marker closes it).
            kinds[index] = "role"
            in_transcript = True
            pending_blank = False
            continue

        if in_transcript:
            if _TRANSCRIPT_END_RE.match(line):
                kinds[index] = "boundary"
                in_transcript = False
                pending_blank = False
                continue
            if not line.strip():
                pending_blank = True
                continue
            if pending_blank:
                # A blank line followed by ordinary prose ends the pasted
                # transcript; the user's own commentary resumes as authored.
                in_transcript = False
                pending_blank = False
                continue
            kinds[index] = "transcript"
            continue
    return kinds


def classify_user_text(user_input: str) -> UserTextOrigin:
    """Classify one user-role message into authored / source / hypothetical spans.

    Deterministic and typography-only.  Chat retention is unaffected: the caller
    keeps logging and semantically storing the RAW text; only admission into
    personal facts, profile heuristics and shorthand uses the authored spans.
    """
    raw = str(user_input or "")
    if not raw.strip():
        return UserTextOrigin(raw=raw, segments=())

    lines = raw.splitlines()
    kinds = _structural_line_kinds(lines)

    # Whole-message rule: an authored (plain) line that opens with an injection
    # directive or an identity override marks the ENTIRE message as pasted
    # third-party material.  Lines already inside a structural source span are
    # skipped — a fenced "You are now a pirate." template must not swallow the
    # genuine declaration written above it.
    for line, kind in zip(lines, kinds, strict=False):
        if kind is not None:
            continue
        if _INJECTION_DIRECTIVE_LINE_RE.match(line) or _INJECTION_IDENTITY_LINE_RE.match(line):
            return UserTextOrigin(raw=raw, segments=(Segment(raw, SOURCE),))

    segments: list[Segment] = []
    authored_buffer: list[str] = []
    quoted_source_buffer: list[str] = []
    source_buffer: list[str] = []

    def _flush_authored() -> None:
        if not any(part.strip() for part in authored_buffer):
            authored_buffer.clear()
            return
        joined = "\n".join(authored_buffer)
        authored_buffer.clear()
        unquoted, utterance_quotes = _quote_spans_are_utterances(joined)
        if utterance_quotes:
            quoted_source_buffer.extend(utterance_quotes)
        if unquoted.strip():
            segments.append(Segment(unquoted, AUTHORED))

    def _flush_source() -> None:
        if source_buffer:
            segments.append(Segment("\n".join(source_buffer), SOURCE))
            source_buffer.clear()

    for line, kind in zip(lines, kinds, strict=False):
        if kind is None:
            if source_buffer:
                _flush_source()
            authored_buffer.append(line)
        else:
            if authored_buffer:
                _flush_authored()
            source_buffer.append(line)
    _flush_authored()
    _flush_source()
    if quoted_source_buffer:
        segments.append(Segment("\n".join(quoted_source_buffer), SOURCE))
    return UserTextOrigin(raw=raw, segments=tuple(segments))
