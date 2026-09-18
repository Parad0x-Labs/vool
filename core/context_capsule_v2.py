"""
core/context_capsule_v2.py
==========================
Context Capsule 2.0 — a hardware-adaptive local-LLM context packer.

The job of a small local model (e.g. qwen3:8b on an 8GB GTX 1080) is usually not
bottlenecked by parameters but by KNOWLEDGE: the right fact/decision/detail was never
in its window. Capsule 2.0 makes every token of the (now bigger, KV-quantized) window
carry maximum value, so a small model punches above its weight — WITHOUT choking a
consumer PC.

This module is the pure, compute-only core. It performs zero I/O and imports
nothing from the VOOL runtime. Live inference wires it through core.context_retrieval
behind VOOL_CONTEXT_CAPSULE_V2, after retrieval has distilled noisy local context. The redaction
(`_sanitize_text`), dedup (`_content_covered` / `_content_covered_substring`) and any
summarizer are *injected as callables* (dependency inversion) so this stays testable and
so a bug here can never crash a live turn.

Three composed innovations ("our IP"):
  1. Hardware-adaptive budget: the window + injection budget derive from the capacity
     bucket (A-E) x role x KV-quant, fixing the shipped INVERTED role->ctx map where the
     deep-reasoning lane got the SMALLEST window.
  2. Value-density three-pool packing: PIN (durable) / RECENT floor (coherence) / FREE
     (greedy-by-density knapsack with a bounded local-swap pass) — more meaning per token
     than fixed per-section clips + tail truncation.
  3. Stable/volatile split: the prefix hash covers only the durable (pinned+structured)
     text so it's byte-stable turn-to-turn — a real KV-cache-reuse boundary for
     llama.cpp/MLX, and stable ordering on Ollama.
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Literal

SCHEMA = "vool.context_capsule.v2"

Bucket = Literal["A", "B", "C", "D", "E"]
KvQuant = Literal["fp16", "q8_0"]
CandidateKind = Literal["pinned", "recent_turn", "semantic", "structured"]
Disposition = Literal["verbatim", "summarized", "dropped"]
Source = Literal["semantic", "bm25_boost", "link_expansion", "pinned", "recent", "structured"]

# --- Hardware tables (fp16 baseline; q8_0 KV roughly halves memory so ~2x context) --------------
_BASE_FP16: dict[str, int] = {"A": 4096, "B": 8192, "C": 16384, "D": 32768, "E": 32768}
_CTX_CEILING: dict[str, int] = {"A": 8192, "B": 16384, "C": 24576, "D": 32768, "E": 32768}
# Role -> fraction of the window (applied after the KV multiplier). Long-context lanes get MORE,
# reversing the shipped inversion where heavy_reasoning got 1024 (the smallest).
_ROLE_FULL = frozenset({"heavy_reasoning", "reasoning", "coding", "coder", "deep_overnight", "deep", "queen"})
_ROLE_LIGHT = frozenset({"lightweight_utility", "utility", "tiny", "tiny_fast", "classifier", "router", "drone_tiny"})
_HARD_RETRIEVAL_CEILING_TOKENS = 2048  # never dump an unbounded wall of low-relevance text, any bucket
_MIN_NUM_CTX = 1024
_DEDUP_THRESHOLD = 0.82  # word-overlap ratio for _content_covered
_DEDUP_MIN_SUBSTRING = 48


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _norm_role(role: str) -> str:
    return str(role or "").strip().lower().replace("-", "_").replace(" ", "_")


def role_fraction(role: str) -> float:
    """Fraction of the window a role should be allowed to fill. Long-context lanes -> 1.0."""
    r = _norm_role(role)
    if r in _ROLE_FULL:
        return 1.0
    if r in _ROLE_LIGHT:
        return 0.5
    return 0.75  # general / daily / daily_accelerated / unknown


def estimate_tokens(text: str) -> int:
    """Conservative chars/4 UPPER bound, rounds up. Mirrors conversation_summarizer.token_estimate's
    /4 convention but for a raw string. Over-estimates so packing never overflows num_ctx."""
    n = len(str(text or ""))
    return (n + 3) // 4


def resolve_kv_quant(env: Mapping[str, str] | None = None) -> KvQuant:
    """Reads OLLAMA_KV_CACHE_TYPE. 'q8_0' -> 'q8_0'; anything else -> 'fp16' (conservative:
    never assume the memory headroom of KV quantization unless it's actually configured)."""
    env = env or {}
    raw = str(env.get("OLLAMA_KV_CACHE_TYPE") or "").strip().lower()
    return "q8_0" if raw in {"q8_0", "q8", "8"} else "fp16"


def resolve_num_ctx(*, bucket: Bucket, role: str, kv_quant: KvQuant = "fp16") -> int:
    """Hardware-adaptive replacement for the shipped INVERTED
    _ollama_context_window_for_bundle_role. Pure + table-driven."""
    b = bucket if bucket in _BASE_FP16 else "A"
    raw = _BASE_FP16[b] * (2 if kv_quant == "q8_0" else 1) * role_fraction(role)
    return int(_clamp(round(raw), _MIN_NUM_CTX, _CTX_CEILING[b]))


@dataclass(frozen=True)
class InjectionBudget:
    bucket: Bucket
    role: str
    kv_quant: KvQuant
    num_ctx: int
    output_reserve_tokens: int
    usable_tokens: int
    pack_target_tokens: int
    pin_ceiling_tokens: int
    recent_floor_tokens: int
    free_tokens: int
    min_score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "bucket": self.bucket, "role": self.role, "kv_quant": self.kv_quant,
            "num_ctx": self.num_ctx, "output_reserve_tokens": self.output_reserve_tokens,
            "usable_tokens": self.usable_tokens, "pack_target_tokens": self.pack_target_tokens,
            "pin_ceiling_tokens": self.pin_ceiling_tokens, "recent_floor_tokens": self.recent_floor_tokens,
            "free_tokens": self.free_tokens, "min_score": self.min_score,
        }


