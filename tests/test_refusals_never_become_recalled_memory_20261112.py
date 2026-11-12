"""The runtime's own word that no answer existed never becomes recalled memory (Database run 14a, 2026-11-12).

On the Mac run 14a, a library chat had earlier turns end in the authorship refusal ("I can't publish this answer:
The checked model (qwen3:8b) couldn't finish this answer ... `ollama-local:qwen3:4b` is not certified to author on
this runtime ..."). Every finalized turn kept its assistant side as "assistant said" evidence, so when the person
asked "is Ines holding any books at the moment", the retrieved context was those refusals, sentence by sentence, and
the model answered that it had no context.

The person's words from such a turn are memory. The refusal or no-answer notice is not: it is not written, a
profile written before this does not serve it (occurrences and receipts), and a chat summary does not quote it as
the chat's last outcome. An answer that explains a failure in the model's own words is still memory.
Every name and value below is written for this file.
"""
from __future__ import annotations

import json

import pytest

import core.context_retrieval as cr
from core import memory_receipts
from core.context_namespace import ensure_chat_namespace
from core.memory.entries import resolve_memory_access_policy
from core.runtime_paths import configure_runtime_home

CHAT = "chat-library-loans"
LOAN = "Ines borrowed Dune on 3 November and has not returned it yet"
QUESTION = "which book did Ines borrow, and can you answer that now?"
#: Sentences of the refusal that run 14a served one by one as "assistant said".
REFUSAL_MARKS = ("I can't publish this answer", "is not certified to author", "pick a ready model",
                 "this runtime can look up")


def _authorship_refusal() -> str:
    """The refusal the runtime ships when its checked model failed and the fallback is not certified."""
    from core.final_answer_authorship import REASON_BLOCKED_BEFORE_GENERATION, AuthorRole, AuthorshipDecision

    decision = AuthorshipDecision(eligible=False, author_role=AuthorRole.FINAL_ANSWER, task_class="general",
                                  selected_model="ollama-local:qwen3:4b", reason=REASON_BLOCKED_BEFORE_GENERATION)
    refusal = decision.refusal_text(attempted_failed=("ollama-local:qwen3:8b",))
    # The exact sentences run 14a's retrieved context carried.
    assert refusal.startswith("I can't publish this answer: The checked model (qwen3:8b) couldn't finish this answer")
    assert "`ollama-local:qwen3:4b` is not certified to author on this runtime" in refusal
    return refusal


def _no_answer_texts() -> dict[str, str]:
    import core.grounding_publication as grounding
    from core.agent_runtime.memory_runtime import chat_surface_honest_degraded_response
    from core.final_answer_authorship import no_model_notice_text

    class _FailedExecution:  # a model call that came back with nothing usable
        source = "provider_error"
        details: dict = {}

    return {
        "authorship refusal": _authorship_refusal(),
        "no model set up": no_model_notice_text(),
        "grounding refusal": f"{grounding._REFUSAL_LEAD} The pages I found don't state it.",
        "no model response": chat_surface_honest_degraded_response(None, _FailedExecution()),
    }


@pytest.fixture()
def home(tmp_path, monkeypatch):
    import core.embedding_service as embedding_service
    from core.runtime_provider_defaults import apply_product_runtime_defaults

    profile = tmp_path / "profile"
    profile.mkdir()
    for name in ("NULLA_HOME", "VOOL_HOME"):
        monkeypatch.setenv(name, str(profile))
    product: dict[str, str] = {}
    apply_product_runtime_defaults(product)  # the memory path a shipped process runs (capsule 2, receipts, kernel)
    for name, value in product.items():
        monkeypatch.setenv(name, value)
    configure_runtime_home(profile)
    monkeypatch.setattr(embedding_service, "_best_embed_model", lambda: None)
    from core.memory.revocation import initialize_memory_home
    from storage.migrations import run_migrations

    initialize_memory_home()  # the forget authority every served read answers to
    run_migrations()
    ensure_chat_namespace(CHAT, grant_current_receipts=False)
    yield str(profile)
    configure_runtime_home(None)


