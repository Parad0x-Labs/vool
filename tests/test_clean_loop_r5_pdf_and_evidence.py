from __future__ import annotations

import zlib
from pathlib import Path
from types import SimpleNamespace

from core.clean_loop import route, tools
from core.clean_loop.hook import _prior_evidence, _remember_evidence
from core.clean_loop.loop import CONTENT_SEARCH_NUDGE, LoopResult, run_loop
from core.clean_loop.registry import Registry, ToolContext, ToolOutcome
from core.clean_loop.tools import install_builtin_tools
from core.pdf_tools import extract_text

OWNER = {"surface": "cli"}


def _pdf(path: Path, pages: list[str]) -> Path:
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        (f"<< /Type /Pages /Kids [{' '.join(f'{3 + 3*i} 0 R' for i in range(len(pages)))}] /Count {len(pages)} >>").encode(),
    ]
    for index, page_text in enumerate(pages):
        page_obj = 3 + 3 * index
        content_obj = page_obj + 2
        objects.extend(
            [
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 {page_obj + 1} 0 R >> >> /Contents {content_obj} 0 R >>".encode(),
                b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            ]
        )
        content = page_text.removeprefix("RAW:") if page_text.startswith("RAW:") else f"BT /F1 10 Tf 40 740 Td ({page_text}) Tj ET"
        stream = zlib.compress(content.encode())
        objects.append(
            b"<< /Length " + str(len(stream)).encode() + b" /Filter /FlateDecode >>\nstream\n"
            + stream + b"\nendstream"
        )
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    path.write_bytes(output)
    return path


def _pdf_context(home: Path) -> ToolContext:
    def execute(intent, arguments, **_kwargs):
        if intent != "pdf.extract_text":
            return SimpleNamespace(ok=False, status="unsupported", response_text="unsupported", details={})
        data = extract_text(
            arguments["path"],
            start_page=arguments.get("start_page", 1),
            max_pages=arguments.get("max_pages", 0),
        )
        if data["status"] == "ok":
            shown = f"Read {data['pages_read']} page(s).\n{data['text']}"
        else:
            shown = f"Could not read {Path(arguments['path']).name}: {data.get('reason') or data['status']}"
        return SimpleNamespace(ok=data["status"] == "ok", status=data["status"], response_text=shown, details=data)

    return ToolContext(home=home, source_context=dict(OWNER), execute=execute)


def test_compressed_text_pdf_is_read_and_scanned_pdf_is_never_called_empty(tmp_path):
    text_pdf = _pdf(tmp_path / "compressed.pdf", ["Saffron marker appears here"])
    extracted = extract_text(str(text_pdf))
    assert extracted["status"] == "ok"
    assert "Saffron marker" in extracted["text"]

    scan_pdf = _pdf(tmp_path / "scan.pdf", [""])
    scan = tools.read_file({"path": str(scan_pdf)}, _pdf_context(tmp_path))
    assert scan.status == "no_text_layer"
    assert "appears scanned" in scan.text
    assert "not empty" in scan.text


def test_long_pdf_marks_the_cut_and_can_continue_from_a_later_page(tmp_path):
    rows = " ".join(f"({('X' * 52)} {index}) Tj 0 -10 Td" for index in range(76))
    long_pdf = _pdf(tmp_path / "long.pdf", [f"RAW:BT /F1 8 Tf 40 760 Td {rows} ET", "Orchid marker is recorded on this page"])
    ctx = _pdf_context(tmp_path)
    first = tools.read_file({"path": str(long_pdf)}, ctx)
    later = tools.read_file({"path": str(long_pdf), "from_page": 2, "max_pages": 1}, ctx)
    assert "[Cut; continue" in first.text
    assert "Orchid marker" in later.text
    assert len(first.text) <= 3000


def test_long_text_file_can_resume_at_a_later_line(tmp_path):
    path = tmp_path / "notes.txt"
    lines = [f"filler row {i}" for i in range(240)] + ["Orchid marker is recorded here"]
    path.write_text("\n".join(lines), encoding="utf-8")

    def execute(intent, arguments, **_kwargs):
        if intent != "machine.read_file":
            return SimpleNamespace(ok=False, status="unsupported", response_text="unsupported", details={})
        rows = path.read_text(encoding="utf-8").splitlines()
        start = max(1, int(arguments.get("start_line", 1)))
        end = start + int(arguments.get("max_lines", 2000))
        text = "\n".join(rows[start - 1 : end])
        return SimpleNamespace(ok=True, status="ok", response_text=text, details={})

    ctx = ToolContext(home=tmp_path, source_context=dict(OWNER), execute=execute)
    first = tools.read_file({"path": str(path)}, ctx)
    later = tools.read_file({"path": str(path), "start_line": 235}, ctx)
    assert "[Cut; continue" in first.text
    assert "Orchid marker" in later.text


def test_prior_evidence_is_limited_to_its_owner_local_session(tmp_path):
    route.note_clean_turn("local-session")
    _remember_evidence("local-session", "Documents/itinerary.txt: 8:35, 14 June")
    ask = "and what is the total?"
    evidence = _prior_evidence("local-session", ask, owner_local=True)
    assert "8:35" in evidence
    assert _prior_evidence("other-session", ask, owner_local=True) == ""
    assert _prior_evidence("local-session", ask, owner_local=False) == ""

    registry = Registry()
    install_builtin_tools(registry)
    seen = []

    def brain(messages):
        seen.extend(messages)
        return '{"answer": "The total is 27.50."}'

    result = run_loop(ask, brain=brain, registry=registry, ctx=ToolContext(home=tmp_path), prior_evidence=evidence)
    assert result.answer == "The total is 27.50."
    assert any("8:35" in message["content"] for message in seen)


def test_absence_answer_waits_for_a_complete_content_search():
    incomplete = LoopResult(
        answer="That file is not there.",
        steps=[{"tool": "find", "status": "content_search_incomplete"}],
    )
    complete = LoopResult(
        answer="I didn't find it in your files.",
        steps=[{"tool": "find", "status": "content_search_complete"}],
    )
    assert "can't say whether it is there" in tools.content_absence_gate(incomplete.answer, incomplete)
    assert tools.content_absence_gate(complete.answer, complete) == complete.answer


def test_loop_takes_one_more_step_before_an_absence_claim():
    registry = Registry()
    install_builtin_tools(registry)
    registry.register_tool(
        "find", "", lambda _args, _ctx: ToolOutcome("search incomplete", status="content_search_incomplete")
    )
    replies = [
        '{"answer": "That note is not there."}',
        '{"tool": "find", "query": "orchid"}',
        '{"answer": "I did not find that note."}',
    ]
    seen = []

    def brain(messages):
        seen.append([dict(item) for item in messages])
        return replies[len(seen) - 1]

    result = run_loop("Is the note saved?", brain=brain, registry=registry, ctx=ToolContext(home=Path(".")))
    assert len(seen) == 3
    assert any(CONTENT_SEARCH_NUDGE in message["content"] for message in seen[1])
    assert "can't say whether it is there" in result.answer
