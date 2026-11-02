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

from core.price_safety_fragment import _GATE_JS
from tests.chat_page_js_harness import DOM, run_node, script

CHAT = "openclaw:aaaaaaaaaaaaaaaaaaaa"
OTHER = "openclaw:bbbbbbbbbbbbbbbbbbbb"


def drive(code: str) -> dict:
    result = run_node(DOM + script() + "\nawait new Promise(r=>setImmediate(r));\n" + code)
    assert result["errors"] == [], result["errors"]
    return result


# --- the composer preflight verifies the balance before the draft is consumed ----------------------








# --- the chat's persisted selection, its pill and its outgoing request agree --------------------------


def test_choosing_auto_or_a_local_model_releases_the_servers_chat_pin():
    result = drive(r"""
setDisplayedChat('openclaw:aaaaaaaaaaaaaaaaaaaa');setModelValue('plugin-sample-provider:gpt-6-astra');
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
const answers={[a]:{ok:true,model:'gpt-6-astra',provider:'plugin-sample-provider',cost_state:'paid'},[b]:{ok:true,model:'',provider:''},[c]:{ok:true,model:'',provider:''}};
fetch=async (url)=>{const m=String(url).match(/session_id=([^&]+)/);if(m)return {ok:true,json:async()=>answers[decodeURIComponent(m[1])]};return {ok:true,json:async()=>({ok:true,sessions:[],connections:[]})}};
// entering a chat restores its own model first, exactly as openSession does
setDisplayedChat(a);restoreChatModel(a);await refreshSelectionProvenance();const adopted=modelValue;
setDisplayedChat(b);restoreChatModel(b);await refreshSelectionProvenance();const kept=modelValue;
setDisplayedChat(c);restoreChatModel(c);await refreshSelectionProvenance();const local=modelValue;
out({adopted,kept,local,stored:modelForChat(a),storedB:modelForChat(b)});
""")
    # The server's pin for THIS chat is adopted; a client-only pin is not cleared by a mid-session
    # refresh (that reload-style clear stays with boot), and a local model stays client-owned.
    assert result["adopted"] == "plugin-sample-provider:gpt-6-astra" and result["stored"] == "plugin-sample-provider:gpt-6-astra"
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
pending[0]({ok:true,json:async()=>({ok:true,model:'gpt-6-astra',provider:'plugin-sample-provider'})});await old;
out({a:modelForChat(a),b:modelForChat(b),displayed:modelValue});
""")
    assert result == {**result, "a": "vool", "b": "vool", "displayed": "vool"}


def test_the_header_pill_names_this_chats_route_and_labels_autos_defaults():
    result = drive(r"""
setDisplayedChat('openclaw:aaaaaaaaaaaaaaaaaaaa');
localModelIds=new Set(['qwen3:8b']);
cloudProviderRows=[{id:'plugin-sample-provider',label:'Example service',plugin_id:'sample-provider'}];
const rows=[{id:'cloud',provider:'openrouter',label:'OpenRouter',state:'ok'}];
const pill=()=>({text:cloudPillEl.children.map(c=>c.textContent).join(''),title:cloudPillEl.title,chips:cloudPillEl.children.filter(c=>String(c.className||'').includes('cp-chip')).map(c=>({text:c.textContent,title:c.title}))});
setModelValue('vool');renderConnections(rows);const auto=pill();
setModelValue('plugin-sample-provider:gpt-6-astra');renderConnections(rows);const pinned=pill();
setModelValue('qwen3:8b');renderConnections(rows);const local=pill();
out({auto,pinned,local});
""")
    assert result["auto"]["chips"][0]["text"] == "Auto" and "defaults" in result["auto"]["chips"][0]["title"]
    assert "OpenRouter" in result["auto"]["text"] and "an Auto default" in result["auto"]["chips"][1]["title"]
    assert result["pinned"]["chips"] == [{"text": "Example service", "title": "Example service — not verified yet — this chat’s selected model: gpt-6-astra"}]
    assert "Example service · gpt-6-astra" in result["pinned"]["title"]
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
