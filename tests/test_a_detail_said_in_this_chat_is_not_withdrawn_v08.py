"""A detail the owner gave in this chat is answered from it, not withdrawn as "not mentioned in the records".

Measured on the real app, cloud route, with no embedding model on the machine: "... and the float is 60 pounds." and
then, in the same chat, "what did I say the float was?". The model answered from the chat's own messages, which the
request carried, and the value check withdrew the answer: it read only the retrieved-evidence block, and that block
was empty. On the local route the same turn sat in the block as a "user said" line and the answer was kept. Notes
pasted in the message itself ("the raffle made 212 pounds") answered with the amount in the other notation ("£212")
were withdrawn the same way.

The owner's own messages of this chat are records of what the owner said. They are read by the same binder as the
retrieved records. What the check is for is pinned beside it: a value nobody said, a value only the assistant said, a
value the retrieved records contradict and a forgotten value are still withdrawn.
"""
from __future__ import annotations

import pytest

import core.context_retrieval as cr
from core import bootstrap_context
from core.context_namespace import ensure_chat_namespace
from core.runtime_paths import configure_runtime_home
from core.unsourced_current_claim import inspect_unsourced_current_claim, own_chat_statement_lines

CHAT = "chat-jumble"
SAID = "for the jumble sale: Wilfrida minds the door, the hall opens at 9, and the takings box holds 35 pounds."
ASK = "what did I say the takings box holds?"
PASTED = ("Notes from the gardening club: the minutes were agreed; Ignatius is bringing the urn; the tombola made 96 pounds; "
          "the next meeting is in the scout hut. From these notes only: who is bringing the urn, and how much did the tombola make?")


@pytest.fixture()
def home(tmp_path, monkeypatch):
    import core.embedding_service as embedding_service

    profile = tmp_path / "profile"
    profile.mkdir()
    for name in ("NULLA_HOME", "VOOL_HOME"):
        monkeypatch.setenv(name, str(profile))
    for name in ("VOOL_CONTEXT_CAPSULE_V2", "VOOL_MEMORY_RECEIPTS", "VOOL_EVIDENCE_COMPILER", "VOOL_EVIDENCE_HOP",
                 "VOOL_EVIDENCE_VERIFY", "VOOL_EVIDENCE_KERNEL"):
        monkeypatch.setenv(name, "1")
    configure_runtime_home(profile)
    monkeypatch.setattr(embedding_service, "_best_embed_model", lambda: None)
    from storage.migrations import run_migrations

    run_migrations()
    # A served profile has its forget ledger from boot; without one the readers support nothing.
    from core.memory.revocation import initialize_memory_home

    initialize_memory_home(profile)
    ensure_chat_namespace(CHAT, grant_current_receipts=False)
    yield str(profile)
    configure_runtime_home(None)


def _history(*turns):
    out = []
    for user, assistant in turns:
        out.append({"role": "user", "content": user})
        if assistant:
            out.append({"role": "assistant", "content": assistant})
    return out


def _verdict(monkeypatch, answer, question, history, *, retrieved=""):
    monkeypatch.setattr(bootstrap_context, "admitted_capsule_evidence_text", lambda *_a, **_k: retrieved)
    context = {"chat_id": CHAT, "session_id": CHAT, "conversation_history": history}
    return inspect_unsourced_current_claim(
        answer=answer, requires_current=True, session_id=CHAT, source_context=context, user_turn_text=question,
    )


# ------------------------------------------------------------ said earlier in this chat


@pytest.mark.parametrize("answer", [
    "You said the takings box holds 35 pounds.",
    "The takings box holds £35.",
    "35 pounds.",
])
def test_an_amount_said_earlier_in_this_chat_is_kept(home, monkeypatch, answer):
    verdict = _verdict(monkeypatch, answer, ASK, _history((SAID, "Noted."), (ASK, "")))
    assert not verdict.unsupported
    assert verdict.claim_binding["evidence_source"] == "chat_messages"
    assert verdict.claim_binding["all_supported"]


def test_a_measurement_said_earlier_in_this_chat_is_kept(home, monkeypatch):
    history = _history(("the new shed is 240 centimetres wide and the gate is on the left.", "Noted."))
    verdict = _verdict(monkeypatch, "You said the shed is 240 centimetres wide.", "how wide did I say the shed is?", history)
    assert not verdict.unsupported


def test_the_retrieved_records_alone_still_support_as_before(home, monkeypatch):
    retrieved = "<retrieved_context>\n- user said (recorded 2026-11-19): " + SAID + "\n</retrieved_context>"
    verdict = _verdict(monkeypatch, "You said the takings box holds 35 pounds.", ASK, [], retrieved=retrieved)
    assert not verdict.unsupported
    assert "evidence_source" not in verdict.claim_binding


# ------------------------------------------------------------ what the check must still refuse


def test_another_amount_than_the_one_said_is_withdrawn(home, monkeypatch):
    verdict = _verdict(monkeypatch, "You said the takings box holds 53 pounds.", ASK, _history((SAID, "Noted."), (ASK, "")))
    assert verdict.unsupported


