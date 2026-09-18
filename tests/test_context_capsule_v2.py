from __future__ import annotations

import re

from core.context_capsule_v2 import (
    ContextCandidate,
    estimate_tokens,
    pack_context,
    resolve_budget,
    resolve_num_ctx,
    role_fraction,
)
from core.context_retrieval import _content_covered as real_covered
from core.context_retrieval import _content_covered_substring as real_covered_substr

# Real audited callables — used where it strengthens the test (redaction + dedup parity).
from core.local_inference_autopilot import _sanitize_text as real_sanitize

BUCKETS = ("A", "B", "C", "D", "E")
ROLES = ("heavy_reasoning", "general", "lightweight_utility", "coding")


# --- fakes ------------------------------------------------------------------------------------
def _noop_sanitize(text: str) -> tuple[str, int]:
    return text, 0


def _fake_sanitize(text: str) -> tuple[str, int]:
    n = 0
    def _r(_m):
        nonlocal n
        n += 1
        return "[REDACTED]"
    clean = re.sub(r"sk-[A-Za-z0-9]{6,}", _r, text)
    return clean, n


def _no_cover(_a: str, _b: str, _thr: float = 0.0) -> bool:
    return False


def _no_cover_sub(_a: str, _b: str, _n: int = 0) -> bool:
    return False


def _fake_distiller(text: str, target_tokens: int) -> str:
    return text[: max(4, target_tokens * 4)]


def _budget(bucket="B", role="general", **kw):
    return resolve_budget(bucket=bucket, role=role, **kw)


# ============================================================================================
# 1. resolve_num_ctx matrix + inversion fix
# ============================================================================================
def test_resolve_num_ctx_matrix_and_inversion_fixed() -> None:
    # exact fp16 values
    assert resolve_num_ctx(bucket="A", role="general") == 3072
    assert resolve_num_ctx(bucket="B", role="general") == 6144
    assert resolve_num_ctx(bucket="C", role="general") == 12288
    assert resolve_num_ctx(bucket="D", role="general") == 24576
    assert resolve_num_ctx(bucket="A", role="lightweight_utility") == 2048
    # q8_0 doubles but never exceeds the per-bucket ceiling
    assert resolve_num_ctx(bucket="B", role="general", kv_quant="q8_0") == 12288
    assert resolve_num_ctx(bucket="C", role="heavy_reasoning", kv_quant="q8_0") == 24576  # ceiling clamps 32768
    # full A-E x roles x quant: always >= 1024 and <= per-bucket ceiling
    ceil = {"A": 8192, "B": 16384, "C": 24576, "D": 32768, "E": 32768}
    for b in BUCKETS:
        for r in ROLES:
            for q in ("fp16", "q8_0"):
                v = resolve_num_ctx(bucket=b, role=r, kv_quant=q)
                assert 1024 <= v <= ceil[b]
    # THE inversion fix: the deep/reasoning lane must get MORE context than the tiny lane.
    assert resolve_num_ctx(bucket="C", role="heavy_reasoning") > resolve_num_ctx(bucket="C", role="lightweight_utility")
    assert role_fraction("heavy_reasoning") > role_fraction("lightweight_utility")


# ============================================================================================
# 2. resolve_budget monotonicity + min_score bands
# ============================================================================================
def test_resolve_budget_monotonic_and_min_score_bands() -> None:
    packs = {b: _budget(bucket=b).pack_target_tokens for b in BUCKETS}
    assert packs["E"] >= packs["D"] >= packs["C"] >= packs["B"] >= packs["A"]
    # utility role never exceeds the general pack_target at the same bucket
    assert _budget(bucket="C", role="lightweight_utility").pack_target_tokens <= _budget(bucket="C", role="general").pack_target_tokens
    assert _budget(bucket="A").min_score == 0.46
    assert _budget(bucket="B").min_score == 0.42
    assert _budget(bucket="D").min_score == 0.38
    assert _budget(bucket="E").min_score == 0.38


