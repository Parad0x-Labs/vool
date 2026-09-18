import uuid

from core.consensus_validator import decide_consensus_for_task
from core.discovery_index import get_spot_check_probability
from core.scoreboard_engine import get_peer_scoreboard
from storage.db import get_connection
from storage.migrations import run_migrations


def _peer_id(label: str) -> str:
    base = f"{label}_{uuid.uuid4().hex}"
    return base[:32] if len(base) > 32 else base.ljust(16, "x")

def setup_db():
    run_migrations()
    conn = get_connection()
    try:
        # Clear specific tables for clean state
        conn.execute("DELETE FROM peers")
        conn.execute("DELETE FROM scoreboard")
        conn.execute("DELETE FROM task_results")
        conn.execute("DELETE FROM task_reviews")
        conn.commit()
    finally:
        conn.close()

def seed_peer(peer_id: str, successful_shards: int, provider_score: float):
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO peers (peer_id, trust_score, successful_shards, last_seen_at, created_at, updated_at)
            VALUES (?, 0.8, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (peer_id, successful_shards)
        )
        conn.commit()
    finally:
        conn.close()

    if provider_score > 0:
        # Award initial score so they have something to lose
        # Simulate some previous task to give them score
        str(uuid.uuid4())
        # Not exact, but we can just insert delta directly
        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO scoreboard (entry_id, peer_id, score_type, delta, reason, created_at)
                VALUES (?, ?, 'provider', ?, 'seed', CURRENT_TIMESTAMP)
                """,
                (str(uuid.uuid4()), peer_id, provider_score)
            )
            conn.commit()
        finally:
            conn.close()

def seed_task_offer(task_id: str):
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO task_offers (
                task_id, parent_peer_id, capsule_id, task_type, subtask_type, summary,
                input_capsule_hash, required_capabilities_json, reward_hint_json, max_helpers,
                priority, deadline_ts, status, created_at, updated_at
            ) VALUES (?, 'mock_parent', 'mock_capsule', 'reasoning', 'generic', 'mock task',
                      'hash', '[]', '{}', 2, 'normal', '2030-01-01T00:00:00Z', 'open', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (task_id,)
        )
        conn.commit()
    finally:
        conn.close()

def insert_mock_result(
    task_id: str,
    helper_id: str,
    summary: str,
    confidence: float,
    helpfulness: float,
    quality: float,
    review_outcome: str
):
    result_id = str(uuid.uuid4())
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO task_results (result_id, task_id, helper_peer_id, result_type, summary, confidence, status, created_at, updated_at)
            VALUES (?, ?, ?, 'text', ?, ?, 'submitted', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (result_id, task_id, helper_id, summary, confidence)
        )
        conn.execute(
            """
            INSERT INTO task_reviews (review_id, task_id, helper_peer_id, reviewer_peer_id, outcome, helpfulness_score, quality_score, harmful_flag, created_at)
            VALUES (?, ?, ?, 'local_node', ?, ?, ?, 0, CURRENT_TIMESTAMP)
            """,
            (str(uuid.uuid4()), task_id, helper_id, review_outcome, helpfulness, quality)
        )
        conn.commit()
    finally:
        conn.close()

def test_anti_cheat():
    setup_db()
    print("--- Testing Phase 28: Automated Anti-Cheat System ---")

    # 1. Test Spot Check Probabilities
    new_node = _peer_id("new_node")
    trusted_node = _peer_id("trusted_node")
    elite_node = _peer_id("elite_node")
    seed_peer(new_node, 0, 0)
    seed_peer(trusted_node, 150, 50)
    seed_peer(elite_node, 1200, 5000)

    prob_new = get_spot_check_probability(new_node)
    prob_trusted = get_spot_check_probability(trusted_node)
    prob_elite = get_spot_check_probability(elite_node)

    print(f"Probabilities - New: {prob_new}, Trusted: {prob_trusted}, Elite: {prob_elite}")
    assert prob_new == 1.0, "New node should have 100% check"
    assert prob_trusted == 0.20, "Trusted node should have 20% check"
    assert prob_elite == 0.01, "Elite node should have 1% check"

    # 2. Test Spot Check Success (both agree)
    task_success = str(uuid.uuid4())
    seed_task_offer(task_success)
    node_a = _peer_id("node_a")
    node_b = _peer_id("node_b")
    seed_peer(node_a, 100, 100)
    seed_peer(node_b, 100, 100)
    # Two nodes give same answer with high quality
    insert_mock_result(task_success, node_a, "The answer is 42.", 1.0, 1.0, 1.0, "")
    insert_mock_result(task_success, node_b, "The answer is 42.", 1.0, 1.0, 1.0, "")

    decision = decide_consensus_for_task(task_success)
    print(f"Success Consensus Decision: {decision.action} ({decision.reason})")
    assert decision.action == "winner_selected", "Should pick winner if perfectly matching spot-check"

    # 3. Test Catastrophic Slashing
    task_slash = str(uuid.uuid4())
    seed_task_offer(task_slash)
    cheating_elite = _peer_id("cheating_elite")
    honest_node = _peer_id("honest_node")
    seed_peer(cheating_elite, 1500, 8000.0) # Very high provider score
    board_before = get_peer_scoreboard(cheating_elite)
    print(f"Cheater Score Before: {board_before.provider}")

    # Honest node gives good answer
    seed_peer(honest_node, 500, 1000)
    insert_mock_result(task_slash, honest_node, "A highly detailed, correct analysis of the smart contract vulnerability.", 1.0, 1.0, 1.0, "")

    # Cheating Elite node gets caught providing garbage during their 1% spot check
    insert_mock_result(task_slash, cheating_elite, "bad bot completely garbage answer spam links", 0.1, 0.1, 0.1, "")

    decision_slash = decide_consensus_for_task(task_slash)
    print(f"Slash Consensus Decision: {decision_slash.action} ({decision_slash.reason})")

    board_after = get_peer_scoreboard(cheating_elite)
    print(f"Cheater Score After: {board_after.provider} (Trust: {board_after.trust})")

    assert board_after.provider <= 0, "Cheating elite node provider score must be zeroed out entirely"
    assert board_after.trust < 0, "Cheating elite node trust must be heavily negative"

    print("\nSUCCESS! Progressive trust spot-checks and automated catastrophic slashing are fully functional.")

if __name__ == "__main__":
    test_anti_cheat()
