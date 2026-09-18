"""Seed a real VOOL home through the PRODUCTION writers, so the portability lane is tested
against the exact shapes a served turn persists — never hand-built stand-ins.

Everything here is hermetic: no model, no network (the repo conftest enforces the network seal).
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

SESSION = "openclaw:aaaaaaaaaaaaaaaaaaaa"
OTHER = "openclaw:bbbbbbbbbbbbbbbbbbbb"

#: A high-confidence secret shape the redaction pass must never leak, planted through a path
#: that BYPASSES the write-time redaction (a legacy row written before redaction existed).
PLANTED_SECRET = "sk-test1234567890abcdefghijklmnop"
PLANTED_PRIVATE_PATH = "/Users/example-user/secret-notes/tokens.txt"


def seed_turns(
    session_id: str = SESSION,
    *,
    turns: list[tuple[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Persist user/assistant exchanges through the production turn writer."""
    from core.persistent_memory import append_conversation_event

    rows: list[tuple[str, str]] = turns or [
        ("What did we decide about the launch window?", "The launch window is Thursday 09:00 UTC."),
        ("And the fallback?", "The fallback is Friday 09:00 UTC, same pad."),
    ]
    for user_text, assistant_text in rows:
        append_conversation_event(
            session_id=session_id,
            user_input=user_text,
            assistant_output=assistant_text,
            source_context={"surface": "test", "platform": "pytest"},
        )
    from core.memory.entries import recent_conversation_events

    return list(
        recent_conversation_events(session_id, limit=50, include_artifacts=True)
    )


def plant_legacy_secret_turn(session_id: str = SESSION) -> None:
    """Write one transcript row WITH a raw secret + private path, bypassing write-time redaction.

    This simulates the legacy-row hazard: rows persisted before `redact_secrets` guarded the
    writer. Export must be the last line of defense and scrub them anyway.
    """
    from core.memory.files import append_sequenced_jsonl, conversation_log_path

    payload = {
        "ts": "2026-09-02T00:00:00+00:00",
        "session_id": session_id,
        "request_id": "",
        "project_id": "",
        "surface": "legacy",
        "platform": "pytest",
        "user": f"my key is {PLANTED_SECRET} and notes live in {PLANTED_PRIVATE_PATH}",
        "assistant": "noted.",
        "history_message_count": 0,
        "share_scope": "private",
        "realm_label": "Private",
        "restricted_terms": [],
    }
    append_sequenced_jsonl(conversation_log_path(), payload)


def seed_tool_receipt(session_id: str = SESSION, *, raw_secret_argument: bool = False) -> dict[str, Any]:
    """Persist one tool receipt through the production writer.

    `raw_secret_argument=True` writes an argument the write-side scrub would normally remove
    (injected with raw SQL afterwards) to prove the export-side redaction pass is defense in depth.
    """
    from core.runtime_continuity import store_tool_receipt

    receipt = store_tool_receipt(
        receipt_key=f"rk-{uuid.uuid4().hex[:16]}",
        session_id=session_id,
        checkpoint_id=f"ck-{uuid.uuid4().hex[:12]}",
        tool_name="http_fetch",
        idempotency_key=f"idem-{uuid.uuid4().hex[:12]}",
        arguments={"host": "api.weather.gov", "path": "/points/OKW/42"},
        execution={"ok": True, "status": 200},
    )
    if raw_secret_argument:
        import sqlite3

        from storage.db import active_default_db_path

        conn = sqlite3.connect(active_default_db_path())
        try:
            conn.execute(
                "UPDATE runtime_tool_receipts SET arguments_json = ? WHERE receipt_key = ?",
                (
                    json.dumps({"host": "api.weather.gov", "api_key": PLANTED_SECRET}),
                    receipt["receipt_key"],
                ),
            )
            conn.commit()
        finally:
            conn.close()
    return receipt


def seed_obligations(request_id: str, session_id: str = SESSION) -> dict[str, Any]:
    """Open one obligation set with a terminal disposition, through the production ledger.

    The turn rows are written inside a REAL bound request context, so their payload lineage is
    the same request id the ledger keys on — the join the collector must find.
    """
    from core.conductor.obligation_ledger import open_obligation_set, record_disposition
    from core.persistent_memory import append_conversation_event
    from core.semantic.semantic_admissions import bound_request_context

    with bound_request_context(request_id):
        append_conversation_event(
            session_id=session_id,
            user_input="What did we decide about the launch window? (obligated)",
            assistant_output="Recorded under its obligation set.",
            source_context={"surface": "test", "platform": "pytest"},
        )
    opened = open_obligation_set(
        obligations=[{"obligation_id": "ob-1", "text": "answer the launch question", "kind": "prose"}],
        request_text="What did we decide about the launch window?",
        request_id=request_id,
    )
    record_disposition(
        opened["set_id"], opened["version"], "ob-1", "satisfied", evidence_source="model_prose"
    )
    return opened


def seed_session_event(session_id: str = SESSION) -> None:
    """Persist one runtime activity event (the turn trace trail) through the production writer."""
    from core.runtime_continuity import append_runtime_event

    append_runtime_event(
        session_id=session_id,
        event_type="turn.trace_completed",
        message="Turn trace completed.",
        details={
            "request_id": "req:test:launch",
            "chat_id": session_id,
            "route": "deterministic:test",
            "model": "test-model",
            "provider_id": "test-provider",
        },
    )


def seed_attachment(session_id: str = SESSION) -> dict[str, Any]:
    """Stage + bind one text attachment through the production attachment authority."""
    from core.chat_attachments import bind_to_turn, stage_attachment

    staged = stage_attachment(
        session_id=session_id,
        declared_name="launch-checklist.txt",
        declared_type="text/plain",
        data=b"checklist item 1\nchecklist item 2\n",
    )
    bound = bind_to_turn(
        session_id=session_id, turn_id="turn-1", attachment_ids=[staged["id"]]
    )
    return bound[0] if bound else staged


def seed_profile_item(session_id: str = SESSION) -> None:
    """Remember one operator profile item sourced from this session."""
    from core.operator_profile import remember

    remember(
        "owner_local",
        "response_style",
        "concise, no preamble",
        session_id=session_id,
        actor="pytest",
    )


def seed_foreign_session() -> None:
    """One turn for a DIFFERENT session, proving export is scoped and never leaks siblings."""
    seed_turns(OTHER, turns=[("foreign question", "foreign answer")])


def set_session_meta(session_id: str = SESSION, title: str = "Launch planning") -> None:
    from core.memory.entries import set_session_meta as _set

    _set(session_id, title=title)


def export_dir(home: Path) -> Path:
    return home / "data" / "session_bundles"
