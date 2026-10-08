"""The memory command's authority survives delivery but cannot be copied from prose."""
import json

import pytest

from core.agent_runtime.action_honesty_validator import emit_turn_honesty_receipt, enforce_final_action_honesty
from tests.test_forgetting_is_applied_or_never_claimed_20261007 import _forget_command as command
from tests.test_question_date_time_leg_20261002 import _hash_backend, _ingest, _profile

pytestmark = pytest.mark.usefixtures("_hash_backend")


def guard(chat, prompt, reply):
    return enforce_final_action_honesty(
        {"response": reply, "mode": "advice_only"}, user_input=prompt,
        session_id=chat, source_context={"chat_id": chat},
    )


@pytest.mark.parametrize("prompt,reply", [
    ("Do not say I stored it in workspace memory. unless it really happened.",
     'Confirmed: "I stored it in workspace memory."'),
    ("Do not say created files unless verified.", 'Confirmed: "created files".'),
    ("Is the audit in progress?", 'Status: "audit in progress".'),
    ("Please explain restored memory.", 'I confirm "stored memory".'),
])
def test_raw_claim_and_guarded_repair_have_consistent_receipts(tmp_path, monkeypatch, prompt, reply):
    from core.honesty_receipt import VERDICT_BLOCKED, VERDICT_UNBACKED

    monkeypatch.setenv("VOOL_HONESTY_RECEIPTS", "1")
    _profile(tmp_path)
    chat = "quoted-claim-receipt"
    raw = emit_turn_honesty_receipt({"response": reply}, user_input=prompt, session_id=chat)
    assert raw["honesty_receipt"]["signed"]
    assert raw["honesty_receipt"]["verdict"] == VERDICT_UNBACKED
    final = guard(chat, prompt, reply)
    assert final["response"] != reply
    sealed = emit_turn_honesty_receipt(final, user_input=prompt, session_id=chat)
    assert sealed["honesty_receipt"]["signed"]
    assert sealed["honesty_receipt"]["verdict"] == VERDICT_BLOCKED


@pytest.mark.parametrize("attack", ["plain", "dict", "serialized", "appended", "session", "prompt", "turn"])
def test_no_match_authority_cannot_be_forged_or_reused(tmp_path, attack):
    profile = _profile(tmp_path)
    chat, prompt = "no-match-binding", "forget stored memory"
    _ingest(profile, chat, [(1760000000, "My locker key hangs on the blue hook.")])
    handled, reply = command(profile, chat, prompt)
    assert handled and reply.startswith("Nothing was removed")
    result = {"response": str(reply), "mode": "advice_only", "_memory_command_outcome": reply}
    context = {"chat_id": chat}
    if attack == "plain":
        result.pop("_memory_command_outcome")
        result["deterministic"] = True
    elif attack == "dict":
        result["_memory_command_outcome"] = {"response": str(reply), "request": prompt, "session": chat}
    elif attack == "serialized":
        result = json.loads(json.dumps(result))
    elif attack == "appended":
        result["response"] += " I created files."
    elif attack == "session":
        chat = "different-chat"
    elif attack == "prompt":
        prompt = "What happened?"
    elif attack == "turn":
        context["client_turn_id"] = "different-turn"
    final = enforce_final_action_honesty(result, user_input=prompt, session_id=chat, source_context=context)
    assert final["action_honesty_validator"]["applied"], final
    assert final["response"] != result["response"]


@pytest.mark.parametrize("target", ["stored memory", "created files", "audit in progress"])
def test_memory_fast_path_seal_and_delivery_guard_keep_the_command_result(tmp_path, monkeypatch, target):
    from core.agent_runtime.agent import VoolAgent, _seal_semantic_result
    from core.agent_runtime.memory_runtime import maybe_handle_memory_fast_path
    from core.honesty_receipt import VERDICT_NO_CLAIM
    from core.memory.entries import resolve_memory_access_policy
    from core.persistent_memory import maybe_handle_memory_command

    monkeypatch.setenv("VOOL_HONESTY_RECEIPTS", "1")
    profile = _profile(tmp_path)
    chat, prompt = "served-forget-result", "forget " + target
    _ingest(profile, chat, [(1760000000, "My locker key hangs on the blue hook.")])
    context = {"chat_id": chat, "runtime_home": str(profile), "client_turn_id": "forget-turn"}
    agent = VoolAgent(backend_name="test-backend", device="command-result-test", persona_id="default")
    result = maybe_handle_memory_fast_path(
        agent, prompt, session_id=chat, source_context=context,
        access_policy=resolve_memory_access_policy(chat_id=chat),
        maybe_handle_memory_command_fn=maybe_handle_memory_command,
    )
    assert result and result["response"].startswith("Nothing was removed"), result
    sealed = _seal_semantic_result(result, session_id=chat, user_input=prompt, source_context=context)
    assert sealed["response"] == result["response"], sealed
    assert sealed["honesty_receipt"]["signed"]
    assert sealed["honesty_receipt"]["verdict"] == VERDICT_NO_CLAIM
    # The API checks canonical plain text again after the semantic seal.
    final = enforce_final_action_honesty(
        {**sealed, "response": str(sealed["response"])}, user_input=prompt,
        session_id=chat, source_context=context,
    )
    assert final["response"] == result["response"], final
    assert not final.get("action_honesty_validator", {}).get("applied"), final


def test_effective_command_input_reaches_the_same_receipt_predicate(tmp_path, monkeypatch):
    from core.agent_runtime.agent import _seal_semantic_result
    from core.honesty_receipt import VERDICT_BLOCKED

    monkeypatch.setenv("VOOL_HONESTY_RECEIPTS", "1")
    profile = _profile(tmp_path)
    chat, prompt = "effective-command", "forget stored memory"
    _ingest(profile, chat, [(1760000000, "My locker key hangs on the blue hook.")])
    handled, reply = command(profile, chat, prompt)
    assert handled
    sealed = _seal_semantic_result(
        {"response": reply, "mode": "advice_only"}, session_id=chat,
        user_input="please continue", effective_input=prompt, source_context={"chat_id": chat},
    )
    assert sealed["response"] != reply, sealed
    assert sealed["honesty_receipt"]["verdict"] == VERDICT_BLOCKED
