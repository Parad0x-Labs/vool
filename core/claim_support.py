"""Per-claim, per-source support: which sources support which claims in an answer.

Why this exists, measured on a2308a26 (2026-09-01, browser drive of the candidate build,
`validation-logs/live-search-ui-proof-20260901/B_r1_open_defect_result.json`). A turn asking for
recent Rust news retrieved three real, dated Google News rows and shipped four invented headlines
("Rust 1.64 Released ..." -- a 2022 version). `core.evidence_binding.inspect_evidence_binding`
returned ``grounded=True`` because the fabricated headline and a real one both contained the word
"released". One incidental shared verb grounded an answer that carried none of the retrieval, and
that one boolean then laundered every unsupported claim in the reply at once.

What this module does instead
-----------------------------

It decomposes the answer into independently testable claims and asks, for each claim, WHICH bound
source supports it. The result is a :class:`ClaimSupportMap`: claim -> supporting source IDs,
the list of unsupported claims, and an overall coverage status. Three properties are load-bearing:

* **Per-claim isolation.** One supported claim cannot ground a fabricated sibling. An answer that
  is half evidence and half invention reports exactly which half is which.
* **Structural support, never one generic word.** A claim is supported only by structure
  appropriate to it -- a matching value/version/date, a matching quoted span, a matching named
  entity, or (for anchor-less prose) at least two distinctive shared terms. Stopwords and the
  small closed class of publication verbs ("released", "announced", ...) can never witness
  support on their own.
* **Bound source content only.** Support is judged against what the bound notes SAY (the same
  content fields `core.evidence_binding` reads), never against provenance, receipts, or anything
  else in the turn's artifact bag. A successful retrieval receipt with zero bound notes supports
  nothing.

Conservative by construction: a claim this module cannot adjudicate is reported as
``uncertain`` or ``unsupported``, never guessed into support.

Determinism: no wall clock is read. Recency for "current" claims is judged against the explicit
``as_of`` argument when the caller provides one, else against the newest date the bound notes
themselves carry; with neither, currency is recorded as unverified rather than guessed either way.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

#: Fields carrying what retrieval FOUND -- kept identical to `core.evidence_binding` so the two
#: verdicts always read the same content. Provenance (`result_url`, `origin_domain`, labels) is
#: deliberately absent: where VOOL looked is not what it found.
_CONTENT_FIELDS = ("summary", "live_quote", "result_title", "snippet", "text", "content")

#: Closed class of publication/reporting words. Structural, not topical: these state THAT
#: something was published or reported, in any domain, and are exactly the words a fabricated
#: headline shares with a real one. They can never be the sole witness of support, and they are
#: never entity material. Nothing domain-specific belongs in this set.
GENERIC_SUPPORT_TERMS = frozenset(
    [
    "released", "release", "releases", "releasing", "launched", "launch", "launches",
    "announced", "announces", "announcement", "published", "publishes", "publishing",
    "unveiled", "unveils", "introduced", "introduces", "shipped", "ships", "shipping",
    "reported", "reports", "reporting", "according", "update", "updated", "updates",
    "news", "headline", "headlines", "coverage", "story", "stories", "article", "articles",
    "latest", "recent", "recently", "new", "newest", "version", "source", "sources",
    ]
)

#: Words and folded phrases that mark a claim as asserting CURRENT truth. Small and structural.
_CURRENCY_TOKENS = frozenset(["today", "now", "currently", "tonight"])
_CURRENCY_PHRASES = (
    "right now",
    "as of now",
    "at the moment",
    "this week",
    "this morning",
    "this month",
)

#: A supporting source for a current claim must not be older than this relative to the reference
#: date. Structural bound, not a topic judgement: beyond it, a dated note cannot witness "today".
CURRENT_HORIZON_DAYS = 30

_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
#: Numbers: prices, readings, versions, counts. Dates are extracted separately and excluded here.
#: The lookahead permits a sentence-final period ("$64,000.") while refusing to split a decimal
#: ("64.5") or bind digits glued to letters ("24h").
_NUMBER_RE = re.compile(r"(?<![\w.])\$?\d[\d,]*(?:\.\d+)*%?(?!\w|\.\d)")
_URL_RE = re.compile(r"\(?\bhttps?://\S+\)?")
_QUOTE_RE = re.compile(r"[\"“]([^\"“”]{2,}?)[\"”]")
_WORD_RE = re.compile(r"[^\W\d_][\w'&.-]*", re.UNICODE)
_CAP_WORD_RE = re.compile(r"[A-ZÀ-Þ][\w'&.-]*")
#: A sentence ends at ". " / "! " / "? " -- unless the period belongs to an abbreviation. Measured
#: live 2026-09-07: "# Volkswagen Golf vs. Toyota Prius: Head-to-Head" was cut at "vs." and the
#: second half adjudicated (and withheld) as a claim of its own. Each lookbehind is fixed-width,
#: as Python's `re` requires; the list is the small set of abbreviations that end no sentence.
_SENTENCE_SPLIT_RE = re.compile(
    r"(?<=[.!?])"
    r"(?<!\bvs\.)(?<!\be\.g\.)(?<!\bi\.e\.)(?<!\betc\.)(?<!\bcf\.)(?<!\bca\.)(?<!\bal\.)"
    r"(?<!\bNo\.)(?<!\bMr\.)(?<!\bMrs\.)(?<!\bMs\.)(?<!\bDr\.)(?<!\bSt\.)(?<!\bJr\.)(?<!\bSr\.)"
    r"(?<!\bU\.S\.)(?<!\bU\.K\.)(?<!\bInc\.)(?<!\bLtd\.)(?<!\bCo\.)(?<!\bapprox\.)(?<!\bfig\.)"
    r"\s+"
)
#: Markdown emphasis and code markers wrap words; they are never part of what a sentence claims.
#: Markdown reads "_" and "__" as emphasis only at a word boundary; inside an identifier
#: ("max_connections", "__init__") they are the word. Asterisks and backticks are never word
#: characters, so they always come off.
_EMPHASIS_MARKER_RE = re.compile(
    r"(\*\*|\*|`|(?<!\w)__(?=\w)|(?<=\w)__(?!\w)|(?<!\w)_(?=\w)|(?<=\w)_(?!\w))"
)
#: A markdown heading is the shape of the answer, not an assertion inside it.
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+")


def strip_emphasis_markers(text: str) -> str:
    """`text` without markdown emphasis / code markers, whitespace otherwise untouched."""
    return _EMPHASIS_MARKER_RE.sub("", str(text or ""))
_CLAUSE_SPLIT_RE = re.compile(r",\s+(?=(?:and|but)\s)|;\s+")
_BULLET_RE = re.compile(r"^\s*(?:[-*•>]+|\d+[.)])\s+")
_TOKEN_RE = re.compile(r"[0-9]+(?:[.,][0-9]+)*|[^\W\d_]+", re.UNICODE)

# Stopwords shared with `core.evidence_binding` (imported there; duplicated nowhere).


def _fold(text: Any) -> str:
    decomposed = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()


def _fold_ws(text: Any) -> str:
    return " ".join(_fold(text).split())


@dataclass(frozen=True)
class ClaimAnchors:
    """Typed structure a claim asserts. Anchors already supplied by the request are excluded --
    restating the question cannot witness that a source was read."""

    entities: tuple[str, ...] = ()
    numerics: tuple[str, ...] = ()
    dates: tuple[str, ...] = ()
    quotes: tuple[str, ...] = ()
    terms: tuple[str, ...] = ()
    currency_markers: tuple[str, ...] = ()
    #: Outward API endpoints a fenced CODE ARTIFACT asserts (``endpoint:`` segments). A URL in
    #: prose is a citation; the same URL in code is a fact the program depends on -- it names
    #: where bytes will be sent -- so it is adjudicated like any other asserted fact, never
    #: laundered by the fence around it.
    endpoints: tuple[str, ...] = ()
    #: Subjects the REQUEST named that this claim is about ("Golf", "Passat"). They cannot witness
    #: support -- restating the question proves nothing -- but a source that never mentions the
    #: claim's subject cannot support the claim either (measured 2026-09-06: a Passat production
    #: page that mentions Europe "supported" "Regions: Golf sells mainly in Europe").
    subjects: tuple[str, ...] = ()

    @property
    def specific(self) -> bool:
        """The claim asserts a concrete value, date or quotation. Entity-only claims are not
        `specific`: they name something without pinning a measurable fact to it."""

        return bool(self.numerics or self.dates or self.quotes or self.endpoints)


@dataclass(frozen=True)
class ClaimSupport:
    """The verdict for one claim: which bound sources support it, and why or why not."""

    claim_id: str
    text: str
    status: str  # "supported" | "unsupported" | "uncertain"
    supporting_sources: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    anchors: ClaimAnchors = field(default_factory=ClaimAnchors)

    def as_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "text": self.text,
            "status": self.status,
            "supporting_sources": list(self.supporting_sources),
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class ClaimSupportMap:
    """Deterministic per-claim support for one answer against its bound sources.

    ``coverage`` vocabulary:

    * ``full`` -- at least one claim supported and no claim unsupported (uncertain prose allowed);
    * ``partial`` -- supported and unsupported claims coexist;
    * ``none`` -- claims exist and none is supported;
    * ``no_claims`` -- the answer carries nothing independently testable;
    * ``no_sources`` -- no bound note carried content; nothing can be supported, receipts included.
    """

    claims: tuple[ClaimSupport, ...] = ()
    source_ids: tuple[str, ...] = ()
    source_labels: Mapping[str, str] = field(default_factory=dict)
    coverage: str = "no_claims"

    @property
    def supported_claims(self) -> tuple[ClaimSupport, ...]:
        return tuple(claim for claim in self.claims if claim.status == "supported")

    @property
    def unsupported_claims(self) -> tuple[ClaimSupport, ...]:
        return tuple(claim for claim in self.claims if claim.status == "unsupported")

    def as_dict(self) -> dict[str, Any]:
        return {
            "coverage": self.coverage,
            "claims": [claim.as_dict() for claim in self.claims],
            "source_ids": list(self.source_ids),
            "source_labels": dict(self.source_labels),
            "supported_claim_ids": [c.claim_id for c in self.supported_claims],
            "unsupported_claim_ids": [c.claim_id for c in self.unsupported_claims],
        }


@dataclass(frozen=True)
class _Source:
    source_id: str
    label: str
    folded: str
    terms: frozenset[str]
    numerics: frozenset[str]
    dates: tuple[date, ...]
    has_currency_marker: bool


def _note_content(note: Any) -> str:
    if not isinstance(note, Mapping):
        return str(note or "")
    parts = [str(note.get(field_name) or "") for field_name in _CONTENT_FIELDS]
    return " ".join(part for part in parts if part.strip())


def _content_terms(text: Any, stopwords: frozenset[str]) -> set[str]:
    terms: set[str] = set()
    for match in _TOKEN_RE.finditer(_fold(text)):
        token = match.group(0)
        if token in stopwords:
            continue
        if token[0].isdigit():
            terms.add(token.replace(",", "."))
            continue
        if len(token) >= 3:
            terms.add(token)
    return terms


def _extract_dates(text: str) -> tuple[date, ...]:
    found: list[date] = []
    for year, month, day in _ISO_DATE_RE.findall(text):
        try:
            found.append(date(int(year), int(month), int(day)))
        except ValueError:
            continue
    return tuple(found)


def _extract_numerics(text: str) -> set[str]:
    """Digit anchors, normalized ('$35,200' -> '35200'; '28.0' -> '28'), ISO dates excluded.

    The trailing-zero fold is exact numeric equality, not a looseness: a node that renders
    its reading as ``28.0`` and a sentence that cites ``28°C`` state the same number, and a
    string-set comparison that refused the pair withheld a true comparison line beside the
    readings that proved it -- measured on the served mixed-demand drive, where the two
    weather rows were supported and the comparison built from them was not.
    """

    stripped = _ISO_DATE_RE.sub(" ", text)
    numerics: set[str] = set()
    for match in _NUMBER_RE.finditer(stripped):
        token = match.group(0).strip("$%").replace(",", "").replace("$", "")
        if not token:
            continue
        try:
            value = float(token)
        except ValueError:
            numerics.add(token)
            continue
        if value == int(value) and abs(value) < 1e15:
            numerics.add(str(int(value)))
        else:
            numerics.add(token)
    return numerics


def _has_currency_marker(folded_text: str) -> tuple[str, ...]:
    markers = [
        token
        for token in (raw.strip("'&.-") for raw in _WORD_RE.findall(folded_text))
        if token in _CURRENCY_TOKENS
    ]
    markers.extend(phrase for phrase in _CURRENCY_PHRASES if phrase in folded_text)
    return tuple(dict.fromkeys(markers))


_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|(?:\s*:?-{2,}:?\s*\|)+\s*$")


#: The fence shapes whose contents are CODE, not prose.
_FENCE_OPEN_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})(.*)$")
_FENCE_CLOSE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})\s*$")

#: Prefix marking a synthetic claim for one outward API endpoint a code artifact asserts.
#: Generated program structure is an ARTIFACT, not a list of sourced factual sentences: its
#: lines are never segmented as prose claims (measured on the live daemon 2026-09-17, an
#: OpenRouter research turn shredded a delivered Python script into 125 withheld and 98
#: uncertain per-line "statements" -- imports, declarations and assignments adjudicated against
#: web pages). The one thing code carries that IS an externally asserted fact is an endpoint
#: URL: the program will send bytes there, so it is claimed, witnessed and withheld as a unit.
ENDPOINT_CLAIM_PREFIX = "endpoint: "


def endpoint_claims_from_code(code_lines: Iterable[str]) -> list[str]:
    """One marked segment per distinct outward URL the fenced code asserts."""
    seen: set[str] = set()
    claims: list[str] = []
    for line in code_lines:
        for match in _URL_RE.finditer(str(line or "")):
            url = match.group(0).rstrip(")\"'`,. ;")
            if url and url not in seen:
                seen.add(url)
                claims.append(ENDPOINT_CLAIM_PREFIX + url)
    return claims


def code_fence_line_mask(text: Any) -> list[bool]:
    """Per line of ``text``: True when the line is inside -- or is a border of -- a fenced code
    block. The publication gate uses this to keep code artifacts out of per-line claim
    adjudication and to withhold a block whole when its endpoints are unwitnessed."""
    mask: list[bool] = []
    fence_marker = ""
    for raw_line in str(text or "").splitlines():
        if fence_marker:
            close = _FENCE_CLOSE_RE.match(raw_line)
            if close and close.group(1)[0] == fence_marker[0]:
                fence_marker = ""
                mask.append(True)
            else:
                mask.append(True)
            continue
        opened = _FENCE_OPEN_RE.match(raw_line)
        if opened:
            fence_marker = opened.group(1)[:3]
            mask.append(True)
            continue
        mask.append(False)
    return mask


def segment_claims(answer: Any) -> list[str]:
    """Split an answer into candidate claim segments: bullet/numbered lines and sentences,
    with top-level coordinated clauses (", and" / ", but" / ";") separated. Lead-in lines that
    end in a colon introduce content rather than assert it, and are dropped.

    Fenced code is NOT claim territory. Its lines are program structure -- generated artifact,
    not sourced prose -- so they yield no segments; the outward endpoints it asserts are
    extracted instead as one ``endpoint:`` claim per URL, which the matcher witnesses against
    the bound sources like any other asserted fact.
    """

    segments: list[str] = []
    fence_marker = ""
    code_lines: list[str] = []

    def _flush_code() -> None:
        segments.extend(endpoint_claims_from_code(code_lines))
        code_lines.clear()

    def _cut(body: str) -> None:
        # Emphasis markers come off BEFORE cutting: "- **Prius**: Smoother ..." otherwise yields the
        # segment "Prius**: Smoother ..." (the opening marker stripped by the edge-strip below, the
        # closing one left inside), and removing that segment from its line leaves a dangling "**".
        body = strip_emphasis_markers(body)
        for sentence in _SENTENCE_SPLIT_RE.split(body):
            for clause in _CLAUSE_SPLIT_RE.split(sentence):
                clause = clause.strip(" \t*_`>")
                if clause:
                    segments.append(clause)

    def _cut_prose_line(raw_line: str, lines: list[str], index: int) -> None:
        line_text = _URL_RE.sub(" ", raw_line)
        if _TABLE_ROW_RE.match(line_text):
            # A markdown table row packs one claim per CELL. Cutting it as one segment made the
            # whole row a single claim (measured live 2026-09-06: one unsupported cell withheld the
            # supported fact beside it, and the header row's entity names were adjudicated as
            # claims). The separator row and the header row (the row a separator follows) are
            # shape, not assertion; the first cell of a data row is its label.
            if _TABLE_SEPARATOR_RE.match(line_text):
                return
            if index + 1 < len(lines) and _TABLE_SEPARATOR_RE.match(_URL_RE.sub(" ", lines[index + 1])):
                return
            cells = [cell.strip() for cell in line_text.strip().strip("|").split("|")]
            for cell in cells[1:]:
                if cell:
                    _cut(cell)
            return
        if _HEADING_RE.match(line_text):
            # A heading asserts nothing: it is the shape the paragraphs below hang on. Measured live
            # 2026-09-07: every "## ..." of a comparison was adjudicated and withheld, and the reader
            # got the body of each section with no section.
            return
        line = _BULLET_RE.sub("", line_text).strip()
        if not line or line.endswith(":"):
            return
        _cut(line)

    raw_lines = str(answer or "").splitlines()
    for index, raw_line in enumerate(raw_lines):
        if fence_marker:
            close = _FENCE_CLOSE_RE.match(raw_line)
            if close and close.group(1)[0] == fence_marker[0]:
                fence_marker = ""
                continue
            code_lines.append(raw_line)
            continue
        opened = _FENCE_OPEN_RE.match(raw_line)
        if opened:
            _flush_code()
            fence_marker = opened.group(1)[:3]
            continue
        _cut_prose_line(raw_line, raw_lines, index)
    _flush_code()
    return segments


def _mid_sentence_capitalized(texts: Sequence[str]) -> set[str]:
    """Folded forms of words seen capitalized mid-sentence -- evidence of a proper noun."""

    seen: set[str] = set()
    for text in texts:
        for match in _CAP_WORD_RE.finditer(str(text or "")):
            start = match.start()
            prefix = str(text)[:start].rstrip()
            if not prefix:
                continue
            if prefix[-1] in ".!?:;•->*\"'“’" or prefix.endswith(("\n", "|")):
                continue
            seen.add(_fold(match.group(0)))
    return seen


def _lowercase_seen(texts: Sequence[str]) -> set[str]:
    seen: set[str] = set()
    for text in texts:
        for match in _WORD_RE.finditer(str(text or "")):
            token = match.group(0)
            if token[0].islower():
                seen.add(_fold(token).strip("'&.-"))
    return seen


def _entity_candidates(
    segment: str,
    *,
    cross_caps: set[str],
    lowercase_seen: set[str],
    stopwords: frozenset[str],
    request_terms: set[str],
) -> tuple[str, ...]:
    """Folded entity phrases the claim names. Sentence-initial single words count only when
    something corroborates proper-noun-hood: the same word capitalized mid-sentence somewhere,
    or never seen lowercase anywhere in scope. Generic publication words are never entities,
    and entities the request already supplied cannot witness support."""

    entities: list[str] = []
    for match in re.finditer(
        r"(?:[A-ZÀ-Þ][\w'&.-]*)(?:[ ]+[A-ZÀ-Þ][\w'&.-]*)*", segment
    ):
        tokens = match.group(0).split()
        folded_tokens = [_fold(token).strip("'&.-") for token in tokens]
        kept = [
            (raw, folded)
            for raw, folded in zip(tokens, folded_tokens, strict=True)
            if len(folded) >= 2
            and folded not in stopwords
            and folded not in GENERIC_SUPPORT_TERMS
        ]
        if not kept:
            continue
        at_sentence_start = match.start() == 0
        if len(kept) == 1 and at_sentence_start:
            folded = kept[0][1]
            if folded not in cross_caps and folded in lowercase_seen:
                continue
        phrase = " ".join(folded for _, folded in kept)
        phrase_tokens = set(phrase.split())
        if phrase_tokens <= request_terms:
            continue
        entities.append(phrase)
    return tuple(dict.fromkeys(entities))


def extract_claim_anchors(
    segment: str,
    *,
    cross_caps: set[str],
    lowercase_seen: set[str],
    stopwords: frozenset[str],
    request_terms: set[str],
    request_numerics: set[str],
) -> ClaimAnchors:
    folded_segment = _fold(segment)
    if segment.startswith(ENDPOINT_CLAIM_PREFIX):
        # A code artifact's outward endpoint: the URL itself is the whole claim. Its support is
        # decided by the endpoint matcher, not by term overlap around it.
        return ClaimAnchors(
            endpoints=(str(segment[len(ENDPOINT_CLAIM_PREFIX) :]).strip(),),
        )
    quotes = tuple(
        _fold_ws(quoted)
        for quoted in _QUOTE_RE.findall(segment)
        if len(quoted.split()) >= 2
    )
    numerics = tuple(sorted(_extract_numerics(segment) - request_numerics))
    dates = tuple(str(d) for d in _extract_dates(segment))
    entities = _entity_candidates(
        segment,
        cross_caps=cross_caps,
        lowercase_seen=lowercase_seen,
        stopwords=stopwords,
        request_terms=request_terms,
    )
    terms = tuple(
        sorted(
            _content_terms(segment, stopwords)
            - GENERIC_SUPPORT_TERMS
            - request_terms
            - {token for token in _content_terms(segment, stopwords) if token[0].isdigit()}
        )
    )
    return ClaimAnchors(
        entities=entities,
        numerics=numerics,
        dates=dates,
        quotes=quotes,
        terms=terms,
        currency_markers=_has_currency_marker(folded_segment),
        subjects=_subject_candidates(segment, stopwords=stopwords, request_terms=request_terms),
    )


def _build_sources(notes: Sequence[Any] | None) -> list[_Source]:
    sources: list[_Source] = []
    from core.evidence_binding import _STOPWORDS  # single stopword authority

    for index, note in enumerate(list(notes or [])):
        content = _note_content(note)
        if not content.strip():
            continue
        label = ""
        if isinstance(note, Mapping):
            label = str(
                note.get("origin_domain")
                or note.get("source_profile_label")
                or note.get("result_title")
                or ""
            ).strip()
        folded = _fold_ws(content)
        sources.append(
            _Source(
                source_id=f"note-{index + 1}",
                label=label,
                folded=folded,
                terms=frozenset(_content_terms(content, _STOPWORDS)),
                numerics=frozenset(_extract_numerics(content)),
                dates=_extract_dates(content),
                has_currency_marker=bool(_has_currency_marker(folded)),
            )
        )
    return sources


def _entity_in_source(entity: str, source: _Source) -> bool:
    if " " in entity:
        return entity in source.folded
    return re.search(rf"(?<![\w'-]){re.escape(entity)}(?![\w'-])", source.folded) is not None


def _year_like(value: str) -> bool:
    return bool(re.fullmatch(r"(?:19|20)\d\d", str(value or "")))


def _subject_in_source(subject: str, source: _Source) -> bool:
    """A subject phrase is present when any of its distinctive tokens is a word of the source
    ("vw passat" is present in a page that says "Volkswagen Passat")."""
    tokens = [token for token in str(subject or "").split() if len(token) >= 3]
    if not tokens:
        return False
    return any(
        re.search(rf"(?<![\w'-]){re.escape(token)}(?![\w'-])", source.folded) is not None for token in tokens
    )


def _subject_candidates(segment: str, *, stopwords: frozenset[str], request_terms: set[str]) -> tuple[str, ...]:
    """Capitalised phrases of the claim whose every token the request supplied: the claim's subjects."""
    subjects: list[str] = []
    for match in re.finditer(r"(?:[A-ZÀ-Þ][\w'&.-]*)(?:[ ]+[A-ZÀ-Þ][\w'&.-]*)*", segment):
        folded_tokens = [_fold(token).strip("'&.-") for token in match.group(0).split()]
        kept = [tok for tok in folded_tokens if len(tok) >= 2 and tok not in stopwords and tok not in GENERIC_SUPPORT_TERMS]
        if not kept:
            continue
        if set(kept) <= request_terms and any(len(tok) >= 3 for tok in kept):
            subjects.append(" ".join(kept))
    return tuple(dict.fromkeys(subjects))


