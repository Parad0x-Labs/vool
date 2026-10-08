"""Every public memory reader declares and enforces the shared revocation boundary."""
import ast
from pathlib import Path

import pytest

from core.memory import entries
from tests.test_question_date_time_leg_20261002 import _hash_backend

pytestmark = pytest.mark.usefixtures("_hash_backend")

READERS = {
    "load_memory_excerpt", "list_memory_entries", "summarize_memory", "recent_conversation_events",
    "load_session_meta", "list_conversation_sessions", "search_relevant_memory", "find_disputed_memory_facts",
    "search_session_summaries", "search_user_heuristics", "combined_memory_entries", "legacy_memory_entries",
}
NON_READERS = {
    "forget_profile_derivatives", "resolve_memory_access_policy", "add_memory_fact", "forget_memory",
    "forget_memory_record", "sweep_governed_memory_rows", "set_session_meta", "sanitize_fact", "derive_fact_key",
    "is_user_correction", "record_memory_entry", "forget_structured_memory", "forget_sessions_memory",
    "erase_finalizations_for_session", "delete_conversation_session", "replace_name_memory",
    "keyword_tokens_filtered", "trim_text", "recency_score",
}


def test_every_new_public_entries_api_requires_an_explicit_reader_classification():
    tree = ast.parse(Path(entries.__file__).read_text())
    public = {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
              and not node.name.startswith("_")}

    assert public == READERS | NON_READERS
    assert set(entries._MEMORY_READERS) == READERS
    for name in READERS:
        assert getattr(entries, name) is entries._MEMORY_READERS[name]


@pytest.fixture()
def reader_store(tmp_path, monkeypatch):
    from core import runtime_paths
    from core.context_namespace import ensure_chat_namespace
    from core.memory.files import memory_path
    from core.persistent_memory import append_conversation_event
    from core.runtime_paths import configure_runtime_home

    previous_runtime_home = runtime_paths._VOOL_HOME_OVERRIDE
    profile = tmp_path / "home"
    profile.mkdir()
    monkeypatch.setenv("VOOL_HOME", str(profile))
    monkeypatch.setenv("VOOL_WORKSPACE_ROOT", str(profile / "workspace"))
    configure_runtime_home(profile)
    chat = "reader-gate"
    ensure_chat_namespace(chat, grant_current_receipts=False)
    policy = entries.resolve_memory_access_policy(chat_id=chat)
    assert entries.add_memory_fact("The lantern marker is MINT-84.", session_id=chat, access_policy=policy)
    append_conversation_event(session_id=chat, user_input="The lantern marker is MINT-84.", assistant_output="Noted.")
    entries.set_session_meta(chat, title="Lantern MINT-84")
    with memory_path().open("a", encoding="utf-8") as handle:
        handle.write("\n- The unbound lantern marker is MINT-84.\n")
    try:
        yield profile, chat, policy
    finally:
        configure_runtime_home(previous_runtime_home)


@pytest.mark.parametrize("reader", [
    "list_memory_entries", "summarize_memory", "search_relevant_memory", "recent_conversation_events",
    "load_memory_excerpt", "combined_memory_entries", "legacy_memory_entries", "load_session_meta",
    "list_conversation_sessions",
])
def test_shared_authority_denial_withholds_each_reader_output(reader_store, monkeypatch, reader):
    from core.memory import revocation

    _, chat, policy = reader_store
    call = {
        "list_memory_entries": lambda: entries.list_memory_entries(access_policy=policy),
        "summarize_memory": lambda: entries.summarize_memory(access_policy=policy),
        "search_relevant_memory": lambda: entries.search_relevant_memory("lantern marker", access_policy=policy),
        "recent_conversation_events": lambda: entries.recent_conversation_events(chat),
        "load_memory_excerpt": entries.load_memory_excerpt,
        "combined_memory_entries": entries.combined_memory_entries,
        "legacy_memory_entries": entries.legacy_memory_entries,
        "load_session_meta": entries.load_session_meta,
        "list_conversation_sessions": entries.list_conversation_sessions,
    }[reader]
    assert "MINT-84" in str(call())
    observed = []

    def deny(payload, **kwargs):
        observed.append((payload, kwargs))
        return False

    monkeypatch.setattr(revocation, "authorize_payload", deny)

    assert "MINT-84" not in str(call())
    assert observed


