"""A forgotten fact from a long pasted message is gone: no later turn gets it and no copy stays in the store."""
import pytest

from apps.vool_agent import VoolAgent
from core import chat_attachments as ca
from core import runtime_paths


@pytest.fixture
def home(tmp_path, monkeypatch):
    path = tmp_path / "home"
    monkeypatch.setenv("VOOL_HOME", str(path))
    runtime_paths.configure_runtime_home(path)
    yield path
    runtime_paths.configure_runtime_home(None)


def _turn(agent, text, session, turn):
    context = {"surface": "api", "_owner_local": True, "runtime_session_id": session,
               "requested_model": "unavailable:forget-after-paste", "attachment_turn_id": turn,
               "operating_mode": "auto"}
    result = agent.run_once(text, session_id_override=session, source_context=context)
    return result, context


def _files_holding(root, needle):
    hits = []
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            try:
                if needle in path.read_bytes():
                    hits.append(str(path.relative_to(root)))
            except OSError:
                continue
    return hits


def test_paste_forget_then_ask_again_finds_nothing(home):
    agent = VoolAgent(backend_name="test-backend", device="forget-after-paste")
    session = "openclaw:6234567890abcdef1234"
    paste = ("Notes for the move, please keep them in mind:\n"
             + "\n".join(f"Box {i}: kitchen items, fragile, label side up." for i in range(110))
             + "\nMy locker code is 4417.")
    assert len(paste) >= ca.DOCUMENT_THRESHOLD_CHARS
    _turn(agent, paste, session, "paste")
    _turn(agent, "Forget my locker code is 4417.", session, "forget")
    _, context = _turn(agent, "What is my locker code?", session, "ask-again")

    carried = ca.carried_documents_for_turn(session_id=session, exclude_turn_id="ask-again",
                                            question="What is my locker code?")
    assert "4417" not in str(carried)
    attachments = ca.model_attachments_from_source_context(context, question="What is my locker code?")
    messages, _ = ca.apply_to_provider_messages(
        [{"role": "user", "content": "What is my locker code?"}], attachments=attachments, supports_images=False,
    )
    assert "4417" not in str(messages)
    assert ca.list_documents(session) == []  # the forget erased the retained copy
    # The attachment store holds no copy (chat history and forget's own records are other stores).
    store = ca._bytes_path("0" * 32).parent
    assert _files_holding(store, b"My locker code is 4417") == []
