"""A PDF named or described next to the folder it sits in is READ, never answered with a listing.

Gate 68 on a35e0e475 (packaged copy, qwen3:8b):
- "what does the boiler service pdf in my Documents say?" was answered "PDFs under ~/Documents:
  - boiler-service-2026.pdf": a listing of the folder, the PDF never read, and the follow-ups ("when's
  the next one due?", "and how much was it?") had nothing to answer from;
- "open dog-vaccination-card.pdf in Downloads and tell me when Biscuit needs the next booster" was
  answered with a listing of Downloads, because the kind reader took the file's own ".pdf" for "the
  pdfs".

The scripted model states a date only when the prompt carries that PDF's text, so a right answer
proves the file was read. A listing of a kind of file in a folder is still a listing.
"""

from __future__ import annotations

import json
import uuid

import pytest

import tests._reader_served_rig as rig
from tests.test_invoice_folder_follow_ups_served import _pdf
from tests.test_memory_forget_served_p0 import _reply_text

pytestmark = pytest.mark.served

FILES = {
    ("Documents", "boiler-service-2026.pdf"): ["NORTHWIND HEATING LTD", "Engineer: Tomas Brekke",
                                              "Next service due: 14 November 2027", "Amount paid: 96.50 EUR"],
    ("Documents", "lease-agreement-flat-4b.pdf"): ["LEASE AGREEMENT", "Rent: 1,150.00 EUR per month"],
    ("Downloads", "dog-vaccination-card.pdf"): ["PET VACCINATION RECORD", "Name: Biscuit",
                                               "Next booster due: 3 October 2027"],
}


def _scripted_reply(body: dict) -> str | None:
    prompt = json.dumps(body)
    if "Next service due: 14 November 2027" in prompt:
        return "Northwind Heating serviced the boiler; the next service is due on 14 November 2027."
    if "Next booster due: 3 October 2027" in prompt:
        return "Biscuit's next booster is due on 3 October 2027."
    return None


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    home_dir = tmp_path_factory.mktemp("pdf-beside-folder-home")
    for folder in ("Desktop", "Documents", "Downloads"):
        (home_dir / folder).mkdir()
    for (folder, name), lines in FILES.items():
        (home_dir / folder / name).write_bytes(_pdf(lines, 40))
    with rig.CapturingProvider(default="I could not find that.", reply_fn=_scripted_reply) as provider:
        daemon = rig.ServedDaemon(home_dir / "vool", provider=provider, env_extra={"HOME": str(home_dir)})
        daemon.start()
        assert daemon.certify(timeout=180).get("state") == "verified"
        try:
            yield daemon
        finally:
            daemon.stop()


def _chat(daemon, text: str) -> str:
    return _reply_text(daemon.chat(text, session_id=rig.canonical_session("pdf-" + uuid.uuid4().hex[:8])))


@pytest.mark.parametrize("asked", [
    "what does the boiler service pdf in my Documents say?",
    "summarise the service boiler pdf in documents for me",
    "when is the next service due according to the boiler pdf?",
])
def test_a_pdf_described_by_the_words_of_its_name_is_read(served, asked):
    reply = _chat(served, asked)
    assert "14 November 2027" in reply, reply
    assert "PDFs under" not in reply and "lease-agreement" not in reply, reply


@pytest.mark.parametrize("asked", [
    "open dog-vaccination-card.pdf in Downloads and tell me when Biscuit needs the next booster",
    "in my downloads there is dog-vaccination-card.pdf - when is the next booster?",
])
def test_a_pdf_named_beside_its_folder_is_read(served, asked):
    reply = _chat(served, asked)
    assert "3 October 2027" in reply, reply
    assert "PDFs under" not in reply, reply


def test_a_kind_of_file_in_a_folder_is_still_listed(served):
    reply = _chat(served, "what pdfs are in my Documents?")
    assert "boiler-service-2026.pdf" in reply and "lease-agreement-flat-4b.pdf" in reply, reply
