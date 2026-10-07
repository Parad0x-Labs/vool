"""Claim binder: the reply's value claims bound to the memory evidence the reader saw.

Why (measured 2026-10-07 on a held-out memory run): 19 correct answers to the user's own purchase questions ("$283 total: cycling
shoes $140, bike lights $48, helmet $95") were replaced by "I could not obtain a current reading" because the
current-claim guard treats a dollar figure as a live price unless the turn observed the world. The records in the
prompt stated every operand; the guard never read them for that kind. This module reads them.

Contract: the binder labels, it never rewrites prose. For every value the reply asserts it
returns a ClaimBinding: stated (the value appears in a user-owned evidence line), derived (sum, average, extremum,
difference or count over user-owned values in the evidence, with the derivation shown), or unsupported. A reply whose
every value claim is stated or derived is memory-sourced; the current-claim guard then has no standing to withdraw it.
A reply with any unsupported value is left to the guard as before.

Scope guard, deliberately narrow: binding is attempted only for a question about the user's own records (first-person
ask with no live-world vocabulary), and only against USER-owned evidence lines ("user said" capsule lines, user receipt
facts); an assistant line, a web note or a stored market reading never sources a claim. A live price with no memory
behind it, and a "current" ask over a stale stored reading of a volatile quantity, therefore still go to the guard.

Record level (after earlier-record guard tests caught 19 false keeps): a value is STATED only by a record that is the user's
own (no foreign possessor, not a quotation), not negated or refused in its clause, whose clause carries the question's
subject terms as fully as any other record does, and that is the CURRENT record of that subject (latest statement
date; on one date a correction marker supersedes a plain statement; two conflicting markers on one date leave no
current record). A "current/now" ask over a subject with no record vocabulary (highest, longest, ...) is the
live-reading guard's case and is not attempted here. Derived values (sum, average, count, extremum, difference) draw
on every owned, non-negated, non-quoted record; the exact derivation is what binds them.
"""
from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

_MONEY_RE = re.compile(r"(?:(?P<cur>[$€£])\s?(?P<v>\d[\d,]*(?:\.\d+)?)|(?P<v2>\d[\d,]*(?:\.\d+)?)\s*(?P<cur2>dollars|euros|pounds|usd|eur|gbp|bucks))", re.IGNORECASE)
_NUMBER_RE = re.compile(r"(?<![\d.,$€£])(?P<v>\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(?![\d,]*\s*(?:am|pm|:))")
_LIST_MARKER_RE = re.compile(r"(?m)^\s*(?:\d{1,2}[.)]|[-*•])\s+")
_NUMBER_WORDS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
                 "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
                 "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50}
