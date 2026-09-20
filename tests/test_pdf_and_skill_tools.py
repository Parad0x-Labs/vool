"""Two capabilities the runtime did not have, driven against real files.

A PDF was the one document type nothing could open. Every read tool returns bytes or lines, so a
PDF reached the model as binary noise and the turn either refused or invented a summary — measured
on the owner's own workspace, where "the website displays most of its data in PDF format so the
agent needs to read it" is the request that started this.

"can we create a new skill?" was the other. The runtime could already LOAD skills and had no way to
WRITE one, so adding a capability meant leaving the product and hand-authoring Markdown with correct
YAML frontmatter into exactly the right folder.

Nothing was installed for either. macOS ships PDFKit and Vision and `/usr/bin/python3` ships the
PyObjC bindings, so the PDF tools run a helper under the SYSTEM interpreter and read JSON back —
this project's virtualenv gains nothing, and no package is added on a machine that holds live keys.

These tests build a real PDF, read it back, author a real skill, and load it with the real loader.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest import mock

import pytest

from core.runtime_execution_tools import execute_runtime_tool
from core.runtime_tool_contracts import runtime_tool_contract_map

SYSTEM_PYTHON = "/usr/bin/python3"
_HAS_PDFKIT = (
    Path(SYSTEM_PYTHON).exists()
    and subprocess.run(
        [SYSTEM_PYTHON, "-c", "import Quartz, Foundation"], capture_output=True
    ).returncode == 0
)
needs_pdfkit = pytest.mark.skipif(not _HAS_PDFKIT, reason="macOS PDFKit bindings not present")

_MAKE_PDF = r'''
import sys, Quartz, CoreFoundation
path, line = sys.argv[1], sys.argv[2]
url = CoreFoundation.CFURLCreateWithFileSystemPath(None, path, CoreFoundation.kCFURLPOSIXPathStyle, False)
ctx = Quartz.CGPDFContextCreateWithURL(url, Quartz.CGRectMake(0, 0, 612, 792), None)
Quartz.CGPDFContextBeginPage(ctx, None)
Quartz.CGContextSelectFont(ctx, b"Helvetica", 28.0, Quartz.kCGEncodingMacRoman)
Quartz.CGContextSetTextDrawingMode(ctx, Quartz.kCGTextFill)
Quartz.CGContextShowTextAtPoint(ctx, 60, 700, line.encode("mac-roman"), len(line))
Quartz.CGPDFContextEndPage(ctx)
Quartz.CGPDFContextClose(ctx)
'''


@pytest.fixture
def a_pdf(tmp_path):
    target = tmp_path / "report.pdf"
    proc = subprocess.run(
        [SYSTEM_PYTHON, "-c", _MAKE_PDF, str(target), "Quarterly totals 4821"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert target.is_file()
    return target


# --------------------------------------------------------------------------------------
# pdf.extract_text
# --------------------------------------------------------------------------------------


@needs_pdfkit
def test_a_pdf_is_read(a_pdf) -> None:
    from core.pdf_tools import extract_text

    result = extract_text(str(a_pdf))

    assert result["status"] == "ok"
    assert "Quarterly totals 4821" in result["text"]
    assert result["pages_total"] == 1
    assert result["characters"] > 0


@needs_pdfkit
def test_the_page_number_is_in_the_text(a_pdf) -> None:
    """A citation into a 60-page PDF is worthless without knowing which page it came from."""

    from core.pdf_tools import extract_text

    assert "[page 1]" in extract_text(str(a_pdf))["text"]


_MAKE_MULTIPAGE_PDF = r'''
import sys, Quartz, CoreFoundation
path, pages = sys.argv[1], int(sys.argv[2])
url = CoreFoundation.CFURLCreateWithFileSystemPath(None, path, CoreFoundation.kCFURLPOSIXPathStyle, False)
ctx = Quartz.CGPDFContextCreateWithURL(url, Quartz.CGRectMake(0, 0, 612, 792), None)
for n in range(1, pages + 1):
    Quartz.CGPDFContextBeginPage(ctx, None)
    Quartz.CGContextSelectFont(ctx, b"Helvetica", 28.0, Quartz.kCGEncodingMacRoman)
    Quartz.CGContextSetTextDrawingMode(ctx, Quartz.kCGTextFill)
    body = ("PAGE MARKER " + str(n)).encode("mac-roman")
    Quartz.CGContextShowTextAtPoint(ctx, 60, 700, body, len(body))
    Quartz.CGPDFContextEndPage(ctx)
Quartz.CGPDFContextClose(ctx)
'''


@pytest.fixture
def a_three_page_pdf(tmp_path):
    target = tmp_path / "long.pdf"
    proc = subprocess.run(
        [SYSTEM_PYTHON, "-c", _MAKE_MULTIPAGE_PDF, str(target), "3"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return target


@needs_pdfkit
def test_max_pages_is_honoured(a_three_page_pdf) -> None:
    """A one-page fixture cannot test this - `max_pages=1` and `max_pages=0` agree on it.

    Added after a sabotage that ignored `max_pages` entirely passed the whole file.
    """

    from core.pdf_tools import extract_text

    everything = extract_text(str(a_three_page_pdf))
    assert everything["pages_total"] == 3
    assert everything["pages_read"] == 3
    assert "PAGE MARKER 3" in everything["text"]

    bounded = extract_text(str(a_three_page_pdf), max_pages=2)
    assert bounded["pages_read"] == 2
    assert bounded["pages_total"] == 3, "the total must still be honest about what was skipped"
    assert "PAGE MARKER 2" in bounded["text"]
    assert "PAGE MARKER 3" not in bounded["text"]


def test_a_missing_file_is_not_an_empty_pdf() -> None:
    from core.pdf_tools import extract_text

    result = extract_text("/nope/missing.pdf")
    assert result["status"] == "not_found"
    assert result["text"] == ""


def test_a_non_pdf_is_refused_without_spawning_anything(tmp_path) -> None:
    """PDFKit would reject a .txt too, so the status alone proves nothing about the check.

    What the suffix test actually buys is refusing before a subprocess starts. A sabotage that
    deleted it passed on status, which is how the assertion got sharpened to the real behaviour.
    """

    from core import pdf_tools

    other = tmp_path / "notes.txt"
    other.write_text("hello\n", encoding="utf-8")

    with mock.patch.object(
        pdf_tools, "_run_helper", side_effect=AssertionError("spawned a helper for a .txt")
    ):
        result = pdf_tools.extract_text(str(other))

    assert result["status"] == "unreadable"
    assert "not a .pdf" in result["reason"]


def test_a_missing_framework_says_so_rather_than_returning_nothing(tmp_path) -> None:
    """`ok` with an empty string would read as "this PDF is blank" - the one thing it is not."""

    from core import pdf_tools

    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    with mock.patch.object(pdf_tools, "_SYSTEM_PYTHON", "/nonexistent/python3"):
        result = pdf_tools.extract_text(str(pdf))

    assert result["status"] == "missing_dependency"
    assert "system Python" in result["reason"]


@needs_pdfkit
def test_a_pdf_with_no_text_layer_names_the_next_step(tmp_path) -> None:
    """A scanned PDF is not blank, and saying "ok, 0 characters" is how a turn confabulates."""

    from core.pdf_tools import extract_text

    blank = tmp_path / "scan.pdf"
    proc = subprocess.run(
        [SYSTEM_PYTHON, "-c", _MAKE_PDF.replace(
            "Quartz.CGContextShowTextAtPoint(ctx, 60, 700, line.encode(\"mac-roman\"), len(line))", "pass"
        ), str(blank), "unused"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr

    result = extract_text(str(blank))
    assert result["status"] == "no_text_layer"
    assert "pdf.ocr" in result["reason"]


# --------------------------------------------------------------------------------------
# pdf.ocr
# --------------------------------------------------------------------------------------


@needs_pdfkit
def test_on_device_recognition_reads_a_rendered_page(a_pdf) -> None:
    """Vision, loaded from the system framework. Nothing leaves the Mac."""

    from core.pdf_tools import ocr

    result = ocr(str(a_pdf), max_pages=1)
    if result["status"] == "missing_dependency":
        pytest.skip(f"Vision unavailable: {result.get('reason')}")

    assert result["status"] == "ok"
    assert "4821" in result["text"], result["text"]


def test_ocr_refuses_a_missing_file() -> None:
    from core.pdf_tools import ocr

    assert ocr("/nope/missing.pdf")["status"] == "not_found"


# --------------------------------------------------------------------------------------
# skill.create / validate / install
# --------------------------------------------------------------------------------------


@pytest.fixture
def skills(tmp_path):
    """A real plugins tree, isolated from the operator's own."""

    import core.skill_tools as module

    with mock.patch.object(module, "plugins_root", lambda: tmp_path):
        yield module, tmp_path


