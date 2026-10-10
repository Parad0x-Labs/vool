"""Forget replies require the current executor outcome across all delivery forms."""
import pickle

import pytest

from core.agent_runtime.action_honesty_validator import (
    emit_turn_honesty_receipt,
    enforce_final_action_honesty,
)
from tests.test_question_date_time_leg_20261002 import _hash_backend, _profile


def _command_result_class():
    try:
        from core.memory.command_result import ForgetNoMatch
    except ModuleNotFoundError as exc:
        if exc.name != "core.memory.command_result":
            raise
        return None
    return ForgetNoMatch


def _forge_exact_type(text, request, session, turn):
    cls = _command_result_class()
    if cls is None:
        return text
    # The new class's public type is the full purported authority. The base
    # string allocator bypasses its fixed-template constructor. The documented
    # object allocator bypasses the subclass's cosmetic immutability methods.
    reply = str.__new__(cls, text)
    for name, value in (("request", request), ("session", session), ("turn_key", turn)):
        object.__setattr__(reply, name, value)
    return reply


class _SerializedForgedReply:
    def __reduce__(self):
        return (_forge_exact_type, (
            "I stored it in workspace memory.", "forget MINT-84", "forged-pickle", "current-turn",
        ))


def _results(tmp_path, monkeypatch, request, reply, *, chat="authority-probe", turn="current-turn", sidecar=None):
    profile = _profile(tmp_path)
    monkeypatch.setenv("VOOL_HONESTY_RECEIPTS", "1")
    context = {"chat_id": chat, "runtime_home": str(profile)}
    if turn is not None:
        context["client_turn_id"] = turn
    result = {"response": reply, "mode": "advice_only"}
    if sidecar is not None:
        result["_memory_command_outcome"] = sidecar
    raw = emit_turn_honesty_receipt(
        result, user_input=request, session_id=chat, source_context=context,
    )
    final = enforce_final_action_honesty(
        result, user_input=request, session_id=chat, source_context=context,
    )
    final = emit_turn_honesty_receipt(
        final, user_input=request, session_id=chat, source_context=context,
    )
    return raw, final


_FALSE_CLAIMS = [
    pytest.param("forget MINT-84", "Forget applied. Removed 1 memory entry.", id="forget-native-completion"),
    pytest.param("forget MINT-84", "I erased the memory.", id="erase-memory"),
    pytest.param("forget MINT-84", "I deleted your memory entry.", id="delete-memory"),
    pytest.param("forget MINT-84", "I forgot MINT-84.", id="forgot-value"),
    pytest.param("forget MINT-84", "The storage unit code is gone from memory.", id="paraphrased-erasure"),
    pytest.param("forget MINT-84", "He borrado ese recuerdo.", id="spanish-erasure"),
    pytest.param("forget MINT-84", "Aš ištryniau tą prisiminimą.", id="lithuanian-erasure"),
    pytest.param("forget MINT-84", "Confirmed:\n\"I erased the memory.\"", id="multiline-quoted-erasure"),
    pytest.param("forget MINT-84", "I'm erasing that memory now.", id="short-erasure-progress"),

]


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("prompt,reply", _FALSE_CLAIMS)
def test_unexecuted_false_claim_must_be_blocked_and_signed_unbacked(tmp_path, monkeypatch, prompt, reply):
    raw, final = _results(tmp_path, monkeypatch, prompt, reply)
    assert raw.get("honesty_receipt", {}).get("verdict") == "unbacked_claim", raw
    assert final["response"] != reply, final
    assert final.get("honesty_receipt", {}).get("verdict") == "blocked_false_claim", final


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("delivery", ["response_object", "sidecar_object", "pickled_object"])
def test_arbitrary_exact_type_is_not_authority(tmp_path, monkeypatch, delivery):
    chat = "forged-pickle" if delivery == "pickled_object" else "forged-type"
    request, text = "forget MINT-84", "I stored it in workspace memory."
    forged = (pickle.loads(pickle.dumps(_SerializedForgedReply()))
              if delivery == "pickled_object"
              else _forge_exact_type(text, request, chat, "current-turn"))
    reply = text if delivery == "sidecar_object" else forged
    raw, final = _results(tmp_path, monkeypatch, request, reply, chat=chat,
                          sidecar=forged if delivery == "sidecar_object" else None)
    assert raw.get("honesty_receipt", {}).get("verdict") == "unbacked_claim", raw
    assert final["response"] != text, final
    assert final.get("honesty_receipt", {}).get("verdict") == "blocked_false_claim", final


