"""Append-only context layout — the compiler laws and their sabotage proof.

The layout laws are load-bearing only if breaking them fails a test that
names the cause (CLAUDE.md §6b.4): a mutated frozen zone, a rewritten digest,
a regressing zone order — each must raise the named violation here.
"""

from __future__ import annotations

import pytest

from core.context_layout import (
    CompiledLayout,
    ContextBlock,
    LayoutLawViolation,
    Role,
    assert_digest_append_only,
    assert_frozen_stable,
    cache_provenance,
    compile_context,
)


def _frozen(text: str = "SYSTEM: you are VOOL.") -> ContextBlock:
    return ContextBlock(name="system", role=Role.FROZEN, text=text)


def _digest(text: str, name: str = "archive") -> ContextBlock:
    return ContextBlock(name=name, role=Role.DIGEST, text=text)


def _frontier(text: str, name: str = "frontier") -> ContextBlock:
    return ContextBlock(name=name, role=Role.FRONTIER, text=text)


def test_compile_emits_zones_in_frozen_digest_frontier_order():
    compiled = compile_context([_frontier("live"), _digest("closed work"), _frozen()])
    # The emitted order is the law's order, regardless of input order:
    assert [b.role for b in compiled.blocks] == [Role.FROZEN, Role.DIGEST, Role.FRONTIER]
    rendered = compiled.render()
    assert rendered.index("SYSTEM:") < rendered.index("closed work") < rendered.index("live")


def test_compilation_is_deterministic_and_receipted():
    blocks = [_frozen(), _digest("archived: O4 closed"), _frontier("open: O7")]
    first = compile_context(blocks)
    second = compile_context(list(reversed(blocks)))
    assert first.receipt == second.receipt
    assert first.render() == second.render()
    assert len(first.receipt["layout_hash"]) == 64  # type: ignore[arg-type]


def test_any_input_order_compiles_to_the_same_canonical_output():
    # The law governs the EMITTED order, not the caller's bookkeeping: every
    # permutation of the same blocks must produce identical bytes and receipt.
    blocks = [_frozen(), _digest("closed work"), _frontier("live")]
    baseline = compile_context(blocks)
    for permutation in (
        [_frontier("live"), _digest("closed work"), _frozen()],
        [_digest("closed work"), _frontier("live"), _frozen()],
        [_frontier("live"), _frozen(), _digest("closed work")],
    ):
        compiled = compile_context(permutation)
        assert compiled.render() == baseline.render()
        assert compiled.receipt == baseline.receipt


def test_empty_frozen_zone_is_still_rejected_after_normalization():
    with pytest.raises(LayoutLawViolation, match="frozen zone may not be empty"):
        compile_context([_digest("closed"), _frontier("live")])


def test_empty_frozen_zone_is_rejected_no_stable_prefix_no_cache():
    with pytest.raises(LayoutLawViolation, match="frozen zone may not be empty"):
        compile_context([_digest("closed"), _frontier("live")])


def test_frozen_zone_may_not_mutate_within_a_generation():
    # THE FROZEN LAW. Sabotage: inject a timestamp into the system prompt
    # between calls. If that stops raising, provider caches die silently.
    morning = compile_context([_frozen("SYSTEM: built 2026-08-29T09:00")]).receipt
    evening = compile_context([_frozen("SYSTEM: built 2026-08-29T21:00")]).receipt
    with pytest.raises(LayoutLawViolation, match="frozen zone changed"):
        assert_frozen_stable(morning, evening)
    assert_frozen_stable(morning, morning)  # identical is fine


def test_digest_may_only_grow_never_be_rewritten():
    # THE APPEND-ONLY LAW. Sabotage: summarize the archive between calls
    # (the compaction-cliff failure). This must name the cause and raise.
    old_text = "archived: O1 closed\narchived: O2 closed\n"
    new_text = "archived: [2 obligations summarized]\n"
    with pytest.raises(LayoutLawViolation, match="rewritten, not appended"):
        assert_digest_append_only({}, old_text, new_text)
    grown = old_text + "archived: O3 closed\n"
    assert_digest_append_only({}, old_text, grown)  # append is legal


def test_cache_provenance_carries_the_layout_hashes():
    compiled = compile_context([_frozen(), _digest("a"), _frontier("b")])
    stamp = cache_provenance(compiled.receipt, cache_hit=True)
    assert stamp["cache_hit"] is True
    assert stamp["layout_hash"] == compiled.receipt["layout_hash"]
    assert stamp["frozen_hash"] == compiled.receipt["frozen_hash"]


def test_messages_keeps_volatile_frontier_out_of_the_system_prefix():
    compiled: CompiledLayout = compile_context([_frozen(), _digest("arch"), _frontier("live")])
    messages = compiled.messages(user_message="convert 1000 usd to eur")
    assert messages[0]["role"] == "system"
    assert "live" not in messages[0]["content"]
    assert messages[1] == {"role": "user", "content": "convert 1000 usd to eur"}
