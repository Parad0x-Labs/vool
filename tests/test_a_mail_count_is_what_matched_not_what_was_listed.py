"""A mail read says how many messages matched, not how many it listed.

Measured 2026-11-24 on sls's iMac: asked for a brief of every unread mail, the model called email.read
with limit 8 (copied from its own earlier "8 unread" answer) while 9 were unread. The tool said
"8 message(s) matched", and the reply told him he had 8 unread mails. The tool now counts what
matched and says when some are not listed; the limit's description says to leave it out unless the
person asked for a number.
"""
from __future__ import annotations

from core import email_tools
from core.runtime_tool_contracts import runtime_tool_contracts
from tests.test_email_read_reply import _fake_imap, _isolated, _raw, _store  # _isolated: autouse fixture


def _nine(monkeypatch):
    _store("imap")
    _fake_imap(monkeypatch, {str(i).encode(): _raw(f"s{i}@x.com", f"Subject {i}", "body", f"<m{i}>")
                             for i in range(1, 10)})


def test_a_read_capped_below_what_matched_says_so(monkeypatch):
    _nine(monkeypatch)
    result = email_tools.search_email(unseen_only=True, limit=8)
    assert result.ok and len(result.messages) == 8
    assert result.message.startswith("9 message(s) matched"), result.message
    assert "1 more matched and are not listed" in result.message


def test_a_read_that_lists_everything_adds_nothing(monkeypatch):
    _nine(monkeypatch)
    result = email_tools.search_email(unseen_only=True)
    assert len(result.messages) == 9 and "not listed" not in result.message, result.message


def test_a_filtered_read_stopped_by_its_limit_says_more_may_exist(monkeypatch):
    _nine(monkeypatch)
    result = email_tools.search_email(sender="x.com", limit=3)
    assert len(result.messages) == 3 and "more matches may exist and are not listed" in result.message


def test_the_limit_is_described_as_left_out_unless_asked():
    contract = next(c for c in runtime_tool_contracts() if c.intent == "email.read")
    assert "leave it out unless the person asked for a number" in contract.input_schema["limit"]