# ============================================================================================
# 3. reserve correctness — never overflow num_ctx, never negative
# ============================================================================================
def test_reserve_never_overflows_or_goes_negative() -> None:
    for b in BUCKETS:
        for tr in (0, 500, 5000, 100000):
            bud = _budget(bucket=b, transcript_tokens=tr)
            assert bud.usable_tokens >= 0
            assert bud.pack_target_tokens >= 0
            assert bud.free_tokens >= 0
            # the whole reservation can never exceed the window
            assert bud.output_reserve_tokens + max(0, bud.num_ctx * 0) <= bud.num_ctx
            assert bud.pack_target_tokens + bud.output_reserve_tokens <= bud.num_ctx
    # fuzz output_reserve up to num_ctx -> still no negatives
    for reserve in (240, 1000, 4096, 100000):
        bud = _budget(bucket="B", output_reserve_tokens=reserve)
        assert bud.usable_tokens >= 0 and bud.pack_target_tokens >= 0 and bud.free_tokens >= 0


# ============================================================================================
# 4. redaction reuse — CRITICAL: this path reaches the model
# ============================================================================================
def test_redaction_is_applied_on_the_injection_boundary() -> None:
    secret = "sk-abcdef0123456789ABCDEF"
    cands = [
        ContextCandidate(id="s1", kind="structured", text=f"the api key is {secret} keep it"),
        ContextCandidate(id="s2", kind="semantic", text="benign fact about ports", source_score=0.9),
    ]
    out = pack_context(
        cands, budget=_budget(), query="key",
        sanitize_fn=_fake_sanitize, covered_fn=_no_cover, covered_substr_fn=_no_cover_sub, now=0.0,
    )
    assert secret not in out.render_block            # raw secret never reaches the prompt
    assert "[REDACTED]" in out.render_block
    assert out.total_redactions >= 1
    # a candidate already sanitized at harvest (redacted_items>0) is not re-sanitized
    pre = [ContextCandidate(id="p", kind="structured", text="clean", redacted_items=2)]
    out2 = pack_context(pre, budget=_budget(), sanitize_fn=_fake_sanitize,
                        covered_fn=_no_cover, covered_substr_fn=_no_cover_sub, now=0.0)
    assert out2.total_redactions == 0

    # the REAL audited sanitizer also strips an sk- token end-to-end (integration smoke)
    real = pack_context(
        [ContextCandidate(id="r", kind="structured", text=f"token {secret} end")],
        budget=_budget(), sanitize_fn=real_sanitize, covered_fn=_no_cover,
        covered_substr_fn=_no_cover_sub, now=0.0,
    )
    assert secret not in real.render_block


# ============================================================================================
# 5. dedup parity — candidates already in the transcript are dropped (real covered fns)
# ============================================================================================
def test_dedup_against_transcript_drops_covered() -> None:
    transcript = "The deploy port is 18789 and the gateway token is required for the dashboard."
    cands = [
        ContextCandidate(id="dup", kind="structured", text="The deploy port is 18789 and the gateway token is required."),
        ContextCandidate(id="fresh", kind="structured", text="Solana registrar lives on mainnet only."),
    ]
    out = pack_context(
        cands, budget=_budget(), query="port",
        sanitize_fn=_noop_sanitize, covered_fn=real_covered, covered_substr_fn=real_covered_substr,
        transcript_text=transcript, now=0.0,
    )
    ids = {b.id for b in out.blocks}
    assert "dup" not in ids
    assert "fresh" in ids
    assert out.dropped_covered >= 1


# ============================================================================================
# 6. three-pool guarantees — recent floor honored; oversized pins truncated
# ============================================================================================
def test_recent_floor_kept_even_when_low_value() -> None:
    bud = _budget(bucket="C")  # generous
    cands = [
        ContextCandidate(id="new", kind="recent_turn", text="x " * 20, recency_rank=0),      # newest, low value
        ContextCandidate(id="olfact", kind="structured", text="y " * 20, source_score=0.99),  # high density old fact
    ]
    out = pack_context(cands, budget=bud, query="q", sanitize_fn=_noop_sanitize,
                       covered_fn=_no_cover, covered_substr_fn=_no_cover_sub, now=0.0)
    assert "new" in {b.id for b in out.blocks}  # the immediate turn is never dropped for coherence