_NUMBER_WORD_RE = re.compile(r"\b(" + "|".join(_NUMBER_WORDS) + r")\b", re.IGNORECASE)
_USER_LINE_RE = re.compile(r"^\s*-\s*(?:\[(?P<day>\d{4}-\d{2}-\d{2})\]\s*)?user said(?:\s*\([^)]*\))?:\s*(?P<body>.*)$", re.IGNORECASE)
_FIRST_PERSON_RE = re.compile(r"\b(?:i|i've|i'd|i'm|my|me|mine|we|our|we've)\b", re.IGNORECASE)
_LIVE_WORLD_RE = re.compile(r"\b(?:exchange rate|stock|share price|bitcoin|btc|eth\b|ethereum|crypto|gold price|silver price|oil price|market price|the market|weather|temperature|forecast|right now outside|today's rate|quote for|live price|spot price)\b", re.IGNORECASE)
_FIRST_PERSON_OR_ADVICE_RE = re.compile(r"\b(?:i|i've|i'd|i'm|my|me|mine|we|our|we've|suggest|recommend|should i|ideas?|tips?)\b", re.IGNORECASE)
_ISO_DAY_RE = re.compile(r"\b((?:19|20)\d{2})-(\d{2})-(\d{2})\b")
_DATE_LIKE_RE = re.compile(r"\b(?:19|20)\d{2}(?:-\d{2}-\d{2})?\b|\b\d{1,2}(?:st|nd|rd|th)\b|\b\d{1,2}[:.]\d{2}\b|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b")
_ASSISTANT_ASK_RE = re.compile(r"\byou\s+(?:said|told|mentioned|recommended|suggested|gave|listed|advised|wrote|shared|proposed)\b|\b(?:your|the)\s+(?:earlier\s+|previous\s+)?(?:reply|answer|suggestion|recommendation|list)\b|\bdid\s+you\s+(?:say|tell|mention|recommend|suggest|give|list)\b", re.IGNORECASE)
_ASSISTANT_LINE_RE = re.compile(r"^\s*-\s*\[?(?:\d{4}-\d{2}-\d{2}\]\s*)?assistant (?:said|listed)(?:\s*\([^)]*\))?:\s*(?P<body>.*)$", re.IGNORECASE)
_STATED_SUFFIX_RE = re.compile(r"\(stated:[^)]*?(?P<d>\d{4}[-/]\d{2}[-/]\d{2})[^)]*\)?\s*$")
_CURRENT_FRAME_RE = re.compile(r"\b(?:current(?:ly)?|right\s+now|at\s+the\s+moment|as\s+of\s+(?:now|today)|these\s+days|nowadays)\b|\bnow\b", re.IGNORECASE)
_RECORD_MODIFIER_FALLBACK_RE = re.compile(r"\b(?:highest|lowest|best|worst|most|fewest|least|longest|shortest|largest|smallest|biggest|greatest|quickest|fastest|slowest|heaviest|lightest|top|record|personal\s+best|maximum|minimum)\b|\b(?!interest\b|earnest\b|request\b|harvest\b|contest\b|honest\b|forest\b|priest\b)[a-z]{3,}est\b", re.IGNORECASE)
_WHOLE_TURN_QUOTE_RE = re.compile(r"[\"“](?P<inner>.*?)[\"”](?P<tail>(?:\s|<[^>]*>|\([^)]*\)|\[[^\]]*\])*)", re.DOTALL)
_QUOTE_RE = re.compile(r"\"[^\"]+\"|“[^”]+”|(?<![A-Za-z0-9])'[^']{3,}?'(?![A-Za-z0-9])|‘[^’]+’|\b(?:says?|said|quotes?|writes?|wrote|reads?|states?|claims?)\s*:\s*.+$", re.IGNORECASE)
# the single-quote arm needs a quote mark that is not inside a word on either side: the apostrophes of "I've" and
# "I'm" in one sentence are not a quotation (measured 2026-10-07: a $79 record read as quoted, a correct average withdrawn)
_CLAUSE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z])|;\s+|,\s+(?=(?:my|not|but|and|it|the|while|whereas)\b)|\s+(?:but|whereas|while)\s+|\s+and\s+(?=my\b)", re.IGNORECASE)
_NEGATION_BEFORE_RE = re.compile(r"\b(?:not|isn['’ʼ]?t|wasn['’ʼ]?t|aren['’ʼ]?t|weren['’ʼ]?t|no\s+longer|never|rather\s+than|instead\s+of)\s*(?:\w+\s+){0,2}$", re.IGNORECASE)
_MISTAKE_RE = re.compile(r"\b(?:mistake|error|typo|wrong|incorrect|misread|mis-?typed|scratch\s+that|ignore\s+that)\b", re.IGNORECASE)
_CORRECTION_RE = re.compile(r"^\s*(?:correction|revised|revision|update|updated|actually|edit)\b|\b(?:is|are|was)\s+now\b|\bnow\s+(?:is|at|stands)\b|\bactually\b", re.IGNORECASE)
_FOREIGN_POSSESSOR_RE = re.compile(r"\b(?:[a-z]+['’ʼ]s|his|her|their|your|(?:my\s+)?(?:neighbou?rs?|mentors?|friends?|coach|teachers?|co-?workers?|colleagues?|partners?|brothers?|sisters?|parents?|mothers?|fathers?|cousins?|boss)|the\s+(?:neighbou?r|mentor|friend|coach|teacher|magazine|catalogue|leaflet|manual|book)['’ʼ]?s?)\s+(?:\w+\s+){0,4}$", re.IGNORECASE)
_PAST_TENSE_RE = re.compile(r"\b(?:held|was|were|had|used\s+to|reached|hit|measured|weighed|stood\s+at)\s+(?:\w+\s+){0,2}$", re.IGNORECASE)
_STOP = {"what", "which", "who", "how", "much", "many", "long", "is", "are", "was", "were", "the", "a", "an", "my", "me", "i", "of", "in", "on", "at", "for", "to", "did", "do", "does",
         "have", "has", "had", "current", "currently", "now", "far", "so", "and", "or", "with", "from", "by", "this", "that", "it", "its", "your", "you", "we", "our", "today", "year",
         "spend", "spent", "pay", "paid", "cost", "buy", "bought", "together", "total", "all", "most", "least", "expensive", "cheap", "cheapest", "between", "about", "there", "been",
         "got", "get", "picked", "purchased", "went", "said", "just", "recently", "also", "even", "week", "today", "yesterday", "new", "some", "then", "ago", "last", "finally",
         "way", "really", "pretty", "quite", "very", "more", "after", "before", "when", "where", "since", "into", "out", "up", "down", "off", "over", "still", "yet", "too", "again",
         "items", "item", "things", "thing", "stuff", "gear", "one", "ones", "which", "first", "second", "third", "earlier", "later", "ever", "far", "much", "many"}


