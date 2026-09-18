"""The new document formats, exercised directly: XLSX/PPTX (stage 1), RTF/ODT/EPUB (stage 2).

Every claim here is asserted on the thing that would actually go wrong. A spreadsheet reader is
most likely to be quietly wrong about formulas (presenting a stale cache as a fresh answer), date
serials (rendering 46268 where the sheet shows a date) and cell identity (a number nobody can
cite). A presentation reader is most likely to walk slide parts by file name instead of deck
order and to drop speaker notes. An RTF scanner is most likely to let link targets or object
payloads through as text. An EPUB reader is most likely to read the manifest instead of the
spine. Each of those has a test that fails if it happens.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile

import pytest

import core.artifact_readers as readers
from core.artifact_readers import documents
from core.artifact_readers._types import ReaderRefused, ReaderResult
from tests.reader_fixtures import (
    EPUB_CHAPTER_MARKER,
    EPUB_TITLE_MARKER,
    ODT_MARKER,
    PPTX_SLIDE_TWO_MARKER,
    QUARTERLY_HEADERS,
    RTF_MARKER,
    XLS_MARKER,
    docx_document,
    epub_book,
    legacy_xls_workbook,
    odt_document,
    pptx_deck,
    quarterly_total,
    rtf_document,
    xlsx_named_like_a_workbook_but_is_a_zip,
    xlsx_with_linked_workbook_reference,
    xlsx_workbook,
    zip_archive,
)

CANARY_AWS = "AKIA" + "Q7X4M2NPLVZW9TCD"

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PPTX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


def _read(data: bytes, *, name: str) -> ReaderResult:
    return readers.read(data, name=name)


def _rewrite_member(raw: bytes, member: str, old: bytes, new: bytes) -> bytes:
    """One member's payload rewritten -- the honest way to damage a DEFLATED package, whose XML
    is not visible as plain text in the container bytes."""
    source = zipfile.ZipFile(io.BytesIO(raw))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in source.namelist():
            payload = source.read(name)
            if name == member:
                assert old in payload, f"fixture drift: {old!r} not in {member}"
                payload = payload.replace(old, new)
            archive.writestr(name, payload)
    return buffer.getvalue()


# --- the registry grew by two rows, honestly declared -----------------------------------------------


def test_xlsx_and_pptx_are_declared_and_available():
    report = readers.capability_report()
    formats = {row["format"]: row for row in report["formats"]}
    assert formats["xlsx"]["available"] is True
    assert formats["xlsx"]["unit_kind"] == "sheet"
    assert formats["pptx"]["available"] is True
    assert formats["pptx"]["unit_kind"] == "slide"
    for extension in (".xlsx", ".pptx"):
        assert extension in report["available_extensions"]
    # In-process readers: no external decoder to confine, and none claimed.
    assert formats["xlsx"]["external_decoder"] is False
    assert formats["pptx"]["external_decoder"] is False


def test_the_door_offers_the_new_extensions_and_derives_the_accept_list_from_availability():
    from core import chat_attachments

    payload = chat_attachments.limits_payload()
    assert ".xlsx" in payload["accept"] and ".pptx" in payload["accept"]


def test_a_zip_named_xlsx_is_refused_as_a_content_mismatch(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VOOL_DATA_DIR", str(tmp_path))
    from core import chat_attachments, runtime_paths

    if hasattr(runtime_paths.active_data_dir, "cache_clear"):
        runtime_paths.active_data_dir.cache_clear()
    with pytest.raises(chat_attachments.AttachmentRefused) as caught:
        chat_attachments.stage_attachment(
            session_id="s-zip-as-xlsx",
            declared_name="fake.xlsx",
            declared_type=XLSX_MEDIA_TYPE,
            data=xlsx_named_like_a_workbook_but_is_a_zip(),
        )
    assert caught.value.code == "content_mismatch"


def test_a_docx_is_not_rebranded_as_a_spreadsheet():
    """The sniffs distinguish OOXML packages from each other: docx bytes stay docx bytes."""
    assert documents.sniff_xlsx(docx_document()) is False
    assert documents.sniff_pptx(docx_document()) is False
    assert readers.detect(docx_document(), extension=".docx").fmt == "docx"
    assert readers.detect(xlsx_workbook(), extension=".xlsx").fmt == "xlsx"
    assert readers.detect(pptx_deck(), extension=".pptx").fmt == "pptx"


# --- XLSX: the grid, the cells, the honest arithmetic -----------------------------------------------


def test_xlsx_renders_the_quarterly_grid_with_cell_addresses():
    result = _read(xlsx_workbook(), name="q3.xlsx")
    assert result.fmt == "xlsx"
    assert result.source_sha256 == hashlib.sha256(xlsx_workbook()).hexdigest()
    sheet = result.unit('sheet "Quarterly"')
    assert sheet is not None, "each sheet is one addressable unit"
    text = sheet.text
    # The header row, a region row with its cells in their columns, and the shared strings.
    assert "row | A | B | C | D" in text
    assert "Northbridge | 41250 | 38400 | 52310" in text
    assert "Eastgate | 27890 | 31025 | 29740" in text
    # The grid survives a pipe inside a cell's text unambiguously.
    assert "Admin note" not in text, "the second sheet's content stays on its own unit"


def test_xlsx_totals_are_formulas_with_their_cache_named_as_cached():
    result = _read(xlsx_workbook(), name="q3.xlsx")
    text = result.unit('sheet "Quarterly"').text
    assert "= SUM(D2:D5); cached, not recalculated" in text
    assert str(quarterly_total("Q3")) in text
    assert result.units[0].meta["formulas"] == 4
    assert any("Formulas were not executed" in warning for warning in result.warnings)


def test_xlsx_a_stale_cache_is_shown_as_the_files_claim_never_as_the_truth():
    """The cache says 999999 for the Q3 total; the real sum is 148145. The reader must pass the
    file's claim through LABELLED AS A CLAIM, and must not quietly correct it -- recomputing is
    the one thing this reader must never pretend to have done."""
    workbook = xlsx_workbook()
    raw = _rewrite_member(
        workbook, "xl/worksheets/sheet1.xml", f"<v>{quarterly_total('Q3')}</v>".encode(), b"<v>999999</v>"
    )
    result = _read(raw, name="stale.xlsx")
    text = result.unit('sheet "Quarterly"').text
    assert "999999 [= SUM(D2:D5); cached, not recalculated]" in text
    assert str(quarterly_total("Q3")) not in text, "the reader must not substitute its own recomputation"


def test_xlsx_a_formula_without_a_cache_says_so_instead_of_rendering_an_empty_cell():
    result = _read(xlsx_workbook(with_cached_totals=False), name="nocache.xlsx")
    text = result.unit('sheet "Quarterly"').text
    assert "= SUM(B2:B5); NO cached result in the file" in text


def test_xlsx_formatting_semantics_dates_times_and_errors():
    result = _read(xlsx_workbook(), name="q3.xlsx")
    text = result.unit('sheet "Admin"').text
    assert "2026-09-03 (date-formatted serial 46268)" in text
    assert "09:30" in text, "a time-formatted serial renders as a clock time, not as a date"
    assert "(date-formatted serial" in text, "the conversion is stated on the cell"
    assert "#DIV/0!" in text, "a cached error value is shown as the error it is"
    assert "inline text" in text


def test_xlsx_a_macro_is_reported_and_never_read():
    result = _read(xlsx_workbook(with_macro=True), name="macro.xlsx")
    assert result.meta["has_macros"] is True
    assert any("NOT run" in warning for warning in result.warnings)
    body = "\n".join(unit.text for unit in result.units)
    assert "vbaProject" not in body, "macro bytes must not appear in the extraction"


def test_xlsx_an_external_workbook_reference_is_reported_and_never_fetched():
    result = _read(xlsx_with_linked_workbook_reference(), name="linked.xlsx")
    assert result.meta["external_references"] >= 1
    assert any("NOT" in warning and "fetch" in warning.lower() for warning in result.warnings)
    body = "\n".join(unit.text for unit in result.units)
    assert "fileserver" not in body, "the link target is named in warnings only, never inlined as content"


def test_xlsx_row_overflow_is_declared_not_silent(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(documents, "MAX_XLSX_ROWS", 3)
    result = _read(xlsx_workbook(), name="q3.xlsx")
    text = result.unit('sheet "Quarterly"').text
    # Three content rows fit (header, two regions); the rest are declared omitted.
    assert any("rows beyond 3" in entry for entry in result.omitted)
    assert "Westmere" not in text


def test_xlsx_a_damaged_workbook_part_is_refused_with_a_reason():
    raw = _rewrite_member(xlsx_workbook(), "xl/workbook.xml", b"<sheets>", b"<sheets<")
    with pytest.raises(ReaderRefused) as caught:
        _read(bytes(raw), name="broken.xlsx")
    assert caught.value.code == "xlsx_corrupt"
    assert "broken.xlsx" in caught.value.message
    assert caught.value.remediation


def test_xlsx_a_shredded_container_is_refused_even_if_only_the_zip_reader_survives():
    """A download cut short has no central directory. Bytes decide: it is refused as the damaged
    ZIP it now is, never read as an empty workbook."""
    broken = bytearray(xlsx_workbook())
    broken[-64:] = b"\x00" * 64
    with pytest.raises(ReaderRefused) as caught:
        _read(bytes(broken), name="broken.xlsx")
    assert caught.value.code in {"xlsx_corrupt", "archive_corrupt"}


def test_xlsx_sheet_flood_is_bounded_and_declared(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(documents, "MAX_SHEETS", 2)
    # Three sheets: the third must be omitted with the reason, not silently dropped.
    workbook = xlsx_workbook()
    names = ["One", "Two", "Three"]
    sheets = "".join(f'<sheet name="{name}" sheetId="{i + 1}" r:id="rId{i + 1}"/>' for i, name in enumerate(names))
    relationships = "".join(
        f'<Relationship Id="rId{i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        f'Target="worksheets/sheet{i + 1}.xml"/>' for i in range(3)
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        source = zipfile.ZipFile(io.BytesIO(workbook))
        for member in source.namelist():
            if member == "xl/workbook.xml":
                payload = (
                    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    f'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                    f'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                    f"<sheets>{sheets}</sheets></workbook>"
                ).encode()
            elif member == "xl/_rels/workbook.xml.rels":
                payload = (
                    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                    + relationships
                    + "</Relationships>"
                ).encode("utf-8")
            else:
                payload = source.read(member)
            archive.writestr(member, payload)
        for index in range(3):
            archive.writestr(
                f"xl/worksheets/sheet{index + 1}.xml",
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                f"<sheetData><row r=\"1\"><c r=\"A1\"><v>{index}</v></c></row></sheetData></worksheet>",
            )
    result = _read(buffer.getvalue(), name="many.xlsx")
    assert len(result.units) == 2
    assert any('sheet "Three"' in entry for entry in result.omitted)



# --- PPTX: deck order, tables, and the notes a flatter reader drops ---------------------------------


def test_pptx_slides_follow_the_decks_order_not_the_file_names():
    """The fixture numbers its slide parts backwards: a reader that walks slideN.xml in name
    order puts the closing slide first and this test fails."""
    result = _read(pptx_deck(), name="deck.pptx")
    assert result.fmt == "pptx"
    assert [unit.locator for unit in result.units] == ["slide 1", "slide 2", "slide 3"]
    assert "Questions" in result.unit("slide 1").text
    assert "Quarterly Review" in result.unit("slide 3").text


def test_pptx_the_quarterly_table_arrives_as_a_table():
    result = _read(pptx_deck(), name="deck.pptx")
    text = result.unit("slide 2").text
    assert "[table]" in text
    assert " | ".join(QUARTERLY_HEADERS) in text
    assert "Northbridge | 41250 | 38400 | 52310" in text
    assert str(quarterly_total("Q3")) not in text, "a slide table has no total row; none may be invented"


def test_pptx_speaker_notes_join_their_slide_and_the_decisive_note_is_not_elsewhere():
    result = _read(pptx_deck(), name="deck.pptx")
    slide_two = result.unit("slide 2").text
    assert "[speaker notes]" in slide_two
    assert PPTX_SLIDE_TWO_MARKER in slide_two
    others = "\n".join(unit.text for unit in result.units if unit.locator != "slide 2")
    assert PPTX_SLIDE_TWO_MARKER not in others, "the decisive note must come from slide two's own rels"


def test_pptx_links_and_media_are_counted_and_not_fetched():
    result = _read(pptx_deck(with_link=True), name="deck.pptx")
    assert result.meta["external_links"] >= 1
    assert any("external link(s)" in warning for warning in result.warnings)
    assert any("media object(s)" in warning for warning in result.warnings)
    body = "\n".join(unit.text for unit in result.units)
    assert "example.invalid" not in body, "link targets are counted, not inlined as content"


def test_pptx_a_damaged_deck_is_refused():
    raw = _rewrite_member(pptx_deck(), "ppt/presentation.xml", b"<p:sldIdLst>", b"<p:sldIdLst<")
    with pytest.raises(ReaderRefused) as caught:
        _read(bytes(raw), name="broken.pptx")
    assert caught.value.code == "pptx_corrupt"
    assert "broken.pptx" in caught.value.message


def test_pptx_a_zip_without_a_presentation_part_is_not_a_deck():
    """Directly, the pptx reader refuses a ZIP with no presentation part. Through the door, the
    same bytes are refused as a content mismatch, because valid ZIP bytes detect as an archive."""
    with pytest.raises(ReaderRefused) as caught:
        documents.read_pptx(zip_archive({"a.txt": b"hello\n"}), name="deck.pptx")
    assert caught.value.code == "pptx_not_a_package"


# --- the door: staging a new format behaves exactly like staging a PDF -------------------------------


def test_the_door_stages_an_xlsx_and_keeps_the_original_bytes_untouched(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VOOL_DATA_DIR", str(tmp_path))
    from core import chat_attachments, runtime_paths

    if hasattr(runtime_paths.active_data_dir, "cache_clear"):
        runtime_paths.active_data_dir.cache_clear()
    workbook = xlsx_workbook()
    record = chat_attachments.stage_attachment(
        session_id="s-xlsx-stage",
        declared_name="q3.xlsx",
        declared_type=XLSX_MEDIA_TYPE,
        data=workbook,
    )
    assert record["kind"] == "artifact"
    assert record["sha256"] == hashlib.sha256(workbook).hexdigest()
    assert chat_attachments._bytes_path(record["id"]).read_bytes() == workbook
    derivative = json.loads(chat_attachments._derivative_path(record["id"]).read_text("utf-8"))
    assert derivative["format"] == "xlsx"
    assert derivative["source_sha256"] == record["sha256"]
    assert [unit["kind"] for unit in derivative["units"]] == ["sheet", "sheet"]


def test_a_credential_inside_a_cell_is_refused_before_anything_is_stored(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VOOL_DATA_DIR", str(tmp_path))
    from core import chat_attachments, runtime_paths

    if hasattr(runtime_paths.active_data_dir, "cache_clear"):
        runtime_paths.active_data_dir.cache_clear()
    workbook = _rewrite_member(
        xlsx_workbook(),
        "xl/sharedStrings.xml",
        b"<si><t>Total</t></si>",
        f"<si><t>key {CANARY_AWS}</t></si>".encode(),
    )
    before = set((chat_attachments.stage_dir()).glob("*"))
    with pytest.raises(chat_attachments.AttachmentRefused) as caught:
        chat_attachments.stage_attachment(
            session_id="s-xlsx-secret",
            declared_name="q3.xlsx",
            declared_type=XLSX_MEDIA_TYPE,
            data=workbook,
        )
    assert caught.value.code == "secret_detected"
    assert 'sheet "Quarterly"' in caught.value.message, "the refusal must say where the credential was found"
    assert set((chat_attachments.stage_dir()).glob("*")) == before, "bytes or a derivative were written for a refused file"


# --- RTF: what the scanner keeps, and what it only counts -------------------------------------------


def test_rtf_body_text_and_structure_survive():
    result = _read(rtf_document(), name="memo.rtf")
    assert result.fmt == "rtf"
    text = result.units[0].text
    assert RTF_MARKER in text
    assert "pipe | character" in text
    assert "field result keeps this text" in text, "a field's cached RESULT is content"
    assert "\u2011" not in text
    assert "en dash: \u2013 done" in text, "escaped unicode becomes the real character"
    assert result.units[0].meta["paragraphs"] >= 3


def test_rtf_objects_pictures_and_link_targets_are_counted_not_extracted():
    result = _read(rtf_document(), name="memo.rtf")
    text = result.units[0].text
    assert result.meta["embedded_objects"] == 1
    assert result.meta["pictures"] == 1
    assert result.meta["hyperlinks"] == 1
    assert any("NOT opened" in warning for warning in result.warnings)
    # None of the skipped material may appear as content:
    assert "FEEDFACE" not in text, "object hex payload must not become text"
    assert "tracker.example.invalid" not in text, "a link TARGET is not document text"
    assert "Hidden title" not in text, "info-group metadata is not body text"
    assert "Calibri" not in text, "font tables are not body text"


def test_rtf_unicode_substitutes_are_not_doubled():
    result = _read(rtf_document(), name="memo.rtf")
    text = result.units[0].text
    assert "\u2013 done" in text
    assert "? done" not in text, "the substitute byte after \\u must be skipped, not emitted"


def test_rtf_deep_brace_nesting_is_refused():
    hostile = b"{\\rtf1" + b"{" * 600 + b"}" * 600
    with pytest.raises(ReaderRefused) as caught:
        _read(hostile, name="hostile.rtf")
    assert caught.value.code == "rtf_too_deep"


# --- ODT --------------------------------------------------------------------------------------------


def test_odt_headings_paragraphs_and_tables_arrive_in_order():
    result = _read(odt_document(), name="note.odt")
    assert result.fmt == "odt"
    text = result.units[0].text
    assert f"# {ODT_MARKER} Heading One" in text
    assert "[table 1: 2 rows x 2 columns]" in text
    assert "Item | 42" in text
    assert text.index("# ") < text.index("[table") < text.index("Closing line.")


def test_odt_macros_are_reported_and_never_read():
    result = _read(odt_document(with_macros=True), name="macro.odt")
    assert result.meta["has_macros"] is True
    assert any("NOT run" in warning for warning in result.warnings)
    assert "Module1" not in result.units[0].text


def test_odt_encrypted_content_is_refused_as_password_protected():
    with pytest.raises(ReaderRefused) as caught:
        _read(odt_document(encrypted=True), name="locked.odt")
    assert caught.value.code == "password_protected"
    assert caught.value.remediation


def test_odt_sniff_rejects_other_odf_kinds():
    """An ODF spreadsheet or presentation is not claimed by the text reader: bytes decide, and
    those bytes belong to no reader yet -- the door refuses them rather than mis-reading them."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("mimetype", "application/vnd.oasis.opendocument.spreadsheet")
        archive.writestr("content.xml", "<x/>")
    assert documents.sniff_odt(buffer.getvalue()) is False


