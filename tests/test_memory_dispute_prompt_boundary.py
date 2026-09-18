from __future__ import annotations

from core.context_namespace import ensure_chat_namespace
from core.human_input_adapter import HumanInputInterpretation
from core.identity_manager import load_active_persona
from core.memory.entries import add_memory_fact, resolve_memory_access_policy
from core.runtime_paths import configure_runtime_home
from core.task_router import classify, create_task_record
from core.tiered_context_loader import TieredContextLoader


def _load(chat_id: str, prompt: str):
    task = create_task_record(prompt)
    interpretation = HumanInputInterpretation(
        raw_text=prompt,
        normalized_text=prompt,
        reconstructed_text=prompt,
        intent_mode="request",
        topic_hints=[],
        reference_targets=[],
        understanding_confidence=0.95,
        quality_flags=[],
    )
    return TieredContextLoader().load(
        task=task,
        classification=classify(
            task.task_summary,
            context=interpretation.as_context(),
        ),
        interpretation=interpretation,
        persona=load_active_persona("default"),
        session_id=chat_id,
        source_context={"surface": "local", "platform": "desktop"},
    )


def test_disputed_values_are_replaced_by_a_clarification_constraint(
    tmp_path,
) -> None:
    configure_runtime_home(tmp_path)
    try:
        chat_id = "chat:dispute-prompt"
        ensure_chat_namespace(chat_id)
        policy = resolve_memory_access_policy(chat_id=chat_id)
        for value in ("Seattle", "Vancouver"):
            assert add_memory_fact(
                f"The office is in {value}.",
                session_id=chat_id,
                fact_key="location:office",
                access_policy=policy,
            )

        result = _load(chat_id, "Where is the office?")
        assembled = result.assembled_context()
        conflict_items = [
            item
            for item in result.relevant_items
            if item.source_type == "memory_conflict"
        ]

        assert len(conflict_items) == 1
        assert conflict_items[0].must_keep is True
        assert "Ask one concise clarification" in assembled
        assert "location:office" in assembled
        assert "Seattle" not in assembled
        assert "Vancouver" not in assembled
    finally:
        configure_runtime_home(None)
