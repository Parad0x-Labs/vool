"""Exposed SB07 is regression evidence: no prefix-erasing budget fallback."""
from dataclasses import replace

from tests.test_overnight_source_structure import _recall, source_env

BODY = 'Mirellanthia: The clock repair meeting was uneventful. I calibrated the escapement gauge to 0.57 mm before sealing the wooden chronometer case.'
ASK = 'What calibration did the escapement gauge use?'
FACT = 'I calibrated the escapement gauge to 0.57 mm before sealing the wooden chronometer case.'
PREFIX = 'Mirellanthia:'


def test_bound_source_is_not_downgraded_to_naked_anchor_under_character_budget(source_env, monkeypatch):
    from core import context_retrieval as cr
    from core.context_capsule_v2 import estimate_tokens, resolve_budget
    from core.context_namespace import ensure_chat_namespace
    from core.memory.entries import resolve_memory_access_policy
    chat = "bound-source-budget"
    ensure_chat_namespace(chat, grant_current_receipts=False)
    mem = cr._open_memory_for_runtime(str(source_env))
    source = mem.occurrence_store(chat_scope=chat, role="user", authority="observed-user-statement",
        body=BODY, recorded_at=1720000000, statement_at=1709251200)
    roomy = _recall(source_env, chat, ASK)
    lines = [line for line in roomy.splitlines() if FACT in line]
    bound = next(line for line in lines if PREFIX in line and "reported source prefix" in line)
    cap = max(1, len(bound)//4)
    with monkeypatch.context() as m:
        m.setattr(cr,"_CAPSULE_TARGET_TOKENS",cap)
        limited = _recall(source_env, chat, ASK)
        selected = cr.get_last_retrieval_telemetry().get("selected_facts",[])
        assert sum(len(line)+1 for line in selected) <= cap*4
        matching = [line for line in limited.splitlines() if "0.57" in line]
        assert not matching or any(FACT in line and PREFIX in line and "reported source prefix" in line for line in matching),limited
    # Final token-packer refusal is atomic too; it cannot split the label.
    header = cr._CAPSULE_FACTS_HEADER
    token_cap = estimate_tokens(header+"\n") + estimate_tokens(bound) -1
    budget = replace(resolve_budget(bucket="B", role="general"), free_tokens=token_cap, min_score=0)
    result = cr.inject_retrieved(chat, ASK,[{"role":"user","content":ASK}],budget=budget,
        access_policy=resolve_memory_access_policy(chat_id=chat),source_context={"chat_id":chat,"runtime_home":str(source_env)})
    capsule="\n".join(str(x.get("content")or "") for x in result if "<retrieved_context>" in str(x.get("content")or ""))
    assert "0.57" not in capsule or (FACT in capsule and PREFIX in capsule and "reported source prefix" in capsule)
    assert mem.occurrence_get(source.occurrence_id).body == BODY