def _min_score_for_bucket(bucket: Bucket) -> float:
    if bucket == "A":
        return 0.46  # scarce room -> only strong hits
    if bucket in {"D", "E"}:
        return 0.38  # room to spare
    return 0.42


def resolve_budget(
    *,
    bucket: Bucket,
    role: str,
    kv_quant: KvQuant = "fp16",
    gpu_served: bool = True,
    output_reserve_tokens: int = 240,   # plain_text max_output_tokens
    transcript_tokens: int = 0,
    recent_floor_frac: float = 0.35,
    pin_ceiling_frac: float = 0.25,
) -> InjectionBudget:
    """The heart of the adaptive scheme — reserve-before-inject, table-driven, pure.
    injection can never crowd the decode window or overflow num_ctx."""
    b = bucket if bucket in _BASE_FP16 else "A"
    num_ctx = resolve_num_ctx(bucket=b, role=role, kv_quant=kv_quant)
    output_reserve = max(int(output_reserve_tokens), 256)
    safety_margin = max(256, round(0.10 * num_ctx))
    usable = max(0, num_ctx - output_reserve - safety_margin - max(0, int(transcript_tokens)))
    derate = 0.90 if gpu_served else 0.80  # CPU/MPS spill is slower -> leave more headroom
    pack_target = int(usable * derate)
    pin_ceiling = round(pin_ceiling_frac * pack_target)
    recent_floor = round(recent_floor_frac * pack_target)
    # Independent hard ceiling on the RETRIEVAL (free/semantic) pool: even a 24GB card never gets
    # an unbounded wall of retrieved text inflating the KV cache. Pins + recent floor are separate.
    free = min(max(0, pack_target - pin_ceiling - recent_floor), _HARD_RETRIEVAL_CEILING_TOKENS)
    return InjectionBudget(
        bucket=b, role=role, kv_quant=kv_quant, num_ctx=num_ctx,
        output_reserve_tokens=output_reserve, usable_tokens=usable, pack_target_tokens=pack_target,
        pin_ceiling_tokens=pin_ceiling, recent_floor_tokens=recent_floor, free_tokens=free,
        min_score=_min_score_for_bucket(b),
    )


