"""Hostile quoting and whitespace must not stall identity or honesty guards."""
from __future__ import annotations

import subprocess
import sys

import pytest


@pytest.mark.parametrize("statement", [
    "from core.user_identity_authority import classify_identity_question; classify_identity_question('“' * 100000 + ' name')",
    "from core.user_identity_authority import classify_identity_question; classify_identity_question('hello' + ' ' * 100000 + 'there')",
    "from core.agent_runtime.action_honesty_validator import completion_claim_kind; completion_claim_kind('files' + ' ' * 100000 + 'possibly')",
    "from core.agent_runtime.action_honesty_validator import _split_clauses; _split_clauses('hello' + ' ' * 100000 + 'there')",
])
def test_identity_and_honesty_scans_finish(statement):
    subprocess.run([sys.executable, '-c', statement], check=True, timeout=3, capture_output=True)


def test_real_name_questions_and_quoted_examples_keep_their_meaning():
    from core.user_identity_authority import classify_identity_question
    assert classify_identity_question('What is my name?').asks_user_identity
    assert classify_identity_question('"What is my name?"').asks_user_identity
    assert not classify_identity_question('Explain this example: “What is my name?”').asks_user_identity


def test_completed_actions_still_require_evidence():
    from core.agent_runtime.action_honesty_validator import completion_claim_kind
    for text in ('files were deleted', 'files deleted', 'funds have been sent', 'funds sent'):
        assert completion_claim_kind(text) == 'mutation'
    assert completion_claim_kind('Nothing was executed, no files were deleted.') == ''
