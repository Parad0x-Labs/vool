from datetime import datetime, timedelta, timezone

from core.task_capsule import build_task_capsule
from sandbox.helper_worker import run_task_capsule
from storage.swarm_memory import get_recent_contexts


def test_swarm_learning():
    print("--- Testing Cooperative Swarm Learning (Phase 25) ---")

    # 1. Parent builds a task capsule that allows learning
    capsule = build_task_capsule(
        parent_agent_id="parent_alpha_uuid_1234567890",
        task_id="task_beta_999",
        task_type="research",
        subtask_type="search_docs",
        summary="User asked to find how to scale websockets.",
        sanitized_context={
            "problem_class": "networking",
            "abstract_inputs": ["websockets", "scaling", "redis pubsub"],
            "known_constraints": ["must be horizontal"],
            "environment_tags": {}
        },
        allowed_operations=["research", "summarize"],
        deadline_ts=datetime.now(timezone.utc) + timedelta(minutes=10),
        learning_allowed=True, # THIS IS THE MAGIC FLAG
        reward_hint={"points": 0, "wnull_pending": 0} # 0 reward, but should be accepted due to learning_allowed
    )

    # 2. Helper runs it
    outcome = run_task_capsule(capsule, helper_agent_id="idle_helper_gamma_456")
    print(f"Helper finished task. Result ID: {outcome.result.result_id}")

    # 3. Verify it was sniffed into the memory database!
    contexts = get_recent_contexts(limit=5)

    sniffed = None
    for ctx in contexts:
        if ctx["parent_peer_id"] == "parent_alpha_uuid_1234567890":
            sniffed = ctx
            break

    assert sniffed is not None, "Failed to find sniffed context in storage/swarm_memory.db!"

    print("\nVerified Sniffed Context from Swarm Memory DB:")
    print(f"  Parent ID: {sniffed['parent_peer_id']}")
    print(f"  Prompt JSON: {sniffed['prompt_json'][:80]}...")
    print(f"  Result JSON: {sniffed['result_json'][:80]}...")

    print("\nSUCCESS! The idle helper successfully harvested the context from the swarm.")

if __name__ == "__main__":
    test_swarm_learning()