def test_the_three_steps_end_with_a_skill_the_real_loader_loads(skills) -> None:
    """The whole point. A skill that is written but never loaded is a file, not a capability."""

    module, root = skills
    created = module.create_skill(
        name="Release Notes",
        description="Use when the user asks for release notes.",
        body="Read CHANGELOG.md, then summarise by version.",
        allowed_tools=["workspace.read_file"],
        triggers=["release notes"],
    )
    assert created["status"] == "ok"
    assert created["staged"] is True

    assert module.validate_skill(created["path"])["status"] == "ok"

    installed = module.install_skill(created["path"])
    assert installed["status"] == "ok"
    assert installed["active"] is True

    from core.plugin_skills import load_skills, rank_skills

    loaded = load_skills(root / "plugins" / "local-skills", plugin_id="local-skills")
    assert [s.name for s in loaded] == ["Release Notes"]
    assert [s.name for s in rank_skills(loaded, "can you write the release notes?")] == ["Release Notes"]


def test_creating_does_not_activate(skills) -> None:
    """`create` writes a draft. Authoring must not be an authorisation to change behaviour."""

    module, root = skills
    created = module.create_skill(name="Draft", description="Use when x.", body="do y")

    assert module.STAGING_DIRNAME in created["path"]
    assert not (root / "plugins").exists()


