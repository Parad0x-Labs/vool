"""The selection -> approval -> verified balance -> dispatch workflow, on the real page handlers.

Executes the chat page's own script and the Settings widget under node against scripted server
answers (no daemon, no provider): the composer preflight verifies the balance through the one
balance door before a draft is consumed, a chat's server pin and its composer pill are reconciled
in both directions, the header pill never paints Auto's defaults as this chat's selection, the
price review says what accepting a price does and does not authorize, and the focused budget panel
shows the decision in ordinary USDC. Refusal controls keep the draft and never send.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.price_safety_fragment import _GATE_JS
from tests.chat_page_js_harness import DOM, run_node, script

CHAT = "openclaw:aaaaaaaaaaaaaaaaaaaa"
OTHER = "openclaw:bbbbbbbbbbbbbbbbbbbb"


def drive(code: str) -> dict:
    result = run_node(DOM + script() + "\nawait new Promise(r=>setImmediate(r));\n" + code)
    assert result["errors"] == [], result["errors"]
    return result


# --- the composer preflight verifies the balance before the draft is consumed ----------------------


@pytest.mark.parametrize(
    "balance,blocker",
    [
        ({"state": "unavailable", "http_status": 403, "error_code": "unauthorized"}, "did not confirm your balance (HTTP 403)"),
        ({"state": "reported", "usdc_balance_microunits": 0, "usdc_balance": "0.000000"}, "balance is 0 USDC"),
        ({"state": "field_malformed"}, "cannot read (field_malformed)"),
        ({"state": "credential_pair_unresolved"}, "still being saved or removed"),
        (None, "could not be checked"),
    ],
)
def test_an_unverified_balance_keeps_the_draft_and_opens_recovery_without_sending(balance, blocker):
    result = drive("const balance=" + json.dumps(balance) + ";\n" + r"""
setDisplayedChat('openclaw:aaaaaaaaaaaaaaaaaaaa');setModelValue('usepod:gpt-6-astra');
inputEl.value='what is best for tg bot coding java or py?';
const requests=[], opened=[], toasts=[];openSettingsInFrame=s=>opened.push(s);toast=t=>toasts.push(String(t));
fetch=async (url,opts)=>{const method=(opts&&opts.method)||'GET';requests.push({url,method});
 if(url==='/api/cloud/usepod/balance'){ if(balance===null) throw new Error('down'); return {ok:balance.state!=='credential_pair_unresolved',status:balance.state==='credential_pair_unresolved'?409:200,json:async()=>balance}; }
 return {ok:true,json:async()=>({spend_readiness:{available:true,state:'available'}})}};
await send();
out({draft:inputEl.value,requests,opened,toasts});
""")
    assert result["draft"] == "what is best for tg bot coding java or py?"
    assert result["opened"] == ["models/usepod/spend"]
    assert [r["method"] for r in result["requests"]] == ["GET", "POST"]
    assert all("/api/chat" not in r["url"] for r in result["requests"])
    assert len(result["toasts"]) == 1 and blocker in result["toasts"][0] and "Nothing was sent" in result["toasts"][0]


def test_a_verified_balance_lets_the_original_question_reach_the_chat_door():
    result = drive(r"""
setDisplayedChat('openclaw:aaaaaaaaaaaaaaaaaaaa');setModelValue('usepod:gpt-6-astra');
inputEl.value='what is best for tg bot coding java or py?';
const requests=[], opened=[];openSettingsInFrame=s=>opened.push(s);
let chatBody=null;
fetch=async (url,opts)=>{const method=(opts&&opts.method)||'GET';requests.push({url:String(url),method});
 if(url==='/api/cloud/usepod/balance')return {ok:true,json:async()=>({state:'reported',usdc_balance_microunits:3000000,usdc_balance:'3.000000',evidence:'live_read',age_seconds:0})};
 if(String(url).startsWith('/api/cloud/model'))return {ok:true,json:async()=>({ok:true,model:'gpt-6-astra',provider:'usepod',cost_state:'paid'})};
 if(url==='/api/chat'){chatBody=JSON.parse(opts.body);throw new Error('stop here: the door was reached');}
 return {ok:true,json:async()=>({spend_readiness:{available:true,state:'available'},ok:true,sessions:[],messages:[],queue:[],pins:[],projects:[],models:[],events:[],receipts:[],counts:{},connections:[],data:[]})}};