def test_oversized_pins_do_not_blow_pack_target() -> None:
    bud = _budget(bucket="A")  # small pin ceiling
    big = "z " * 4000  # ~2000 tokens, far over any pin ceiling
    cands = [ContextCandidate(id="pin1", kind="pinned", pinned=True, text=big)]
    out = pack_context(cands, budget=bud, sanitize_fn=_noop_sanitize,
                       covered_fn=_no_cover, covered_substr_fn=_no_cover_sub, now=0.0)
    assert out.tokens_used <= bud.pack_target_tokens + 5  # never overflows the budget
    assert out.dropped_budget >= 1


# ============================================================================================
# 7. knapsack — best-single guard recovers a high-value item greedy-by-density skips
# ============================================================================================
def test_knapsack_recovers_high_value_item_over_greedy() -> None:
    bud = _budget(bucket="B")
    free = bud.free_tokens
    assert free >= 120  # sanity for the construction
    # Two tiny high-density items whose values SUM to less than one big high-value item.
    # Pure greedy-by-density fills the tinies first (density 0.45 >> big's), leaving no room for
    # BIG (value 0.9 < 1.0). The best-single guard must recover BIG.
    tinies = [
        ContextCandidate(id=f"t{i}", kind="semantic", text="a" * 4, source_score=0.45)  # ~1 tok, value 0.45
        for i in range(2)
    ]
    big = ContextCandidate(id="BIG", kind="semantic", text="b" * (free * 4), source_score=1.0)  # ~free tok
    out = pack_context([*tinies, big], budget=bud, query="q", sanitize_fn=_noop_sanitize,
                       covered_fn=_no_cover, covered_substr_fn=_no_cover_sub, now=0.0)
    chosen_ids = {b.id for b in out.blocks}
    assert "BIG" in chosen_ids                      # guard recovered the high-value item
    assert sum(b.value for b in out.blocks) >= 0.9  # >= pure-greedy's 2*0.45; here 1.0 (BIG)


# ============================================================================================
# 8. distillation — verbatim->summarized under pressure; None distiller -> clean drop
# ============================================================================================
def test_distillation_summarizes_overflow_or_drops_when_no_distiller() -> None:
    bud = _budget(bucket="A")  # tight free pool
    free = bud.free_tokens
    # one item that fits verbatim + one that only fits when summarized to 40%
    fit = ContextCandidate(id="fit", kind="semantic", text="c" * (free * 2), source_score=0.9)   # ~free/2 tok
    over = ContextCandidate(id="over", kind="semantic", text="d" * (free * 3), source_score=0.88)  # too big verbatim
    with_distill = pack_context([fit, over], budget=bud, query="q", sanitize_fn=_noop_sanitize,
                                covered_fn=_no_cover, covered_substr_fn=_no_cover_sub,
                                distiller=_fake_distiller, now=0.0)
    dispositions = {b.id: b.disposition for b in with_distill.blocks}
    # at least one summarized OR the summary path was exercised
    assert with_distill.summarized >= 1 or "over" in dispositions

    no_distill = pack_context([fit, over], budget=bud, query="q", sanitize_fn=_noop_sanitize,
                              covered_fn=_no_cover, covered_substr_fn=_no_cover_sub,
                              distiller=None, now=0.0)
    assert no_distill.summarized == 0
    assert no_distill.dropped_budget >= 1  # overflow is cleanly dropped, no stub tier