# --- EPUB -------------------------------------------------------------------------------------------


def test_epub_sections_follow_the_spine_not_the_manifest():
    result = _read(epub_book(), name="book.epub")
    assert result.fmt == "epub"
    assert [unit.locator for unit in result.units] == ["book metadata", "section 1", "section 2"]
    assert "Foreword" in result.unit("section 1").text
    assert EPUB_CHAPTER_MARKER in result.unit("section 2").text
    assert EPUB_TITLE_MARKER in result.unit("book metadata").text


def test_epub_headings_tables_and_lists_are_kept():
    result = _read(epub_book(), name="book.epub")
    text = result.unit("section 2").text
    assert "# Wear orbits the stone" in text
    assert "[table]" in text and "alpha | 5" in text
    assert "• bullet one" in result.unit("section 1").text


def test_epub_images_and_links_are_not_fetched():
    result = _read(epub_book(), name="book.epub")
    assert any("not rendered" in warning for warning in result.warnings)
    body = "\n".join(unit.text for unit in result.units)
    assert "\x89PNG" not in body


def test_epub_drm_is_refused_not_scraped():
    with pytest.raises(ReaderRefused) as caught:
        _read(epub_book(with_drm=True), name="locked.epub")
    assert caught.value.code == "password_protected"


def test_epub_a_broken_container_is_refused():
    with pytest.raises(ReaderRefused) as caught:
        documents.read_epub(zip_archive({"a.txt": b"hi\n"}), name="broken.epub")
    assert caught.value.code == "epub_not_a_package"


