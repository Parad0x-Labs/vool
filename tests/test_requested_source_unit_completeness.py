"""A requested collection is one bounded source unit, never one lucky item."""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import context_retrieval as cr
from core.context_capsule_v2 import resolve_budget
from core.memory.entries import resolve_memory_access_policy
from core.runtime_paths import configure_runtime_home
from tests.test_recall_evidence_merge_law_20260929 import _live


def plan(subject="printmaking bench", size=38):
    return (f"For your own {subject}, I proposed this complete ordered plan. "
            "Use washable binder throughout. Keep a six-millimetre clearance at the hinge.\n"
            + "\n".join(f"{i}. Stage {i}: Lay the marked sheet on the clean board and inspect its numbered edge before proceeding."
                         for i in range(1, size+1)))


@pytest.fixture
def profile(tmp_path, monkeypatch):
    from core import embedding_service
    from storage.migrations import run_migrations
    home = Path(os.environ["VOOL_HOME"]) / tmp_path.name
    home.mkdir(parents=True)
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setattr(embedding_service, "_best_embed_model", lambda: None)
    configure_runtime_home(home)
    run_migrations()
    yield str(home)
    configure_runtime_home(None)


def capsule(home, chat, question, tokens=8192):
    budget=resolve_budget(bucket="D", role="heavy_reasoning", evidence_target_tokens=tokens,
                          retrieval_ceiling_tokens=tokens, output_reserve_tokens=2048)
    out=cr.inject_retrieved(chat, question, [{"role":"user", "content":question}],
        budget=budget, access_policy=resolve_memory_access_policy(chat_id=chat),
        source_context={"chat_id":chat, "runtime_home":home}, env={"VOOL_CONTEXT_CAPSULE_V2":"1"})
    return "\n".join(m["content"] for m in out if m["role"]=="system")


@pytest.mark.parametrize("subject,count", [("printmaking bench",38),("seedling transfer",11)])
def test_complete_requested_plan_survives_retention_and_budget(profile,subject,count):
    body=plan(subject,count)
    chat="complete-plan"
    _live(profile,chat,[("user",f"Propose a plan for my {subject}."),("assistant",body)])
    cap=capsule(profile,chat,f"Restate the complete {subject} plan you proposed, with all numbered stages in order.")
    assert body in cap, cap
    refs=cr.get_last_retrieval_telemetry()["evidence_refs"]
    assert any(r.get("source_complete") and r.get("delivered") for r in refs), refs
    assert "assistant said" in cap


def test_complete_unit_over_budget_is_refused_atomically(profile):
    body=plan()
    _live(profile,"too-large",[("user","Propose a printmaking bench plan."),("assistant",body)])
    cap=capsule(profile,"too-large","Restate the complete printmaking bench plan with all numbered stages.",80)
    assert "Stage 1:" not in cap and "Stage 38:" not in cap, cap
    assert not any(r.get("delivered") and r.get("role")=="assistant" for r in cr.get_last_retrieval_telemetry()["evidence_refs"]), cap


def test_complete_collection_selects_one_named_episode_not_both():
    body="Spring seedling transfer plan:\n1. Select blue trays.\n2. Keep roots damp.\n\nAutumn seedling transfer plan:\n1. Select red pots.\n2. Protect from frost."
    windows=cr._structured_source_windows("Restate the complete autumn seedling transfer plan, including every stage.",body)
    text="\n".join(str(w["text"]) for w in windows)
    assert "red pots" in text and "Protect from frost" in text, text
    assert "blue trays" not in text, text


def test_named_item_lookup_keeps_existing_narrow_representation():
    body=plan()
    windows=cr._evidence_clause_windows("What was the 27th item in the printmaking bench list you provided?",body)
    assert windows
    assert "27. Stage 27" in windows[0]["text"]
    assert "28. Stage 28" not in windows[0]["text"]


