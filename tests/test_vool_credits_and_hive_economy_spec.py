from __future__ import annotations

import re
import uuid
from unittest import mock

import pytest

import network.signer as signer_mod
from core.credit_ledger import award_credits, get_credit_balance, get_escrow_for_task
from core.hive_activity_tracker import set_hive_interaction_state
from core.memory_first_router import ModelExecutionDecision
from storage.db import get_connection, reset_default_connection
from storage.migrations import run_migrations


@pytest.fixture(autouse=True)
def credit_runtime_reset() -> None:
    reset_default_connection()
    run_migrations()
    conn = get_connection()
    try:
        for table in (
            "compute_credit_ledger",
            "dispatch_credit_escrow",
            "swarm_dispatch_budget_events",
            "session_hive_watch_state",
        ):
            try:
                conn.execute(f"DELETE FROM {table}")
            except Exception:
                continue
        conn.commit()
    finally:
        conn.close()
    reset_default_connection()


@pytest.fixture
def stable_agent_peer(monkeypatch) -> str:
    peer_id = f"credit-peer-{uuid.uuid4().hex}"
    monkeypatch.setattr(signer_mod, "get_local_peer_id", lambda: peer_id)
    return peer_id


def test_credit_balance_uses_model_wording_over_real_current_credits(make_agent, stable_agent_peer):
    agent = make_agent()
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="credit-balance",
            provider_id="ollama:qwen",
            used_model=True,
            output_text="You currently have 12.00 compute credits available.",
            confidence=0.84,
            trust_score=0.84,
        )
    )
    peer_id = stable_agent_peer
    award_credits(peer_id, 12.0, "test_award", receipt_id="credit-balance-test")

    result = agent.run_once(
        "what is my credit balance?",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert get_credit_balance(peer_id) >= 12.0
    assert result["response_class"] == "utility_answer"
    assert result["model_execution"]["used_model"] is True
    assert "12.00 compute credits" in result["response"]


def test_credit_status_explains_current_reward_contract(make_agent, stable_agent_peer):
    del stable_agent_peer
    agent = make_agent()
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="credit-policy",
            provider_id="ollama:qwen",
            used_model=True,
            output_text=(
                "Plain public Hive posts do not mint credits by themselves. "
                "Credits and provider score come from rewarded assist tasks and accepted results."
            ),
            confidence=0.84,
            trust_score=0.84,
        )
    )

    result = agent.run_once(
        "how do i earn hive credits?",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    lowered = result["response"].lower()
    assert result["response_class"] == "utility_answer"
    assert result["model_execution"]["used_model"] is True
    assert "plain public hive posts do not mint credits" in lowered
    assert "rewarded assist tasks and accepted results" in lowered


def test_chat_can_spend_credits_to_prioritize_hive_task(make_agent, stable_agent_peer):
    agent = make_agent()
    peer_id = stable_agent_peer
    assert award_credits(peer_id, 50.0, "priority_seed", receipt_id="priority-seed")
    assert get_credit_balance(peer_id) >= 50.0
    session_id = "openclaw:credit-priority-task"
    topic_id = "7d33994f-dd40-4a7e-b78a-f8e2d94fb702"
    set_hive_interaction_state(
        session_id,
        mode="hive_task_active",
        payload={"active_topic_id": topic_id, "active_title": "Priority task"},
    )

    result = agent.run_once(
        "spend 10 credits to prioritize the current Hive task",
        session_id_override=session_id,
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert result["response_class"] == "task_status"
    assert "reserved 10.00 credits" in result["response"].lower()
    assert topic_id[:8] in result["response"]
    escrow = get_escrow_for_task(topic_id)
    assert escrow is not None
    assert escrow["total_escrowed"] >= 10.0


def test_chat_can_transfer_credits_to_another_peer(make_agent, stable_agent_peer):
    agent = make_agent()
    peer_id = stable_agent_peer
    assert award_credits(peer_id, 20.0, "transfer_seed", receipt_id="transfer-seed")
    assert get_credit_balance(peer_id) >= 20.0

    result = agent.run_once(
        "send 5 credits to peer-remote-1 for helping on this task",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert result["response_class"] == "task_status"
    assert "sent 5.00 credits" in result["response"].lower()


def test_credit_status_reports_glory_score_and_tier(make_agent, stable_agent_peer):
    agent = make_agent()
    peer_id = stable_agent_peer
    award_credits(peer_id, 3.0, "score-glance", receipt_id="score-glance")

    result = agent.run_once(
        "what is my glory score and tier?",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    lowered = result["response"].lower()
    assert result["response_class"] == "utility_answer"
    assert "glory score" in lowered
    assert "tier" in lowered


def test_credit_status_can_list_recent_credit_receipts(make_agent, stable_agent_peer):
    agent = make_agent()
    peer_id = stable_agent_peer
    tag = uuid.uuid4().hex[:8]
    receipt_id = f"recent-receipt-{tag}"
    assert award_credits(peer_id, 4.25, "recent_receipt_seed", receipt_id=receipt_id)

    result = agent.run_once(
        "show my recent credit receipts",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    lowered = result["response"].lower()
    assert result["response_class"] == "utility_answer"
    assert "recent credit receipts" in lowered
    assert receipt_id[:24] in result["response"]
    assert "+4.25" in result["response"]


def test_hive_task_create_reserves_estimated_reward_pool(make_agent, stable_agent_peer):
    agent = make_agent()
    peer_id = stable_agent_peer
    session_id = f"openclaw:hive-credit-create-{uuid.uuid4().hex[:8]}"
    topic_id = f"topic-{uuid.uuid4().hex}"
    seed_receipt = f"hive-create-seed-{uuid.uuid4().hex[:8]}"
    assert award_credits(peer_id, 80.0, "hive_create_seed", receipt_id=seed_receipt)

    request_text = (
        "create new task in hive. "
        "Task: Design safer Hive payout receipts. "
        "Goal: Prove escrow and settlement work end to end for accepted tasks."
    )

    with mock.patch.object(agent.public_hive_bridge, "enabled", return_value=True), mock.patch.object(
        agent.public_hive_bridge, "write_enabled", return_value=True
    ), mock.patch.object(
        agent.public_hive_bridge,
        "create_public_topic",
        return_value={"ok": True, "status": "created", "topic_id": topic_id},
    ), mock.patch.object(
        agent.hive_activity_tracker,
        "note_watched_topic",
        return_value=None,
    ), mock.patch.object(
        agent,
        "_check_hive_duplicate",
        return_value=None,
    ):
        preview = agent.run_once(
            request_text,
            session_id_override=session_id,
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )
        confirm = agent.run_once(
            "yes",
            session_id_override=session_id,
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    reward_match = re.search(r"Estimated reward pool:\s*([0-9]+(?:\.[0-9])?) credits", preview["response"])
    assert reward_match, preview["response"]
    reserved_amount = float(reward_match.group(1))
    escrow = get_escrow_for_task(topic_id)
    assert escrow is not None
    assert escrow["total_escrowed"] == pytest.approx(reserved_amount)
    assert f"Reserved {reserved_amount:.1f} credits" in confirm["response"]
    assert topic_id[:8] in confirm["response"]


def test_escrow_lifecycle_poster_to_helper():
    """Poster escrows credits, helper earns from escrow, remainder refunds."""
    import uuid

    from storage.migrations import run_migrations

    run_migrations()

    from core.credit_ledger import (
        escrow_credits_for_task,
        get_escrow_for_task,
        refund_escrow_remainder,
        release_escrow_to_helper,
    )

    tag = uuid.uuid4().hex[:6]
    poster, helper = f"poster-{tag}", f"helper-{tag}"
    task_id = f"task-escrow-{tag}"

    award_credits(poster, 100.0, "seed", receipt_id=f"escrow-seed-{tag}")
    assert get_credit_balance(poster) == 100.0

    ok = escrow_credits_for_task(poster, task_id, 30.0)
    assert ok
    assert get_credit_balance(poster) == 70.0

    escrow = get_escrow_for_task(task_id)
    assert escrow is not None
    assert escrow["total_escrowed"] == 30.0
    assert escrow["remaining"] == 30.0

    release_escrow_to_helper(task_id, helper, 20.0)
    assert get_credit_balance(helper) == 20.0
    escrow = get_escrow_for_task(task_id)
    assert escrow["remaining"] == 10.0

    refunded = refund_escrow_remainder(task_id)
    assert refunded == 10.0
    assert get_credit_balance(poster) == 80.0
    escrow = get_escrow_for_task(task_id)
    assert escrow["status"] == "settled"


def test_transfer_credits_between_peers():
    import uuid

    from storage.migrations import run_migrations

    run_migrations()
    from core.credit_ledger import transfer_credits

    tag = uuid.uuid4().hex[:6]
    alice, bob = f"alice-{tag}", f"bob-{tag}"
    award_credits(alice, 50.0, "seed", receipt_id=f"xfer-seed-{tag}")
    assert transfer_credits(alice, bob, 15.0)
    assert get_credit_balance(alice) == 35.0
    assert get_credit_balance(bob) == 15.0
    assert not transfer_credits(alice, bob, 999.0)


def test_presence_credits_awarded():
    import uuid

    from storage.migrations import run_migrations

    run_migrations()
    from core.credit_ledger import award_presence_credits

    tag = uuid.uuid4().hex[:6]
    node = f"node-{tag}"
    assert award_presence_credits(node, 0.10, receipt_id=f"presence:{tag}:1")
    assert get_credit_balance(node) == 0.10
    assert not award_presence_credits(node, 0.10, receipt_id=f"presence:{tag}:1")
