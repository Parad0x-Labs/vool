"""Persisted, policy-gated transcript to an actual readable PDF; no live model."""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path

import pytest
from pypdf import PdfReader

from tests import test_chat_export_api as export
from tests import test_chat_visuals_browser as visual_browser
from tests.test_chat_export_copy_ui import HTML

_isolated_home = export._isolated_home
browser = visual_browser.browser


@pytest.mark.parametrize('question,answer,expected', [
    ('Deliver the portfolio result in PDF.',
     '# Portfolio\n\n| Asset | USD |\n|---|---:|\n| GBP | 3,125.00 |\n| Silver | 5,156.25 |\n| Platinum | 4,218.75 |',
     ['GBP','3,125.00','Silver','5,156.25','Platinum','4,218.75']),
    ('Export the depot inventory as a portable document.',
     '# Depot inventory\n\n| Depot | Cartons |\n|---|---:|\n| West | 504 |\n| East | 280 |\n\nReserve: 17 cartons.',
     ['West','504','East','280','Reserve: 17 cartons.']),
])
def test_export_delivers_pdf_with_exact_source_values(question,answer,expected,tmp_path):
    export._seed(user=question,assistant=answer)
    response = export._export(format='pdf')
    assert response.status == 200
    assert response.content_type == 'application/pdf'
    assert response.body.startswith(b'%PDF-')
    reader = PdfReader(BytesIO(response.body))
    text = '\n'.join(page.extract_text() for page in reader.pages)
    assert question in text
    for value in expected:
        assert value in text
    assert '.pdf' in response.headers['Content-Disposition']
    output = Path(os.environ.get('VOOL_PDF_EVIDENCE',str(tmp_path)))
    output.mkdir(parents=True,exist_ok=True)
    (output / ('portfolio.pdf' if 'portfolio' in question else 'depot.pdf')).write_bytes(response.body)


def test_pdf_export_does_not_open_an_external_export_door():
    response = export._get('/api/chat/export?session='+export.SESSION+'&format=pdf',client_host='10.9.8.7')
    assert response.status == 403


def pdf_text(response):
    assert response.status == 200, response.body
    return '\n'.join(page.extract_text() for page in PdfReader(BytesIO(response.body)).pages)


def test_pdf_keeps_the_existing_availability_and_session_gate():
    from core.finalization import AVAILABILITY_WITHHELD, set_availability

    secret = 'WITHHELD-SOURCE-7319'
    commit = export._admit_finalize(secret,request_id='pdf-private')
    assert set_availability(commit['finalization_id'],AVAILABILITY_WITHHELD,reason='test')
    with export._request_scope('pdf-private'):
        export._seed(user='Private request',assistant=secret)
    export._seed(session=export.OTHER,assistant='OTHER-SESSION-9271')
    text = pdf_text(export._export(format='pdf'))
    assert secret not in text and 'OTHER-SESSION-9271' not in text
    assert 'unavailable by privacy policy' in text


@pytest.mark.parametrize('source',[
    '![image](https://example.invalid/private.png)',
    '```mermaid\nsequenceDiagram\n  participant S\n  S->>T: level\n```',
    'Unsupported glyph: \U0001f680',
])
def test_unsupported_content_refuses_instead_of_shipping_an_incomplete_pdf(source):
    export._seed(assistant=source)
    response = export._export(format='pdf')
    assert response.status == 422
    assert json.loads(response.body)['error'] == 'pdf_export_refused'
    assert not response.body.startswith(b'%PDF-')


def test_pdf_source_ceiling_refuses_without_a_partial_download(monkeypatch):
    from core.presentation import render_pdf

    export._seed(assistant='A complete but deliberately oversized document.')
    monkeypatch.setattr(render_pdf,'MAX_SOURCE_BYTES',20)
    assert export._export(format='pdf').status == 422


