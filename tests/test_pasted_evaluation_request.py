from pathlib import Path

from core.active_mission import extract_active_mission_slots
from core.agent_runtime.demand_ownership import execution_unit_spans
from core.inline_payload import turn_supplies_its_own_content

PROMPT = (Path(__file__).parent / 'fixtures/pasted_reasoning_evaluation.txt').read_text()


def test_tool_prohibition_is_not_a_vocabulary_ban():
    slots = extract_active_mission_slots(PROMPT)
    assert not [s for s in slots if s.slot_name.startswith('forbidden_term:')]


def test_the_pasted_evaluation_is_one_execution_with_its_full_context():
    assert turn_supplies_its_own_content(PROMPT)
    spans = execution_unit_spans(PROMPT)
    assert len(spans) == 1
    assert spans[0].text == PROMPT.strip()


def test_action_rules_and_quoted_plugin_text_do_not_create_word_bans():
    text = 'Do not write files. Never use plugins. Explain the string "never say hello".'
    assert not [s for s in extract_active_mission_slots(text) if s.slot_name.startswith('forbidden_term:')]
    assert [s.value_raw for s in extract_active_mission_slots("Do not use the word 'tools'.")] == ['tools']
    assert [s.value_raw for s in extract_active_mission_slots('Never mention Web3.')] == ['Web3']


def test_previously_saved_action_ban_is_reinterpreted_without_erasing_real_rules():
    import uuid

    from core.active_mission import ActiveMissionSlot, current_active_mission_slots, upsert_active_mission_slots
    sid = 'evaluation-' + uuid.uuid4().hex
    upsert_active_mission_slots(sid, [ActiveMissionSlot('forbidden_term:tools', 'tools')], source_text='Do NOT use tools.')
    upsert_active_mission_slots(sid, [ActiveMissionSlot('forbidden_term:web3', 'Web3')], source_text='Never mention Web3.')
    slots = current_active_mission_slots(sid)
    assert [s['value'] for s in slots] == ['Web3']


def test_novel_tool_free_analysis_keeps_shared_material_and_requested_sections():
    prompt = 'Do not use tools. Review this snippet:\n```python\ndef f(x):\n    return x + 1\n```\nExplain the defect and compare two fixes. Return diagnosis and corrected code.'
    assert len(execution_unit_spans(prompt)) == 1
    assert execution_unit_spans(prompt)[0].text == prompt


def test_unquoted_external_requests_keep_their_execution_owners():
    prompt = 'Read a.txt and then tell me the weather in Oslo.'
    assert len(execution_unit_spans(prompt)) >= 2


def test_pricing_exercise_does_not_request_runtime_telemetry():
    from core.token_usage_receipt import token_usage_question
    assert not token_usage_question(PROMPT)
    assert not token_usage_question('Estimate the cost of 5000 input tokens at $1/M.')
    assert token_usage_question(PROMPT + "\nAlso report the token usage for this turn.")
