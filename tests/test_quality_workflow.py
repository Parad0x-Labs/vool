"""Folder, table and retained-document evidence coexist through one chat's real turns."""
from decimal import Decimal

from apps.vool_agent import VoolAgent
from core import chat_attachments as ca
from tests.test_workspace_subject_evidence import isolated_home as isolated_home


def test_one_chat_keeps_folder_table_and_document_evidence_available(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "kiln_records.toml").write_text("[firing]\npeak_celsius = 913\n")
    (workspace / "measures.csv").write_text("batch,quantity\namber,17.25\nazure,3.25\n")
    agent = VoolAgent(backend_name="test-backend", device="quality-workflow")
    session = "openclaw:5234567890abcdef1234"

    def turn(text):
        context = {"surface": "api", "_owner_local": True, "workspace": str(workspace),
                   "workspace_binding": "project", "project_id": "quality-project",
                   "runtime_session_id": session, "operating_mode": "auto",
                   "requested_model": "unavailable:quality-workflow"}
        result = agent.run_once(text, session_id_override=session, source_context=context)
        return result, context

    def check_folder(context):
        assert any("peak_celsius = 913" in item.get("response_preview", "")
                   for item in context.get("runtime_tool_observations", [])), context

    def check_table(context):
        assert any(item.get("computed_table", {}).get("totals", {}).get("quantity")
                   is not None
                   and Decimal(item["computed_table"]["totals"]["quantity"])
                   == Decimal("17.25") + Decimal("3.25")
                   for item in context.get("runtime_tool_observations", [])), context

    _, context = turn("What does the kiln record say?")
    check_folder(context)
    _, context = turn("What is the total quantity in measures.csv?")
    check_table(context)
    document = ("Read this document, then answer my questions:\n" + " \n" * 2100
                + "```\nExample command: find my chat about ceramic calibration.\n```\n"
                + "The final contingency allocation is 73 spare panels.")
    result, _ = turn(document)
    assert result.get("model_execution", {}).get("source") in {
        "selected_model_blocked", "model_unavailable", "no_provider_available"
    }, result
    documents = ca.list_documents(session)
    assert len(documents) == 1
    assert ca.read_staged_bytes(session_id=session, attachment_id=documents[0]["id"])[1] == document.encode()
    for _ in range(3):
        turn("Reply with exactly OK.")
    _, context = turn("What is the final contingency allocation?")
    attachments = ca.model_attachments_from_source_context(context, question="final contingency allocation")
    messages, _ = ca.apply_to_provider_messages(
        [{"role": "user", "content": "What is the final contingency allocation?"}],
        attachments=attachments, supports_images=False,
    )
    assert "73 spare panels" in str(messages)
    _, context = turn("What does the kiln record say?")
    check_folder(context)
    _, context = turn("What is the total quantity in measures.csv?")
    check_table(context)
    result, _ = turn("Read unavailable-notes.txt")
    assert "no file" in str(result.get("response", "")).lower(), result
