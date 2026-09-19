"""Execute the real picker handlers: one server-owned decision per paid selection."""
from __future__ import annotations

import json

import pytest

from core.price_safety_fragment import _GATE_JS
from tests.chat_page_js_harness import DOM, run_node, script


@pytest.mark.parametrize("provider,row_free,server_paid,allow", [
    ("usepod", False, True, True),
    ("openrouter", False, True, True),
    ("usepod", False, True, False),
    ("usepod", True, True, True),  # stale free label cannot bypass the server
    ("openrouter", True, False, True),
])
def test_row_selection_has_one_server_owned_confirmation(provider, row_free, server_paid, allow):
    config = json.dumps(dict(provider=provider, row_free=row_free, server_paid=server_paid, allow=allow))
    result = run_node(DOM + script() + "\nawait new Promise(r=>setImmediate(r));\nconst cfg=" + config + ";\n" + r"""
setDisplayedChat('openclaw:abababababababababab'); setModelValue('vool');
const decisions=[], posts=[];
window.VoolPriceGate={review:async ctx=>{decisions.push(ctx); return cfg.allow;}};
fetch=async (url,opts)=>{
  if (!opts || opts.method!=='POST') return {ok:true,json:async()=>({ok:true})};
  const body=JSON.parse(opts.body); posts.push(body);
  if(cfg.server_paid && !body.confirm_paid)
    return {ok:false,status:409,json:async()=>({code:'paid_model_confirm_required'})};
  return {ok:true,status:200,json:async()=>({ok:true,provider:cfg.provider,model:'demo-model'})};
};
const row=makeCloudRow({provider:cfg.provider,id:'demo-model',name:'Demo',free:cfg.row_free});
await row.__on.click({stopPropagation(){}});
out({decisions,posts,selected:modelValue,acked:paidPinAcked(modelValue)});
""")
    assert result["errors"] == []
    assert result["acked"] == (server_paid and allow)
    assert len(result["decisions"]) == int(server_paid)
    assert "confirm_paid" not in result["posts"][0]
    if server_paid:
        assert result["decisions"][0]["code"] == "paid_model_confirm_required"
    if server_paid and not allow:
        assert len(result["posts"]) == 1
        assert result["selected"] == "vool"
    else:
        assert len(result["posts"]) == (2 if server_paid else 1)
        if server_paid:
            assert result["posts"][1]["confirm_paid"] is True
        assert result["selected"] == ("usepod:" if provider == "usepod" else "") + "demo-model"


def test_stale_prices_refresh_before_the_single_confirmation_click():
    result = run_node(DOM + r"""
let releaseRefresh; const requests=[];
fetch=async url=>{
  requests.push(url);
  if(url.includes('/acceptances')) return {json:async()=>({acceptances:[]})};
  if(url.includes('refresh=1')) return await new Promise(r=>releaseRefresh=r);
  return {json:async()=>({provider:'usepod',age_seconds:1200,models:[{id:'demo',prompt_usd_per_m:1,completion_usd_per_m:2}]})};
};
""" + _GATE_JS + r"""
const decision=window.VoolPriceGate.review({kind:'pin',id:'usepod:demo',provider:'usepod'});
await new Promise(r=>setImmediate(r));
const overlay=document.body.children.find(x=>x.id==='vgOverlay');
const actions=overlay.querySelector('#vgActions');
const automatic=Boolean(releaseRefresh), disabledBefore=actions.children[0].disabled;
if(!automatic){out({automatic,disabledBefore});}else{
  releaseRefresh({json:async()=>({provider:'usepod',age_seconds:0,models:[{id:'demo',prompt_usd_per_m:3,completion_usd_per_m:4}]})});
  await new Promise(r=>setImmediate(r));
  const beforeClick=overlay.querySelector('#vgBody').innerHTML;
  const enabledAfter=!actions.children[0].disabled;
  actions.children[0].click();
  out({automatic,disabledBefore,enabledAfter,beforeClick,accepted:await decision,hidden:overlay.hidden,requests});
}
""")
    assert result["automatic"] is True
    assert result["disabledBefore"] is True
    assert result["enabledAfter"] is True
    assert "3.00 USDC" in result["beforeClick"] and "4.00 USDC" in result["beforeClick"]
    assert result["accepted"] is True and result["hidden"] is True
    assert sum("refresh=1" in url for url in result["requests"]) == 1


