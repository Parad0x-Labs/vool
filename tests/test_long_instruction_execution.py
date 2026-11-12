"""Padding must not turn actual execution requests into document-only reasoning."""
import pytest

from apps.vool_agent import VoolAgent
from tests.test_workspace_subject_evidence import isolated_home as isolated_home


@pytest.mark.parametrize("padded", [False, True])
def test_read_and_compute_keep_their_execution_owner(tmp_path, padded):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "notes.txt").write_text("FIRST-LINE\nSECOND-LINE\n")
    gap = " \n" * 2100 if padded else " "
    text = "Return exactly the second line of notes.txt." + gap + "Calculate 37 x 19."
    session = "openclaw:8234567890abcdef1234"
    context = {"surface": "openclaw", "platform": "openclaw", "workspace": str(workspace),
               "workspace_binding": "project", "project_id": "compound-project",
               "runtime_session_id": session}
    result = VoolAgent(backend_name="test-backend", device="compound-instruction").run_once(
        text, session_id_override=session, source_context=context,
    )
    assert "SECOND-LINE" in str(result.get("response", "")), result
    assert "703" in str(result.get("response", "")), result


@pytest.mark.parametrize("padded", [False, True])
@pytest.mark.parametrize("instruction", ["Read this file:", "Read the following document:"])
def test_a_labelled_file_target_keeps_compound_execution(tmp_path, padded, instruction):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "notes.txt").write_text("DISK-ONLY-CONTENT\n")
    gap = " \n" * 2100 if padded else "\n"
    text = instruction + "\nnotes.txt." + gap + "Calculate 37 x 19."
    session = "openclaw:b234567890abcdef1234"
    context = {"surface": "openclaw", "platform": "openclaw", "workspace": str(workspace),
               "workspace_binding": "project", "project_id": "compound-project",
               "runtime_session_id": session}
    result = VoolAgent(backend_name="test-backend", device="compound-instruction").run_once(
        text, session_id_override=session, source_context=context,
    )
    assert "DISK-ONLY-CONTENT" in str(result.get("response", "")), result
    assert "703" in str(result.get("response", "")), result


def test_a_filename_heading_keeps_supplied_prose_in_the_document(tmp_path):
    from core import chat_attachments

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "notes.txt").write_text("DISK-ONLY-CONTENT\n")
    text = ("Summarize this document:\nnotes.txt\n"
            + "We keep this supplied narrative as text with its original paragraph content.\n" * 100)
    session = "openclaw:c234567890abcdef1234"
    context = {"surface": "api", "_owner_local": True, "workspace": str(workspace),
               "workspace_binding": "project", "project_id": "compound-project",
               "runtime_session_id": session, "requested_model": "unavailable:compound-document"}
    result = VoolAgent(backend_name="test-backend", device="compound-document").run_once(
        text, session_id_override=session, source_context=context,
    )
    assert result.get("model_execution", {}).get("source") in {
        "selected_model_blocked", "model_unavailable", "no_provider_available"
    }, result
    assert "DISK-ONLY-CONTENT" not in str(context.get("runtime_tool_observations", [])), context
    documents = chat_attachments.list_documents(session)
    assert len(documents) == 1
    assert chat_attachments.read_staged_bytes(session_id=session, attachment_id=documents[0]["id"])[1] == text.encode()


@pytest.mark.parametrize("instruction", ["Summarize this document:", "Read this document, then answer my questions:", "Summarize the following:"])
@pytest.mark.parametrize("body", ["What is a mutex?\n" * 300, "Calculate 23 x 11.\n" * 250])
def test_executable_sentences_in_a_labelled_document_stay_supplied_material(tmp_path, instruction, body):
    from core import chat_attachments

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "notes.txt").write_text("DISK-ONLY-CONTENT\n")
    text = instruction + "\nnotes.txt\n" + body
    session = "openclaw:d234567890abcdef1234"
    context = {"surface": "api", "_owner_local": True, "workspace": str(workspace),
               "workspace_binding": "project", "project_id": "compound-project",
               "runtime_session_id": session, "requested_model": "unavailable:compound-document"}
    result = VoolAgent(backend_name="test-backend", device="compound-document").run_once(
        text, session_id_override=session, source_context=context,
    )
    assert result.get("model_execution", {}).get("source") in {
        "selected_model_blocked", "model_unavailable", "no_provider_available"
    }, result
    assert "DISK-ONLY-CONTENT" not in str(context.get("runtime_tool_observations", [])), context
    documents = chat_attachments.list_documents(session)
    assert len(documents) == 1
    assert chat_attachments.read_staged_bytes(session_id=session, attachment_id=documents[0]["id"])[1] == text.encode()


@pytest.mark.parametrize("repeats", [2, 300])
def test_a_bare_transform_heading_owns_its_supplied_text(tmp_path, repeats):
    from core import chat_attachments

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "notes.txt").write_text("DISK-ONLY-CONTENT\n")
    text = "Summarize:\nnotes.txt\n" + "What is a mutex?\n" * repeats
    session = "openclaw:e234567890abcdef1234"
    context = {"surface": "api", "_owner_local": True, "workspace": str(workspace),
               "workspace_binding": "project", "project_id": "compound-project",
               "runtime_session_id": session, "requested_model": "unavailable:compound-document"}
    result = VoolAgent(backend_name="test-backend", device="compound-document").run_once(
        text, session_id_override=session, source_context=context,
    )
    assert result.get("model_execution", {}).get("source") in {
        "selected_model_blocked", "model_unavailable", "no_provider_available"
    }, result
    assert "DISK-ONLY-CONTENT" not in str(context.get("runtime_tool_observations", [])), context
    documents = chat_attachments.list_documents(session)
    if len(text) >= chat_attachments.DOCUMENT_THRESHOLD_CHARS:
        assert len(documents) == 1
        assert chat_attachments.read_staged_bytes(session_id=session, attachment_id=documents[0]["id"])[1] == text.encode()
    else:
        assert documents == []
