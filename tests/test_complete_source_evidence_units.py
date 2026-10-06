"""Source completeness and final-pack truth over the real retention/retrieval seam."""
from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest

from core import context_retrieval as cr
from core.context_capsule_v2 import resolve_budget
from core.runtime_paths import configure_runtime_home
from tests.test_recall_evidence_merge_law_20260929 import _capsule, _live


@pytest.fixture
def fresh_profile(tmp_path, monkeypatch):
    from core import embedding_service
    from storage.migrations import run_migrations

    home = tmp_path / "profile"
    home.mkdir()
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_CONTEXT_CAPSULE_V2", "1")
    monkeypatch.setattr(embedding_service, "_best_embed_model", lambda: None)
    configure_runtime_home(home)
    run_migrations()
    yield str(home)
    configure_runtime_home(None)


@pytest.mark.parametrize("text,question", [
    (
        "Session date: 2025/03/11\nElise: I visited the footbridge renewal forum. "
        "It was moving because dozens of volunteers offered to restore footpaths for residents. "
        "That convinced me to join the restoration group.",
        "What did Elise see at the footbridge renewal forum?",
    ),
    (
        "Session date: 2025/04/17\nRavi: The conservatory grant enabled repairs and new ventilation. "
        "That made the learning space safer and less humid for the students.",
        "How did the grant help Ravi's conservatory?",
    ),
    (
        "Session date: 2025/04/17\nImani: I attended the workshop briefing. We waited through "
        "several routine notices. The grant had repaired the extractor and installed new filters. "
        "The apprentices could finally sand timber without breathing sawdust. That persuaded me to volunteer.",
        "What did Imani hear at the workshop briefing?",
    ),
])
def test_short_source_is_one_complete_attributed_unit(fresh_profile, text, question):
    chat = "complete-source"
    _live(fresh_profile, chat, [("user", text)])
    cap = _capsule(fresh_profile, chat, question)
    receipts = [r for r in cr.get_last_retrieval_telemetry()["evidence_refs"] if r.get("delivered")]
    assert receipts, cap
    complete = [r for r in receipts if r.get("source_complete")]
    assert complete, receipts
    mem = cr._open_memory_for_runtime(fresh_profile)
    try:
        for receipt in complete:
            source = mem.occurrence_get(receipt["occurrence_id"])
            assert receipt["span"]["text"] == source.body
            assert source.body in cap
            assert receipt["span"]["start"] == 0
            assert receipt["span"]["end"] == len(source.body)
            assert receipt["source_digest"] == hashlib.sha256(source.body.encode("utf-8")).hexdigest()
    finally:
        mem.close()


@pytest.mark.parametrize("body", [
    "The grant had repaired the extractor. The apprentices could finally sand timber if funding arrived.",
    "We plan to repair the extractor. The apprentices could finally sand timber without dust.",
    "The grant had repaired the extractor. Perhaps the apprentices could finally sand timber without dust.",
    "The grant had repaired the extractor. Could the apprentices finally sand timber without dust?",
    "The grant had repaired the extractor. The apprentices could sand timber without dust.",
])
def test_complete_source_capacity_exception_does_not_expand_speculation(body):
    from types import SimpleNamespace

    source = SimpleNamespace(body=body, role="user", status="active", body_integrity="verified")
    assert cr._complete_source_window(source, "the extractor", max_chars=1680) is None


def test_delivery_receipts_and_selected_facts_follow_final_pack(fresh_profile, monkeypatch):
    chat = "final-pack"
    _live(fresh_profile, chat, [("user", "The ceramics society meets at Alder Hall on Wednesdays. "
                               "The organiser is Imani and the fee is 32 euros.")])
    real_pack = cr.pack_context

    def drop_final_pack(*args, **kwargs):
        packed = real_pack(*args, **kwargs)
        return replace(packed, blocks=(), render_block="", tokens_used=0, chosen=0,
                       dropped_budget=packed.dropped_budget+packed.chosen)

    monkeypatch.setattr(cr, "pack_context", drop_final_pack)
    assert not _capsule(fresh_profile, chat, "Where does the ceramics society meet?")
    telemetry = cr.get_last_retrieval_telemetry()
    assert not telemetry["selected_facts"]
    assert not cr.admitted_evidence_records(telemetry)
    rejected = [r for r in telemetry["evidence_refs"] if r.get("line")]
    assert rejected
    assert all(not r["delivered"] and r["omission_reason"] == "final_pack" for r in rejected)


def test_complete_source_never_overflows_small_headroom(fresh_profile):
    from core.memory.entries import resolve_memory_access_policy
    chat = "bounded-units"
    text = "The kiln workshop is at Willow Lodge. " + "The ventilation briefing is compulsory. "*10
    _live(fresh_profile, chat, [("user", text)])
    question = "Where is the kiln workshop?"
    budget = replace(resolve_budget(bucket="B", role="general"), free_tokens=80)
    out = cr.inject_retrieved(
        chat, question, [{"role":"user", "content":question}],
        budget=budget, access_policy=resolve_memory_access_policy(chat_id=chat),
        source_context={"chat_id":chat,"runtime_home":fresh_profile},
        env={"VOOL_CONTEXT_CAPSULE_V2":"1"},
    )
    telemetry = cr.get_last_retrieval_telemetry()
    assert telemetry["estimated_distilled_tokens"] <= budget.free_tokens
    cap = "\n".join(m["content"] for m in out if m["role"] == "system")
    for receipt in telemetry["evidence_refs"]:
        if receipt.get("delivered") and receipt.get("source_complete"):
            assert receipt["span"]["text"] in cap
            assert receipt["span"]["end"]-receipt["span"]["start"] == len(receipt["span"]["text"])