window.VoolPriceGate={review:async()=>true};
await send();
for (let i=0;i<20;i++) await new Promise(r=>setImmediate(r));
out({draft:inputEl.value,opened,reached:!!chatBody,model:chatBody&&chatBody.model,selection:chatBody&&chatBody.model_selection,session:chatBody&&chatBody.session_id,
  order:requests.filter(r=>r.url==='/api/cloud/usepod/balance'||r.url==='/api/chat').map(r=>r.url)});
""")
    assert result["opened"] == []
    assert result["reached"] is True
    assert (result["model"], result["selection"], result["session"]) == ("usepod:gpt-6-astra", "pin", CHAT)
    assert result["order"] == ["/api/cloud/usepod/balance", "/api/chat"]


def test_a_novel_question_on_a_different_usepod_model_takes_the_same_path():
    result = drive(r"""
setDisplayedChat('openclaw:bbbbbbbbbbbbbbbbbbbb');setModelValue('usepod:meridian-synth-chat');
inputEl.value='Which weekday comes three days after Thursday? One word.';
const requests=[];let chatBody=null;
fetch=async (url,opts)=>{requests.push(String(url));
 if(url==='/api/cloud/usepod/balance')return {ok:true,json:async()=>({state:'reported',usdc_balance_microunits:80000000,usdc_balance:'80.000000',evidence:'cache',age_seconds:12})};
 if(String(url).startsWith('/api/cloud/model'))return {ok:true,json:async()=>({ok:true,model:'meridian-synth-chat',provider:'usepod',cost_state:'paid'})};
 if(url==='/api/chat'){chatBody=JSON.parse(opts.body);throw new Error('stop here');}
 return {ok:true,json:async()=>({spend_readiness:{available:true,state:'available'},ok:true,sessions:[],messages:[],queue:[],pins:[],projects:[],models:[],events:[],receipts:[],counts:{},connections:[],data:[]})}};
window.VoolPriceGate={review:async()=>true};
await send();for (let i=0;i<20;i++) await new Promise(r=>setImmediate(r));
out({discovery:requests.find(u=>u.includes('/discovery?q=')),model:chatBody&&chatBody.model,text:chatBody&&chatBody.messages&&chatBody.messages[chatBody.messages.length-1].content});
""")
    assert result["discovery"].endswith("/discovery?q=meridian-synth-chat")
    assert result["model"] == "usepod:meridian-synth-chat"
    assert result["text"] == "Which weekday comes three days after Thursday? One word."


# --- the chat's persisted selection, its pill and its outgoing request agree --------------------------


def test_choosing_auto_or_a_local_model_releases_the_servers_chat_pin():
    result = drive(r"""
setDisplayedChat('openclaw:aaaaaaaaaaaaaaaaaaaa');setModelValue('usepod:gpt-6-astra');
localModelIds=new Set(['qwen3:8b']);
const posts=[];fetch=async (url,opts)=>{if(opts&&opts.method==='POST'&&url==='/api/cloud/model')posts.push(JSON.parse(opts.body));return {ok:true,json:async()=>({ok:true,model:'',provider:'',sessions:[],connections:[]})}};
await releaseServerChatPin(displayedChat);
pinLocalModel('qwen3:8b',null);
for (let i=0;i<20;i++) await new Promise(r=>setImmediate(r));
out({posts,model:modelValue});
""")
    assert result["model"] == "qwen3:8b"
    assert [p["model"] for p in result["posts"]] == ["auto", "auto"]
    assert all(p["session_id"] == CHAT and p["selection_revision"] > 0 for p in result["posts"])


def test_navigation_adopts_the_servers_pin_and_keeps_client_only_pins():
    result = drive(r"""