@dataclass
class EvidenceRecord:
    """One value as one clause of one evidence line states it, with everything that decides whether it can source a claim."""
    value: float
    kind: str                 # money | number
    clause: str
    line: str
    stated: str               # ISO day or ""
    order: int
    owned: bool
    quoted: bool
    negated: bool
    corrected: bool
    past_tense: bool
    terms: set[str]
    head: set[str] = field(default_factory=set)   # the content words right around the value: the thing it measures

    @property
    def usable(self) -> bool:
        return self.owned and not self.quoted and not self.negated

    def as_dict(self) -> dict[str, Any]:
        return {"value": self.value, "clause": self.clause[:160], "stated": self.stated, "owned": self.owned, "quoted": self.quoted,
                "negated": self.negated, "corrected": self.corrected, "past_tense": self.past_tense}


_UNIT_WORDS = {"metres", "meters", "metre", "meter", "centimetres", "centimeters", "kilometres", "kilometers", "kilograms", "kg", "grams", "points", "seconds", "minutes",
               "hours", "days", "weeks", "months", "years", "dollars", "euros", "pounds", "litres", "liters", "miles", "feet", "inches", "degrees", "percent", "now", "not", "isnt", "its"}


def _terms(text: str) -> set[str]:
    """Subject terms: content words, without stop words and without unit words (a unit names no subject)."""
    return {t for t in re.findall(r"[a-z][a-z-]+", str(text or "").lower()) if len(t) > 2 and t not in _STOP and t not in _UNIT_WORDS}


def _values_in(text: str) -> list[tuple[str, float, int, int]]:
    out: list[tuple[str, float, int, int]] = []
    spans = [m.span() for m in _LIST_MARKER_RE.finditer(text)]
    for m in _MONEY_RE.finditer(text):
        v = _num(m.group("v") or m.group("v2"))
        if v is not None:
            out.append(("money", v, m.start(), m.end())); spans.append(m.span())
    for m in _DATE_LIKE_RE.finditer(text):
        spans.append(m.span())
    for m in _NUMBER_RE.finditer(text):
        if any(a <= m.start() < b for a, b in spans):
            continue
        v = _num(m.group("v"))
        if v is not None:
            out.append(("number", v, m.start(), m.end()))
    for m in _NUMBER_WORD_RE.finditer(text):
        out.append(("number", float(_NUMBER_WORDS[m.group(1).lower()]), m.start(), m.end()))
    return out


