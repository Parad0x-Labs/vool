"""SLM-MUX: make a small local model punch like a big one, free, on your own hardware.

Instead of trusting one small model's single answer on a hard prompt, run a couple of the
models you already have, sample each a few times, and pick the answer from the model that is
most *self-consistent* (its samples agree with each other). A model that keeps landing on the
same answer is far more likely to be right than one whose samples scatter -- so confidence-
selection across 2 small models can match a model an order of magnitude larger, with no GPU
upgrade and nothing leaving the box.

Based on the SLM-MUX result (arXiv 2510.05077): two 7-8B models selected by self-consistency
confidence beat a 72B on GPQA/GSM8K. Crucially it is *selection*, NOT debate -- multi-agent
debate causes groupthink and lowers small-model accuracy, so we never let the models talk.

This module is the pure, deterministic selection core. It takes a `sampler(model, prompt) ->
answer` callable so it is fully testable without a live model, and so the caller controls
temperature, timeouts, and which models participate.
"""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

_WS = re.compile(r"\s+")
_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")
_FINAL_NUM = re.compile(r"-?\d[\d,]*\.?\d*")


@dataclass(frozen=True)
class MuxCandidate:
    model: str
    answer: str  # a full representative sample from this model's majority cluster
    confidence: float  # self-consistency: fraction of this model's samples in the majority cluster
    agree_count: int
    sample_count: int
    samples: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class MuxResult:
    answer: str
    model: str
    confidence: float
    candidates: tuple[MuxCandidate, ...]
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "answer": self.answer,
            "model": self.model,
            "confidence": round(self.confidence, 4),
            "reason": self.reason,
            "candidates": [
                {"model": c.model, "confidence": round(c.confidence, 4),
                 "agree": c.agree_count, "samples": c.sample_count}
                for c in self.candidates
            ],
        }


def default_answer_key(text: str) -> str:
    """Cluster key for grouping samples that 'say the same thing'.

    If the sample ends in a concrete numeric answer (math/counting -- where small models most
    often disagree), cluster on that number. Otherwise cluster on the normalized final line,
    which is where a short factual answer lands.
    """
    raw = str(text or "").strip()
    if not raw:
        return ""
    nums = _FINAL_NUM.findall(raw)
    if nums:
        return nums[-1].replace(",", "").rstrip(".")
    last_line = raw.splitlines()[-1].lower()
    return _WS.sub(" ", _NON_ALNUM.sub(" ", last_line)).strip()


def _majority(samples: Sequence[str], answer_key: Callable[[str], str]) -> tuple[str, float, int]:
    """Return (representative full sample, agreement fraction, agree_count) for the largest cluster."""
    valid = [s for s in samples if str(s or "").strip()]
    if not valid:
        return "", 0.0, 0
    keys = [answer_key(s) for s in valid]
    top_key, agree = Counter(keys).most_common(1)[0]
    # representative = the first full sample whose key is the majority key
    rep = next((s for s, k in zip(valid, keys, strict=False) if k == top_key), valid[0])
    return rep, agree / len(valid), agree


def slm_mux_select(
    prompt: str,
    models: Sequence[str],
    sampler: Callable[[str, str], str],
    *,
    k: int = 3,
    answer_key: Callable[[str], str] | None = None,
) -> MuxResult:
    """Run each model k times, pick the answer from the most self-consistent model.

    models: ordered by preference (first = tie-break winner). sampler(model, prompt) returns one
    sampled answer (caller sets temperature > 0 so samples vary). k: samples per model.
    """
    key = answer_key or default_answer_key
    candidates: list[MuxCandidate] = []
    for model in models:
        samples = tuple(str(sampler(model, prompt) or "") for _ in range(max(1, k)))
        rep, conf, agree = _majority(samples, key)
        candidates.append(
            MuxCandidate(model=model, answer=rep, confidence=conf,
                         agree_count=agree, sample_count=len([s for s in samples if s.strip()]),
                         samples=samples)
        )
    if not candidates:
        return MuxResult(answer="", model="", confidence=0.0, candidates=(), reason="no_models")
    # Highest self-consistency wins; ties broken by model preference order (index).
    order = {m: i for i, m in enumerate(models)}
    best = min(candidates, key=lambda c: (-c.confidence, order.get(c.model, 1_000)))
    reason = (
        f"selected {best.model} (self-consistency {best.confidence:.0%}) over "
        + ", ".join(f"{c.model} {c.confidence:.0%}" for c in candidates if c.model != best.model)
    ) if len(candidates) > 1 else f"single model {best.model}"
    return MuxResult(answer=best.answer, model=best.model, confidence=best.confidence,
                     candidates=tuple(candidates), reason=reason)


__all__ = ["MuxCandidate", "MuxResult", "default_answer_key", "slm_mux_select"]
