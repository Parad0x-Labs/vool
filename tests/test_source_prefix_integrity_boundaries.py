"""Formatting labels require exact verified source slices and known roles."""
from types import SimpleNamespace
from dataclasses import replace
from tests.test_overnight_source_structure import source_env, _store, _recall


def test_unverified_legacy_unknown_and_transformed_sources_keep_old_rendering(source_env):
    from core.context_retrieval import _open_memory_for_runtime, _reported_source_prefix_receipt, _distill_retrieved_hits
    body = "Arlen: The hall has granite pillars. I carried 8 woven baskets."
    text = "I carried 8 woven baskets."
    start = body.index(text)
    _store(source_env, "legacy-prefix", body, "Recorded.")
    mem = _open_memory_for_runtime(str(source_env))
    source = next(o for o,s in mem.occurrence_search("baskets", chat_scope="legacy-prefix", limit=8) if o.role == "user")
    assert _reported_source_prefix_receipt(source, start, start+len(text), text)
    for variant in [replace(source, body_integrity="legacy-unverified"), replace(source, role="unknown")]:
        assert _reported_source_prefix_receipt(variant, start, start+len(text), text) is None
    assert _reported_source_prefix_receipt(source, start+1, start+len(text), text) is None
    assert _reported_source_prefix_receipt(source, start, start+len(text), text.replace("8", "9")) is None
    plain, _ = _distill_retrieved_hits("How many woven baskets were carried?", [(body, .9)])
    unknown, telemetry = _distill_retrieved_hits("How many woven baskets were carried?", [(body,.9)], record_sources=[source])
    assert unknown == plain and telemetry["reported_source_prefix_refs"] == []


def test_literal_annotation_inside_asserted_body_is_not_stripped_as_metadata():
    from core.context_retrieval import _without_reported_prefix_annotation
    text = 'I wrote [reported source prefix "Arlen:"] on the label.'
    assert _without_reported_prefix_annotation(text) == text
