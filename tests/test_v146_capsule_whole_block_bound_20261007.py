"""v14.6 triage (coordinator, 2026-10-07): the capsule's distilled laws now read the distilled section, so the whole
block needs a bound of its own. The block is header + distilled lines (the evidence target) + the receipts packet (its
own cap, reported as packet_tokens) + the verbatim whole-turn lane (its cap and the free window) + the wrapper. The verbatim whole-turn lane (ddb2d4e5) is sized by its own cap and never beyond the
free window the budget was resolved from, and the whole block (header, distilled lines, turn lane, wrapper) stays
within the evidence target plus the turn lane plus the header. Served store, no model call. Contributor: sls_0x."""
from __future__ import annotations

import pytest

import core.context_retrieval as cr
from core import context_capsule_v2 as capsule
from tests.test_capsule_match_strength_eviction_20261002 import (
    HARBOR_DECISIVE,
    HARBOR_FILLERS,
    _capsule,
    _seed,
    fresh_profile,
)

HEADER_SLACK_TOKENS = 220   # the distiller header, the turn-lane header and the wrapper lines


@pytest.mark.parametrize("target_tokens", [200, 340, 800])
def test_the_whole_block_never_exceeds_its_target_plus_the_capped_turn_lane(fresh_profile, target_tokens):
    chat = f"wb-harbor-{target_tokens}"
    _seed(fresh_profile, chat, "Ilse", HARBOR_FILLERS, HARBOR_DECISIVE)
    cr.reset_retrieval_telemetry()
    block = _capsule(fresh_profile, chat, "Which harbor does Ilse describe as the quietest one in Nordvik?", target_tokens)
    tel = dict(cr.get_last_retrieval_telemetry())
    budget = tel["evidence_budget"]
    turn_tokens = int(tel.get("whole_turn_tokens") or 0)
    assert turn_tokens <= cr._TURN_LANE_MAX_TOKENS, tel
    assert turn_tokens <= int(budget["free_tokens"]), tel
    whole = capsule.estimate_tokens(block)
    packet_tokens = int((tel.get("evidence_compiler") or {}).get("packet_tokens") or 0)   # the receipts packet, its own cap
    assert whole <= int(budget["resolved_target_tokens"]) + turn_tokens + packet_tokens + HEADER_SLACK_TOKENS, (whole, budget, turn_tokens, packet_tokens)


def test_the_turn_lane_reports_its_size(fresh_profile):
    chat = "wb-harbor-report"
    _seed(fresh_profile, chat, "Ilse", HARBOR_FILLERS, HARBOR_DECISIVE)
    cr.reset_retrieval_telemetry()
    _capsule(fresh_profile, chat, "Which harbor does Ilse describe as the quietest one in Nordvik?", 200)
    tel = dict(cr.get_last_retrieval_telemetry())
    assert tel.get("whole_turn_lines", 0) >= 1 and tel.get("whole_turn_tokens", 0) >= 1


def test_a_tight_free_window_bounds_the_turn_lane(fresh_profile):
    # a window with little room left: the verbatim turn lane must fit inside it, not inside its own 4500-token cap
    from core.memory.entries import resolve_memory_access_policy

    chat = "wb-harbor-tight"
    _seed(fresh_profile, chat, "Ilse", HARBOR_FILLERS, HARBOR_DECISIVE)
    from dataclasses import replace as _replace

    budget = capsule.resolve_budget(bucket="B", role="general", evidence_target_tokens=200)
    tight = _replace(budget, free_tokens=300)   # the same shape the packer uses when it reserves the header
    policy = resolve_memory_access_policy(chat_id=chat)
    cr.reset_retrieval_telemetry()
    question = "Which harbor does Ilse describe as the quietest one in Nordvik?"
    out = cr.inject_retrieved(chat, question, [{"role": "user", "content": question}], access_policy=policy,
                              source_context={"chat_id": chat, "runtime_home": fresh_profile}, budget=tight)
    tel = dict(cr.get_last_retrieval_telemetry())
    free = int(tel["evidence_budget"]["free_tokens"])
    assert free < 1000, tel["evidence_budget"]          # the window really is tight
    assert int(tel.get("whole_turn_tokens") or 0) <= free, tel
    block = "\n".join(str(m.get("content") or "") for m in out if "retrieved_context" in str(m.get("content")))
    packet_tokens = int((tel.get("evidence_compiler") or {}).get("packet_tokens") or 0)
    assert capsule.estimate_tokens(block) <= free + int(tel["evidence_budget"]["resolved_target_tokens"]) + packet_tokens + HEADER_SLACK_TOKENS
