"""An approved relative path is bound to its workspace and tool, not every copy."""
import hashlib
from pathlib import Path

import pytest

from core.mode_permission_policy import PermissionEffect, decide_tool_call, reset_mode_permission_state
from tests.repoops._harness import BUGGY, FIXED, context, door
from tests.repoops.test_forge_actions import world


def _approved_repair(world, ctx, path):
    root = Path(ctx['workspace_root'])
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(BUGGY)
    # Real control-plane open/read/reproduce/identify/propose/approve. No forged
    # journal rows or direct approved=True injection.
    opened = door('code.task.open', {'objective': f'Repair the arithmetic defect in {path}'}, ctx)
    assert opened.ok, opened.response_text
    tid = opened.details['task_id']
    read = door('code.task.step', {'task_id': tid, 'step_id': 'read', 'intent': 'workspace.read_file', 'arguments': {'path': path}}, ctx)
    assert read.ok, read.response_text
    # The selected owner is exercised directly by a deterministic command. A
    # different file with the same basename is not the reproduction target.
    command = "python -c " + __import__('shlex').quote(f"import runpy; assert runpy.run_path({path!r})['add'](2,3)==5")
    repro = door('code.task.step', {'task_id': tid, 'step_id': 'repro', 'intent': 'workspace.run_tests', 'arguments': {'command': command}}, ctx)
    assert repro.details['tool_result']['success'] is False
    identified = door('code.task.identify', {'task_id': tid, 'path': path, 'line': 2, 'reason': 'add subtracts instead of adding'}, ctx)
    assert identified.ok, identified.response_text
    args = {'path': path, 'content': FIXED, 'expected_hash': hashlib.sha256(target.read_bytes()).hexdigest()}
    proposed = door('code.task.propose', {'task_id': tid, 'proposal_id': 'reviewed', 'intent': 'workspace.write_file', 'arguments': args, 'rationale': f'{path}: repair the reproduced subtraction defect'}, ctx)
    assert proposed.ok, proposed.response_text
    assert door('code.task.approve', {'task_id': tid, 'proposal_id': 'reviewed'}, ctx).ok
    return tid, args


@pytest.mark.parametrize('destination', ['another-workspace', 'machine-desktop'])
def test_approved_replacement_cannot_migrate_to_another_destination(world, tmp_path, monkeypatch, destination):
    root, _, _ = world
    ctx = context(root, session='review-approved-replacement')
    relative = 'calc.py' if destination == 'another-workspace' else 'Desktop/calc.py'
    tid, args = _approved_repair(world, ctx, relative)
    approved = decide_tool_call(intent='workspace.write_file', arguments=args, task_id=tid, source_context=ctx)
    assert approved.effect is PermissionEffect.ALLOW, approved
    if destination == 'another-workspace':
        other = tmp_path / 'other-workspace'
        other.mkdir()
        (other / relative).write_text(BUGGY)
        switched = {**ctx, 'workspace_root': str(other), 'workspace': str(other)}
        tool = 'workspace.write_file'
    else:
        home = tmp_path / 'disposable-home'
        (home / 'Desktop').mkdir(parents=True)
        (home / relative).write_text(BUGGY)
        monkeypatch.setenv('HOME', str(home))
        switched = ctx
        tool = 'machine.write_file'
    result = decide_tool_call(intent=tool, arguments=args, task_id=tid, source_context=switched)
    assert result.effect is PermissionEffect.REQUIRE_APPROVAL, (destination, result)
