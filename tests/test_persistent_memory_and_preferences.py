from __future__ import annotations

import hashlib
import json
from unittest import mock

from core.context_namespace import ensure_chat_namespace, grant_context_import
from core.context_scope import ContextAccessPolicy
from core.memory.learning import auto_capture_memory
from core.persistent_memory import (
    append_conversation_event,
    conversation_log_path,
    describe_session_memory_policy,
    ensure_memory_files,
    load_operator_dense_profile,
    maybe_handle_memory_command,
    memory_entries_path,
    memory_path,
    operator_dense_profile_path,
    search_relevant_memory,
    search_session_summaries,
    search_user_heuristics,
    session_memory_policy,
    session_summaries_path,
    summarize_memory,
    user_heuristics_path,
)
from core.runtime_paths import data_path
from core.user_preferences import (
    extract_requested_agent_name,
    load_preferences,
    maybe_handle_preference_command,
    user_address,
)
from storage.db import get_connection


def setup_function() -> None:
    for path in (
        memory_path(),
        conversation_log_path(),
        memory_entries_path(),
        session_summaries_path(),
        user_heuristics_path(),
        operator_dense_profile_path(),
    ):
        if path.exists():
            path.unlink()
    prefs_path = data_path("user_preferences.json")
    if prefs_path.exists():
        prefs_path.unlink()
    conn = get_connection()
    try:
        for table in ("session_memory_policies", "learning_shards", "local_tasks", "knowledge_holders", "knowledge_manifests"):
            try:
                conn.execute(f"DELETE FROM {table}")
            except Exception:
                continue
        conn.commit()
    finally:
        conn.close()


def test_memory_remember_and_forget_commands_roundtrip() -> None:
    ensure_chat_namespace("memory-roundtrip")
    handled, _ = maybe_handle_memory_command(
        "remember that the owner prefers concise answers",
        session_id="memory-roundtrip",
    )
    assert handled is True
    remembered = "\n".join(
        summarize_memory(chat_id="memory-roundtrip", limit=20)
    ).lower()
    assert "prefers concise answers" in remembered

    handled, response = maybe_handle_memory_command(
        "Remember this exact identifier: KAS-ALPHA-8841. Reply with only stored.",
        session_id="memory-roundtrip",
    )
    assert handled is True
    assert "locked in" in response.lower()
    remembered = "\n".join(
        summarize_memory(chat_id="memory-roundtrip", limit=20)
    )
    assert "KAS-ALPHA-8841" in remembered
    assert "Reply with only stored" not in remembered


def test_remember_with_a_trailing_question_defers_to_the_turn() -> None:
    # "Remember X ... what is the current mission?" must NOT stop at "Locked in" and swallow the
    # question — defer so the turn answers it (mission values are tracked by active-mission slots).
    handled, response = maybe_handle_memory_command(
        "Remember this mission: cap 0.05 SOL, domain parad0x.null, Windows only. What is the current mission?",
        session_id="memory-question",
    )
    assert handled is False
    assert response == ""
    # a plain store command with no question still short-circuits and confirms
    ensure_chat_namespace("memory-question")
    handled, response = maybe_handle_memory_command(
        "remember to water the plants",
        session_id="memory-question",
    )
    assert handled is True
    assert "remember" in response.lower() or "locked in" in response.lower()

    handled, response = maybe_handle_memory_command(
        "forget concise answers",
        session_id="memory-question",
    )
    assert handled is True
    assert "removed" in response.lower()
    remembered_after = "\n".join(
        summarize_memory(chat_id="memory-question", limit=20)
    ).lower()
    assert "prefers concise answers" not in remembered_after


def test_natural_forget_target_extracts_typed_value(tmp_path, monkeypatch) -> None:
    from core import context_retrieval, runtime_paths
    from core.context_retrieval import _open_memory_for_runtime
    from core.fact_extractor import stable_text_embedding

    runtime_home = tmp_path / "runtime-home"
    monkeypatch.setenv("VOOL_HOME", str(runtime_home))
    monkeypatch.setattr(runtime_paths, "_VOOL_HOME_OVERRIDE", None, raising=False)
    ensure_chat_namespace("memory-natural-forget")
    handled, _ = maybe_handle_memory_command(
        "remember this exact identifier: KAS-ALPHA-8841",
        session_id="memory-natural-forget",
    )
    assert handled is True
    stored = context_retrieval.store_turn(
        "memory-natural-forget",
        "Remember this exact identifier: KAS-ALPHA-8841",
        "Locked in.",
        source_context={"runtime_home": str(runtime_home)},
    )
    assert int(stored.get("stored_count", 0)) >= 1

    handled, response = maybe_handle_memory_command(
        "Forget the identifier KAS-ALPHA-8841 from this chat.",
        session_id="memory-natural-forget",
    )
    assert handled is True
    assert "Removed 1 memory entry" in response
    assert "KAS-ALPHA-8841" not in "\n".join(
        summarize_memory(chat_id="memory-natural-forget", limit=20)
    )

    with _open_memory_for_runtime(None) as memory:
        hits = memory.node_search_hybrid(
            "KAS-ALPHA-8841",
            stable_text_embedding("KAS-ALPHA-8841"),
            session_id="memory-natural-forget",
            min_score=0.0,
        )
    assert not any("KAS-ALPHA-8841" in node.content for node, _score in hits)


