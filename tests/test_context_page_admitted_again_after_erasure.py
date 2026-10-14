"""A page whose words are admitted again after its erasure is a new admission, served and erasable again.

Measured on the 0.7.1 Mac smoke (2026-10-14): "Remember that my locker is 14." then "Forget 14." passed once; the
same sentence in a later chat made the identical page (pages are content-addressed), the row stayed marked erased
while its bytes were back in the store, and the next "Forget 14." failed: the second erasure's chain event reused
the first one's id with a different payload. The review of that fix (2026-10-14) found the rest of the contract:
the revived page takes the new admission's owner, classes and pin; the chat that erased it has nothing left to
forget; each later archive and erasure records its own chain event, chosen without a race; and the forget log
names no page hash, since a hash of a short sentence can be recovered.
"""
from __future__ import annotations

import inspect
import logging
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from core.context_pages import (
    ContextPage,
    ErasureNotAllowedError,
    PageNotFoundError,
    PinActiveError,
    admit_page,
    archive_closed_pages,
    erase_page,
    page_in,
    release_pin,
)
from core.turn_context import AdmissionClosedError, AdmissionRefusedError, forget_chat_context_pages, recall_page
from storage.cas import get_bytes
from storage.db import get_connection
from storage.event_hash_chain import verify_chain
from tests.test_forget_context_pages_r6 import _hash_backend, admit, blob_for, isolated_pages
from tests.test_forgetting_is_applied_or_never_claimed_20261007 import _forget_command as command

pytestmark = pytest.mark.usefixtures("_hash_backend")

LOCKER = "[user]\nRemember that my locker is 14.\n[/user]"


def _page(session_id: str, **fields) -> ContextPage:
    return ContextPage(kind="turn_context", source="turn-context:chat_exchange", content=LOCKER,
                       session_id=session_id, **fields)


def _erase(page_hash: str) -> dict:
    return erase_page(page_hash, governance_actor="operator", governance_reason="memory forget",
                      require_exclusive_blob=True)


def _row(page_hash: str) -> dict:
    conn = get_connection()
    try:
        return dict(conn.execute("SELECT * FROM context_pages WHERE page_hash = ?", (page_hash,)).fetchone())
    finally:
        conn.close()


def _events(prefix: str, page_hash: str) -> list[str]:
    conn = get_connection()
    try:
        return [str(row[0]) for row in conn.execute(
            "SELECT event_id FROM event_hash_chain WHERE event_id = ? OR event_id LIKE ? ORDER BY seq",
            (f"{prefix}:{page_hash}", f"{prefix}:{page_hash}:%"))]
    finally:
        conn.close()


def test_words_admitted_again_after_an_erasure_are_served_and_erasable_again(isolated_pages):
    first = _page("chat-first")
    admit_page(first)
    first_receipt = _erase(first.page_hash)["erasure_receipt"]
    with pytest.raises(PageNotFoundError):
        page_in(first.page_hash)

    again = _page("chat-again")
    assert again.page_hash == first.page_hash
    admit_page(again)
    assert page_in(again.page_hash, chat_id="chat-again") == again.content
    row = _row(again.page_hash)
    # The new chat owns the page now; the chat that erased it has no claim on it.
    assert (row["session_id"], row["erased"]) == ("chat-again", 0)

    second_receipt = _erase(again.page_hash)["erasure_receipt"]
    assert second_receipt != first_receipt
    assert get_bytes(str(row["content_ref"])) is None
    with pytest.raises(PageNotFoundError):
        page_in(again.page_hash)
    assert len(set(_events("ctx-page-erased", again.page_hash))) == 2
    assert verify_chain()


@pytest.mark.parametrize("kind,source", [
    ("turn_context", "turn-context:chat_exchange"), ("turn_context", "turn-context:tool_result"),
    ("file_read", "fixture-document"), ("web_fetch", "fixture-source"), ("tool_result", "fixture-tool"),
])
@pytest.mark.parametrize("same_chat", [True, False])
def test_every_page_kind_is_served_and_erased_again_after_its_words_return(isolated_pages, kind, source, same_chat):
    first = ContextPage(kind=kind, source=source, content="A page alpha.", session_id="chat-old")
    admit_page(first)
    one = erase_page(first.page_hash, governance_actor="operator", governance_reason="memory forget")
    again = replace(first, session_id="chat-old" if same_chat else "chat-new")
    admit_page(again)
    assert page_in(again.page_hash, chat_id=again.session_id) == again.content
    two = erase_page(again.page_hash, governance_actor="operator", governance_reason="memory forget")
    assert one != two
    assert verify_chain()


