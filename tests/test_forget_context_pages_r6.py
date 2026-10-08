"""Chat-memory page erasure covers scoped admissions and their owned CAS bytes."""

import json
from pathlib import Path

import pytest

from tests.test_forgetting_is_applied_or_never_claimed_20261007 import _forget_command as command
from tests.test_question_date_time_leg_20261002 import _hash_backend as _hash_backend
from tests.test_question_date_time_leg_20261002 import _ingest, _profile

pytestmark = pytest.mark.usefixtures("_hash_backend")


@pytest.fixture(autouse=True)
def isolated_pages(tmp_path, runtime_storage_reset):
    import core.runtime_continuity as continuity
    import core.runtime_paths as paths
    import storage.db as database
    from storage.chunk_store import chunk_root
    from storage.migrations import run_migrations

    previous = paths._VOOL_HOME_OVERRIDE
    previous_db = database.active_default_db_path()
    previous_continuity_db = continuity._DB_PATH_OVERRIDE
    profile = _profile(tmp_path)
    paths.configure_runtime_home(profile)
    try:
        db_path = profile / "data" / "vool_web0_v2.db"
        database.configure_default_db_path(db_path)
        continuity.configure_runtime_continuity_db_path(db_path)
        run_migrations()
        conn = database.get_connection()
        try:
            actual_db = next(row[2] for row in conn.execute("PRAGMA database_list") if row[1] == "main")
        finally:
            conn.close()
        assert Path(actual_db).resolve() == db_path.resolve()
        assert chunk_root().resolve() == (profile / "data" / "cas_chunks").resolve()
        yield profile
    finally:
        continuity.configure_runtime_continuity_db_path(previous_continuity_db)
        database.configure_default_db_path(previous_db)
        paths.configure_runtime_home(previous)


def admit(chat, content, **fields):
    from core.context_namespace import ensure_chat_namespace
    from core.turn_context import TurnScope, admit_turn_context

    ensure_chat_namespace(chat)
    return admit_turn_context(
        content, scope=TurnScope(principal="owner_local", session_id=chat, turn_id=chat + "-turn"),
        **fields,
    )


def blob_for(page_hash):
    from storage.db import get_connection

    conn = get_connection()
    try:
        return str(conn.execute(
            "SELECT content_ref FROM context_pages WHERE page_hash = ?", (page_hash,),
        ).fetchone()[0])
    finally:
        conn.close()


def test_forget_erases_chat_page_metadata_and_cas_bytes(isolated_pages):
    from core.context_pages import PageNotFoundError, page_in
    from core.turn_context import inspect_admissions, recall_page
    from storage.cas import get_bytes

    chat = "r6-context-owned"
    _ingest(isolated_pages, chat, [(1760000000, "The locker code is MINT-84.")])
    own = admit(chat, "The locker code is MINT-84.", title="MINT-84", metadata={"fact": "MINT-84"})
    safe = admit(chat, "The notebook is blue.")
    blob = blob_for(own["page_hash"])
    assert get_bytes(blob) == b"The locker code is MINT-84."
    handled, reply = command(isolated_pages, chat, "forget MINT-84")
    assert handled and "Forget applied" in reply, reply
    assert get_bytes(blob) is None
    with pytest.raises(PageNotFoundError):
        page_in(own["page_hash"])
    assert recall_page("owner_local", safe["admission_id"])["content"] == "The notebook is blue."
    assert "MINT-84" not in json.dumps(inspect_admissions("owner_local", session_id=chat, include_content=True))


def test_shared_chat_page_is_retained_and_named_incomplete(isolated_pages):
    from core.context_pages import PageNotFoundError, page_in
    from core.turn_context import AdmissionClosedError, recall_page
    from storage.cas import get_bytes

    own = admit("r6-context-shared", "The locker code is MINT-84.")
    foreign = admit("r6-context-foreign", "The locker code is MINT-84.")
    assert own["page_hash"] == foreign["page_hash"]
    blob = blob_for(own["page_hash"])
    handled, reply = command(isolated_pages, "r6-context-shared", "forget MINT-84")
    assert handled and "context pages" in reply and "could not be verified" in reply, reply
    assert b"MINT-84" in get_bytes(blob)
    with pytest.raises(AdmissionClosedError):
        recall_page("owner_local", own["admission_id"])
    with pytest.raises(PageNotFoundError):
        page_in(own["page_hash"])
    assert "MINT-84" in recall_page("owner_local", foreign["admission_id"])["content"]


def test_document_with_same_blob_keeps_its_bytes(isolated_pages):
    from core.context_pages import ContextPage, admit_page, page_in

    content = "A user document mentions MINT-84."
    own = admit("r6-context-document", content)
    document = ContextPage(kind="file_read", source="user-document", content=content, session_id="docs")
    admit_page(document)
    assert blob_for(own["page_hash"]) == blob_for(document.page_hash)
    handled, reply = command(isolated_pages, "r6-context-document", "forget MINT-84")
    assert handled and "context pages" in reply and "could not be verified" in reply, reply
    assert page_in(document.page_hash) == content


def test_shared_cas_chunks_are_retained_without_damaging_other_blob(isolated_pages):
    from storage.cas import get_bytes, put_bytes

    shared = "MINT-84 " + "x" * (65536 - 8)
    admit("r6-context-chunks", shared + " own")
    foreign = put_bytes((shared + " foreign").encode())
    handled, reply = command(isolated_pages, "r6-context-chunks", "forget MINT-84")
    assert handled and "context pages" in reply and "could not be verified" in reply, reply
    assert get_bytes(foreign["blob_hash"]) == (shared + " foreign").encode()


