"""Land chosen chats from another app in VOOL's chat history.

The laws are the session-bundle importer's (core.session_portability.importer), applied to
history that arrives from outside VOOL:

1. **Never overwrite resident data.** Every imported chat gets a NEW VOOL chat id; no existing
   chat, message or title is touched.
2. **Inert content.** Only user and assistant messages land, as plain text in the import store
   (``core.chat_import.store``). Nothing is written to the conversation log or any memory store,
   so no fact capture, summary, heuristic, recall or training reader ever sees it, and nothing
   arrives as a system or tool instruction.
3. **Secrets masked.** Every title and message passes the session-bundle secret scrub
   (``core.session_portability.redaction.scrub_secret_text``); the count is reported.
4. **Forgotten stays forgotten.** Each chat is admitted under the same writer fence every memory
   writer uses: a chat carrying words the user asked VOOL to forget everywhere is held back.
5. **Idempotent.** A source chat is recognised by a digest of its app family and its own id. The
   same chat imported again is a no-op, reported as "already here".
6. **Atomic per chat, recoverable.** A chat's row and all its messages commit in ONE
   transaction, marked PREPARED. Its title and namespace are then written and it is marked
   COMPLETE. A crash between the two leaves a PREPARED row that the next run finishes, so a
   retry yields each chat exactly once; a crash before the commit leaves nothing.

Imports run in batches so a large history reports progress and never holds the database for
long. Message contents are never logged.
"""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
import sqlite3
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from core.chat_import.model import ImportedChat
from core.chat_import.store import (
    CHATS_TABLE,
    MESSAGES_TABLE,
    STATE_COMPLETE,
    STATE_PREPARED,
    connect,
    iso_time,
)

_LOG = logging.getLogger(__name__)

#: Chats committed per batch before titles are written and progress is reported.
BATCH_SIZE = 50

#: What a string is replaced with when credential material survives both scrubbers.
SECRET_MARKER = "[hidden]"

#: Formats that hold the same chats share a family, so a chat read once from an app's old
#: transcript files and once from its newer database is recognised as the same chat.
FAMILY_OF_FORMAT = {
    "openai_export": "export-mapping-tree",
    "anthropic_export": "export-message-list",
    "hermes_state_db": "local-agent",
    "openclaw_agent_db": "local-assistant",
    "openclaw_jsonl": "local-assistant",
    "cursor_state_vscdb": "local-code-editor",
}

_TITLE_CHARS = 80


@dataclass
class ImportOutcome:
    """What one import run did. Counts only; never a word of any message."""

    brought_in: int = 0
    already_here: int = 0
    empty: int = 0
    held_back: int = 0
    failed: int = 0
    secrets_masked: int = 0
    messages: int = 0
    session_ids: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "brought_in": self.brought_in,
            "already_here": self.already_here,
            "empty": self.empty,
            "held_back": self.held_back,
            "failed": self.failed,
            "secrets_masked": self.secrets_masked,
            "messages": self.messages,
        }


def source_key(chat: ImportedChat) -> str:
    """The digest that recognises a source chat again. Its own id when it has one; otherwise its
    title and first messages, so an id-less chat is still recognised on a second import."""
    family = FAMILY_OF_FORMAT.get(chat.source_format, chat.source_format)
    identity = chat.source_chat_id
    if not identity:
        sample = [chat.title] + [f"{m.role}:{m.text}" for m in chat.messages[:4]]
        identity = "content:" + hashlib.sha256(json.dumps(sample, ensure_ascii=False).encode("utf-8")).hexdigest()
    return hashlib.sha256(f"chat-import:{family}:{identity}".encode()).hexdigest()


def display_title(chat: ImportedChat, scrubbed_title: str, scrubbed_first_user: str) -> str:
    """The original title, or the first line the user wrote when the chat has none."""
    title = " ".join(scrubbed_title.split()).strip()
    if title:
        return title
    first = " ".join(scrubbed_first_user.split()).strip()
    return first[:_TITLE_CHARS]


def _namespace_deleted(session_id: str) -> bool:
    from core.context_namespace import load_chat_namespace

    namespace = load_chat_namespace(session_id)
    return namespace is not None and namespace.lifecycle_state == "deleted"


def _new_session_id(connection: sqlite3.Connection) -> str:
    from core.chat_session_identity import CANONICAL_PREFIX
    from core.context_namespace import load_chat_namespace

    while True:
        candidate = CANONICAL_PREFIX + secrets.token_hex(10)
        taken = connection.execute(f"SELECT 1 FROM {CHATS_TABLE} WHERE session_id = ?", (candidate,)).fetchone()
        if taken is None and load_chat_namespace(candidate) is None:
            return candidate


def existing_keys(keys: Iterable[str]) -> set[str]:
    """Which source keys are already here as a live chat (for the preview)."""
    wanted = list(dict.fromkeys(keys))
    if not wanted:
        return set()
    connection = connect()
    try:
        found: dict[str, str] = {}
        for start in range(0, len(wanted), 500):
            chunk = wanted[start : start + 500]
            marks = ",".join("?" for _ in chunk)
            for key, session_id in connection.execute(
                f"SELECT source_key, session_id FROM {CHATS_TABLE} WHERE source_key IN ({marks})", chunk
            ):
                found[str(key)] = str(session_id)
    finally:
        connection.close()
    return {key for key, session_id in found.items() if not _namespace_deleted(session_id)}


def _finish(connection: sqlite3.Connection, titles: dict[str, str]) -> None:
    """Write titles and namespaces for committed chats, then mark them COMPLETE."""
    if not titles:
        return
    from core.memory.entries import _set_session_titles

    _set_session_titles(titles)
    connection.executemany(
        f"UPDATE {CHATS_TABLE} SET state = ?, pending_title = '' WHERE session_id = ?",
        [(STATE_COMPLETE, session_id) for session_id in titles],
    )
    connection.commit()


