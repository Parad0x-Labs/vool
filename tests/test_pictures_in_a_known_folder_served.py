"""The phone conversation from 2026-11-12, through the real /api/chat door, on a throwaway home.

Measured on the phone: "I have fees pics in my downloads folder in iMac, can u check what pics are
there and give me a list?" (refused for a missing permission), then "Try again, I fixed permissions"
(answered "Let me know which photos you need" -- the request never ran again), then "Just fetch the
latest 10 pics that you can find in that folder" (a disk-wide search for folders named "in").

Here the same three asks and new wordings run against a home whose Downloads holds known pictures
with known ages, so a right answer can be checked file by file.
"""

from __future__ import annotations

import os
import time
import uuid

import pytest

import tests._reader_served_rig as rig
from tests.test_memory_forget_served_p0 import _reply_text

pytestmark = pytest.mark.served

PICTURES = [f"trip-{n:02d}.jpg" for n in range(1, 13)] + ["receipt.png"]
OTHER = ["installer.dmg", "notes.txt"]


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    home_dir = tmp_path_factory.mktemp("pics-home")
    downloads = home_dir / "Downloads"
    downloads.mkdir()
    (home_dir / "Desktop").mkdir()
    (home_dir / "Documents").mkdir()
    now = time.time()
    # receipt.png is the newest, then trip-12 ... trip-01.
    for age, name in enumerate(["receipt.png"] + [f"trip-{n:02d}.jpg" for n in range(12, 0, -1)]):
        path = downloads / name
        path.write_bytes(b"\x89PNG" if name.endswith(".png") else b"\xff\xd8\xff")
        os.utime(path, (now - 60 * age, now - 60 * age))
    for name in OTHER:
        (downloads / name).write_text("x", encoding="utf-8")
    (downloads / "old-folder").mkdir()
    with rig.CapturingProvider(default="Let me know which photos you need, and I'll help you find them.") as provider:
        daemon = rig.ServedDaemon(home_dir / "vool", provider=provider, env_extra={"HOME": str(home_dir)})
        daemon.start()
        assert daemon.certify(timeout=180).get("state") == "verified"
        try:
            yield daemon
        finally:
            daemon.stop()


def _chat(daemon, session: str, text: str) -> str:
    return _reply_text(daemon.chat(text, session_id=session))


def test_the_phone_conversation_lists_the_pictures_then_the_latest_ten(served):
    session = rig.canonical_session("pics-" + uuid.uuid4().hex[:8])
    first = _chat(served, session, "I have fees pics in my downloads folder in iMac, can u check what pics are "
                                   "there and give me a list? I'll tell you which ones I need")
    for name in PICTURES:
        assert name in first, (name, first)
    for name in [*OTHER, "old-folder"]:
        assert name not in first, (name, first)

    again = _chat(served, session, "Try again, I fixed permissions")
    assert all(name in again for name in PICTURES), again

    latest = _chat(served, session, "Just fetch the latest 10 pics that you can find in that folder")
    newest_ten = ["receipt.png"] + [f"trip-{n:02d}.jpg" for n in range(12, 3, -1)]
    shown = [name for name in PICTURES if name in latest]
    assert sorted(shown) == sorted(newest_ten), latest
    assert latest.index("receipt.png") < latest.index("trip-12.jpg") < latest.index("trip-04.jpg"), latest
    assert "matching 'in'" not in latest and "Found" not in latest.split("\n")[0], latest


@pytest.mark.parametrize("asked, expect_count", [
    ("show me the newest 3 photos in my downloads", 3),
    ("any pics in downloads?", 13),
])
def test_new_wordings_list_the_right_pictures(served, asked, expect_count):
    session = rig.canonical_session("pics-" + uuid.uuid4().hex[:8])
    reply = _chat(served, session, asked)
    shown = [name for name in PICTURES if name in reply]
    assert len(shown) == expect_count, reply
    assert "installer.dmg" not in reply and "notes.txt" not in reply


def test_there_points_back_at_the_folder_named_earlier(served):
    session = rig.canonical_session("pics-" + uuid.uuid4().hex[:8])
    _chat(served, session, "whats in my downloads folder")
    reply = _chat(served, session, "ok just the 2 most recent pictures there")
    assert [name for name in PICTURES if name in reply] == ["receipt.png", "trip-12.jpg"] or (
        "receipt.png" in reply and "trip-12.jpg" in reply and "trip-11.jpg" not in reply
    ), reply


def _history_user_rows(daemon, session: str) -> list[str]:
    import json
    from urllib.request import urlopen

    with urlopen(f"{daemon.base_url}/api/chat/history?session={session}", timeout=60) as response:
        rows = json.loads(response.read().decode()).get("messages", [])
    return [str(row.get("content") or "") for row in rows if row.get("role") == "user"]


def test_a_rerun_keeps_what_the_person_typed_in_the_chat(served):
    session = rig.canonical_session("pics-" + uuid.uuid4().hex[:8])
    first = _chat(served, session, "which pics are in my downloads")
    assert "trip-01.jpg" in first, first
    again = _chat(served, session, "do that again pls")
    assert "trip-01.jpg" in again, again
    users = _history_user_rows(served, session)
    assert users == ["which pics are in my downloads", "do that again pls"], users


@pytest.mark.parametrize("typed", [
    "list the pics in my downloads pls",
    "what pictures do i have in the downloads folder (on this mac)?",
    "any  pics in downloads tho",
])
def test_the_chat_history_keeps_an_ordinary_turn_as_typed(served, typed):
    # Red team R3-01: "pls" was stored as "please" and "folder (in" as "folder(in" on turns that
    # were never rewritten for routing.
    session = rig.canonical_session("pics-" + uuid.uuid4().hex[:8])
    _chat(served, session, typed)
    assert _history_user_rows(served, session) == [typed]
