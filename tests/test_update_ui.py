"""Execute the shipped update controls, including translated button labels."""
from tests.chat_page_js_harness import DOM, HTML, run_node, script, slice_source


def test_header_shows_up_to_date_and_dispatches_by_phase():
    code = slice_source(script(), "const updateChip =", "setInterval(maybeRepaintSidebarLifecycle")
    result = run_node(DOM + r"""
const calls = [];
const pageT = (key, fallback) => key === 'update.action_restart' ? 'Paleisti iš naujo' : fallback;
const pageTF = (key, fallback, args) => fallback.replace(/\{(\w+)\}/g, (_, name) => args[name]);
globalThis.fetch = async (url, options) => { calls.push([url, options && options.method]);
  return {ok:true, json:async () => ({phase:'up_to_date',message:'Up to date'})}; };
globalThis.setTimeout = () => 1; globalThis.setInterval = () => 1;
""" + code + """
await new Promise(resolve => process.nextTick(resolve));
renderUpdateChip({phase:'up_to_date', message:'Up to date'});
const current = {visible: !updateChip.hidden, label: updateChipTxt.textContent,
                 actionHidden: updateActionBtn.hidden, checkVisible: !updateCheckBtn.hidden};
renderUpdateChip({phase:'ready_to_restart', target_version:'0.7.1'});
const translated = updateActionBtn.textContent;
updateActionBtn.click();
renderUpdateChip({phase:'ready', target_version:'0.7.1'});
updateActionBtn.click();
updateCheckBtn.click();
await new Promise(resolve => process.nextTick(resolve));
out({current, translated, calls});
""")
    assert result["errors"] == []
    assert result["current"] == {"visible": True, "label": "Up to date", "actionHidden": True, "checkVisible": True}
    assert result["translated"] == "Paleisti iš naujo"
    assert [call[0] for call in result["calls"] if call[1] == "POST"] == [
        "/api/update/restart", "/api/update/install", "/api/update/check"
    ]
    assert "#updateCtrl [hidden] { display:none !important; }" in HTML


def test_settings_status_and_check_use_the_same_authority():
    import re

    from core.vool_settings_page import render_vool_settings_html
    source = next(part for part in re.findall(r"<script[^>]*>(.*?)</script>", render_vool_settings_html(), re.DOTALL) if "function widgetUpdates(stack)" in part)
    code = slice_source(source, "function widgetUpdates(stack)", "function widgetBuild(stack)")
    result = run_node(DOM + """
const state = {sources:{'/api/update/status':{phase:'up_to_date'}},sourceErr:{}};
const T = (key, fallback) => key === 'update.up_to_date' ? 'Atnaujinta' : fallback;
const el = (tag, cls, text) => {const node = new El(tag); node.textContent = text; return node;};
const calls = [];
const readSource = async url => { calls.push(['GET',url]); state.sources[url] = {phase:'ready',message:'Version 0.7.1 ready'}; };
globalThis.fetch = async (url, options) => { calls.push([options.method,url]); return {ok:true,json:async()=>({})}; };
const stack = new El();
""" + code + """
widgetUpdates(stack);
const initial = stack.children[0].textContent;
stack.children[1].click();
await new Promise(resolve => process.nextTick(resolve));
out({initial,after:stack.children[0].textContent,calls});
""")
    assert result["errors"] == []
    assert result["initial"] == "Atnaujinta"
    assert result["after"] == "Version 0.7.1 ready"
    assert result["calls"] == [["POST", "/api/update/check"], ["GET", "/api/update/status"]]


def test_header_rollback_uses_explicit_action_and_hides_when_unavailable():
    code = slice_source(script(), 'const updateChip =', 'setInterval(maybeRepaintSidebarLifecycle')
    result = run_node(DOM + """
const calls=[]; const pageT=(key, fallback)=>fallback;
const pageTF=(key,fallback,args)=>fallback.replace('{version}',args.version);
globalThis.fetch=async(url,options)=>{calls.push([url,options&&options.method]);return {ok:true,json:async()=>({phase:'up_to_date'})};};
globalThis.setTimeout=()=>1;globalThis.setInterval=()=>1;
""" + code + """
await new Promise(resolve=>process.nextTick(resolve));
renderUpdateChip({phase:'up_to_date',rollback_available:true,rollback_version:'0.7.0'});
const available=!updateRollbackBtn.hidden;const label=updateRollbackBtn.textContent;
updateRollbackBtn.click();
renderUpdateChip({phase:'installing',rollback_available:true,rollback_version:'0.7.0'});
const busy=updateRollbackBtn.disabled;
renderUpdateChip({phase:'rolled_back',rollback_available:false});
await new Promise(resolve=>process.nextTick(resolve));
out({available,label,busy,hidden:updateRollbackBtn.hidden,calls});
""")
    assert result['errors'] == []
    assert result['available'] and result['busy'] and result['hidden']
    assert result['label'] == 'Roll back to 0.7.0'
    assert [call[0] for call in result['calls'] if call[1] == 'POST'] == ['/api/update/rollback']


def test_settings_rollback_dispatches_and_explains_current_data_preservation():
    import re

    from core.vool_settings_page import render_vool_settings_html
    source = next(part for part in re.findall(r'<script[^>]*>(.*?)</script>', render_vool_settings_html(), re.DOTALL)
                  if 'function widgetUpdates(stack)' in part)
    code = slice_source(source, 'function widgetUpdates(stack)', 'function widgetBuild(stack)')
    result = run_node(DOM + """
const state={sources:{'/api/update/status':{phase:'up_to_date',rollback_available:true,rollback_version:'0.7.0'}},sourceErr:{}};
const T=(key,fallback)=>fallback;const el=(tag,cls,text)=>{const node=new El(tag);node.textContent=text;return node;};
const calls=[];const readSource=async url=>{calls.push(['GET',url]);};
globalThis.fetch=async(url,options)=>{calls.push([options.method,url]);return {ok:true,json:async()=>({accepted:true})};};
const stack=new El();
""" + code + """
widgetUpdates(stack);const label=stack.children[2].textContent;const help=stack.children[3].textContent;
stack.children[2].click();await new Promise(resolve=>process.nextTick(resolve));out({label,help,calls});
""")
    assert result['errors'] == []
    assert result['label'] == 'Roll back to 0.7.0'
    assert result['help'] == 'Keeps your current chats, memory, keys and settings'
    assert result['calls'] == [['POST', '/api/update/rollback'], ['GET', '/api/update/status']]
