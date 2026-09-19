"""Exercise the displayed budget form and its existing approval door without live spend."""
import json
from pathlib import Path

import pytest

from tests.chat_page_js_harness import DOM, run_node

SOURCE = Path('core/vool_settings_page.py').read_text()
WIDGET = SOURCE[SOURCE.index('const USEPOD_DISCOVERY_URL'):SOURCE.index('/* One model row:')]


@pytest.mark.parametrize('decision', ['allow', 'deny', 'form'])
def test_focused_budget_has_one_visible_approval_and_no_dispatch(decision):
    result = run_node(DOM + r'''
const state={focusPart:'spend'};
const $=s=>document.getElementById(s.slice(1));
const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!=null)n.textContent=text;return n;};
const d={lane:{transport_mode:'prepaid_token'},credential:{configured:true},authorities:{},
 spend_approval:{state:'pending',approval_id:'approval-fixture',facts:{account:'fixture-account',asset:'USDC',network:'usepod:account',unit:'usdc_microunit',models:['demo'],per_call_atomic:3000000,max_total_atomic:3000000}}};
const getJSON=async()=>JSON.parse(JSON.stringify(d));
const posts=[];
fetch=async(url,options)=>{
 const body=JSON.parse(options.body);posts.push({url,body});
 if(url==='/api/mode')d.spend_approval.state=body.decision==='allow'?'approved':'denied';
 if(url.endsWith('/confirm')){d.spend_approval.state='minted';d.spend_approval.grant={grant_id:'fixture-grant',state:'active'};}
 return {ok:true,status:200,json:async()=>({ok:true,grant_id:'fixture-grant'})};
};
const stack=el('div');
const settle=async()=>{for(let i=0;i<5;i++)await new Promise(r=>setImmediate(r));};
const all=n=>[n,...n.children.flatMap(all)];
const find=cls=>all(stack).find(n=>String(n.className||'').split(' ').includes(cls));
''' + WIDGET + '\nconst decision=' + json.dumps(decision) + ';\n' + r'''
if(decision==='form')d.spend_approval=null;
widgetUsePod(stack);await settle();
const hasPicker=!!find('model-picker');
if(decision==='form'){find('usepod-spend-percall').value='3';find('usepod-budget-mode').value='daily';}
const button=find(decision==='form'?'usepod-spend-propose':decision==='allow'?'usepod-spend-allow':'usepod-spend-deny');
const label=button.textContent;
const first=button.__on.click(), duplicate=decision==='form'?Promise.resolve():button.__on.click();
await Promise.all([first,duplicate]);await settle();
out({label,posts,hasPicker});
''')
    assert 'posts' in result, result
    assert not result['hasPicker']
    if decision=='form':
        assert len(result['posts'])==1
        assert result['posts'][0]['url'].endswith('/spend-approval/propose')
        assert result['posts'][0]['body']['per_call_atomic']==3000000
        assert result['posts'][0]['body']['max_total_atomic']==3000000
        assert result['posts'][0]['body']['budget_mode']=='daily'
        assert result['posts'][0]['body']['expiry_epoch']==0
        return
    assert result['posts'][0]['url']=='/api/mode'
    assert result['posts'][0]['body']['decision']==decision
    assert len(result['posts']) == (2 if decision=='allow' else 1)
    if decision=='allow':
        assert result['label']=='Approve one paid call'
        assert result['posts'][1]['url'].endswith('/spend-approval/confirm')
        assert result['posts'][1]['body']=={'approval_id':'approval-fixture'}
    assert all('/chat' not in post['url'] for post in result['posts'])


def test_normal_units_convert_exactly_and_invalid_amounts_never_round_into_authority():
    result=run_node(DOM + WIDGET + r'''
out({usdc:usepodAtomicAmount('3','USDC'),sol:usepodAtomicAmount('0.01','SOL'),
 tiny:usepodAtomicAmount('0.000001','USDC'),excess:usepodAtomicAmount('0.0000001','USDC'),
 negative:usepodAtomicAmount('-1','USDC'),exponent:usepodAtomicAmount('1e6','USDC'),
 huge:usepodAtomicAmount('9007199254.740992','USDC'),display:usepodDisplayAmount(3000000,'USDC')});
''')
    assert result['usdc']==3000000 and result['sol']==10000000 and result['tiny']==1
    assert all(result[k] is None for k in ('excess','negative','exponent','huge'))
    assert result['display']=='3 USDC'