@dataclass(frozen=True)
class ContextCandidate:
    id: str
    kind: CandidateKind
    text: str
    value: float = 0.0
    cost_tokens: int = 0
    recency_rank: int | None = None      # 0 = newest turn
    pinned: bool = False
    source_score: float | None = None    # upstream node_search_hybrid score, if kind=="semantic"
    source: Source = "structured"
    redacted_items: int = 0              # >0 means text already sanitized at harvest


@dataclass(frozen=True)
class PackedBlock:
    id: str
    kind: CandidateKind
    text: str
    tokens: int
    value: float
    disposition: Disposition


@dataclass(frozen=True)
class PackedContext:
    schema: str
    stable_prefix_hash: str
    blocks: tuple[PackedBlock, ...]
    render_block: str
    budget: InjectionBudget
    tokens_used: int
    chosen: int
    summarized: int
    dropped_covered: int
    dropped_budget: int
    total_redactions: int
    degraded_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema, "stable_prefix_hash": self.stable_prefix_hash,
            "blocks": [
                {"id": b.id, "kind": b.kind, "tokens": b.tokens, "value": round(b.value, 4),
                 "disposition": b.disposition}
                for b in self.blocks
            ],
            "budget": self.budget.to_dict(), "tokens_used": self.tokens_used, "chosen": self.chosen,
            "summarized": self.summarized, "dropped_covered": self.dropped_covered,
            "dropped_budget": self.dropped_budget, "total_redactions": self.total_redactions,
            "degraded_reason": self.degraded_reason,
        }


def _query_overlap(text: str, query: str, covered_fn: Callable[[str, str, float], bool]) -> bool:
    if not query:
        return False
    try:
        return bool(covered_fn(query, text, 0.34))
    except Exception:
        return False


def score_candidates(
    candidates: Sequence[ContextCandidate],
    *,
    query: str,
    covered_fn: Callable[[str, str, float], bool],
    now: float,
) -> tuple[ContextCandidate, ...]:
    """Assign value in [0,1] + cost_tokens. Pure; returns priced copies."""
    priced: list[ContextCandidate] = []
    for cand in candidates:
        cost = cand.cost_tokens or estimate_tokens(cand.text)
        overlap = _query_overlap(cand.text, query, covered_fn)
        if cand.pinned or cand.kind == "pinned":
            value = 1.0
        elif cand.kind == "semantic":
            value = float(_clamp(cand.source_score if cand.source_score is not None else 0.0, 0.0, 1.0))
        elif cand.kind == "recent_turn":
            rank = max(0, int(cand.recency_rank or 0))
            value = min(1.0, 0.9 * (0.85 ** rank) + (0.05 if overlap else 0.0))
        else:  # structured
            value = min(1.0, 0.55 + (0.15 if overlap else 0.0))
        priced.append(replace(cand, value=round(value, 6), cost_tokens=int(cost)))
    return tuple(priced)


def _density(cand: ContextCandidate) -> float:
    return cand.value / cand.cost_tokens if cand.cost_tokens > 0 else cand.value