def recover_pending() -> int:
    """Finish every chat a crashed run left PREPARED. Returns how many were finished."""
    connection = connect()
    try:
        rows = connection.execute(
            f"SELECT session_id, pending_title FROM {CHATS_TABLE} WHERE state = ?", (STATE_PREPARED,)
        ).fetchall()
        titles = {}
        for session_id, title in rows:
            if _namespace_deleted(str(session_id)):
                continue
            titles[str(session_id)] = str(title or "")
        _finish(connection, titles)
        return len(titles)
    finally:
        connection.close()


def _scrub(text: str, report) -> str:
    from core.session_portability.redaction import scrub_secret_text

    return scrub_secret_text(text, report, residue_marker=SECRET_MARKER)


def _prepare_rows(chat: ImportedChat, report) -> tuple[str, list[tuple]]:
    """(display title, message rows) with every string scrubbed. Rows carry no session id yet."""
    rows: list[tuple] = []
    first_user = ""
    for message in chat.messages:
        if message.role not in ("user", "assistant") or not message.text.strip():
            continue
        text = _scrub(message.text, report)
        if message.role == "user" and not first_user:
            first_user = text
        names = [_scrub(name, report) for name in message.attachment_names]
        rows.append((message.role, text, iso_time(message.created_at), json.dumps(names, ensure_ascii=False)))
    title = display_title(chat, _scrub(chat.title, report) if chat.title else "", first_user or (rows[0][1] if rows else ""))
    return title, rows


def import_chats(
    chats: list[ImportedChat],
    *,
    progress: Callable[[int, int], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    batch_size: int = BATCH_SIZE,
) -> ImportOutcome:
    """Bring ``chats`` in. Returns counts; raises only on a store fault (nothing half-written)."""
    from core.memory.revocation import writing
    from core.session_portability.redaction import RedactionReport

    outcome = ImportOutcome()
    recover_pending()
    total = len(chats)
    done = 0
    report = RedactionReport()
    connection = connect()
    try:
        for start in range(0, total, max(1, batch_size)):
            if should_stop is not None and should_stop():
                break
            batch_titles: dict[str, str] = {}
            for chat in chats[start : start + batch_size]:
                done += 1
                key = source_key(chat)
                row = connection.execute(
                    f"SELECT session_id, state, pending_title FROM {CHATS_TABLE} WHERE source_key = ?", (key,)
                ).fetchone()
                if row is not None:
                    session_id, state, pending_title = str(row[0]), str(row[1]), str(row[2] or "")
                    if not _namespace_deleted(session_id):
                        if state == STATE_PREPARED and session_id not in outcome.session_ids:
                            batch_titles[session_id] = pending_title
                            outcome.brought_in += 1
                            outcome.session_ids.append(session_id)
                        else:
                            outcome.already_here += 1
                        continue
                    # The user deleted the chat this source chat became: bringing it in again
                    # is a new, deliberate import under a new id.
                    connection.execute(f"DELETE FROM {MESSAGES_TABLE} WHERE session_id = ?", (session_id,))
                    connection.execute(f"DELETE FROM {CHATS_TABLE} WHERE session_id = ?", (session_id,))
                    connection.commit()
                secrets_before = report.secrets
                title, rows = _prepare_rows(chat, report)
                if not rows:
                    outcome.empty += 1
                    continue
                session_id = _new_session_id(connection)
                try:
                    with writing({"title": title, "messages": [text for _role, text, _at, _names in rows]}, session_id):
                        connection.execute(
                            f"INSERT INTO {CHATS_TABLE} (session_id, source_key, source_format, state, pending_title, "
                            "created_at, updated_at, imported_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                            (
                                session_id,
                                key,
                                chat.source_format,
                                STATE_PREPARED,
                                title,
                                iso_time(chat.created_at or _first_time(chat)),
                                iso_time(chat.updated_at or _last_time(chat) or chat.created_at),
                                iso_time(time.time()),
                            ),
                        )
                        connection.executemany(
                            f"INSERT INTO {MESSAGES_TABLE} (session_id, event_sequence, role, text, created_at, "
                            "attachment_names_json) VALUES (?, ?, ?, ?, ?, ?)",
                            [(session_id, index + 1, *values) for index, values in enumerate(rows)],
                        )
                        connection.commit()
                except RuntimeError as exc:
                    connection.rollback()
                    if "revoked content" not in str(exc):
                        raise
                    # The writer fence refused: this chat carries words the user asked VOOL to
                    # forget. Nothing of it was written.
                    report.secrets = secrets_before
                    outcome.held_back += 1
                    continue
                except sqlite3.DatabaseError:
                    connection.rollback()
                    outcome.failed += 1
                    _LOG.warning("chat import: one chat could not be stored")
                    continue
                batch_titles[session_id] = title
                outcome.brought_in += 1
                outcome.messages += len(rows)
                outcome.session_ids.append(session_id)
            _finish(connection, batch_titles)
            outcome.secrets_masked = report.secrets
            if progress is not None:
                progress(done, total)
    finally:
        connection.close()
    outcome.secrets_masked = report.secrets
    _LOG.info(
        "chat import finished: %d brought in, %d already here, %d empty, %d held back, %d failed",
        outcome.brought_in, outcome.already_here, outcome.empty, outcome.held_back, outcome.failed,
    )
    return outcome


def _first_time(chat: ImportedChat) -> float | None:
    return next((m.created_at for m in chat.messages if m.created_at is not None), None)


def _last_time(chat: ImportedChat) -> float | None:
    return next((m.created_at for m in reversed(chat.messages) if m.created_at is not None), None)