def evidence_records(lines: Sequence[str], *, owner: str = "user") -> list[EvidenceRecord]:
    """Every value in the evidence lines as a record with its clause, ownership, quotation, negation, correction
    marker, tense, statement date and subject terms."""
    records: list[EvidenceRecord] = []
    for order, raw in enumerate(lines):
        line = str(raw or "").strip()
        sm = _STATED_SUFFIX_RE.search(line)
        stated = sm.group("d").replace("/", "-") if sm else ""
        body = line[: sm.start()].strip() if sm else line
        wm = _WHOLE_TURN_QUOTE_RE.fullmatch(body.strip())
        if wm and '"' not in wm.group("inner") and "“" not in wm.group("inner"):
            body = wm.group("inner") + " " + (wm.group("tail") or "")   # the capsule renders a whole turn in quotes, then its annotations; not a quotation the user made
        quoted_spans = [m.span() for m in _QUOTE_RE.finditer(body)]
        line_corrected = bool(_CORRECTION_RE.match(body))
        pos = 0
        pieces: list[tuple[int, str]] = []
        for m in _CLAUSE_SPLIT_RE.finditer(body):
            pieces.append((pos, body[pos:m.start()])); pos = m.end()
        pieces.append((pos, body[pos:]))
        # a comma fragment is a clause of its own only when it carries a value of its own ("widest oak board 31cm,
        # widest ash board 44cm"); otherwise it stays with the clause before it
        split: list[tuple[int, str]] = []
        for start, piece in pieces:
            sub_pos = 0
            for m in re.finditer(r",\s+", piece):
                head, tail = piece[sub_pos:m.start()], piece[m.end():]
                if _values_in(head) and _values_in(tail):
                    split.append((start + sub_pos, head)); sub_pos = m.end()
            split.append((start + sub_pos, piece[sub_pos:]))
        pieces = split
        prev_terms: set[str] = set()
        for start, clause in pieces:
            if not clause.strip():
                continue
            mistake = bool(_MISTAKE_RE.search(clause))
            for kind, value, a, b in _values_in(clause):
                before = clause[:a]
                abs_a = start + a
                quoted = any(qa <= abs_a < qb for qa, qb in quoted_spans)
                negated = bool(_NEGATION_BEFORE_RE.search(before)) or mistake
                foreign = bool(_FOREIGN_POSSESSOR_RE.search(before))
                owned = (owner == "assistant") or not foreign   # the line is the user's own turn; only a foreign possessor (or a quotation) un-owns a value
                corrected = line_corrected or bool(_CORRECTION_RE.search(before))
                past = bool(_PAST_TENSE_RE.search(before)) and not re.search(r"\b(?:is|are|am)\b", before, re.IGNORECASE)
                terms = _terms(clause) - _terms(clause[a:b])
                if re.match(r"\s*(?:it|that|this|which|now|and|but|so)\b", clause, re.IGNORECASE) or not terms:
                    terms |= prev_terms   # "..., it is 46 metres" is about the clause before it
                head = _terms(" ".join(re.findall(r"[A-Za-z][A-Za-z'-]*", clause[:a])[-6:] + re.findall(r"[A-Za-z][A-Za-z'-]*", clause[b:])[:2])) - _terms(clause[a:b])
                records.append(EvidenceRecord(value, kind, clause.strip(), line, stated, order, owned, quoted, negated, corrected, past, terms, head or set(terms)))
            clause_terms = _terms(clause)
            if clause_terms:
                prev_terms = clause_terms
    return records


