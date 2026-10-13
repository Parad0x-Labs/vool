"""An answer looks formatted while it streams, on the served chat page in a real browser.

Before: the bubble showed raw Markdown (``#``, ``**``, ``-``) until the last token, then popped
into formatting. Now the same renderer paints the partial answer a few times a second.

The page talks to a real daemon for everything except the one /api/chat stream, which is replaced
by a slow, hand-paced stream of the same NDJSON frames the server sends (content deltas, then the
response commit), so the test can look at the bubble between frames:

- mid-stream the bubble carries a real heading, list and bold, and no raw Markdown markers;
- a half-written chart is NOT hydrated while streaming (no visual job on partial source);
- after the commit the canonical text is painted, the streaming mark is gone, and the finished
  chart block is handed to the visuals layer as before;
- no batched repaint lands after the commit and overwrites it;
- unfinished markup (a half table row, an open ** or link) never flashes up raw.
"""
from __future__ import annotations

import hashlib
import json

from tests import _reader_served_rig as rig
from tests import served_browser

PART_1 = "# Lantern notes\n\n- **Bright** and calm\n- Easy to carry\n\n"
PART_2 = "```chart\n{\"type\": \"bar\", \"data\": {\"labels\": [\"a\""
PART_3 = ", \"b\"], \"datasets\": [{\"data\": [1, 2]}]}}\n```\n\nThat is all."
FINAL = PART_1 + PART_2 + PART_3

PACED_FETCH = """
({frames, commit}) => {
  const original = window.fetch;
  window.__gate = [];
  window.fetch = function(url, opts) {
    if (String(url) !== '/api/chat') return original.apply(this, arguments);
    const enc = new TextEncoder();
    const body = new ReadableStream({
      async start(controller) {
        for (const frame of frames) {
          await new Promise(resolve => window.__gate.push(resolve));
          controller.enqueue(enc.encode(JSON.stringify(frame) + '\\n'));
        }
        await new Promise(resolve => window.__gate.push(resolve));
        const turn = (view.run && view.run.turnId) || '';
        controller.enqueue(enc.encode(JSON.stringify({vool_response_commit: Object.assign({turn_id: turn}, commit)}) + '\\n'));
        controller.close();
      }
    });
    return Promise.resolve(new Response(body, {status: 200, headers: {'Content-Type': 'application/x-ndjson'}}));
  };
}
"""


def _release(page):
    page.wait_for_function("window.__gate && window.__gate.length > 0", timeout=10000)
    page.evaluate("window.__gate.shift()()")


def test_streaming_answer_is_formatted_and_settles_on_the_commit(tmp_path):
    with rig.CapturingProvider(default="unused") as provider:
        daemon = rig.ServedDaemon(tmp_path / "home", provider=provider,
                                  env_extra={"VOOL_INSTALL_PROFILE": "hybrid-fallback"})
        try:
            daemon.start(timeout=120)
            assert daemon.certify(timeout=60).get("state") == "verified"
            manager, browser = served_browser.launch_chromium()
            try:
                page = browser.new_page(viewport={"width": 1280, "height": 800})
                errors: list[str] = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.route("**/*", lambda route: route.continue_()
                           if route.request.url.startswith(daemon.base_url) else route.abort())
                page.goto(daemon.base_url + "/chat", wait_until="domcontentloaded")
                page.wait_for_selector("#input", timeout=10000)
                commit = {
                    "type": "response.commit", "version": 1, "revision": 1,
                    "canonical_content": FINAL,
                    "content_hash": "sha256:" + hashlib.sha256(FINAL.encode()).hexdigest(),
                    "display_metadata": {},
                }
                frames = [{"message": {"content": part}} for part in (PART_1, PART_2, PART_3)]
                # Unfinished pieces never flash up raw: a half-received table row, an open ** or a
                # half-typed link wait for the rest; finished text and code blocks are untouched.
                preview = page.evaluate("""() => [
                    streamingPreviewText('| a | b |\\n|---|---|\\n| Wick | Bu'),
                    streamingPreviewText('Some **Bri'),
                    streamingPreviewText('see [the doc](http://x'),
                    streamingPreviewText('done **bold** and'),
                    streamingPreviewText('```py\\nx = [1,'),
                ]""")
                assert preview == ["| a | b |\n|---|---|\n", "Some ", "see ", "done **bold** and", "```py\nx = [1,"], preview
                page.evaluate(PACED_FETCH, {"frames": frames, "commit": commit})

                page.locator("#input").fill("Tell me about lanterns.")
                page.locator("#send").click()
                bubble = page.locator(".msg.assistant").last.locator(".msg-text")

                _release(page)   # heading + list
                page.wait_for_function(
                    "() => { const t = [...document.querySelectorAll('.msg.assistant .msg-text')].at(-1);"
                    " return t && t.querySelector('h1'); }", timeout=5000)
                assert bubble.get_attribute("data-streaming") == "1"
                assert bubble.locator("h1").inner_text() == "Lantern notes"
                assert bubble.locator("li").count() == 2
                assert bubble.locator("strong").inner_text() == "Bright"
                text = bubble.inner_text()
                assert "#" not in text and "**" not in text

                _release(page)   # a half-written chart fence
                page.wait_for_function(
                    "() => [...document.querySelectorAll('.msg.assistant .msg-text')].at(-1).querySelector('.chat-code-wrap')",
                    timeout=5000)
                page.wait_for_timeout(300)
                assert bubble.locator(".vool-visual").count() == 0

                _release(page)   # the rest of the chart and the closing line
                page.wait_for_function(
                    "() => [...document.querySelectorAll('.msg.assistant .msg-text')].at(-1).textContent.includes('That is all.')",
                    timeout=5000)
                assert bubble.locator(".vool-visual").count() == 0

                _release(page)   # the server commit
                page.wait_for_function("view.run && view.run.released", timeout=10000)
                page.wait_for_timeout(400)   # longer than one batched repaint
                assert bubble.get_attribute("data-streaming") is None
                assert bubble.get_attribute("data-raw") == FINAL
                assert bubble.locator("h1").inner_text() == "Lantern notes"
                assert bubble.locator(".vool-visual").count() == 1
                assert not errors, errors
            finally:
                browser.close()
                manager.stop()
        finally:
            daemon.stop()
