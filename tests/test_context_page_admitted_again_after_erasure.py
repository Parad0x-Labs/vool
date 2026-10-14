"""A page whose words are admitted again after its erasure is served again, and can be erased a second time.

Measured on the 0.7.1 Mac smoke (2026-10-14): "Remember that my locker is 14." then "Forget 14." passed once; the
same sentence in a later chat made the identical page (pages are content-addressed), the row stayed marked erased
while its bytes were back in the store, and the next "Forget 14." failed: the second erasure's chain event reused
the first one's id with a different payload.
"""
from __future__ import annotations

import pytest

from core.context_pages import ContextPage, PageNotFoundError, admit_page, erase_page, page_in
from storage.cas import get_bytes
from storage.db import get_connection


def _page(session_id: str) -> ContextPage:
    return ContextPage(kind="turn_context", source="turn-context:chat_exchange",
                       content="[user]\nRemember that my locker is 14.\n[/user]", session_id=session_id)


def _erase(page_hash: str) -> dict:
    return erase_page(page_hash, governance_actor="operator", governance_reason="memory forget",
                      require_exclusive_blob=True)


def test_words_admitted_again_after_an_erasure_are_served_and_erasable_again():
    first = _page("chat-first")
    admit_page(first)
    first_receipt = _erase(first.page_hash)["erasure_receipt"]
    with pytest.raises(PageNotFoundError):
        page_in(first.page_hash)

    again = _page("chat-again")
    assert again.page_hash == first.page_hash
    admit_page(again)
    assert page_in(again.page_hash, chat_id="chat-again") == again.content

    conn = get_connection()
    try:
        row = conn.execute("SELECT session_id, erased, content_ref FROM context_pages WHERE page_hash = ?",
                           (again.page_hash,)).fetchone()
    finally:
        conn.close()
    # The new chat owns the page now; the chat that erased it has no claim on it.
    assert (row["session_id"], row["erased"]) == ("chat-again", 0)

    second_receipt = _erase(again.page_hash)["erasure_receipt"]
    assert second_receipt != first_receipt
    assert get_bytes(str(row["content_ref"])) is None
    with pytest.raises(PageNotFoundError):
        page_in(again.page_hash)
