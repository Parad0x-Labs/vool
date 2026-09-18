"""Law 5 — the session is paged, not truncated (NIA-015 wiring proof).

The motivating defect, measured 2026-08-29 in core/kernel/repl.py: cross-turn
continuity was ``history[-4:]`` with answers cut at 400 characters — a URL planted
in turn 3 no longer existed anywhere by turn 10. These tests pin the law that
replaces it: spine always covered, hot window, sha-verified cold recall,
fail-closed refusal, and the REPL seam that now routes through PagedSession.
"""
from __future__ import annotations

import re

import pytest

from core.kernel.paged_memory import (
    ContextUndercovered,
    CoverageProof,
    PagedSession,
    RecallRefused,
    recall_handles,
)


def _long_session(pager: PagedSession, turns: int = 12) -> None:
    """A session where the load-bearing item exists ONLY in turn 3."""
    pager.admit_turn(
        "use the base url https://api.openrouter.ai/v1 and budget 42 EUR for this job",
        "registered: base url https://api.openrouter.ai/v1, budget 42 EUR",
        turn_index=3,
    )
    filler = "tell me a fact"
    for i in range(1, turns + 1):
        if i == 3:
            continue
        pager.admit_turn(filler, f"fact {i}: the sky is large", turn_index=i)


# -- the law itself ----------------------------------------------------------------------


def test_turn3_url_survives_past_the_hot_window():
    """THE regression: the motivating defect lost turn-3 truth by turn 10. The law
    is closed-vocabulary: recall fires when this turn's opaque anchors intersect
    the stored page's anchors — here, the exact URL from turn 3."""
    pager = PagedSession()
    _long_session(pager)
    assembly = pager.assemble(
        "resume the job against https://api.openrouter.ai/v1 — what were the settings?",
        ledger_rows=[], hot_history=[], budget_chars=6000,
    )
    assert "openrouter" in assembly.text.lower(), (
        "cold tier must surface the anchor-intersecting page header"
    )
    proof = assembly.proof
    assert any(row.tier == "cold_hit" for row in proof.rows)


def test_spine_is_always_covered_and_first():
    pager = PagedSession()
    _long_session(pager)
    rows = [{"id": "L1", "description": "ship the report", "reason": "user asked"}]
    assembly = pager.assemble("status?", ledger_rows=rows, budget_chars=6000)
    proof: CoverageProof = assembly.proof
    assert proof.rows[0].tier == "spine" and proof.rows[0].item == "L1"
    assert all(
        any(r.item == c["id"] and r.tier == "spine" for r in proof.rows) for c in rows
    ), "every ledger row must be vouched for — a dropped obligation is the defect reborn"


def test_fail_closed_when_spine_cannot_fit():
    pager = PagedSession()
    rows = [{"id": "L1", "description": "x" * 9_000, "reason": "r"}]
    with pytest.raises(ContextUndercovered) as exc:
        pager.assemble("q", ledger_rows=rows, budget_chars=6000)
    assert "L1" in str(exc.value) and exc.value.uncovered[0][0] == "L1"


def test_recall_is_byte_exact_and_sha_verified():
    pager = PagedSession()
    page = pager.admit_turn("remember abc123", "stored abc123", turn_index=1)
    assert pager.recall(f"p{page.page_id}") == page.text
    # Bit-rot is a refusal, not silent corruption.
    pager._pages[0] = type(page)(
        page_id=page.page_id, turn_index=1, user_text="remember abc123",
        answer_text="TAMPERED", sha256=page.sha256, anchors=page.anchors,
    )
    with pytest.raises(RecallRefused):
        pager.recall(f"p{page.page_id}")


def test_bad_handle_is_refused():
    pager = PagedSession()
    with pytest.raises(RecallRefused):
        pager.recall("nope")


def test_hot_window_uses_caller_history_shape():
    pager = PagedSession(hot_window=2)
    assembly = pager.assemble(
        "now what?",
        ledger_rows=[],
        hot_history=[("q1", "a1"), ("q2", "a2"), ("q3", "a3")],
        budget_chars=6000,
    )
    assert "q3" in assembly.text and "q1" not in assembly.text


def test_admit_is_all_or_nothing_and_1_based():
    pager = PagedSession()
    with pytest.raises(ValueError):
        pager.admit_turn("", "a", turn_index=1)
    with pytest.raises(ValueError):
        pager.admit_turn("q", "a", turn_index=0)
    assert len(pager) == 0 and pager.stats()["pages"] == 0


def test_recall_handles_extraction():
    assert recall_handles("see p0001 and again p0001 plus p0002") == ("p0001", "p0002")


# -- the REPL seam (wiring is real, not nominal) ------------------------------------------


def test_repl_module_imports_and_owns_the_law():
    """The defect site must route through PagedSession now: the import exists and
    build_context delegates to pager.assemble (grep-level proof, cheap and honest)."""
    import pathlib

    source = pathlib.Path("core/kernel/repl.py").read_text()
    assert "from core.kernel.paged_memory import" in source
    assert "pager.assemble(" in source
    assert "pager.admit_turn(" in source
    # The old truncation defect is gone from the served assembly path.
    assert "history[-4:]" not in source, "the measured defect must not survive the law"


def test_paged_memory_has_no_model_and_no_network():
    """Structural law: memory is deterministic — stdlib + lexical authority only."""
    import pathlib

    source = pathlib.Path("core/kernel/paged_memory.py").read_text()
    imports = re.findall(r"^(?:import|from)\s+([a-zA-Z_][\w.]*)", source, re.M)
    allowed_roots = {"hashlib", "re", "dataclasses", "typing", "__future__", "core"}
    assert {i.split(".")[0] for i in imports} <= allowed_roots