const a='openclaw:aaaaaaaaaaaaaaaaaaaa', b='openclaw:bbbbbbbbbbbbbbbbbbbb', c='openclaw:cccccccccccccccccccc';
localModelIds=new Set(['qwen3:8b']);
setDisplayedChat(a);setModelValue('vool');
setDisplayedChat(b);setModelValue('openrouter/kept:free');
setDisplayedChat(c);setModelValue('qwen3:8b');
const answers={[a]:{ok:true,model:'gpt-6-astra',provider:'usepod',cost_state:'paid'},[b]:{ok:true,model:'',provider:''},[c]:{ok:true,model:'',provider:''}};
fetch=async (url)=>{const m=String(url).match(/session_id=([^&]+)/);if(m)return {ok:true,json:async()=>answers[decodeURIComponent(m[1])]};return {ok:true,json:async()=>({ok:true,sessions:[],connections:[]})}};
// entering a chat restores its own model first, exactly as openSession does
setDisplayedChat(a);restoreChatModel(a);await refreshSelectionProvenance();const adopted=modelValue;
setDisplayedChat(b);restoreChatModel(b);await refreshSelectionProvenance();const kept=modelValue;
setDisplayedChat(c);restoreChatModel(c);await refreshSelectionProvenance();const local=modelValue;
out({adopted,kept,local,stored:modelForChat(a),storedB:modelForChat(b)});
""")
    # The server's pin for THIS chat is adopted; a client-only pin is not cleared by a mid-session
    # refresh (that reload-style clear stays with boot), and a local model stays client-owned.
    assert result["adopted"] == "usepod:gpt-6-astra" and result["stored"] == "usepod:gpt-6-astra"
    assert result["kept"] == result["storedB"] == "openrouter/kept:free"
    assert result["local"] == "qwen3:8b"


def test_a_stale_selection_answer_after_switching_chats_changes_nothing():
    result = drive(r"""
const a='openclaw:aaaaaaaaaaaaaaaaaaaa', b='openclaw:bbbbbbbbbbbbbbbbbbbb';
setDisplayedChat(a);setModelValue('vool');setDisplayedChat(b);setModelValue('vool');
const pending=[];
fetch=async (url)=>{if(String(url).startsWith('/api/cloud/model'))return new Promise(res=>pending.push(res));return {ok:true,json:async()=>({ok:true,sessions:[],connections:[]})}};
setDisplayedChat(a);const old=refreshSelectionProvenance();
setDisplayedChat(b);const fresh=refreshSelectionProvenance();
pending[1]({ok:true,json:async()=>({ok:true,model:'',provider:''})});await fresh;
pending[0]({ok:true,json:async()=>({ok:true,model:'gpt-6-astra',provider:'usepod'})});await old;
out({a:modelForChat(a),b:modelForChat(b),displayed:modelValue});
""")
    assert result == {**result, "a": "vool", "b": "vool", "displayed": "vool"}


def test_the_header_pill_names_this_chats_route_and_labels_autos_defaults():
    result = drive(r"""
setDisplayedChat('openclaw:aaaaaaaaaaaaaaaaaaaa');
localModelIds=new Set(['qwen3:8b']);
const rows=[{id:'cloud',provider:'openrouter',label:'OpenRouter',state:'ok'}];
const pill=()=>({text:cloudPillEl.children.map(c=>c.textContent).join(''),title:cloudPillEl.title,chips:cloudPillEl.children.filter(c=>String(c.className||'').includes('cp-chip')).map(c=>({text:c.textContent,title:c.title}))});
setModelValue('vool');renderConnections(rows);const auto=pill();
setModelValue('usepod:gpt-6-astra');renderConnections(rows);const pinned=pill();
setModelValue('qwen3:8b');renderConnections(rows);const local=pill();
out({auto,pinned,local});
""")
    assert result["auto"]["chips"][0]["text"] == "Auto" and "defaults" in result["auto"]["chips"][0]["title"]
    assert "OpenRouter" in result["auto"]["text"] and "an Auto default" in result["auto"]["chips"][1]["title"]
    assert result["pinned"]["chips"] == [{"text": "UsePod", "title": "UsePod — not verified yet — this chat’s selected model: gpt-6-astra"}]
    assert "UsePod · gpt-6-astra" in result["pinned"]["title"]
    assert result["local"]["text"] == "Local" and "qwen3:8b" in result["local"]["title"]


# --- the price review says what accepting a price does, and only that ----------------------------------


def test_the_price_review_names_provider_model_usdc_rates_and_the_chat_scope():
    result = run_node(DOM + r"""
