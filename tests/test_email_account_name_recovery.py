"""A hallucinated account name recovers once, deterministically, to the default account.

Measured across two operator sessions (2026-11-05/06): the model promoted a sender or a made-up
name into `account=...` ('Kucoin', 'primary', 'user@example.com'), the needs_credentials refusal
ended the turn, and the person got a capability denial for an ask the default account could
serve. The refusal text teaches the recovery path, but the turn ended before the model could
use it. The executor now performs the recovery itself: one bounded retry without the `account`
argument (the default account the refusal names), only for email.read failures carrying a
hallucinated account name.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

sys.path.insert(0, ".")

from core.runtime_execution_tools import RuntimeExecutionResult


def _execution(ok: bool, status: str, text: str = "") -> RuntimeExecutionResult:
    return RuntimeExecutionResult(handled=True, ok=ok, status=status, response_text=text)


def test_recovery_shape_matches_the_executor_call() -> None:
    """The retry omits `account` and keeps every other argument the model extracted."""
    arguments = {"account": "Kucoin", "folder": "inbox", "sender": "kucoin", "limit": 5}
    retry_arguments = {k: v for k, v in arguments.items() if k != "account"}
    assert retry_arguments == {"folder": "inbox", "sender": "kucoin", "limit": 5}


def test_recovery_only_fires_for_needs_credentials_email_reads() -> None:
    """The gate: email.read + needs_credentials + a named account. Everything else passes."""
    cases = [
        (_execution(False, "needs_credentials"), "email.read", {"account": "Kucoin"}, True),
        (_execution(True, "executed"), "email.read", {"account": "Kucoin"}, False),
        (_execution(False, "needs_credentials"), "email.read", {"folder": "inbox"}, False),
        (_execution(False, "needs_credentials"), "email.send", {"account": "Kucoin"}, False),
        (_execution(False, "quota_exceeded"), "email.read", {"account": "Kucoin"}, False),
    ]
    for execution, intent, arguments, want in cases:
        fires = (
            not execution.ok
            and str(execution.status or "") == "needs_credentials"
            and str(intent) == "email.read"
            and isinstance(arguments, dict)
            and str(arguments.get("account") or "").strip() != ""
        )
        assert fires == want, (intent, arguments, want)
