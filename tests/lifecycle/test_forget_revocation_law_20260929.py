"""q90-lifecycle — the forget revocation law across every serving carrier.

Landed law (lead slice d1a6c69e + lifecycle derivative-surface extension):
forget_session_memory records a durable (token, chat) revocation in the
memory_revocations ledger, sweeps derived surfaces (topic archives, capsule
versions, learning shards, session-state text, session summaries), and the
prompt carriers — canonical transcript assembly and dialogue context items —
drop rows carrying a revoked token, so the value cannot re-enter a prompt
even from a carrier the sweep cannot reach. Turn rows intentionally keep
referential identity; serving is the bar, not row deletion.

This suite pins the whole law at its serving seams (original night-market
reproduction shape + fresh harbor-ferry domain + preservation controls +
the user-facing chat command + the cross-chat summary sweep this lane
added).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import core.context_retrieval as cr
from core.context_namespace import ensure_chat_namespace
from core.memory.entries import resolve_memory_access_policy
from core.runtime_paths import configure_runtime_home


@pytest.fixture()
def fresh_profile(tmp_path, monkeypatch):
    import core.embedding_service as embedding_service

    home = tmp_path / "profile"
    home.mkdir()
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_HOME", str(home))
    configure_runtime_home(home)
    embedding_service._best_embed_model = lambda: None
    from storage.migrations import run_migrations

    run_migrations()
    yield str(home)
    configure_runtime_home(None)


def _policy(chat: str):
    ensure_chat_namespace(chat, grant_current_receipts=False)
    return resolve_memory_access_policy(chat_id=chat)


def _turn(chat: str, text: str, role: str = "user") -> str:
    from storage.dialogue_memory import record_dialogue_turn

    return record_dialogue_turn(
        chat,
        raw_input=text,
        normalized_input=text,
        reconstructed_input=text,
        speaker_role=role,
        topic_hints=[],
        reference_targets=[],
        understanding_confidence=0.9,
        quality_flags=[],
    )


def _archive(chat: str, subject: str) -> None:
    from storage.dialogue_memory import archive_dialogue_topic

    archive_dialogue_topic(
        chat,
        last_subject=subject,
        topic_hints=[],
        current_user_goal=subject,
        closure_status="resolved",
        closure_reason="topic_shift",
        closing_user_input="noted",
    )


def _summary_row(home: str, chat: str, summary: str, keywords: list[str]) -> None:
    path = Path(home) / "data" / "session_summaries.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "session_id": chat,
        "summary": summary,
        "keywords": keywords,
        "created_at": "2026-09-29T00:00:00+00:00",
        "status": "active",
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _forget(home: str, chat: str, token: str) -> int:
    return cr.forget_session_memory(
        chat,
        token,
        access_policy=_policy(chat),
        source_context={"chat_id": chat, "runtime_home": home},
    )


def _transcript_text(home: str, chat: str, question: str) -> str:
    from core.bootstrap_context import canonical_runtime_transcript

    transcript, _source = canonical_runtime_transcript(
        session_id=chat,
        source_context=None,
        current_user_text=question,
        max_messages=10,
        max_chars=5000,
    )
    return "\n".join(str(item.get("content") or "") for item in transcript)


def _dialogue_item_texts(chat: str) -> list[str]:
    from core.tiered_context_loader import _dialogue_items

    return [str(item.content) for item in _dialogue_items(chat)]


def _summaries(home: str) -> list[dict]:
    path = Path(home) / "data" / "session_summaries.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_token_forget_gates_every_serving_carrier(fresh_profile):
    """Original reproduction shape: transcript + dialogue items stop serving
    the revoked token; unrelated rows keep serving; the foreign chat's
    identical token is untouched in ITS transcript."""
    chat = "q90-f1-market"
    foreign = "q90-f1-foreign"
    ensure_chat_namespace(chat, grant_current_receipts=False)
    ensure_chat_namespace(foreign, grant_current_receipts=False)
    token = "Noodle Alley"

    _turn(chat, "The Noodle Alley stall at the night market closes at 23:30 on Fridays.")
    _turn(chat, "23:30 Friday closing for Noodle Alley - noted.", role="assistant")
    _turn(chat, "Good to remember for late snack runs.")
    _turn(foreign, "The Noodle Alley stall at the night market closes at 23:30 on Fridays.")

    _forget(fresh_profile, chat, token)

    assert cr.revoked_tokens_for_chat(chat) == (
        " ".join(token.casefold().split()),
    ), {"note": "revocation must be durably recorded, chat-scoped"}
    assert cr.revoked_tokens_for_chat(foreign) == (), {
        "note": "foreign chat must carry no revocation"
    }

    transcript = _transcript_text(fresh_profile, chat, "When does the stall shut?")
    assert "Noodle Alley" not in transcript and "23:30" not in transcript, {
        "transcript": transcript,
    }
    assert "late snack runs" in transcript, {
        "transcript": transcript,
        "note": "unrelated same-chat rows must keep serving",
    }
    items = _dialogue_item_texts(chat)
    assert not any("Noodle Alley" in t for t in items), {"items": items}
    foreign_transcript = _transcript_text(fresh_profile, foreign, "When does the stall shut?")
    assert "Noodle Alley" in foreign_transcript and "23:30" in foreign_transcript, {
        "foreign_transcript": foreign_transcript,
        "note": "foreign chat's identical token must keep serving there",
    }


def test_token_forget_new_domain_sweeps_archives_state_and_summaries(fresh_profile):
    """Fresh entities (harbor ferry, 06:15): derived surfaces are swept —
    topic archives deleted, session-state text cleared, session summaries
    (the CROSS-CHAT continuity surface) deleted for this chat only."""
    chat = "q90-f2-harbor"
    other = "q90-f2-unrelated"
    ensure_chat_namespace(chat, grant_current_receipts=False)
    ensure_chat_namespace(other, grant_current_receipts=False)
    from storage.dialogue_memory import (
        get_dialogue_session,
        recent_archived_dialogue_topics,
        update_dialogue_session,
    )

    _turn(chat, "The Kestrel ferry from the harbor leaves at 06:15 on winter mornings.")
    _turn(chat, "06:15 winter departure for the Kestrel ferry - noted.", role="assistant")
    _turn(chat, "I take it when I visit the shipyard on Tuesdays.")
    _archive(chat, "Kestrel ferry winter departure 06:15")
    update_dialogue_session(
        chat,
        last_subject="Kestrel ferry departure",
        topic_hints=[],
        last_intent_mode="statement",
        current_user_goal="Remember the 06:15 Kestrel ferry departure",
    )
    _summary_row(
        fresh_profile,
        chat,
        "Recent asks: The Kestrel ferry from the harbor leaves at 06:15 on winter mornings.",
        ["kestrel", "ferry", "06:15"],
    )
    _summary_row(
        fresh_profile,
        chat,
        "Recent asks: I take it when I visit the shipyard on Tuesdays.",
        ["shipyard", "tuesdays"],
    )
    _summary_row(
        fresh_profile,
        other,
        "Recent asks: The Kestrel ferry leaves at 06:15 in winter.",
        ["kestrel", "ferry"],
    )

    _forget(fresh_profile, chat, "Kestrel ferry")

    assert not any(
        "Kestrel" in str(row.get("summary") or "")
        for row in recent_archived_dialogue_topics(chat, limit=10)
    ), {"note": "token-carrying topic archives must be swept"}
    session = get_dialogue_session(chat)
    assert "Kestrel" not in str(session.get("last_subject") or "") + str(
        session.get("current_user_goal") or ""
    ), {"session_row": session, "note": "session-state text must be cleared"}
    remaining = _summaries(fresh_profile)
    mine = [row for row in remaining if row["session_id"] == chat]
    others = [row for row in remaining if row["session_id"] == other]
    assert mine and all(
        "Kestrel" not in row["summary"] and "06:15" not in row["summary"]
        for row in mine
    ), {"chat_summaries": mine}
    assert any("Kestrel" in row["summary"] for row in others), {
        "other_summaries": others,
        "note": "foreign summary with identical token preserved",
    }
    transcript = _transcript_text(fresh_profile, chat, "When does the ferry leave?")
    assert "Kestrel" not in transcript and "06:15" not in transcript, {"transcript": transcript}
    assert "shipyard on Tuesdays" in transcript, {
        "transcript": transcript,
        "note": "unrelated same-chat turn keeps serving",
    }


def test_user_facing_forget_command_gates_serving_carriers(fresh_profile):
    """The chat 'forget ...' command funnels into the same law: after the
    command, the revoked token no longer serves from any carrier."""
    from core.persistent_memory import maybe_handle_memory_command
    from storage.dialogue_memory import update_dialogue_session

    chat = "q90-f3-clinic"
    ensure_chat_namespace(chat, grant_current_receipts=False)
    _turn(chat, "My physio slot at the Cedar clinic is moved to 17:45.")
    _turn(chat, "17:45 Cedar clinic physio slot - noted.", role="assistant")
    update_dialogue_session(
        chat,
        last_subject="Cedar clinic physio",
        topic_hints=[],
        last_intent_mode="statement",
    )

    handled, response = maybe_handle_memory_command(
        "forget Cedar clinic",
        session_id=chat,
        access_policy=_policy(chat),
        source_context={"chat_id": chat, "runtime_home": fresh_profile},
    )
    assert handled, {"response": response}

    transcript = _transcript_text(fresh_profile, chat, "When is my physio slot?")
    assert "Cedar clinic" not in transcript and "17:45" not in transcript, {
        "transcript": transcript,
        "command_response": response,
    }
    assert cr.revoked_tokens_for_chat(chat), {
        "note": "the command path must record the ledger revocation"
    }
