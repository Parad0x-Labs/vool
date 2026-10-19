"""A long answer streams without re-rendering everything it already showed, on the served chat page.

Before: every live repaint rendered the whole answer so far, so a 95k-character answer spent about a
third of the page's time re-rendering text that had not changed. Now the settled part (everything
above the last finished block boundary) is rendered once and kept, and each paint renders only the
rest. The bubble must look exactly as it did: the pieces render to the same markup as the whole.

- Mid-stream, after every frame of a long answer, the bubble's markup equals a full render of the
  same partial text -- across lists that resume after commentary, code blocks holding blank lines
  and "#" lines, tables, quotes and nested items.
- The settled part is really kept: its nodes are the same objects from one paint to the next.
- Many random documents cut at random points render identically piece-wise and whole.
- The commit still paints the canonical answer.
"""
from __future__ import annotations

import hashlib

from tests import _reader_served_rig as rig
from tests import served_browser

BLOCK = (
    "## Step group\n\n"
    "1. lazy one\n1. lazy two\n\n"
    "Some words between the steps, with **bold**, `code` and a [link](https://example.com/a).\n\n"
    "1. resumed step\n1. and another\n\n"
    "```python\nx = 1\n\n# not a heading\n\ny = 2\n```\n\n"
    "- bullet\n  wrapped line\n\n- loose bullet\n  - nested\n\n"
    "| a | b |\n|---|---|\n| 1 | 2 |\n\n"
    "> a quote\n> more of it\n\n"
)
LONG = BLOCK * 30   # ~11k characters: long enough that the settled part is kept

STREAM_FETCH = """
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

FUZZ = r"""() => {
  const blocks = [
    '# Title\n', 'Para with **bold** and `code`.\nSecond line.\n', '1. one\n2. two\n', '1. lazy\n1. lazy\n',
    'Words between lists.\n', '- bullet\n  continued\n- second\n\n- loose\n', '  - nested\n', '> quote\n',
    '| a | b |\n|---|---|\n| 1 | 2 |\n', '```py\nx = 1\n\n\n# comment\n```\n', '````\n```\ninner\n```\n````\n',
    '~~~\ntilde\n~~~\n', '---\n', '* star item\n', '**Bold start** text\n', '10. ten\n11. eleven\n',
    '```json title="x"\n{}\n```\n', '+ plus item\n', '`local | m | 5 tok`\n',
  ];
  let seed = 11; const rnd = () => (seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648;
  let checks = 0;
  for (let d = 0; d < 25; d++) {
    let t = '';
    while (t.length < 7000) { t += blocks[Math.floor(rnd() * blocks.length)]; if (rnd() < 0.8) t += '\n'; }
    const run = { text: '', textEl: document.createElement('div') };
    const ref = document.createElement('div');
    for (let i = 0; i < t.length;) {
      i = Math.min(t.length, i + 1 + Math.floor(rnd() * 150));
      run.text = t.slice(0, i);
      paintStreamingText(run);
      renderRichText(ref, streamingPreviewText(run.text));
      checks += 1;
      if (run.textEl.innerHTML !== ref.innerHTML) return {checks, bad: JSON.stringify(t.slice(Math.max(0, i - 300), i))};
    }
  }
  return {checks, bad: ''};
}"""


def test_long_answer_streams_piecewise_and_looks_the_same(tmp_path):
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

                fuzz = page.evaluate(FUZZ)
                assert fuzz["bad"] == "", fuzz
                assert fuzz["checks"] > 500

                step = 271   # cuts land mid-line, mid-fence and mid-table
                parts = [LONG[i:i + step] for i in range(0, len(LONG), step)]
                commit = {
                    "type": "response.commit", "version": 1, "revision": 1,
                    "canonical_content": LONG,
                    "content_hash": "sha256:" + hashlib.sha256(LONG.encode()).hexdigest(),
                    "display_metadata": {},
                }
                page.evaluate(STREAM_FETCH, {"frames": [{"message": {"content": p}} for p in parts],
                                             "commit": commit})
                page.locator("#input").fill("Give me the long version.")
                page.locator("#send").click()

                sent = ""
                kept_same = 0
                for part in parts:
                    page.wait_for_function("window.__gate && window.__gate.length > 0", timeout=10000)
                    marker = page.evaluate("""() => { const t = [...document.querySelectorAll('.msg.assistant .msg-text')].at(-1);
                        const k = t && t.firstElementChild; if (k) k.__mark = (k.__mark || 0) + 1; return !!k; }""")
                    page.evaluate("window.__gate.shift()()")
                    sent += part
                    # Wait out the batched repaint, then compare with a full render of the same text.
                    page.wait_for_function(
                        "(n) => view.run && view.run.text && view.run.text.length >= n && !view.run.streamPaintTimer",
                        arg=len(sent), timeout=10000)
                    same = page.evaluate("""() => {
                        const t = [...document.querySelectorAll('.msg.assistant .msg-text')].at(-1);
                        const ref = document.createElement('div');
                        renderRichText(ref, streamingPreviewText(view.run.text));
                        return {equal: t.innerHTML === ref.innerHTML, streaming: t.dataset.streaming,
                                kept: !!(t.firstElementChild && t.firstElementChild.__mark)};
                    }""")
                    assert same["equal"], (len(sent), same)
                    assert same["streaming"] == "1"
                    if marker and same["kept"] and len(sent) > 4000:
                        kept_same += 1
                # Once the answer is long, the first block is the same node paint after paint.
                assert kept_same > len(parts) // 2, kept_same

                page.wait_for_function("window.__gate && window.__gate.length > 0", timeout=10000)
                page.evaluate("window.__gate.shift()()")   # the server commit
                page.wait_for_function("view.run && view.run.released", timeout=10000)
                page.wait_for_timeout(400)
                final = page.evaluate("""() => {
                    const t = [...document.querySelectorAll('.msg.assistant .msg-text')].at(-1);
                    const ref = document.createElement('div'); renderRichText(ref, view.run.text);
                    return {equal: t.innerHTML.startsWith(ref.innerHTML), streaming: t.dataset.streaming || ''};
                }""")
                assert final == {"equal": True, "streaming": ""}, final
                assert errors == []
            finally:
                browser.close()
                manager.stop()
        finally:
            daemon.stop()
