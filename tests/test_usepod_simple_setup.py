"""Execute the shipped search, receipt and pause UI projection with synthetic data."""
import re

from core.task_event_model import build_task_event
from core.vool_settings_page import render_vool_settings_html
from tests.chat_page_js_harness import DOM, run_node, script


def test_search_normalizes_display_spelling_without_changing_wire_model_id():
    result = run_node(DOM + script() + r'''
const id='gpt-5.6-astra';out({hits:['GPT 5.6','gpt-5.6','GpT_5.6','GPT–5.6'].map(q=>modelSearchKey(id).includes(modelSearchKey(q))), id});
''')
    assert all(result['hits']) and result['id'] == 'gpt-5.6-astra'
    helper = re.search(r'function modelSearchKey\(value\) \{.*?\n\}', render_vool_settings_html(), re.S).group()
    result = run_node(DOM + helper + "out({hit:modelSearchKey('GPT-5.6 Astra').includes(modelSearchKey('gpt 5.6'))});")
    assert result['hit']


def test_unknown_balance_unit_is_never_labelled_usdc():
    result = run_node(DOM + script() + r'''
out({text:ledgerUsePodReceipt({provider_receipt:{schema:'vool.usepod.receipt.v1',balance_remaining:{state:'reported',raw:'2999043',decimal:'2999043',unit:'unverified_header_unit'}}})});
''')
    assert 'unit is unverified' in result['text']
    assert '2999043 USDC' not in result['text']


def test_live_pause_event_reaches_chat_and_keeps_stop_available():
    event = build_task_event({'event_type':'usepod_price_paused','message':'Price rose; waiting safely.'})
    assert event['type'] == 'task.price_paused' and event['summary'] == 'Price rose; waiting safely.'
    result = run_node(DOM + script() + r'''
const run=newRun('openclaw:aaaaaaaaaaaaaaaaaaaa');
applyTaskEvent(run,{type:'task.price_paused',summary:'Price rose; waiting safely.'});
const paused=run.pricePaused, ended=run.ended;
applyTaskEvent(run,{type:'task.price_resumed',summary:'Continuing.'});
out({paused,ended,resumed:!run.pricePaused,action:run.action});
''')
    assert not result['errors']
    assert {key: result[key] for key in ('paused','ended','resumed','action')} == {'paused':True,'ended':False,'resumed':True,'action':'Continuing.'}
