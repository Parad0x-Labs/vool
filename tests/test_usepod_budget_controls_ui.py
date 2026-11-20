"""Execute actual controls for provider budgets, duplicate selection and price waiting."""

from core.price_safety_fragment import _GATE_JS
from tests.chat_page_js_harness import DOM, run_node, script


def test_duplicate_price_review_is_one_stable_decision():
    result = run_node(
        DOM
        + r"""
let release;const posts=[];
fetch=async(url,opts)=>{
 if(opts&&opts.method==='POST'){posts.push({url,body:JSON.parse(opts.body)});return {ok:true,json:async()=>({ok:true})};}
 if(url.includes('/acceptances'))return {ok:true,json:async()=>({acceptances:[]})};
 if(url.includes('/discovery'))return {ok:true,json:async()=>({approved_routes:{}})};
 return new Promise(r=>release=r);
};
"""
        + _GATE_JS
        + r"""
const ctx={kind:'pin',provider:'usepod',id:'usepod:model'};
const one=VoolPriceGate.review(ctx),two=VoolPriceGate.review({...ctx});
await new Promise(r=>setImmediate(r));const overlay=document.body.children.find(x=>x.id==='vgOverlay');
const loadingVisible=!overlay.hidden;
const disabledBefore=overlay.querySelector('#vgActions').children[0].disabled;
release({ok:true,json:async()=>({provider:'usepod',age_seconds:0,models:[{id:'model',prompt_usd_per_m:1,completion_usd_per_m:2}]})});
await new Promise(r=>setImmediate(r));const visibleAfter=!overlay.hidden;
const enabledAfter=!overlay.querySelector('#vgActions').children[0].disabled;
document.getElementById('vgMaxIn').value='1';document.getElementById('vgMaxOut').value='2';
overlay.querySelector('#vgActions').children[0].click();out({same:one===two,loadingVisible,disabledBefore,visibleAfter,enabledAfter,one:await one,two:await two,posts});
""".replace("VoolPriceGate.review", "window.VoolPriceGate.review")
    )
    assert all(result[key] for key in ["same", "loadingVisible", "disabledBefore", "visibleAfter", "enabledAfter", "one", "two"])
    assert len(result["posts"]) == 1
    assert result["posts"][0]["url"] == "/api/cloud/usepod/approve-route"
    assert result["posts"][0]["body"]["max_input_usdc_per_million"] == "1"
    assert result["posts"][0]["body"]["max_output_usdc_per_million"] == "2"


def test_rerendered_model_row_cannot_launch_second_selection():
    result = run_node(
        DOM
        + script()
        + r"""
await new Promise(r=>setImmediate(r));setDisplayedChat('openclaw:aaaaaaaaaaaaaaaaaaaa');
let resolvePin;let requests=0;
fetch=async()=>{requests++;return await new Promise(r=>resolvePin=r)};
const first=switchCloudModel('model','Model',null,'usepod');
const second=await switchCloudModel('model','Model',null,'usepod');
resolvePin({ok:false,status:400,json:async()=>({error:'synthetic refusal'})});await first;
out({requests,second});
"""
    )
    assert result["requests"] == 1 and not result["second"]






def test_claimed_price_task_keeps_its_model_after_chat_selection_changes():
    result = run_node(
        DOM
        + script()
        + r"""
await new Promise(r=>setImmediate(r));const chat='openclaw:aaaaaaaaaaaaaaaaaaaa';setDisplayedChat(chat);setModelValue('vool');
let chatBody=null;let reviews=0;let claimedOnce=false;window.VoolPriceGate={review:async()=>{reviews++;return true;}};
fetch=async(url,opts)=>{
 if(url==='/api/chat/queue'&&opts&&JSON.parse(opts.body).op==='claim'){if(claimedOnce)return {ok:true,json:async()=>({item:null})};claimedOnce=true;return {ok:true,json:async()=>({item:{queue_item_id:'saved-task',payload:{text:'Write a friendly greeting.',price_wait:{model_id:'saved-model'}}}})};}
 if(String(url).startsWith('/api/cloud/model'))return {ok:true,json:async()=>({ok:true,model:'',provider:'',cost_state:'paid'})};
 if(url==='/api/chat'){chatBody=JSON.parse(opts.body);throw new Error('stop at dispatch');}
 return {ok:true,json:async()=>({ok:true,sessions:[],messages:[],queue:[],pins:[],projects:[],models:[],events:[],receipts:[],counts:{},connections:[],data:[]})};};
await pumpQueue(chat);out({chatBody,reviews});
"""
    )
    assert result["chatBody"]["model"] == "usepod:saved-model"
    assert result["chatBody"]["model_selection"] == "pin"
    assert result["chatBody"]["queue_item_id"] == "saved-task"
    assert result["reviews"] == 0