# ============================================================================================
# 9. determinism / purity — identical + order-independent
# ============================================================================================
def test_determinism_and_order_independence() -> None:
    cands = [
        ContextCandidate(id="p", kind="pinned", pinned=True, text="rule one"),
        ContextCandidate(id="s", kind="structured", text="repo detail alpha"),
        ContextCandidate(id="m", kind="semantic", text="recalled fact beta", source_score=0.8),
        ContextCandidate(id="r", kind="recent_turn", text="user said gamma", recency_rank=0),
    ]
    kw = {"budget": _budget(bucket="C"), "query": "alpha", "sanitize_fn": _noop_sanitize,
          "covered_fn": _no_cover, "covered_substr_fn": _no_cover_sub, "now": 0.0}
    a = pack_context(list(cands), **kw)
    b = pack_context(list(cands), **kw)
    assert a.render_block == b.render_block
    assert a.stable_prefix_hash == b.stable_prefix_hash
    # shuffled input -> identical canonical output
    c = pack_context(list(reversed(cands)), **kw)
    assert c.render_block == a.render_block


# ============================================================================================
# 10. stable_prefix_hash — stable when only the volatile tail changes
# ============================================================================================
def test_stable_prefix_hash_ignores_volatile_tail() -> None:
    durable = [
        ContextCandidate(id="p", kind="pinned", pinned=True, text="durable rule"),
        ContextCandidate(id="s", kind="structured", text="durable repo fact"),
    ]
    turn1 = [*durable, ContextCandidate(id="r1", kind="recent_turn", text="turn one", recency_rank=0)]
    turn2 = [*durable, ContextCandidate(id="r2", kind="recent_turn", text="a totally different turn two", recency_rank=0)]
    kw = {"budget": _budget(bucket="C"), "sanitize_fn": _noop_sanitize, "covered_fn": _no_cover,
          "covered_substr_fn": _no_cover_sub, "now": 0.0}
    h1 = pack_context(turn1, **kw).stable_prefix_hash
    h2 = pack_context(turn2, **kw).stable_prefix_hash
    assert h1 == h2 and h1  # durable prefix identical -> hash identical (KV reuse boundary)


# ============================================================================================
# 11. estimate_tokens — conservative upper bound, rounds up
# ============================================================================================
def test_estimate_tokens_is_upper_bound() -> None:
    for s in ("", "a", "abcd", "hello world", "def f():\n    return 1", "日本語テキスト" * 3):
        assert estimate_tokens(s) >= len(s) // 4
    assert estimate_tokens("abcde") == 2  # (5+3)//4


# ============================================================================================
# 12. best-effort resilience — a raising injected fn degrades, never crashes
# ============================================================================================
def test_pack_context_degrades_on_injected_fn_error() -> None:
    def _boom_sanitize(_t):
        raise RuntimeError("boom")
    out = pack_context(
        [ContextCandidate(id="x", kind="structured", text="anything")],
        budget=_budget(), sanitize_fn=_boom_sanitize, covered_fn=_no_cover,
        covered_substr_fn=_no_cover_sub, now=0.0,
    )
    assert out.degraded_reason == "error"
    assert out.blocks == ()
    assert out.render_block == ""


# ============================================================================================
# 13. flagship budget regression — locks the real 8GB-1080 (bucket B) uplift
# ============================================================================================
def test_flagship_bucket_b_budget_regression() -> None:
    # 8GB GTX 1080, qwen3:8b, general role, fp16 (P0 off), transcript ~1500 tok
    bud = resolve_budget(bucket="B", role="general", kv_quant="fp16", transcript_tokens=1500)
    assert bud.num_ctx == 6144
    assert bud.pack_target_tokens == 3396          # ~5x the shipped 350-token injection cap
    assert bud.min_score == 0.42
    # with q8_0 KV (P0 on) the window doubles but retrieval stays bounded by the 2048 ceiling
    bud_q = resolve_budget(bucket="B", role="general", kv_quant="q8_0", transcript_tokens=1500)
    assert bud_q.num_ctx == 12288
    assert bud_q.free_tokens <= 2048
