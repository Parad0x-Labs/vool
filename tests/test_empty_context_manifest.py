from __future__ import annotations

from core.provenance_store import load_manifest
from core.runtime_paths import configure_runtime_home
from core.tiered_context_loader import empty_tiered_context_result


def test_plain_provider_turn_gets_a_signed_empty_context_manifest(tmp_path) -> None:
    configure_runtime_home(tmp_path)
    try:
        source_context = {
            "request_id": "request-empty-context-001",
            "chat_id": "chat-empty-context-001",
        }
        result = empty_tiered_context_result(
            task_id="task-empty-context-001",
            reason="plain_task_minimal_no_context",
            source_context=source_context,
        )

        assert result.report.context_manifest_required is True
        assert result.report.context_manifest_id
        assert source_context["context_manifest_id"] == result.report.context_manifest_id
        manifest = load_manifest(result.report.context_manifest_id)
        assert manifest is not None
        assert manifest["trace_id"] == "request-empty-context-001"
        assert manifest["selected_items"] == []
        assert manifest["candidate_items"] == []
    finally:
        configure_runtime_home(None)
