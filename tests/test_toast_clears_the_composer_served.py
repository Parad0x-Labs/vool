"""A confirmation toast on the served chat page covers none of the composer's controls and goes on its own.

sls on the installed app (CUT 60): "Approved for this task. Later edit batches still need their own review." sat over
the mode / attach chip row under it and stayed up 6.5 s. The toast now sits above the whole composer area, however
tall it is (approval bar open or not), and a plain confirmation hides within 4 s; one carrying an action (Undo) keeps
its longer time so the button can be reached.
"""
from __future__ import annotations

from tests import _reader_served_rig as rig
from tests import served_browser

TASK_TOAST = "Approved for this task. Later edit batches still need their own review."

OVERLAPS = """() => {
  const t = document.getElementById('nToast').getBoundingClientRect();
  const hit = (r) => r.width && r.height && !(r.right <= t.left || r.left >= t.right || r.bottom <= t.top || r.top >= t.bottom);
  const foot = document.getElementById('input').closest('footer');
  return { overFooter: t.bottom > foot.getBoundingClientRect().top,
           covered: [...foot.querySelectorAll('button, textarea, input, select, [role=button]')]
             .filter((el) => hit(el.getBoundingClientRect())).map((el) => el.id || el.className) };
}"""
SHOWN = "() => document.getElementById('nToast').classList.contains('show')"


def test_the_toast_sits_above_the_composer_and_goes_on_its_own(tmp_path):
    with rig.CapturingProvider(default="The lantern glowed.") as provider:
        daemon = rig.ServedDaemon(tmp_path / "home", provider=provider)
        try:
            daemon.start(timeout=120)
            manager, browser = served_browser.launch_chromium()
            try:
                page = browser.new_page(viewport={"width": 1100, "height": 720})
                errors: list[str] = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(daemon.base_url + "/chat", wait_until="domcontentloaded")
                page.wait_for_selector("#input", timeout=10000)
                for bar_open in (False, True):
                    # The approval bar open makes the composer area much taller: the toast must still clear it.
                    page.evaluate("(open) => { document.getElementById('permBar').hidden = !open; }", bar_open)
                    page.evaluate("(text) => toast(text)", TASK_TOAST)
                    got = page.evaluate(OVERLAPS)
                    assert got == {"overFooter": False, "covered": []}, (bar_open, got)
                    assert page.evaluate(SHOWN)
                    page.wait_for_timeout(4000)
                    assert not page.evaluate(SHOWN), "a plain confirmation is still up after 4 s"
                # One with an action stays long enough to press it.
                page.evaluate("() => toast('Chat deleted.', { label: 'Undo', run: () => {} })")
                page.wait_for_timeout(4000)
                assert page.evaluate(SHOWN)
                assert errors == []
            finally:
                browser.close()
                manager.stop()
        finally:
            daemon.stop()