def test_denied_metadata_reader_does_not_drop_unrelated_rows_when_a_writer_updates(reader_store, monkeypatch):
    from core.memory import revocation
    from core.memory.files import chat_session_meta_path

    _, chat, _ = reader_store
    monkeypatch.setattr(revocation, "authorize_payload", lambda *_args, **_kwargs: False)
    assert entries.load_session_meta() == {}

    entries.set_session_meta("another-chat", title="Another title")

    persisted = chat_session_meta_path().read_text()
    assert "MINT-84" in persisted
    assert chat in persisted and "Another title" in persisted


def test_unreadable_authority_returns_withholding_instead_of_visible_absence(reader_store, monkeypatch):
    from core.agent_runtime.same_chat_recall import same_chat_transcript_recall_fast_path
    from core.memory import revocation

    profile, chat, _ = reader_store
    context = {"runtime_home": str(profile), "chat_id": chat, "conversation_history": [
        {"role": "user", "content": "Give me a title."},
        {"role": "assistant", "content": "Title: MINT-84"},
    ]}
    question = "What title did you give me earlier in this chat?"
    assert same_chat_transcript_recall_fast_path(question, source_surface="api", source_context=context).found
    monkeypatch.setattr(revocation, "authorize_payload", lambda *_args, **_kwargs: False)

    recalled = same_chat_transcript_recall_fast_path(question, source_surface="api", source_context=context)

    assert recalled is not None and recalled.found is False
    assert "MINT-84" not in recalled.response
    assert "withheld" in recalled.response
    assert "does not appear" not in recalled.response


def test_transcript_delivery_rechecks_after_extracting_the_answer(reader_store, monkeypatch):
    from core.agent_runtime import same_chat_recall
    from core.memory import revocation

    profile, chat, _ = reader_store
    original = same_chat_recall._visible_text_answer

    def extract_then_close_authority(*args, **kwargs):
        response = original(*args, **kwargs)
        assert "MINT-84" in response
        monkeypatch.setattr(revocation, "authorize_payload", lambda *_args, **_kwargs: False)
        return response

    monkeypatch.setattr(same_chat_recall, "_visible_text_answer", extract_then_close_authority)
    recalled = same_chat_recall.same_chat_transcript_recall_fast_path(
        "What title did you give me earlier in this chat?", source_surface="api",
        source_context={"runtime_home": str(profile), "chat_id": chat, "conversation_history": [
            {"role": "user", "content": "Give me a title."},
            {"role": "assistant", "content": "Title: MINT-84"},
        ]},
    )

    assert recalled is not None and recalled.found is False
    assert "MINT-84" not in recalled.response


def test_frontdoor_rechecks_after_fast_path_result_is_built(make_agent, monkeypatch, reader_store):
    from core.memory import revocation

    profile, _, _ = reader_store
    agent = make_agent()
    original = agent._fast_path_result
    built = []

    def build_then_close_authority(*args, **kwargs):
        result = original(*args, **kwargs)
        if kwargs.get("reason") == "same_chat_transcript_recall":
            built.append(result["response"])
            monkeypatch.setattr(revocation, "authorize_payload", lambda *_args, **_kwargs: False)
        return result

    monkeypatch.setattr(agent, "_fast_path_result", build_then_close_authority)
    result = agent.run_once(
        "Repeat the title you gave me earlier in this chat.", session_id_override="reader-finalization",
        source_context={"surface": "api", "platform": "api", "allow_remote_fetch": False, "runtime_home": str(profile),
                        "conversation_history": [
                            {"role": "user", "content": "Give me a title."},
                            {"role": "assistant", "content": "Title: MINT-84"},
                        ]},
    )

    assert built and "MINT-84" in built[0], {"built": built, "result": result}
    assert "MINT-84" not in result["response"]
    assert result["recall_found"] is False
    assert result["route_reason"] == "visible_history_revocation_withheld"
