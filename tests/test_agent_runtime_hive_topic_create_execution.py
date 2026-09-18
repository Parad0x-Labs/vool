from __future__ import annotations

from types import SimpleNamespace

from core.agent_runtime import (
    hive_topic_create,
    hive_topic_publish_effects,
    hive_topic_publish_failures,
    hive_topic_publish_flow,
    hive_topic_publish_transport,
)


def test_hive_topic_create_execution_exports_stay_available_from_create_facade() -> None:
    assert hive_topic_create.execute_confirmed_hive_create is hive_topic_publish_flow.execute_confirmed_hive_create
    assert hive_topic_create.hive_topic_create_failure_text is hive_topic_publish_flow.hive_topic_create_failure_text


def test_hive_topic_create_failure_text_keeps_invalid_auth_copy() -> None:
    assert "rejected this runtime's write auth" in hive_topic_publish_flow.hive_topic_create_failure_text("invalid_auth")


def test_hive_topic_create_failure_text_reexports_from_failure_support() -> None:
    assert hive_topic_publish_flow.hive_topic_create_failure_text is hive_topic_publish_failures.hive_topic_create_failure_text


def test_build_hive_topic_created_response_keeps_tag_and_variant_copy() -> None:
    response = hive_topic_publish_effects.build_hive_topic_created_response(
        title="Trace Rail",
        topic_id="abcdef1234567890",
        topic_tags=["runtime", "trace"],
        variant="original",
        pending={"variants": {"original": {"title": "Trace Rail"}}},
        estimated_cost=4.0,
    )

    assert "Created Hive task `Trace Rail`" in response
    assert "Tags: runtime, trace." in response
    assert "Using original draft." in response


def test_publish_topic_with_admission_retry_maps_unauthorized_failures() -> None:
    bridge = SimpleNamespace(create_public_topic=lambda **_: (_ for _ in ()).throw(RuntimeError("unauthorized")))
    agent = SimpleNamespace(public_hive_bridge=bridge)

    result = hive_topic_publish_transport.publish_topic_with_admission_retry(
        agent,
        title="Trace Rail",
        summary="Map rail state",
        topic_tags=["runtime"],
        linked_task_id="task-123",
    )

    assert result == {
        "ok": False,
        "status": "invalid_auth",
        "details": {"status": "invalid_auth"},
        "error": "unauthorized",
    }