def _policy():
    return resolve_memory_access_policy(chat_id=CHAT)


def _store(home: str, user: str, assistant: str) -> dict:
    return cr.store_turn(CHAT, user, assistant, access_policy=_policy(),
                         source_context={"chat_id": CHAT, "runtime_home": home})


def _memory(home: str):
    from core.vool_memory import VoolMemory

    return VoolMemory(runtime_home=home)


def _rows(home: str) -> list[tuple[str, str]]:
    mem = _memory(home)
    try:
        with mem._lock:
            return [(str(r[0]), str(r[1])) for r in mem._conn.execute(
                "SELECT role, body FROM source_occurrences WHERE chat_scope = ? AND status = 'active'", (CHAT,))]
    finally:
        mem.close()


def _as_an_older_build_wrote_it(home: str, body: str) -> str:
    """An assistant occurrence (and its receipt) written the way every build before this one did."""
    import time

    mem = _memory(home)
    try:
        occurrence = mem.occurrence_store(chat_scope=CHAT, role="assistant", body=body, authority="assistant-output",
                                          project_id=_policy().project_id, source_kind="live-turn",
                                          statement_at=time.time())
        memory_receipts.write_receipt(mem, occurrence, chat_scope=CHAT)
        return occurrence.occurrence_id
    finally:
        mem.close()


def _retrieved(home: str, question: str) -> str:
    out = cr.inject_retrieved(CHAT, question, [{"role": "user", "content": question}], access_policy=_policy(),
                              source_context={"chat_id": CHAT, "runtime_home": home})
    return "\n".join(str(m.get("content") or "") for m in out if isinstance(m, dict))


@pytest.mark.parametrize("family", ["authorship refusal", "no model set up", "grounding refusal", "no model response"])
def test_a_turn_that_ended_without_an_answer_keeps_the_persons_words_only(home, family):
    notice = _no_answer_texts()[family]
    result = _store(home, "can you add a notes column to the loans table", notice)
    rows = _rows(home)
    assert ("user", "can you add a notes column to the loans table") in rows, rows
    assert not [body for role, body in rows if role == "assistant"], f"the {family} was stored as memory: {rows}"
    assert result.get("status") != "failed", result
    from core.context_history_authority import assistant_text_is_no_answer

    assert assistant_text_is_no_answer(notice), f"the {family} text must be one the runtime ships"


def test_an_answer_that_explains_a_failure_is_still_memory(home):
    """The control: a model's own words about a failure are an answer, and recall keeps them."""
    answer = "I couldn't find Tomas in the loans table, so nothing he borrowed is still out."
    _store(home, "has Tomas given everything back", answer)
    assert ("assistant", answer) in _rows(home)
    mem = _memory(home)
    try:
        served = [occ.body for occ, _score in mem.occurrence_search("Tomas loans table", chat_scope=CHAT)]
    finally:
        mem.close()
    assert answer in served, served


def test_a_profile_that_already_holds_refusals_serves_none_of_them(home):
    _store(home, LOAN, "Noted.")
    refusal_id = _as_an_older_build_wrote_it(home, _authorship_refusal())
    answer_id = _as_an_older_build_wrote_it(home, "The library database contains 5 books in total.")
    mem = _memory(home)
    try:
        lexical = [occ.occurrence_id for occ, _s in mem.occurrence_search(
            "answer model check certified library books", chat_scope=CHAT, limit=20)]
        assert answer_id in lexical, "the control answer must be findable by the same search"
        assert refusal_id not in lexical, "a stored refusal was served by the lexical door"
        loan = next(occ for occ, _s in mem.occurrence_search("Ines Dune", chat_scope=CHAT))
        around = [occ.occurrence_id for occ in mem.occurrence_neighbors(loan, before=5, after=5)]
        assert answer_id in around and refusal_id not in around, around
        stated = [occ.occurrence_id for occ in mem.occurrence_stated_between(chat_scope=CHAT, start=0.0, end=4e9)]
        assert answer_id in stated and refusal_id not in stated, stated
        # The row stays on disk, whole: the forget law and diagnostic reads still see it.
        kept = mem.occurrence_get(refusal_id)
        assert kept is not None and kept.body.startswith("I can't publish this answer"), kept
        receipts = memory_receipts.receipts_for_scope(mem, CHAT)
    finally:
        mem.close()
    by_occurrence = {r["occurrence_id"]: r for r in receipts}
    assert answer_id in by_occurrence, "the control answer's receipt must be served"
    assert refusal_id not in by_occurrence, "a stored refusal was served by its receipt"


