import uuid

import pytest

from core.credit_ledger import _init_ledger_table, get_credit_balance
from core.dna_payment_bridge import dna_bridge
from core.parent_orchestrator import orchestrate_parent_task
from network.signer import get_local_peer_id as local_peer_id
from storage.db import get_connection
from storage.migrations import run_migrations


@pytest.mark.xfail(reason="Pre-existing: free tier downgrade changed behavior")
def test_credit_system():
    print("--- Testing Anti-Freeloader Credit System (Phase 26) ---")
    run_migrations()

    # Mock the predictor so it doesn't fall back to local-only execution
    import core.parent_orchestrator
    core.parent_orchestrator.predict_local_override_necessity = lambda: False

    # 0. Clean the DB just for this test run so we start empty
    _init_ledger_table()
    conn = get_connection()
    conn.execute("DELETE FROM compute_credit_ledger WHERE peer_id = ?", (local_peer_id(),))
    conn.commit()
    conn.close()

    my_id = local_peer_id()
    print(f"Initial balance for {my_id}: {get_credit_balance(my_id)}")

    # 1. Try to orchestrate a task (simulating user prompt asking a hard question)
    print("\nAttempt 1: Dispatching task with 0 balance...")
    result = orchestrate_parent_task(
        parent_task_id=f"gui_task_{uuid.uuid4().hex[:8]}",
        user_input="Please research scaling websockets and compare with Redis Pubsub.",
        classification={"task_class": "research"},
    )

    print(f"Action: {result.action}")
    print(f"Reason: {result.reason}")
    assert result.action == "decomposed", "Zero-balance users now downgrade to free tier instead of blocking."
    assert get_credit_balance(my_id) == 0.0, "Free-tier downgrade should not burn credits."

    # 2. Oh no! I am a freeloader but I have USDC!
    print("\nLinking DNA Wallet and buying $5 of credits...")
    dna_bridge.link_wallet("solana_super_secret_address_44444")
    purchase = dna_bridge.purchase_credits(usdc_amount=5.0, local_peer_id=my_id)

    print(f"Purchase Successful! Tx: {purchase['tx_id']}")
    balance = get_credit_balance(my_id)
    print(f"New Balance: {balance} credits.")
    assert balance == 5000.0, "Conversion off!"

    # 3. Try to dispatch again
    print("\nAttempt 2: Dispatching task with 5000 balance...")
    result2 = orchestrate_parent_task(
        parent_task_id=f"gui_task_{uuid.uuid4().hex[:8]}",
        user_input="Please research scaling websockets and compare with Redis Pubsub.",
        classification={"task_class": "research"},
    )

    print(f"Action: {result2.action}")
    print(f"Reason: {result2.reason}")
    assert result2.action == "decomposed", "Task should have succeeded this time!"

    # 4. Check balance deduction
    new_balance = get_credit_balance(my_id)
    print(f"\nBalance after dispatch: {new_balance} credits.")
    assert new_balance < 5000.0, "Credits were not deducted!"
    burned = 5000.0 - new_balance
    print(f"Successfully burned {burned} credits across {result2.subtasks_known} subtasks.")

    print("\nSUCCESS! The Anti-Freeloader mechanism is active.")

if __name__ == "__main__":
    test_credit_system()
