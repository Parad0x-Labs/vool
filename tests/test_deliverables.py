"""Files handed to the user (core.deliverables): every format opens, carries its content, and only
its own chat can download it.

Each built file is read back by an INDEPENDENT reader where one exists in the standard library or
on this machine (zipfile, tarfile, json, csv, ElementTree; pypdf for PDF), not only by this
runtime's own readers, so a writer and a reader sharing one misunderstanding cannot pass together.
"""

from __future__ import annotations

import csv
import io
import json
import tarfile
import zipfile
from xml.etree import ElementTree

import pytest

from core import deliverables, runtime_paths
from core.deliverables import DeliverableRefused, build_file, create_deliverable, read_deliverable

SESSION = "openclaw:dededededededededede"
REPORT = """# Field Report

The audit code is HAZEL-4411.

## Findings
- North depot on time
- South depot late

| Site | Units |
|---|---|
| North | 41 |
| South | 17 |
"""


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    runtime_paths.configure_runtime_home(tmp_path / "home")
    yield
    runtime_paths.configure_runtime_home(None)


def _xml_text(package: zipfile.ZipFile, prefix: str) -> str:
    out = []
    for name in sorted(package.namelist()):
        if name.startswith(prefix) and name.endswith(".xml"):
            out.extend(t for t in ElementTree.fromstring(package.read(name)).itertext())
    return " ".join(out)


def test_docx_is_a_valid_package_carrying_headings_lists_and_the_table():
    built = build_file(REPORT, filename="report.docx")
    with zipfile.ZipFile(io.BytesIO(built.data)) as package:
        assert package.testzip() is None
        assert {"[Content_Types].xml", "word/document.xml", "word/styles.xml"} <= set(package.namelist())
        text = _xml_text(package, "word/document.xml")
        document = package.read("word/document.xml").decode()
    assert "HAZEL-4411" in text and "South depot late" in text and "North" in text and "41" in text
    assert 'w:pStyle w:val="Heading1"' in document and "<w:tbl>" in document


def test_xlsx_cells_keep_their_types_and_each_table_is_a_sheet():
    built = build_file("name,qty,price\nbolts,40,0.25\ncode,007,x\n", filename="parts.xlsx")
    with zipfile.ZipFile(io.BytesIO(built.data)) as package:
        sheet = package.read("xl/worksheets/sheet1.xml").decode()
    assert '<c r="B2"><v>40</v></c>' in sheet and '<c r="C2"><v>0.25</v></c>' in sheet
    # A leading-zero code is text, not the number 7.
    assert "<t xml:space=\"preserve\">007</t>" in sheet
    two = build_file("## North\n| a | b |\n|---|---|\n| 1 | 2 |\n\n## South\n| a | b |\n|---|---|\n| 3 | 4 |\n", filename="two.xlsx")
    with zipfile.ZipFile(io.BytesIO(two.data)) as package:
        workbook = package.read("xl/workbook.xml").decode()
    assert 'name="North"' in workbook and 'name="South"' in workbook


def test_pptx_has_one_slide_per_section_and_the_title_slide():
    built = build_file(REPORT, filename="deck.pptx")
    with zipfile.ZipFile(io.BytesIO(built.data)) as package:
        slides = sorted(n for n in package.namelist() if n.startswith("ppt/slides/slide") and n.endswith(".xml"))
        assert len(slides) == 2
        assert "HAZEL-4411" in _xml_text(package, "ppt/slides/slide1.xml")
        assert "South depot late" in _xml_text(package, "ppt/slides/slide2.xml")


def test_pdf_carries_its_text_for_an_independent_reader():
    pypdf = pytest.importorskip("pypdf", reason="an independent PDF text reader")
    built = build_file(REPORT, filename="report.pdf")
    text = " ".join(page.extract_text() for page in pypdf.PdfReader(io.BytesIO(built.data)).pages)
    assert "HAZEL-4411" in text


def test_text_formats_parse_and_a_markdown_table_becomes_real_csv():
    rows = list(csv.reader(io.StringIO(build_file("| a | b |\n|---|---|\n| 1 | x,y |\n", filename="t.csv").data.decode())))
    assert rows == [["a", "b"], ["1", "x,y"]]
    assert json.loads(build_file('{"k": [1, 2]}', filename="c.json").data) == {"k": [1, 2]}
    ElementTree.fromstring(build_file("<a><b>1</b></a>", filename="f.xml").data)
    assert build_file("print('x')", filename="tool.py").media_type == "text/x-python"


def test_bundles_hold_built_members_and_never_escape():
    built = build_file("", filename="b.zip", files=[{"filename": "docs/r.docx", "content": REPORT}, {"filename": "t.py", "content": "x = 1"}])
    with zipfile.ZipFile(io.BytesIO(built.data)) as bundle:
        assert sorted(bundle.namelist()) == ["docs/r.docx", "t.py"]
        with zipfile.ZipFile(io.BytesIO(bundle.read("docs/r.docx"))) as inner:
            assert "HAZEL-4411" in _xml_text(inner, "word/document.xml")
    tgz = build_file("", filename="b.tar.gz", files=[{"filename": "a.md", "content": "# A"}])
    with tarfile.open(fileobj=io.BytesIO(tgz.data), mode="r:gz") as bundle:
        assert bundle.getnames() == ["a.md"]
    for hostile in ("../../etc/passwd", "/etc/passwd", "C:/x.txt", "a/../../b"):
        with pytest.raises(DeliverableRefused) as caught:
            build_file("", filename="x.zip", files=[{"filename": hostile, "content": "x"}])
        assert caught.value.code == "unsafe_member_name"


