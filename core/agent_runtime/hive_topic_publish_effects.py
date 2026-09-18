from __future__ import annotations

import contextlib
from typing import Any

from core.autonomous_topic_research import research_topic_from_signal
from core.credit_ledger import escrow_credits_for_task, get_credit_balance
from core.hive_activity_tracker import set_hive_interaction_state
from network import signer as signer_mod


def build_hive_topic_created_response(
    *,
    title: str,
    topic_id: str,
    topic_tags: list[str],
    variant: str,
    pending: dict[str, Any],
    estimated_cost: float,
) -> str:
    tag_suffix = f" Tags: {', '.join(topic_tags[:6])}." if topic_tags else ""
    variant_suffix = (
        f" Using {variant or 'improved'} draft."
        if dict(pending.get('variants') or {}).get("original")
        else ""
    )
    response = f"Created Hive task `{title}` (#{topic_id[:8]}).{tag_suffix}{variant_suffix}"
    if estimated_cost <= 0:
        return response
    return response


def maybe_reserve_hive_credits(
    *,
    response: str,
    estimated_cost: float,
    topic_id: str,
) -> str:
    if estimated_cost <= 0:
        return response
    peer_id = signer_mod.get_local_peer_id()
    if escrow_credits_for_task(
        peer_id,
        topic_id,
        estimated_cost,
        receipt_id=f"hive_task_escrow:{topic_id}",
    ):
        return (
            f"{response} Reserved {estimated_cost:.1f} credits for Hive payouts. "
            f"Remaining balance: {get_credit_balance(peer_id):.2f}."
        )
    return (
        f"{response} No credits were reserved because your current balance is "
        f"{get_credit_balance(peer_id):.2f}."
    )


def maybe_start_hive_auto_research(
    agent: Any,
    *,
    response: str,
    topic_id: str,
    title: str,
    session_id: str,
    source_context: dict[str, object] | None,
    auto_start_research: bool,
    research_topic_from_signal_fn: Any = research_topic_from_signal,
) -> str:
    with contextlib.suppress(Exception):
        agent.hive_activity_tracker.note_watched_topic(session_id=session_id, topic_id=topic_id)
    if not auto_start_research:
        return response
    signal = {"topic_id": topic_id, "title": title}
    agent._sync_public_presence(status="busy", source_context=source_context)
    research_result = research_topic_from_signal_fn(
        signal,
        public_hive_bridge=agent.public_hive_bridge,
        curiosity=agent.curiosity,
        hive_activity_tracker=agent.hive_activity_tracker,
        session_id=session_id,
        auto_claim=True,
    )
    if research_result.ok:
        set_hive_interaction_state(
            session_id,
            mode="hive_task_active",
            payload={
                "active_topic_id": topic_id,
                "active_title": title,
                "claim_id": str(research_result.claim_id or "").strip(),
            },
        )
        response = f"{response} Started Hive research on `{title}`."
        if research_result.claim_id:
            response = f"{response} Claim `{str(research_result.claim_id)[:8]}` is active."
        return response
    failure_text = str(research_result.response_text or "").strip()
    if failure_text:
        return f"{response} The task is live, but starting research failed: {failure_text}"
    return response
