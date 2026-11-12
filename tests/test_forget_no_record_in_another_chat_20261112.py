"""NO RECORD = REFUSE after a forget, also when the question comes from another chat.

Found by the Codex Oct 20 thread (2026-11-12, same on c9a7f426 and 28fc78dd): after "Forget PL-7731" in one chat,
"What do you remember about PL-7731?" in a second chat went to the model. Nothing stored came back, but the forget law's
no-record answer was not given: the revocations are read per chat, and that question shape is left to the memory lanes,
which hold nothing any more.

Law (``core.web.api.runtime._forgotten_in_another_chat_clauses``): a recall question about words a completed forget in
another chat revoked is answered ``FORGOTTEN_IN_ANOTHER_CHAT_REPLY`` without a model call, unless something holds the
words again. The forget law is per chat, so a new saved entry, or the person stating the words in this chat, is theirs
again and is answered as usual. Unrelated questions are untouched.

All names and codes are invented for this contract.
"""
from __future__ import annotations

import json

import pytest

CODE = "PL-7731"
ASKS = ("What do you remember about PL-7731?", "Do you remember PL-7731?")


def _judge(body: dict) -> str | None:
    """Answer the entity-ambiguity probe as a model would; every other call gets the scripted reply."""
    if "well-known referents" in json.dumps(body.get("messages") or []):
        return '{"ambiguous": false, "referents": [], "clarification": ""}'
    return None


def _reply(payload: dict) -> str:
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    return str(message.get("content") or payload.get("response") or "")


@pytest.fixture()
def served(tmp_path):
    from tests._reader_served_rig import CapturingProvider, ServedDaemon, canonical_session

    with CapturingProvider(default="A lamp glows by the window.", reply_fn=_judge) as provider, \
            ServedDaemon(tmp_path / "home", provider=provider) as daemon:
        assert daemon.certify().get("state") == "verified"

        def ask(text: str, chat: str) -> tuple[str, bool]:
            provider.reset()
            answer = _reply(daemon.chat(text, session_id=canonical_session(chat)))
            return answer, bool(provider.payloads())

        assert "Locked in" in ask(f"Remember that the Pine lamp model is {CODE}", "forgetting-chat")[0]
        assert ask(f"Forget {CODE}", "forgetting-chat")[0].startswith("Forget applied")
        yield ask


def test_another_chat_gets_the_no_record_answer_without_a_model_call(served):
    from core.web.api.runtime import FORGOTTEN_IN_ANOTHER_CHAT_REPLY

    for question in ASKS:
        answer, called = served(question, "another-chat")
        assert answer == FORGOTTEN_IN_ANOTHER_CHAT_REPLY, (question, answer)
        assert not called, question


def test_the_forgetting_chat_keeps_its_own_no_record_answer(served):
    for question in ASKS:
        answer, called = served(question, "forgetting-chat")
        assert answer == "I have no record of that information in this chat; it was forgotten.", (question, answer)
        assert not called, question


def test_an_unrelated_recall_question_in_another_chat_still_reaches_the_model(served):
    answer, called = served("What do you remember about the weather?", "another-chat")
    assert called and answer == "A lamp glows by the window.", answer


def test_words_the_person_states_again_in_a_new_chat_are_that_chats_own(served):
    served(f"My Pine lamp is the {CODE}, by the way.", "new-chat")
    answer, called = served(ASKS[0], "new-chat")
    # This chat's own record holds the words now (the forget law is per chat): the turn goes on as usual.
    assert called, answer


def test_words_saved_again_are_recalled_from_memory(served):
    assert "Locked in" in served(f"Remember that the Pine lamp model is {CODE}", "resave-chat")[0]
    answer, _called = served(ASKS[0], "fourth-chat")
    assert CODE in answer and "forgotten" not in answer, answer


def test_a_plain_question_in_another_chat_stays_with_the_other_chat_search(served):
    """Not a question put to memory: the other-chat search keeps it, answers no record, and sends nothing to the model."""
    answer, called = served("What is the saved model of my Pine lamp?", "another-chat")
    assert not called and CODE not in answer and answer.startswith("I don't have that saved"), answer
