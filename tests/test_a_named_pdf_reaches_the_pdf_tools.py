"""A PDF named in chat must reach the PDF tools, and each failure must keep its own words.

Measured against the running daemon on `e3f59af` over `/api/chat`, one fresh session per turn,
before anything here changed. The tools were already correct — driven directly,
`execute_runtime_tool("pdf.extract_text", ...)` returned the real text, `no_text_layer` on a scan,
`unreadable` on a disguised file and `not_found` on a missing one. Chat reached none of it:

    "Read ~/Downloads/.../quarterly_report.pdf and tell me the total revenue."
                                        -> "I couldn't map that cleanly to a real action."  (61.7s)
    "extract the text from ~/Downloads/.../quarterly_report.pdf"
                                        -> "I couldn't map that cleanly to a real action."  (61.3s)
    "open the PDF at ~/Downloads/.../quarterly_report.pdf and summarise it"
                                        -> "I couldn't map that cleanly to a real action."  (76.3s)
    "pull the text out of the file ~/Downloads/.../quarterly_report.pdf please"
                                        -> "I couldn't map that cleanly to a real action."  (61.4s)
    "I have a PDF at ~/Downloads/.../quarterly_report.pdf - what does it say?"
                                        -> "I couldn't map that cleanly to a real action."  (61.6s)
    "What is the project codename inside ~/Downloads/.../quarterly_report.pdf?"
                                        -> a description of the workspace folder                (0.7s)

Two structural seams, not a wording accident. The machine-read lane stands down the moment the user
types an explicit path below a home folder and hands off to "the general machine and workspace
tools" — which cannot open a PDF; `machine.read_file` answers a `.pdf` with `binary_file`. What was
left was a small local model being asked to emit `{"intent": "pdf.extract_text"}` as JSON, which is
the exact failure the fast paths exist to avoid: ~61s per miss, ending in the one catch-all string
that a missing file, a renamed file, a scanned page and a router bail all shared.

The other half of the bug is that catch-all. A user can act on "there is no file there" and on
"that is not really a PDF" and on "this one is scanned"; they can act on none of them when all
three arrive as the same sentence. Every status is pinned to its own wording below.
"""
from __future__ import annotations

import subprocess
import types
from pathlib import Path

import pytest

from core.agent_runtime.fast_paths_pdf import maybe_handle_pdf_read, pdf_read_request

SYSTEM_PYTHON = "/usr/bin/python3"
_HAS_PDFKIT = (
    Path(SYSTEM_PYTHON).exists()
    and subprocess.run(
        [SYSTEM_PYTHON, "-c", "import Quartz, Foundation, AppKit"], capture_output=True
    ).returncode == 0
)
needs_pdfkit = pytest.mark.skipif(not _HAS_PDFKIT, reason="macOS PDFKit bindings not present")

# Draws real glyphs through AppKit, so the text layer is a real text layer and the scanned fixture
# is a real raster with no text layer at all — an empty page would pass a weaker test by accident.
_MAKE_PDF = r'''
import sys, json, Quartz, AppKit
from Foundation import NSURL, NSAttributedString

path, mode, lines = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
W, H = 612.0, 792.0
media = Quartz.CGRectMake(0, 0, W, H)

def draw(ctx, rows, size, start, leading):
    nsctx = AppKit.NSGraphicsContext.graphicsContextWithCGContext_flipped_(ctx, False)
    AppKit.NSGraphicsContext.saveGraphicsState()
    AppKit.NSGraphicsContext.setCurrentContext_(nsctx)
    font = AppKit.NSFont.fontWithName_size_("Helvetica", size) or AppKit.NSFont.systemFontOfSize_(size)
    attrs = {AppKit.NSFontAttributeName: font,
             AppKit.NSForegroundColorAttributeName: AppKit.NSColor.blackColor()}
    y = start
    for row in rows:
        NSAttributedString.alloc().initWithString_attributes_(row, attrs).drawAtPoint_(
            AppKit.NSMakePoint(64.0, y))
        y -= leading
    AppKit.NSGraphicsContext.restoreGraphicsState()

pdf = Quartz.CGPDFContextCreateWithURL(NSURL.fileURLWithPath_(path), media, None)
for page in lines:
    if mode == "text":
        Quartz.CGPDFContextBeginPage(pdf, None)
        Quartz.CGContextSetRGBFillColor(pdf, 1, 1, 1, 1)
        Quartz.CGContextFillRect(pdf, media)
        draw(pdf, page, 18.0, 700.0, 28.0)
        Quartz.CGPDFContextEndPage(pdf)
    else:
        scale = 2.0
        w, h = int(W * scale), int(H * scale)
        bmp = Quartz.CGBitmapContextCreate(
            None, w, h, 8, 0, Quartz.CGColorSpaceCreateDeviceRGB(),
            Quartz.kCGImageAlphaPremultipliedLast)
        Quartz.CGContextSetRGBFillColor(bmp, 1, 1, 1, 1)
        Quartz.CGContextFillRect(bmp, Quartz.CGRectMake(0, 0, w, h))
        Quartz.CGContextScaleCTM(bmp, scale, scale)
        draw(bmp, page, 28.0, 680.0, 52.0)
        image = Quartz.CGBitmapContextCreateImage(bmp)
        Quartz.CGPDFContextBeginPage(pdf, None)
        Quartz.CGContextDrawImage(pdf, media, image)
        Quartz.CGPDFContextEndPage(pdf)
Quartz.CGPDFContextClose(pdf)
'''


