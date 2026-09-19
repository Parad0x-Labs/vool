"""Independent r5 destination identity probes; real proposals, permission classification only.
No unapproved write, native acceptance or external effect is performed.
"""
from pathlib import Path

import pytest

from core.mode_permission_policy import PermissionEffect
from tests.repoops._harness import context
from tests.repoops.test_forge_actions import world
from tests.test_approval_destination_binding_r5 import BUGGY_MONEY, _approved, _decide, _step


@pytest.mark.parametrize('kind',['file-link','directory-link'])
def test_approved_path_cannot_follow_a_retargeted_link(world,kind):
    root, _bare, _forge=world
    original=root/'original';other=root/'other'
    original.mkdir();other.mkdir()
    (original/'money.py').write_text(BUGGY_MONEY)
    (other/'money.py').write_text(BUGGY_MONEY)
    if kind=='file-link':
        path='selected.py';link=root/path;link.symlink_to(original/'money.py')
    else:
        path='selected/money.py';link=root/'selected';link.symlink_to(original,target_is_directory=True)
    ctx=context(root,session='retarget-'+kind)
    tid,args=_approved(ctx,path=path)
    assert _decide('code.task.step',_step(tid,args),ctx) is PermissionEffect.ALLOW
    # Same lexical path, same bytes, same task; physical identity changed after approval.
    link.unlink();link.symlink_to(other/'money.py' if kind=='file-link' else other,target_is_directory=kind=='directory-link')
    from core.code_assistant.task_runtime import code_task_runtime
    code_task_runtime().reset()
    effect=_decide('code.task.step',_step(tid,args),ctx)
    assert effect is PermissionEffect.REQUIRE_APPROVAL, ('Approval migrated to a different physical target after symlink retarget/restart',kind,effect)
    assert (original/'money.py').read_text()==BUGGY_MONEY
    assert (other/'money.py').read_text()==BUGGY_MONEY

@pytest.mark.parametrize('path',['notes.txt','reports/status.md'])
def test_existence_gate_uses_the_same_context_root_as_the_writer(world,tmp_path,path):
    root,_bare,_forge=world
    actual=root/path;actual.parent.mkdir(parents=True,exist_ok=True);actual.write_text('existing owner content\n')
    unrelated=tmp_path/'empty-context-root';unrelated.mkdir()
    ctx=context(root,session='root-conflict')
    ctx['workspace']=str(root);ctx['workspace_root']=str(unrelated)
    from core.runtime_execution_tools import _resolve_workspace_path, _workspace_root
    assert _resolve_workspace_path(path,workspace_root=_workspace_root(ctx))==actual.resolve()
    result=_decide('workspace.write_file',{'path':path,'content':'unapproved replacement\n'},ctx)
    assert result is PermissionEffect.REQUIRE_APPROVAL, ('Existence check used the empty alternate root and allowed overwrite of the writer target',path,result)
    assert actual.read_text()=='existing owner content\n'
