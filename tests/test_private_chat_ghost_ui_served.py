"""Served proof in a real browser: the ghost on private messages and the banner that ends private chat.

A real daemon (own home, own port, a scripted local model), real Chromium, the shipped chat page. The person
types "don't record this": both that message and VOOL's reply wear the ghost and the banner shows. The next
private exchange is marked too. Pressing the banner's "Turn recording back on" ends it; the banner goes and the
following exchange has no ghost. Reloading the chat shows only what was saved. Authored wording only.
"""
from __future__ import annotations

import json
import subprocess
import sys

import tests._reader_served_rig as rig
from tests.served_browser import launch_chromium

SECRET = "Quillmere"


def _send(page, text: str) -> None:
    page.fill("#input", text)
    page.click("#send")


def _wait_answers(page, count: int, timeout_ms: int = 180_000) -> None:
    page.wait_for_function(
        "n => document.querySelectorAll('#log .msg.assistant:not(.pending)').length >= n "
        "&& !document.querySelector('#send').textContent.includes('Queue')",
        arg=count,
        timeout=timeout_ms,
    )
    page.wait_for_timeout(400)


MARKS = """(() => [...document.querySelectorAll('#log .msg')].map(m => ({
  role: m.classList.contains('user') ? 'user' : 'assistant',
  ghost: !!m.querySelector('.msg-ghost'),
  text: (m.querySelector('.msg-text') || {}).textContent || '',
})))()"""


def test_private_messages_wear_the_ghost_and_the_banner_ends_it(tmp_path):
    def reply(body):
        from core.entity_ambiguity import AMBIGUITY_SYSTEM_PROMPT

        messages = body.get("messages") or []
        if any(AMBIGUITY_SYSTEM_PROMPT in str(m.get("content", "")) for m in messages):
            return json.dumps({"ambiguous": False, "referents": [], "clarification": ""})
        return "Sounds lovely."

    with rig.CapturingProvider(reply_fn=reply) as provider:
        daemon = rig.ServedDaemon(tmp_path / "home", provider=provider,
                                 env_extra={"VOOL_RAW_OLLAMA_API_URL": provider.base_url})
        daemon.register_provider()
        daemon.start()
        try:
            subprocess.run([sys.executable, "-c",
                "from storage.model_provider_manifest import list_provider_manifests; "
                "from tests._authorship_certification import certify_for_authorship; "
                "[certify_for_authorship(m) for m in list_provider_manifests() "
                "if m.provider_name == 'reader-stub' and m.model_name == 'reader-drive:stub']"
            ], cwd=rig.REPO_ROOT, env=daemon.env(), check=True, capture_output=True, timeout=30)
            manager, browser = launch_chromium()
            try:
                page = browser.new_page()
                errors: list[str] = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.route("**/api/tags", lambda route: route.fulfill(
                    content_type="application/json",
                    body=json.dumps({"models": [{"name": daemon.model}]}),
                ))
                page.goto(daemon.base_url + "/chat", wait_until="domcontentloaded")
                page.wait_for_selector("#input", timeout=30_000)
                page.click("#modelBtn")
                page.locator(f'.local-dyn[data-model="{daemon.model}"]').click()

                _send(page, "My cat is called Biscuit.")
                _wait_answers(page, 1)
                assert page.is_hidden("#privateBanner")

                _send(page, "don't record this")
                _wait_answers(page, 2)
                page.wait_for_selector("#privateBanner:not([hidden])", timeout=10_000)
                _send(page, f"The surprise party is at {SECRET} Hall.")
                _wait_answers(page, 3)

                marks = page.evaluate(MARKS)
                assert [m["ghost"] for m in marks] == [False, False, True, True, True, True], marks
                assert "Private chat is on" in marks[3]["text"], marks
                # A private bubble offers nothing that would save a copy of it.
                assert page.evaluate(
                    "() => [...document.querySelectorAll('#log .msg.is-private .msg-pin')]"
                    ".every(b => getComputedStyle(b).display === 'none')"
                )

                # A second tab opened on the private chat reads it back from the server: the private
                # messages are not there, and it says why instead of looking like lost messages.
                other = browser.new_page()
                other.on("pageerror", lambda e: errors.append(str(e)))
                other.route("**/api/tags", lambda route: route.fulfill(
                    content_type="application/json", body=json.dumps({"models": [{"name": daemon.model}]})))
                other.goto(daemon.base_url + "/chat", wait_until="domcontentloaded")
                other.locator(".session .s-title", has_text="Biscuit").first.click()
                other.wait_for_selector("#privateBanner:not([hidden])", timeout=30_000)
                other.wait_for_selector(".private-note", timeout=10_000)
                assert SECRET not in other.evaluate("() => document.getElementById('log').textContent")

                page.bring_to_front()
                page.click("#privateEnd")
                _wait_answers(page, 4)
                page.wait_for_selector("#privateBanner[hidden]", state="attached", timeout=10_000)
                marks = page.evaluate(MARKS)
                assert "Recording is back on" in marks[7]["text"], marks
                # The line under that reply: what follows is saved again.
                assert page.evaluate(
                    "() => { const d = document.querySelector('#log .private-divider');"
                    " return !!d && d.previousElementSibling === [...document.querySelectorAll('#log .msg')][7]; }"
                )

                # The second tab still shows the banner it last heard. Coming back to it re-reads the
                # server, and its next message, saved, wears no ghost.
                other.bring_to_front()
                other.evaluate("() => window.dispatchEvent(new Event('focus'))")
                other.wait_for_selector("#privateBanner[hidden]", state="attached", timeout=10_000)
                other.close()
                page.bring_to_front()
                _send(page, "My cat likes tuna.")
                _wait_answers(page, 5)
                marks = page.evaluate(MARKS)
                assert [m["ghost"] for m in marks] == [False, False, True, True, True, True, True, True, False, False], marks

                # Reload: the chat comes back from what was saved, and the private part is not in it.
                page.reload(wait_until="domcontentloaded")
                page.wait_for_function("() => document.querySelectorAll('#log .msg').length >= 4", timeout=30_000)
                texts = " ".join(m["text"] for m in page.evaluate(MARKS))
                assert "Biscuit" in texts and "tuna" in texts and SECRET not in texts, texts
                assert page.is_hidden("#privateBanner")
                assert errors == [], errors
            finally:
                browser.close()
                manager.stop()
        finally:
            daemon.stop()
