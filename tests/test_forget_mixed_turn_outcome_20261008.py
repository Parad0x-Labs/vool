"""Real in-process turn front door on disposable profiles; no model or live app."""
import pytest

from tests.test_question_date_time_leg_20261002 import _hash_backend, _ingest, _profile


@pytest.mark.usefixtures('_hash_backend')
@pytest.mark.parametrize('with_client_turn', [False, True])
def test_mixed_profile_run_once_keeps_executor_reply(tmp_path, with_client_turn):
    prompt = "Forget my name. Forget stored memory."
    from core.agent_runtime.agent import VoolAgent

    profile = _profile(tmp_path)
    chat = 'real-frontdoor-forget'
    _ingest(profile, chat, [(1760000000, 'My locker key hangs on the blue hook.')])
    context = {'chat_id': chat, 'runtime_home': str(profile)}
    if with_client_turn:
        context['client_turn_id'] = 'review-turn'
    agent = VoolAgent(backend_name='test-backend', device='review-profile', persona_id='default')
    result = agent.run_once(prompt, session_id_override=chat, source_context=context)
    assert 'Forget found no memory in this chat that matched "stored memory".' in result['response'], result
    assert not result.get('action_honesty_validator', {}).get('applied'), result


@pytest.mark.usefixtures("_hash_backend")
def test_mixed_profile_keeps_original_argument_bytes(tmp_path):
    from core.agent_runtime.agent import VoolAgent

    profile = _profile(tmp_path)
    chat = "mixed-lossless-argument"
    prompt = "Forget   my name.  Forget storage  label   MINT-84."
    _ingest(profile, chat, [(1760000000, "My locker key hangs on the blue hook.")])
    context = {"chat_id": chat, "runtime_home": str(profile)}
    agent = VoolAgent(backend_name="test-backend", device="lossless-profile", persona_id="default")
    result = agent.run_once(prompt, session_id_override=chat, source_context=context)
    assert 'matched "storage label MINT-84".' in result["response"], result
    from core.memory.command_result import verified_forget_outcome

    outcome = verified_forget_outcome(
        result, user_input=prompt, session_id=chat, source_context=context,
    )
    assert outcome is not None, result
    assert outcome.request == prompt
    assert outcome.command == "Forget storage  label   MINT-84."
    assert outcome.argument == "storage  label   MINT-84."
    assert outcome.identity[1] and outcome.identity[2]
