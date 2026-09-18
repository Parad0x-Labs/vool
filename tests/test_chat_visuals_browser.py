"""Shipped Markdown parser + visual fragment, real browser, no model/native claims."""
import hashlib
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from core.vool_chat_page import _VOOL_CHAT_HTML, _page_fragments
from tests.test_chat_export_copy_ui import _slice

CASES = [
    ("chart", json.dumps({"type": "bar", "data": {"labels": ["Silver", "Gold"],
      "datasets": [{"label": "Allocation GBP", "data": [750, 1250]}]}})),
    ("chart", json.dumps({"type": "line", "data": {"labels": ["Mon", "Tue", "Wed"],
      "datasets": [{"label": "Reservoir litres", "data": [630, 420, 910]}]}})),
    ("mermaid", "flowchart LR\n  A[Request] --> B[Authorize]\n  B --> C[Execute]"),
    ("mermaid", "sequenceDiagram\n  participant S as Sensor\n  participant T as Tank\n  S->>T: Level 420 litres\n  T-->>S: Valve closed"),
]


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
    # All fragments are shipped by the product; select only this boundary so unrelated
    # UI boot APIs cannot turn this controlled renderer test into app acceptance.
    fragment = next((f for f in _page_fragments() if "window.VoolVisuals" in f), "")
    html = ('<!doctype html><meta name="viewport" content="width=device-width">'
            '<div id="answer" style="max-width:900px"></div><script>' + source + '</script>' + fragment)

    css = _VOOL_CHAT_HTML.split("<style>", 1)[1].split("</style>", 1)[0]
    layout = ('<style>' + css + '</style><main id="main"><div id="log">'
              '<div class="msg assistant"><div id="answer" class="msg-text"></div></div></div></main>'
              '<script>' + source + '</script>' + fragment)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/":
                body, status, mime = html.encode(), 200, "text/html"
            elif self.path == "/layout":
                body, status, mime = layout.encode(), 200, "text/html"
            elif self.path.startswith('/chat-assets/'):
                from core.web.api.service import dispatch_get
                response = dispatch_get(path=self.path, query={}, runtime=None, model_name="")
                body, status, mime = response.body, response.status, response.content_type
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


@pytest.mark.parametrize("lang,body", CASES)
@pytest.mark.parametrize("width", [1280, 390])
def test_visuals_render_actual_pixels_preserving_source(browser, origin, lang, body, width, tmp_path):
    page = browser.new_page(viewport={"width": width, "height": 844})
    requests = []
    page.on("request", lambda req: requests.append(req.url))
    try:
        page.goto(origin)
        assert page.evaluate("typeof window.VoolVisuals") == "object", "shipped visual renderer absent"
        page.evaluate("md => renderRichText(document.querySelector('#answer'), md)",
                      f"```{lang}\n{body}\n```")
        page.wait_for_selector('.vool-visual[data-state="ready"]', timeout=12000)
        frame = page.frame_locator(".vool-visual iframe")
        if lang == "chart":
            canvas = frame.locator("canvas")
            assert canvas.evaluate("el => { const p=el.getContext('2d').getImageData(0,0,el.width,el.height).data; return p.some((v,i)=>i%4===3&&v>0); }")
            frame.locator("summary").click()
            assert frame.locator("table").inner_text().find(json.loads(body)["data"]["labels"][0]) >= 0
        else:
            assert frame.locator("svg").count() == 1
            assert frame.locator("svg").bounding_box()["height"] > 30
            expected = "420" if "420" in body else "Authorize"
            assert expected in frame.locator("svg").text_content()
        assert page.locator(".chat-code-wrap").get_attribute("data-raw-code") == page.evaluate("encodeURIComponent", body)
        assert page.locator(".code-copy").count() == 1
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert all(url.startswith(origin + "/") or url == origin + "/" for url in requests)
        output = Path(os.environ.get('VOOL_VISUAL_EVIDENCE', str(tmp_path)))
        output.mkdir(parents=True, exist_ok=True)
        case_id = hashlib.sha256(body.encode()).hexdigest()[:8]
        page.screenshot(path=str(output / f"{lang}-{case_id}-{width}.png"), full_page=True)
    finally:
        page.close()


@pytest.mark.parametrize("lang,body", [
    ("chart", '{"type":"bar","data":{"labels":["a"],"datasets":[{"data":["NaN"]}]}}'),
    ("chart", '{"type":"bar","data":{"labels":["a"],"datasets":[{"data":[1]}]},"plugins":[]}'),
    ("mermaid", '%%{init: {"securityLevel":"loose"}}%%\nflowchart LR\nA-->B'),
    ("mermaid", '---\nconfig:\n  securityLevel: loose\n---\nflowchart LR\nA-->B'),
    ("mermaid", "Receiving --> Inspection --> North depot\nInspection --> South depot"),
])
def test_unsafe_or_invalid_input_remains_source_not_success(browser, origin, lang, body):
    page = browser.new_page()
    try:
        page.goto(origin)
        assert page.evaluate("typeof window.VoolVisuals") == "object", "shipped visual renderer absent"
        page.evaluate("md => renderRichText(document.querySelector('#answer'), md)", f"```{lang}\n{body}\n```")
        page.wait_for_selector('.vool-visual[data-state="error"]', timeout=5000)
        assert page.locator(".chat-code").is_visible()
        assert page.locator(".vool-visual iframe").count() == 0
    finally:
        page.close()


