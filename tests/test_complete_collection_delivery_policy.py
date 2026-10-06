"""Complete collection intent owns evidence allowance and delivered answer shape."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import bootstrap_context as bc
from core import context_retrieval as cr
from core import ordinary_chat_response_guard as guard
from core import prompt_normalizer as normalizer
from core.context_capsule_v2 import estimate_tokens, resolve_budget
from core.runtime_paths import configure_runtime_home
from tests.test_recall_evidence_merge_law_20260929 import _live
from tests.test_requested_source_unit_completeness import plan

MANIFEST_QUERY = "Give my full confirmed reading-room relocation manifest in dispatch order."
PLAN_QUERY = "Restate the complete printmaking bench plan you proposed."


def _policy(query):
    return guard.ordinary_chat_output_policy(
        prompt_profile="chat_capsule", output_mode="plain_text", user_text=query,
    )


def _generation(query):
    return normalizer._generation_profile(
        surface="api", task_kind="conversation", output_mode="plain_text",
        task_class="chat_conversation", user_text=query,
        history_messages=10, context_attached=True,
    )


def _capture_producer(monkeypatch, query, base):
    monkeypatch.setenv("VOOL_CONTEXT_CAPSULE_V2", "1")
    monkeypatch.setattr(bc, "_assemble_runtime_transcript", lambda **kwargs: (base, "authorized_test"))
    calls = []
    def inject(chat, question, messages, **kwargs):
        calls.append((chat, question, messages, kwargs))
        return messages
    monkeypatch.setattr(cr, "inject_retrieved", inject)
    marker = {"source": "prior_turn", "evidence_refs": [{"id": "sentinel"}]}
    cr._set_retrieval_telemetry(marker)
    before = cr.get_last_retrieval_telemetry()
    source = {
        "chat_id": "must-not-replace-session",
        "runtime_home": "/authorized/test/profile",
        "question_as_of": "2026-07-03T12:00:00Z",
        "private_profile": "must-not-be-forwarded",
    }
    transcript, origin = bc.canonical_runtime_transcript(
        session_id="authorized-chat", source_context=source, current_user_text=query,
    )
    assert transcript == base
    assert origin == "authorized_test"
    after = cr.get_last_retrieval_telemetry()
    # Canonical assembly owns the additive admitted-capsule record, while
    # the pre-existing turn retrieval fields remain unchanged.
    assert {k: v for k, v in after.items() if k != "last_admitted_capsule"} == {
        k: v for k, v in before.items() if k != "last_admitted_capsule"
    }
    assert len(calls) == 1
    return calls[0]


def test_complete_producer_reserves_existing_transcript_and_output(monkeypatch):
    base = [{"role": "assistant", "content": "Previous ordinary turn. " * 40}]
    chat, query, messages, kwargs = _capture_producer(monkeypatch, MANIFEST_QUERY, base)
    assert chat == "authorized-chat" and query == MANIFEST_QUERY
    assert messages[-1] == {"role": "user", "content": MANIFEST_QUERY}
    budget = kwargs.get("budget")
    assert budget is not None, "Complete collections still use the ordinary default allowance."
    expected = resolve_budget(
        bucket="B", role="general", output_reserve_tokens=2048,
        transcript_tokens=estimate_tokens(json.dumps(messages, ensure_ascii=False)),
        pin_ceiling_frac=0, recent_floor_frac=0,
        evidence_target_tokens=2048, retrieval_ceiling_tokens=2048,
    )
    assert budget == expected
    assert budget.evidence_target_tokens == 2048
    assert budget.pin_ceiling_tokens == budget.recent_floor_tokens == 0


@pytest.mark.parametrize("query", [MANIFEST_QUERY, "Where is the reading-room trolley?"])
def test_producer_forwards_only_established_store_and_temporal_context(monkeypatch, query):
    _, _, _, kwargs = _capture_producer(monkeypatch, query, [])
    assert kwargs["source_context"] == {
        "chat_id": "authorized-chat",
        "runtime_home": "/authorized/test/profile",
        "question_as_of": "2026-07-03T12:00:00Z",
    }


def test_ordinary_lookup_keeps_default_evidence_allocation(monkeypatch):
    _, _, _, kwargs = _capture_producer(monkeypatch, "Where is the reading-room trolley?", [])
    assert "budget" not in kwargs
    assert resolve_budget(bucket="B", role="general").evidence_target_tokens == 420


def test_transcript_capacity_reduces_collection_allowance(monkeypatch):
    base = [{"role": "assistant", "content": "Already reserved dialogue. " * 640}]
    _, _, _, kwargs = _capture_producer(monkeypatch, MANIFEST_QUERY, base)
    budget = kwargs.get("budget")
    assert budget is not None
    assert budget.evidence_target_tokens == 0
    assert budget.usable_tokens == 0


@pytest.mark.parametrize("query", [MANIFEST_QUERY, PLAN_QUERY])
def test_complete_delivery_has_no_ordinary_word_clip(query):
    policy = _policy(query)
    answer = "\n".join(
        f"{i}. Move crate {i} from the reading room to its assigned rack after checking the recorded seal."
        for i in range(1, 39)
    )
    assert policy["detail_requested"] == "true"
    assert policy["max_words"] == ""
    assert guard.constrain_ordinary_chat_output(answer, policy) == answer
    verdict = guard.inspect_ordinary_chat_output(answer, policy, current_user_text=query)
    assert verdict.allowed, verdict
    assert "at most 64 words" not in guard.ordinary_chat_retry_instruction(policy)


@pytest.mark.parametrize("query", [MANIFEST_QUERY, PLAN_QUERY])
def test_complete_generation_and_intent_share_bounded_allowance(query):
    profile = _generation(query)
    assert profile["profile_id"] == "chat_complete_collection"
    assert profile["max_output_tokens"] == 2048
    assert profile["output_budget_intent"] == {
        "output_mode": "plain_text", "base": 2048, "floor": 2048,
        "ceiling": 2048, "reason": "complete_collection",
    }


@pytest.mark.parametrize("query", [
    "Where is the reading-room trolley?",
    "What was the seventh item in the list you provided?",
    "Is the short notebook on the rack?",
])
def test_ordinary_lookup_generation_and_word_defaults_remain(query):
    assert _policy(query)["max_words"] == ("" if "list" in query else "64")
    profile = _generation(query)
    assert profile["profile_id"] == "chat_plain_text"
    assert profile["max_output_tokens"] <= 520
    assert profile["output_budget_intent"]["ceiling"] == 520


@pytest.mark.parametrize("suffix,words", [
    ("Answer in exactly three words.", 3),
    ("Answer with one word.", 1),
    ("Use at most 20 words.", 20),
    ("Give a 12-word summary.", 12),
    ("Keep the answer brief.", 64),
    ("Summarize it in one sentence.", 64),
    ("Keep it to one sentence.", 64),
])
def test_explicit_short_collection_contract_overrides_complete_output(suffix, words):
    query = MANIFEST_QUERY + " " + suffix
    policy = _policy(query)
    assert policy["max_words"] == str(words)
    assert policy["detail_requested"] == "false"
    profile = _generation(query)
    assert profile["profile_id"] == "chat_short_collection"
    assert profile["max_output_tokens"] <= 520
    assert profile["output_budget_intent"]["ceiling"] == profile["max_output_tokens"]


def test_exact_literal_output_contract_stays_first():
    query = MANIFEST_QUERY + " Reply with exactly GREEN LOOP and nothing else."
    profile = _generation(query)
    assert profile["profile_id"] == "chat_exact_plain_text"
    assert profile["max_output_tokens"] <= 32
    assert profile["stop_sequences"] == ["\n"]
    assert profile["output_budget_intent"]["reason"] == "exact_output_target"


@pytest.fixture
def retained_profile(tmp_path, monkeypatch):
    from core import embedding_service
    from storage.migrations import run_migrations
    home = Path(tmp_path) / "profile"
    home.mkdir()
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_CONTEXT_CAPSULE_V2", "1")
    monkeypatch.setattr(embedding_service, "_best_embed_model", lambda: None)
    configure_runtime_home(home)
    run_migrations()
    yield str(home)
    configure_runtime_home(None)


def test_retained_full_plan_reaches_producer_without_answer_truncation(retained_profile, monkeypatch):
    body = plan(size=38)
    _live(retained_profile, "full-plan-chat", [
        ("user", "Propose a printmaking bench plan."), ("assistant", body),
    ])
    # Semantic producer receives no already-present history copy of this plan.
    monkeypatch.setattr(bc, "_assemble_runtime_transcript", lambda **kwargs: ([], "authorized_test"))
    transcript, _ = bc.canonical_runtime_transcript(
        session_id="full-plan-chat",
        source_context={"chat_id": "full-plan-chat", "runtime_home": retained_profile},
        current_user_text=PLAN_QUERY,
    )
    capsule = "\n".join(m["content"] for m in transcript if m["role"] == "system")
    assert body in capsule, capsule
    assert "assistant said" in capsule
    assert _generation(PLAN_QUERY)["max_output_tokens"] == 2048
    assert guard.constrain_ordinary_chat_output(body, _policy(PLAN_QUERY)) == body


def test_retained_collection_is_atomic_when_transcript_consumes_capacity(retained_profile, monkeypatch):
    body = plan(size=38)
    _live(retained_profile, "full-plan-chat", [
        ("user", "Propose a printmaking bench plan."), ("assistant", body),
    ])
    base = [{"role": "assistant", "content": "Already reserved dialogue. " * 640}]
    monkeypatch.setattr(bc, "_assemble_runtime_transcript", lambda **kwargs: (base, "authorized_test"))
    transcript, _ = bc.canonical_runtime_transcript(
        session_id="full-plan-chat",
        source_context={"chat_id": "full-plan-chat", "runtime_home": retained_profile},
        current_user_text=PLAN_QUERY,
    )
    assert transcript == base
    assert not any("Stage 1:" in m["content"] for m in transcript)


@pytest.mark.parametrize("query", [
    "Restate the complete numbered plan.",
    "Give the entire multi-stage printmaking plan.",
    "Restate all of the 38 stages in the plan.",
    "Give each of my recorded steps in order.",
    "Restate the seedling transfer plan in full.",
    "Give all cart fields with their recorded values.",
    "Restate the complete pamphlet-binding plan you proposed for my bench, keeping all numbered stages in order, and include the adhesive and hinge-gap requirements.",
])
def test_completeness_binds_to_collection_noun_phrase(query):
    assert cr.query_requests_complete_collection(query)
    assert _generation(query)["max_output_tokens"] == 2048


@pytest.mark.parametrize("query", [
    "Which stage uses full-sheet paper?",
    "Which step mentions all the exhibits?",
    "Which stage in the complete plan uses wheat paste?",
    "What was the field in the full manifest that recorded the seal?",
    "Is my full name in the manifest?",
    "Which row contains all the blue paper in the list?",
    "What was the seventh item in the list you provided?",
    "Give the first item from the complete list you provided.",
])
def test_item_lookup_does_not_become_complete_collection(query):
    assert not cr.query_requests_complete_collection(query)
    assert not cr._requested_collection_windows(query, plan(size=38))
    profile = _generation(query)
    assert profile["profile_id"] == "chat_plain_text"
    assert profile["max_output_tokens"] <= 520


@pytest.mark.parametrize("suffix", [
    "Use at most 20 words per item.",
    "Explain it in one sentence for each stage.",
    "Keep the one-word crate labels intact.",
])
def test_per_item_shape_does_not_shorten_whole_collection(suffix):
    query = MANIFEST_QUERY + " " + suffix
    assert _policy(query)["max_words"] == ""
    assert _generation(query)["max_output_tokens"] == 2048
