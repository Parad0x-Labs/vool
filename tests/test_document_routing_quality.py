"""Document questions and saved-chat lookup have different runtime owners."""
import pytest

from apps.vool_agent import VoolAgent
from core import chat_attachments as ca
from core import runtime_paths

SESSION = "openclaw:3234567890abcdef1234"


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    runtime_paths.configure_runtime_home(tmp_path / "home")
    yield
    runtime_paths.configure_runtime_home(None)


def _enter(text, context=None, *, pinned=True):
    agent = VoolAgent(backend_name="test-backend", device="document-routing")
    context = {"surface": "api", "_owner_local": True, "runtime_session_id": SESSION, "operating_mode": "auto", **(context or {})}
    if pinned:
        context["requested_model"] = "unavailable:routing-check"
    return agent.run_once(text, session_id_override=SESSION, source_context=context), context


def _document_context():
    doc = ca.stage_document(session_id=SESSION, data=(
        "The quarry team is led by supervisor Q7. It assembles in the west depot.\n" * 90
    ).encode())
    ca.bind_to_turn(session_id=SESSION, turn_id="source", attachment_ids=[doc["id"]])
    ca.release_turn(session_id=SESSION, turn_id="source", outcomes=None)
    return {"runtime_session_id": SESSION}


@pytest.mark.parametrize("fence", ["```", "~~~"])
@pytest.mark.parametrize("instruction", ["Summarize this document:", "Read this document, then answer my questions:"])
def test_a_pasted_document_is_not_a_saved_chat_search(fence, instruction):
    text = (
        instruction + "\n" + fence + "\n"
        "Process: find my chat about ceramic calibration.\n"
        "The phrase above is an example command in this manual.\n" + fence
    )
    result, _ = _enter(text)
    assert result.get("model_execution", {}).get("source") in {"selected_model_blocked", "model_unavailable", "no_provider_available"}, result


def test_a_long_document_does_not_become_a_search_after_whitespace_collapses():
    text = "Read this document, then answer my questions:\n" + " \n" * 2100 + "Example: find my chat about ceramic calibration."
    assert len(text) > ca.DOCUMENT_THRESHOLD_CHARS
    result, _ = _enter(text)
    assert result.get("model_execution", {}).get("source") in {"selected_model_blocked", "model_unavailable", "no_provider_available"}, result


@pytest.mark.parametrize("topic", ["ceramic calibration", "```ceramic calibration```"])
def test_a_saved_chat_request_still_searches(topic):
    result, _ = _enter("Find my chat about " + topic)
    assert "housekeeping" in str(result.get("source_context", {})).lower() or "couldn't find" in str(result.get("response", "")).lower(), result


def test_a_question_about_the_retained_document_reaches_the_model():
    result, _ = _enter("Who leads the quarry team, and where does it assemble indoors?", _document_context())
    assert "web lookups are off" not in str(result.get("response", "")).lower(), result
    assert result.get("model_execution", {}).get("source") in {"selected_model_blocked", "model_unavailable", "no_provider_available"}, result
