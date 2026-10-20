"""A forget erases what the chat kept of a long paste, and nothing it kept carries the words into a later turn.

Measured 2026-10-20 on a served daemon, at 153148eb (the 0.7.2 train) and at v0.7.1 baf115c alike: a composer
paste of 4000 or more characters is kept as a DOCUMENT of the chat (core/chat_attachments.py, retention "chat")
and carried into every later turn. After a paste holding "My locker code is 4417." and "Forget my locker code is
4417.", the pasted bytes were still on disk and carried_documents_for_turn still returned 4417: the forget
never looked at the document store.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from tests._reader_served_rig import CapturingProvider, ServedDaemon, canonical_session
from tests.test_forget_scrubs_run_history import command, isolated_pages  # isolated_pages: the runtime home fixture
from tests.test_question_date_time_leg_20261002 import _hash_backend

pytestmark = pytest.mark.usefixtures("_hash_backend")

FACT = "My locker code is 4417."
INVENTORY = "\n".join(f"Inventory note {index:03d}: crate {index} holds spare harbor parts." for index in range(90))


@pytest.fixture(autouse=True)
def _a_chat_that_has_taken_turns(request):
    """A chat's first turn opens its memory, which sets up the home's forget authority."""
    if "isolated_pages" not in request.fixturenames:
        return
    from core.vool_memory import VoolMemory

    VoolMemory(runtime_home=request.getfixturevalue("isolated_pages")).close()


def _chat(seed: str) -> str:
    return "openclaw:" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:20]


def _paste(chat: str, text: str, *, turn: str, name: str = "") -> dict:
    """The composer's paste door, then the send that binds it to its turn."""
    from core.chat_attachments import bind_to_turn, stage_document
    from core.context_namespace import ensure_chat_namespace

    ensure_chat_namespace(chat)
    record = stage_document(session_id=chat, data=text.encode("utf-8"), declared_name=name)
    bind_to_turn(session_id=chat, turn_id=turn, attachment_ids=[record["id"]])
    return record


def _carried(chat: str) -> list[dict]:
    from core.chat_attachments import carried_documents_for_turn

    return carried_documents_for_turn(session_id=chat, exclude_turn_id="the-next-turn")


def _bytes_path(record: dict) -> Path:
    from core.chat_attachments import stage_dir

    return stage_dir() / f"{record['id']}.bin"


def _manifest(record: dict) -> dict:
    from core.chat_attachments import stage_dir

    return json.loads((stage_dir() / f"{record['id']}.json").read_text(encoding="utf-8"))


def test_a_forget_erases_the_pasted_document_that_holds_the_words(isolated_pages):
    chat = _chat("forget-paste-holds-the-fact")
    other_chat = _chat("forget-paste-other-chat")
    pasted = _paste(chat, INVENTORY[:2000] + "\n" + FACT + "\n" + INVENTORY[2000:], turn="t1")
    bystander = _paste(chat, INVENTORY.replace("harbor", "orchard"), turn="t2")
    foreign = _paste(other_chat, FACT + "\n" + INVENTORY, turn="t1")
    assert any("4417" in entry["text"] for entry in _carried(chat)), "probe is blind: the paste was never carried"

    handled, reply = command(isolated_pages, chat, "Forget my locker code is 4417.")

    assert handled and reply == "Forget applied. Removed 1 memory entry.", reply
    carried = _carried(chat)
    assert [entry["attachment_id"] for entry in carried] == [bystander["id"]], carried
    assert not any("4417" in entry["text"] for entry in carried)
    assert not _bytes_path(pasted).exists()
    assert _manifest(pasted)["state"] == "erased"
    # The other chat never forgot anything.
    assert "4417" in _carried(other_chat)[0]["text"]
    assert _bytes_path(foreign).exists()


def test_a_forget_that_matches_no_document_keeps_them_and_says_nothing_was_removed(isolated_pages):
    chat = _chat("forget-paste-no-match")
    pasted = _paste(chat, INVENTORY, turn="t1")

    handled, reply = command(isolated_pages, chat, "Forget my locker code is 4417.")

    assert handled and reply.startswith("Nothing was removed."), reply
    assert [entry["attachment_id"] for entry in _carried(chat)] == [pasted["id"]]


def test_a_document_named_for_the_words_loses_the_name_and_keeps_unrelated_text(isolated_pages):
    chat = _chat("forget-paste-name")
    dropped = _paste(chat, INVENTORY, turn="t1", name="locker-4417.txt")

    handled, reply = command(isolated_pages, chat, "Forget 4417.")

    assert handled and reply == "Forget applied. Removed 1 memory entry.", reply
    assert "4417" not in json.dumps(_manifest(dropped))
    carried = _carried(chat)
    assert [entry["attachment_id"] for entry in carried] == [dropped["id"]]
    assert "4417" not in json.dumps(carried) and "crate 89 holds" in carried[0]["text"]


def test_a_document_that_cannot_be_read_is_never_reported_as_forgotten_and_a_retry_finishes(isolated_pages):
    chat = _chat("forget-paste-unreadable")
    pasted = _paste(chat, FACT + "\n" + INVENTORY, turn="t1")
    path = _bytes_path(pasted)
    held = path.with_name("held-elsewhere.bin")
    path.rename(held)
    # The store refuses to follow a link: a document it cannot read is an unverified one, not an empty one.
    os.symlink(held, path)

    handled, reply = command(isolated_pages, chat, "Forget my locker code is 4417.")

    assert handled and reply.startswith("Forget could not be verified as fully applied: "), reply
    assert "chat documents" in reply, reply
    assert not any("4417" in entry["text"] for entry in _carried(chat))

    path.unlink()
    held.rename(path)
    handled, retry = command(isolated_pages, chat, "Forget my locker code is 4417.")

    assert handled and retry.startswith("Forget applied. Removed "), retry
    assert not path.exists()
    assert _carried(chat) == []


