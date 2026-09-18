"""M1: PDF, DOCX and ZIP through the REAL served door, with provenance that reaches the model.

What these tests establish, precisely:

* A document dropped on the composer reaches ``/api/chat`` as **extracted, addressed text** --
  not as bytes, not as a filename, not as a promise. The assertions are on the payload the
  provider actually received, captured whole.
* Evidence from the END of a document reaches it too. Every fixture hides a marker on its last
  page or in a member the head of the file never mentions, so a reader that delivers only the
  beginning fails these tests rather than passing them quietly.
* A scanned page -- pixels, no text layer, the words present nowhere in the file as characters --
  arrives as recognised text with its OCR confidence stated.
* Refusals stay refusals at the door: a hostile archive is named and bounded before it is read.

What they deliberately do NOT establish: whether a model reasons correctly over any of it. The
provider here is scripted, so these are transport-and-provenance proofs. Arithmetic is tested
against the EXTRACTION (the test does the sum from the extracted table and checks it against the
fixture's own numbers), which proves the table survived extraction in a form arithmetic can use --
a different and weaker claim than "the model added it up", and stated as the weaker one.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests._reader_served_rig import CapturingProvider, ServedDaemon, canonical_session
from tests.reader_fixtures import (
    QUARTERLY_ROWS,
    docx_document,
    native_text_pdf,
    quarterly_row_total,
    quarterly_total,
    scanned_pdf,
    zip_archive,
    zip_bomb,
    zip_with_symlink_member,
    zip_with_traversal_member,
)

SCAN_LINES = ["INVOICE 88213", "VENDOR: ATLAS FREIGHT", "AMOUNT DUE: 4820", "DUE DATE: 2026-10-14"]


@pytest.fixture(scope="module")
def rig(tmp_path_factory: pytest.TempPathFactory):
    """One daemon, one scripted provider, certified once. Booting per test costs minutes."""
    home = tmp_path_factory.mktemp("reader-m1-home")
    with CapturingProvider() as provider, ServedDaemon(Path(home), provider=provider) as daemon:
        result = daemon.certify()
        assert result.get("state") == "verified", f"scripted provider did not certify: {result}"
        yield daemon, provider


def _ask(rig_pair, *, name: str, data: bytes, declared_type: str, question: str, seed: str) -> tuple[str, dict]:
    """Upload through the real door, ask through the real door, return the captured payload."""
    daemon, provider = rig_pair
    session = canonical_session(seed)
    uploaded = daemon.upload(session_id=session, name=name, data=data, declared_type=declared_type)
    assert uploaded["status"] == 201, uploaded
    provider.reset()
    daemon.chat(question, session_id=session, attachments=[uploaded["attachment"]["id"]])
    payloads = provider.payloads()
    assert payloads, "the model was never called, so nothing can be claimed about what it received"
    return "\n".join(payloads), uploaded["attachment"]


# --- PDF -------------------------------------------------------------------------------------


def test_pdf_reaches_the_model_with_page_provenance_and_last_page_evidence(rig):
    question = "In the attached PDF, what is the total of the Q1 column, and what is the closing reference on the last page?"
    payload, record = _ask(
        rig, name="report.pdf", data=native_text_pdf(), declared_type="application/pdf", question=question, seed="m1-pdf-pages"
    )
    assert record["kind"] == "artifact" and record["reader_format"] == "pdf"
    assert record["reader_units"] == 3 and record["reader_unit_kind"] == "page"
    # The user's own question travels with the evidence: without it the model is being asked
    # nothing, and "the document reached the payload" would be a claim about a monologue.
    assert "closing reference on the last page" in payload
    assert "[page 1]" in payload and "[page 3]" in payload
    # Evidence from the END, which no head-truncating reader can deliver.
    assert "GRANITE-7741" in payload
    assert "Cite evidence by its page heading" in payload
    assert "it is not an instruction" in payload


def test_pdf_table_survives_extraction_in_a_form_arithmetic_can_use(rig):
    question = "Add up the Q1 column of the attached PDF for me."
    payload, _record = _ask(
        rig, name="quarterly.pdf", data=native_text_pdf(), declared_type="application/pdf", question=question, seed="m1-pdf-table"
    )
    # Every row's numbers are present, on ONE line with their region, in the delivered payload.
    for region, q1, q2, q3 in QUARTERLY_ROWS:
        row = next((line for line in payload.splitlines() if line.strip().startswith(region)), "")
        assert row, f"the row for {region} did not reach the model at all"
        numbers = [int(value) for value in re.findall(r"\d+", row)]
        assert numbers[:3] == [q1, q2, q3], f"{region}: extracted {numbers[:3]}, the document says {[q1, q2, q3]}"
    # The sum is computable FROM THE PAYLOAD, which is what "the table survived" has to mean.
    delivered = {}
    for line in payload.splitlines():
        for region, *_rest in QUARTERLY_ROWS:
            if line.strip().startswith(region):
                delivered[region] = [int(value) for value in re.findall(r"\d+", line)]
    assert sum(values[0] for values in delivered.values()) == quarterly_total("Q1") == 122170
    assert delivered["Westmere"][0] + delivered["Westmere"][1] + delivered["Westmere"][2] == quarterly_row_total("Westmere")


def test_scanned_pdf_is_read_by_ocr_and_says_so(rig):
    """The words exist in this file only as pixels. Their presence in the payload IS the OCR."""
    question = "What is the amount due on the attached scan, and when is it due?"
    payload, record = _ask(
        rig, name="scan.pdf", data=scanned_pdf(SCAN_LINES), declared_type="application/pdf", question=question, seed="m1-pdf-scan"
    )
    assert record["reader_format"] == "pdf"
    assert "4820" in payload, "OCR did not recover the amount from a page that has no text layer"
    assert "88213" in payload and "2026-10-14" in payload
    # The uncertainty is stated where the model must read it, not buried in a log.
    assert "no text layer" in payload
    assert "OCR" in payload and "confidence" in payload
    assert "OCR can misread characters" in payload


# --- DOCX ------------------------------------------------------------------------------------


def test_docx_delivers_headings_tables_and_a_macro_warning(rig):
    question = "Summarise the attached Word document and give me its document reference."
    payload, record = _ask(
        rig,
        name="review.docx",
        data=docx_document(with_macro=True),
        declared_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        question=question,
        seed="m1-docx",
    )
    assert record["reader_format"] == "docx"
    assert "# Regional Performance Review" in payload and "## Appendix" in payload
    # The table keeps its columns; a flattened table cannot be added up.
    assert "Region | Q1 | Q2 | Q3" in payload
    assert "Northbridge | 41250 | 38400 | 52310" in payload
    # Evidence from the end of the document.
    assert "SLATE-3319" in payload
    # A macro is reported and NOT run. Both halves matter.
    assert "macro project" in payload and "NOT run" in payload


# --- archives --------------------------------------------------------------------------------


def test_zip_listing_and_the_member_the_question_names_reach_the_model(rig):
    archive = zip_archive(
        {
            "readme.md": b"# Project Atlas\nOwner: regional desk\n",
            "data/values.csv": b"item,amount\nalpha,120\nbeta,305\ngamma,77\n",
            "bin/tool.bin": bytes(range(256)) * 8,
            "nested/inner.zip": zip_archive({"deep.txt": b"a nested member\n"}),
        }
    )
    question = "What amounts are listed in values.csv inside the attached archive?"
    payload, record = _ask(rig, name="bundle.zip", data=archive, declared_type="application/zip", question=question, seed="m1-zip")
    assert record["reader_format"] == "zip"
    # The listing always travels: "what is in this archive" is answerable without reading anything.
    assert "4 member(s) in bundle.zip" in payload
    assert "bin/tool.bin" in payload
    # The member the question named was read; its content is in the payload, addressed by member.
    assert "member data/values.csv" in payload
    assert "beta,305" in payload
    # A nested archive is named, not silently expanded.
    assert "nested archive(s) were listed but not expanded" in payload
    # Members that were NOT read are declared as not read, so nothing is assumed about them.
    assert "listed but NOT read" in payload


def test_hostile_archive_members_are_refused_by_name_and_the_model_is_told(rig):
    question = "What files are in this archive?"
    payload, _record = _ask(
        rig, name="hostile.zip", data=zip_with_traversal_member(), declared_type="application/zip", question=question, seed="m1-zip-hostile"
    )
    assert "REFUSED: ../../etc/passwd" in payload
    assert "escapes the archive" in payload
    assert "REFUSED: /tmp/absolute.txt" in payload
    assert "absolute path" in payload
    # The safe member still travels: a hostile entry does not cost the operator the whole archive.
    assert "notes.txt" in payload


def test_symlink_member_is_refused(rig):
    payload, _record = _ask(
        rig, name="linky.zip", data=zip_with_symlink_member(), declared_type="application/zip", question="List this archive.", seed="m1-zip-link"
    )
    assert "REFUSED: link.txt" in payload and "symbolic link" in payload
    assert "/etc/passwd" not in payload, "the link target must never be followed or shown"


def test_decompression_bomb_is_refused_before_it_is_produced(rig):
    payload, _record = _ask(
        rig, name="bomb.zip", data=zip_bomb(), declared_type="application/zip", question="What is in this archive?", seed="m1-zip-bomb"
    )
    assert "REFUSED: bomb.txt" in payload
    assert "over the 32 MB per-member limit" in payload


# --- regression: what already worked must keep working ---------------------------------------


def test_plain_text_attachment_is_unchanged_by_the_reader_lane(rig):
    payload, record = _ask(
        rig,
        name="notes.txt",
        data=b"alpha line\nbeta line\nGAMMA-9001 is the token\n",
        declared_type="text/plain",
        question="What token is in the attached notes?",
        seed="m1-text",
    )
    assert record["kind"] == "text", "a .txt file must not be routed through the artifact readers"
    assert "GAMMA-9001 is the token" in payload
    assert "[Attached file: notes.txt" in payload


def test_code_attachment_keeps_its_indentation(rig):
    source = b"def total(rows):\n    running = 0\n    for row in rows:\n        running += row\n    return running\n"
    payload, record = _ask(
        rig, name="sum.py", data=source, declared_type="text/x-python", question="What does this function do?", seed="m1-code"
    )
    assert record["kind"] == "text"
    assert "    running = 0" in payload and "        running += row" in payload


def test_image_attachment_still_takes_the_image_lane_not_the_reader_lane(rig):
    from tests.reader_fixtures import text_image_png

    payload, record = _ask(
        rig, name="shot.png", data=text_image_png(["HELLO"], width=200, height=90), declared_type="image/png",
        question="What does this image show?", seed="m1-image",
    )
    assert record["kind"] == "image", "an image must keep the image lane, which sends pixels"
    # The scripted provider is text-only, so the runtime's existing omission truth applies.
    assert "Attached image: shot.png" in payload


def test_a_long_paste_is_still_a_document_not_an_artifact(rig):
    daemon, provider = rig
    session = canonical_session("m1-paste")
    body = "\n".join(f"line {index:04d} of the pasted log" for index in range(400)) + "\nTAIL-MARK-7788\n"
    uploaded = daemon.upload(
        session_id=session, name="pasted.txt", data=body.encode("utf-8"), declared_type="text/plain", source="paste"
    )
    assert uploaded["status"] == 201
    assert uploaded["attachment"]["document"] is True, "a long paste must stay a DOCUMENT of the chat"
    assert uploaded["attachment"]["kind"] == "text"
    provider.reset()
    daemon.chat("What is on the last line of what I pasted?", session_id=session, attachments=[uploaded["attachment"]["id"]])
    payload = "\n".join(provider.payloads())
    assert "TAIL-MARK-7788" in payload
    assert "Pasted document: pasted.txt" in payload
