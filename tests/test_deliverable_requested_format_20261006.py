"""A file ordered in a named format is delivered in that format, or not at all.

Measured live on a pinned paid model (gpt-4.1-mini, 2026-10-06): "Summarize the benefits of regular
walking in a short one-page PDF" delivered Benefits_of_Regular_Walking.md, and "Write a Python script
... and a README for it, and zip them up for me" delivered fibonacci.py and README.md as two loose
files. The tool description already asked for the named format. The turn's own words now decide:
`file.deliver` refuses a format the request did not name, before anything is stored, and the
correction round delivers the named one.
"""
from __future__ import annotations

import pytest

from core import deliverables, runtime_paths
from core.deliverables import REQUESTED_FORMATS_KEY, requested_formats
from core.runtime_execution_tools import execute_runtime_tool

SESSION = "openclaw:0123456789abcdef0123"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    runtime_paths.configure_runtime_home(tmp_path / "home")
    yield
    runtime_paths.configure_runtime_home(None)


def _stored() -> list[str]:
    root = deliverables.store_dir()
    return sorted(p.name for p in root.iterdir()) if root.exists() else []


def _deliver(arguments, wanted):
    context = {"runtime_session_id": SESSION}
    if wanted is not None:
        context[REQUESTED_FORMATS_KEY] = list(wanted)
    return execute_runtime_tool("file.deliver", arguments, source_context=context)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Summarize the benefits of regular walking in a short one-page PDF.", ("pdf",)),
        ("Write a Python script that prints primes and a README for it, and zip them up for me.", ("zip",)),
        ("Turn these meeting notes into a Word document please", ("docx",)),
        ("export the monthly totals to Excel", ("xlsx",)),
        ("Give me a CSV file of the planets and their moons", ("csv",)),
        ("I need the report as a PDF and the raw numbers as a CSV file", ("pdf", "csv")),
        ("make me a slide deck about tide pools", ("pptx",)),
        ("Give me the three config files as a tarball", ("tar.gz",)),
    ],
)
def test_the_request_names_its_format(text, expected):
    assert requested_formats(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "is a PDF better than a Word document for a CV?",  # a question about formats orders nothing
        "what does the attached pdf say about the deadline?",
        "write me a 500-word essay about rivers",  # no file ordered
        "",
    ],
)
def test_no_file_ordered_or_no_format_named_is_no_requirement(text):
    assert requested_formats(text) == ()


def test_the_attached_files_format_is_not_the_ordered_one():
    assert requested_formats("Turn the attached pdf into a Word document") == ("docx",)


def test_a_markdown_file_is_refused_when_a_pdf_was_ordered_and_nothing_is_stored():
    result = _deliver({"filename": "walking.md", "content": "# Walking\n\nGood for you."}, ("pdf",))
    assert result.ok is False and result.status == "format_not_requested"
    assert ".pdf" in result.response_text
    assert _stored() == []
    retried = _deliver({"filename": "walking.pdf", "content": "# Walking\n\nGood for you."}, ("pdf",))
    assert retried.ok is True and retried.details["deliverable"]["format"] == "pdf"


def test_loose_files_are_refused_when_a_zip_was_ordered_and_the_zip_is_delivered():
    script = _deliver({"filename": "fib.py", "content": "print(1)\n"}, ("zip",))
    assert script.ok is False and script.status == "format_not_requested"
    assert "ONE call with filename *.zip" in script.response_text
    assert _stored() == []
    bundle = _deliver(
        {"filename": "fib.zip", "files": [{"path": "fib.py", "content": "print(1)\n"}, {"path": "README.md", "content": "# fib\n"}]},
        ("zip",),
    )
    assert bundle.ok is True and bundle.details["deliverable"]["format"] == "zip"


def test_every_named_format_is_allowed_in_a_multi_file_order():
    assert _deliver({"filename": "report.pdf", "content": "# R\n\nText."}, ("pdf", "csv")).ok
    assert _deliver({"filename": "data.csv", "content": "a,b\n1,2\n"}, ("pdf", "csv")).ok


@pytest.mark.parametrize("wanted", [None, ()])
def test_without_a_named_format_any_supported_file_is_delivered(wanted):
    assert _deliver({"filename": "notes.md", "content": "# Notes\n"}, wanted).ok
    assert _deliver({"filename": "tool.py", "content": "print(2)\n"}, wanted).ok


def test_an_unsupported_format_keeps_its_own_specific_refusal():
    result = _deliver({"filename": "haiku.rar", "content": "leaves"}, ("zip",))
    assert result.ok is False and result.status == "format_unsupported"


def test_a_refused_format_named_by_the_request_is_refused_not_rebuilt_as_text():
    # Measured live: "Give me a haiku about autumn as a .rar file." delivered autumn_haiku.rar.txt.
    assert requested_formats("Give me a haiku about autumn as a .rar file.") == ("rar",)
    as_text = _deliver({"filename": "autumn_haiku.txt", "content": "leaves fall"}, ("rar",))
    assert as_text.ok is False and as_text.status == "format_unsupported"
    assert "ZIP" in as_text.response_text
    assert _stored() == []


def test_a_name_claiming_a_refused_format_is_refused_whatever_format_is_passed():
    result = _deliver({"filename": "autumn_haiku.rar", "content": "leaves fall", "format": "txt"}, None)
    assert result.ok is False and result.status == "format_unsupported"
    assert _stored() == []



@pytest.mark.parametrize(
    "text,expected",
    [
        # Measured live 2026-10-06: both were answered as chat prose with no file at all.
        ("Write a shell script that backs up my Documents folder into a dated folder, plus a short usage note, "
         "and zip both files.", ("zip",)),
        ("Export a CSV of the five largest US states by area with their areas in square miles.", ("csv",)),
        ("make me a PDF of the packing list we wrote", ("pdf",)),
        ("zip all of the files and send them over", ("zip",)),
    ],
)
def test_a_verb_ordering_a_named_file_kind_is_a_file_order(text, expected):
    from core.tool_demand_signals import asks_for_a_file

    assert asks_for_a_file(text)
    assert requested_formats(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "how do I export a CSV from Google Sheets?",
        "what is the best way to export a CSV from a database?",
        "give me a csv format example in the reply, no file",
    ],
)
def test_asking_about_a_file_kind_still_orders_nothing(text):
    from core.tool_demand_signals import asks_for_a_file

    assert not asks_for_a_file(text)

@pytest.mark.parametrize("text,expected", [
    ("Give me an HTML page and CSS file as a ZIP archive", ("zip",)),
    ("Give me an HTML page as a tar.gz archive", ("tar.gz",)),
    ("Give me a PDF report and separately a ZIP archive", ("pdf", "zip")),
])
def test_outer_archive_is_not_a_choice_of_loose_member_formats(text, expected):
    assert requested_formats(text) == expected
