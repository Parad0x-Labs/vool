"""Real Chromium: the phone app leads the Phone settings, says what it is for, and offers both steps.

sls 2026-11-23: the phone app looked like less than Telegram and Discord. Its card now comes first and is
the largest, with a few plain reasons to use it, a code that opens the download page, then pairing.
"""
import os

from test_phone_listener_served import desktop, http  # noqa: F401 (fixture)


def _browser(driver):
    return driver.chromium.launch(executable_path=os.environ.get("VOOL_TEST_CHROMIUM"))


def test_phone_card_leads_and_holds_get_the_app_then_pair(desktop):
    from playwright.sync_api import sync_playwright

    base, _runtime, _home = desktop
    status, value = http(base, "/api/mobile/settings", {"enabled": True, "address": "127.0.0.1"})
    assert status == 200, value
    with sync_playwright() as driver:
        browser = _browser(driver)
        try:
            for width in (1280, 390):
                page = browser.new_page(viewport={"width": width, "height": 900})
                outside = []
                page.on("request", lambda req: outside.append(req.url) if not req.url.startswith(base) and not req.url.startswith("data:") else None)
                page.goto(base + "/settings#phone")
                nav = page.locator('[data-group="phone"]')
                if nav.is_visible():
                    nav.click()
                hero = page.locator(".phone-hero")
                hero.wait_for(timeout=10000)
                facts = page.evaluate(
                    """() => {
                      const hero = document.querySelector('.phone-hero');
                      const apps = document.querySelector('.phone-bot-connect');
                      const box = hero.getBoundingClientRect();
                      const appBox = apps.getBoundingClientRect();
                      return {
                        heroFirst: !!(hero.compareDocumentPosition(apps) & Node.DOCUMENT_POSITION_FOLLOWING),
                        heroArea: box.width * box.height,
                        appsArea: appBox.width * appBox.height,
                        points: hero.querySelectorAll('.phone-hero-points li').length,
                        steps: [...hero.querySelectorAll('.phone-hero-step h4')].map(h => h.textContent),
                        qr: !!hero.querySelector('.phone-hero-step canvas.phone-bot-qr'),
                        pairInHero: [...hero.querySelectorAll('button')].some(b => b.textContent === 'Pair a phone'),
                        link: (hero.querySelector('.phone-hero-step a') || {}).href || '',
                        overflow: document.documentElement.scrollWidth > window.innerWidth + 1,
                        heroInside: box.left >= 0 && box.right <= window.innerWidth + 1,
                      };
                    }"""
                )
                assert facts["heroFirst"], facts
                assert facts["heroArea"] > facts["appsArea"], facts
                assert facts["points"] == 5, facts
                assert facts["steps"] == ["1. Get the app", "2. Pair it with this computer"], facts
                assert facts["qr"], facts
                assert facts["pairInHero"], facts
                assert facts["link"] == "https://parad0x-labs.github.io/vool/app/", facts
                assert not facts["overflow"] and facts["heroInside"], (width, facts)
                # Showing the code fetches nothing from outside this computer.
                assert outside == [], outside
                page.close()
        finally:
            browser.close()