def test_the_next_question_gets_no_refusal_in_its_retrieved_context(home):
    """The run-14a shape, on a profile an older build wrote: refusals beside a fact the question needs."""
    _store(home, "how many books are there", "The library database contains 5 books in total.")
    _store(home, LOAN, "Noted.")
    for ask in ("can you add a notes column to the books table", "has Tomas given everything back"):
        _store(home, ask, "")
        _as_an_older_build_wrote_it(home, _authorship_refusal())
    text = _retrieved(home, QUESTION)
    assert "<retrieved_context>" in text and "Dune" in text, text
    served = [mark for mark in REFUSAL_MARKS if mark in text]
    assert not served, f"the refusal reached the retrieved context: {served}"


def test_refusal_turns_written_now_leave_nothing_for_the_next_question(home):
    _store(home, "how many books are there", "The library database contains 5 books in total.")
    _store(home, LOAN, "Noted.")
    for ask in ("can you add a notes column to the books table", "has Tomas given everything back"):
        _store(home, ask, _authorship_refusal())
    text = _retrieved(home, QUESTION)
    assert "Dune" in text, text
    assert not [mark for mark in REFUSAL_MARKS if mark in text], text


def test_a_chat_summary_never_quotes_a_refusal_as_the_last_outcome(home):
    from core.memory.files import session_summaries_path
    from core.persistent_memory import append_conversation_event

    append_conversation_event(session_id=CHAT, user_input="how many books are there",
                              assistant_output="The library database contains 5 books in total.", source_context={})
    append_conversation_event(session_id=CHAT, user_input="can you add a notes column to the books table",
                              assistant_output=_authorship_refusal(), source_context={})
    rows = [json.loads(line) for line in session_summaries_path().read_text(encoding="utf-8").splitlines()
            if line.strip()]
    latest = [row for row in rows if row.get("session_id") == CHAT][-1]["summary"]
    assert "can you add a notes column to the books table" in latest, latest  # the person's ask is kept
    assert "I can't publish this answer" not in latest and "not certified" not in latest, latest


def test_a_summary_written_before_this_is_served_without_the_refusal(home):
    from core.memory.entries import search_session_summaries
    from core.memory.files import session_summaries_path

    def row(session: str, summary: str) -> str:
        return json.dumps({"session_id": session, "summary": summary, "created_at": "2026-11-12T18:50:00+00:00",
                           "scope": "chat", "source": "session_summary", "status": "active",
                           "origin_chat_id": session, "origin_project_id": "",
                           "provenance": {"kind": "derived_session_summary", "origin_chat_id": session}})

    refused = ("Recent asks: which loans are overdue in the library database. Last assistant outcome: "
               + _authorship_refusal()[:140] + ".")
    answered = ("Recent asks: how many loans does the library database hold. Last assistant outcome: "
                "The library database holds 4 loans.")
    ensure_chat_namespace("chat-earlier-answer", grant_current_receipts=False)
    session_summaries_path().parent.mkdir(parents=True, exist_ok=True)
    session_summaries_path().write_text(row(CHAT, refused) + "\n" + row("chat-earlier-answer", answered) + "\n",
                                        encoding="utf-8")

    def served(chat: str) -> list[str]:
        policy = resolve_memory_access_policy(chat_id=chat)
        return [h["summary"] for h in search_session_summaries("library database loans", access_policy=policy, limit=4)
                if h.get("session_id") == chat]

    assert served(CHAT) == ["Recent asks: which loans are overdue in the library database."], served(CHAT)
    # The control: a real last outcome is still served.
    assert served("chat-earlier-answer") == [answered], served("chat-earlier-answer")