def _greedy_by_density(pool: list[ContextCandidate], budget_tokens: int) -> list[ContextCandidate]:
    """Greedy-by-density, then a bounded local-swap + best-single-item guard so we always do at
    least as well as pure greedy on the classic 'one big item vs many small' knapsack trap."""
    if budget_tokens <= 0:
        return []
    # deterministic order: density desc, then value desc, then id asc
    ordered = sorted(pool, key=lambda c: (-_density(c), -c.value, c.id))
    chosen: list[ContextCandidate] = []
    used = 0
    for cand in ordered:
        if used + cand.cost_tokens <= budget_tokens:
            chosen.append(cand)
            used += cand.cost_tokens
    greedy_value = sum(c.value for c in chosen)

    # Guard: a single high-value item that greedy-by-density skipped may beat the greedy set.
    fits_single = [c for c in pool if c.cost_tokens <= budget_tokens]
    if fits_single:
        best_single = max(fits_single, key=lambda c: (c.value, -c.cost_tokens, c.id))
        if best_single.value > greedy_value:
            chosen, used, greedy_value = [best_single], best_single.cost_tokens, best_single.value

    # One bounded local-swap pass: try to replace the lowest-value included item with a
    # higher-value excluded one that fits in the freed space.
    included_ids = {c.id for c in chosen}
    excluded = [c for c in pool if c.id not in included_ids]
    if chosen and excluded:
        worst = min(chosen, key=lambda c: (c.value, -c.cost_tokens, c.id))
        room = budget_tokens - (used - worst.cost_tokens)
        candidate_swaps = [c for c in excluded if c.cost_tokens <= room and c.value > worst.value]
        if candidate_swaps:
            best = max(candidate_swaps, key=lambda c: (c.value, -c.cost_tokens, c.id))
            chosen = [c for c in chosen if c.id != worst.id] + [best]
    return chosen


