"""A file that was listed and then read was read: the later read backs the claim.

From the live agent-team comparison, 2026-10-07. The audit lists the workspace before it reads sources, so the
first record naming `pricing.py` is a listing. `verify_claim` returned at that first match with
"listed only, not read" — before looking at the records after it — so a file the audit did read could still be
reported as never opened ("I did not actually open `pricing.py`, so I can't report on it"). Order of records
must not decide whether a read happened.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core import execution_records

LISTING = SimpleNamespace(result_items_key="entries", asserts_action=False)


@pytest.fixture()
def session():
    sid = "openclaw:" + "c" * 20
    execution_records.clear(sid)
    yield sid
    execution_records.clear(sid)


def test_a_listing_then_a_read_of_the_same_file_is_supported(session):
    execution_records.record(session_id=session, intent="workspace.list_files",
                             details={"entries": ["pricing.py", "stock.py", "orders.py"]}, claim=LISTING)
    execution_records.record(session_id=session, intent="workspace.read_file", arguments={"path": "pricing.py"},
                             details={"resolved_target": "/srv/shop/pricing.py"})
    verdict = execution_records.verify_claim(session, target="pricing.py")
    assert verdict["supported"] is True and verdict["matched_intent"] == "workspace.read_file"


def test_a_file_only_listed_is_still_listed_only(session):
    execution_records.record(session_id=session, intent="workspace.list_files",
                             details={"entries": ["pricing.py", "stock.py"]}, claim=LISTING)
    execution_records.record(session_id=session, intent="workspace.read_file", arguments={"path": "stock.py"},
                             details={"resolved_target": "/srv/shop/stock.py"})
    verdict = execution_records.verify_claim(session, target="pricing.py")
    assert verdict["supported"] is False and verdict.get("listed_only") == "workspace.list_files"
