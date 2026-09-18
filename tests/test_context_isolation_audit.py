"""Model-free context isolation and context-manifest audit cases."""
from __future__ import annotations

import uuid

import pytest

from core.context_namespace import ensure_chat_namespace, grant_context_import
from core.human_input_adapter import HumanInputInterpretation, adapt_user_input
from core.identity_manager import load_active_persona
from core.memory.entries import add_memory_fact, set_session_meta
from core.memory.files import append_jsonl, session_summaries_path
from core.persistent_memory import append_conversation_event
from core.prompt_normalizer import normalize_prompt
from core.provenance_store import load_manifest
from core.task_router import classify, create_task_record
from core.tiered_context_loader import TieredContextLoader
from storage.dialogue_memory import recent_dialogue_turns


@pytest.fixture
def isolated_context_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    monkeypatch.delenv("VOOL_CONTEXT_CAPSULE_V2", raising=False)
    from core import runtime_paths

    monkeypatch.setattr(runtime_paths, "_VOOL_HOME_OVERRIDE", None, raising=False)
    return tmp_path


def _sid(label: str) -> str:
    return f"audit:{label}:{uuid.uuid4().hex}"


def _load(session_id: str, prompt: str, *, source_context: dict[str, object] | None = None):
    from core.context_namespace import ensure_chat_namespace

    ensure_chat_namespace(session_id)
    task = create_task_record(prompt)
    interpretation = HumanInputInterpretation(
        raw_text=prompt,
        normalized_text=prompt,
        reconstructed_text=prompt,
        intent_mode="request",
        topic_hints=[],
        reference_targets=[],
        understanding_confidence=0.9,
        quality_flags=[],
    )
    context = {
        "surface": "openclaw",
        "platform": "openclaw",
        "allow_user_profile_context": False,
        "allow_project_context": False,
        "allow_shared_context": False,
        "allow_action_receipts": False,
        "allow_cold_context": False,
    }
    context.update(source_context or {})
    return TieredContextLoader().load(
        task=task,
        classification=classify(task.task_summary, context=interpretation.as_context()),
        interpretation=interpretation,
        persona=load_active_persona("default"),
        session_id=session_id,
        source_context=context,
    )


def _context_blob(result) -> str:
    return "\n".join(
        [
            *(item.content for item in result.bootstrap_items),
            *(item.content for item in result.relevant_items),
            *(item.content for item in result.cold_items),
        ]
    )


def test_default_deny_blocks_foreign_chat_and_keeps_current_chat(isolated_context_home):
    target = _sid("target")
    foreign = _sid("foreign")
    ensure_chat_namespace(target)
    ensure_chat_namespace(foreign)
    add_memory_fact(
        "Current chat marker CHAT-ONLY-7419",
        session_id=target,
        source="audit",
    )
    add_memory_fact(
        "Foreign chat marker FOREIGN-ONLY-8624",
        session_id=foreign,
        source="audit",
    )

    result = _load(target, "recall the chat marker", source_context={"chat_id": target})
    blob = _context_blob(result)

    assert "CHAT-ONLY-7419" in blob
    assert "FOREIGN-ONLY-8624" not in blob
    assert result.report.chat_id == target
    assert result.report.capsule_version == "none"


def test_group_surface_keeps_its_own_chat_but_not_foreign_memory(
    isolated_context_home,
):
    target = _sid("group-target")
    foreign = _sid("group-foreign")
    ensure_chat_namespace(target)
    ensure_chat_namespace(foreign)
    assert add_memory_fact(
        "Current group marker GROUP-CURRENT-5219",
        session_id=target,
        source="audit",
    )
    assert add_memory_fact(
        "Foreign group marker GROUP-FOREIGN-7831",
        session_id=foreign,
        source="audit",
    )

    result = _load(
        target,
        "recall the group marker",
        source_context={
            "surface": "telegram",
            "platform": "telegram",
            "is_group": True,
        },
    )
    blob = _context_blob(result)

    assert "GROUP-CURRENT-5219" in blob
    assert "GROUP-FOREIGN-7831" not in blob


def test_project_context_requires_explicit_matching_project(isolated_context_home):
    source_chat_a = _sid("source-a")
    source_chat_b = _sid("source-b")
    target_chat = _sid("target")
    ensure_chat_namespace(source_chat_a, project_id="project-a")
    ensure_chat_namespace(source_chat_b, project_id="project-b")
    grant_context_import(
        source_chat_a,
        scope="project",
        source_id="project:project-a",
        source_project_id="project-a",
    )
    grant_context_import(
        source_chat_b,
        scope="project",
        source_id="project:project-b",
        source_project_id="project-b",
    )
    assert add_memory_fact(
        "Project A marker PROJECT-A-3141",
        session_id=source_chat_a,
        project_id="project-a",
        source="audit",
        scope="project",
    )
    assert add_memory_fact(
        "Project B marker PROJECT-B-2718",
        session_id=source_chat_b,
        project_id="project-b",
        source="audit",
        scope="project",
    )
    ensure_chat_namespace(target_chat, project_id="project-b")

    isolated = _load(
        target_chat,
        "find the project marker",
        source_context={"chat_id": target_chat},
    )
    assert "PROJECT-A-3141" not in _context_blob(isolated)
    assert "PROJECT-B-2718" not in _context_blob(isolated)

    grant_context_import(
        target_chat,
        scope="project",
        source_id="project:project-b",
        source_project_id="project-b",
    )
    shared = _load(
        target_chat,
        "find the project marker",
        source_context={"chat_id": target_chat},
    )
    assert "PROJECT-A-3141" not in _context_blob(shared)
    assert "PROJECT-B-2718" in _context_blob(shared)