@pytest.mark.parametrize("role,body", [
    ("user", "USER: My bench plan is approved.\nASSISTANT: Her own bench uses red trays."),
    ("user", "I plan to use the blue trays. Perhaps I will switch to red pots."),
])
def test_complete_source_expansion_never_promotes_mixed_or_quoted_ownership(role,body):
    source=SimpleNamespace(body=body,role=role,status="active",body_integrity="verified")
    assert cr._complete_source_window(source,"blue trays",max_chars=32768) is None


def test_separate_topics_cannot_jointly_cover_one_event(profile, monkeypatch):
    _live(profile,"workshop",[("user","My photography magazine arrived yesterday."),
        ("user","I enjoyed the gardening workshop at the town library."),
        ("user","Session date: 2023/11/01\nI went to a three-day photography workshop in the nearby city today where I learned different techniques. Can you explain dark frames and how they help in stacking?")])
    original = cr._distill_retrieved_hits
    def unrelated_distilled(*args, **kwargs):
        _text, telemetry = original(*args, **kwargs)
        telemetry["selected_facts"] = ["- user said: My photography magazine arrived yesterday.",
                                     "- user said: I enjoyed the gardening workshop at the town library."]
        telemetry["source_unit_refs"] = []
        return "\n".join(telemetry["selected_facts"]), telemetry
    monkeypatch.setattr(cr, "_distill_retrieved_hits", unrelated_distilled)
    cap=capsule(profile,"workshop","How many months ago did I attend the photography workshop?",8192)
    assert "three-day photography workshop" in cap, cap


@pytest.mark.parametrize("query,expected", [
    ("Give my full relocation manifest in dispatch order.", True),
    ("Restate the complete seedling transfer plan.", True),
    ("Give each item in the confirmed list with its details.", True),
    ("What was the seventh item in the list you provided?", False),
    ("Where is the seedling trolley kept?", False),
])
def test_shared_complete_collection_signal(query, expected):
    assert cr.query_requests_complete_collection(query) is expected


def test_named_person_sections_stay_separate():
    body = "Lina's seedling transfer plan:\n1. Use blue trays.\n2. Shade the roots.\n\nOmar's seedling transfer plan:\n1. Use red pots.\n2. Water at dusk."
    windows = cr._requested_collection_windows("Restate Omar's complete seedling transfer plan.", body)
    assert len(windows) == 1
    assert "red pots" in windows[0]["text"] and "Water at dusk" in windows[0]["text"]
    assert "blue trays" not in windows[0]["text"]
    assert body[windows[0]["start"]:windows[0]["end"]] == windows[0]["text"]


def test_complete_table_preserves_every_row_and_header():
    body = "Confirmed cargo manifest:\n| Cargo | Destination | Count |\n|---|---|---|\n| Linen | North | 12 |\n| Wool | South | 7 |\n| Canvas | West | 19 |"
    windows = cr._requested_collection_windows("Give the full confirmed cargo manifest with every row.", body)
    assert len(windows) == 1
    assert windows[0]["text"] == body


@pytest.mark.parametrize("status,integrity", [("deleted", "verified"), ("active", "mismatch")])
def test_source_unit_cannot_resurrect_or_ignore_failed_digest(status, integrity):
    body = plan(size=3)
    source = SimpleNamespace(body=body, role="assistant", status=status, body_integrity=integrity)
    assert not cr._source_unit_safe(source, body)


def test_source_unit_preserves_pasted_role_authority():
    body = "ASSISTANT: Here is the complete seedling transfer plan:\n1. Use blue trays.\n2. Water at dawn."
    source = SimpleNamespace(body=body, role="user", status="active", body_integrity="verified")
    assert not cr._source_unit_safe(source, body)


@pytest.mark.parametrize("lines,covered", [
    (["- user said: I am joining a photography community.",
      "- user said: I attended the gardening workshop."], False),
    (["- user said: I attended the photography workshop."], True),
    (["- assistant said: Photography workshop, noted."], False),
    (["- user said: I have not bought any telescope."], True),
])
def test_coverage_is_one_assertion_and_preserves_user_negation(lines, covered):
    terms = {"telescope"} if "telescope" in lines[0] else {"photography", "workshop"}
    assert cr._assertion_query_terms_covered(terms, lines, terms) is covered
