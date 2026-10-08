"""Capsule erasure preserves foreign chains and ordinary immutability."""
import sqlite3

import pytest

from core.active_context_capsule import (
    create_shadow_capsule,
    erase_capsule_versions_for_forget,
    list_capsule_versions,
    load_latest_capsule,
)
from core.context_namespace import ensure_chat_namespace
from storage.db import get_connection
from tests.test_active_context_capsule import capsule_home

pytestmark = pytest.mark.usefixtures("capsule_home")


def seed(chat, text):
    ensure_chat_namespace(chat)
    return create_shadow_capsule(chat_id=chat, user_text=text)


def test_forget_erases_the_carrying_chain_and_allows_clean_rebuild():
    seed("erased-chat", "Remember my code is MINT-84.")
    seed("erased-chat", "Now keep the blue key.")
    foreign = seed("foreign-chat", "Remember my code is MINT-84.")
    conn = get_connection()
    try:
        removed = erase_capsule_versions_for_forget(conn, chat_id="erased-chat", token="MINT-84")
        assert removed == 2
        assert conn.execute("SELECT 1 FROM context_capsule_heads WHERE chat_id = ?", ("erased-chat",)).fetchone() is None
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("DELETE FROM context_capsule_versions WHERE version_id = ?", (foreign.version_id,))
    finally:
        conn.close()
    assert list_capsule_versions("erased-chat") == ()
    assert load_latest_capsule("foreign-chat") == foreign
    rebuilt = create_shadow_capsule(chat_id="erased-chat", user_text="Keep the blue key.")
    assert rebuilt.version_number == 1
    assert "MINT-84" not in str(rebuilt)


def test_capsule_no_match_keeps_the_original_version():
    original = seed("safe-chat", "Keep the blue key.")
    conn = get_connection()
    try:
        assert erase_capsule_versions_for_forget(conn, chat_id="safe-chat", token="MINT-84") == 0
    finally:
        conn.close()
    assert load_latest_capsule("safe-chat") == original


def test_failed_capsule_delete_rolls_back_heads_and_delete_protection():
    original = seed("denied-chat", "Remember my code is MINT-84.")
    conn = get_connection()
    try:
        conn.execute("CREATE TRIGGER reject_erasure BEFORE DELETE ON context_capsule_versions "
                     "BEGIN SELECT RAISE(ABORT, 'synthetic erasure denied'); END")
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError, match="synthetic"):
            erase_capsule_versions_for_forget(conn, chat_id="denied-chat", token="MINT-84")
        assert conn.execute("SELECT version_id FROM context_capsule_heads WHERE chat_id = ?", ("denied-chat",)).fetchone()[0] == original.version_id
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'trigger' AND name = 'context_capsule_versions_no_delete'").fetchone()
    finally:
        conn.close()
    assert load_latest_capsule("denied-chat") == original


def test_capsule_erasure_decodes_structured_json_before_matching():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("CREATE TABLE context_capsule_versions (version_id TEXT PRIMARY KEY, chat_id TEXT, capsule_json TEXT)")
        encoded = '{"text":"M' + chr(92) + 'u0049NT-84"}'
        conn.execute("INSERT INTO context_capsule_versions VALUES ('v1', 'escaped-chat', ?)", (encoded,))
        conn.commit()
        assert erase_capsule_versions_for_forget(conn, chat_id="escaped-chat", token="MINT-84") == 1
        assert conn.execute("SELECT count(*) FROM context_capsule_versions").fetchone()[0] == 0
    finally:
        conn.close()


def test_malformed_capsule_json_never_becomes_verified_absence():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("CREATE TABLE context_capsule_versions (version_id TEXT PRIMARY KEY, chat_id TEXT, capsule_json TEXT)")
        conn.execute("INSERT INTO context_capsule_versions VALUES ('v1', 'broken-chat', '{broken')")
        conn.commit()
        with pytest.raises(ValueError):
            erase_capsule_versions_for_forget(conn, chat_id="broken-chat", token="MINT-84")
        assert conn.execute("SELECT count(*) FROM context_capsule_versions").fetchone()[0] == 1
    finally:
        conn.close()
