"""Regressions for the 2026-06 whole-stack production-audit fixes.

Each test pins a confirmed audit finding's fix so it cannot silently regress:
- BM25 single-match keyword boost (was collapsing to 0.0);
- embedding cosine / projection edge cases (zero vector, shorter-than-target);
- money-math boundaries (banker's-rounding tie, more shares than atomic units).
"""
from __future__ import annotations

from core.credit_ledger import _allocate_pool_atomic, usdc_to_atomic
from core.embedding_service import cosine_similarity, project_to_dim
from core.vool_memory import VoolMemory

# --- BM25 single-match boost (was 0.0) -------------------------------------

def test_bm25_single_match_scores_full_not_zero(tmp_path) -> None:
    mem = VoolMemory(runtime_home=tmp_path, agent_id="bm25")
    node = mem.node_store(
        content="ZX99 singular keyword token", keywords=["zx99"], tags=["t"],
        context_description="c", embedding=[1.0, 0.0, 0.0, 0.0], linked_node_ids=[],
    )
    try:
        scores = mem._bm25_scores("zx99")
        assert scores.get(node.node_id) == 1.0  # full keyword hit, not collapsed to 0.0
    finally:
        mem.close()


# --- embedding cosine / projection edge cases ------------------------------

def test_cosine_zero_vector_is_zero_not_nan() -> None:
    # same length, one vector all-zero -> the mag guard returns 0.0 (no div0/NaN)
    assert cosine_similarity([0.0, 0.0, 0.0, 0.0], [1.0, 0.0, 0.0, 0.0]) == 0.0


def test_cosine_shorter_vs_longer_uses_min_dim() -> None:
    # length mismatch -> both project to the min dim instead of silently scoring 0
    assert cosine_similarity([1.0, 0.0, 0.0, 0.0], [1.0, 0.0]) > 0.99


def test_project_to_dim_zero_vector_stays_zero() -> None:
    out = project_to_dim([0.0, 0.0, 0.0, 0.0], 2)
    assert len(out) == 2 and all(v == 0.0 for v in out)  # mag==0 guard, no div0


def test_project_to_dim_pads_when_shorter() -> None:
    out = project_to_dim([1.0, 0.0], 4)
    assert len(out) == 4
    assert abs(sum(x * x for x in out) - 1.0) < 1e-9  # unit-normalized after padding


# --- money-math boundaries -------------------------------------------------

def test_usdc_to_atomic_bankers_rounding_tie() -> None:
    # documented banker's rounding (round-half-to-even): 2.5 -> 2, not 3
    assert usdc_to_atomic(0.0000025) == 2


def test_allocate_pool_more_shares_than_units() -> None:
    alloc = _allocate_pool_atomic(2, 5)
    assert alloc == [1, 1, 0, 0, 0]
    assert sum(alloc) == 2  # whole pool allocated; leaders take the remainder
