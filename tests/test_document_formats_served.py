"""M3: the six new document formats reach the model as provider-bound payload with provenance.

The drive is the authorship/reader lanes' proven shape: a REAL daemon process with its own
VOOL_HOME, the REAL raw upload door, the REAL ``/api/chat``, and a scripted provider that
answers from a table and keeps every model-bound request IN FULL. What each test asserts is the
claim that can fail: the decisive span -- words that exist only inside the attached file's
content, never in its name -- arrives in the payload the provider was given, under the locator
that says where it came from, together with the disclosures the reader owed ("formulas were not
executed", "objects were not opened").

One substitution, stated: the model itself is a table. That makes this a TRANSPORT-and-
provenance proof. It establishes that the evidence reached the provider boundary with its
provenance attached; it deliberately establishes nothing about whether a model reasons over that
evidence well. No live model, no network beyond 127.0.0.1, no operator state.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._reader_served_rig import CapturingProvider, ServedDaemon, canonical_session
from tests.reader_fixtures import (
    EPUB_CHAPTER_MARKER,
    EPUB_TITLE_MARKER,
    ODT_MARKER,
    PPTX_SLIDE_TWO_MARKER,
    RTF_MARKER,
    epub_book,
    legacy_xls_workbook,
    odt_document,
    pptx_deck,
    quarterly_total,
    rtf_document,
    xlsx_workbook,
)


@pytest.fixture(scope="module")
def rig(tmp_path_factory: pytest.TempPathFactory):
    home = tmp_path_factory.mktemp("doc-formats-m3-home")
    with CapturingProvider() as provider, ServedDaemon(Path(home), provider=provider) as daemon:
        assert daemon.certify().get("state") == "verified"
        yield daemon, provider


def _ask(rig_pair, *, seed: str, name: str, data: bytes, declared_type: str, question: str):
    daemon, provider = rig_pair
    session = canonical_session(seed)
    uploaded = daemon.upload(session_id=session, name=name, data=data, declared_type=declared_type)
    assert uploaded["status"] == 201, uploaded
    record = uploaded["attachment"]
    provider.reset()
    reply = daemon.chat(question, session_id=session, attachments=[record["id"]])
    assert provider.calls, f"the model was never called: {reply}"
    return "\n".join(provider.payloads()), record


# --- XLSX -------------------------------------------------------------------------------------------


def test_served_xlsx_totals_and_their_cache_honesty_reach_the_provider(rig):
    payload, record = _ask(
        rig,
        seed="m3-xlsx",
        name="q3-workbook.xlsx",
        data=xlsx_workbook(),
        declared_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        question="What is the Q3 total in the attached workbook, and were the formulas recomputed?",
    )
    assert record["reader_format"] == "xlsx"
    assert record["reader_unit_kind"] == "sheet"
    # The decisive numbers exist only inside the workbook's cells:
    assert "Westmere | 33600 | 30980 | 41220" in payload
    assert str(quarterly_total("Q3")) in payload
    # ...and they travel with their cell identity and their formula honesty:
    assert "sheet \"Quarterly\"" in payload
    assert "[= SUM(D2:D5); cached, not recalculated]" in payload
    assert "Formulas were not executed" in payload, "the stale-cache disclosure travels with the numbers"


def test_served_xlsx_the_second_sheet_is_not_confused_with_the_first(rig):
    payload, _record = _ask(
        rig,
        seed="m3-xlsx-admin",
        name="q3-workbook.xlsx",
        data=xlsx_workbook(),
        declared_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        question="What does the Admin sheet say?",
    )
    assert "sheet \"Admin\"" in payload
    assert "2026-09-03 (date-formatted serial 46268)" in payload
    assert "#DIV/0!" in payload


# --- legacy XLS -------------------------------------------------------------------------------------


def test_served_xls_totals_and_formula_marks_reach_the_provider(rig):
    payload, record = _ask(
        rig,
        seed="m3-xls",
        name="q3-legacy.xls",
        data=legacy_xls_workbook(),
        declared_type="application/vnd.ms-excel",
        question="Which rows are formulas in the attached legacy workbook?",
    )
    assert record["reader_format"] == "xls"
    assert "Northbridge | 41250 | 38400 | 52310" in payload
    assert f"{quarterly_total('Q3')} [= formula; cached, not recalculated]" in payload
    assert "Formulas were not executed" in payload


# --- PPTX -------------------------------------------------------------------------------------------


def test_served_pptx_deck_order_and_speaker_notes_reach_the_provider(rig):
    payload, record = _ask(
        rig,
        seed="m3-pptx",
        name="quarterly-deck.pptx",
        data=pptx_deck(),
        declared_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        question="What must be flagged to finance, and from which slide?",
    )
    assert record["reader_format"] == "pptx"
    assert record["reader_unit_kind"] == "slide"
    # The decisive span lives ONLY in slide two's speaker notes:
    assert PPTX_SLIDE_TWO_MARKER in payload
    assert "[slide 2]" in payload
    assert "[speaker notes]" in payload
    assert "Flag" in payload and "finance" in payload
    # The table on the same slide travels as a table:
    assert "Northbridge | 41250 | 38400 | 52310" in payload


# --- RTF --------------------------------------------------------------------------------------------


def test_served_rtf_body_and_counts_reach_the_provider(rig):
    payload, record = _ask(
        rig,
        seed="m3-rtf",
        name="memo.rtf",
        data=rtf_document(),
        declared_type="application/rtf",
        question="What is the memo reference in the attached RTF, and does it embed anything?",
    )
    assert record["reader_format"] == "rtf"
    assert RTF_MARKER in payload
    assert "field result keeps this text" in payload
    assert "NOT opened" in payload, "the embedded-object disclosure travels"
    assert "FEEDFACE" not in payload, "object payload bytes must not reach the model as content"


# --- ODT --------------------------------------------------------------------------------------------


def test_served_odt_heading_table_and_close_reach_the_provider(rig):
    payload, record = _ask(
        rig,
        seed="m3-odt",
        name="note.odt",
        data=odt_document(),
        declared_type="application/vnd.oasis.opendocument.text",
        question="Summarise the attached OpenDocument note.",
    )
    assert record["reader_format"] == "odt"
    assert f"# {ODT_MARKER} Heading One" in payload
    assert "[table 1: 2 rows x 2 columns]" in payload
    assert "Item | 42" in payload
    assert "Closing line." in payload


# --- EPUB -------------------------------------------------------------------------------------------


def test_served_epub_spine_order_and_chapter_marker_reach_the_provider(rig):
    payload, record = _ask(
        rig,
        seed="m3-epub",
        name="field-guide.epub",
        data=epub_book(),
        declared_type="application/epub+zip",
        question="Which stone does wear orbit in chapter one of the attached book?",
    )
    assert record["reader_format"] == "epub"
    assert EPUB_CHAPTER_MARKER in payload
    assert "[section 2]" in payload, "the chapter arrives under its spine position"
    assert EPUB_TITLE_MARKER in payload, "the book metadata travels with the sections"
    assert "Foreword" in payload and payload.index("[section 1]") < payload.index("[section 2]")


# --- refusals stay typed at the served door ----------------------------------------------------------


def test_served_encrypted_odt_is_refused_typed_before_any_chat(rig):
    daemon, provider = rig
    session = canonical_session("m3-odt-locked")
    uploaded = daemon.upload(
        session_id=session,
        name="locked.odt",
        data=odt_document(encrypted=True),
        declared_type="application/vnd.oasis.opendocument.text",
    )
    assert uploaded["status"] == 422, uploaded
    assert uploaded["error"] == "password_protected"
    provider.reset()
    reply = daemon.chat("What is in the file?", session_id=session, attachments=[])
    assert reply.get("ok") is not False, "a chat without attachments still answers"


def test_served_epub_with_drm_is_refused_typed(rig):
    daemon, _provider = rig
    session = canonical_session("m3-epub-drm")
    uploaded = daemon.upload(
        session_id=session,
        name="locked-book.epub",
        data=epub_book(with_drm=True),
        declared_type="application/epub+zip",
    )
    assert uploaded["status"] == 422, uploaded
    assert uploaded["error"] == "password_protected"


def test_served_xlsx_credential_in_a_cell_is_refused_and_never_staged(rig):
    import io
    import zipfile

    CANARY = "AKIA" + "Q7X4M2NPLVZW9TCD"
    source = zipfile.ZipFile(io.BytesIO(xlsx_workbook()))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for member in source.namelist():
            payload = source.read(member)
            if member == "xl/sharedStrings.xml":
                payload = payload.replace(b"<si><t>Total</t></si>", f"<si><t>key {CANARY}</t></si>".encode())
            archive.writestr(member, payload)
    daemon, _provider = rig
    session = canonical_session("m3-xlsx-secret")
    uploaded = daemon.upload(
        session_id=session,
        name="leaked.xlsx",
        data=buffer.getvalue(),
        declared_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    assert uploaded["status"] == 422, uploaded
    assert uploaded["error"] == "secret_detected"
