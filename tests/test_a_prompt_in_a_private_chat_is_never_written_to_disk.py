"""A permission prompt raised in a private chat is kept in memory only: nothing of it lands on disk.

macOS Intel package smoke on 02c37a13b (2026-11-23): in a private chat, "My cousin's boat is called Quillmere.
Remember that." was offered the contacts tool, Manual mode raised a contact-save prompt, and the prompt was
mirrored to pending_approvals.json with the asking message in it. Private chat promises nothing in it is saved.

Now a private chat's prompt stays in memory (a restart drops it, as it drops everything else of a private chat),
no empty mirror is created for it, and an ordinary chat's prompt still survives a restart. The chat ids and the
asking words are built from parts at random.
"""
from __future__ import annotations

import json
import random
import uuid

import pytest

from core import runtime_paths

_RNG = random.Random(20261123)
_NAMES = ("Quillmere", "Sparrowdale", "Tinkerwake", "Blue Hollow", "Marigold")
_THINGS = ("boat", "caravan", "allotment", "workshop", "cabin")


@pytest.fixture()
def home(tmp_path, monkeypatch):
    import core.mode_permission_policy as mpp

    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    mpp._APPROVALS.clear()
    mpp._PERSISTED_APPROVALS_RESTORED = False
    yield tmp_path
    mpp._APPROVALS.clear()
    mpp._PERSISTED_APPROVALS_RESTORED = False
    runtime_paths.configure_runtime_home(None)


def _mint(session_id: str, words: str) -> str:
    from core.mode_permission_policy import OperatingMode, PermissionAction, _new_approval_request

    request = _new_approval_request(
        session_id=session_id,
        task_id=uuid.uuid4().hex,
        intent="contacts.save",
        arguments={"name": words.split(" is called ")[-1].split(".")[0], "note": words},
        actions=(PermissionAction.MODIFY_FILES,),
        mode=OperatingMode.MANUAL,
        mode_revision=0,
        source_context={"turn_request": {"user_text": words}},
        fingerprint="fp-" + uuid.uuid4().hex[:8],
    )
    return str(request["approval_id"])


def _words() -> str:
    return f"My cousin's {_RNG.choice(_THINGS)} is called {_RNG.choice(_NAMES)}. Remember that."


def _files_holding(root, needle: str) -> list[str]:
    hits = []
    for path in root.rglob("*"):
        if path.is_file():
            try:
                if needle.encode() in path.read_bytes():
                    hits.append(str(path.relative_to(root)))
            except OSError:
                continue
    return hits


def test_a_private_chats_prompt_leaves_no_file_and_still_answers(home):
    import core.mode_permission_policy as mpp
    from core.private_chat import set_private

    session = "openclaw:" + uuid.uuid4().hex[:20]
    assert set_private(session, True)
    words = _words()
    before = {str(p.relative_to(home)) for p in home.rglob("*") if p.is_file()}
    token = _mint(session, words)
    after = {str(p.relative_to(home)) for p in home.rglob("*") if p.is_file()}
    assert not (runtime_paths.active_data_dir() / "pending_approvals.json").exists(), sorted(after - before)
    assert _files_holding(home, words.split(" is called ")[-1].split(".")[0]) == []
    # The prompt is still live in this process: the person can answer it.
    resolved = mpp.resolve_approval(token, decision="allow")
    assert resolved is not None and resolved["status"] == "approved"


def test_an_ordinary_chats_prompt_is_still_kept_while_a_private_one_is_not(home):
    import core.mode_permission_policy as mpp
    from core.private_chat import set_private

    ordinary = "openclaw:" + uuid.uuid4().hex[:20]
    private = "openclaw:" + uuid.uuid4().hex[:20]
    assert set_private(private, True)
    kept = _mint(ordinary, _words())
    hidden = _mint(private, _words())
    mirror = json.loads((runtime_paths.active_data_dir() / "pending_approvals.json").read_text())
    assert kept in mirror and hidden not in mirror
    # A restart keeps the ordinary prompt and has nothing of the private one.
    mpp._APPROVALS.clear()
    mpp._PERSISTED_APPROVALS_RESTORED = False
    assert mpp.resolve_approval(kept, decision="allow") is not None
    assert mpp.resolve_approval(hidden, decision="allow") is None
