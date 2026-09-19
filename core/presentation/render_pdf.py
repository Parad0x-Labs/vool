"""Local PDF projection of existing content; no fetches, scripts, or generated facts."""
from __future__ import annotations

import io
import threading
from pathlib import Path
from xml.sax.saxutils import escape

from core.presentation.chart_payload import chart_payload, parse_chart_body
from core.presentation.model import Chart, Code, Heading, Paragraph, RenderDocument, Span, Table

MAX_SOURCE_BYTES = 256 * 1024
MAX_PDF_BYTES = 4 * 1024 * 1024
MAX_PAGES = 100
_FONT_LOCK = threading.Lock()


class PdfRefused(ValueError):
    """No partial document may be delivered after a representation failure."""


def markdown_document(source: str) -> RenderDocument:
    from markdown_it import MarkdownIt

    if len(source.encode('utf-8')) > MAX_SOURCE_BYTES:
        raise PdfRefused('PDF source exceeds the bounded document limit')
    tokens = MarkdownIt('commonmark', {'html': False}).enable('table').parse(source)
    blocks, lists = [], []
    prefix = ''
    heading = 0
    table = None
    row = None
    for token in tokens:
        kind = token.type
        if kind in ('bullet_list_open','ordered_list_open'):
            lists.append([kind,int(token.attrGet('start') or 1)])
        elif kind in ('bullet_list_close','ordered_list_close'):
            lists.pop()
        elif kind == 'list_item_open':
            parent = lists[-1]
            prefix = '  ' * (len(lists)-1) + (str(parent[1])+'. ' if parent[0]=='ordered_list_open' else '- ')
            parent[1] += 1
        elif kind == 'heading_open':
            heading = int(token.tag[1:])
        elif kind == 'table_open':
            table = []
        elif kind == 'tr_open':
            row = []
        elif kind == 'tr_close':
            table.append(row)
        elif kind == 'table_close':
            blocks.append(Table(headers=table[0],rows=table[1:]))
            table = row = None
        elif kind == 'inline':
            parts = []
            links = []
            for child in token.children or ():
                if child.type == 'image':
                    raise PdfRefused('Inline images require an explicitly bound image source; nothing was fetched')
                if child.type in ('text','code_inline'):
                    parts.append(child.content)
                elif child.type in ('softbreak','hardbreak'):
                    parts.append('\n')
                elif child.type == 'link_open':
                    links.append(str(child.attrGet('href') or ''))
                elif child.type == 'link_close':
                    # Link destinations remain visible without active PDF actions.
                    parts.append(' ('+links.pop()+')')
            text = ''.join(parts)
            if table is not None:
                row.append(text)
            elif heading:
                blocks.append(Heading(text,level=heading))
                heading = 0
            else:
                blocks.append(Paragraph([Span(prefix+text)]))
                prefix = ''
        elif kind in ('fence','code_block'):
            blocks.append(Code(token.content,lang=token.info.strip().lower()))
        elif kind == 'hr':
            blocks.append(Paragraph([Span('---')]))
    return RenderDocument(blocks=blocks)


class _BoundedBuffer(io.BytesIO):
    def write(self, value):
        if self.tell() + len(value) > MAX_PDF_BYTES:
            raise PdfRefused('PDF exceeds the single-file byte limit')
        return super().write(value)


def _font():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    with _FONT_LOCK:
        if 'VoolPDF' not in pdfmetrics.getRegisteredFontNames():
            root = Path(__file__).parent / 'fonts'
            pdfmetrics.registerFont(TTFont('VoolPDF',str(root/'DejaVuSans.ttf')))
            pdfmetrics.registerFont(TTFont('VoolPDFBold',str(root/'DejaVuSans-Bold.ttf')))
    return pdfmetrics.getFont('VoolPDF')