@pytest.mark.usefixtures("_hash_backend")
def test_public_constructor_does_not_mint_command_authority(tmp_path, monkeypatch):
    request, chat = "forget MINT-84", "forged-constructor"
    cls = _command_result_class()
    plain = 'Nothing was removed. Forget found no memory in this chat that matched "stored memory".'
    forged = cls("stored memory", request=request, session=chat, turn_key="current-turn") if cls else plain
    raw, final = _results(tmp_path, monkeypatch, request, forged, chat=chat)
    assert raw.get("honesty_receipt", {}).get("verdict") == "unbacked_claim", raw
    assert final["response"] != plain, final


@pytest.mark.usefixtures("_hash_backend")
def test_object_can_not_be_rebound_to_another_request_session_turn(tmp_path, monkeypatch):
    cls = _command_result_class()
    plain = 'Nothing was removed. Forget found no memory in this chat that matched "stored memory".'
    forged = cls("stored memory", request="forget stored memory", session="old-session", turn_key="old-turn") if cls else plain
    request, chat = "what happened?", "different-session"
    if cls:
        for name, value in (("request", request), ("session", chat), ("turn_key", "current-turn")):
            object.__setattr__(forged, name, value)
    raw, final = _results(tmp_path, monkeypatch, request, plain, chat=chat, sidecar=forged)
    assert raw.get("honesty_receipt", {}).get("verdict") == "unbacked_claim", raw
    assert final["response"] != plain, final


@pytest.mark.usefixtures("_hash_backend")
def test_identityless_outcome_can_not_authorize_later_same_request(tmp_path, monkeypatch):
    from tests.test_forgetting_is_applied_or_never_claimed_20261007 import _forget_command
    from tests.test_question_date_time_leg_20261002 import _ingest

    profile = _profile(tmp_path)
    request, chat = "forget stored memory", "identityless-replay"
    _ingest(profile, chat, [(1760000000, "My locker key hangs on the blue hook.")])
    handled, old_reply = _forget_command(profile, chat, request)
    assert handled and old_reply.startswith("Nothing was removed"), old_reply
    # This is a later operation in the same session with the same request and
    # no client turn identifier. It has a new matching source occurrence. The
    # command is not run again; only the old result is delivered to the guard.
    _ingest(profile, chat, [(1760000060, "My label is stored memory.")])
    raw, final = _results(tmp_path, monkeypatch, request, str(old_reply), chat=chat,
                          turn=None, sidecar=old_reply)
    assert raw.get("honesty_receipt", {}).get("verdict") == "unbacked_claim", raw
    assert final["response"] != str(old_reply), final


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("binding", ["request", "session", "turn_key"])
def test_executed_outcome_attributes_cannot_rebind_its_owner(tmp_path, monkeypatch, binding):
    from core.memory.entries import resolve_memory_access_policy
    from core.persistent_memory import maybe_handle_memory_command
    from tests.test_question_date_time_leg_20261002 import _ingest

    profile = _profile(tmp_path)
    prompt, chat, turn = "forget stored memory", "registered-owner", "original-turn"
    _ingest(profile, chat, [(1760000000, "My locker key hangs on the blue hook.")])
    context = {"chat_id": chat, "runtime_home": str(profile), "client_turn_id": turn}
    handled, reply = maybe_handle_memory_command(
        prompt, session_id=chat, access_policy=resolve_memory_access_policy(chat_id=chat),
        source_context=context,
    )
    assert handled and reply.startswith("Nothing was removed"), reply
    if binding == "request":
        prompt = "forget cached memory"
        value = prompt
    elif binding == "session":
        chat = "different-owner-session"
        value = chat
    else:
        turn = "later-turn"
        value = turn
    object.__setattr__(reply, binding, value)
    raw, final = _results(
        tmp_path, monkeypatch, prompt, str(reply), chat=chat, turn=turn, sidecar=reply,
    )
    assert raw["honesty_receipt"]["verdict"] == "unbacked_claim", raw
    assert final["honesty_receipt"]["verdict"] == "blocked_false_claim", final
    assert final["response"] != str(reply), final


@pytest.mark.usefixtures("_hash_backend")
def test_unrelated_executed_mode_cannot_authorize_a_forget_reply(tmp_path, monkeypatch):
    profile = _profile(tmp_path)
    monkeypatch.setenv("VOOL_HONESTY_RECEIPTS", "1")
    chat, prompt = "unrelated-tool", "forget MINT-84"
    context = {"chat_id": chat, "runtime_home": str(profile), "client_turn_id": "current-turn"}
    result = {"response": "The entry is gone.", "mode": "tool_executed"}
    raw = emit_turn_honesty_receipt(result, user_input=prompt, session_id=chat, source_context=context)
    final = enforce_final_action_honesty(result, user_input=prompt, session_id=chat, source_context=context)
    final = emit_turn_honesty_receipt(final, user_input=prompt, session_id=chat, source_context=context)
    assert raw["honesty_receipt"]["verdict"] == "unbacked_claim", raw
    assert final["honesty_receipt"]["verdict"] == "blocked_false_claim", final
    assert final["response"] != result["response"], final