@pytest.mark.parametrize(
    ("filename", "content", "code"),
    [
        ("bad.json", "{nope", "invalid_json"),
        ("bad.yaml", "a: [1,", "invalid_yaml"),
        ("bad.xml", "<a><b></a>", "invalid_xml"),
        ("ent.xml", '<!DOCTYPE x [<!ENTITY a "b">]><x>&a;</x>', "xml_doctype"),
        ("x.rar", "hi", "format_unsupported"),
        ("x.7z", "hi", "format_unsupported"),
        ("x.exe", "hi", "format_unsupported"),
        ("empty.docx", "   ", "empty_content"),
    ],
)
def test_what_cannot_be_built_correctly_is_refused_with_a_reason(filename, content, code):
    with pytest.raises(DeliverableRefused) as caught:
        build_file(content, filename=filename)
    assert caught.value.code == code and caught.value.message


def test_a_filename_is_a_label_never_a_path():
    assert build_file("x", filename="../../etc/report.txt").filename == "report.txt"
    assert build_file("x", filename="notes", fmt="md").filename == "notes.md"
    assert build_file("x", filename="export", fmt="word").filename == "export.docx"


def test_only_the_creating_chat_can_read_a_file_and_it_is_kept_intact():
    record = create_deliverable(session_id=SESSION, filename="r.docx", content=REPORT)
    assert record["url"].startswith("/api/chat/deliverable?id=dlv_")
    found = read_deliverable(deliverable_id=record["id"], session_id=SESSION)
    assert found is not None and found[0]["filename"] == "r.docx"
    assert read_deliverable(deliverable_id=record["id"], session_id="openclaw:00000000000000000000") is None
    assert read_deliverable(deliverable_id="dlv_../../x", session_id=SESSION) is None
    # A tampered file is not served.
    (deliverables.store_dir() / f"{record['id']}.bin").write_bytes(b"tampered")
    assert read_deliverable(deliverable_id=record["id"], session_id=SESSION) is None


def test_expired_files_are_swept():
    record = create_deliverable(session_id=SESSION, filename="r.txt", content="x")
    assert deliverables.sweep_expired(now=record["created_epoch"] + deliverables.RETENTION_SECONDS + 1) == 1
    assert read_deliverable(deliverable_id=record["id"], session_id=SESSION) is None


def test_the_download_door_is_owner_local_and_session_bound():
    from types import SimpleNamespace

    from core.web.api.deliverables_api import handle_deliverable_get

    runtime = SimpleNamespace(runtime_version_stamp={})
    record = create_deliverable(session_id=SESSION, filename="r.csv", content="a,b\n1,2")
    query = {"id": [record["id"]], "session": [SESSION]}
    ok = handle_deliverable_get(query=query, runtime=runtime, client_host="127.0.0.1")
    assert ok.status == 200 and ok.body.startswith(b"a,b")
    assert ok.headers["Content-Disposition"].startswith('attachment; filename="r.csv"')
    assert handle_deliverable_get(query=query, runtime=runtime, client_host="203.0.113.9").status == 403
    other = {"id": [record["id"]], "session": ["openclaw:11111111111111111111"]}
    assert handle_deliverable_get(query=other, runtime=runtime, client_host="127.0.0.1").status == 404


def test_the_delivery_tool_is_declared_governed_and_offered_for_file_requests():
    from core.runtime_tool_contracts import runtime_tool_contract_map
    from core.tool_demand_signals import resolve_demand_signals

    contract = runtime_tool_contract_map()["file.deliver"]
    assert contract.permission_actions == ("create_files",) and contract.renders_final_answer
    # A delivered file ends the turn; no other builtin tool claims that.
    assert contract.completes_turn
    assert [c.intent for c in runtime_tool_contract_map().values() if c.completes_turn] == ["file.deliver"]
    for asked in ("Give me that as a Word document", "export the table to csv", "zip these files", "put it in a pdf"):
        assert "file.deliver" in resolve_demand_signals(asked).explicit_intents, asked
    for not_asked in ("summarize the attached pdf", "what's in the pdf?", "respond in json"):
        assert "file.deliver" not in resolve_demand_signals(not_asked).explicit_intents, not_asked


def test_without_confinement_only_the_runtimes_own_pdf_is_read_back_in_process(monkeypatch):
    pytest.importorskip("pypdf", reason="the in-process reader of this runtime's own PDF")
    from core import artifact_readers
    from core.artifact_readers import _sandbox

    monkeypatch.setattr(_sandbox, "confinement_available", lambda: False)
    built = build_file(REPORT, filename="report.pdf")
    assert built.verified_by == "pypdf (this runtime's own render, read in-process)"
    # The same bytes arriving from outside are still refused: the unconfined path is the builder's alone.
    with pytest.raises(artifact_readers.ReaderError) as caught:
        artifact_readers.read(built.data, name="report.pdf", extension=".pdf")
    assert caught.value.code == "confinement_unavailable"


def test_without_confinement_a_pdf_missing_its_own_text_is_not_delivered(monkeypatch):
    pytest.importorskip("pypdf", reason="the in-process reader of this runtime's own PDF")
    from core.artifact_readers import _sandbox
    from core.presentation import render_pdf

    monkeypatch.setattr(_sandbox, "confinement_available", lambda: False)
    blank = render_pdf.render_markdown_pdf("# Other\n\nNothing here.", title="Other")
    monkeypatch.setattr(render_pdf, "render_markdown_pdf", lambda text, title="": blank)
    with pytest.raises(DeliverableRefused) as caught:
        build_file(REPORT, filename="report.pdf")
    assert caught.value.code == "verification_failed"
    assert "HAZEL-4411" in str(caught.value)
