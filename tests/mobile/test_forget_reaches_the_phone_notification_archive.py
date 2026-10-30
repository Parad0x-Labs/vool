"""A chat "forget" reaches the paired phone's notification archive.

Red-team F1 (0.7.7 train, 2026-10-30): the phone's inbox keeps its own copy of each notification it was sent, in
mobile_companion/notification_archive.sqlite3 (the whole event, title included: "Reminder: <the person's words>").
The chat forget erased the notification itself from the runtime database but never this copy, so the phone could
still page back to the forgotten words. Other chats' copies and this chat's unrelated copies stay.
"""
from __future__ import annotations

import json
import sqlite3
import uuid

from tests.test_forget_scrubs_run_history import CANARY, _remember, command
from tests.test_forget_scrubs_run_history import isolated_pages as isolated_pages  # noqa: F401  (fixture)

DEVICE = "phone:abc"


def _notify(chat: str, title: str) -> str:
    """One notification as the runtime records it, and the phone archive's copy of it."""
    from core.mobile_companion import notification_archive, notifications
    from core.operator import notification_center

    item = notification_center.record_item(dedupe_key=f"reminder:{uuid.uuid4().hex}:0", source_kind="reminder",
                                           title=title, body="", payload={}, session_id=chat)
    event = notifications._project(notification_center.load_item(item["notification_id"]), preserve_records=True)
    assert event is not None and event["detail"] == title
    notification_archive.append(DEVICE, [event])
    return item["notification_id"]


def _archived() -> dict[str, dict]:
    from core.mobile_companion.notification_archive import archive_path

    conn = sqlite3.connect(archive_path())
    try:
        return {row[0]: json.loads(row[1]) for row in conn.execute("SELECT source_id, event_json FROM notification_archive")}
    finally:
        conn.close()


def test_forget_erases_this_chats_archived_copy_and_keeps_the_rest(isolated_pages):
    from core.mobile_companion.notification_archive import archive_path

    chat, other = "phone-archive-own", "phone-archive-other"
    _remember(isolated_pages, chat, "my sister's bakery is called Flourstone Bakehouse.")
    own = _notify(chat, "Reminder: order bread at the Flourstone Bakehouse")
    unrelated = _notify(chat, "Reminder: water the harbor lantern plants")
    theirs = _notify(other, "Reminder: Flourstone Bakehouse opens at 7")
    assert CANARY in json.dumps(_archived()[own])

    handled, reply = command(isolated_pages, chat, f"Forget {CANARY}.")

    assert handled and reply.startswith("Forget applied"), reply
    left = _archived()
    assert own not in left
    assert unrelated in left and "harbor lantern" in left[unrelated]["detail"]
    # another chat's notification is not this chat's forget
    assert theirs in left and CANARY in left[theirs]["detail"]
    raw = archive_path().read_bytes().lower()
    assert raw.count(b"flourstone") == 1  # only the other chat's row; the erased row's bytes were zeroed


def test_an_archive_that_cannot_be_erased_is_never_reported_as_forgotten(isolated_pages):
    from core.mobile_companion.notification_archive import archive_path

    chat = "phone-archive-refused"
    _remember(isolated_pages, chat, "my sister's bakery is called Flourstone Bakehouse.")
    own = _notify(chat, "Reminder: order bread at the Flourstone Bakehouse")
    conn = sqlite3.connect(archive_path())
    conn.execute("CREATE TRIGGER refuse_archive_erasure BEFORE DELETE ON notification_archive "
                 "BEGIN SELECT RAISE(ABORT, 'archive unavailable'); END")
    conn.commit()
    try:
        handled, reply = command(isolated_pages, chat, f"Forget {CANARY}.")
        assert handled and reply.startswith("Forget could not be verified as fully applied"), reply
        assert own in _archived()
    finally:
        conn.execute("DROP TRIGGER IF EXISTS refuse_archive_erasure")
        conn.commit()
        conn.close()
    handled, reply = command(isolated_pages, chat, f"Forget {CANARY}.")
    assert handled and "could not be verified" not in reply, reply
    assert own not in _archived()


def test_no_archive_on_this_computer_creates_none(isolated_pages):
    from core.mobile_companion.notification_archive import archive_path

    chat = "phone-archive-none"
    _remember(isolated_pages, chat, "my sister's bakery is called Flourstone Bakehouse.")
    handled, reply = command(isolated_pages, chat, f"Forget {CANARY}.")
    assert handled and reply.startswith("Forget applied"), reply
    assert not archive_path().exists()