def test_archived_summary_and_global_shared_sources_stay_out_by_default(isolated_context_home):
    archived_chat = _sid("archived")
    target = _sid("new")
    ensure_chat_namespace(archived_chat)
    set_session_meta(archived_chat, archived=True)
    summary = "Archived-only marker ARCHIVE-5508 from another conversation."
    append_jsonl(
        session_summaries_path(),
        {
            "created_at": "2026-07-27T12:00:00+00:00",
            "session_id": archived_chat,
            "summary": summary,
            "keywords": ["archived-only", "marker", "archive-5508"],
            "turn_count": 2,
        },
    )

    result = _load(target, "recall the archived marker", source_context={"chat_id": target})
    assert "ARCHIVE-5508" not in _context_blob(result)
    assert not any(item.source_type in {"swarm_context", "final_response", "payment_status"} for item in result.relevant_items)


def test_semantic_memory_requires_current_chat_provenance(isolated_context_home):
    from core.context_retrieval import inject_retrieved, store_turn

    foreign = _sid("semantic-foreign")
    target = _sid("semantic-target")
    store_turn(foreign, "Remember the semantic foreign marker SEMANTIC-9090 for this chat.", "Stored.")
    transcript = [{"role": "user", "content": "What is the semantic marker?"}]

    result = inject_retrieved(target, "semantic foreign marker", transcript)
    assert "SEMANTIC-9090" not in "\n".join(item.get("content", "") for item in result)


def test_new_chat_starts_without_historic_transcript_and_manifest_is_traceable(isolated_context_home):
    from core.bootstrap_context import canonical_runtime_transcript

    old = _sid("old")
    fresh = _sid("fresh")
    append_conversation_event(
        session_id=old,
        user_input="Old chat marker OLD-4404",
        assistant_output="Recorded.",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )
    transcript, source = canonical_runtime_transcript(
        session_id=fresh,
        source_context={"chat_id": fresh},
        current_user_text="Hi",
    )
    assert transcript == []
    assert source == "scope_denied"

    result = _load(fresh, "Hi", source_context={"chat_id": fresh})
    manifest_id = result.report.context_manifest_id
    assert manifest_id
    manifest = load_manifest(manifest_id)
    assert manifest is not None
    assert manifest["chat_id"] == fresh
    assert manifest["capsule_version"] == "none"
    assert isinstance(manifest["selected_items"], list)
    assert isinstance(manifest["excluded_items"], list)
    assert manifest["trace_id"] == result.report.trace_id
    assert manifest["access_policy"]["chat_id"] == fresh
    assert manifest["access_policy"]["namespace_state"] == "active"
    assert isinstance(manifest["candidate_items"], list)
    assert result.report.context_manifest_required is True


def test_resolved_reference_is_never_replayed_as_user_authored_context(isolated_context_home):
    from core.bootstrap_context import canonical_runtime_transcript

    session_id = _sid("resolved-reference")
    ensure_chat_namespace(session_id)
    adapt_user_input("Please explain the Discord integration boundaries.", session_id=session_id)
    follow_up = adapt_user_input("What about it?", session_id=session_id)

    assert follow_up.reference_targets == ["discord integration", "discord"]
    assert follow_up.reconstructed_text == follow_up.normalized_text
    assert "Context subject:" not in follow_up.reconstructed_text

    stored_turns = recent_dialogue_turns(session_id, limit=4, speaker_roles=("user", "assistant"))
    stored_follow_up = next(turn for turn in stored_turns if turn["raw_input"] == "What about it?")
    assert stored_follow_up["normalized_input"] == "What about it?"
    assert "Context subject:" not in stored_follow_up["reconstructed_input"]

    transcript, transcript_source = canonical_runtime_transcript(
        session_id=session_id,
        source_context={"chat_id": session_id, "surface": "openclaw", "platform": "openclaw"},
        current_user_text=follow_up.normalized_text,
    )
    assert transcript_source == "structured_dialogue_memory"
    assert "Context subject:" not in "\n".join(item["content"] for item in transcript)

    task = create_task_record(follow_up.normalized_text)
    classification = classify(task.task_summary, context=follow_up.as_context())
    source_context = {
        "chat_id": session_id,
        "session_id": session_id,
        "surface": "openclaw",
        "platform": "openclaw",
        "allow_user_profile_context": False,
        "allow_project_context": False,
        "allow_shared_context": False,
    }
    context_result = TieredContextLoader().load(
        task=task,
        classification=classification,
        interpretation=follow_up,
        persona=load_active_persona("default"),
        session_id=session_id,
        source_context=source_context,
    )
    assert "Context subject:" not in _context_blob(context_result)

    request = normalize_prompt(
        task=task,
        classification=classification,
        interpretation=follow_up,
        context_result=context_result,
        persona=load_active_persona("default"),
        output_mode="plain_text",
        task_kind="normalization_assist",
        trace_id=task.task_id,
        surface="openclaw",
        source_context=source_context,
    )
    assert request.messages[-1].role == "user"
    assert request.messages[-1].content == "What about it?"
    assert "Context subject:" not in "\n".join(message.content for message in request.messages)