def test_a_skill_with_no_description_is_refused(skills) -> None:
    """`match_corpus` is name + description + triggers, so one without a description never ranks."""

    module, _root = skills
    result = module.create_skill(name="X", description="", body="do y")

    assert result["status"] == "invalid_arguments"
    assert "never rank" in result["reason"] or "when the skill applies" in result["reason"]


def test_validation_names_a_tool_this_runtime_does_not_have(skills) -> None:
    """An unknown tool narrows the model to nothing on the turn the skill matches."""

    module, _root = skills
    created = module.create_skill(
        name="Bad Tools", description="Use when x.", body="do y",
        allowed_tools=["workspace.read_file", "nope.does_not_exist"],
    )
    verdict = module.validate_skill(created["path"])

    assert verdict["status"] == "invalid"
    assert verdict["unknown_tools"] == ["nope.does_not_exist"]


def test_install_revalidates_rather_than_trusting_that_validate_ran(skills) -> None:
    """They are separate tool calls and a model can skip one."""

    module, root = skills
    broken = root / "hand-written"
    broken.mkdir(parents=True)
    (broken / "SKILL.md").write_text("no frontmatter at all\n", encoding="utf-8")

    result = module.install_skill(str(broken))

    assert result["status"] == "refused"
    assert result["problems"]
    assert not (root / "plugins").exists()


def test_an_existing_skill_is_not_silently_replaced(skills) -> None:
    module, _root = skills
    created = module.create_skill(name="Once", description="Use when x.", body="do y")

    again = module.create_skill(name="Once", description="Use when x.", body="different")
    assert again["status"] == "exists"

    forced = module.create_skill(
        name="Once", description="Use when x.", body="different", overwrite=True
    )
    assert forced["status"] == "ok"
    assert Path(created["path"]).read_text(encoding="utf-8").endswith("different\n")


def test_a_description_containing_a_colon_does_not_break_the_header(skills) -> None:
    """YAML frontmatter written by hand is where this normally goes wrong."""

    module, _root = skills
    created = module.create_skill(
        name="Colon", description="Use when the user says: build me a report", body="do y"
    )
    verdict = module.validate_skill(created["path"])

    assert verdict["status"] == "ok"
    assert verdict["description"] == "Use when the user says: build me a report"


# --------------------------------------------------------------------------------------
# Registered, not merely written
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "intent", ["pdf.extract_text", "pdf.ocr", "skill.create", "skill.validate", "skill.install"]
)
def test_each_tool_is_in_the_catalogue(intent: str) -> None:
    """A handler no model can see is a handler that never runs."""

    assert intent in runtime_tool_contract_map()


@needs_pdfkit
def test_the_pdf_tool_runs_through_the_real_executor(a_pdf) -> None:
    result = execute_runtime_tool(
        "pdf.extract_text", {"path": str(a_pdf)}, source_context={"surface": "api"}
    )

    assert result is not None and result.ok
    assert "Quarterly totals 4821" in result.response_text
    assert result.details["observation"]["intent"] == "pdf.extract_text"