def test_conversation_ack_is_bounded_scoped_and_once_really_is_once():
    result = run_node(DOM + script() + "\nawait new Promise(r=>setImmediate(r));\n" + r"""
const a='openclaw:aaaaaaaaaaaaaaaaaaaa', b='openclaw:bbbbbbbbbbbbbbbbbbbb';
setDisplayedChat(a); setModelValue('usepod:astra');
acknowledgePaidPin(a,'usepod:astra','conversation');
consumePaidPinAck(a,'usepod:astra');
const retained=paidPinAcked('usepod:astra',a);
const otherChat=paidPinAcked('usepod:astra',b), otherProvider=paidPinAcked('openrouter:astra',a);
setModelValue('usepod:other');setModelValue('usepod:astra');
const afterSwitch=paidPinAcked('usepod:astra',a);
acknowledgePaidPin(a,'usepod:astra',true);
const onceBefore=paidPinAcked('usepod:astra',a);
consumePaidPinAck(a,'usepod:astra');const onceAfter=paidPinAcked('usepod:astra',a);
acknowledgePaidPin(a,'usepod:astra','conversation');
const now=Date.now;Date.now=()=>now()+86400001;  // conversation acks now last 24h
const expired=paidPinAcked('usepod:astra',a);
out({retained,otherChat,otherProvider,afterSwitch,onceBefore,onceAfter,expired});
""")
    assert result["retained"] and result["onceBefore"]
    assert not any(result[k] for k in ['otherChat','otherProvider','afterSwitch','onceAfter','expired'])


def test_price_toggle_only_repaints_matching_rows_without_catalogue_fetch():
    result = run_node(DOM + script() + "\nawait new Promise(r=>setImmediate(r));\n" + r"""
let paints=0, requests=0;fetch=async()=>{requests++;throw Error('unexpected fetch');};
const toolbar=buildCloudToolbar(()=>paints++);
const label=toolbar.children[toolbar.children.length-1];const checkbox=label.children[0];
checkbox.checked=true;checkbox.__on.change({stopPropagation(){}});
out({paints,requests,showPrices:cloudShowPrices});
""")
    assert result["paints"] == 1 and result["requests"] == 0 and result["showPrices"]


@pytest.mark.parametrize("kind", ["pin", "per-send"])
def test_gate_offers_a_conversation_decision_with_explicit_limits(kind):
    result = run_node(DOM + r"""
fetch=async url=>({json:async()=>url.includes('/acceptances')?{acceptances:[]}:
  {provider:'usepod',age_seconds:0,models:[{id:'demo',prompt_usd_per_m:1,completion_usd_per_m:6}]}});
""" + _GATE_JS + "\nconst kind=" + json.dumps(kind) + ";\n" + r"""
const decision=window.VoolPriceGate.review({kind,id:'usepod:demo',provider:'usepod'});
await new Promise(r=>setImmediate(r));
const overlay=document.body.children.find(x=>x.id==='vgOverlay');
const actions=overlay.querySelector('#vgActions');
const button=actions.children.find(x=>String(x.className||'').split(' ').includes('vg-conversation'));
const label=button.textContent;
const body=overlay.querySelector('#vgBody').innerHTML;
button.click();out({decision:await decision,body,hidden:overlay.hidden,label});
""")
    assert result["decision"] == "conversation" and result["hidden"]
    assert result["label"] == "Accept price for this chat (1 hour)"
    assert "1 hour" in result["body"] and "spending limits" in result["body"]
    # Price acceptance does not widen the separately approved monetary budget.
    assert "price only" in result["body"] and "approved UsePod budget" in result["body"]
    assert "Provider <b>UsePod</b>" in result["body"] and "6.00 USDC" in result["body"]