def test_a_chat_that_forgot_the_words_has_nothing_left_when_another_chat_says_them_again(isolated_pages):
    old = admit("chat-forgot", "The locker code is MINT-84.")
    handled, reply = command(isolated_pages, "chat-forgot", "forget MINT-84")
    assert handled and "Forget applied" in reply, reply

    new = admit("chat-says-it-again", "The locker code is MINT-84.")
    assert new["page_hash"] == old["page_hash"]
    assert page_in(new["page_hash"], chat_id="chat-says-it-again") == "The locker code is MINT-84."
    with pytest.raises(PageNotFoundError):
        page_in(new["page_hash"], chat_id="chat-forgot")
    with pytest.raises(PageNotFoundError):
        page_in(new["page_hash"], chat_id="chat-unrelated")
    with pytest.raises(AdmissionClosedError):
        recall_page("owner_local", old["admission_id"])
    assert recall_page("owner_local", new["admission_id"])["content"] == "The locker code is MINT-84."

    # Forgetting again in the old chat finds nothing of its own, and leaves the other chat's words alone.
    handled, reply = command(isolated_pages, "chat-forgot", "forget MINT-84")
    assert handled and "could not be verified" not in reply, reply
    assert page_in(new["page_hash"], chat_id="chat-says-it-again") == "The locker code is MINT-84."



def test_the_new_chat_can_forget_the_words_too_and_the_old_chat_cannot_write_them_back(isolated_pages):
    content = "The locker code is MINT-84."
    old = admit("chat-first-owner", content)
    assert "Forget applied" in command(isolated_pages, "chat-first-owner", "forget MINT-84")[1]
    with pytest.raises(AdmissionRefusedError):
        admit("chat-first-owner", content)

    new = admit("chat-second-owner", content)
    assert "Forget applied" in command(isolated_pages, "chat-second-owner", "forget MINT-84")[1]
    assert get_bytes(blob_for(new["page_hash"])) is None
    for record in (old, new):
        with pytest.raises(AdmissionClosedError):
            recall_page("owner_local", record["admission_id"])
    assert verify_chain()

def test_the_revived_page_keeps_the_new_admissions_retention_trust_and_permission(isolated_pages):
    erasable = _page("chat-erasable", retention_class="ERASABLE")
    admit_page(erasable)
    erase_page(erasable.page_hash)

    persistent = _page("chat-persistent", retention_class="PERSISTENT", trust_class="UNTRUSTED",
                       permission_class="private.read")
    admit_page(persistent)
    row = _row(persistent.page_hash)
    assert (row["retention_class"], row["trust_class"], row["permission_class"]) == (
        "PERSISTENT", "UNTRUSTED", "private.read")
    # A PERSISTENT page is erased only through the governed path, whatever class its words had before.
    with pytest.raises(ErasureNotAllowedError):
        erase_page(persistent.page_hash)
    assert page_in(persistent.page_hash) == LOCKER


def test_the_revived_page_is_pinned_by_its_task_again(isolated_pages):
    pinned = _page("chat-task", task_id="turn-7")
    admit_page(pinned)
    release_pin(pinned.page_hash, pin_id="task:turn-7")
    _erase(pinned.page_hash)

    admit_page(_page("chat-task", task_id="turn-7"))
    with pytest.raises(PinActiveError):
        _erase(pinned.page_hash)
    assert page_in(pinned.page_hash) == LOCKER


def test_a_revived_page_is_archived_again_under_its_own_event(isolated_pages):
    first = _page("chat-archive-a")
    admit_page(first)
    assert [r["page_hash"] for r in archive_closed_pages(session_id="chat-archive-a")] == [first.page_hash]
    # Archiving the same page again records nothing new.
    archive_closed_pages(session_id="chat-archive-a")
    _erase(first.page_hash)

    admit_page(_page("chat-archive-b"))
    assert [r["page_hash"] for r in archive_closed_pages(session_id="chat-archive-b")] == [first.page_hash]
    assert _events("ctx-page-archived", first.page_hash) == [
        f"ctx-page-archived:{first.page_hash}", f"ctx-page-archived:{first.page_hash}:2"]


def test_a_volatile_page_admitted_again_belongs_to_its_new_admission(isolated_pages):
    old = _page("chat-volatile-a", retention_class="ERASABLE", metadata={"surface": "old"})
    admit_page(old)
    erase_page(old.page_hash)

    admit_page(_page("chat-volatile-b", retention_class="ERASABLE", metadata={"surface": "new"}))
    row = _row(old.page_hash)
    assert (row["session_id"], row["metadata_json"], row["content_ref"]) == (
        "chat-volatile-b", '{"surface": "new"}', None)
    with pytest.raises(PageNotFoundError):
        page_in(old.page_hash)


