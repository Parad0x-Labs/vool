"""Deterministic structured claim identity — the alignment authority.

Trial #1 exposed that token-Jaccard + threshold cannot be the authority for
semantic fact identity: it inverted causally-opposite sentences were caught
only by special-casing, paraphrases fell apart, and any threshold tweak traded
false agreement against false disagreement blindly.

This module replaces surface similarity as AUTHORITY with structured slot
extraction, all mechanical:

    quantities   number + unit + approximation-hedge + range shape
    polarity     affirmative vs negated
    quantifier   all / most / some / few ...
    modality     assertion / possibility / prediction / past-fact
    time         past-marked / present-marked / unmarked
    condition    conditional clause ("only when replicas >= 3")
    causal       direction pair + strength (correlation < contribution < causation)

Comparison is rule-based over these slots. Surface token overlap is DEMOTED to
two narrow gates: an entity-anchor requirement (claims must share at least one
non-stopword content token to be comparable at all) and a tie-breaker. There is
NO synonym dictionary and NO model judgment anywhere: where two cultures of
wording share no vocabulary, the matcher returns UNRELATED (a visible coverage
gap routed to voters), NEVER a silent merge. False disagreement is thus always
the failure mode, never false agreement — failing visibly beats merging wrongly.

Closed operator lexicons (negators, modal verbs, causal-strength markers,
hedges) are grammar, not semantic authority: they carry no world facts.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_NUM_RE = re.compile(r"(?<![0-9A-Za-z.])\d{1,3}(?:,\d{3})+(?:\.\d+)?(?:\s?[-–]\s?\d[\d,.]*)?"
                     r"|(?<![0-9A-Za-z.])\d+(?:\.\d+)?")
_RANGE_RE = re.compile(r"^(\d[\d,]*(?:\.\d+)?)\s?[-–]\s?(\d[\d,]*(?:\.\d+)?)$")

_UNITS = (
    "%", "percent", "$", "usd", "eur", "ms", "milliseconds", "seconds", "minutes",
    "mb", "gb", "kb", "tb", "celsius", "c", "fahrenheit", "events/sec", "rps",
    "requests/sec", "month", "monthly", "months", "year", "years", "day", "days",
    "hour", "hours", "week", "weeks", "replicas", "nodes", "brokers", "engineers",
    "points", "percentage points", "connections", "requests", "events",
)
_PRE_UNITS = ("monthly", "per month", "a month", "/month", "per year", "yearly", "per day")

_HEDGES = ("~", "approximately", "about", "around", "roughly", "nearly", "estimated",
           "estimate", "circa", "close to")

_NEGATORS = ("not", "no", "never", "cannot", "can't", "won't", "doesn't",
             "don't", "isn't", "aren't", "fails to", "unlikely")

_QUANTIFIER_MAP = (("all", "all"), ("every", "all"), ("each", "all"), ("any", "all"),
                   ("most", "most"), ("majority", "most"),
                   ("some", "some"), ("several", "some"), ("many", "many"), ("few", "few"))

_MODAL_MAP = (("did ", "past_fact"), (" was ", "past_fact"), (" were ", "past_fact"),
              (" had ", "past_fact"), ("failed", "past_fact"), ("happened", "past_fact"),
              (" will ", "prediction"), (" shall ", "prediction"), (" going to ", "prediction"),
              (" can ", "possibility"), (" could ", "possibility"), (" may ", "possibility"),
              (" might ", "possibility"), (" possibly ", "possibility"))

_PAST_MARKERS = ("yesterday", "last week", "last month", "previously", "in the past",
                 "ago", "historically")
_PRESENT_MARKERS = ("currently", "now", "today", "at present", "right now")

_CONDITION_RE = re.compile(
    r"\b(only when|only if|unless|provided that|as long as|if|when)\b[:\s]+([^.!?]{3,80})", re.I)

_CAUSAL_STRENGTH = (
    ("correlat", "correlation"), ("associated with", "correlation"),
    ("contributes to", "contribution"), ("contribute to", "contribution"),
    ("causes", "causation"), ("cause", "causation"), ("caused", "causation"),
    ("leads to", "causation"), ("results in", "causation"), ("drives", "causation"),
)
_CAUSAL_SPLIT = (" causes ", " cause ", " caused ", " leads to ", " results in ",
                 " contributes to ", " contribute to ", " due to ", " because ",
                 " drives ", " correlated with ", " correlate with ",
                 " correlates with ", " associated with ")

#: tokens that may never serve as entity anchors: units and time markers are
#: grammar, and anchoring on them merged "uptime 99%" with "CPU 99%".
_NON_ANCHOR = frozenset(_UNITS) | {
    "today", "yesterday", "tomorrow", "currently", "now", "present",
}

_STOPWORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "to", "of", "in", "at", "on",
    "and", "or", "it", "its", "this", "that", "with", "for", "by", "as", "be",
    "been", "has", "have", "had", "will", "would", "should", "than", "then",
    "from", "into", "there", "their", "they", "them", "these", "those", "his",
    "her", "she", "he", "we", "our", "you", "your", "i", "me", "my", "so", "but",
    "not", "no", "yes", "if", "when", "while", "which", "what", "who", "how",
    "why", "where", "all", "every", "each", "any", "some", "most", "many", "few",
    "very", "also", "just", "only", "about", "around", "roughly", "nearly",
    "approximately", "estimated", "like", "up", "out", "over", "under", "more",
    "less", "per", "did", "does", "do", "after", "before", "during", "since",
    "until", "when", "were", "being", "both", "each", "such", "same", "other",
    "here", "there",
})


@dataclass(frozen=True)
class SemanticClaim:
    """Structured identity of one declarative sentence."""

    text: str
    content_tokens: frozenset[str]
    quantities: tuple[tuple[str, str, bool], ...]   # (normalized_value, unit, approx)
    ranges: tuple[str, ...]
    polarity_negated: bool
    quantifier: str            # "" | all | most | some | many | few
    modality: str              # assertion | possibility | prediction | past_fact
    time_class: str            # "" | past | present
    condition: str             # "" | normalized conditional clause
    causal_pair: tuple[str, str] | None
    causal_strength: str       # "" | correlation | contribution | causation
    causal_heads: tuple[tuple[str, ...], tuple[str, ...]] | None = None


def _tokens(text: str) -> list[str]:
    return [t.strip(".,!?:;'\"()").lower() for t in text.split()]


def _content_tokens(text: str) -> frozenset[str]:
    """Content words plus hyphen/slash PARTS: 'deepseek-v4-flash' yields
    deepseek, v4, flash as additional anchors, so model-id aliases can align.
    Substring aliases (Postgres ⊂ PostgreSQL) deliberately do NOT match."""
    out: set[str] = set()
    for raw in _tokens(text):
        tok = re.sub(r"[^a-z0-9.\-/]", "", raw.lower())
        for cand in (tok, *re.split(r"[-/.]", tok)):
            if (
                len(cand) >= 3
                and cand not in _STOPWORDS
                and cand not in _NON_ANCHOR
                and not cand[0].isdigit()
            ):
                out.add(cand)
    return frozenset(out)


def _extract_quantities(text: str, lowered: str) -> tuple[tuple[tuple[str, str, bool], ...], tuple[str, ...]]:
    quantities: list[tuple[str, str, bool]] = []
    ranges: list[str] = []
    raw_toks = _tokens(text)
    for match in _NUM_RE.finditer(text):
        raw = match.group(0).strip()
        value = raw.replace(",", "").replace(" ", "")
        rng = _RANGE_RE.match(value)
        if rng:
            ranges.append(f"{rng.group(1)}-{rng.group(2)}")
            continue
        start, end = match.span()
        window = lowered[max(0, start - 30):min(len(lowered), end + 24)]
        unit = ""
        # preceding time qualifier
        before = lowered[max(0, start - 34):start]
        after = lowered[end:min(len(lowered), end + 20)]
        for pre in ("monthly", "per month", "a month", "per year"):
            if pre in before or pre in after[:10]:
                unit = "month" if "month" in pre or pre == "monthly" else "year"
                break
        if not unit:
            after_toks = [t.strip(".,()") for t in after.split()]
            before_toks = [t.strip(".,()") for t in before.split()]
            for tok in after_toks[:3]:
                if tok in _UNITS:
                    unit = {"percent": "%", "usd": "$", "milliseconds": "ms",
                            "requests/sec": "rps", "events/sec": "rps"}.get(tok, tok)
                    break
            if not unit:
                for tok in reversed(before_toks[-3:]):
                    if tok in _UNITS:
                        unit = {"percent": "%", "usd": "$", "milliseconds": "ms",
                                "requests/sec": "rps", "events/sec": "rps"}.get(tok, tok)
                        break
            if not unit and "$" in text[max(0, start - 2):start + 1]:
                unit = "$"
        approx = any(h in window for h in _HEDGES)
        quantities.append((value, unit, approx))
    # canonicalization: a month-context anywhere in the claim wins over a bare
    # currency tag ("$730" inside a monthly-cost sentence is a monthly cost)
    if any("month" in lowered for _ in (0,)) and len(quantities) <= 2:
        quantities = [
            (v, "month" if u in ("$", "") else u, ap) for (v, u, ap) in quantities
        ]
    return tuple(quantities), tuple(ranges)


def extract_semantic_claim(text: str) -> SemanticClaim:
    lowered = " " + text.lower().strip() + " "
    content = _content_tokens(text)
    quantities, ranges = _extract_quantities(text, lowered)

    negated = any((" " + n) in lowered or lowered.startswith(" " + n) for n in _NEGATORS)
    quantifier = ""
    for word, cls in _QUANTIFIER_MAP:
        if re.search(rf"\b{word}\b", lowered):
            quantifier = cls
            break
    modality = "assertion"
    for marker, cls in _MODAL_MAP:
        if marker in lowered:
            modality = cls
            break
    time_class = ""
    if any(m in lowered for m in _PAST_MARKERS):
        time_class = "past"
    elif any(m in lowered for m in _PRESENT_MARKERS):
        time_class = "present"

    condition = ""
    cond = _CONDITION_RE.search(text)
    if cond:
        condition = re.sub(r"\s+", " ", cond.group(0)).strip().lower()

    causal_pair = None
    causal_heads = None
    strength = ""
    for marker, cls in _CAUSAL_STRENGTH:
        if re.search(rf"\b\w*{marker}\w*\b", lowered) or marker in lowered:
            strength = cls
            break
    if strength:
        for split in _CAUSAL_SPLIT:
            if split in lowered:
                left = lowered.split(split)[0]
                right = lowered.split(split)[1]
                # heads = up to two content tokens ADJACENT to the marker
                lt = [t for t in _tokens(left) if t not in _STOPWORDS and len(t) >= 3]
                rt = [t for t in _tokens(right) if t not in _STOPWORDS and len(t) >= 3]
                if lt and rt:
                    causal_pair = (lt[-1], rt[0])
                    causal_heads = (
                        tuple(lt[-2:]),
                        tuple(rt[:2]),
                    )
                break

    return SemanticClaim(
        text=text,
        content_tokens=content,
        quantities=quantities,
        ranges=ranges,
        polarity_negated=negated,
        quantifier=quantifier,
        modality=modality,
        time_class=time_class,
        condition=condition,
        causal_pair=causal_pair,
        causal_strength=strength,
        causal_heads=causal_heads,
    )


# comparison verdicts --------------------------------------------------------

AGREE = "agree"
UNRELATED = "unrelated"


def compare_semantic(a: SemanticClaim, b: SemanticClaim) -> tuple[str, str]:
    """Return (verdict, dispute_kind).

    verdict ∈ {AGREE, UNRELATED, "dispute"}; kind is "" unless verdict=="dispute".
    Order of rules is deliberate: operator conflicts dominate value identity,
    which dominates surface vocabulary.
    """
    # 1. operator conflicts — these are disputes even at 100% token overlap,
    #    but ONLY between strongly co-referent claims: a polarity/modal flip on
    #    a merely adjacent pair is a topic drift, not a contradiction.
    shared = a.content_tokens & b.content_tokens

    def _heads_compatible(x: tuple[tuple[str, ...], tuple[str, ...]],
                          y: tuple[tuple[str, ...], tuple[str, ...]]) -> bool:
        """Same causal direction under partial head overlap (paraphrase heads)."""
        return (set(x[0]) & set(y[0]) or x[0][-1] == y[0][-1]) and \
               (set(x[1]) & set(y[1]) or x[1][0] == y[1][0])

    if a.causal_heads and b.causal_heads:
        if (a.causal_strength or "causation") != (b.causal_strength or "causation"):
            return "dispute", "causal_strength"
        ha, hb = a.causal_heads, b.causal_heads
        if _heads_compatible(ha, hb) and _heads_compatible(hb, ha):
            pass  # same direction; fall through to value/polarity checks
        elif _heads_compatible(ha, (hb[1], hb[0])):
            return "dispute", "causal_inversion"
        else:
            return "dispute", "causal"
    strong_pair = len(shared) >= 2
    if a.polarity_negated != b.polarity_negated:
        if strong_pair or (shared and a.quantities and b.quantities):
            return "dispute", "negation"
        # weakly related and differently signed: topic drift, not contradiction
        return UNRELATED, ""
    if a.quantifier and b.quantifier and a.quantifier != b.quantifier and strong_pair:
        return "dispute", "quantifier"
    ma, mb = a.modality, b.modality
    if ma != mb and "assertion" not in (ma, mb) and strong_pair:
        return "dispute", "modality"
    if a.time_class and b.time_class and a.time_class != b.time_class:
        return "dispute", "temporal"
    ca, cb = a.condition, b.condition
    if bool(ca) != bool(cb) or (ca and cb and ca != cb):
        return "dispute", "conditional_scope"

    anchored = bool(shared)
    if not anchored:
        return UNRELATED, ""
    # SINGLE-ANCHOR GUARD (trial-3 residual pin): when the ONLY shared token is
    # one word, two claims about different attributes of that same subject
    # ("server latency is 45 ms" vs "server timeout is 45 ms") would otherwise
    # merge silently on equal values. One shared word is not shared meaning.
    # Trade-off (deliberate): number-anchored paraphrases whose ONLY link is a
    # single entity token now land as UNRELATED coverage gaps instead of merges.
    # Failing visibly beats merging wrongly; there is no dictionary to rescue them.
    if len(shared) == 1 and len(a.content_tokens) > 1 and len(b.content_tokens) > 1:
        return UNRELATED, ""

    # 2. quantity identity dominates surface wording (paraphrase equivalence),
    #    compared PER UNIT with subset tolerance: a sub-claim citing two of the
    #    three figures of a fuller sentence agrees with it; genuinely
    #    conflicting figures in a shared unit are a dispute.
    def _buckets(quantities) -> dict[str, set[tuple[str, bool]]]:
        out: dict[str, set[tuple[str, bool]]] = {}
        for value, unit, approx in quantities:
            out.setdefault(unit, set()).add((value, approx))
        return out

    ba, bb = _buckets(a.quantities), _buckets(b.quantities)
    if a.ranges != b.ranges:
        return "dispute", "value_mismatch"
    if ba or bb:
        common_units = set(ba) & set(bb)
        if common_units:
            for unit in common_units:
                va, vb = ba[unit], bb[unit]
                if va != vb and not (va <= vb or vb <= va):
                    return "dispute", "value_mismatch"
        elif ba and bb and not (set(map(lambda t: t[0], a.quantities)) & set(map(lambda t: t[0], b.quantities))):
            # both quantify but about different measures -> different facts
            return UNRELATED, ""

    return AGREE, ""


__all__ = [
    "AGREE",
    "SemanticClaim",
    "UNRELATED",
    "compare_semantic",
    "extract_semantic_claim",
]