def _write_pdf(path: Path, mode: str, pages: list[list[str]]) -> Path:
    import json

    proc = subprocess.run(
        [SYSTEM_PYTHON, "-c", _MAKE_PDF, str(path), mode, json.dumps(pages)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert path.is_file()
    return path


class _Agent:
    """Only what the fast path touches. A real reply is a string; nothing here can fabricate one."""

    def __init__(self) -> None:
        self.events: list[str] = []

    def _fast_path_result(self, *, response: str, reason: str, **_: object) -> dict[str, object]:
        return {"response": response, "reason": reason}

    def _emit_runtime_event(self, _source_context: object, **kwargs: object) -> None:
        self.events.append(str(kwargs.get("tool_name") or ""))


@pytest.fixture
def fixtures(tmp_path, monkeypatch):
    """A real Desktop/Downloads/Documents trio, so the root rule is exercised, not mocked."""
    home = tmp_path / "home"
    for name in ("Desktop", "Downloads", "Documents"):
        (home / name).mkdir(parents=True)
    monkeypatch.setattr("core.runtime_execution_tools._machine_home", lambda: home)

    box = home / "Downloads" / "papers"
    box.mkdir()
    _write_pdf(box / "quarterly_report.pdf", "text", [[
        "VOOL ACCEPTANCE FIXTURE ALPHA",
        "Total revenue for Q3 was 48213 euros.",
        "The project codename is ZEPHYR-9.",
    ]])
    _write_pdf(box / "my summer notes.pdf", "text", [[
        "The secret word in this file is PELICAN.",
    ]])
    _write_pdf(box / "three_page_manual.pdf", "text", [
        ["The first page marker is ANCHOR-ONE-7741."],
        ["The second page marker is ANCHOR-TWO-2298."],
        ["The third page marker is ANCHOR-THREE-5063."],
    ])
    _write_pdf(box / "scanned_invoice.pdf", "scan", [[
        "SCANNED FIXTURE CHARLIE",
        "Invoice number 88412",
    ]])
    (box / "not_really_a_pdf.pdf").write_text("plain text pretending to be a PDF\n")

    outside = tmp_path / "elsewhere"
    outside.mkdir()
    _write_pdf(outside / "offlimits.pdf", "text", [["VOOL FIXTURE ECHO"]])
    return types.SimpleNamespace(home=home, box=box, outside=outside)


def _reply(fixtures, phrasing: str) -> str:
    agent = _Agent()
    result = maybe_handle_pdf_read(agent, phrasing, session_id="t", source_context={})
    assert result is not None, f"the PDF lane did not claim {phrasing!r}"
    return str(result["response"])


# --------------------------------------------------------------------------------------
# The reported phrasings, verbatim, one assertion each
# --------------------------------------------------------------------------------------

#: Every one of these came back as "I couldn't map that cleanly to a real action." on the live
#: daemon. The path is rewritten onto the fixture folder; the wording around it is untouched.
REPORTED_PHRASINGS = (
    "Read {p} and tell me the total revenue.",
    "What is the project codename inside {p}?",
    "extract the text from {p}",
    "open the PDF at {p} and summarise it",
    "pull the text out of the file {p} please",
    "I have a PDF at {p} - what does it say?",
)


@needs_pdfkit
@pytest.mark.parametrize("template", REPORTED_PHRASINGS)
def test_a_reported_phrasing_returns_the_text_that_is_in_the_file(fixtures, template) -> None:
    reply = _reply(fixtures, template.format(p=fixtures.box / "quarterly_report.pdf"))
    # Asserted against what was PUT in the file, not against the shape of a successful reply.
    assert "48213" in reply
    assert "ZEPHYR-9" in reply
    assert "couldn't map that cleanly" not in reply


@needs_pdfkit
def test_a_filename_with_spaces_is_not_cut_at_the_first_space(fixtures) -> None:
    reply = _reply(fixtures, f"read {fixtures.box / 'my summer notes.pdf'}")
    assert "PELICAN" in reply


@needs_pdfkit
def test_a_bare_filename_is_found_under_the_allowed_roots(fixtures) -> None:
    reply = _reply(fixtures, "quarterly_report.pdf - what is in it?")
    assert "48213" in reply


@needs_pdfkit
def test_every_page_of_a_multi_page_pdf_comes_back(fixtures) -> None:
    reply = _reply(fixtures, f"read all of {fixtures.box / 'three_page_manual.pdf'}")
    assert "ANCHOR-ONE-7741" in reply
    assert "ANCHOR-TWO-2298" in reply
    assert "ANCHOR-THREE-5063" in reply


# --------------------------------------------------------------------------------------
# The four failures must not share one sentence
# --------------------------------------------------------------------------------------


@needs_pdfkit
def test_a_missing_file_says_the_file_is_missing(fixtures) -> None:
    reply = _reply(fixtures, f"read {fixtures.box / 'no_such_report.pdf'}")
    assert "no file at" in reply.lower()
    assert "no_such_report.pdf" in reply


# --------------------------------------------------------------------------------------
# Found by the re-drive, on eight phrasings none of the code had seen
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sentence,literal",
    [
        # The one that produced a folder listing instead of the file.
        ("scanned_invoice.pdf in my downloads fixtures folder - what invoice number does it show?",
         "scanned_invoice.pdf"),
        ("have a read of '~/Downloads/fixtures/my summer notes.pdf' and tell me the secret word",
         "notes.pdf"),
        ("open the report.docx on my desktop", "report.docx"),
        ("here is the chart.png I mentioned", "chart.png"),
        ("pull the rows out of expenses.csv", "expenses.csv"),
    ],
)
def test_the_normalizer_does_not_split_a_document_filename(sentence: str, literal: str) -> None:
    """A format missing from the literal list is a filename the runtime never sees.

    Measured on the live daemon at ffd1b6f, after the PDF lane was already correct:
    "scanned_invoice.pdf in my downloads fixtures folder - what invoice number does it show?"
    reached the front door as "scanned_invoice. pdf in my downloads fixtures folder ...". No lane
    could see a filename in that, and the turn answered with a listing of the folder. `pdf` — and
    every other document and media format a user names out loud — was absent from
    `_LITERAL_SPAN_RE`, which already protected `.py`, `.json` and `.md`.
    """
    from core.input_normalizer import normalize_user_text

    assert literal in normalize_user_text(sentence).normalized_text


@needs_pdfkit
def test_words_in_front_of_a_path_are_not_part_of_the_path(fixtures) -> None:
    """"show me ~/Downloads/x/budget.pdf" must not be read as a relative path called "show me …".

    Measured live: that sentence was refused with "is outside those folders" for a file under
    ~/Downloads, because the candidate carried the two words in front of it, which made it
    relative, which resolved against the process cwd. A noise-word list cannot fix this — the words
    in front of a path are whatever the user felt like typing — so the path is cut at the first
    word carrying a separator.
    """
    missing = _reply(fixtures, f"show me {fixtures.box / 'budget_2024.pdf'}")
    assert "outside those folders" not in missing.lower()
    assert "no file at" in missing.lower()

    present = _reply(fixtures, f"show me {fixtures.box / 'quarterly_report.pdf'}")
    assert "48213" in present


@needs_pdfkit
def test_the_filename_does_not_vote_on_whether_to_run_ocr(fixtures) -> None:
    """`scan_of_contract.pdf` is a filename, not a request for optical recognition.

    Measured live: "scanned_invoice.pdf in my downloads fixtures folder - what invoice number does
    it show?" matched " scan" inside its OWN filename and went straight to `pdf.ocr`. That file is
    a scan, so the answer was right and the bug invisible. Pinned on a file that has a text layer,
    where choosing OCR means approximate recognition — slowly — of text that was sitting there.
    """
    _write_pdf(fixtures.box / "scan_of_contract.pdf", "text", [[
        "This contract has a real text layer. The clause number is 4471.",
    ]])
    # The live shape, and the only one that reproduces it: the bare filename leads the sentence, so
    # the marker " scan" meets a word boundary. A first version of this test put the same name
    # inside a full path, where "/scan..." has no space in front of it — the sabotage sailed
    # through, because the fixture was not the bug.
    sentence = "scan_of_contract.pdf in my downloads papers folder - what clause number is in it?"
    assert pdf_read_request(sentence)["wants_ocr"] is False
    # The user's own word still wins.
    assert pdf_read_request(f"run ocr on {fixtures.box / 'scan_of_contract.pdf'}")["wants_ocr"] is True

    agent = _Agent()
    result = maybe_handle_pdf_read(agent, sentence, session_id="t", source_context={})
    assert agent.events == ["pdf.extract_text", "pdf.extract_text"]
    assert "4471" in str(result["response"])


@needs_pdfkit
def test_a_quoted_path_is_read_after_the_normalizer_pads_the_quotes(fixtures) -> None:
    """The live phrasing, through the real normalizer rather than around it."""
    from core.input_normalizer import normalize_user_text

    sentence = f"have a read of '{fixtures.box / 'my summer notes.pdf'}' and tell me the secret word"
    assert "PELICAN" in _reply(fixtures, normalize_user_text(sentence).normalized_text)


@needs_pdfkit
def test_a_bare_name_that_is_nowhere_says_so_without_blaming_a_folder(fixtures) -> None:
    """"`budget.pdf` is outside those folders" sends the user to fix a folder they never named.

    A bare filename that the root search does not turn up resolves relative to the process cwd,
    which is outside the allowed roots by accident rather than by anything the user wrote. Found by
    sabotaging the bare-name search and reading the reply it produced.
    """
    reply = _reply(fixtures, "read budget_2019.pdf")
    assert "could not find" in reply.lower()
    assert "outside those folders" not in reply.lower()


@needs_pdfkit
def test_a_renamed_text_file_says_it_is_not_a_pdf(fixtures) -> None:
    reply = _reply(fixtures, f"extract the text from {fixtures.box / 'not_really_a_pdf.pdf'}")
    assert "not a readable pdf" in reply.lower()


@needs_pdfkit
def test_a_scanned_pdf_is_ocred_instead_of_refused(fixtures) -> None:
    """`no_text_layer` names its own successor, so the lane runs it rather than reporting a dead end."""
    reply = _reply(fixtures, f"read {fixtures.box / 'scanned_invoice.pdf'}")
    assert "88412" in reply, "OCR did not recover the text that is on the page"
    assert "no text layer" in reply.lower()
    assert "ocr" in reply.lower()


@needs_pdfkit
def test_a_path_outside_the_allowed_roots_is_refused_by_name(fixtures) -> None:
    reply = _reply(fixtures, f"read {fixtures.outside / 'offlimits.pdf'}")
    assert "only read files inside" in reply.lower()
    assert "~/Downloads" in reply


@needs_pdfkit
def test_the_four_failures_do_not_share_one_sentence(fixtures) -> None:
    """The whole point. Four distinct causes, four distinct replies.

    This is the assertion the pre-fix build failed hardest: every one of these was the same
    string, so the reply could not tell the user whether to fix their typo, their file, or file
    a bug.
    """
    replies = [
        _reply(fixtures, f"read {fixtures.box / 'no_such_report.pdf'}"),
        _reply(fixtures, f"read {fixtures.box / 'not_really_a_pdf.pdf'}"),
        _reply(fixtures, f"read {fixtures.box / 'scanned_invoice.pdf'}"),
        _reply(fixtures, f"read {fixtures.outside / 'offlimits.pdf'}"),
    ]
    assert len({r.strip().lower() for r in replies}) == 4, replies


# --------------------------------------------------------------------------------------
# What the lane must NOT claim
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "phrasing",
    [
        "how do I open a PDF on a Mac?",
        "create a pdf called notes.pdf with my todo list",
        "convert my report to pdf",
        "make a pdf summary of this chat",
        "what is a PDF text layer anyway",
        "save as pdf please",
    ],
)
def test_a_message_that_does_not_name_a_pdf_to_read_is_left_alone(phrasing: str) -> None:
    assert pdf_read_request(phrasing) is None