def test_two_erasures_at_once_record_two_events(isolated_pages):
    """Both real erasers are paused at the chain append (a trace hook, nothing replaced) and then let go together;
    neither may take the other's event id. A volatile page leaves only the event id allocation in the schedule."""
    page = ContextPage(kind="tool_result", source="fixture", content="A race value.", retention_class="ERASABLE")
    admit_page(page)
    lines, start = inspect.getsourcelines(erase_page)
    stop_line = next(start + i for i, line in enumerate(lines) if "event_hash = append_hashed_event(" in line)
    rendezvous = threading.Barrier(2)

    def erase_worker():
        def trace(frame, event, arg):
            if frame.f_code is erase_page.__code__ and event == "line" and frame.f_lineno == stop_line:
                rendezvous.wait(timeout=20)
                sys.settrace(None)
            return trace

        sys.settrace(trace)
        try:
            return erase_page(page.page_hash)
        finally:
            sys.settrace(None)

    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(erase_worker) for _ in range(2)]
        results = []
        for future in futures:
            try:
                results.append(future.result())
            except Exception as exc:
                results.append(type(exc).__name__)
    assert all(isinstance(item, dict) for item in results), results
    assert len({item["erasure_receipt"] for item in results}) == 2
    assert verify_chain()


def test_a_failed_page_erasure_logs_no_page_hash(isolated_pages, caplog):
    chat = "chat-log"
    own = admit(chat, "The locker code is MINT-84.")
    conn = get_connection()
    try:
        conn.execute("CREATE TRIGGER refuse_manifest_erasure BEFORE DELETE ON manifest_store "
                     "BEGIN SELECT RAISE(ABORT, 'test erasure denied'); END")
        conn.commit()
        with caplog.at_level(logging.WARNING):
            handled, reply = command(isolated_pages, chat, "forget MINT-84")
        assert handled and "context pages" in reply, reply
    finally:
        conn.execute("DROP TRIGGER IF EXISTS refuse_manifest_erasure")
        conn.commit()
        conn.close()
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "a context page was not erased" in logged, logged
    for secret in (own["page_hash"][:8], blob_for(own["page_hash"])[:8], "MINT-84"):
        assert secret not in logged, logged


def test_a_refused_page_erasure_logs_nothing_that_identifies_a_short_sentence(isolated_pages, caplog):
    """A page hash is a hash of its words: logging even its first characters lets "My locker is 0." through
    "My locker is 99." be hashed until one matches."""
    from core.context_pages import pin_page

    record = admit("chat-log-hold", "My locker is 14.")
    pin_page(record["page_hash"], pin_id="external-hold")
    with caplog.at_level(logging.WARNING, logger="core.turn_context"):
        forget_chat_context_pages(chat_id="chat-log-hold", token="14", erasures={})
    logged = "\n".join(r.getMessage() for r in caplog.records if r.name == "core.turn_context")
    assert "a context page was not erased: PinActiveError" in logged, logged
    matches = [n for n in range(100) if ContextPage(kind="turn_context", source="turn-context:chat_exchange",
                                                   content=f"My locker is {n}.").page_hash[:12] in logged]
    assert matches == [], (logged, matches)


def test_a_half_finished_erasure_of_words_no_chat_holds_any_more_is_finished_by_either_chat(isolated_pages):
    """Two chats said the same words. Once both have forgotten them and one erasure stopped half way, a retry in
    the chat that does not own the page row still finds the page: no other chat holds those words any more."""
    content = "The locker code is MINT-84."
    first = admit("chat-row-owner", content)
    admit("chat-second-speaker", content)
    handled, reply = command(isolated_pages, "chat-second-speaker", "forget MINT-84")
    assert handled and "could not be verified" in reply, reply  # the first chat still holds the shared page

    conn = get_connection()
    try:
        conn.execute("CREATE TRIGGER refuse_manifest_erasure BEFORE DELETE ON manifest_store "
                     "BEGIN SELECT RAISE(ABORT, 'test erasure denied'); END")
        conn.commit()
        handled, reply = command(isolated_pages, "chat-row-owner", "forget MINT-84")
        assert handled and "could not be verified" in reply, reply
    finally:
        conn.execute("DROP TRIGGER IF EXISTS refuse_manifest_erasure")
        conn.commit()
        conn.close()
    blob = blob_for(first["page_hash"])

    handled, reply = command(isolated_pages, "chat-second-speaker", "forget MINT-84")
    assert handled and "could not be verified" not in reply, reply
    conn = get_connection()
    try:
        assert conn.execute("SELECT 1 FROM manifest_store WHERE blob_hash = ?", (blob,)).fetchone() is None
    finally:
        conn.close()