def subject_eligible(records: Sequence[EvidenceRecord], question: str, reply: str = "") -> list[EvidenceRecord]:
    """The records whose clause carries the subject the reply and the question name as fully as any record does (a
    red sluice channel is not sourced by the blue one; a tunnel route is not sourced by the ridge walk in the same
    sentence; "the flash unit, $190" is sourced by the flash unit's record, not the camera bag's)."""
    q_terms = _terms(question) | _terms(reply)
    present = {t for r in records for t in r.terms} & q_terms
    if not present:
        return list(records)
    best = max(len(r.terms & present) for r in records)
    return [r for r in records if len(r.terms & present) == best] if best > 0 else list(records)


def same_subject(record: EvidenceRecord, others: Sequence[EvidenceRecord]) -> list[EvidenceRecord]:
    """The records about the same thing as *record*, read on the words right around the value (its head): two
    shared head terms, or every term of a one-word head. "a bike helmet for $95" and "a bike computer for $165" share
    one head word and are two purchases; "my longest swim is 1800 m" and "my longest swim is now 2400 m" are one record."""
    def same(a: set[str], b: set[str]) -> bool:
        if not a or not b:
            return not a and not b
        shared = len(a & b)
        return shared >= min(2, len(a), len(b))
    return [r for r in others if r is record or same(r.head, record.head)]


def current_record(cands: Sequence[EvidenceRecord], *, current_ask: bool = True) -> tuple[EvidenceRecord | None, str]:
    """The current record among same-subject usable records: the latest statement date; on one date a correction
    marker beats a plain statement; two different values still tied means no current record. A past-tense record
    ("my forest held 51 kg") is not a current one, which matters only when the ask is about now."""
    usable = [r for r in cands if r.usable and (not current_ask or not r.past_tense)]
    if not usable:
        return None, "no_usable_record"
    latest = max(r.stated for r in usable)
    tier = [r for r in usable if r.stated == latest]
    if len({r.value for r in tier}) > 1 and any(r.corrected for r in tier):
        tier = [r for r in tier if r.corrected]
    if len({r.value for r in tier}) > 1:
        return None, "conflicting_records_on_the_latest_date"
    return max(tier, key=lambda r: r.order), "current"


def assistant_lines(evidence_text: Any, packet_facts: Sequence[Mapping[str, Any]] | None = None) -> list[str]:
    lines: list[str] = []
    for raw in str(evidence_text or "").splitlines():
        m = _ASSISTANT_LINE_RE.match(raw)
        if m:
            lines.append(m.group("body").strip())
    for fact in packet_facts or []:
        if str(fact.get("role") or "") == "assistant" and fact.get("sentence"):
            lines.append(str(fact["sentence"]))
    return lines


@dataclass
class ClaimBinding:
    value: float
    text: str
    kind: str                 # money | count | number
    status: str               # stated | derived | unsupported
    evidence: list[str] = field(default_factory=list)   # the evidence lines (clipped) the claim binds to
    derivation: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"value": self.value, "text": self.text, "kind": self.kind, "status": self.status, "evidence": list(self.evidence), "derivation": self.derivation}


@dataclass
class BindingResult:
    attempted: bool
    reason: str
    claims: list[ClaimBinding] = field(default_factory=list)

    @property
    def all_supported(self) -> bool:
        return self.attempted and bool(self.claims) and all(c.status != "unsupported" for c in self.claims)

    def as_dict(self) -> dict[str, Any]:
        return {"attempted": self.attempted, "reason": self.reason, "all_supported": self.all_supported, "claims": [c.as_dict() for c in self.claims]}


def _num(text: str) -> float | None:
    try:
        return float(str(text).replace(",", ""))
    except ValueError:
        return None


