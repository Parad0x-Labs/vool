"""Layout laws for delivered artifacts, measured in real Chrome pixels.

The delivered answer's visual rhythm is part of the presentation contract: an answer
holding a table, a chart and a diagram must space them like the page spaces its other
blocks (the table rhythm), not drop a 46px void between consecutive artifacts. And a
wide diagram in a narrow window must stay readable: below 520px it keeps its natural
size and the frame scrolls sideways instead of scaling labels to ~8px.

Same harness shape as tests/test_chat_visuals_browser.py: the page's own renderer +
visual fragment, served locally, driven with real Chrome. No model or native claims.
"""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from core.vool_chat_page import _VOOL_CHAT_HTML, _page_fragments
from tests.test_chat_export_copy_ui import _slice

DEPOT_MD = """| Depot | Cartons |
| --- | --- |
| North depot | 144 |
| South depot | 216 |
| **Total** | **360** |

```chart
{"type":"bar","title":"Cartons per depot","data":{"labels":["North depot","South depot"],"datasets":[{"label":"Cartons","data":[144,216]}]}}
```

```mermaid
flowchart LR
  R[Receiving] --> I[Inspection]
  I --> N[North depot]
  I --> S[South depot]
```

North and South together hold 360 cartons.
"""


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        executable = os.environ.get("VOOL_BROWSER_BINARY")
        if not executable:
            candidate = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
            executable = str(candidate) if candidate.is_file() else None
        b = p.chromium.launch(headless=True, executable_path=executable)
        yield b
        b.close()


@pytest.fixture(scope="module")
def origin():
    source = _slice("function esc(", "\n  el.innerHTML = html;\n}", include_end=True)
    fragment = next((f for f in _page_fragments() if "window.VoolVisuals" in f), "")
    css = _VOOL_CHAT_HTML.split("<style>", 1)[1].split("</style>", 1)[0]
    layout = ('<!doctype html><meta name="viewport" content="width=device-width">'
              '<style>' + css + '</style><main id="main"><div id="log">'
              '<div class="msg assistant"><div id="answer" class="msg-text"></div></div></div></main>'
              '<script>' + source + '</script>' + fragment)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith('/chat-assets/'):
                from core.web.api.service import dispatch_get
                response = dispatch_get(path=self.path, query={}, runtime=None, model_name="")
                body, status, mime = response.body, response.status, response.content_type
            elif self.path == "/layout":
                body, status, mime = layout.encode(), 200, "text/html"
            else:
                body, status, mime = b'', 404, 'text/plain'
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def _render(browser, origin, md, width):
    page = browser.new_page(viewport={"width": width, "height": 1000})
    page.goto(origin + "/layout")
    page.evaluate("md => renderRichText(document.querySelector('#answer'),md)", md)
    page.wait_for_function("document.querySelectorAll('.vool-visual[data-state=ready]').length === 2", timeout=15000)
    return page


GAPS = r"""
() => {
  const R = Math.round;
  const box = (s) => { const el = document.querySelector(s); if (!el) return null;
    const b = el.getBoundingClientRect(); return { top: R(b.top), bottom: R(b.bottom) }; };
  return {
    tableToChart: box('iframe[title=Chart]').top - box('.chat-table-wrap').bottom,
    chartToDiagram: box('iframe[title=Diagram]').top - box('iframe[title=Chart]').bottom,
    diagramToFinal: (() => { const paras = [...document.querySelectorAll('#answer > p')];
      const last = paras[paras.length - 1]; if (!last) return null;
      return R(last.getBoundingClientRect().top) - box('iframe[title=Diagram]').bottom; })(),
    overflow: document.documentElement.scrollWidth - window.innerWidth,
  };
}
"""


@pytest.mark.parametrize("width", [1280, 700])
def test_consecutive_artifacts_share_one_vertical_rhythm(browser, origin, width):
    page = _render(browser, origin, DEPOT_MD, width)
    try:
        gaps = page.evaluate(GAPS)
        # The table's own bottom rhythm is ~20px; artifacts may not invent a much larger one
        # (46px was the measured defect). The "View source" disclosure row is content, so the
        # chart->diagram lane may exceed the pure-text rhythm by its height, not more.
        assert gaps["tableToChart"] <= 24, gaps
        assert gaps["chartToDiagram"] <= 34, gaps
        assert gaps["diagramToFinal"] <= 40, gaps
        assert gaps["overflow"] <= 0, gaps
    finally:
        page.close()


def test_narrow_window_diagram_keeps_readable_natural_size(browser, origin):
    page = _render(browser, origin, DEPOT_MD, 390)
    try:
        frame = page.frame_locator('iframe[title=Diagram]')
        svg = frame.locator("svg")
        box = svg.bounding_box()
        frame_box = page.locator('iframe[title=Diagram]').bounding_box()
        # Natural size: the SVG is wider than the frame and the frame scrolls sideways,
        # instead of scaling the labels down to an unreadable size.
        assert box["width"] > frame_box["width"] + 40, (box, frame_box)
        scrollable = frame.locator("body").evaluate("b => b.scrollWidth > b.clientWidth")
        assert scrollable, "narrow diagram frame must scroll instead of shrinking text"
        # Labels keep ~15px font in the SVG's own coordinate space (not the shrunken render).
        fontsize = svg.evaluate("el => el.querySelector('.nodeLabel, foreignObject div, span.nodeLabel') "
                                "? getComputedStyle(el.querySelector('.nodeLabel, foreignObject div, span.nodeLabel')).fontSize : '15px'")
        assert fontsize in ("15px", "16px"), fontsize
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "page must not scroll sideways"
    finally:
        page.close()


def test_wide_window_diagram_still_fits_the_answer_width(browser, origin):
    page = _render(browser, origin, DEPOT_MD, 1280)
    try:
        frame = page.locator('iframe[title=Diagram]').bounding_box()
        svg = page.frame_locator('iframe[title=Diagram]').locator("svg").bounding_box()
        assert svg["width"] <= frame["width"] + 2, (svg, frame)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    finally:
        page.close()


@pytest.mark.parametrize("diagram", [
    'flowchart LR\nA["Collection"] --> B["Lab"] --> C["Archive"]',
    'flowchart LR\nG["Gate"] --> D["Dock"] --> S["Shelves"]',
])
def test_diagram_frame_follows_its_content_height(browser, origin, diagram):
    before, remainder = DEPOT_MD.split("```mermaid\n", 1)
    _, after = remainder.split("```", 1)
    page = _render(browser, origin, before + "```mermaid\n" + diagram + "\n```" + after, 1100)
    try:
        frame = page.locator('iframe[title=Diagram]')
        svg = page.frame_locator('iframe[title=Diagram]').locator("svg")
        box, art = frame.bounding_box(), svg.bounding_box()
        assert box["height"] - art["height"] <= 32, (box, art)
        assert art["y"] + art["height"] <= box["y"] + box["height"], (box, art)
    finally:
        page.close()
