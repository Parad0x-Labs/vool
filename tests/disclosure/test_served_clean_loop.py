"""The clean loop on the owner's cloud model: what it read on the computer is previewed before it leaves.

THE MEASURED DEFECT (cloud disclosure moved onto the v0.8 candidate c9a7f426, 2026-11-12)
-----------------------------------------------------------------------------------------
On v0.8 an ask about the owner's own files ("how much do I owe the plumber?") is answered by the
clean loop, and with VOOL_CLEAN_LOOP_CLOUD on (the default) the owner's own cloud model is its brain.
Each step after a tool hands that model what the tool read on the computer (file names, then the
invoice text) as plain chat messages. The disclosure boundary saw no sources and no attachments in
those requests, so with previews on the invoice went to the cloud model without the preview the
person turned on. A step that carries what the loop read now counts as a request that carries the
owner's files: the chat page shows it first, an API caller is told where the preview waits, and
"Don't send" sends none of it.
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from core.clean_loop.prompt import build_system_prompt
from core.clean_loop.registry import Registry
from core.disclosure.wire import PREVIEW_NEEDED_DETAIL
from tests import _reader_served_rig as rig
from tests.disclosure import test_served_rules
from tests.disclosure.test_served_preview import CloudDaemon, _json

LOOP_PROMPT = build_system_prompt(Registry()).splitlines()[0]
INVOICE = "Plumber invoice\nTotal due: EUR 140.00\nReference PLUMB-5521\n"
ANSWER = "You owe EUR 140.00 to the plumber."
ASK = "How much do I owe the plumber?"


def _owner_files(body: bytes) -> bool:
    return b"PLUMB-5521" in body or b"plumber_invoice" in body


def _brain(body):
    """The owner's cloud model, scripted: find the invoice, read it, answer from it."""
    text = "\n".join(str(m.get("content") or "") for m in body.get("messages") or [] if isinstance(m, dict))
    if LOOP_PROMPT not in text:
        return None
    if "PLUMB-5521" in text:
        return json.dumps({"answer": ANSWER})
    if "plumber_invoice.txt" in text:
        return json.dumps({"tool": "read_file", "path": "Documents/plumber_invoice.txt"})
    return json.dumps({"tool": "find", "query": "plumber invoice"})


@pytest.fixture
def loop_daemon(tmp_path, monkeypatch):
    owner = tmp_path / "owner-home"
    for folder in ("Desktop", "Downloads", "Documents"):
        (owner / folder).mkdir(parents=True)
    (owner / "Documents" / "plumber_invoice.txt").write_text(INVOICE, encoding="utf-8")

    class LoopDaemon(CloudDaemon):
        def env(self):
            env = super().env()
            # The 0.8 defaults, stated: the clean loop on, and the owner's cloud model as its brain.
            env.update(VOOL_CLEAN_LOCAL_LOOP="1", VOOL_CLEAN_LOOP_CLOUD="1", HOME=str(owner))
            return env

    monkeypatch.setattr(test_served_rules, "CloudDaemon", LoopDaemon)
    with test_served_rules.recording_daemon(tmp_path, reply_fn=_brain) as running:
        yield running


def _chat_page(daemon, text, action):
    """The chat page's side of a turn: it shows every waiting preview and answers it."""
    observed = []
    with ThreadPoolExecutor() as executor:
        future = executor.submit(daemon.chat, text, session_id=rig.canonical_session("clean-loop-" + action))
        deadline = time.monotonic() + 60
        while not future.done() and time.monotonic() < deadline:
            for preview in _json(daemon.base_url + "/api/disclosure/pending")["requests"]:
                if preview["state"] == "waiting" and preview["id"] not in {p["id"] for p in observed}:
                    observed.append(preview)
                    _json(daemon.base_url + "/api/disclosure/decision", {
                        "id": preview["id"], "sha256": preview["sha256"], "action": action})
            time.sleep(0.05)
        return future.result(timeout=30), observed


def test_the_invoice_leaves_only_as_the_owner_saw_it(loop_daemon):
    """Fails before the fix: no preview was ever shown, and the invoice text was sent anyway."""
    daemon, provider, raw = loop_daemon
    response, observed = _chat_page(daemon, ASK, "send")
    assert observed, {"response": response, "log": daemon.log_tail()}
    assert any("PLUMB-5521" in preview["body"] for preview in observed), observed
    shown = {preview["body"].encode("utf-8") for preview in observed}
    carrying = [body for body in raw if _owner_files(body)]
    assert carrying, "the scripted model never got the invoice, so nothing here is proven"
    assert all(body in shown for body in carrying), "a request with the owner's file left without its preview"
    assert response["message"]["content"] == ANSWER, response


def test_dont_send_keeps_the_invoice_on_the_computer(loop_daemon):
    """Fails before the fix: the invoice text reached the cloud model with no question asked."""
    daemon, provider, raw = loop_daemon
    response, observed = _chat_page(daemon, ASK, "cancel")
    assert observed, {"response": response, "log": daemon.log_tail()}
    assert not any(_owner_files(body) for body in raw), [body[:300] for body in raw if _owner_files(body)]
    assert ANSWER not in json.dumps(response), response


def test_a_caller_that_cannot_show_previews_is_told_where_it_waits(loop_daemon):
    """An API caller gets the pending preview at once; nothing from the computer is sent."""
    daemon, provider, raw = loop_daemon
    response = daemon.chat(ASK, session_id=rig.canonical_session("clean-loop-api"), disclosure_review="", timeout=90)
    pending = response.get("vool_pending")
    assert pending and pending["type"] == "disclosure_preview" and pending["approve_at"] == "chat_page", response
    assert response["message"]["content"] == PREVIEW_NEEDED_DETAIL, response
    assert not any(_owner_files(body) for body in raw), [body[:300] for body in raw if _owner_files(body)]


# --- the pieces, without a daemon ------------------------------------------------------------


def test_only_steps_that_carry_what_the_loop_read_count_as_files():
    from core.clean_loop.loop import (
        PRIOR_EVIDENCE_HEADING,
        STAGED_EVIDENCE_HEADING,
        TOOL_RESULT_HEADING,
        carries_owner_evidence,
    )

    system = {"role": "system", "content": LOOP_PROMPT}
    ask = {"role": "user", "content": ASK}
    assert not carries_owner_evidence([system, ask])
    # A reply that quotes a heading is the model's words, not something the loop read.
    assert not carries_owner_evidence([system, ask, {"role": "assistant", "content": TOOL_RESULT_HEADING}])
    for shown in (
        {"role": "user", "content": TOOL_RESULT_HEADING + "\nDocuments/plumber_invoice.txt"},
        {"role": "user", "content": ASK + "\n\n" + STAGED_EVIDENCE_HEADING + "\nplumber_invoice.txt"},
        {"role": "user", "content": ASK + "\n\n" + PRIOR_EVIDENCE_HEADING + "\nTotal due: EUR 140.00"},
    ):
        assert carries_owner_evidence([system, ask, shown]), shown


def test_the_boundary_counts_a_marked_request_as_carrying_files():
    from types import SimpleNamespace

    from core.disclosure.wire import CARRIES_FILES_KEY, request_context

    plain = SimpleNamespace(metadata={}, context={}, attachments=None)
    marked = SimpleNamespace(metadata={CARRIES_FILES_KEY: True}, context={}, attachments=None)
    assert request_context(plain, provider="p", cloud=True)["has_files"] is False
    assert request_context(marked, provider="p", cloud=True)["has_files"] is True
