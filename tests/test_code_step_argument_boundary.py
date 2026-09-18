"""Malformed inner calls must be correctable before approvals or journal allocation."""
from types import SimpleNamespace

import pytest

from tests.test_code_assistant_task_runtime import _ctx, _door
from tests.test_code_assistant_task_runtime import fixture_repo as fixture_repo


@pytest.mark.parametrize('inner', [{}, {'path': None}, {'path': ''}, {'path': 12}, []])
def test_missing_file_target_is_correctable_without_poisoning_step(fixture_repo, inner):
    ctx = _ctx(fixture_repo)
    task = _door('code.task.open', {'objective': 'Repair the failing calculation'}, ctx).details['task_id']
    args = {'task_id': task, 'step_id': 'read-source', 'intent': 'workspace.read_file', 'arguments': inner}
    refused = _door('code.task.step', args, ctx)
    assert refused.status == 'invalid_arguments'
    assert 'path' in refused.response_text or 'object' in refused.response_text
    corrected = _door('code.task.step', {**args, 'arguments': {'path': 'calc.py'}}, ctx)
    assert corrected.ok, corrected.response_text
    assert corrected.details['executed']


def test_malformed_step_does_not_request_manual_approval(fixture_repo):
    from core.hive_activity_tracker import HiveActivityTracker, HiveActivityTrackerConfig
    from core.tool_intent_executor import execute_tool_intent

    ctx = _ctx(fixture_repo, operating_mode='manual')
    task = _door('code.task.open', {'objective': 'Repair calculation'}, ctx).details['task_id']
    result = execute_tool_intent(
        {'intent': 'code.task.step', 'arguments': {'task_id': task, 'intent': 'workspace.read_file', 'arguments': {}}},
        task_id='turn-boundary', session_id=ctx['session_id'], source_context=ctx,
        hive_activity_tracker=HiveActivityTracker(HiveActivityTrackerConfig(enabled=False)),
    )
    assert result.status == 'invalid_arguments'
    assert not result.details.get('approval_request')
    assert 'path' in result.response_text


def test_nested_approval_names_actual_operation_and_file(fixture_repo):
    from core.mode_permission_policy import PermissionEffect, decide_tool_call

    result = decide_tool_call(intent='code.task.step', arguments={
        'task_id': 'ct-preview', 'intent': 'workspace.write_file',
        'arguments': {'path': 'calc.py', 'content': 'new contents\n'},
    }, task_id='turn-preview', source_context=_ctx(fixture_repo, operating_mode='manual'))
    assert result.effect is PermissionEffect.REQUIRE_APPROVAL
    request = result.approval_request
    assert 'workspace.write_file' in request['action']
    assert 'calc.py' in request['affected_resources']
    assert '+new contents' in request['diff_preview']


def test_real_tool_loop_returns_argument_feedback_to_the_model():
    from tests.test_a_narrated_batch_does_not_vanish import _drive

    _, router = _drive([SimpleNamespace(intent='code.task.step', name='code.task.step', call_id='boundary',
        arguments={'task_id': 'ct-argument-repair', 'intent': 'workspace.read_file', 'arguments': {}})])
    assert router.calls == 2, 'A malformed call must reach the correction round, not end at an approval.'


@pytest.mark.parametrize(('intent', 'arguments', 'valid'), [
    ('workspace.read_file', {'file_path': 'calc.py'}, True),
    ('workspace.read_file', {'path': 'calc.py', 'max_lines': None}, True),
    ('workspace.read_file', {'path': 'calc.py', 'max_lines': True}, False),
    ('workspace.write_file', {'path': 'empty.txt', 'content': ''}, True),
    ('workspace.write_file', {'path': 'empty.txt'}, False),
    ('workspace.run_tests', {}, True),
    ('workspace.list_tree', {}, True),
    ('workspace.read_file', {'path': 'calc.py', '_trusted_local_only': True}, False),
])
def test_contract_validation_preserves_legitimate_defaults_and_aliases(intent, arguments, valid):
    from core.code_assistant.step_validation import step_argument_error

    error = step_argument_error({'intent': intent, 'arguments': arguments})
    assert bool(error) is not valid, error


def test_step_permissions_inherit_actual_inner_action(tmp_path):
    from core.mode_permission_policy import PermissionEffect, decide_tool_call

    ctx = {"session_id": "step-actual-mode", "operating_mode": "manual", "workspace_root": str(tmp_path)}
    for inner, arguments, effect in (
        ("workspace.read_file", {"path": "source.py"}, PermissionEffect.ALLOW),
        ("workspace.write_file", {"path": "source.py", "content": "replacement"}, PermissionEffect.REQUIRE_APPROVAL),
        ("workspace.run_tests", {"command": "python -m pytest"}, PermissionEffect.REQUIRE_APPROVAL),
        ("code.task.step", {}, PermissionEffect.REQUIRE_APPROVAL),
        ("unknown.tool", {}, PermissionEffect.REQUIRE_APPROVAL),
    ):
        decision = decide_tool_call(intent="code.task.step", arguments={"task_id": "ct-gate", "intent": inner,
            "arguments": arguments}, task_id="outer", source_context=ctx)
        assert decision.effect is effect, (inner, decision)