# --- the door: all three formats stage like any other artifact ---------------------------------------


@pytest.mark.parametrize(
    ("filename", "media_type", "builder", "expected_format"),
    [
        ("memo.rtf", "application/rtf", lambda: rtf_document(), "rtf"),
        ("note.odt", "application/vnd.oasis.opendocument.text", lambda: odt_document(), "odt"),
        ("book.epub", "application/epub+zip", lambda: epub_book(), "epub"),
    ],
)
def test_the_door_stages_rtf_odt_and_epub_with_untouched_originals(
    tmp_path, monkeypatch: pytest.MonkeyPatch, filename, media_type, builder, expected_format
):
    monkeypatch.setenv("VOOL_DATA_DIR", str(tmp_path))
    from core import chat_attachments, runtime_paths

    if hasattr(runtime_paths.active_data_dir, "cache_clear"):
        runtime_paths.active_data_dir.cache_clear()
    blob = builder()
    record = chat_attachments.stage_attachment(
        session_id=f"s-{expected_format}-stage",
        declared_name=filename,
        declared_type=media_type,
        data=blob,
    )
    assert record["kind"] == "artifact"
    assert record["sha256"] == hashlib.sha256(blob).hexdigest()
    assert chat_attachments._bytes_path(record["id"]).read_bytes() == blob
    derivative = json.loads(chat_attachments._derivative_path(record["id"]).read_text("utf-8"))
    assert derivative["format"] == expected_format
    assert derivative["units"], "an extraction with no units cannot carry provenance"