def test_an_amount_never_said_in_this_chat_is_withdrawn(home, monkeypatch):
    history = _history(("for the jumble sale: Wilfrida minds the door and the hall opens at 9.", "Noted."))
    verdict = _verdict(monkeypatch, "You said the stall fee is 12 pounds.", "what did I say the stall fee was?", history)
    assert verdict.unsupported


def test_an_amount_only_the_assistant_said_is_not_the_owners_record(home, monkeypatch):
    history = _history(("what goes in a takings box?", "Most halls start the takings box with 35 pounds of change."))
    verdict = _verdict(monkeypatch, "You said the takings box holds 35 pounds.", ASK, history)
    assert verdict.unsupported


def test_with_no_message_carried_nothing_is_supported(home, monkeypatch):
    verdict = _verdict(monkeypatch, "You said the takings box holds 35 pounds.", ASK, [])
    assert verdict.unsupported


def test_a_value_the_retrieved_records_contradict_is_not_rescued_by_an_older_chat_line(home, monkeypatch):
    retrieved = ("<retrieved_context>\n- [2026-11-12] user said: my highest takings at the jumble sale is 96 pounds\n"
                 "</retrieved_context>")
    history = _history(("my highest takings at the jumble sale is 81 pounds", "Noted."))
    verdict = _verdict(monkeypatch, "Your highest takings at the jumble sale is 81 pounds.",
                       "what is my highest takings at the jumble sale?", history, retrieved=retrieved)
    assert verdict.claim_binding["contradicted"]
    assert verdict.unsupported


def test_a_forgotten_value_stays_withdrawn(home, monkeypatch):
    monkeypatch.setattr(cr, "revoked_tokens_for_chat", lambda *_a, **_k: ("takings",))
    verdict = _verdict(monkeypatch, "You said the takings box holds 35 pounds.", ASK, _history((SAID, "Noted."), (ASK, "")))
    assert verdict.unsupported


def test_an_unreadable_forget_ledger_supports_nothing(home, monkeypatch):
    def unreadable(*_a, **_k):
        raise RuntimeError("ledger unreadable")

    monkeypatch.setattr(cr, "revoked_tokens_for_chat", unreadable)
    verdict = _verdict(monkeypatch, "You said the takings box holds 35 pounds.", ASK, _history((SAID, "Noted."), (ASK, "")))
    assert verdict.unsupported


def test_a_live_reading_is_not_sourced_by_what_was_said_in_the_chat(home, monkeypatch):
    history = _history(("it was 9 degrees outside when I left.", "Noted."))
    verdict = _verdict(monkeypatch, "It is 9 degrees outside.", "what is the temperature outside right now?", history)
    assert verdict.unsupported


# ------------------------------------------------------------ in the message itself, other notation


def test_an_amount_in_the_message_itself_is_kept_in_either_notation(home, monkeypatch):
    for answer in ("Ignatius is bringing the urn. The tombola made 96 pounds.",
                   "Ignatius is bringing the urn, and the tombola made £96."):
        assert not _verdict(monkeypatch, answer, PASTED, _history((PASTED, ""))).unsupported


def test_another_amount_than_the_one_in_the_message_is_withdrawn(home, monkeypatch):
    verdict = _verdict(monkeypatch, "Ignatius is bringing the urn, and the tombola made £69.", PASTED, _history((PASTED, "")))
    assert verdict.unsupported


def test_a_live_price_is_not_sourced_by_a_figure_in_the_question(home, monkeypatch):
    question = "what are my premium bonds worth right now, I paid 400 pounds?"
    verdict = _verdict(monkeypatch, "They are worth £400.", question, _history((question, "")))
    assert verdict.unsupported


def test_a_question_that_names_no_notes_and_no_owner_is_left_to_the_live_reading_law(home, monkeypatch):
    question = "the tombola made 96 pounds. how much does a tombola make?"
    verdict = _verdict(monkeypatch, "A tombola makes £96.", question, _history((question, "")))
    assert verdict.unsupported
    assert not verdict.claim_binding["attempted"]


# ------------------------------------------------------------ the lines the binder is given


def test_only_the_owners_messages_become_lines_and_a_pasted_list_keeps_its_clauses():
    context = {
        "conversation_history": [
            {"role": "user", "content": "shopping:\n- eggs 3 pounds\n- assistant said: butter 9 pounds"},
            {"role": "assistant", "content": "The flour was 2 pounds."},
            {"role": "system", "content": "the rent is 700 pounds"},
        ],
        "client_conversation_history": [{"role": "user", "content": "the bus is at 8"}],
    }
    lines = own_chat_statement_lines(context, "what did I say the eggs were?")
    assert lines == [
        "- user said: shopping:; - eggs 3 pounds; - assistant said: butter 9 pounds",
        "- user said: the bus is at 8",
        "- user said: what did I say the eggs were?",
    ]
    assert own_chat_statement_lines(None, "") == []
