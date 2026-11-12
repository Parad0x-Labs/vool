"""Long inline messages survive real runtime intake through the document authority."""
import pytest

from apps.vool_agent import VoolAgent
from core import chat_attachments as ca
from core import runtime_paths


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    runtime_paths.configure_runtime_home(tmp_path / "home")
    yield
    runtime_paths.configure_runtime_home(None)


def _turn(agent, text, session, turn):
    context = {"surface": "api", "_owner_local": True, "runtime_session_id": session,
               "requested_model": "unavailable:retention-check", "attachment_turn_id": turn,
               "operating_mode": "auto"}
    agent.run_once(text, session_id_override=session, source_context=context)
    return context


def test_long_inline_text_is_retained_and_delivered_three_turns_later():
    agent = VoolAgent(backend_name="test-backend", device="inline-retention")
    session = "openclaw:1234567890abcdef1234"
    text = "Please summarize this document:\n\n" + "\n".join(
        f"Procedure {i}: inspect the fixture and retain its numbered receipt." for i in range(95)
    ) + "\nThe final contingency allocation is 73 spare panels."
    assert len(text) > ca.DOCUMENT_THRESHOLD_CHARS
    _turn(agent, text, session, "paste-origin")
    documents = ca.list_documents(session)
    assert len(documents) == 1, documents
    assert ca.read_staged_bytes(session_id=session, attachment_id=documents[0]["id"])[1] == text.encode()
    _turn(agent, text, session, "paste-origin")
    assert len(ca.list_documents(session)) == 1
    for i in range(3):
        _turn(agent, "Reply with exactly OK.", session, f"later-{i}")
    context = _turn(agent, "What is the final contingency allocation?", session, "question")
    attachments = ca.model_attachments_from_source_context(context, question="final contingency allocation")
    messages, _ = ca.apply_to_provider_messages(
        [{"role": "user", "content": "What is the final contingency allocation?"}],
        attachments=attachments, supports_images=False,
    )
    assert "73 spare panels" in str(messages)
    assert ca.model_attachments_from_source_context({"runtime_session_id": "openclaw:1234567890abcdef5678"}) == []
    ca.erase_document(session_id=session, attachment_id=documents[0]["id"])
    assert ca.model_attachments_from_source_context(context) == []
    _turn(agent, text, session, "paste-origin")
    assert ca.list_documents(session) == []


def test_a_private_long_message_is_not_retained():
    agent = VoolAgent(backend_name="test-backend", device="private-inline-retention")
    session = "openclaw:2234567890abcdef1234"
    _turn(agent, "private chat", session, "private-start")
    _turn(agent, "Please summarize:\n" + "Disposable fixture details.\n" * 240, session, "private-document")
    assert ca.list_documents(session) == []


@pytest.mark.parametrize("stream", [False, True])
def test_a_served_inline_document_is_released_with_its_bytes_retained(stream, tmp_path):
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_post

    session = "openclaw:4234567890abcdef1234"
    text = "Summarize this document:\n" + "Retain the numbered fixture receipt.\n" * 160
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = RuntimeServices(agent=VoolAgent(backend_name="test-backend", device="served-inline"))
    try:
        response = dispatch_post(
            path="/api/chat",
            body={"session_id": session, "stream": stream, "mode": "auto",
                  "model": "unavailable:retention-check",
                  "messages": [{"role": "user", "content": text}]},
            headers={"content-type": "application/json"}, runtime=runtime,
            model_name="vool", workspace_root_provider=lambda: str(workspace),
            client_host="127.0.0.1",
        )
        assert response.status == 200, response.body
        if stream:
            assert response.stream is not None
            assert b"".join(response.stream)
        documents = ca.list_documents(session)
        assert len(documents) == 1, documents
        assert documents[0]["state"] == "released", documents
        assert ca.read_staged_bytes(session_id=session, attachment_id=documents[0]["id"])[1] == text.encode()
    finally:
        runtime.shutdown()
