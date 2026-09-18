"""Execute the shipped UI parser; Node DOM shim, not native acceptance."""
import json
from html.parser import HTMLParser

import pytest

from core.presentation.model import RenderDocument, Table
from core.presentation.render_markdown import MarkdownRenderer
from tests.test_chat_export_copy_ui import SHIM, _run_node, _slice, requires_node


class Cells(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.active = False

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.rows.append([])
        if tag in {"td", "th"}:
            self.active = True
            self.rows[-1].append("")

    def handle_endtag(self, tag):
        if tag in {"td", "th"}:
            self.active = False

    def handle_data(self, data):
        if self.active:
            self.rows[-1][-1] += data


@requires_node
@pytest.mark.parametrize("label,value", [
    ("A|B", "5"), ("East|West", "48 kg"),
    (r"A\|B", "12"), (r"A\\|B", "23"),
    ("C:\\storage\\", "17"), ("<script>alert(1)</script>", "9"),
    ("ordinary", "31"),
])
def test_recovered_table_cells_round_trip_through_shipped_ui(label, value):
    headers = ["Result", "Calculation", "Value"]
    rows = [[label, "6 * 8", value]]
    md = MarkdownRenderer().render(RenderDocument(blocks=[Table(headers=headers, rows=rows)]))
    source = _slice("function esc(", "\n  el.innerHTML = html;\n}", include_end=True)
    html = _run_node(SHIM + source + "\nconst el = new El(); renderRichText(el,"
                     + json.dumps(md) + "); process.stdout.write(el.innerHTML);")
    parser = Cells()
    parser.feed(html)
    assert parser.rows == [headers, *rows]
    assert "<script>" not in html