# --------------------------------------------------------------------------------------
# A local path must never become a web search
# --------------------------------------------------------------------------------------


@needs_pdfkit
def test_the_front_door_itself_answers_a_named_pdf(fixtures) -> None:
    """The real front door, not the handler in isolation.

    A first version of this test read `turn_frontdoor.py` and compared the character offsets of the
    handler names. Sabotaging the wiring to `pdf_read = None and maybe_handle_pdf_read(...)` left
    that test green — the string was still in the file — so it was measuring the source layout and
    not the dispatch. This drives `_handle_turn_frontdoor` and asserts the fixture's own text comes
    back out of it.
    """
    from types import SimpleNamespace
    from unittest import mock

    from apps.vool_agent import VoolAgent

    agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
    text = f"open the PDF at {fixtures.box / 'quarterly_report.pdf'} and summarise it"
    with mock.patch.object(agent, "_startup_sequence_fast_path", return_value=None), mock.patch(
        "core.agent_runtime.agent.maybe_handle_preference_command", return_value=(False, "")
    ), mock.patch.object(agent, "_maybe_handle_credit_command", return_value=None), mock.patch.object(
        agent, "_maybe_handle_workspace_audit_request", return_value=None
    ):
        outcome = agent._handle_turn_frontdoor(
            raw_user_input=text,
            effective_input=text,
            normalized_input=text,
            source_surface="openclaw",
            session_id="pdf-front-door",
            source_context={"surface": "openclaw", "_owner_local": True},
            persona=mock.sentinel.persona,
            interpreted=SimpleNamespace(understanding_confidence=0.8),
        )

    result = outcome.get("result")
    assert result is not None, "the front door let a named PDF fall through to a model"
    reply = str(result.get("response") or "")
    assert "48213" in reply and "ZEPHYR-9" in reply, reply


