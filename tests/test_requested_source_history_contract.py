"""The existing request owners govern explicit historical source selection. Contributor: sls_0x."""
from __future__ import annotations

import pytest

from core import context_retrieval as cr
from core.context_namespace import ensure_chat_namespace
from core.memory.entries import resolve_memory_access_policy
from core.temporal_question_scope import question_time_scope
from tests.test_requested_source_authority_contract import _capsule, source_home

BODY=("Seven items in the park bench restoration kit:\n"
      "1. Cedar slats measuring 900 mm by 70 mm.\n"
      "2. A hand saw with a fine-tooth blade.\n"
      "3. A box of stainless M8 bolts.\n"
      "4. A tub of exterior wood oil.\n"
      "5. Instead of a mains extension cable, a 12 V battery pack.\n"
      '6. A printed inspection card asking, "Are both rear bolts tight?"\n'
      "7. A labelled tin for spare M8 washers.")
QUESTION="What seven items did you list in the park bench restoration kit earlier?"


def seed(home,body=BODY):
    ensure_chat_namespace("source-history-contract",grant_current_receipts=False)
    result=cr.store_turn("source-history-contract","What would you include in a seven-item park bench restoration kit?",body,
        access_policy=resolve_memory_access_policy(chat_id="source-history-contract"),source_context={"runtime_home":home})
    assert result["status"] in {"stored","retained"} and len(result["occurrence_ids"])==2


@pytest.mark.parametrize("question",[QUESTION,
    "Which seven items did you recommend for the park bench restoration kit?",
    "Which seven components did you suggest for the park bench restoration kit?"])
def test_auxiliary_history_collection_reaches_real_source_selection(source_home,question):
    assert question_time_scope(question).past_subject=="assistant"
    seed(source_home)
    capsule=_capsule(source_home,question,chat="source-history-contract")
    assert BODY in capsule,{"question":question,"capsule":capsule,"telemetry":cr.get_last_retrieval_telemetry()}
    refs=cr.get_last_retrieval_telemetry().get("evidence_refs",[])
    assert any(r.get("delivered") and r.get("source_preserving") and r.get("span",{}).get("text")==BODY for r in refs)


@pytest.mark.parametrize("question",[
    "Which seven items would you list in the park bench restoration kit now?",
    "What seven items did I list in the park bench restoration kit earlier?",
    "What seven items did Mira list in the park bench restoration kit earlier?",
    'Explain the sentence "What seven items did you list in the park bench restoration kit earlier?".',
])
def test_cardinality_alone_does_not_turn_current_other_actor_or_quote_into_assistant_source_request(source_home,question):
    # A quotation may contain historical grammar; cardinality authority must
    # still exclude that quoted example from this request.
    if not question.startswith("Explain the sentence"):
        assert question_time_scope(question).past_subject!="assistant"
    seed(source_home)
    _capsule(source_home,question,chat="source-history-contract")
    refs=cr.get_last_retrieval_telemetry().get("evidence_refs",[])
    assert not any(r.get("source_preserving") and r.get("role")=="assistant" for r in refs)


def test_auxiliary_history_collection_keeps_episode_bound(source_home):
    wrong="Seven items in the lighthouse inspection kit:\n"+"\n".join(f"{i}. Lighthouse inspection marker {i}." for i in range(1,8))
    seed(source_home,BODY+"\n\n"+wrong)
    capsule=_capsule(source_home,QUESTION,chat="source-history-contract")
    assert BODY in capsule
    assert "Lighthouse inspection marker" not in capsule
