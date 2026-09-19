"""Independent coding-r2 review: real task door, disposable files and Node checks.
No model, native UI or live account acceptance is claimed.
"""
import hashlib

import pytest

from tests.test_code_task_model_surface import _ctx, _door, _journal, fixture_repo

CASES = [
    ('math.js', 'exports.add=(a,b)=>a-b;\n', 'exports.add=(a,b)=>a*b;\n', 'exports.add=(a,b)=>a+b;\n',
     "require('assert').strictEqual(require('./math.js').add(7,4),11);\n"),
    ('labels.js', "exports.label=x=>x.join(';');\n", "exports.label=x=>x.join('|');\n", "exports.label=x=>x.join(',');\n",
     "require('assert').strictEqual(require('./labels.js').label(['sky','port']),'sky,port');\n"),
]


def setup_task(root, case):
    path, bug, wrong, fixed, check = case
    (root/path).write_text(bug)
    (root/'review_check.js').write_text(check)
    ctx = _ctx(root, session='independent-v2-'+path)
    opened = _door('code.task.open', {'objective':'Repair '+path+' and run checks after each independent repair'},ctx)
    assert opened.ok
    tid = opened.details['task_id']
    def step(sid, intent, args):
        return _door('code.task.step',{'task_id':tid,'step_id':sid,'intent':intent,'arguments':args},ctx)
    red = step('reproduce','workspace.run_tests',{'command':'node review_check.js'})
    assert red.details['executed'] and red.details['tool_result']['success'] is False
    assert _door('code.task.identify',{'task_id':tid,'path':path,'reason':'Existing assertion proves the wrong operation'},ctx).ok
    assert step('read','workspace.read_file',{'path':path}).ok
    return ctx,tid,step


def propose(ctx,tid,root,path,content,pid):
    args={'path':path,'content':content,'expected_hash':hashlib.sha256((root/path).read_bytes()).hexdigest()}
    out=_door('code.task.propose',{'task_id':tid,'proposal_id':pid,'intent':'workspace.write_file','arguments':args,
        'rationale':'Owner '+path+': implement the operation required by the existing assertion'},ctx)
    assert out.ok,out.response_text
    return args


def approve(ctx,tid,pid):
    out=_door('code.task.approve',{'task_id':tid,'proposal_id':pid},ctx)
    assert out.ok,out.response_text


@pytest.mark.parametrize('case',CASES,ids=['arithmetic','labels'])
def test_new_repair_cannot_reuse_previous_failed_verification(fixture_repo,case):
    root=fixture_repo;path,bug,wrong,fixed,check=case
    ctx,tid,step=setup_task(root,case)
    first=propose(ctx,tid,root,path,wrong,'wrong-first');approve(ctx,tid,'wrong-first')
    assert step('first-mutation','workspace.write_file',first).ok
    failed=step('first-verification','workspace.run_tests',{'command':'node review_check.js'})
    assert failed.details['tool_result']['success'] is False
    assert _door('code.task.identify',{'task_id':tid,'path':path,'reason':'Failed check proves first repair wrong'},ctx).ok
    assert step('reread','workspace.read_file',{'path':path}).ok
    second=propose(ctx,tid,root,path,fixed,'corrected');approve(ctx,tid,'corrected')
    changed=step('second-mutation','workspace.write_file',second)
    assert changed.ok and (root/path).read_text()==fixed
    # The next repair cycle must first verify these NEW bytes; the old failure is history.
    from core.code_assistant.task_runtime import code_task_runtime
    code_task_runtime().reset()
    bypass=_door('code.task.identify',{'task_id':tid,'path':path,'reason':'Try yet another repair without checking current bytes'},ctx)
    assert not bypass.ok, ('Stale failure reopened review after a new mutation, without new verification',bypass.status,bypass.details)
    report=_door('code.task.report',{'task_id':tid},ctx)
    assert not report.details.get('verification_failed'),report.details
    assert len(_journal(root,tid)['verifications'])==1  # historical failure must remain


@pytest.mark.parametrize('case',CASES,ids=['arithmetic','labels'])
def test_independent_pending_repair_does_not_block_validation_checkpoint(fixture_repo,case):
    root=fixture_repo;path,bug,wrong,fixed,check=case
    ctx,tid,step=setup_task(root,case)
    other='independent.js';(root/other).write_text('exports.value=0;\n')
    assert step('read-independent','workspace.read_file',{'path':other}).ok
    first=propose(ctx,tid,root,path,fixed,'first-independent')
    second=propose(ctx,tid,root,other,'exports.value=1;\n','second-independent')
    approve(ctx,tid,'first-independent');approve(ctx,tid,'second-independent')
    assert step('first-mutation','workspace.write_file',first).ok
    # There is no dependency between these files; a batch of approvals does not make them atomic.
    checkpoint=step('between-repairs','workspace.run_tests',{'command':'node review_check.js'})
    assert checkpoint.details.get('executed'), ('Validation blocked merely because another approved repair remains',checkpoint.status,checkpoint.response_text)
    assert (root/other).read_text()=='exports.value=0;\n'
    assert _journal(root,tid)['proposals']['second-independent']['consumed_by']==''


@pytest.mark.parametrize('case',CASES,ids=['arithmetic','labels'])
def test_approved_repair_cannot_replace_its_reviewed_base_hash(fixture_repo,case):
    root=fixture_repo;path,bug,wrong,fixed,check=case
    ctx,tid,step=setup_task(root,case)
    args=propose(ctx,tid,root,path,fixed,'reviewed');approve(ctx,tid,'reviewed')
    # An independent editor writes after approval. A fresh hash is not fresh operator consent.
    external=bug+'// independent owner change after approval\n';(root/path).write_text(external)
    changed_args={**args,'expected_hash':hashlib.sha256(external.encode()).hexdigest()}
    result=step('repointed-base','workspace.write_file',changed_args)
    assert not result.ok and (root/path).read_text()==external, ('Approval silently retargeted to a different base and erased intervening work',result.status,(root/path).read_text())


@pytest.mark.parametrize('case',CASES,ids=['arithmetic','labels'])
def test_revised_proposal_id_cannot_silently_replay_old_content(fixture_repo,case):
    root=fixture_repo;path,bug,wrong,fixed,check=case
    ctx,tid,step=setup_task(root,case)
    old=propose(ctx,tid,root,path,wrong,'repair')
    revised={**old,'content':fixed}
    out=_door('code.task.propose',{'task_id':tid,'proposal_id':'repair','intent':'workspace.write_file',
        'arguments':revised,'rationale':'Correct the first proposal before approval'},ctx)
    assert not out.ok, ('Same ID with different mutation reported successful replay of the obsolete proposal',out.status,out.details)
    # A replay with exactly the original payload remains valid.
    exact=_door('code.task.propose',{'task_id':tid,'proposal_id':'repair','intent':'workspace.write_file',
        'arguments':old,'rationale':'Owner '+path+': implement the operation required by the existing assertion'},ctx)
    assert exact.ok and exact.details.get('replayed')