@needs_pdfkit
def test_the_pdf_lane_is_wired_above_the_arbiter_and_the_live_info_lane() -> None:
    """Ordering, on top of the dispatch test above — the two catch different mistakes.

    One measured turn spent 236s web-searching Wikipedia for a path on this disk. The PDF lane
    sits above the intent arbiter (whose menu has no PDF option, so its best pick is `read_file`,
    which answers a PDF with `binary_file`), above the machine-read lane (which stands down on an
    explicit path), and above the live-info lane (which is what turned a local path into a query).
    """
    source = Path("core/agent_runtime/turn_frontdoor.py").read_text()
    pdf_at = source.index("pdf_read = maybe_handle_pdf_read(")
    arbiter_at = source.index("_maybe_arbitrate_intent(\n        agent,")
    machine_at = source.index("_maybe_handle_direct_machine_read_request(")
    live_at = source.index("_maybe_handle_live_info_fast_path(")
    assert pdf_at < arbiter_at < machine_at < live_at


@needs_pdfkit
def test_the_reply_is_the_tool_s_own_output_not_a_model_s(fixtures) -> None:
    """The bytes are read, rendered and returned locally; nothing here can invent a number.

    Worth pinning: on this route a PDF's contents never enter a model prompt, so a document read
    this way does not leave the machine even when the turn's model lane is a cloud provider.
    """
    agent = _Agent()
    result = maybe_handle_pdf_read(
        agent,
        f"read {fixtures.box / 'quarterly_report.pdf'}",
        session_id="t",
        source_context={},
    )
    assert result is not None
    assert agent.events == ["pdf.extract_text", "pdf.extract_text"]
    assert "48213" in str(result["response"])


def test_the_pdf_lane_respects_a_forbidden_turn() -> None:
    """Measured by the 2026-08-19 authority census: this lane was the one tool-executing claimant
    with no `action_forbidden` gate -- a turn stamped FORBIDDEN ("do not use tools") still executed
    `pdf.extract_text`. The gate must sit between the import and the call."""
    source = Path("core/agent_runtime/turn_frontdoor.py").read_text()
    import_at = source.index("from core.agent_runtime.fast_paths_pdf import maybe_handle_pdf_read")
    call_at = source.index("pdf_read = maybe_handle_pdf_read(")
    between = source[import_at:call_at]
    assert "action_forbidden" in between, "the pdf lane lost its action_forbidden gate"
    assert "pdf_read = None" in between
