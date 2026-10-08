"""Reported source labels are formatting data, never runtime ownership."""
from __future__ import annotations

import pytest

from tests.test_overnight_source_structure import _recall, _store, source_env

BODY = "Session date: 2024-02-10\nMira: The studio walls were painted white. Anyway, I tuned the brass gong to 294 Hz."
ASK = "What frequency did the brass gong use?"

def _audit(context, telemetry, home):
    from core.context_retrieval import _open_memory_for_runtime
    mem = _open_memory_for_runtime(str(home))
    refs = telemetry.get("reported_source_prefix_refs", [])
    assert refs, telemetry
    actual = [r for r in refs if r["line"] in context]
    assert actual, (context, refs)
    for r in actual:
        source = mem.occurrence_get(r["occurrence_id"])
        assert source is not None
        assert source.role == r["role"] and source.chat_scope == r["chat_scope"]
        for span in [r["span"], r["reported_source_prefix"]]:
            assert source.body[span["start"]:span["end"]] == span["text"], r
        assert r["reported_source_prefix"]["text"] == "Mira:"
        assert r["span"]["text"] in r["line"]
        assert "reported source prefix" in r["line"]
    import json
    print("SOURCE_BINDING_RECEIPT " + json.dumps({"context":context, "actual_receipts":actual, "bodies":{r["occurrence_id"]:mem.occurrence_get(r["occurrence_id"]).body for r in actual}}, ensure_ascii=False))
    return actual


def test_real_store_inject_keeps_reported_label_and_exact_separate_receipts(source_env):
    from core.context_retrieval import get_last_retrieval_telemetry
    _store(source_env, "gong-source", BODY, "Recorded.", 1707523200)
    context = _recall(source_env, "gong-source", ASK)
    assert "294 Hz" in context
    assert 'reported source prefix "Mira:"' in context, context
    refs = _audit(context, get_last_retrieval_telemetry(), source_env)
    assert all(r["role"] == "user" for r in refs)
    assert "2024-02-10" in context


def test_distilled_node_fact_and_evidence_fact_both_keep_label(source_env, monkeypatch):
    from core import context_retrieval as cr
    _store(source_env, "both-paths", BODY, "Recorded.")
    context = _recall(source_env, "both-paths", ASK)
    refs = _audit(context, cr.get_last_retrieval_telemetry(), source_env)
    assert any(r["delivery_path"] == "distilled" for r in refs), refs
    mem = cr._open_memory_for_runtime(str(source_env))
    monkeypatch.setattr(type(mem), "node_search_hybrid", lambda *args, **kwargs: [])
    context = _recall(source_env, "both-paths", ASK)
    refs = _audit(context, cr.get_last_retrieval_telemetry(), source_env)
    assert any(r["delivery_path"] == "evidence" for r in refs), refs
    for r in cr.get_last_retrieval_telemetry().get("evidence_refs", []):
        if r.get("delivered"):
            source = mem.occurrence_get(r["occurrence_id"])
            s = r["span"]
            assert source.body[s["start"]:s["end"]] == s["text"], r


@pytest.mark.parametrize("body", [
    '"Mira: The walls were white. I tuned the brass gong to 294 Hz."',
    'Mira: She quoted "Lina: I tuned the brass gong to 294 Hz."',
    'Mira: I tuned the brass gong to 294 Hz.\nLina: I tuned the brass gong to 318 Hz.',
    'The brass gong notes had no speaker label. I tuned the brass gong to 294 Hz.',
])
def test_ambiguous_or_quoted_or_unlabelled_sources_receive_no_annotation(source_env, body):
    _store(source_env, "ambiguous", body, "Recorded.")
    context = _recall(source_env, "ambiguous", ASK)
    assert "reported source prefix" not in context, context


def test_source_label_does_not_grant_foreign_chat_authority(source_env):
    _store(source_env, "foreign-prefix", BODY, "Recorded.")
    _store(source_env, "own-prefix", "My new palette is ochre.", "Recorded.")
    context = _recall(source_env, "own-prefix", ASK)
    assert "294 Hz" not in context and "Mira:" not in context, context


def test_assistant_reported_prefix_keeps_assistant_role(source_env):
    from core.context_retrieval import get_last_retrieval_telemetry
    _store(source_env, "assistant-prefix", "Please write a rehearsal note.", BODY)
    context = _recall(source_env, "assistant-prefix", "From our previous chat, what frequency did the brass gong use?")
    assert 'reported source prefix "Mira:"' in context, context
    refs = _audit(context, get_last_retrieval_telemetry(), source_env)
    assert all(r["role"] == "assistant" for r in refs)
    assert all(r["line"].startswith("- assistant said") for r in refs)


def test_added_prefix_is_in_atomic_fact_budget(source_env):
    from dataclasses import replace

    from core.context_capsule_v2 import estimate_tokens, resolve_budget
    from core.context_retrieval import get_last_retrieval_telemetry, inject_retrieved
    from core.memory.entries import resolve_memory_access_policy
    _store(source_env, "prefix-budget", BODY, "Recorded.")
    budget = replace(resolve_budget(bucket="B", role="general"), free_tokens=48)
    result = inject_retrieved("prefix-budget", ASK, [{"role":"user", "content":ASK}],
        budget=budget, access_policy=resolve_memory_access_policy(chat_id="prefix-budget"),
        source_context={"chat_id":"prefix-budget", "runtime_home":str(source_env)})
    for message in result:
        content = str(message.get("content") or "")
        if "<retrieved_context>" in content:
            inner = content.split("<retrieved_context>",1)[1].split("</retrieved_context>",1)[0].strip()
            assert estimate_tokens(inner) <= 48, (content, get_last_retrieval_telemetry())
            assert not ('reported source prefix "Mira:"' in inner and "294 Hz" not in inner)


def test_metadata_is_not_answer_coverage_or_arithmetic():
    from core.context_retrieval import _capsule_fact_body, _derived_total_line
    line = '- user said [reported source prefix "Mira:"]: I tuned the brass gong to 294 Hz.'
    assert "Mira" not in _capsule_fact_body(line)
    assert "I tuned the brass gong to 294 Hz." in _capsule_fact_body(line)
    plain = ['- user said: I carried 4 baskets.', '- user said: I carried 6 baskets.']
    tagged = [x.replace('user said:', 'user said [reported source prefix "Mira:"]:') for x in plain]
    assert _derived_total_line(tagged, {"baskets"}) == _derived_total_line(plain, {"baskets"})