@pytest.mark.parametrize('body',[
    '{"type":"bar","data":{"labels":["A"],"datasets":[{"data":[12]}]},"plugins":[]}',
    '{"type":"line","data":{"labels":["B"],"datasets":[{"data":[NaN]}]}}',
])
def test_invalid_chart_is_a_typed_pdf_refusal(body):
    export._seed(assistant='```chart\n'+body+'\n```')
    response = export._export(format='pdf')
    assert response.status == 422
    assert json.loads(response.body)['error']=='pdf_export_refused'


def test_pdf_keeps_two_link_destinations_and_treats_markup_as_text():
    export._seed(assistant='[One](https://one.invalid) and [Two](https://two.invalid).\n\n<script>NO_EXECUTION</script>')
    text = pdf_text(export._export(format='pdf'))
    assert 'https://one.invalid' in text and 'https://two.invalid' in text
    assert '<script>NO_EXECUTION</script>' in text


def test_pdf_table_paginates_without_losing_rows(tmp_path):
    answer = '| Item | Quantity |\n|---|---:|\n' + '\n'.join(f'| Inventory-{i:03} | {i*13} |' for i in range(120))
    export._seed(assistant=answer)
    response = export._export(format='pdf')
    reader = PdfReader(BytesIO(response.body))
    assert len(reader.pages)>2
    text = '\n'.join(page.extract_text() for page in reader.pages)
    for i in range(120):
        assert f'Inventory-{i:03}' in text
    output = Path(os.environ.get('VOOL_PDF_EVIDENCE',str(tmp_path)))
    output.mkdir(parents=True,exist_ok=True)
    (output/'inventory.pdf').write_bytes(response.body)


@pytest.mark.parametrize('label,count',[('Inventory',120),('Measurement',85)])
def test_pdf_starts_long_tables_on_the_available_first_page(label,count):
    answer = '| Item | Value |\n|---|---:|\n'+'\n'.join(f'| {label}-{i:03} | {i*7} |' for i in range(count))
    export._seed(assistant=answer)
    reader = PdfReader(BytesIO(export._export(format='pdf').body))
    assert label+'-000' in reader.pages[0].extract_text()


@pytest.mark.parametrize('kind',['bar','line','pie','doughnut'])
def test_pdf_chart_keeps_exact_series_and_real_vector_content(kind,tmp_path):
    spec = {'type':kind,'data':{'labels':['North','South'],'datasets':[{'label':'Litres','data':[12.75,31.125]}]}}
    export._seed(assistant='```chart\n'+json.dumps(spec)+'\n```')
    response = export._export(format='pdf')
    text = pdf_text(response)
    assert all(value in text for value in ['North','South','12.75','31.125','Litres'])
    assert b' re' in PdfReader(BytesIO(response.body)).pages[-1].get_contents().get_data()
    output = Path(os.environ.get('VOOL_PDF_EVIDENCE',str(tmp_path)))
    output.mkdir(parents=True,exist_ok=True)
    (output / ('chart-'+kind+'.pdf')).write_bytes(response.body)


@pytest.mark.parametrize('kind,values',[('bar',[18,47]),('line',[33,12])])
def test_pdf_keeps_explicit_chart_color_and_background(kind,values):
    from pypdf.generic import ContentStream

    spec = {'type':kind,'background':'#eaf4f0','data':{'labels':['A','B'],
            'datasets':[{'label':'Units','data':values,'color':'#123456'}]}}
    export._seed(assistant='```chart\n'+json.dumps(spec)+'\n```')
    reader = PdfReader(BytesIO(export._export(format='pdf').body))
    operations = [operation for page in reader.pages for operation in ContentStream(page.get_contents(),reader).operations]
    colors = [list(map(float,args)) for args,operator in operations if operator in (b'rg',b'RG')]
    assert any(all(abs(a-b)<1e-5 for a,b in zip(rgb,[18/255,52/255,86/255],strict=True)) for rgb in colors)
    assert any(all(abs(a-b)<1e-5 for a,b in zip(rgb,[234/255,244/255,240/255],strict=True)) for rgb in colors)