def test_an_unfinished_forget_withholds_the_chats_documents_until_it_completes(isolated_pages):
    from core.memory import revocation

    chat = _chat("forget-paste-pending")
    _paste(chat, FACT + "\n" + INVENTORY, turn="t1")
    assert _carried(chat)
    operation = revocation.begin(chat, "4417", isolated_pages)

    # Unknown state fails closed: while an erasure is open, nothing it might cover is carried.
    assert _carried(chat) == []

    revocation.complete(operation, isolated_pages)
    assert not any("4417" in entry["text"] for entry in _carried(chat))


def test_a_home_whose_forget_authority_was_lost_carries_no_document(isolated_pages):
    from core.memory.revocation import _directory

    chat = _chat("forget-paste-lost-authority")
    _paste(chat, INVENTORY, turn="t1")
    assert _carried(chat)
    # The home holds memory, so a missing ledger is a lost one: what it forgot is unknown.
    (_directory(isolated_pages) / "ledger.json").unlink()

    assert _carried(chat) == []


def test_a_forget_that_starts_while_a_turn_reads_a_new_homes_documents_keeps_its_words_out(tmp_path, monkeypatch):
    """Review of 20ac941: the "never held memory" answer was taken once, before any document was read. A forget
    that started its barrier after that answer, while the turn was reading the document, did not stop its carry."""
    import core.chat_attachments as ca
    import core.runtime_paths as runtime_paths
    from core.memory import revocation

    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    try:
        chat = _chat("forget-paste-new-home-race")
        record = ca.stage_document(session_id=chat, data=(FACT + "\n" + INVENTORY).encode("utf-8"))
        ca.bind_to_turn(session_id=chat, turn_id="t1", attachment_ids=[record["id"]])
        assert revocation.memory_home_is_new(), "probe is blind: the home already held memory"
        read = ca._open_regular

        def read_while_a_forget_starts(path):
            # The same chat's forget opens its barrier between the turn's first look and its read of the bytes.
            if Path(path).name == f"{record['id']}.bin":
                revocation.begin(chat, "4417", tmp_path)
            return read(path)

        monkeypatch.setattr(ca, "_open_regular", read_while_a_forget_starts)

        assert not any("4417" in entry["text"] for entry in _carried(chat))
    finally:
        runtime_paths.configure_runtime_home(None)


def test_the_words_pasted_again_after_a_forget_are_not_carried_into_later_turns(isolated_pages):
    chat = _chat("forget-paste-again")
    _paste(chat, FACT + "\n" + INVENTORY, turn="t1")
    handled, reply = command(isolated_pages, chat, "Forget my locker code is 4417.")
    assert handled and reply.startswith("Forget applied"), reply

    again = _paste(chat, INVENTORY + "\n" + FACT, turn="t3")
    later = _paste(chat, INVENTORY.replace("harbor", "orchard"), turn="t4")

    carried = _carried(chat)
    assert [entry["attachment_id"] for entry in carried] == [later["id"]], carried
    assert _bytes_path(again).exists(), "the paste itself is the user's; only its carry into later turns is refused"


def _judge(body: dict) -> str | None:
    """The scripted model answers the entity-ambiguity probe as a model would for a plain question: not ambiguous.
    Every other call gets the table's reply, so a later turn reaches the answering lane that carries documents."""
    if "well-known referents" in json.dumps(body.get("messages") or []):
        return '{"ambiguous": false, "referents": [], "clarification": ""}'
    return None


@pytest.fixture(scope="module")
def paste_rig(tmp_path_factory: pytest.TempPathFactory):
    home = tmp_path_factory.mktemp("forget-paste-home")
    with CapturingProvider(reply_fn=_judge) as provider, ServedDaemon(Path(home), provider=provider) as daemon:
        result = daemon.certify()
        assert result.get("state") == "verified", f"scripted provider did not certify: {result}"
        yield daemon, provider, Path(home)


def _reply(payload: dict) -> str:
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    return str(message.get("content") or payload.get("response") or "")


def test_served_paste_then_forget_then_ask_never_carries_the_words_to_the_model(paste_rig):
    daemon, provider, home = paste_rig
    session = canonical_session("forget-paste-served")
    paste = INVENTORY[:2000] + "\n" + FACT + "\n" + INVENTORY[2000:] + "\n"
    uploaded = daemon.upload(session_id=session, name="pasted.txt", data=paste.encode("utf-8"),
                             declared_type="text/plain", source="paste")
    assert uploaded["status"] == 201 and uploaded["attachment"]["document"] is True, uploaded
    daemon.chat("What is on the last line of what I pasted?", session_id=session, attachments=[uploaded["attachment"]["id"]])
    provider.reset()
    daemon.chat("Tell me a short rhyme about harbor lanterns.", session_id=session)
    assert provider.any_payload_has("4417"), "probe is blind: the pasted document never reached a later turn"

    reply = _reply(daemon.chat("Forget my locker code is 4417.", session_id=session))
    assert reply == "Forget applied. Removed 1 memory entry.", reply

    provider.reset()
    daemon.chat("Tell me a short rhyme about harbor lanterns.", session_id=session)
    assert provider.payloads(), "the model was never called, so nothing can be claimed about what it received"
    assert not provider.any_payload_has("4417")
    held = [str(path.relative_to(home)) for path in (home / "data" / "chat_attachments").glob("*")
            if path.is_file() and b"4417" in path.read_bytes()]
    assert held == []