def test_late_acceptance_read_cannot_replace_enabled_confirmation_buttons():
    result = run_node(DOM + r'''
const acceptanceReads=[];
fetch=async url=>url.includes('/acceptances')
  ? new Promise(resolve=>acceptanceReads.push(resolve))
  : {json:async()=>({provider:'usepod',age_seconds:0,models:[{id:'demo',prompt_usd_per_m:1,completion_usd_per_m:6}]})};
''' + _GATE_JS + r'''
const decision=window.VoolPriceGate.review({kind:'pin',id:'usepod:demo',provider:'usepod'});
await new Promise(r=>setImmediate(r));
const overlay=document.body.children.find(x=>x.id==='vgOverlay');
const actions=overlay.querySelector('#vgActions');
const before=actions.children[0].disabled;
acceptanceReads.forEach(resolve=>resolve({json:async()=>({acceptances:[]})}));
await new Promise(r=>setImmediate(r));
const button=actions.children[0];
await new Promise(r=>setImmediate(r));
const unchanged=button===actions.children[0], enabled=!button.disabled;
button.click();out({before,unchanged,enabled,decision:await decision});
''')
    assert result['before'] and result['unchanged'] and result['enabled']
    assert result['decision'] is True


def test_rapid_double_click_on_one_model_row_creates_one_decision():
    result = run_node(DOM + script() + '\nawait new Promise(r=>setImmediate(r));\n' + r'''
setDisplayedChat('openclaw:abababababababababab');setModelValue('vool');
let resolveDecision, decisions=0;const posts=[];
window.VoolPriceGate={review:()=>{decisions++;return new Promise(r=>resolveDecision=r);}};
fetch=async (url,opts)=>{
 if(!opts || opts.method!=='POST')return {ok:true,json:async()=>({})};
 const b=JSON.parse(opts.body);posts.push(b);
 return {ok:!!b.confirm_paid,status:b.confirm_paid?200:409,json:async()=>b.confirm_paid?{ok:true,provider:'usepod',model:'demo'}:{code:'paid_model_confirm_required'}};
};
const row=makeCloudRow({provider:'usepod',id:'demo',name:'Demo',free:false});
const event={stopPropagation(){}};
const first=row.__on.click(event), second=row.__on.click(event);
await new Promise(r=>setImmediate(r));const disabled=row.disabled;
resolveDecision(true);await Promise.all([first,second]);
out({decisions,posts:posts.length,disabled,selected:modelValue});
''')
    assert result['decisions']==1 and result['posts']==2 and result['disabled']
    assert result['selected']=='usepod:demo'


def test_chat_provider_badge_ignores_global_provider_and_stale_response():
    result = run_node(DOM + script() + '\nawait new Promise(r=>setImmediate(r));\n' + r'''
setDisplayedChat('openclaw:abababababababababab');
const pending=[], urls=[];
fetch=async url=>{urls.push(url);return new Promise(resolve=>pending.push(resolve));};
setModelValue('usepod:demo');
renderConnections([{id:'cloud',provider:'openrouter',label:'OpenRouter',state:'ok'}]);
const label=()=>cloudPillEl.children.map(n=>n.children.map(c=>c.textContent).join('')).join('');
const initial=label();
setModelValue('openai:demo');
pending[0]({json:async()=>({connections:[{id:'cloud',provider:'usepod',label:'UsePod',state:'ok'}]})});
await new Promise(r=>setImmediate(r));
const afterStale=label();
out({initial,afterStale,urls});
''')
    assert result['initial']=='UsePod' and result['afterStale']=='openai'
    assert any('provider=usepod' in u for u in result['urls'])
    assert any('provider=openai' in u for u in result['urls'])
