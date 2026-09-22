"""Independent checks; imports the delivered controls unchanged. No network required."""
import pytest

from tests.test_code_task_purposeful_verification import *  # noqa: F403 — inherits the delivered suite's fixtures/helpers verbatim
from tests.test_code_task_purposeful_verification import CHECK, Task, _door, _journal, _wrong_fix_task


@pytest.mark.parametrize('intent,command', [
    ('sandbox.run_command', 'cat calc.py'),
    ('sandbox.run_command', 'python3 -c "print(123)"'),
    ('workspace.run_tests', 'echo ok'),
])
def test_irrelevant_checks_cannot_retire_a_known_wrong_repair(project, intent, command):
    task = _wrong_fix_task(project, 'irrelevant-review')
    outcome = task.step(intent, {'command': command})
    assert outcome.details['executed'] is True, outcome.status
    assert _journal(task.id)['stage'] == 'narrow_test', _journal(task.id)['stage']
    assert _journal(task.id)['narrow'] is None


def test_named_check_under_cwd_remains_bound_after_green(project):
    task = Task(project, 'cwd-binding-review')
    checks = project / 'checks'
    checks.mkdir()
    (checks / 'check.py').write_text("import sys\nsys.path.insert(0, '..')\n" + CHECK)
    args = {'command': 'python3 check.py', 'cwd': 'checks'}
    assert task.step('workspace.run_tests', args).details['stage'] == 'cumulative'
    # Task's reproduced failure was the root check.py. The check under checks/
    # is a separate narrow check; cumulative acceptance still owes the original.
    assert task.step('workspace.run_tests', {'command': 'python3 check.py'}).details['stage'] == 'inspect_diff'
    assert task.step('workspace.git_diff', {}).ok
    assert _door('code.task.report', {'task_id': task.id}, task.ctx).details['verdict'] == 'completed'
    (checks / 'check.py').write_text('raise AssertionError("changed check")\n')
    report = _door('code.task.report', {'task_id': task.id}, task.ctx).details
    assert report['verdict'] == 'unresolved', report
    assert 'checks/check.py' in report['bytes_changed']


def test_selection_flags_with_values_are_not_collapsed():
    from core.code_assistant.task_runtime import _command_selection
    assert _command_selection('pytest tests/test_calc.py -k=first') != _command_selection(
        'pytest tests/test_calc.py -k=second')
