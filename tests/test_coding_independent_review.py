"""Independent repair reach/recovery checks; real disposable files and command door.

No model or native acceptance claim. The folder tests call the owning fast path;
the repair tests execute the production task door, real node suites and real writes.
"""
import hashlib
from types import SimpleNamespace

import pytest

from tests.test_code_task_model_surface import fixture_repo, _ctx, _door


@pytest.mark.parametrize('user_text',[
    'Debug the failing tests in this project and repair the root cause.',
    'Fix the bug in this project and explain the change.',
    'Repair the broken behavior in this project and describe the changes.',
])
def test_repair_requests_are_not_consumed_by_folder_overview(tmp_path,user_text):
    from core.agent_runtime.fast_paths_utility import maybe_handle_folder_overview_request
    from core.tool_demand_signals import resolve_demand_signals
    demand=resolve_demand_signals(user_text)
    assert 'code.task.open' in demand.explicit_intents, demand
    agent=SimpleNamespace(_fast_path_result=lambda **kw:kw)
    answer=maybe_handle_folder_overview_request(agent,user_text,session_id='review-reach',
        source_surface='api',source_context={'surface':'api','workspace':str(tmp_path)})
    assert answer is None, 'A repair demand already recognized by tool seating was answered as a folder overview'


@pytest.mark.parametrize('stage',['narrow_test','cumulative'])
@pytest.mark.parametrize('variant',['original','novel'])
def test_failed_verification_can_return_to_reviewed_repair(fixture_repo,stage,variant):
    ctx=_ctx(fixture_repo,session='recovery-'+stage+'-'+variant)
    if variant=='original':
        path='math.js';bug='exports.add = (a,b) => a-b;\n';wrong='exports.add = (a,b) => a*b;\n';fixed='exports.add = (a,b) => a+b;\n'
        check="const {add}=require('./math.js');require('assert').strictEqual(add(7,4),11);\n"
    else:
        path='labels.js';bug="exports.label = x => x.join(';');\n";wrong="exports.label = x => x.join('|');\n";fixed="exports.label = x => x.join(',');\n"
        check="const {label}=require('./labels.js');require('assert').strictEqual(label(['sky','port']),'sky,port');\n"
    (fixture_repo/path).write_text(bug)
    (fixture_repo/'review_check.js').write_text(check)
    (fixture_repo/'review_narrow.js').write_text("require('assert').strictEqual(2+3,5);\n")
    before_tests={name:(fixture_repo/name).read_bytes() for name in ['review_check.js','review_narrow.js']}
    opened=_door('code.task.open',{'objective':'Repair '+path+' and validate the existing suites'},ctx)
    assert opened.ok
    task=opened.details['task_id']
    def step(sid,intent,args):
        return _door('code.task.step',{'task_id':task,'step_id':sid,'intent':intent,'arguments':args},ctx)
    repro=step('red','workspace.run_tests',{'command':'node review_check.js'})
    assert repro.details['executed'] and repro.details['tool_result']['success'] is False
    assert repro.details['stage']=='identify'
    assert _door('code.task.identify',{'task_id':task,'path':path,'reason':'The output differs from the existing assertion'},ctx).ok
    assert step('read','workspace.read_file',{'path':path}).ok
    args={'path':path,'content':wrong,'expected_hash':hashlib.sha256(bug.encode()).hexdigest()}
    proposed=_door('code.task.propose',{'task_id':task,'proposal_id':'first','intent':'workspace.write_file',
        'arguments':args,'rationale':'Owner '+path+': correct the operator producing the failing output'},ctx)
    assert proposed.ok,proposed.response_text
    assert _door('code.task.approve',{'task_id':task,'proposal_id':'first'},ctx).ok
    mutation=step('write-first','workspace.write_file',args)
    assert mutation.ok and mutation.details['executed'],mutation.response_text
    assert (fixture_repo/path).read_text()==wrong
    if stage=='cumulative':
        narrow=step('narrow','workspace.run_tests',{'command':'node review_narrow.js'})
        assert narrow.details['tool_result']['success'] is True and narrow.details['stage']=='cumulative'
    failed=step('failed-verification','workspace.run_tests',{'command':'node review_check.js'})
    assert failed.details['executed'] and failed.details['tool_result']['success'] is False
    assert failed.details['stage']==stage
    report=_door('code.task.report',{'task_id':task},ctx)
    assert report.details['verdict']=='unresolved'
    # A genuinely new diagnosis and proposal must have a supported continuation in this task.
    diagnosis=_door('code.task.identify',{'task_id':task,'path':path,'reason':'The first fix still fails; use the assertion-required operation'},ctx)
    assert step('reread','workspace.read_file',{'path':path}).ok
    revised=_door('code.task.propose',{'task_id':task,'proposal_id':'second','intent':'workspace.write_file',
        'arguments':{'path':path,'content':fixed,'expected_hash':hashlib.sha256(wrong.encode()).hexdigest()},
        'rationale':'Owner '+path+': the first operator remains wrong; apply the operation established by the failed check'},ctx)
    assert all((fixture_repo/name).read_bytes()==value for name,value in before_tests.items())
    assert (fixture_repo/path).read_text()==wrong, 'A new proposal must remain review-only'
    assert revised.ok, (diagnosis.status,diagnosis.response_text,revised.status,revised.response_text)


def test_genuine_folder_overview_is_preserved(tmp_path):
    from core.agent_runtime.fast_paths_utility import maybe_handle_folder_overview_request
    agent=SimpleNamespace(_fast_path_result=lambda **kw:kw)
    assert maybe_handle_folder_overview_request(agent,'What is this project about?',
        session_id='overview-control',source_surface='api',
        source_context={'surface':'api','workspace':str(tmp_path)}) is not None
