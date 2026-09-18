"""Deterministic disagreement extraction.

Whether two blind answers agree is decided by CODE, not by a model asking
another model "do you two agree?". Surface similarity is not agreement: two
answers can share almost every word and disagree on the one number that
matters, and two causal claims can use identical token SETS while asserting
opposite directions ("X causes Y" vs "Y causes X").

Claims are sentence-level, carrying their extracted numbers and a causal flag.
Comparison rules, all mechanical:

- aligned (token-Jaccard >= ALIGN_THRESHOLD) with equal number sets and
  (when causal) equal ordered token sequences  -> agreement;
- aligned with differing number sets           -> FACTUAL dispute;
- causal-flagged with same token set but different order -> CAUSAL dispute
  (this exact shape was measured passing naive similarity — the false-agreement
  attack this module exists to refuse);
- unaligned claim in one candidate             -> COVERAGE gap against the other.

Disputes are preserved verbatim in the receipt. Nothing downstream receives a
"summary of disagreements" — it receives the dispute objects themselves.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from core.council.claim_identity import (
    AGREE,
    UNRELATED,
    compare_semantic,
    extract_semantic_claim,
)

_ALIGN_THRESHOLD = 0.22
_NUM_RE = re.compile(r"(?<![0-9A-Za-z.])\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![0-9A-Za-z.])\d+(?:\.\d+)?")
_CAUSAL_MARKERS = ("because", "therefore", "causes", "cause", "due to", "leads to", "results in")
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


def _tokens(text: str) -> frozenset[str]:
    return frozenset(t for t in re.findall(r"[a-z0-9.,]+", text.lower()) if len(t) > 1)


def _norm_num(value: str) -> str:
    return value.replace(",", "")


def _clean_token(tok: str) -> str:
    """Strip edge punctuation only — 'cancer.' aligns with 'cancer', while the
    interior comma of '1,420' (a number) is preserved."""
    return tok.strip(".,!?:;'\"")


@dataclass(frozen=True)
class Claim:
    claim_id: str
    candidate: str          # anonymized candidate label (cand-N)
    text: str
    numbers: tuple[str, ...]   # normalized (comma-stripped)
    causal: bool
    token_order: tuple[str, ...]
    sem: "object | None" = None   # SemanticClaim (structured identity), set by extractor


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / len(a | b)


@dataclass(frozen=True)
class Dispute:
    dispute_id: str
    kind: str                    # factual | causal | coverage
    claim_a: Claim               # anonymized sides; no seat identity
    claim_b: Claim | None        # None for coverage gaps


@dataclass(frozen=True)
class Delta:
    """The mechanical disagreement extraction over the full candidate pool."""

    candidates: tuple[str, ...]                  # cand-1..N in stable order
    disputes: tuple[Dispute, ...]
    gaps: tuple[Dispute, ...]                    # kind == "coverage"

    @property
    def has_disputes(self) -> bool:
        return bool(self.disputes or self.gaps)


def extract_claims(text: str, candidate: str) -> tuple[Claim, ...]:
    claims: list[Claim] = []
    for i, raw in enumerate(_SENT_SPLIT.split(str(text or "").strip())):
        sentence = raw.strip()
        if len(sentence) < 3:
            continue
        lowered = sentence.lower()
        nums = tuple(_norm_num(m.group(0)) for m in _NUM_RE.finditer(sentence))
        order = tuple(
            t for t in (_clean_token(w) for w in re.findall(r"[a-z0-9.,]+", lowered)) if t
        )
        claims.append(
            Claim(
                claim_id=f"{candidate}#c{i}",
                candidate=candidate,
                text=sentence,
                numbers=nums,
                causal=any(m in lowered for m in _CAUSAL_MARKERS),
                token_order=order,
                sem=extract_semantic_claim(sentence),
            )
        )
    return tuple(claims)


def _dedup(disputes: list[Dispute]) -> list[Dispute]:
    seen: set[tuple[str, ...]] = set()
    out: list[Dispute] = []
    for d in disputes:
        key = (d.kind, d.claim_a.text.lower(), (d.claim_b.text.lower() if d.claim_b else ""))
        if key not in seen:
            seen.add(key)
            out.append(d)
    return out


def extract_delta(answer_texts: dict[str, str]) -> Delta:
    """``answer_texts`` maps anonymized candidate label -> R1 answer text.

    The runtime passes labels, never seat ids: extraction is where authorship
    is stripped, so every downstream phase operates on anonymous candidates.

    Alignment authority is STRUCTURED (core/council/claim_identity.py): slot
    comparison over quantities/operators/causal direction. Token-Jaccard is
    demoted to a secondary gate that only ranks WHICH claim of the other
    candidate is the best structural partner — it can no longer merge or
    split claims by itself.
    """
    labels = sorted(answer_texts)
    all_claims: dict[str, tuple[Claim, ...]] = {
        c: extract_claims(answer_texts[c], c) for c in labels
    }

    disputes: list[Dispute] = []
    gaps: list[Dispute] = []
    counter = 0

    for i, a in enumerate(labels):
        for b in labels[i + 1:]:
            for ca in all_claims[a]:
                best, best_verdict, best_kind, best_j = None, UNRELATED, "", 0.0
                for cb in all_claims[b]:
                    j = _jaccard(frozenset(ca.token_order), frozenset(cb.token_order))
                    verdict_, kind_ = compare_semantic(ca.sem, cb.sem)
                    # structural verdict dominates; jaccard only breaks ties
                    rank = {AGREE: 2, "dispute": 1, UNRELATED: 0}[verdict_]
                    brank = {AGREE: 2, "dispute": 1, UNRELATED: 0}[best_verdict]
                    if rank > brank or (rank == brank and j > best_j):
                        best, best_verdict, best_kind, best_j = cb, verdict_, kind_, j
                if best is None or best_verdict == UNRELATED:
                    gaps.append(Dispute(f"d{counter}", "coverage", ca, None))
                    counter += 1
                    continue
                if best_verdict == "dispute":
                    disputes.append(Dispute(f"d{counter}", best_kind or "factual", ca, best))
                    counter += 1

    # Dedup keeps the first-seen dispute id; id holes from dedup are harmless —
    # ids only need to be stable within one run, not dense.
    return Delta(
        candidates=tuple(labels),
        disputes=tuple(_dedup(disputes)),
        gaps=tuple(_dedup(gaps)),
    )


__all__ = ["Claim", "Delta", "Dispute", "extract_claims", "extract_delta"]
