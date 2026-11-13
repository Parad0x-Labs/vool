"""On the real daemon: a skill added from Browse is listed, switches off, and is removed by asking.

Measured on the Mac (d5d044b0): gentle-workout-plan, added from Browse, was missing from
/api/skills, and asking VOOL to remove it was refused. Here a fresh home adds it through the
same /api/addons calls the store makes, then the person's own paths are driven:
- My add-ons, Skills shows it with an off switch;
- /api/skills lists it, and its Off is read back there;
- "remove the gentle-workout-plan skill" in chat asks for approval, then removes it with no model
  call, and Browse offers it again.
"""

from __future__ import annotations

import json
from urllib.request import Request, urlopen

from tests import _reader_served_rig as rig
from tests import served_browser

ENTRY = "github-ec341f8c28f1570f773bd87b"
SKILL = "gentle-workout-plan"


def _call(base: str, path: str, body: dict | None = None) -> dict:
    request = Request(base + path, data=None if body is None else json.dumps(body).encode(),
                      method="GET" if body is None else "POST", headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=60) as response:
        return json.load(response)


def _listed(base: str) -> list[dict]:
    return [row for row in _call(base, "/api/skills")["skills"] if row.get("id") == SKILL]


def test_a_browse_skill_is_listed_switches_off_and_is_removed_by_asking(tmp_path) -> None:
    with rig.CapturingProvider(default="The lantern glowed.") as provider:
        daemon = rig.ServedDaemon(tmp_path / "home", provider=provider,
                                  env_extra={"VOOL_INSTALL_PROFILE": "hybrid-fallback"})
        try:
            daemon.start(timeout=180)
            assert daemon.certify(timeout=120).get("state") == "verified"
            base = daemon.base_url
            review = _call(base, "/api/addons", {"action": "local_install", "id": ENTRY, "approved": True})
            done = _call(base, "/api/addons", {"action": "install", "review_id": review["review_id"], "accepted": True})
            assert done["ok"] is True, done

            rows = _listed(base)
            assert len(rows) == 1 and rows[0]["enabled"] is True and rows[0]["source"] == "added", rows

            manager, browser = served_browser.launch_chromium()
            try:
                page = browser.new_page(viewport={"width": 1280, "height": 900})
                errors: list[str] = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.route("**/*", lambda route: route.continue_()
                           if route.request.url.startswith(base) else route.abort())
                page.goto(base + "/chat", wait_until="domcontentloaded")
                page.wait_for_selector("#input", timeout=15000)

                # The off switch the person uses, under My add-ons, Skills.
                page.evaluate("document.getElementById('pluginsBtn').click()")
                page.wait_for_selector("#addonNav button", timeout=15000)
                page.locator("#addonNav").get_by_role("button", name="My add-ons", exact=True).click()
                page.locator("#pluginsOverlay").get_by_role("button", name="Skills", exact=True).first.click()
                card = page.locator("#pluginsOverlay .plugin-card", has_text="Gentle workout plan")
                card.get_by_role("button", name="Disable").click()
                card.get_by_role("button", name="Enable").wait_for(timeout=15000)
                assert _listed(base)[0]["enabled"] is False
                page.locator("#pluginsClose").click()

                # Removing it by asking, as the person would.
                before = len(provider.payloads())
                page.locator("#input").fill("remove the gentle-workout-plan skill")
                page.locator("#send").click()
                # Removing a skill asks first, like every change to what VOOL loads.
                page.locator("#permOnce").click(timeout=60000)
                page.wait_for_function(
                    "() => [...document.querySelectorAll('.msg.assistant')].some(m => /Removed/.test(m.innerText))",
                    timeout=60000)
                reply = [text for text in page.locator(".msg.assistant").all_inner_texts() if "Removed" in text][-1]
                assert "gentle-workout-plan" in reply and "add it again from Browse" in reply, reply
                assert len(provider.payloads()) == before, "removing a skill needs no model call"
                assert errors == []
            finally:
                browser.close()
                manager.stop()

            assert _listed(base) == []
            plugins = _call(base, "/api/plugins")["plugins"]
            assert not any(SKILL in json.dumps(p.get("skills") or []) for p in plugins)
            entry = next(e for e in _call(base, "/api/addons")["entries"] if e.get("id") == ENTRY)
            assert entry["installed"] is False
        finally:
            daemon.stop()
