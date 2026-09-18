"""Before the server acknowledges a turn, the card states only what the client knows.

`newRun()` labelled a just-created run "Request accepted" although nothing had been accepted yet;
the server's acknowledgement arrives as the typed `task.started`. The client's own fact is that the
request was sent.
"""
from __future__ import annotations

from tests.test_multichat_navigation_unlock import _drive

A = "sess-label-a"


def test_a_new_run_says_sent_not_accepted() -> None:
    data = _drive(f"""
const run = newRun('{A}');
out({{ action: run.action, stage: run.stage }});
""")
    assert data["action"] != "Request accepted", data
    assert "sent" in data["action"].lower(), data