def user_owned_lines(evidence_text: Any, packet_facts: Sequence[Mapping[str, Any]] | None = None) -> list[str]:
    """The evidence lines the user authored: capsule and lane "user said" lines, and user receipt facts."""
    lines: list[str] = []
    in_user = False
    for raw in str(evidence_text or "").splitlines():
        m = _USER_LINE_RE.match(raw)
        if m:
            body = m.group("body").strip()
            if m.group("day") and not _STATED_SUFFIX_RE.search(body):
                body += f" (stated: {m.group('day')})"   # the capsule's own statement day, kept for the record law
            lines.append(body); in_user = True
            continue
        if raw.startswith("- ") or raw.startswith("<") or raw.startswith("Evidence ") or raw.startswith("Distilled "):
            in_user = False
            continue
        if in_user and raw.strip() and lines:
            sm = _STATED_SUFFIX_RE.search(lines[-1])
            cont = " " + raw.strip()
            lines[-1] = (lines[-1][: sm.start()].rstrip() + cont + " " + sm.group(0)) if sm else lines[-1] + cont  # continuation stays before the suffix
    for fact in packet_facts or []:
        if str(fact.get("role") or "") == "user" and fact.get("sentence"):
            lines.append(str(fact["sentence"]) + _fact_stated_suffix(fact))
    return lines


def _fact_stated_suffix(fact: Mapping[str, Any]) -> str:
    try:
        from datetime import datetime, timezone

        return " (stated: " + datetime.fromtimestamp(float(fact["statement_at"]), tz=timezone.utc).date().isoformat() + ")" if fact.get("statement_at") is not None else ""
    except Exception:
        return ""


def reply_value_claims(reply: Any) -> list[tuple[str, float, str]]:
    """(kind, value, text) for every value the reply asserts: money amounts, then bare numbers and number words that
    are not part of a money amount, a date, a clock time or an ordinal."""
    body = str(reply or "")
    claims: list[tuple[str, float, str]] = []
    spans: list[tuple[int, int]] = [m.span() for m in _LIST_MARKER_RE.finditer(body)]  # "1." list markers are not claims
    for m in _MONEY_RE.finditer(body):
        v = _num(m.group("v") or m.group("v2"))
        if v is not None:
            claims.append(("money", v, m.group(0))); spans.append(m.span())
    for m in _DATE_LIKE_RE.finditer(body):
        spans.append(m.span())
    for m in _NUMBER_RE.finditer(body):
        if any(a <= m.start() < b for a, b in spans):
            continue
        v = _num(m.group("v"))
        if v is not None:
            claims.append(("number", v, body[m.start():m.end() + 8]))
    for m in _NUMBER_WORD_RE.finditer(body):
        claims.append(("count", float(_NUMBER_WORDS[m.group(1).lower()]), body[m.start():m.end() + 8]))
    return claims


def evidence_values(lines: Sequence[str]) -> tuple[list[tuple[float, str]], list[tuple[float, str]]]:
    """(money values, other numbers) stated in user-owned evidence lines, each with its line."""
    money: list[tuple[float, str]] = []; numbers: list[tuple[float, str]] = []
    for line in lines:
        spans = [m.span() for m in _LIST_MARKER_RE.finditer(line)]
        for m in _MONEY_RE.finditer(line):
            v = _num(m.group("v") or m.group("v2"))
            if v is not None:
                money.append((v, line)); spans.append(m.span())
        for m in _DATE_LIKE_RE.finditer(line):
            spans.append(m.span())
        for m in _NUMBER_RE.finditer(line):
            if any(a <= m.start() < b for a, b in spans):
                continue
            v = _num(m.group("v"))
            if v is not None:
                numbers.append((v, line))
        for m in _NUMBER_WORD_RE.finditer(line):
            numbers.append((float(_NUMBER_WORDS[m.group(1).lower()]), line))
    return money, numbers


def _record_modifier_re():
    try:
        from core.unsourced_current_claim import _RECORD_MODIFIER_RE

        return _RECORD_MODIFIER_RE
    except Exception:
        return _RECORD_MODIFIER_FALLBACK_RE


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(0.01, 0.005 * abs(b))


