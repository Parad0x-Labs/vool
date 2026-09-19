"""Real price dialog: slow reads remain visible, cancellable and fail closed."""
import pytest
from playwright.sync_api import expect, sync_playwright

from core.price_safety_fragment import render_price_safety_fragment


@pytest.fixture
def page():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content('''<body><script>
window.pending = []; window.calls = [];
window.fetch = (url, opts) => {
  calls.push(String(url));
  if (String(url).includes('/models?')) return new Promise(resolve => pending.push(resolve));
  return Promise.resolve({ok:true,json:async()=>({acceptances:[]})});
};
window.finishPrices = () => pending.splice(0).forEach(resolve => resolve({ok:true,json:async()=>({provider:'openrouter',age_seconds:0,models:[{id:'test/model',prompt_usd_per_m:1,completion_usd_per_m:2}]})}));
</script>''' + render_price_safety_fragment() + '''<button id="choose" onclick="window.VoolPriceGate.review({kind:'pin',provider:'openrouter',id:'test/model',code:'paid_model_confirm_required'}).then(x=>window.decision=x)">Choose model</button>''')
        yield page
        browser.close()

def test_slow_price_read_shows_dialog_and_can_be_cancelled(page):
    page.locator('#choose').click()
    expect(page.locator('#vgOverlay')).to_be_visible(timeout=500)
    expect(page.locator('.vg-confirm')).to_be_disabled()
    page.locator('.vg-decline').click()
    page.evaluate('finishPrices()')
    expect(page.locator('#vgOverlay')).to_be_hidden()
    assert page.evaluate('decision') is False

def test_stalled_price_read_has_bounded_recovery_without_approval(page):
    page.clock.install()
    page.locator('#choose').click()
    page.clock.run_for(20001)
    expect(page.locator('#vgError')).to_contain_text('too long', timeout=500)
    expect(page.locator('.vg-confirm')).to_be_disabled()
    page.get_by_role('button', name='Retry price check').click()
    page.evaluate('finishPrices()')
    expect(page.locator('.vg-confirm')).to_be_enabled()
    page.locator('.vg-confirm').click()
    assert page.evaluate('decision') == 'conversation'

def test_ready_prices_enable_the_same_button_once_without_moving_it(page):
    page.locator('#choose').click()
    expect(page.locator('#vgOverlay')).to_be_visible(timeout=500)
    before = page.locator('.vg-confirm').bounding_box()
    page.evaluate('finishPrices()')
    expect(page.locator('.vg-confirm')).to_be_enabled()
    after = page.locator('.vg-confirm').bounding_box()
    assert before == after
    page.locator('.vg-confirm').click()
    expect(page.locator('#vgOverlay')).to_be_hidden()
    assert page.evaluate('decision') == 'conversation'


def test_model_row_shows_pending_then_timeout_and_can_be_selected_again():
    from core.vool_chat_page import render_vool_chat_html
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.add_init_script('''window.posts=[]; window.fetch=(url,opts)=>{
if(String(url)==='/api/cloud/model' && opts && opts.method==='POST'){posts.push(JSON.parse(opts.body));return new Promise(()=>{});}
return Promise.resolve({ok:true,status:200,json:async()=>({ok:true,sessions:[],items:[],models:[],projects:[],provider:'openrouter'})});};''')
            page.route('**/*', lambda r: r.fulfill(status=200, content_type='text/html', body=render_vool_chat_html()))
            page.goto('http://vool-picker.test/')
            page.evaluate("document.body.appendChild(makeCloudRow({id:'test/model',name:'Test Model',provider:'openrouter',free:false})).id='testModelRow'")
            page.clock.install()
            row=page.locator('#testModelRow')
            row.click()
            expect(row).to_contain_text('Checking model and price')
            expect(row).to_be_disabled()
            page.clock.run_for(20001)
            expect(row).to_contain_text('took too long')
            expect(row).to_be_enabled()
            row.click()
            assert page.evaluate('posts.length') == 2
        finally:
            browser.close()