@pytest.mark.parametrize("marker", ["````", "~~~~"])
def test_quoted_visual_source_stays_code_in_the_real_renderer(browser, origin, marker):
    page = browser.new_page()
    body = "```mermaid\nflowchart LR\nCedar --> Birch\n```"
    try:
        page.goto(origin)
        page.evaluate("md => renderRichText(document.querySelector('#answer'), md)",
                      marker + "markdown\n" + body + "\n" + marker)
        assert page.locator(".chat-code-wrap").count() == 1
        assert page.locator(".chat-code").inner_text() == body
        assert page.locator(".chat-code-wrap").get_attribute("data-raw-code") == page.evaluate("encodeURIComponent", body)
        assert page.locator(".vool-visual").count() == 0
    finally:
        page.close()


@pytest.mark.parametrize('lang,body', [
    ('chart', json.dumps({'type':'doughnut', 'title':'</script><script>parent.pwned=1</script>',
     'background':'#f4f8ff', 'data':{'labels':['Passed','Rejected'],
     'datasets':[{'label':'Inspections','data':[95,5]}]}})),
    ('mermaid', 'flowchart LR\n A["<img src=\'https://example.invalid/leak\'>"] --> B[Done]\nclick B "https://example.invalid/nav"'),
])
def test_held_out_visuals_cannot_reach_parent_storage_or_network(browser, origin, lang, body):
    page = browser.new_page()
    escaped = []
    def guard(route):
        if route.request.url.startswith(origin + '/'):
            route.continue_()
        else:
            escaped.append(route.request.url)
            route.abort()
    page.route('**/*', guard)
    try:
        page.goto(origin)
        page.evaluate("md => renderRichText(document.querySelector('#answer'), md)", f"```{lang}\n{body}\n```")
        page.wait_for_selector('.vool-visual[data-state="ready"]', timeout=12000)
        frame = page.locator('.vool-visual iframe')
        assert frame.get_attribute('sandbox') == 'allow-scripts'
        realm = frame.content_frame
        assert realm.locator('body').evaluate("()=>{try{parent.document.body;return false}catch(e){return e.name==='SecurityError'}}")
        assert realm.locator('body').evaluate("()=>{try{localStorage.getItem('credential');return false}catch(e){return e.name==='SecurityError'}}")
        assert page.evaluate('window.pwned === undefined')
        assert not escaped
        if lang == 'chart':
            assert realm.locator('body').evaluate("e=>getComputedStyle(e).backgroundColor") == 'rgb(244, 248, 255)'
            realm.locator('summary').click()
            assert '95' in realm.locator('table').inner_text()
        else:
            assert realm.locator('a').count() == 0
    finally:
        page.close()


def test_frames_release_when_history_is_removed_or_offscreen(browser, origin):
    page = browser.new_page(viewport={'width':900,'height':700})
    try:
        page.goto(origin)
        lang, body = CASES[0]
        page.evaluate("md => renderRichText(document.querySelector('#answer'), md)", f"```{lang}\n{body}\n```")
        page.wait_for_selector('.vool-visual[data-state="ready"]')
        page.evaluate("document.querySelector('#answer').style.marginTop='4000px'")
        page.wait_for_selector('.vool-visual iframe', state='detached')
        page.evaluate("document.querySelector('#answer').style.marginTop='0'")
        page.wait_for_selector('.vool-visual[data-state="ready"]')
        page.evaluate("renderRichText(document.querySelector('#answer'),'Plain replacement')")
        page.wait_for_function('window.frames.length === 0')
        assert page.locator('#answer').inner_text() == 'Plain replacement'
    finally:
        page.close()


@pytest.mark.parametrize("width", [1280, 390])
@pytest.mark.parametrize("labels,values,diagram", [
    (["North depot", "South depot"], [144, 216], 'flowchart LR\nR[Receiving] --> I[Inspection]\nI --> N[North depot]\nI --> S[South depot]'),
    (["Amber", "Violet"], [18, 42], 'flowchart LR\nC[Collection] --> L[Lab] --> A[Archive]'),
])
def test_answer_layout_uses_available_width_and_the_chat_theme(browser, origin, width, labels, values, diagram, tmp_path):
    page = browser.new_page(viewport={"width": width, "height": 1000})
    chart = {"type": "bar", "data": {"labels": labels, "datasets": [{"label": "Count", "data": values}]}}
    md = ("| Location | Count |\n| --- | --- |\n" + "\n".join(f"| {label} | {value} |" for label,value in zip(labels, values, strict=True))
          + "\n\n```chart\n" + json.dumps(chart) + "\n```\n\n```mermaid\n" + diagram + "\n```\n\nThe supplied counts are shown above.")
    try:
        page.goto(origin + "/layout")
        page.evaluate("md => renderRichText(document.querySelector('#answer'),md)", md)
        page.wait_for_function("document.querySelectorAll('.vool-visual[data-state=ready]').length === 2")
        answer = page.locator('#answer').bounding_box()
        assert answer['width'] >= min(720, width-48), answer
        table = page.locator('.chat-table').bounding_box()
        assert table['width'] >= answer['width'] - 2
        chart_frame = page.frame_locator('iframe[title=Chart]')
        assert chart_frame.locator('body').evaluate("e => getComputedStyle(e).backgroundColor") == 'rgb(16, 18, 22)'
        assert page.locator('iframe[title=Chart]').bounding_box()['height'] < 340
        chart_frame.locator('summary').click()
        for value in values:
            assert str(value) in chart_frame.locator('table').inner_text()
        assert page.frame_locator('iframe[title=Diagram]').locator('svg').is_visible()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert page.locator('.code-copy:visible').count() == 0
        page.locator('.vool-visual-source summary').first.click()
        assert page.locator('.code-copy:visible').count() == 1
        page.locator('.vool-visual-source summary').first.click()
        chart_frame.locator('summary').click()
        output=Path(os.environ.get('VOOL_VISUAL_EVIDENCE', str(tmp_path)))
        output.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(output / f'layout-{labels[0].replace(" ","-")}-{width}.png'),full_page=True)
    finally:
        page.close()