def _derive(value: float, pool: Sequence[tuple[float, str]], *, max_items: int = 8) -> dict[str, Any] | None:
    """A derivation of *value* from the pool: a stated value, an extremum, a sum or average of a subset (sizes 2..n
    over at most max_items distinct values), a count of distinct values, or a difference of two."""
    distinct: list[tuple[float, str]] = []
    seen: set[float] = set()
    for v, line in pool:
        if v not in seen:
            seen.add(v); distinct.append((v, line))
    for v, line in distinct:
        if _close(value, v):
            return {"op": "stated", "args": [v], "lines": [line]}
    if not distinct:
        return None
    vals = distinct[:max_items]
    if _close(value, max(v for v, _ in vals)) or _close(value, min(v for v, _ in vals)):
        v = max(vals, key=lambda x: x[0]) if _close(value, max(v for v, _ in vals)) else min(vals, key=lambda x: x[0])
        return {"op": "extremum", "args": [x[0] for x in vals], "result": v[0], "lines": [v[1]]}
    if _close(value, float(len(vals))):
        return {"op": "count", "args": [x[0] for x in vals], "result": len(vals), "lines": [x[1] for x in vals]}
    for k in range(2, len(vals) + 1):
        for combo in itertools.combinations(vals, k):
            s = sum(x[0] for x in combo)
            if _close(value, s):
                return {"op": "sum", "args": [x[0] for x in combo], "result": s, "lines": [x[1] for x in combo]}
            if _close(value, s / k):
                return {"op": "average", "args": [x[0] for x in combo], "result": round(s / k, 2), "lines": [x[1] for x in combo]}
    for a, b in itertools.permutations(vals, 2):
        if _close(value, a[0] - b[0]) and a[0] > b[0]:
            return {"op": "difference", "args": [a[0], b[0]], "result": a[0] - b[0], "lines": [a[1], b[1]]}
    return None


def _evidence_days(evidence_text: Any, packet_facts: Sequence[Mapping[str, Any]] | None) -> list[tuple[str, str]]:
    """ISO days the evidence states (event days, statement days, the reference day), each with its line."""
    from datetime import datetime, timezone

    out: list[tuple[str, str]] = []
    for raw in str(evidence_text or "").splitlines():
        for m in _ISO_DAY_RE.finditer(raw):
            out.append((m.group(0), raw.strip()[:200]))
    for fact in packet_facts or []:
        if fact.get("event_at") is not None:
            try:
                out.append((datetime.fromtimestamp(float(fact["event_at"]), tz=timezone.utc).date().isoformat(), str(fact.get("sentence") or "")[:200]))
            except Exception:
                pass
    seen = set(); dedup = []
    for d, line in out:
        if d not in seen:
            seen.add(d); dedup.append((d, line))
    return dedup[:40]


def _date_derivation(value: float, text: str, days: Sequence[tuple[str, str]]) -> dict[str, Any] | None:
    """*value* as a day or week count between two stated days (the unit is read from the claim's own text)."""
    from datetime import date

    unit_weeks = bool(re.search(r"\bweeks?\b", text, re.IGNORECASE))
    parsed = []
    for d, line in days:
        try:
            parsed.append((date.fromisoformat(d), line))
        except ValueError:
            continue
    for i, (a, la) in enumerate(parsed):
        for b, lb in parsed[i + 1:]:
            diff = abs((a - b).days)
            if diff == 0:
                continue
            if unit_weeks and abs(diff / 7.0 - value) <= 0.5:
                return {"op": "weeks_between", "args": [a.isoformat(), b.isoformat()], "result": round(diff / 7.0, 1), "lines": [la, lb]}
            if abs(diff - value) <= 1:
                return {"op": "days_between", "args": [a.isoformat(), b.isoformat()], "result": diff, "lines": [la, lb]}
    return None


