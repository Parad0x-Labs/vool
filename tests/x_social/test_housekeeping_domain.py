from tests._credential_intelligence_support import isolated_home, vault_home
from tests.x_social.test_amendment_served import served
from tests.x_social.test_skill_served import test_installed_skill_reaches_native_x_flow_and_owner_card as _run


def test_explicit_x_search_reaches_installed_skill(vault_home, tmp_path, monkeypatch):
    monkeypatch.setenv("SOCIAL_EXPLICIT_SEARCH", "1")
    _run(vault_home, tmp_path, monkeypatch)


def test_chat_history_search_keeps_its_own_wording(served):
    from core.chat_housekeeping import recalled_results
    from core.persistent_memory import append_conversation_event

    source = "openclaw:mandarinpackagingorigin"
    append_conversation_event(session_id=source,
        user_input="We discussed Mandarin packaging.",
        assistant_output="Use a small recyclable carton for Mandarin packaging.",
        source_context={"surface": "api", "platform": "api"})
    reply = served.chat("Find the chat where we discussed Mandarin packaging.",
                        [{"final": "The scripted provider must not own this history search."}],
                        "history-wording", session="history-search-current")
    hits = recalled_results(reply["vool_session_id"])["hits"]
    assert any(hit["session_id"] == source for hit in hits), (reply, hits)
    assert served.cursor[0] == 0, "history search should complete through its real housekeeping lane"