def test_a_credential_inside_an_epub_section_is_refused(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VOOL_DATA_DIR", str(tmp_path))
    from core import chat_attachments, runtime_paths

    if hasattr(runtime_paths.active_data_dir, "cache_clear"):
        runtime_paths.active_data_dir.cache_clear()
    book = _rewrite_member(
        epub_book(),
        "OEBPS/text/ch1.xhtml",
        b"First paragraph of chapter one.",
        f"First paragraph with key {CANARY_AWS} inside.".encode(),
    )
    before = set((chat_attachments.stage_dir()).glob("*"))
    with pytest.raises(chat_attachments.AttachmentRefused) as caught:
        chat_attachments.stage_attachment(
            session_id="s-epub-secret",
            declared_name="book.epub",
            declared_type="application/epub+zip",
            data=book,
        )
    assert caught.value.code == "secret_detected"
    assert set((chat_attachments.stage_dir()).glob("*")) == before


# --- legacy XLS: the same grid and the same honesty, through the pinned decoder ----------------------


def test_xls_renders_the_quarterly_grid_with_cell_addresses():
    raw = legacy_xls_workbook()
    result = _read(raw, name="q3.xls")
    assert result.fmt == "xls"
    assert result.source_sha256 == hashlib.sha256(raw).hexdigest()
    text = result.unit('sheet "Quarterly"').text
    assert "row | A | B | C | D" in text
    assert "Northbridge | 41250 | 38400 | 52310" in text
    assert "Eastgate | 27890 | 31025 | 29740" in text
    assert XLS_MARKER in text


def test_xls_formula_cells_are_marked_and_their_cache_is_a_claim_not_a_truth():
    result = _read(legacy_xls_workbook(), name="q3.xls")
    text = result.unit('sheet "Quarterly"').text
    # All three total cells carry BIFF FORMULA records; each shows its cached value as a claim.
    assert f"{quarterly_total('Q1')} [= formula; cached, not recalculated]" in text
    assert f"{quarterly_total('Q3')} [= formula; cached, not recalculated]" in text
    assert result.units[0].meta["formulas"] == 3
    assert any("Formulas were not executed" in warning for warning in result.warnings)


def test_xls_boolean_error_and_date_cells_render_honestly():
    result = _read(legacy_xls_workbook(), name="q3.xls")
    text = result.unit('sheet "Quarterly"').text
    assert "TRUE" in text
    assert "#DIV/0!" in text
    assert "2026-09-03 (date-formatted serial 46268.0)" in text


def test_xls_macros_are_reported_and_never_read():
    result = _read(legacy_xls_workbook(with_macros=True), name="macro.xls")
    assert result.meta["has_macros"] is True
    assert any("NOT run" in warning for warning in result.warnings)


def test_xls_encrypted_is_refused_as_password_protected():
    with pytest.raises(ReaderRefused) as caught:
        _read(legacy_xls_workbook(encrypted=True), name="locked.xls")
    assert caught.value.code == "password_protected"
    assert caught.value.remediation


def test_xls_without_its_decoder_is_blocked_typed_never_read_as_empty(monkeypatch: pytest.MonkeyPatch):
    """The pinned xlrd is an OPTIONAL lane dependency. Absent it, the format is BLOCKED with a
    reason and a remediation -- never an empty extraction, never a crash."""
    monkeypatch.setattr(documents, "xlrd_available", lambda: False)
    report = readers.capability_report()
    formats = {row["format"]: row for row in report["formats"]}
    assert formats["xls"]["available"] is False
    assert "xlrd" in formats["xls"]["blocked_reason"]
    with pytest.raises(readers.ReaderUnavailable) as caught:
        readers.read(legacy_xls_workbook(), name="q3.xls", extension=".xls")
    assert caught.value.code == "xls_decoder_unavailable"
    assert "xlrd==2.0.1" in caught.value.remediation


def test_xls_ole_bytes_are_not_claimed_by_the_doc_reader(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """Both formats live in OLE containers; only the workbook stream's presence tells them apart.
    A workbook named .doc is refused at the door as a content mismatch, never converted."""
    from core.artifact_readers import office

    assert documents.sniff_xls(docx_document()) is False
    raw = legacy_xls_workbook()
    assert office.sniff_doc(raw) is False
    monkeypatch.setenv("VOOL_DATA_DIR", str(tmp_path))
    from core import chat_attachments, runtime_paths

    if hasattr(runtime_paths.active_data_dir, "cache_clear"):
        runtime_paths.active_data_dir.cache_clear()
    with pytest.raises(chat_attachments.AttachmentRefused) as caught:
        chat_attachments.stage_attachment(
            session_id="s-xls-as-doc",
            declared_name="q3.doc",
            declared_type="application/msword",
            data=raw,
        )
    assert caught.value.code == "content_mismatch"


def test_xlsx_wide_overflow_is_clamped_and_declared(monkeypatch: pytest.MonkeyPatch):
    """One stray cell far to the right must not stretch every row into a vast empty grid."""
    monkeypatch.setattr(documents, "MAX_XLSX_GRID_COLUMNS", 2)
    raw = _rewrite_member(
        xlsx_workbook(),
        "xl/worksheets/sheet1.xml",
        b'<row r="6">',
        b'<row r="5"><c r="AZ5"><v>7</v></c></row><row r="6">',
    )
    result = _read(raw, name="q3.xlsx")
    text = result.unit('sheet "Quarterly"').text
    assert any("columns beyond 2" in entry for entry in result.omitted)
    assert "| |" not in text, "no 24-column stretch of empty cells may be rendered"
    assert text.splitlines()[1] == "row | A | B"


def test_rtf_table_cells_stay_separated():
    raw = (
        r"{\rtf1\pard\qc Row one\cell Second cell\cell \row Tail after the table\par}"
    ).encode("ascii")
    text = _read(raw, name="table.rtf").units[0].text
    assert "Row one | Second cell" in text, "cell boundaries render as separators, not glue"
    assert "Tail after the table" in text


def test_epub_non_xhtml_spine_sections_are_declared_not_silent():
    raw = _rewrite_member(
        epub_book(),
        "OEBPS/content.opf",
        b'<item id="c3" href="cover.png" media-type="image/png"/>',
        b'<item id="c3" href="scan.pdf" media-type="application/pdf"/>',
    )
    result = _read(raw, name="fixed.epub")
    assert any("section 3 (application/pdf)" in entry for entry in result.omitted)


# ---------------------------------------------------------------------------
# The missing-decoder path. The lane that wrote read_xls always had the pinned
# xlrd on its path, so the branch that fires WITHOUT it was never executed and
# shipped raising `NameError: name 'ReaderUnavailable' is not defined` instead
# of the typed refusal the module documents ("BLOCKED and says so -- never read
# as empty"). Integration reproduced that on a machine with no xlrd. This test
# executes the branch with the decoder forced absent, so the typed outcome is
# the thing under test rather than a side effect of the environment.
# ---------------------------------------------------------------------------
def test_xls_without_the_pinned_decoder_refuses_typed_and_never_reads_empty(monkeypatch):
    from core.artifact_readers import documents as _documents
    from core.artifact_readers._types import ReaderUnavailable

    monkeypatch.setattr(_documents, "_import_xrd", lambda: None)

    with pytest.raises(ReaderUnavailable) as caught:
        _documents.read_xls(_documents._XLS_MAGIC + b"\x00" * 64, name="quarterly.xls")

    refusal = caught.value
    assert refusal.code == "xls_decoder_unavailable", refusal.code
    assert "xlrd==2.0.1" in str(refusal), str(refusal)
    # The point of the law: an unreadable format is BLOCKED, not silently empty.
    assert "not read" in str(refusal).lower()