def bind_claims(*, question: Any, reply: Any, evidence_text: Any, packet_facts: Sequence[Mapping[str, Any]] | None = None) -> BindingResult:
    """Bind every value claim of *reply* to user-owned memory evidence. See the module docstring for the scope."""
    q = str(question or "")
    asks_assistant = bool(_ASSISTANT_ASK_RE.search(q))
    if not asks_assistant and not _FIRST_PERSON_OR_ADVICE_RE.search(q):
        return BindingResult(False, "question is not about the user's own records")
    if _LIVE_WORLD_RE.search(q) and not asks_assistant:
        # "what did you say the oven temperature was" asks for the assistant's own earlier words, a record, not the world
        return BindingResult(False, "question asks about a live-world quantity")
    if _CURRENT_FRAME_RE.search(q) and not _record_modifier_re().search(q) and not asks_assistant:
        # "my honest estimate currently", "my forest mass currently": a current value of a quantity that is not a
        # record (no superlative) is a live reading unless the turn observed it; the guard's law, not a memory bind
        return BindingResult(False, "current ask without record vocabulary: the live-reading law decides")
    lines = assistant_lines(evidence_text, packet_facts) if asks_assistant else user_owned_lines(evidence_text, packet_facts)
    if not lines:
        return BindingResult(False, "no assistant evidence line in the prompt" if asks_assistant else "no user-owned evidence line in the prompt")
    claims = reply_value_claims(reply)
    if not claims:
        return BindingResult(False, "reply asserts no value")
    records = evidence_records(lines, owner="assistant" if asks_assistant else "user")
    usable = [r for r in records if r.usable]
    money = [(r.value, r.line) for r in usable if r.kind == "money"]
    numbers = [(r.value, r.line) for r in usable if r.kind != "money"]
    days = _evidence_days(evidence_text, packet_facts)
    out: list[ClaimBinding] = []
    for kind, value, text in claims:
        # stated: the current record of the question's subject states exactly this value
        same_value = [r for r in records if _close(value, r.value) and (kind != "money" or r.kind == "money")]
        d = None; refused = None
        if same_value:
            typed = [r for r in records if (kind != "money" or r.kind == "money")]
            # The subject discriminator is for a single-value answer: "my current longest tunnel route is 26 km"
            # must not be sourced by the ridge walk. A reply that lists several values ("$283: shoes $140, lights
            # $48, helmet $95") names its records itself; each listed value binds to the usable record stating it.
            eligible = subject_eligible(typed, q, str(reply or "")) if len(claims) == 1 else typed
            cur, why = None, "no_usable_record"
            for r in same_value:
                if r not in eligible:
                    why = "record_is_not_about_the_asked_subject"; continue
                c, w = current_record(same_subject(r, eligible), current_ask=bool(_CURRENT_FRAME_RE.search(q)))
                if c is not None and _close(value, c.value):
                    cur, why = c, w; break
                why = w if c is None else "superseded_by_current_record"
            if cur is not None and _close(value, cur.value):
                d = {"op": "stated", "args": [cur.value], "lines": [cur.line], "record": cur.as_dict()}
            else:
                refused = {"op": "refused_record", "reason": why if cur is None else "superseded_by_current_record",
                           "current": cur.as_dict() if cur is not None else None, "records": [r.as_dict() for r in same_value][:4]}
        pool = money if kind == "money" else (numbers + money)
        if d is None:
            d = _derive(value, pool)
            if d is not None and d["op"] == "stated":
                d = None  # a stated match the record law refused above is not rescued by the pool
        if d is None and kind != "money" and days:
            d = _date_derivation(value, text, days)
        if d is None and kind != "money":
            # a count may be the number of user-owned lines that each state a money amount (three items bought)
            if _close(value, float(len({line for _v, line in money}))) and money:
                d = {"op": "count_of_stated_items", "args": [], "result": len({line for _v, line in money}), "lines": sorted({line for _v, line in money})}
        if d is None:
            out.append(ClaimBinding(value, text, kind, "unsupported", [], refused))
        else:
            out.append(ClaimBinding(value, text, kind, "stated" if d["op"] == "stated" else "derived", [l[:200] for l in d.get("lines", [])][:6], d))
    return BindingResult(True, "bound", out)