@pytest.mark.parametrize('axis,values',[('x',[12.75,31.125]),('y',[5.5,17.25])])
def test_pdf_bar_geometry_represents_values_relative_to_zero(axis,values):
    from pypdf.generic import ContentStream

    spec = {'type':'bar','axis':axis,'data':{'labels':['First','Second'],
            'datasets':[{'data':values,'color':'#123456'}]}}
    export._seed(assistant='```chart\n'+json.dumps(spec)+'\n```')
    reader = PdfReader(BytesIO(export._export(format='pdf').body))
    rectangles = []
    color,stack = None,[]
    for page in reader.pages:
        for args,op in ContentStream(page.get_contents(),reader).operations:
            if op==b'q':
                stack.append(color)
            elif op==b'Q':
                color = stack.pop()
            elif op==b'rg':
                color = list(map(float,args))
            elif op==b're' and color and all(abs(a-b)<1e-5 for a,b in zip(color,[18/255,52/255,86/255],strict=True)):
                rectangles.append(list(map(float,args)))
    assert len(rectangles)==2
    sizes = [r[3] if axis=='x' else r[2] for r in rectangles]
    assert all(size>0 for size in sizes)
    assert sizes[0]/sizes[1] == pytest.approx(values[0]/values[1],rel=1e-5)


@pytest.mark.parametrize('kind',['pie','doughnut'])
def test_pdf_circular_charts_are_not_stretched_into_ellipses(kind,monkeypatch):
    from reportlab.graphics.charts.doughnut import Doughnut
    from reportlab.graphics.charts.piecharts import Pie

    cls = Pie if kind=='pie' else Doughnut
    original = cls.draw
    shapes = []
    def draw(self):
        shapes.append((self.width,self.height))
        return original(self)
    monkeypatch.setattr(cls,'draw',draw)
    spec = {'type':kind,'data':{'labels':['Remaining','Used'],'datasets':[{'data':[34,66]}]}}
    export._seed(assistant='```chart\n'+json.dumps(spec)+'\n```')
    assert export._export(format='pdf').status==200
    assert shapes and all(width==height for width,height in shapes)


def test_shipped_export_controls_download_the_servers_actual_pdf(browser):
    export._seed(user='Export inventory.',assistant='Available stock: 763 cartons.')
    markup = HTML[HTML.index('<div id="exportOverlay"'):HTML.index('<div id="bugReportOverlay"')]
    script = HTML[HTML.index('(function wireExport()'):HTML.index('xpCloseEl.addEventListener')]
    page_source = ('<!doctype html><button id="exportBtn">Export chat</button>'+markup+
                   '<script>const displayedChat='+json.dumps(export.SESSION)+
                   '; function chatTitleFor(){return "Inventory";}'+script+'</script>')

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path=='/':
                body,mime,status,headers = page_source.encode(),'text/html',200,{}
            else:
                response = export._get(self.path)
                body,mime,status,headers = response.body,response.content_type,response.status,response.headers
            self.send_response(status)
            self.send_header('Content-Type',mime)
            for name,value in headers.items():
                self.send_header(name,value)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self,*_):
            pass

    server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
    worker = threading.Thread(target=server.serve_forever,daemon=True)
    worker.start()
    page = browser.new_page(accept_downloads=True)
    try:
        page.goto(f'http://127.0.0.1:{server.server_port}')
        page.locator('#exportBtn').click()
        page.locator('#exportFormat').select_option('pdf')
        with page.expect_download() as download:
            page.locator('#exportGo').click()
        file = download.value
        assert file.suggested_filename.endswith('.pdf')
        reader = PdfReader(file.path())
        assert 'Available stock: 763 cartons.' in '\n'.join(p.extract_text() for p in reader.pages)
    finally:
        page.close()
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