def test_the_skill_tool_runs_through_the_real_executor(skills) -> None:
    _module, _root = skills
    result = execute_runtime_tool(
        "skill.validate", {"path": "/nope"}, source_context={"surface": "api"}
    )

    assert result is not None
    assert result.status == "not_found"
    assert result.ok is False


def test_installing_a_skill_needs_explicit_opt_in() -> None:
    """It changes what the assistant does on every later turn, so it is not an ordinary write."""

    contract = runtime_tool_contract_map()["skill.install"]

    assert contract.side_effect_class == "runtime_capability_change"
    assert contract.approval_requirement == "explicit_user_opt_in"
    assert runtime_tool_contract_map()["pdf.extract_text"].read_only is True


def test_no_package_was_added_to_this_project() -> None:
    """The supply-chain rule is the reason for the subprocess: this machine holds live keys.

    History: this pin used to forbid EVERY pdf package in pyproject, because core/pdf_tools
    parses through the macOS system Python (PDFKit/Quartz) and must stay dependency-free.
    The artifact-reader lane later added `pypdf==6.16.2` as a measured, DELIBERATE exception:
    it runs only inside the confined decoder subprocess (core/artifact_readers/pdf.py — a
    hostile PDF meets pypdf inside the same sandbox every other decoder gets), with its
    projections kept in sync in requirements*.txt and installer/bundle. Everything else
    pdf-ish stays out: pyobjc would pull the PDFKit lane into pip, and the PyPDF2/pdfminer/
    pdfplumber/PyMuPDF family has no lane at all.
    """

    from core import pdf_tools

    assert pdf_tools._SYSTEM_PYTHON == "/usr/bin/python3"
    pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    for package in ("PyPDF2", "pdfminer", "pdfplumber", "PyMuPDF", "pyobjc"):
        assert package.lower() not in pyproject.lower(), package
    # The one sanctioned pdf dependency is the confined-reader fallback, pinned exactly.
    assert "pypdf==6.16.2" in pyproject


# --------------------------------------------------------------------------------------
# What the gauntlet's "review the permissions" instruction actually caught
# --------------------------------------------------------------------------------------


class TestPermissionsResolveFromTheContract:
    """A read-only tool that needs an approval prompt on every call is an unusable tool.

    `actions_for_tool` derived permissions by matching on the intent STRING — a `workspace.`/
    `machine.` prefix, then substrings like "read" and "write". A new family matches none of that,
    so all five of these resolved to `unknown_side_effect`, which DENIES in Auto mode: a read-only
    PDF reader would have prompted on every call and been refused outright in Auto.

    The golden regeneration is what surfaced it. `RuntimeToolContract.permission_actions` already
    existed for exactly this and had no reader anywhere.
    """

    @pytest.mark.parametrize(
        "intent,expected",
        [
            ("pdf.extract_text", ["read_files"]),
            ("pdf.ocr", ["read_files"]),
            ("skill.validate", ["read_files"]),
            ("skill.create", ["create_files"]),
            ("skill.install", ["change_settings", "create_files"]),
        ],
    )
    def test_the_declared_actions_are_used(self, intent: str, expected: list[str]) -> None:
        from core.mode_permission_policy import actions_for_tool

        assert sorted(a.value for a in actions_for_tool(intent, {})) == sorted(expected)

    def test_reading_a_pdf_is_allowed_in_auto(self) -> None:
        """The whole point: a read must not need a prompt."""

        from core.mode_permission_policy import PermissionAction, actions_for_tool

        assert PermissionAction.UNKNOWN_SIDE_EFFECT not in actions_for_tool("pdf.extract_text", {})
        assert actions_for_tool("pdf.extract_text", {}) == actions_for_tool("pdf.ocr", {})

    def test_installing_a_skill_is_not(self) -> None:
        from core.mode_permission_policy import PermissionAction, actions_for_tool

        assert PermissionAction.CHANGE_SETTINGS in actions_for_tool("skill.install", {})

    def test_a_tool_that_declares_nothing_still_uses_the_old_derivation(self) -> None:
        """The declaration is an addition, not a replacement — every existing tool is untouched."""

        from core.mode_permission_policy import PermissionAction, actions_for_tool

        assert PermissionAction.READ_FILES in actions_for_tool("machine.read_file", {"path": "x"})
        assert PermissionAction.LIST_DIRECTORIES in actions_for_tool("workspace.list_files", {})
        assert actions_for_tool("sandbox.run_command", {"command": "rm -rf /"})