const posts=[];
fetch=async (url,opts)=>{
 if(opts&&opts.method==='POST'){posts.push({url,body:JSON.parse(opts.body)});return {ok:true,json:async()=>({ok:true})};}
 if(url.includes('/acceptances'))return {ok:true,json:async()=>({acceptances:[]})};
 if(url.includes('/discovery'))return {ok:true,json:async()=>({approved_routes:{}})};
 return {ok:true,json:async()=>({provider:'usepod',age_seconds:0,models:[{id:'gpt-6-astra',prompt_usd_per_m:0.8,completion_usd_per_m:4}]})};
};
""" + _GATE_JS + r"""
const decision=window.VoolPriceGate.review({kind:'per-send',id:'usepod:gpt-6-astra',provider:'usepod',label:'gpt-6-astra'});
await new Promise(r=>setImmediate(r));
const overlay=document.body.children.find(x=>x.id==='vgOverlay');
const actions=overlay.querySelector('#vgActions');
const labels=actions.children.map(b=>b.textContent);
const body=overlay.querySelector('#vgBody').innerHTML;
document.getElementById('vgMaxIn').value='0.8';document.getElementById('vgMaxOut').value='4';
actions.children[0].click();
out({labels,body,decision:await decision,posts});
""")
    assert result["labels"][:2] == ["Accept price and send", "Accept price for this chat (24 hours)"]
    assert "Provider <b>UsePod</b>" in result["body"] and "model <b>gpt-6-astra</b>" in result["body"]
    assert "0.80 USDC per 1M tokens" in result["body"] and "4.00 USDC per 1M tokens" in result["body"]
    assert "approved UsePod budget" in result["body"] and "never widens" in result["body"]
    assert "permission for this conversation" not in result["body"].lower()
    assert result["decision"] == "conversation"
    assert len(result["posts"]) == 1 and result["posts"][0]["url"] == "/api/cloud/usepod/approve-route"


# --- the focused budget panel: the decision in ordinary USDC, with a balance check ---------------------

SETTINGS_SOURCE = Path("core/vool_settings_page.py").read_text(encoding="utf-8")
WIDGET = SETTINGS_SOURCE[SETTINGS_SOURCE.index("const USEPOD_DISCOVERY_URL"):SETTINGS_SOURCE.index("/* One model row:")]


def test_the_focused_budget_panel_shows_the_decision_and_checks_the_balance_without_sending():
    result = run_node(DOM + r"""
const state={focusPart:'spend'};
const $=s=>document.getElementById(s.slice(1));
const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!=null)n.textContent=text;return n;};
const now=Math.floor(Date.now()/1000);
const d={lane:{transport_mode:'prepaid_token'},credential:{configured:true},authorities:{},
 approved_routes:{'gpt-6-astra':{max_input_usdc_per_million:'0.8',max_output_usdc_per_million:'4',snapshot_fetched_at:now-30}},
 credential_discovery:{balance:{state:'reported',usdc_balance_microunits:3000000,observed_at:now-4723}},
 spend_approval:{state:'pending',approval_id:'approval-fixture',facts:{account:'upc_fixture',asset:'USDC',network:'usepod:account',unit:'usdc_microunit',models:['gpt-6-astra'],per_call_atomic:3000000,max_total_atomic:3000000,expiry_epoch:now+24*3600}}};
const getJSON=async()=>JSON.parse(JSON.stringify(d));
const posts=[];
fetch=async(url,options)=>{const body=JSON.parse(options.body);posts.push({url,body});
 if(url==='/api/cloud/usepod/balance'){d.credential_discovery.balance={state:'reported',usdc_balance_microunits:3000000,observed_at:Math.floor(Date.now()/1000)};return {ok:true,status:200,json:async()=>({state:'reported',usdc_balance_microunits:3000000,usdc_balance:'3.000000'})};}
 return {ok:true,status:200,json:async()=>({ok:true})};};
const stack=el('div');
const settle=async()=>{for(let i=0;i<5;i++)await new Promise(r=>setImmediate(r));};
const all=n=>[n,...n.children.flatMap(all)];
const find=cls=>all(stack).find(n=>String(n.className||'').split(' ').includes(cls));
const text=n=>all(n).map(x=>x.__text||'').filter(Boolean).join(' | ');
""" + WIDGET + r"""
widgetUsePod(stack);await settle();
const before=text(find('usepod-spend-summary'));
const balanceBefore=find('usepod-spend-balance').textContent;
await find('usepod-balance-check').__on.click();await settle();
const balanceAfter=find('usepod-spend-balance').textContent;
out({before,balanceBefore,balanceAfter,posts,note:stack.children[stack.children.length-1].textContent});
""")
    assert "Provider | UsePod — prepaid token balance" in result["before"]
    assert "1. Your UsePod account" in result["before"]
    assert "Maximum spend" not in result["before"]  # budget is reviewed in its own step
    assert result["balanceBefore"].startswith("3 USDC — checked 79 min ago") and "re-read before sending" in result["balanceBefore"]
    assert result["balanceAfter"].startswith("3 USDC — checked just now")
    assert result["posts"] == [{"url": "/api/cloud/usepod/balance", "body": {"max_age_seconds": 0}}]
    assert "3 USDC confirmed by UsePod" in result["note"]
    assert "3000000" not in result["before"] and "3M" not in result["before"]