def render_document(document: RenderDocument, *, title: str = 'VOOL') -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.platypus import CondPageBreak, LongTable, SimpleDocTemplate, Spacer, TableStyle
    from reportlab.platypus import Paragraph as PdfParagraph

    _font()
    normal = ParagraphStyle('body',fontName='VoolPDF',fontSize=10,leading=15,
                            spaceAfter=8,splitLongWords=1,alignment=TA_LEFT)
    header = ParagraphStyle('heading',parent=normal,fontName='VoolPDFBold',fontSize=16,
                            leading=21,spaceBefore=10,spaceAfter=10)
    cell = ParagraphStyle('cell',parent=normal,fontSize=9,leading=13,spaceAfter=0)
    width = A4[0]-88
    source_size = 0

    def paragraph(text, style=normal):
        nonlocal source_size
        text = str(text)
        source_size += len(text.encode('utf-8'))
        if source_size > MAX_SOURCE_BYTES:
            raise PdfRefused('PDF source exceeds the bounded document limit')
        font = pdfmetrics.getFont(style.fontName)
        if any(ord(c) not in font.face.charToGlyph for c in text if not c.isspace()):
            raise PdfRefused('The PDF font cannot represent every source character; no replacement glyphs were emitted')
        return PdfParagraph(escape(text).replace('\n','<br/>'),style)

    def glyph_check(text):
        # The same per-glyph coverage law paragraph() enforces, for text drawn as vector
        # shapes (diagram labels) instead of flowable paragraphs. Both faces are checked
        # because diagram titles render bold while labels render regular.
        for name in ('VoolPDF', 'VoolPDFBold'):
            font = pdfmetrics.getFont(name)
            if any(ord(c) not in font.face.charToGlyph for c in text if not c.isspace()):
                raise PdfRefused('The PDF font cannot represent every source character; no replacement glyphs were emitted')

    def table_flow(block):
        matrix = [block.headers,*block.rows]
        count = len(block.headers)
        if not count or count > 12 or any(len(row) != count for row in matrix):
            raise PdfRefused('PDF table has invalid or excessive columns')
        table = LongTable([[paragraph(value,cell) for value in row] for row in matrix],
                          colWidths=[width/count]*count,repeatRows=1,splitInRow=1,hAlign='LEFT')
        table.setStyle(TableStyle([
            ('BACKGROUND',(0,0),(-1,0),colors.HexColor('#e7eef0')),
            ('VALIGN',(0,0),(-1,-1),'TOP'),
            ('LINEBELOW',(0,0),(-1,0),0.6,colors.HexColor('#527078')),
            ('LINEBELOW',(0,1),(-1,-1),0.25,colors.HexColor('#c7d0d3')),
            ('LEFTPADDING',(0,0),(-1,-1),7),('RIGHTPADDING',(0,0),(-1,-1),7),
            ('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6),
        ]))
        return [table,Spacer(1,10)]

    def chart_flow(spec):
        from reportlab.graphics.charts.barcharts import HorizontalBarChart, VerticalBarChart
        from reportlab.graphics.charts.doughnut import Doughnut
        from reportlab.graphics.charts.lineplots import LinePlot
        from reportlab.graphics.charts.piecharts import Pie
        from reportlab.graphics.shapes import Drawing, Rect

        labels = spec['data']['labels']
        datasets = spec['data']['datasets']
        if len(labels)>30:
            raise PdfRefused('Chart has too many categories for readable PDF delivery')
        drawing = Drawing(width,230)
        drawing.add(Rect(0,0,width,230,fillColor=colors.HexColor(spec.get('background','#ffffff')),strokeColor=None))
        if spec['type'] in ('pie','doughnut'):
            if len(datasets)!=1:
                raise PdfRefused('PDF pie requires a single declared series')
            chart = Doughnut() if spec['type']=='doughnut' else Pie()
            chart.data = datasets[0]['data']
            chart.labels = [str(i+1) for i in range(len(labels))]
        elif spec['type']=='line':
            chart = LinePlot()
            chart.data = [list(enumerate(ds['data'],1)) for ds in datasets]
        else:
            chart = HorizontalBarChart() if spec.get('axis')=='y' else VerticalBarChart()
            chart.data = [ds['data'] for ds in datasets]
            chart.categoryAxis.categoryNames = [str(i+1) for i in range(len(labels))]
        chart.x,chart.y,chart.width,chart.height = 38,28,width-60,180
        values = [value for dataset in datasets for value in dataset['data']]
        low,high = min(0,*values),max(0,*values)
        if low==high:
            high = low+1
        if spec['type']=='bar':
            chart.valueAxis.valueMin,chart.valueAxis.valueMax = low,high
            chart.valueAxis.labels.fontName = chart.categoryAxis.labels.fontName = 'VoolPDF'
        elif spec['type']=='line':
            chart.yValueAxis.valueMin,chart.yValueAxis.valueMax = low,high
            chart.xValueAxis.valueSteps = list(range(1,len(labels)+1))
            chart.xValueAxis.labels.fontName = chart.yValueAxis.labels.fontName = 'VoolPDF'
        else:
            chart.width = chart.height = 180
            chart.x = (width-180)/2
            chart.slices.fontName = 'VoolPDF'
        palette = ('#167d9a','#d14465','#4d9259','#ab7013','#7854a8','#555555')
        for i,dataset in enumerate(datasets):
            color = colors.HexColor(dataset.get('color',palette[i%len(palette)]))
            if spec['type']=='bar':
                chart.bars[i].fillColor = color
            elif spec['type']=='line':
                chart.lines[i].strokeColor = color
        drawing.add(chart)
        rows = [[str(i+1),label,*[str(ds['data'][i]) for ds in datasets]] for i,label in enumerate(labels)]
        return [paragraph(spec.get('title') or 'Chart',header),drawing,*table_flow(Table(
            ['#','Label',*[str(ds.get('label') or 'Value') for ds in datasets]],rows))]

    def admitted_chart(block):
        try:
            spec = chart_payload(block) if isinstance(block,Chart) else parse_chart_body(block.text)
        except (ValueError,TypeError,OverflowError) as exc:
            raise PdfRefused('Chart cannot be represented in PDF: '+str(exc)) from exc
        return chart_flow(spec)

    story = []
    for block in document.blocks:
        if isinstance(block,Heading):
            story.append(paragraph(block.text,header))
        elif isinstance(block,Paragraph):
            if any(not isinstance(run,Span) for run in block.runs):
                raise PdfRefused('Unsupported inline block in PDF')
            story.append(paragraph(''.join(run.text for run in block.runs)))
        elif isinstance(block,Table):
            story.extend(table_flow(block))
        elif isinstance(block,Chart):
            story.extend(admitted_chart(block))
        elif isinstance(block,Code):
            if block.lang=='chart':
                story.extend(admitted_chart(block))
            elif block.lang=='mermaid':
                # Flowcharts render as deterministic vector shapes through the same local
                # reportlab lane as charts; other diagram families refuse with a typed reason.
                from core.presentation.mermaid_pdf import mermaid_pdf_flows
                story.extend(mermaid_pdf_flows(block.text, available_width=width,
                                               available_height=A4[1]-140, label_check=glyph_check))
                story.append(Spacer(1,10))
            else:
                story.append(paragraph(block.text))
        else:
            raise PdfRefused('Unsupported presentation block in PDF')
    if not story:
        raise PdfRefused('No content to export')
    # Keep headings with the beginning of content, not an entire multipage table.
    laid_out = []
    index = 0
    while index < len(story):
        group = []
        while index < len(story) and isinstance(story[index],PdfParagraph) and story[index].style is header:
            group.append(story[index])
            index += 1
        if group:
            needed = sum(item.wrap(width,A4[1])[1]+item.getSpaceBefore()+item.getSpaceAfter() for item in group)
            laid_out.append(CondPageBreak(needed+50))
            laid_out.extend(group)
        if index < len(story):
            laid_out.append(story[index])
            index += 1
    output = _BoundedBuffer()
    def page_footer(canvas, doc):
        if doc.page > MAX_PAGES:
            raise PdfRefused('PDF exceeds the page limit')
        canvas.setFont('VoolPDF',8)
        canvas.drawRightString(A4[0]-44,24,str(doc.page))
    document = SimpleDocTemplate(output,pagesize=A4,leftMargin=44,rightMargin=44,
                                 topMargin=40,bottomMargin=40,title=title,author='')
    from reportlab.platypus.doctemplate import LayoutError

    try:
        document.build(laid_out,onFirstPage=page_footer,onLaterPages=page_footer)
    except LayoutError as exc:
        raise PdfRefused('Document content cannot fit the PDF layout; no partial file was delivered') from exc
    return output.getvalue()


def render_markdown_pdf(source: str, *, title: str = 'VOOL') -> bytes:
    return render_document(markdown_document(source),title=title)