def _hash_stable_prefix(blocks: Sequence[PackedBlock]) -> str:
    """sha256 (16 hex) over the DURABLE prefix (pinned + structured) text only, in canonical id
    order. Recent/semantic tail is excluded so the hash is byte-stable turn-to-turn."""
    durable = sorted(
        (b for b in blocks if b.kind in {"pinned", "structured"}),
        key=lambda b: b.id,
    )
    joined = "\n".join(f"{b.id}:{b.text}" for b in durable)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def pack_context(
    candidates: Sequence[ContextCandidate],
    *,
    budget: InjectionBudget,
    query: str = "",
    sanitize_fn: Callable[[str], tuple[str, int]],
    covered_fn: Callable[[str, str, float], bool],
    covered_substr_fn: Callable[[str, str, int], bool],
    transcript_text: str = "",
    distiller: Callable[[str, int], str] | None = None,
    now: float | None = None,
) -> PackedContext:
    """THE core entry point. Deterministic, no I/O. Any internal failure -> a degraded
    PackedContext with empty blocks so the caller cleanly falls back to legacy behavior."""
    now = 0.0 if now is None else float(now)
    empty_budget = budget
    try:
        # 1) price
        priced = list(score_candidates(candidates, query=query, covered_fn=covered_fn, now=now))

        # 2) dedup vs the transcript already in the prompt
        dropped_covered = 0
        surviving: list[ContextCandidate] = []
        for cand in priced:
            covered = False
            if transcript_text:
                try:
                    covered = bool(covered_fn(cand.text, transcript_text, _DEDUP_THRESHOLD)) or \
                        bool(covered_substr_fn(cand.text, transcript_text, _DEDUP_MIN_SUBSTRING))
                except Exception:
                    covered = False
            if covered:
                dropped_covered += 1
            else:
                surviving.append(cand)

        # 3) redact every survivor that wasn't sanitized at harvest (this path REACHES the model)
        total_redactions = 0
        redacted: list[ContextCandidate] = []
        for cand in surviving:
            if cand.redacted_items > 0:
                redacted.append(cand)
                continue
            clean, n = sanitize_fn(cand.text)
            total_redactions += int(n or 0)
            redacted.append(replace(cand, text=clean, redacted_items=int(n or 0),
                                    cost_tokens=estimate_tokens(clean)))

        # 4) three-pool knapsack: PIN -> RECENT floor -> FREE (density)
        pins = [c for c in redacted if c.pinned or c.kind == "pinned"]
        recents = [c for c in redacted if c.kind == "recent_turn" and not c.pinned]
        free_pool = [c for c in redacted if c.kind not in {"pinned", "recent_turn"} and not c.pinned]

        chosen: list[tuple[ContextCandidate, Disposition]] = []
        dropped_budget = 0
        summarized = 0

        # PIN pool (durable, value 1.0): take highest-value within pin ceiling
        used_pin = 0
        for cand in sorted(pins, key=lambda c: (-c.value, c.id)):
            if used_pin + cand.cost_tokens <= budget.pin_ceiling_tokens:
                chosen.append((cand, "verbatim"))
                used_pin += cand.cost_tokens
            else:
                dropped_budget += 1

        # RECENT floor (coherence): newest first, verbatim, within recent_floor
        used_recent = 0
        for cand in sorted(recents, key=lambda c: (c.recency_rank if c.recency_rank is not None else 999, c.id)):
            if used_recent + cand.cost_tokens <= budget.recent_floor_tokens:
                chosen.append((cand, "verbatim"))
                used_recent += cand.cost_tokens
            else:
                dropped_budget += 1

        # FREE pool: greedy-by-density knapsack over remaining free tokens, min_score gate
        gated_free = [c for c in free_pool if c.value >= budget.min_score]
        picked_free = _greedy_by_density(gated_free, budget.free_tokens)
        picked_ids = {c.id for c in picked_free}
        for cand in picked_free:
            chosen.append((cand, "verbatim"))

        # 5) progressive distillation of free-pool overflow (verbatim -> summary -> drop)
        used_free = sum(c.cost_tokens for c in picked_free)
        remaining_free = max(0, budget.free_tokens - used_free)
        overflow = [c for c in gated_free if c.id not in picked_ids]
        for cand in sorted(overflow, key=lambda c: (-c.value, c.id)):
            if distiller is not None and remaining_free > 0:
                target = max(16, int(cand.cost_tokens * 0.4))
                if target <= remaining_free:
                    summary = distiller(cand.text, target)
                    stoks = estimate_tokens(summary)
                    if summary and stoks <= remaining_free:
                        chosen.append((replace(cand, text=summary, cost_tokens=stoks), "summarized"))
                        remaining_free -= stoks
                        summarized += 1
                        continue
            dropped_budget += 1

        # 6) render density-first: pinned header, structured/semantic, then recent verbatim
        order = {"pinned": 0, "structured": 1, "semantic": 2, "recent_turn": 3}
        chosen.sort(key=lambda pair: (order.get(pair[0].kind, 4),
                                      -pair[0].value, pair[0].id))
        blocks = tuple(
            PackedBlock(id=c.id, kind=c.kind, text=c.text, tokens=c.cost_tokens, value=c.value, disposition=disp)
            for c, disp in chosen
        )
        tokens_used = sum(b.tokens for b in blocks)
        render_block = ""
        if blocks:
            body = "\n".join(b.text for b in blocks if b.text.strip())
            render_block = f"<retrieved_context>\n{body}\n</retrieved_context>"

        return PackedContext(
            schema=SCHEMA,
            stable_prefix_hash=_hash_stable_prefix(blocks),
            blocks=blocks,
            render_block=render_block,
            budget=budget,
            tokens_used=tokens_used,
            chosen=len(blocks),
            summarized=summarized,
            dropped_covered=dropped_covered,
            dropped_budget=dropped_budget,
            total_redactions=total_redactions,
            degraded_reason=None,
        )
    except Exception:
        # Never crash a live turn: caller falls back to legacy behavior on a degraded result.
        return PackedContext(
            schema=SCHEMA, stable_prefix_hash="", blocks=(), render_block="", budget=empty_budget,
            tokens_used=0, chosen=0, summarized=0, dropped_covered=0, dropped_budget=0,
            total_redactions=0, degraded_reason="error",
        )


__all__ = [
    "SCHEMA",
    "ContextCandidate",
    "InjectionBudget",
    "PackedBlock",
    "PackedContext",
    "estimate_tokens",
    "pack_context",
    "resolve_budget",
    "resolve_kv_quant",
    "resolve_num_ctx",
    "role_fraction",
    "score_candidates",
]