@pytest.mark.parametrize('fmt', ['md', 'txt', 'pdf'])
@pytest.mark.parametrize('outcome', ['save', 'cancel', 'refuse'])
def test_native_transcript_export_uses_save_bridge_without_blob_navigation(browser, fmt, outcome):
    markup = HTML[HTML.index('<div id="exportOverlay"'):HTML.index('<div id="bugReportOverlay"')]
    script = HTML[HTML.index('(function wireExport()'):HTML.index('xpCloseEl.addEventListener')]
    response = {'ok': True} if outcome == 'save' else ({'ok': False, 'cancelled': True} if outcome == 'cancel' else {'ok': False, 'message': 'Export unavailable'})
    setup = '''
      let displayedChat = 'chosen-chat';
      function chatTitleFor(){return 'Chosen';}
      window.saved = [];
      window.pywebview = {api:{save_chat_export:async (...args)=>{window.saved.push(args);return RESULT;}}};
      window.fetch = async (url) => {
        if (!url.includes('preview=1')) throw new Error('browser export path used');
        return {ok:true,json:async()=>({ok:true,message_count:2,turns:1,attachment_items:1,artifact_cards:0,complete:true})};
      };
    '''.replace('RESULT', json.dumps(response))
    page = browser.new_page()
    try:
        page.set_content('<!doctype html><meta charset="utf-8"><button id="exportBtn">Export</button>' + markup + '<script>' + setup + script + '</script>')
        page.locator('#exportBtn').click()
        page.locator('#exportFormat').select_option(fmt)
        page.locator('#exportTimestamps').check()
        page.locator('#exportAttachments').uncheck()
        page.evaluate("displayedChat = 'different-chat'")
        page.locator('#exportGo').click()
        assert page.evaluate('window.saved') == [['chosen-chat', fmt, True, False]]
        assert page.url == 'about:blank'
        assert page.locator('#exportGo').is_enabled()
        if outcome == 'save':
            assert page.locator('#exportOverlay').is_hidden()
        else:
            assert page.locator('#exportOverlay').is_visible()
            assert page.locator('#exportStatus').inner_text() == ('Export cancelled.' if outcome == 'cancel' else 'Export unavailable')
    finally:
        page.close()


def test_native_export_serializes_save_and_cancel_keeps_the_chat(browser):
    markup = HTML[HTML.index('<div id="exportOverlay"'):HTML.index('<div id="bugReportOverlay"')]
    script = HTML[HTML.index('(function wireExport()'):HTML.index('xpCloseEl.addEventListener')]
    setup = """
      const displayedChat = 'chosen-chat';
      function chatTitleFor(){return 'Chosen';}
      window.saved = [];
      window.pywebview = {api:{save_chat_export:(...args)=>{
        window.saved.push(args);return new Promise(resolve=>window.finishSave=resolve);
      }}};
      window.fetch = async () => ({ok:true,json:async()=>({ok:true,excluded:[]})});
    """
    page = browser.new_page()
    try:
        page.set_content('<!doctype html><button id="exportBtn">Export</button>' + markup + '<script>' + setup + script + '</script>')
        page.locator('#exportBtn').click()
        page.locator('#exportGo').click()
        assert page.locator('#exportGo').is_disabled()
        page.locator('#exportGo').dispatch_event('click')
        page.locator('#exportCancel').click()
        assert page.evaluate('window.saved.length') == 1
        assert page.locator('#exportOverlay').is_visible()
        page.evaluate('window.finishSave({ok:false,cancelled:true})')
        assert page.locator('#exportGo').is_enabled()
        assert page.locator('#exportStatus').inner_text() == 'Export cancelled.'
        page.locator('#exportCancel').click()
        assert page.locator('#exportOverlay').is_hidden()
        assert page.url == 'about:blank'
    finally:
        page.close()


@pytest.mark.parametrize("source", [
    "# Šiauliai → orchard\n\nMarker: violet-kite-927\n\nRésumé: 42 samples — readback only.",
    "# Łódź — Δοκιμή\n\nČeský přehled: 73 položek ← Ω.",
])
def test_unicode_pdf_preserves_headings_and_body_characters(source, tmp_path):
    export._seed(assistant=source)
    response = export._export(format="pdf")
    text = pdf_text(response)
    for line in source.splitlines():
        if line.strip():
            assert line.removeprefix("# ") in text
    (tmp_path / "unicode.pdf").write_bytes(response.body)
