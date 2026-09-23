"""Run the Settings picker itself, including provider identity through paid confirmation."""
from pathlib import Path

from tests.chat_page_js_harness import DOM, run_node


def test_settings_provider_search_and_pin_keep_exact_provider():
    source=Path('core/vool_settings_page.py').read_text()
    start=source.index('function modelSearchKey(value)')
    widget=source[start:source.index('const USEPOD_ORIGIN_DEFAULT',start)]
    assert 'function widgetModel(stack)' in widget
    result=run_node(DOM + r'''
const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!=null)n.textContent=text;return n;};
const autoFallbackControl=async()=>{};
const gets=[], posts=[]; let confirms=0;
window.confirm=()=>{confirms++;return true;};
const getJSON=async url=>{
 gets.push(url);
 if(url==='/api/cloud/model')return {model:'',provider:'openrouter'};
 if(url==='/api/cloud/providers')return {providers:[{id:'openrouter',label:'OpenRouter'},{id:'usepod',label:'UsePod'},{id:'unused',label:'Unused'}]};
 if(url==='/api/settings/credentials')return {credentials:[{name:'llm.cloud.openrouter'},{name:'llm.cloud.usepod'},{name:'llm.cloud.usepod_origin'}]};
 if(url==='/api/connections')return {connections:[]};
 if(url==='/api/discovery')return {providers:{}};
 if(url.includes('/api/cloud/models')){const provider=url.includes('usepod')?'usepod':'openrouter';return {provider,models:[...Array.from({length:100},(_,i)=>({id:'model-'+i,name:'Model '+i,free:true})),{id:'gpt-6-astra',name:'Astra',free:false}]};}
 throw Error('unexpected '+url);
};
fetch=async (url,opts)=>{const b=JSON.parse(opts.body);posts.push(b);return {ok:!!b.confirm_paid,status:b.confirm_paid?200:409,json:async()=>b.confirm_paid?{ok:true}:{code:'paid_model_confirm_required'}};};
const stack=el('div');
const settle=async()=>{for(let i=0;i<8;i++)await new Promise(r=>setImmediate(r));};
const all=n=>[n,...n.children.flatMap(all)];
const find=cls=>all(stack).find(n=>String(n.className||'').split(' ').includes(cls));
''' + widget + r'''
widgetModel(stack);await settle();
const providers=find('model-provider').children.map(x=>x.value);
const beforeCount=find('model-picker').children.length;
find('model-provider').value='usepod';find('model-provider').__on.change();await settle();
find('model-search').value='astra';find('model-search').__on.input();
const matching=find('model-picker').children.map(x=>x.value);
const resultRow=find('model-picker').children.find(x=>x.value==='gpt-6-astra');
const visibleLabel=resultRow.textContent;resultRow.__on.click();
const selected=find('model-picker').value;await find('model-pin').__on.click();
out({providers,beforeCount,matching,posts,confirms,gets,visibleLabel,selected});
''')
    assert 'providers' in result, result
    assert result['providers']==['openrouter','usepod']
    assert result['beforeCount'] <= 42
    assert result['matching']==['auto','gpt-6-astra']
    assert result['posts']==[{'model':'gpt-6-astra','provider':'usepod'},{'model':'gpt-6-astra','provider':'usepod','confirm_paid':True}]
    assert result['confirms']==1
    assert result['visibleLabel']=='Astra · paid' and result['selected']=='gpt-6-astra'


def test_usepod_verification_places_client_header_on_the_auth_probe():
    from core.credential_intelligence.provider_registry import default_registry
    from core.credential_intelligence.verification import _place_credential
    descriptor=default_registry().get('usepod')
    url,headers,body=_place_credential('synthetic-path-token',descriptor)
    assert headers['User-Agent']=='VOOL/0.5'
    assert url.endswith('/proxy/synthetic-path-token/balance')
    assert 'Authorization' not in headers and body is None
