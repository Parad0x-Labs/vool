from __future__ import annotations

import uuid
from unittest import mock

from core.active_context_capsule import create_shadow_capsule
from core.context_namespace import ensure_chat_namespace
from core.human_input_adapter import HumanInputInterpretation
from core.identity_manager import load_active_persona
from core.task_router import classify, create_task_record
from core.tiered_context_loader import TieredContextLoader
from storage.db import get_connection


def _interpretation(text: str) -> HumanInputInterpretation:
    return HumanInputInterpretation(
        raw_text=text,
        normalized_text=text,
        reconstructed_text=text,
        intent_mode="request",
        topic_hints=[],
        reference_targets=[],
        understanding_confidence=0.9,
        quality_flags=[],
        needs_clarification=False,
        turn_id=None,
    )


def _load_context(chat_id: str, *, project_id: str = ""):
    ensure_chat_namespace(chat_id, project_id=project_id)
    text = "Explain the current context boundary."
    task = create_task_record(text)
    interpretation = _interpretation(text)
    return TieredContextLoader().load(
        task=task,
        classification=classify(
            task.task_summary,
            context=interpretation.as_context(),
        ),
        interpretation=interpretation,
        persona=load_active_persona("default"),
        session_id=chat_id,
        total_context_budget=1200,
    )


def test_shadow_capsule_telemetry_measures_selected_prompt_context() -> None:
    chat_id = f"shadow-audit-{uuid.uuid4().hex}"
    project_id = f"project-{uuid.uuid4().hex}"
    baseline = _load_context(chat_id, project_id=project_id)
    covered_claim = baseline.bootstrap_items[0].content
    capsule = create_shadow_capsule(
        chat_id=chat_id,
        project_id=project_id,
        user_text=covered_claim,
    )

    result = _load_context(chat_id, project_id=project_id)
    report = result.report
    serialized = report.to_dict()

    assert report.capsule_version == "none"
    assert report.shadow_capsule_version == capsule.version_id
    assert report.shadow_capsule_claim_count == 1
    assert report.shadow_capsule_claim_coverage_count == 1
    assert report.shadow_capsule_claim_coverage_ratio == 1.0
    assert report.shadow_capsule_used_in_prompt is False
    assert serialized["capsule_version"] == "none"
    assert serialized["shadow_capsule_version"] == capsule.version_id
    assert serialized["shadow_capsule_claim_count"] == 1
    assert serialized["shadow_capsule_claim_coverage_count"] == 1
    assert serialized["shadow_capsule_claim_coverage_ratio"] == 1.0
    assert serialized["shadow_capsule_used_in_prompt"] is False
    assert covered_claim not in str(
        {
            key: value
            for key, value in serialized.items()
            if key.startswith("shadow_capsule_")
        }
    )


def test_shadow_capsule_is_audited_but_never_injected() -> None:
    chat_id = f"shadow-no-inject-{uuid.uuid4().hex}"
    marker = f"SHADOW-ONLY-{uuid.uuid4().hex}"
    ensure_chat_namespace(chat_id)
    capsule = create_shadow_capsule(
        chat_id=chat_id,
        user_text=marker,
    )

    result = _load_context(chat_id)

    assert result.report.shadow_capsule_version == capsule.version_id
    assert result.report.shadow_capsule_claim_count == 1
    assert result.report.shadow_capsule_claim_coverage_count == 0
    assert result.report.shadow_capsule_claim_coverage_ratio == 0.0
    assert result.report.shadow_capsule_used_in_prompt is False
    assert result.report.capsule_version == "none"
    assert marker not in result.assembled_context()
    assert marker not in str(result.report.to_dict())


def test_missing_and_wrong_chat_shadow_capsules_fail_closed() -> None:
    missing_chat = f"shadow-missing-{uuid.uuid4().hex}"
    missing = _load_context(missing_chat)
    assert missing.report.shadow_capsule_version == "none"
    assert missing.report.shadow_capsule_claim_count == 0
    assert missing.report.shadow_capsule_claim_coverage_count == 0
    assert missing.report.shadow_capsule_claim_coverage_ratio == 0.0
    assert missing.report.shadow_capsule_used_in_prompt is False

    current_chat = f"shadow-current-{uuid.uuid4().hex}"
    other_chat = f"shadow-other-{uuid.uuid4().hex}"
    ensure_chat_namespace(current_chat)
    ensure_chat_namespace(other_chat)
    other_capsule = create_shadow_capsule(
        chat_id=other_chat,
        user_text="This belongs to another chat.",
    )
    with mock.patch(
        "core.tiered_context_loader.load_latest_capsule",
        return_value=other_capsule,
    ):
        wrong_chat = _load_context(current_chat)

    assert wrong_chat.report.shadow_capsule_version == "none"
    assert wrong_chat.report.shadow_capsule_claim_count == 0
    assert wrong_chat.report.shadow_capsule_used_in_prompt is False
    assert "This belongs to another chat." not in wrong_chat.assembled_context()


def test_corrupt_shadow_capsule_fails_closed_without_changing_context() -> None:
    chat_id = f"shadow-corrupt-{uuid.uuid4().hex}"
    marker = f"CORRUPT-SHADOW-{uuid.uuid4().hex}"
    ensure_chat_namespace(chat_id)
    capsule = create_shadow_capsule(
        chat_id=chat_id,
        user_text=marker,
    )
    conn = get_connection()
    try:
        conn.execute(
            "DROP TRIGGER IF EXISTS context_capsule_versions_no_update"
        )
        conn.execute(
            """
            UPDATE context_capsule_versions
            SET capsule_json = '{}'
            WHERE version_id = ?
            """,
            (capsule.version_id,),
        )
        conn.commit()
    finally:
        conn.close()

    result = _load_context(chat_id)

    assert result.report.shadow_capsule_version == "none"
    assert result.report.shadow_capsule_claim_count == 0
    assert result.report.shadow_capsule_claim_coverage_count == 0
    assert result.report.shadow_capsule_claim_coverage_ratio == 0.0
    assert result.report.shadow_capsule_used_in_prompt is False
    assert result.report.capsule_version == "none"
    assert marker not in result.assembled_context()
