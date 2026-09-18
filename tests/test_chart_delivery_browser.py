"""Executed arithmetic -> real composer -> shipped parser/visuals, controlled planner."""
import base64
import json
import os
from pathlib import Path

import pytest
from playwright.sync_api import expect

from core.conductor.compose import compose_answer
from core.conductor.product_decision import ExecutionReport, reduce_execution_report
from core.presentation.chart_payload import parse_chart_body
from core.response_constraints import _presentation_answer_has_format
from tests import test_chat_visuals_browser as visual_browser
from tests.test_chart_delivery_contract import execute_chart, payload_from

browser = visual_browser.browser
origin = visual_browser.origin


@pytest.mark.parametrize('expressions,expected,shape', [
    (['750 + 250','200 * 3'],[1000,600],'bar'),
    (['24 * 7','31 * 9'],[168,279],'line'),
    (['2.5 * 18','3.25 * 14'],[45,45.5],'horizontal bar'),
])
@pytest.mark.parametrize('width',[1280,390])
def test_executed_chart_survives_composition_and_draws_exact_data(browser, origin, expressions, expected, shape, width, tmp_path):
    plan, outcomes = execute_chart(expressions,shape)
    decision = reduce_execution_report(ExecutionReport(bound_plan=plan.bound_plan,
        node_outcomes=tuple(outcomes), planned_node_ids=tuple(n.node_id for n in plan.nodes),
        requirement_nodes=plan.requirement_nodes))
    answer = compose_answer(plan,outcomes,decision)
    assert outcomes[-1].fulfilled
    assert _presentation_answer_has_format(answer.text,'chart')
    assert payload_from(answer.text)['data']['datasets'][0]['data'] == expected
    for o in outcomes[:-1]:
        assert answer.provenance['represented_by:'+o.node.node_id] == outcomes[-1].node.node_id
        assert answer.provenance[o.node.node_id] in answer.text
    page = browser.new_page(viewport={'width':width,'height':844})
    try:
        page.goto(origin)
        page.evaluate("text=>renderRichText(document.querySelector('#answer'),text)",answer.text)
        page.wait_for_selector('.vool-visual[data-state="ready"]')
        frame = page.frame_locator('.vool-visual iframe')
        expect(frame.locator('summary')).to_be_in_viewport(ratio=1)
        frame.locator('summary').click()
        expect(frame.locator('table tr').last).to_be_in_viewport(ratio=1)
        frame.locator('summary').click()
        expect(frame.locator('summary')).to_be_in_viewport(ratio=1)
        expect(frame.locator('canvas')).to_be_in_viewport(ratio=1)
        actual = frame.locator('canvas').evaluate('el=>Chart.getChart(el).data.datasets[0].data')
        assert actual == expected
        pixels = frame.locator('canvas').evaluate("el=>{const p=el.getContext('2d').getImageData(0,0,el.width,el.height).data; return p.filter((v,i)=>i%4===3&&v>0).length}")
        assert pixels > 1000
        assert page.locator('#answer table').count() == 1
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        output = Path(os.environ.get('VOOL_CHART_DELIVERY_EVIDENCE',str(tmp_path)))
        output.mkdir(parents=True,exist_ok=True)
        capture = page.screenshot(path=str(output/f'{shape.replace(" ","-")}-{width}.png'),full_page=True)
        colored = page.evaluate("""async png => {
            const image = new Image(); image.src = 'data:image/png;base64,' + png;
            await image.decode();
            const canvas = document.createElement('canvas');
            canvas.width = image.width; canvas.height = image.height;
            const context = canvas.getContext('2d'); context.drawImage(image,0,0);
            const data = context.getImageData(0,0,canvas.width,canvas.height).data;
            let count = 0;
            for (let i=0; i<data.length; i+=4)
                if (data[i+1] > data[i]+30 && data[i+2] > data[i]+30) count++;
            return count;
        }""",base64.b64encode(capture).decode('ascii'))
        assert colored > 25, 'visible screenshot lost the rendered chart'
    finally:
        page.close()


@pytest.mark.parametrize('change,accepted', [
    ({},True), ({'axis':'y'},True), ({'background':'#eaf4f0'},True),
    ({'options':{}},False), ({'background':'url(https://example.invalid)'},False),
    ({'data':{'labels':['A'],'datasets':[{'data':[9007199254740993]}]}},False),
    ({'data':{'labels':['A'],'datasets':[{'data':[True]}]}},False),
])
def test_publication_and_browser_enforce_the_same_payload_cases(browser,origin,change,accepted):
    body = json.dumps({'type':'bar','data':{'labels':['A'],'datasets':[{'data':[17.25]}]},**change})
    try:
        parse_chart_body(body)
        server_accepts = True
    except (ValueError,TypeError,OverflowError):
        server_accepts = False
    assert server_accepts is accepted
    page = browser.new_page()
    try:
        page.goto(origin)
        page.evaluate("text=>renderRichText(document.querySelector('#answer'),text)",'```CHART\n'+body+'\n```')
        state = 'ready' if accepted else 'error'
        page.wait_for_selector(f'.vool-visual[data-state="{state}"]')
    finally:
        page.close()