def test_natural_capture_forms_and_corrections_are_scoped() -> None:
    session_id = "memory-natural-forms"
    ensure_chat_namespace(session_id)

    handled, _ = maybe_handle_memory_command(
        "This is a fresh chat. Remember this private marker only here: PRIVATE-BETA-2207.",
        session_id=session_id,
    )
    assert handled is True
    handled, response = maybe_handle_memory_command(
        "Forget the private marker PRIVATE-BETA-2207.",
        session_id=session_id,
    )
    assert handled is True
    assert "Removed 1 memory entry" in response
    assert "PRIVATE-BETA-2207" not in "\n".join(
        summarize_memory(chat_id=session_id, limit=20)
    )
    handled, response = maybe_handle_memory_command(
        "What private identifier remains stored in this chat? Answer only if one is still active.",
        session_id=session_id,
    )
    assert handled is True
    assert "No matching active memory" in response
    handled, _ = maybe_handle_memory_command(
        "Keep this date in mind for this chat: 2026-09-17.",
        session_id=session_id,
    )
    assert handled is True
    handled, _ = maybe_handle_memory_command(
        "Please retain this exact route label: ORBIT 19 SOUTH.",
        session_id=session_id,
    )
    assert handled is True
    handled, _ = maybe_handle_memory_command(
        "Keep your next answers concise and direct.",
        session_id=session_id,
    )
    assert handled is False

    handled, response = maybe_handle_memory_command(
        "What exact route label did I just tell you to retain?",
        session_id=session_id,
    )
    assert handled is True
    assert "ORBIT 19 SOUTH" in response

    handled, response = maybe_handle_memory_command(
        "What identifier did I give you in a different chat? If you cannot access it here, say so.",
        session_id=session_id,
    )
    assert handled is True
    assert "another chat" in response
    assert "ORBIT 19 SOUTH" not in response

    handled, response = maybe_handle_memory_command(
        "Correction: the date is now 2026-10-21, not 2026-09-17. Use the new date.",
        session_id=session_id,
    )
    assert handled is True
    assert "2026-10-21" in response
    remembered = "\n".join(summarize_memory(chat_id=session_id, limit=20))
    assert "2026-10-21" in remembered
    assert "2026-09-17" not in remembered

    handled, _ = maybe_handle_memory_command(
        "Keep this exact budget note in this chat: 0.037 SOL.",
        session_id=session_id,
    )
    assert handled is True
    handled, response = maybe_handle_memory_command(
        "Correction: the budget note is now 0.088 SOL, replacing 0.037 SOL.",
        session_id=session_id,
    )
    assert handled is True
    assert "0.088 SOL" in response
    remembered = "\n".join(summarize_memory(chat_id=session_id, limit=20))
    assert "0.088 SOL" in remembered
    assert "0.037 SOL" not in remembered
    rows = [
        json.loads(line)
        for line in memory_entries_path().read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    corrected_budget = next(row for row in rows if "0.088 SOL" in str(row.get("text") or ""))
    assert "budget" in str(corrected_budget.get("fact_key") or "")

    handled, response = maybe_handle_memory_command(
        "Forget the marker ORBIT 19 SOUTH, but keep the budget note.",
        session_id=session_id,
    )
    assert handled is True
    assert "Removed 1 memory entry" in response


def test_natural_phrase_recall_routes_save_as_note_questions_to_memory() -> None:
    session_id = "memory-natural-phrase-recall"
    ensure_chat_namespace(session_id)

    handled, _ = maybe_handle_memory_command(
        "Remember this phrase as a note for this chat: North Star.",
        session_id=session_id,
    )
    assert handled is True

    handled, response = maybe_handle_memory_command(
        "What phrase did I save as a note here?",
        session_id=session_id,
    )
    assert handled is True
    assert "North Star" in response
    assert "North start" not in response


def test_current_memory_recall_does_not_treat_negated_stale_marker_as_stale_request() -> None:
    session_id = "memory-negated-stale-recall"
    ensure_chat_namespace(session_id)

    handled, _ = maybe_handle_memory_command(
        "In this new conversation, remember the exact marker CROSS-GAMMA-4408.",
        session_id=session_id,
    )
    assert handled is True
    handled, _ = maybe_handle_memory_command(
        "Update the marker: use CROSS-GAMMA-9921 instead of CROSS-GAMMA-4408 from now on.",
        session_id=session_id,
    )
    assert handled is True

    handled, response = maybe_handle_memory_command(
        "Repeat the current marker, not the superseded one.",
        session_id=session_id,
    )
    assert handled is True
    assert "CROSS-GAMMA-9921" in response
    assert "CROSS-GAMMA-4408" not in response


def test_current_identifier_recall_understands_do_not_include_superseded_values() -> None:
    session_id = "memory-negated-superseded-identifier"
    ensure_chat_namespace(session_id)

    handled, _ = maybe_handle_memory_command(
        "Remember this exact identifier for this chat: INSTALL-MEM-A8AA-7257.",
        session_id=session_id,
    )
    assert handled is True
    handled, response = maybe_handle_memory_command(
        "Correction: the exact identifier is INSTALL-MEM-A8AA-9921, not INSTALL-MEM-A8AA-7257.",
        session_id=session_id,
    )
    assert handled is True
    assert "INSTALL-MEM-A8AA-9921" in response

    handled, response = maybe_handle_memory_command(
        "What is the current exact identifier? Do not include superseded values.",
        session_id=session_id,
    )

    assert handled is True
    assert "INSTALL-MEM-A8AA-9921" in response
    assert "INSTALL-MEM-A8AA-7257" not in response

    handled, response = maybe_handle_memory_command(
        "Forget the exact identifier I asked you to remember in this chat.",
        session_id=session_id,
    )
    assert handled is True
    assert "Removed 1 memory entry" in response

    handled, response = maybe_handle_memory_command(
        "What exact identifier is currently remembered in this chat?",
        session_id=session_id,
    )
    assert handled is True
    assert "don't have an active remembered value" in response
    assert "INSTALL-MEM-A8AA" not in response

    handled, response = maybe_handle_memory_command(
        "What is the current exact identifier?",
        session_id=session_id,
    )
    assert handled is True
    assert "don't have an active remembered value" in response
    assert "INSTALL-MEM-A8AA" not in response


def test_referential_forget_honors_a_preservation_clause() -> None:
    session_id = "memory-referential-forget-preserve"
    ensure_chat_namespace(session_id)

    assert maybe_handle_memory_command(
        "Remember this exact identifier for this chat: INSTALL-MEM-E91D-7319.",
        session_id=session_id,
    )[0]
    assert maybe_handle_memory_command(
        "Keep this exact budget note in this chat: 0.091 SOL.",
        session_id=session_id,
    )[0]

    handled, response = maybe_handle_memory_command(
        "Forget the exact identifier I asked you to remember in this chat, but keep the budget note.",
        session_id=session_id,
    )

    assert handled is True
    assert "Removed 1 memory entry" in response
    remembered = "\n".join(summarize_memory(chat_id=session_id, limit=20))
    assert "INSTALL-MEM-E91D-7319" not in remembered
    assert "0.091 SOL" in remembered


def test_combined_capture_direct_correction_and_descriptive_forget_round_trip() -> None:
    session_id = "memory-combined-correction-forget"
    ensure_chat_namespace(session_id)

    handled, response = maybe_handle_memory_command(
        "Remember that my exact installer verification identifier is INSTALL-MEM-E31-7319. "
        "Also remember that my test budget note is 0.091 SOL.",
        session_id=session_id,
    )
    assert handled is True
    assert "remember" in response.casefold()
    remembered = "\n".join(summarize_memory(chat_id=session_id, limit=20))
    assert "INSTALL-MEM-E31-7319" in remembered
    assert "0.091 SOL" in remembered

    handled, response = maybe_handle_memory_command(
        "Correction: my exact installer verification identifier is INSTALL-MEM-E31-9842.",
        session_id=session_id,
    )
    assert handled is True
    assert "Correction applied" in response

    handled, response = maybe_handle_memory_command(
        "What is my current exact installer verification identifier?",
        session_id=session_id,
    )
    assert handled is True
    assert "INSTALL-MEM-E31-9842" in response
    assert "INSTALL-MEM-E31-7319" not in response

    handled, response = maybe_handle_memory_command(
        "Forget the exact installer verification identifier I asked you to remember, "
        "but keep the budget note.",
        session_id=session_id,
    )
    assert handled is True
    assert "Removed 1 memory entry" in response

    remembered = "\n".join(summarize_memory(chat_id=session_id, limit=20))
    assert "INSTALL-MEM-E31" not in remembered
    assert "0.091 SOL" in remembered

    handled, response = maybe_handle_memory_command(
        "What is my current exact installer verification identifier?",
        session_id=session_id,
    )
    assert handled is True
    assert "don't have an active remembered value" in response
    assert "INSTALL-MEM-E31" not in response


def test_correction_text_is_not_recaptured_as_an_additional_memory_fact() -> None:
    session_id = "memory-correction-no-duplicate"
    ensure_chat_namespace(session_id)

    auto_capture_memory(
        session_id=session_id,
        user_input="Update the marker: use CROSS-GAMMA-9921 instead of CROSS-GAMMA-4408 from now on.",
    )
    ensure_memory_files()

    rows = [
        json.loads(line)
        for line in memory_entries_path().read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert not any(
        "CROSS-GAMMA-9921" in str(row.get("text") or "")
        for row in rows
    )


def test_fresh_chat_memory_inventory_is_deterministic_without_provider_access() -> None:
    ensure_chat_namespace("memory-fresh-inventory")
    handled, response = maybe_handle_memory_command(
        "This is a completely fresh conversation. What identifiers have I asked you to remember in this chat?",
        session_id="memory-fresh-inventory",
    )

    assert handled is True
    assert "active remembered value" in response.lower()
    assert "chat" in response.lower()


def test_ordinary_memory_stays_in_chat_scope_but_explicit_profile_preferences_do_not() -> None:
    session_id = "memory-scope-source"
    other_session_id = "memory-scope-other"
    ContextAccessPolicy.for_request(
        session_id=session_id,
        source_context={"surface": "desktop"},
    )
    other_policy = ContextAccessPolicy.for_request(
        session_id=other_session_id,
        source_context={"surface": "desktop"},
    )

    handled, _ = maybe_handle_memory_command(
        "Remember this private marker only here: CHAT-ONLY-8841.",
        session_id=session_id,
    )
    assert handled is True
    handled, _ = maybe_handle_memory_command(
        "Remember my preferred answer style is concise.",
        session_id=session_id,
    )
    assert handled is True

    rows = [
        json.loads(line)
        for line in memory_entries_path().read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    marker = next(row for row in rows if "CHAT-ONLY-8841" in str(row.get("text") or ""))
    preference = next(row for row in rows if "preferred answer style" in str(row.get("text") or ""))
    assert marker["scope"] == "chat"
    assert marker["origin_chat_id"] == session_id
    assert preference["scope"] == "user_profile"

    assert search_relevant_memory(
        "CHAT-ONLY-8841",
        access_policy=other_policy,
    ) == []


def test_preference_commands_persist() -> None:
    handled, _ = maybe_handle_preference_command("set humor 90%")
    assert handled is True
    handled, _ = maybe_handle_preference_command("act like Cornholio")
    assert handled is True

    prefs = load_preferences()
    assert prefs.humor_percent == 90
    assert prefs.character_mode.lower() == "cornholio"


def test_user_address_is_operator_profile_business_not_a_silent_preference_write() -> None:
    # "call me X" / "my name is X" are Operator Profile statements (core.operator_profile): the
    # preference-command surface no longer writes a name silently. The profile lane turns the
    # stated form into a candidate the user confirms and the explicit form into a reported save.
    from core.operator_profile import OWNER_PRINCIPAL, list_items, remember, resolve
    from core.operator_profile_interpretation import interpret_profile_turn
    from tests.operator_profile_rig import set_owner_preferred_name

    set_owner_preferred_name("")
    try:
        for text in ("call me Alex", "my name is Boss"):
            handled, _ = maybe_handle_preference_command(text)
            assert handled is False
            assert load_preferences().user_address == ""
            assert interpret_profile_turn(text)[0].strength == "strong"
        assert interpret_profile_turn("remember to call me Boss")[0].strength == "explicit"
        assert remember(OWNER_PRINCIPAL, "preferred_name", "Boss", origin="explicit").kind == "saved"
        assert user_address() == "Boss"

        # The greeting weaves the address in at least some of the time across many random draws.
        from core.agent_runtime.fast_paths_utility import build_greeting_reply
        assert any("Boss" in build_greeting_reply("gm", addr="Boss") for _ in range(60))

        assert interpret_profile_turn("forget my name")[0].action == "forget"
        for item in list_items(OWNER_PRINCIPAL):
            if item.category == "preferred_name":
                from core.operator_profile import forget_item

                forget_item(item.item_id)
        assert resolve(OWNER_PRINCIPAL, "preferred_name") is None
        assert user_address() == ""
    finally:
        set_owner_preferred_name("")


def test_greeting_emoji_preserves_terminal_punctuation(monkeypatch) -> None:
    from core.agent_runtime.fast_paths_utility import _with_greeting_emoji

    monkeypatch.setattr("core.agent_runtime.fast_paths_utility.random.choice", lambda _items: "👋")
    assert _with_greeting_emoji("Hello.") == "Hello. 👋"
    assert _with_greeting_emoji("Ready?") == "Ready? 👋"


def test_call_you_renames_vool_not_the_user() -> None:
    # "call you X" targets VOOL's own name; it must never set the user's address.
    before = load_preferences().user_address
    with mock.patch("core.onboarding.force_rename"), mock.patch("core.identity_manager.update_local_persona"):
        handled, _ = maybe_handle_preference_command("call you Robo")
    assert handled is True
    assert load_preferences().user_address == before


def test_autonomy_and_workflow_preferences_persist() -> None:
    handled, response = maybe_handle_preference_command("don't ask for micro step approval")
    assert handled is True
    assert "hands-off" in response.lower()

    handled, response = maybe_handle_preference_command("hide workflow")
    assert handled is True
    assert "disabled" in response.lower()

    prefs = load_preferences()
    assert prefs.autonomy_mode == "hands_off"
    assert prefs.show_workflow is False


def test_workflow_default_is_hidden() -> None:
    prefs = load_preferences()
    assert prefs.show_workflow is False


def test_natural_language_hide_workflow_command_persists() -> None:
    handled, response = maybe_handle_preference_command(
        "Do not show me this workflow, keep it to yourself."
    )
    assert handled is True
    assert "disabled" in response.lower()
    assert load_preferences().show_workflow is False


def test_hive_followup_preferences_persist() -> None:
    handled, response = maybe_handle_preference_command("disable hive followups")
    assert handled is True
    assert "disabled" in response.lower()

    handled, response = maybe_handle_preference_command("don't help with research when idle")
    assert handled is True
    assert "disabled" in response.lower()

    prefs = load_preferences()
    assert prefs.hive_followups is False
    assert prefs.idle_research_assist is False


def test_hive_task_intake_preferences_persist() -> None:
    handled, response = maybe_handle_preference_command("stay visible but don't take tasks")
    assert handled is True
    assert "stay visible" in response.lower()

    prefs = load_preferences()
    assert prefs.accept_hive_tasks is False

    handled, response = maybe_handle_preference_command("accept hive tasks")
    assert handled is True
    assert "enabled" in response.lower()

    prefs = load_preferences()
    assert prefs.accept_hive_tasks is True


def test_social_commons_preferences_persist() -> None:
    handled, response = maybe_handle_preference_command("disable agent commons")
    assert handled is True
    assert "disabled" in response.lower()

    prefs = load_preferences()
    assert prefs.social_commons is False

    handled, response = maybe_handle_preference_command("enable agent commons")
    assert handled is True
    assert "enabled" in response.lower()

    prefs = load_preferences()
    assert prefs.social_commons is True


def test_extract_requested_agent_name_handles_natural_language_rename() -> None:
    assert extract_requested_agent_name("I renaming you to Cornholio, and my name is SLS") == "Cornholio"
    assert extract_requested_agent_name("rename yourself to Cornholio") == "Cornholio"
    assert extract_requested_agent_name("your name is Cornholio now") == "Cornholio"
    assert extract_requested_agent_name("you are acting weird but create hello world file and save it as .txt in Marchtest folder") is None


def test_rename_command_respects_owner_authority() -> None:
    with mock.patch("core.onboarding.force_rename") as force_rename, mock.patch(
        "core.identity_manager.update_local_persona"
    ) as update_persona:
        handled, response = maybe_handle_preference_command("I am renaming you to Cornholio")

    assert handled is True
    assert "Cornholio" in response
    force_rename.assert_called_once_with("Cornholio")
    update_persona.assert_called_once_with("default", display_name="Cornholio")


def test_auto_memory_extraction_persists_names_and_style_preferences() -> None:
    append_conversation_event(
        session_id="openclaw:test-user",
        user_input="My name is Operator. Keep answers concise and brutally honest.",
        assistant_output="Noted.",
        source_context={"surface": "channel", "platform": "openclaw"},
    )

    remembered = "\n".join(
        summarize_memory(chat_id="openclaw:test-user", limit=20)
    ).lower()
    # The operator's NAME is Operator Profile business (a candidate the user confirms), never a
    # free-text memory fact harvested behind their back; the style sentence still is one.
    assert "operator name is" not in remembered
    assert "keep answers concise and brutally honest" in remembered

    hits = search_relevant_memory(
        "what style should you use",
        access_policy=ContextAccessPolicy.for_request(
            session_id="openclaw:test-user",
            source_context={"surface": "local"},
        ),
        topic_hints=["concise", "honest"],
        limit=4,
    )
    assert any("concise" in str(hit.get("text") or "").lower() for hit in hits)


def test_user_heuristics_capture_stack_sources_and_autonomy_preferences() -> None:
    append_conversation_event(
        session_id="openclaw:heuristics",
        user_input=(
            "I am building Telegram bots in Python. Use official docs and good GitHub repos first. "
            "Don't ask for micro approval, just do the work and test all."
        ),
        assistant_output="Noted.",
        source_context={"surface": "channel", "platform": "openclaw"},
    )

    heuristics = search_user_heuristics(
        "build a telegram bot",
        access_policy=ContextAccessPolicy.for_request(
            session_id="openclaw:heuristics",
            source_context={"surface": "local"},
        ),
        topic_hints=["telegram bot", "github"],
        limit=6,
    )
    categories = {(str(item.get("category") or ""), str(item.get("signal") or "")) for item in heuristics}

    assert ("preferred_stack", "python") in categories
    assert ("source_preference", "official_docs") in categories
    assert ("source_preference", "github_repos") in categories
    assert ("autonomy_preference", "hands_off") in categories


def test_new_user_heuristics_persist_content_addressed_provenance() -> None:
    append_conversation_event(
        session_id="openclaw:heuristic-provenance",
        user_input="Keep answers concise and direct.",
        assistant_output="Understood.",
        source_context={"surface": "channel", "platform": "openclaw"},
    )

    rows = [
        json.loads(line)
        for line in user_heuristics_path().read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert rows
    for row in rows:
        provenance = row["provenance"]
        assert provenance["source_id"] == "profile:confirmed"
        assert provenance["content_hash"] == hashlib.sha256(
            str(row["text"]).encode()
        ).hexdigest()


def test_dense_operator_profile_stays_local_and_tracks_project_focus() -> None:
    append_conversation_event(
        session_id="openclaw:dense-profile",
        user_input=(
            "I am building a Telegram bot in Python. Use official docs and GitHub repos first. "
            "Keep answers concise and brutally honest."
        ),
        assistant_output="Stored.",
        source_context={"surface": "channel", "platform": "openclaw"},
    )

    profile = load_operator_dense_profile()

    assert profile["share_scope"] == "local_only"
    assert "LOCAL_ONLY" in list(profile.get("policy_tags") or [])
    assert list(profile.get("active_projects") or []) == []
    assert "official_docs_first" in list(profile.get("source_preferences") or [])
    assert "python" in list(profile.get("preferred_stacks") or [])
    assert "concise_direct" in list(profile.get("response_style") or [])
    assert "telegram bot build" not in str(profile.get("dense_summary") or "").lower()


def test_new_name_replaces_old_name_memory() -> None:
    append_conversation_event(
        session_id="openclaw:test-user",
        user_input="My name is Operator.",
        assistant_output="Noted.",
        source_context={"surface": "channel", "platform": "openclaw"},
    )
    append_conversation_event(
        session_id="openclaw:test-user",
        user_input="Call me SL now.",
        assistant_output="Understood.",
        source_context={"surface": "channel", "platform": "openclaw"},
    )

    remembered = "\n".join(
        summarize_memory(chat_id="openclaw:test-user", limit=20)
    ).lower()
    # Neither statement becomes a memory fact: names live in the Operator Profile, where a
    # second name is a conflict the user resolves, not a silent replacement.
    assert "operator name is operator" not in remembered
    assert "operator name is sl" not in remembered


def test_session_scope_command_defaults_to_local_and_can_switch_to_hive() -> None:
    session_id = "openclaw:privacy-check"
    ensure_chat_namespace(session_id)
    assert "PRIVATE VAULT" in describe_session_memory_policy(session_id)

    handled, response = maybe_handle_memory_command(
        "shared pack except my real name and address",
        session_id=session_id,
    )
    assert handled is True
    assert "SHARED PACK" in response

    policy = session_memory_policy(session_id)
    assert policy["share_scope"] == "hive_mind"
    assert policy["realm_label"] == "SHARED PACK"
    assert "my real name" in policy["restricted_terms"]
    assert "address" in policy["restricted_terms"]


def test_session_summaries_become_searchable_for_new_sessions() -> None:
    append_conversation_event(
        session_id="openclaw:session-a",
        user_input="We are debugging the telegram installer and OpenClaw continuity issue.",
        assistant_output="I'll inspect the installer and persistence path.",
        source_context={"surface": "channel", "platform": "openclaw"},
    )
    append_conversation_event(
        session_id="openclaw:session-a",
        user_input="The problem is that a fresh session forgets prior continuity.",
        assistant_output="I will preserve the conversation and add session summaries.",
        source_context={"surface": "channel", "platform": "openclaw"},
    )

    ensure_chat_namespace("openclaw:new-session")
    grant_context_import(
        "openclaw:new-session",
        scope="chat",
        source_id="chat:openclaw:session-a",
    )
    summaries = search_session_summaries(
        "telegram installer continuity",
        access_policy=ContextAccessPolicy.for_request(
            session_id="openclaw:new-session",
            source_context={"surface": "local"},
        ),
        topic_hints=["openclaw", "installer"],
        limit=3,
        exclude_session_id="openclaw:new-session",
    )
    assert summaries
    assert any("continuity" in str(item.get("summary") or "").lower() for item in summaries)


def test_public_commons_alias_maps_to_public_knowledge() -> None:
    session_id = "openclaw:public-commons"
    ensure_chat_namespace(session_id)
    handled, response = maybe_handle_memory_command("public commons", session_id=session_id)
    assert handled is True
    assert "HIVE/PUBLIC COMMONS" in response
    policy = session_memory_policy(session_id)
    assert policy["share_scope"] == "public_knowledge"
    assert policy["realm_label"] == "HIVE/PUBLIC COMMONS"


def test_hive_mind_task_question_is_not_treated_as_memory_scope_command() -> None:
    handled, response = maybe_handle_memory_command(
        "what are the tasks available for Hive mind?",
        session_id="openclaw:hive-query",
    )
    assert handled is False
    assert response == ""


def test_style_instruction_with_a_real_task_is_answered_not_saved() -> None:
    # "Answer in short Telegram dev style: alpha vs beta readiness in one sentence." must NOT be
    # swallowed as a bare style-save — defer so the turn answers it (the model applies the style).
    handled, _ = maybe_handle_preference_command(
        "Answer in short Telegram dev style: alpha vs beta readiness in one sentence."
    )
    assert handled is False
    handled, _ = maybe_handle_preference_command("In telegram style, tell me a joke")
    assert handled is False
    # a bare style preference (no attached task) is not swallowed here either: response style is
    # an Operator Profile item now (a candidate the user confirms), never a silent style_notes save
    handled, _response = maybe_handle_preference_command("answer in telegram style")
    assert handled is False
    assert load_preferences().style_notes == ""


def test_deep_reasoning_defaults_off_and_is_switchable(monkeypatch) -> None:
    monkeypatch.delenv("VOOL_DEEP_REASONING", raising=False)
    # Default: off (fast local chat).
    assert load_preferences().deep_reasoning is False
    from core.reasoning_mode import deep_reasoning_enabled

    # Turn it on via a natural command.
    handled, response = maybe_handle_preference_command("think harder")
    assert handled is True and "reasoning on" in response.lower()
    assert load_preferences().deep_reasoning is True
    assert deep_reasoning_enabled() is True

    # And off again.
    handled, _ = maybe_handle_preference_command("reasoning off")
    assert handled is True
    assert load_preferences().deep_reasoning is False
    assert deep_reasoning_enabled() is False

    # Env override wins (for CI / tests).
    monkeypatch.setenv("VOOL_DEEP_REASONING", "1")
    assert deep_reasoning_enabled() is True


# --- A refusal is available to the reader, but is not material for the next prompt -------------


def test_a_published_refusal_does_not_re_enter_the_next_prompt(tmp_path, monkeypatch) -> None:
    """Measured live 2026-09-09 on the built app: three turns, one chat, distinct questions.

        U: Compare electric cars and gasoline cars on purchase cost, range and emissions.
        A: I can't publish an answer to this ... (request: "Compare electric cars and ...")
        U: Answer in exactly 3 bullet points: why is the sky blue?
        A: - Electric cars typically have higher purchase costs than gasoline cars ...

    The second question was answered with the FIRST question's subject, silently. It reproduces
    only after a refusal: the same question in a fresh session, and the same question after an
    ANSWERED turn, both answer the sky correctly. The runtime asked the right question -- its own
    events show no rewrite -- so what carried the old request forward was history hydration
    replaying the refusal, request echo and all, as if it were an answer. That is precisely the
    harm this hydration gate already names for withheld payloads: "the model would re-quote or
    re-synthesize it".
    """
    from core import runtime_paths
    from core.context_history_authority import is_closed_exchange_marker
    from core.grounding_publication import is_typed_refusal_text
    from core.persistent_memory import augment_history_from_session_log

    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    try:
        session = "openclaw:" + hashlib.sha256(b"refusal-replay").hexdigest()[:20]
        asked = "Compare electric cars and gasoline cars on purchase cost, range and emissions."
        refusal = (
            "I can't publish an answer to this: it needed current information, and nothing in "
            "what this turn actually retrieved supports the answer that was written. "
            f'(request: "{asked}")'
        )
        assert is_typed_refusal_text(refusal), "the fixture must be a refusal this module ships"
        append_conversation_event(
            session_id=session, user_input=asked, assistant_output=refusal, source_context={}
        )

        history = augment_history_from_session_log(
            [], session_id=session, user_text="Answer in exactly 3 bullet points: why is the sky blue?"
        )
        # The CONTRACT is that the refusal TEXT never re-enters, not that the assistant row
        # vanishes. Deleting the row outright was the first shape of this fix and it stranded the
        # user's request as an unanswered ask -- see
        # `test_a_suppressed_exchange_is_closed_not_left_pending`. What may stand in that slot is
        # the closed-exchange notice, which carries no refusal text and no answer content.
        assistant_turns = [m for m in history if m.get("role") == "assistant"]
        assert not any(
            is_typed_refusal_text(str(m.get("content") or "")) for m in assistant_turns
        ), f"the refusal re-entered the prompt: {assistant_turns}"
        assert all(
            is_closed_exchange_marker(m.get("content")) for m in assistant_turns
        ), f"only the closed-exchange notice may stand in for a held-back refusal: {assistant_turns}"
        assert asked not in " ".join(str(m.get("content") or "") for m in assistant_turns), (
            "the refused request was re-quoted back into the prompt as assistant content"
        )
        # The user's own words are untouched -- only the runtime's refusal is held back.
        assert any(m.get("role") == "user" and m.get("content") == asked for m in history)

        # CONTROL: an ordinary answer must still hydrate, or this gate would erase the thread.
        answered = "openclaw:" + hashlib.sha256(b"refusal-replay-ok").hexdigest()[:20]
        append_conversation_event(
            session_id=answered,
            user_input="What is the chemical symbol for gold?",
            assistant_output="The chemical symbol for gold is Au.",
            source_context={},
        )
        kept = augment_history_from_session_log(
            [], session_id=answered, user_text="And silver?"
        )
        assert any(
            m.get("role") == "assistant" and "Au" in str(m.get("content"))
            for m in kept
        ), f"an ordinary answer was dropped from history: {kept}"
    finally:
        runtime_paths.configure_runtime_home(None)


def test_every_published_refusal_family_is_held_back_not_just_one(tmp_path, monkeypatch) -> None:
    """One family covered is not the class covered — measured, after getting it wrong.

    The first version of the hydration gate knew only `core.grounding_publication`'s three leads.
    The very next served pack showed the hole: acceptance turn 14 is refused by the AUTHORSHIP
    family ("I can't publish this answer: nothing this turn retrieved, computed or observed backs
    it"), which that module never emits, so turn 15 went on being served turn 14's answer. Every
    module that publishes a refusal lead has to be represented, and this test is what says so.
    """
    import core.grounding_publication as grounding
    from core import runtime_paths
    from core.context_history_authority import is_closed_exchange_marker
    from core.final_answer_authorship import UNCERTIFIED_AUTHOR_NOTICE_LEAD
    from core.grounding_publication import is_typed_refusal_text
    from core.persistent_memory import augment_history_from_session_log

    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    try:
        families = {
            "grounding": grounding._REFUSAL_LEAD,
            "re_presentation": grounding._RE_PRESENTATION_REFUSAL_LEAD,
            "re_presentation_withheld": grounding._RE_PRESENTATION_OF_WITHHELD_LEAD,
            "uncertified_author": UNCERTIFIED_AUTHOR_NOTICE_LEAD,
        }
        for name, lead in families.items():
            published = f'{lead} (request: "Compare electric cars and gasoline cars.")'
            assert is_typed_refusal_text(published), f"{name} refusal not recognised"
            session = "openclaw:" + hashlib.sha256(name.encode()).hexdigest()[:20]
            append_conversation_event(
                session_id=session,
                user_input="Compare electric cars and gasoline cars.",
                assistant_output=published,
                source_context={},
            )
            history = augment_history_from_session_log(
                [], session_id=session, user_text="Why is the sky blue?"
            )
            served = [m for m in history if m.get("role") == "assistant"]
            assert not any(
                is_typed_refusal_text(str(m.get("content") or "")) for m in served
            ), f"the {name} refusal re-entered the prompt"
            assert all(is_closed_exchange_marker(m.get("content")) for m in served), (
                f"only the closed-exchange notice may stand in for the {name} refusal: {served}"
            )
    finally:
        runtime_paths.configure_runtime_home(None)


def test_a_suppressed_exchange_is_closed_not_left_pending(tmp_path, monkeypatch) -> None:
    """Holding the refusal back is only half of it: the REQUEST must stop looking pending.

    Measured on build 7fe94596, one chat, the same pack that proved the holdback works. Turn 6
    ("Do not look anything up. From your own knowledge only: who wrote the novel 1984?") was
    REFUSED. Turn 7 asked something unrelated and the served answer opened:

        "The novel *1984* was written by George Orwell."

    So the runtime published, in its own voice, the very content it had declined to publish one
    turn earlier -- the contamination does not merely add noise, it DEFEATS the refusal.

    The cause is that A8 suppression is ROW-scoped while the semantic unit is the EXCHANGE.
    Dropping the assistant row alone hands the model a user request with nothing answering it,
    and a pending-looking request is what a model acts on. `enforce_history_budget` already
    states the same law for its own budgeting -- "a missing pair is preferable to a half-pair".
    """
    from core import runtime_paths
    from core.bootstrap_context import _client_conversation_history
    from core.context_history_authority import is_closed_exchange_marker, select_history_policy
    from core.final_answer_authorship import UNCERTIFIED_AUTHOR_NOTICE_LEAD
    from core.persistent_memory import augment_history_from_session_log

    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)

    asked = "Do not look anything up. From your own knowledge only: who wrote the novel 1984?"
    refusal = f'{UNCERTIFIED_AUTHOR_NOTICE_LEAD} nothing this turn retrieved backs it. (request: "{asked}")'
    next_question = "What is 10 percent of 250?"

    def _dangling_requests(rows: list[dict[str, str]]) -> list[str]:
        """User rows with no assistant row answering them -- the shape the model acts on."""
        return [
            str(row.get("content"))
            for index, row in enumerate(rows)
            if str(row.get("role")) == "user"
            and (index + 1 >= len(rows) or str(rows[index + 1].get("role")) != "assistant")
        ]

    try:
        # --- carrier 1: the client-carried transcript -------------------------------------
        selection = select_history_policy(
            scope_allows_transcript=True,
            expansion_hint=None,
            authority_reason="regression",
            requested_max_messages=10,
            requested_max_chars=5000,
        )
        carried = _client_conversation_history(
            {
                "client_conversation_history": [
                    {"role": "user", "content": asked, "turn_id": "t1"},
                    {"role": "assistant", "content": refusal, "turn_id": "t1"},
                    {"role": "user", "content": next_question, "turn_id": "t2"},
                ]
            },
            current_user_text=next_question,
            current_user_raw_text=next_question,
            current_turn_id="t2",
            max_messages=selection.max_messages,
            max_chars=selection.max_chars,
            selection=selection,
            session_id="closed-exchange-regression",
            project_id="",
        )
        assert _dangling_requests(carried) == [], (
            f"the refused request was handed to the model as still-pending work: {carried}"
        )
        assert refusal not in " ".join(str(row.get("content")) for row in carried), (
            "the refusal text itself must still be held back (the 7fe94596 repair)"
        )
        # The request text SURVIVES: an explicit retry, a previous-turn operand, a correction and
        # a standing prohibition all need their referent. Only its lifecycle is restated.
        assert any(str(row.get("content")) == asked for row in carried), (
            f"the user's own request must not be deleted from history: {carried}"
        )
        assert any(is_closed_exchange_marker(row.get("content")) for row in carried)

        # --- carrier 2: the persisted session log -----------------------------------------
        session = "openclaw:" + hashlib.sha256(b"closed-exchange").hexdigest()[:20]
        append_conversation_event(
            session_id=session, user_input=asked, assistant_output=refusal, source_context={}
        )
        hydrated = augment_history_from_session_log(
            [], session_id=session, user_text=next_question
        )
        prior = [row for row in hydrated if str(row.get("content")) != next_question]
        assert _dangling_requests(prior) == [], (
            f"the session log stranded the refused request as pending: {hydrated}"
        )

        # --- CONTROL: an ordinary answered exchange is untouched ---------------------------
        answered = "openclaw:" + hashlib.sha256(b"closed-exchange-ok").hexdigest()[:20]
        append_conversation_event(
            session_id=answered,
            user_input="What is the chemical symbol for gold?",
            assistant_output="The chemical symbol for gold is Au.",
            source_context={},
        )
        kept = augment_history_from_session_log(
            [], session_id=answered, user_text="And silver?"
        )
        assert any("Au" in str(row.get("content")) for row in kept), (
            f"an ordinary answer must still hydrate unchanged: {kept}"
        )
        assert not any(is_closed_exchange_marker(row.get("content")) for row in kept), (
            f"an answered exchange must never be marked closed-unavailable: {kept}"
        )
    finally:
        runtime_paths.configure_runtime_home(None)
