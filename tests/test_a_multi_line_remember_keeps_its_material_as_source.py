"""A remember command whose material sits under it on its own lines ("Remember this template:" then a fenced or
quoted block) is one memory command. The block is remembered as source material in this chat, never as the owner's
own profile ("My name is ..." inside the block is somebody's template, not the owner's name). A message whose later
lines are the owner's own words or a question stays a turn for the model.
"""
from __future__ import annotations

import json

import pytest

from core.context_scope import ContextAccessPolicy
from core.memory.files import memory_entries_path
from core.persistent_memory import maybe_handle_memory_command

LOCAL = {"surface": "cli", "platform": "cli"}


def _rows():
    path = memory_entries_path()
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def _command(text, chat="multi-line-remember"):
    ContextAccessPolicy.for_request(session_id=chat, source_context=LOCAL)
    return maybe_handle_memory_command(text, session_id=chat, source_context=LOCAL)


@pytest.mark.parametrize("text", [
    "Remember this signature block:\n```\nMy name is Odile Brandt. I run the Larkspur bakery.\n```",
    "Please remember this:\n> I am the treasurer of the Fenwick rowing club.\n> I prefer replies in French.",
])
def test_the_block_under_a_remember_command_is_stored_as_chat_source_not_profile(text):
    handled, reply = _command(text)
    assert handled, reply
    rows = _rows()
    assert rows, "the remembered block was discarded"
    assert not [r for r in rows if r.get("scope") == "user_profile"], rows
    body = json.dumps(rows, ensure_ascii=False)
    assert "Larkspur" in body or "Fenwick" in body
    assert all(r["scope"] == "chat" and "\n" not in r["text"] for r in rows), rows


@pytest.mark.parametrize("text", [
    "Remember the boiler code is 4471.\nAlso, what time does the market close today?",
    "Note that the backup failed.\nCan you check the logs?",
])
def test_a_later_line_in_the_owners_own_words_is_not_folded_into_the_command(text):
    handled, _reply = _command(text, chat="multi-line-turn")
    assert not handled
    assert not _rows()


@pytest.mark.parametrize("text,kept", [
    ("This is important, remember this:\n> I said, keep the receipts from the Harrow fair", "keep the receipts from the Harrow fair"),
    ("In this chat, remember this:\n> keep calm, save every Tamsin ledger", "save every Tamsin ledger"),
])
def test_a_lead_in_never_reads_the_command_out_of_the_material(text, kept):
    # The lead-in once ran across the line break to a comma inside the quote, took the quote's own verb ("keep",
    # "save") for the command and stored only what followed it.
    handled, reply = _command(text, chat="lead-in")
    assert handled, reply
    body = " ".join(r["text"] for r in _rows())
    assert kept in body and ("I said" in body or "keep calm" in body), _rows()


@pytest.mark.parametrize("text", [
    "This is it.\nRemember that the Harrow boiler code is 4471",
    "This is important,\nremember the Harrow boiler code is 4471",
    "This is it.\r\nRemember that the Harrow boiler code is 4471",
])
def test_a_lead_in_on_its_own_line_still_starts_a_command(text):
    handled, reply = _command(text, chat="lead-in-line")
    assert handled, reply
    assert any("Harrow boiler code is 4471" in r["text"] for r in _rows()), _rows()
