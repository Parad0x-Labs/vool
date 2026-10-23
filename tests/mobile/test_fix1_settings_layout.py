"""Real Chromium checks the phone card bounds and PC warning sheet."""
import os
from pathlib import Path

from tests.mobile.test_fix1_money_contract import desktop, rig
from tests.mobile.test_zz_money_probe import propose


def test_phone_fields_stay_inside_card_at_desktop_and_narrow_widths(rig, tmp_path):
    from playwright.sync_api import sync_playwright

    base = rig[0]
    with sync_playwright() as driver:
        browser = driver.chromium.launch(executable_path=os.environ.get('VOOL_TEST_CHROMIUM'))
        try:
            page = browser.new_page(viewport={'width':1280,'height':1000})
            page.goto(base+'/settings#phone')
            page.locator('[data-group="phone"]').click()
            page.locator('.phone-device .phone-cap input').first.wait_for()
            shots = Path(os.environ.get('VOOL_PHONE_SHOTS_DIR') or tmp_path)
            for width in (1280, 820, 600):
                page.set_viewport_size({'width':width,'height':1000})
                overflow = page.locator('.phone-device').evaluate_all('''cards => cards.flatMap(card => {
                    const box=card.getBoundingClientRect();
                    return [...card.querySelectorAll('input.inp,select.inp')].filter(input => {
                        const r=input.getBoundingClientRect();
                        return r.width>0 && (r.left<box.left-1 || r.right>box.right+1);
                    }).map(input=>({label:input.getAttribute('aria-label'),width:input.offsetWidth}));
                })''')
                assert not overflow, (width, overflow)
                page.locator('.phone-device .phone-cap input').last.scroll_into_view_if_needed()
                page.screenshot(path=str(shots/f'fix1-phone-settings-{width}.png'),full_page=True)
        finally:
            browser.close()


def test_pc_approval_sheet_shows_bound_phone_warnings(rig, tmp_path):
    from playwright.sync_api import sync_playwright

    base, phone, _, _, rpc, _ = rig
    status, value, _ = propose(phone, 79, 9_000_000)
    assert status == 200, value
    pid = value['result']['proposal']['proposal_id']
    with sync_playwright() as driver:
        browser = driver.chromium.launch(executable_path=os.environ.get('VOOL_TEST_CHROMIUM'))
        try:
            page = browser.new_page(viewport={'width':1280,'height':1000})
            page.goto(base+'/chat',wait_until='domcontentloaded')
            page.locator(f'.vw-card[data-proposal="{pid}"] .vw-review').click(timeout=30000)
            review = page.locator('#vwSheetReview')
            review.locator('[data-warning="new_payee"]').wait_for()
            assert review.locator('[data-warning="new_payee"]').is_visible()
            assert review.locator('[data-warning="unusual_amount"]').is_visible()
            assert review.locator('[data-review="mobile_origin"]').is_visible()
            shots = Path(os.environ.get('VOOL_PHONE_SHOTS_DIR') or tmp_path)
            page.screenshot(path=str(shots/'fix1-pc-payment-warnings.png'),full_page=True)
            assert rpc.send_count() == 0
        finally:
            browser.close()