def test_pending_erasure_withholds_page_content_and_metadata(isolated_pages):
    from core.context_pages import PageNotFoundError, page_in
    from core.turn_context import inspect_admissions
    from core.vool_memory import VoolMemory

    chat = "r6-context-pending"
    own = admit(chat, "A notebook is blue.", title="MINT-84", metadata={"fact": "MINT-84"})
    mem = VoolMemory(runtime_home=isolated_pages)
    try:
        mem.begin_erasure(chat, "MINT-84")
    finally:
        mem.close()
    with pytest.raises(PageNotFoundError):
        page_in(own["page_hash"], chat_id=chat)
    with pytest.raises(PageNotFoundError):
        page_in(own["page_hash"], chat_id="unowned-scope")
    assert inspect_admissions("owner_local", session_id=chat, include_content=False) == []


def test_completed_forget_cannot_republish_chat_page(isolated_pages):
    from core.turn_context import AdmissionRefusedError

    chat = "r6-context-republish"
    admit(chat, "The locker code is MINT-84.")
    handled, reply = command(isolated_pages, chat, "forget MINT-84")
    assert handled and "Forget applied" in reply, reply
    with pytest.raises(AdmissionRefusedError):
        admit(chat, "Forget MINT-84.")
    with pytest.raises(AdmissionRefusedError):
        admit(chat, "A notebook is blue.", metadata={"fact": "MINT-84"})


def test_failed_cas_delete_is_incomplete_and_retry_verifies_remaining_manifest(isolated_pages):
    from core.context_pages import PageNotFoundError, page_in
    from storage.db import get_connection

    chat = "r6-context-delete-failure"
    own = admit(chat, "The locker code is MINT-84.")
    conn = get_connection()
    try:
        conn.execute("CREATE TRIGGER refuse_manifest_erasure BEFORE DELETE ON manifest_store "
                     "BEGIN SELECT RAISE(ABORT, 'test erasure denied'); END")
        conn.commit()
        handled, reply = command(isolated_pages, chat, "forget MINT-84")
        assert handled and "context pages" in reply and "could not be verified" in reply, reply
        with pytest.raises(PageNotFoundError):
            page_in(own["page_hash"])
        conn.execute("DROP TRIGGER refuse_manifest_erasure")
        conn.commit()
        handled, reply = command(isolated_pages, chat, "forget MINT-84")
        assert handled and "could not be verified" not in reply, reply
        assert conn.execute("SELECT 1 FROM manifest_store WHERE blob_hash = ?", (blob_for(own["page_hash"]),)).fetchone() is None
    finally:
        conn.close()


@pytest.mark.parametrize("field,value", [
    ("title", "MINT-84"),
    ("metadata_json", '{"retained":"MINT-84"}'),
])
def test_admission_payload_restored_by_trigger_is_incomplete(isolated_pages, field, value):
    from core.turn_context import inspect_admissions
    from storage.db import get_connection

    chat = "r6-context-admission-verify"
    own = admit(chat, "The locker code is MINT-84.")
    conn = get_connection()
    try:
        literal = "'" + value.replace("'", "''") + "'"
        conn.execute(
            "CREATE TRIGGER restore_admission_payload AFTER UPDATE ON turn_context_admissions "
            "WHEN NEW.status = 'erased' AND NEW." + field + " != " + literal + " "
            "BEGIN UPDATE turn_context_admissions SET " + field + " = " + literal
            + " WHERE admission_id = NEW.admission_id; END",
        )
        conn.commit()
        handled, reply = command(isolated_pages, chat, "forget MINT-84")
        assert handled and "context pages" in reply and "could not be verified" in reply, reply
        retained = conn.execute(
            "SELECT " + field + " FROM turn_context_admissions WHERE admission_id = ?",
            (own["admission_id"],),
        ).fetchone()[0]
        assert "MINT-84" in retained
        assert inspect_admissions("owner_local", session_id=chat, include_content=True) == []
    finally:
        conn.close()


def test_every_chunk_layout_for_the_same_blob_is_erased(isolated_pages):
    from storage.cas import get_bytes, put_bytes
    from storage.chunk_store import chunk_root
    from storage.db import get_connection

    chat = "r6-context-old-layouts"
    content = b"The locker code is MINT-84."
    first = put_bytes(content, chunk_size=7)
    own = admit(chat, content.decode())
    latest = put_bytes(content, chunk_size=11)
    conn = get_connection()
    try:
        layouts = conn.execute(
            "SELECT manifest_json FROM manifest_store WHERE blob_hash = ?", (latest["blob_hash"],),
        ).fetchall()
        all_chunks = {chunk for row in layouts for chunk in json.loads(row[0])["chunk_hashes"]}
        assert set(first["chunk_hashes"]) - set(latest["chunk_hashes"])
        assert len(layouts) == 3
        paths = [chunk_root() / chunk[:2] / chunk[2:4] / chunk for chunk in all_chunks]
        assert all(path.exists() for path in paths)
        handled, reply = command(isolated_pages, chat, "forget MINT-84")
        assert handled and "Forget applied" in reply, reply
        assert all(not path.exists() for path in paths)
        assert get_bytes(blob_for(own["page_hash"])) is None
        assert conn.execute("SELECT 1 FROM manifest_store WHERE blob_hash = ?", (latest["blob_hash"],)).fetchone() is None
    finally:
        conn.close()