def _endpoint_in_source(endpoint: str, source: _Source) -> bool:
    """Whether a source's CONTENT witnesses the endpoint a code artifact asserts.

    The full URL is the strongest witness; documentation pages more often carry the host with
    the path (``openrouter.ai/api/v1/models``) or the path alone (``/api/v1/models``) in their
    text. A bare host or a one-segment path is NOT a witness -- every page about a site names
    the site, and ``/api`` proves nothing about ``/api/v1/models``.
    """
    url = str(endpoint or "").strip()
    if not url:
        return False
    without_scheme = re.sub(r"^https?://", "", url).rstrip("/")
    parts = without_scheme.split("/", 1)
    host = parts[0].lower().removeprefix("www.")
    path = parts[1].strip("/") if len(parts) > 1 else ""
    candidates = [url, without_scheme]
    if path and "/" in path:
        candidates.append(path)
        candidates.append("/" + path)
    folded = source.folded
    if any(candidate in folded for candidate in candidates):
        return True
    if not path or "/" not in path:
        return False
    # Host + first path segment ("openrouter.ai/api") is still an address, not a name.
    return f"{host}/{path.split('/')[0]}" in folded


def _source_supports(
    anchors: ClaimAnchors, source: _Source
) -> tuple[bool, tuple[str, ...]]:
    """Does this one source structurally support this one claim?"""

    if anchors.endpoints:
        if all(_endpoint_in_source(endpoint, source) for endpoint in anchors.endpoints):
            return True, ("endpoint_match",)
        return False, ("endpoint_not_in_sources",)
    reasons: list[str] = []
    matched_numerics = set(anchors.numerics) & source.numerics
    matched_dates = {d for d in anchors.dates if str(d) in {str(s) for s in source.dates}}
    matched_quotes = {q for q in anchors.quotes if q in source.folded}
    matched_entities = {e for e in anchors.entities if _entity_in_source(e, source)}
    matched_terms = set(anchors.terms) & source.terms

    if anchors.numerics and source.numerics and not matched_numerics:
        return False, ("value_mismatch",)
    # A shared YEAR cannot carry a quantity: "37 million units by end of 2024" is not supported by a
    # page that mentions a 2024 facelift. When the claim states quantities, one of them must match.
    quantities = {value for value in anchors.numerics if not _year_like(value)}
    if quantities and source.numerics and not (quantities & source.numerics):
        return False, ("quantity_mismatch",)
    if anchors.subjects and not any(_subject_in_source(subject, source) for subject in anchors.subjects):
        return False, ("subject_absent_from_source",)
    if anchors.entities and not matched_entities:
        # The claim names something; a source that never mentions it cannot support it,
        # even when an incidental number happens to coincide.
        return False, ("entity_absent_from_source",)

    if anchors.specific:
        if matched_numerics:
            reasons.append("value_match")
        if matched_dates:
            reasons.append("date_match")
        if matched_quotes:
            reasons.append("quote_match")
        if not reasons:
            return False, ("specific_anchor_unmatched",)
        if matched_entities:
            reasons.append("entity_match")
        return True, tuple(reasons)

    if matched_entities:
        return True, ("entity_match",)
    if len(matched_terms) >= 2:
        return True, ("term_overlap",)
    return False, ("no_structural_overlap",)


