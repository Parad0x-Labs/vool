"""Task state and real file bytes must reach the model, not just success labels."""
import json
from types import SimpleNamespace

from tests.test_code_assistant_task_runtime import _ctx, _door
from tests.test_code_assistant_task_runtime import fixture_repo as fixture_repo


def _observe(execution, name):
    from core.agent_runtime.response_policy_tool_history import tool_history_observation_payload
    return tool_history_observation_payload(execution=execution, tool_name=name)


def test_open_task_exposes_its_stage_and_plan(fixture_repo):
    opened = _door('code.task.open', {'objective': 'Repair the calculation'}, _ctx(fixture_repo))
    observed = _observe(opened, 'code.task.open')
    assert observed.get('task_id') == opened.details['task_id']
    assert observed.get('stage') == 'reproduce'
    assert observed.get('plan') == opened.details['plan']


def test_inner_file_bytes_and_hash_reach_the_model(fixture_repo):
    ctx = _ctx(fixture_repo)
    task = _door('code.task.open', {'objective': 'Repair calculation'}, ctx).details['task_id']
    read = _door('code.task.step', {'task_id': task, 'intent': 'workspace.read_file', 'arguments': {'path': 'calc.py'}}, ctx)
    assert read.ok
    observed = _observe(read, 'code.task.step')
    assert 'return a - b' in json.dumps(observed)
    assert observed['tool_result']['hash'] == read.details['tool_result']['hash']


def test_nested_command_uses_existing_output_budget():
    execution=SimpleNamespace(ok=True,status='ok',response_text='workspace.run_tests ok',details={
        'task_id':'ct-bounded','stage':'identify','intent':'workspace.run_tests',
        'tool_result':{'returncode':1,'stdout':'first marker\n'+'x\n'*2000+'last marker','stderr':'failed assert'},
    })
    observed = _observe(execution, 'code.task.step')
    inner=observed.get('tool_result',{})
    assert inner.get('returncode') == 1
    assert 'first marker' in inner.get('stdout_excerpt','')
    assert 'last marker' in inner.get('stdout_excerpt','')
    assert len(inner['stdout_excerpt']) < 3000
