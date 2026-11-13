"""An answer about what a document on this computer says needs that document read on this turn.

Answer-quality Mac check on the v0.8 head f4f048f7 (real qwen3:8b, loop off, 2026-11-13): "what does
the lease pdf in my downloads say about notice" ran machine.find_file, read nothing (no PDF reader was
offered), and the reply described three lease PDFs with notice periods the files do not state. The
wordings below are new, except the measured one named.
"""
from __future__ import annotations

import pytest

from core.agent_runtime.research_tool_loop_facade import _no_unread_document_contents
from core.agent_runtime.unread_document_guard import asks_what_a_document_says, unread_document_reply
from core.tool_demand_signals import resolve_demand_signals

ASK = "what does the lease pdf in my downloads say about notice"   # measured
INVENTED = ("I found three lease PDF files in your Downloads folder.\n\n1. **Flat_Lease_2026.pdf**: This document "
            "mentions a **30-day notice period** for tenants.")
FOUND = {"tool_name": "machine.find_file", "ok": True, "status": "ok",
         "details": {"matches": ["/Users/someone/Downloads/Flat_Lease_2026.pdf",
                                 "/Users/someone/Desktop/Lease_agreement_2026.pdf"]}}
READ = {"tool_name": "pdf.extract_text", "ok": True, "status": "ok", "details": {"text": "three (3) months"}}


@pytest.mark.parametrize("asked", (
    ASK,
    "summarise the tax pdf on my desktop",
    "read my insurance pdf",
    "what do the pdfs in my documents say about the deposit",
))
def test_a_pdf_content_ask_is_offered_the_pdf_reader(asked):
    assert "pdf.extract_text" in resolve_demand_signals(asked).explicit_intents


@pytest.mark.parametrize("asked", ("make me a pdf of this", "what is a pdf", "convert my notes to the pdf format"))
def test_other_pdf_words_do_not_ask_for_the_reader(asked):
    assert "pdf.extract_text" not in resolve_demand_signals(asked).explicit_intents


def test_contents_no_tool_read_are_not_published():
    reply = _no_unread_document_contents(ASK, [FOUND], INVENTED)
    assert "30-day" not in reply and "notice period" not in reply
    assert "Flat_Lease_2026.pdf" in reply and "Lease_agreement_2026.pdf" in reply
    assert "didn't read" in reply
    assert "/Users/" not in reply


def test_a_refused_read_is_not_a_read():
    refused = {"tool_name": "pdf.extract_text", "ok": False, "status": "refused", "details": {}}
    assert unread_document_reply(ASK, [FOUND, refused], INVENTED) is not None
    correction = {"tool_name": "pdf.extract_text", "correction": True, "ok": False}
    assert unread_document_reply(ASK, [FOUND, correction], INVENTED) is not None


def test_an_answer_from_a_read_document_stands():
    answer = "The lease says three months' notice from you and six from the landlord."
    assert _no_unread_document_contents(ASK, [FOUND, READ], answer) == answer


@pytest.mark.parametrize("asked", (
    "where is my lease pdf",
    "how big is my lease pdf",
    "what does a lease usually say about notice",
    "what does the bible say about forgiveness",
    "summarise my notes from this week",
))
def test_other_asks_are_not_document_reads(asked):
    assert not asks_what_a_document_says(asked)
    assert _no_unread_document_contents(asked, [], "an answer") == "an answer"


def test_a_pasted_document_carries_its_own_words():
    pasted = "what does my contract say about overtime?\n\n" + "Clause 4. Overtime is paid at 1.5x. " * 30
    assert not asks_what_a_document_says(pasted)