def _reference_date(as_of: Any, sources: Sequence[_Source]) -> date | None:
    if as_of:
        if isinstance(as_of, date):
            return as_of
        parsed = _extract_dates(str(as_of))
        if parsed:
            return parsed[0]
    all_dates = [d for source in sources for d in source.dates]
    return max(all_dates) if all_dates else None


def match_claims(
    *,
    answer: Any,
    notes: Sequence[Any] | None,
    request_text: Any = "",
    as_of: Any = None,
) -> ClaimSupportMap:
    """Compute the per-claim support map for `answer` against the bound `notes`.

    Only bound note CONTENT participates. Receipts, provenance and anything else the turn
    carries are structurally excluded: with zero content-bearing notes the map is
    ``coverage="no_sources"`` and every claim is unsupported.
    """

    from core.evidence_binding import _STOPWORDS

    sources = _build_sources(notes)
    answer_text = str(answer or "")
    note_texts = [_note_content(note) for note in list(notes or [])]
    request_terms = _content_terms(request_text, _STOPWORDS)
    request_numerics = _extract_numerics(str(request_text or ""))
    cross_caps = _mid_sentence_capitalized([answer_text, *note_texts])
    lowercase_seen = _lowercase_seen([answer_text, *note_texts, str(request_text or "")])
    reference = _reference_date(as_of, sources)

    claims: list[ClaimSupport] = []
    for segment in segment_claims(answer_text):
        anchors = extract_claim_anchors(
            segment,
            cross_caps=cross_caps,
            lowercase_seen=lowercase_seen,
            stopwords=_STOPWORDS,
            request_terms=request_terms,
            request_numerics=request_numerics,
        )
        has_anchor = bool(anchors.entities or anchors.specific)
        is_claim = has_anchor or len(anchors.terms) >= 2
        if not is_claim:
            # Single unanchored words ("Bergen.") are adjudicable only when a source
            # witnesses them; then they are one-word claims, supported.
            single = anchors.terms[0] if len(anchors.terms) == 1 else ""
            if single and any(single in source.terms for source in sources):
                claims.append(
                    ClaimSupport(
                        claim_id=f"c{len(claims) + 1}",
                        text=segment,
                        status="supported",
                        supporting_sources=tuple(
                            source.source_id for source in sources if single in source.terms
                        ),
                        reasons=("term_match",),
                        anchors=anchors,
                    )
                )
            continue

        claim_id = f"c{len(claims) + 1}"
        supporters: list[str] = []
        support_reasons: list[str] = []
        refusal_reasons: list[str] = []
        for source in sources:
            supported, reasons = _source_supports(anchors, source)
            if not supported:
                refusal_reasons.extend(reasons)
                continue
            if anchors.currency_markers and reference is not None and source.dates:
                if (
                    not source.has_currency_marker
                    and max(source.dates) < reference - timedelta(days=CURRENT_HORIZON_DAYS)
                ):
                    refusal_reasons.append("stale_source_for_current_claim")
                    continue
            supporters.append(source.source_id)
            support_reasons.extend(reasons)

        if supporters:
            reasons = tuple(dict.fromkeys(support_reasons))
            if anchors.currency_markers and reference is None:
                reasons = (*reasons, "currency_unverified")
            claims.append(
                ClaimSupport(
                    claim_id=claim_id,
                    text=segment,
                    status="supported",
                    supporting_sources=tuple(dict.fromkeys(supporters)),
                    reasons=reasons,
                    anchors=anchors,
                )
            )
        elif has_anchor:
            claims.append(
                ClaimSupport(
                    claim_id=claim_id,
                    text=segment,
                    status="unsupported",
                    reasons=tuple(dict.fromkeys(refusal_reasons)) or ("no_source_match",),
                    anchors=anchors,
                )
            )
        else:
            # Anchor-less prose with no structural overlap: nothing to adjudicate against.
            # Uncertain, never guessed into support -- and never counted as refuted fact.
            claims.append(
                ClaimSupport(
                    claim_id=claim_id,
                    text=segment,
                    status="uncertain",
                    reasons=tuple(dict.fromkeys(refusal_reasons)) or ("no_structural_overlap",),
                    anchors=anchors,
                )
            )

    supported = [claim for claim in claims if claim.status == "supported"]
    unsupported = [claim for claim in claims if claim.status == "unsupported"]
    if not sources:
        coverage = "no_sources"
    elif not claims:
        coverage = "no_claims"
    elif supported and not unsupported:
        coverage = "full"
    elif supported:
        coverage = "partial"
    else:
        coverage = "none"

    return ClaimSupportMap(
        claims=tuple(claims),
        source_ids=tuple(source.source_id for source in sources),
        source_labels={source.source_id: source.label for source in sources},
        coverage=coverage,
    )


__all__ = [
    "CURRENT_HORIZON_DAYS",
    "ENDPOINT_CLAIM_PREFIX",
    "GENERIC_SUPPORT_TERMS",
    "ClaimAnchors",
    "ClaimSupport",
    "ClaimSupportMap",
    "code_fence_line_mask",
    "endpoint_claims_from_code",
    "extract_claim_anchors",
    "match_claims",
    "segment_claims",
]
