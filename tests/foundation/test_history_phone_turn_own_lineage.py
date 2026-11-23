"""A phone's answer in the chat history is judged by its own request, not by every answer with the same words.

Measured 2026-11-23 on the phone: the "Try again" turn stopped for the owner's OK and its answer,
"Manual mode requires approval for this exact action. Review the details, then allow or deny this
request.", came back from the history as an empty bubble. The phone's turns are accepted under the
phone's principal, so the owner_local replay read is refused for them; the history then judged the
answer by its text, and the same fixed sentence, erased with a deleted chat weeks before, blanked it.
"""
from __future__ import annotations

from core.finalization import AVAILABILITY_ERASED, erase_finalization_payload, payload_availability_for_hash

from tests.foundation.test_a8_privacy_pass001 import (  # noqa: F401 (fixture)
    _append_event, _finalize_under_request, _history, _runtime, _sha, a8_env,
)

SENTENCE = "Manual mode requires approval for this exact action. Review the details, then allow or deny this request."
PHONE = "channel:http:mobile:phone:0a1b2c3d4e5f60718293"


def _phone_request(value: str) -> str:
    from core.invocation.ledger import accept_invocation

    return accept_invocation(external_kind="http", external_value=value, principal=PHONE)["request_id"]


def _assistant(session):
    return [m for m in _history(_runtime(), session)["messages"] if m["role"] == "assistant"]


def test_a_phone_answer_shows_although_the_same_words_were_erased_elsewhere(a8_env):
    old = _phone_request("mob-old-deleted-chat")
    erased = _finalize_under_request(SENTENCE, old)
    erase_finalization_payload(erased["finalization_id"], reason="chat deleted", governance_actor="owner_local")
    new = _phone_request("mob-try-again")
    own = _finalize_under_request(SENTENCE, new)
    # The words themselves are judged erased (the strictest row wins): what the history did before.
    assert payload_availability_for_hash(_sha(SENTENCE)) == AVAILABILITY_ERASED
    _append_event(a8_env, "sess-phone", "Try again", SENTENCE, request_id=new)
    shown = _assistant("sess-phone")
    assert [m["content"] for m in shown] == [SENTENCE], shown
    # Its own record, read as the phone that sent it: checked, and named by its own finalization.
    assert shown[0]["a7"]["status"] == "verified", shown
    assert shown[0]["a7"]["finalization_id"] == own["finalization_id"], shown


def test_a_turn_from_some_other_host_is_still_read_as_owner_local(a8_env):
    from core.finalization import owner_view_principal
    from core.invocation.ledger import accept_invocation

    other = accept_invocation(external_kind="http", external_value="lan-guest", principal="channel:http:203.0.113.50")
    assert owner_view_principal(other["request_id"]) == "owner_local"
    assert owner_view_principal(_phone_request("mob-phone")) == PHONE
    assert owner_view_principal("") == "owner_local"


def test_a_phone_answer_that_was_itself_erased_stays_blank(a8_env):
    rid = _phone_request("mob-erased-own")
    commit = _finalize_under_request("a private phone answer", rid)
    _append_event(a8_env, "sess-phone-2", "question", "a private phone answer", request_id=rid)
    erase_finalization_payload(commit["finalization_id"], reason="erase", governance_actor="owner_local")
    _append_event(a8_env, "sess-phone-2", "question", "a private phone answer", request_id=rid)
    shown = _assistant("sess-phone-2")
    assert shown and all(m["content"] == "" for m in shown), shown
    assert shown[-1]["a7"]["status"] == "unavailable_by_policy"
