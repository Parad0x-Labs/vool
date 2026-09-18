"""Sabotage proof for the C02 lane's three load-bearing invariants (CLAUDE.md §6b.4).

Each mutation breaks ONE guard in the production source, runs the NAMED owner test in a
subprocess, and demands that test FAIL; the source is then restored byte-exactly (sha256
asserted). If a mutation survives — the owner test still passes under it — the invariant is
unprotected and this pack fails.

Mutations:
  seat_removed        — the registered-effect budget seat no longer reserves (placement gone).
  refusal_swallowed   — the seat reserves but a budget refusal no longer short-circuits:
                        the handler dispatches anyway (the "zero handler calls" law broken).
  session_scope_blind — the gateway's identity resolution drops the session, so session-scoped
                        rules stop binding (identity scope broken at the one authority).
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

_OWNER_PLUGIN_EXHAUSTION = (
    "tests/plugin_mcp_budgets/test_registered_effect_doors.py::"
    "TestPluginBudgetDoor::test_exhausted_session_budget_refuses_plugin_effect_with_zero_handler_calls"
)
_OWNER_PLUGIN_SCOPES = (
    "tests/plugin_mcp_budgets/test_registered_effect_doors.py::"
    "TestPluginBudgetDoor::test_session_scope_isolates_sessions_and_project_scope_binds_the_workspace"
)


@dataclass(frozen=True)
class Mutation:
    name: str
    path: str
    edits: tuple[tuple[str, str], ...]
    owner_test: str
    note: str


MUTATIONS = (
    Mutation(
        name="seat_removed",
        path="core/tool_intent_executor.py",
        edits=(
            (
                """    try:
        budget_effect = _open_registered_budget_effect(intent, source_context=source_context)
    except EffectBudgetRefusedError as budget_refusal:
        return _budget_refused_execution(intent, budget_refusal)
""",
                """    budget_effect = None
""",
            ),
        ),
        owner_test=_OWNER_PLUGIN_EXHAUSTION,
        note="reservation placement removed: the exhausted-budget refusal must disappear",
    ),
    Mutation(
        name="refusal_swallowed",
        path="core/tool_intent_executor.py",
        edits=(
            (
                """    except EffectBudgetRefusedError as budget_refusal:
        return _budget_refused_execution(intent, budget_refusal)""",
                """    except EffectBudgetRefusedError as budget_refusal:
        budget_effect = None""",
            ),
        ),
        owner_test=_OWNER_PLUGIN_EXHAUSTION,
        note="refusal short-circuit dropped: the handler must not dispatch after a refusal",
    ),
    Mutation(
        name="session_scope_blind",
        path="core/effect_gateway.py",
        edits=(
            (
                '''        session_id = session_id or str(context.get("session_id") or "")''',
                '''        session_id = ""''',
            ),
        ),
        owner_test=_OWNER_PLUGIN_SCOPES,
        note="identity scope broken: session-scoped rules must stop binding, refusing must stop",
    ),
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run_owner_tests(tests: tuple[str, ...]) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=str(REPO), PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *tests],
        cwd=str(REPO), env=env, capture_output=True, text=True, timeout=900,
    )


@pytest.mark.parametrize("mutation", MUTATIONS, ids=lambda m: m.name)
def test_every_sabotage_fails_its_named_owner_test_and_restores_byte_exactly(mutation: Mutation) -> None:
    target = REPO / mutation.path
    original_bytes = target.read_bytes()
    original_hash = _sha(target)

    mutated = target.read_text(encoding="utf-8")
    for before, after in mutation.edits:
        assert mutated.count(before) == 1, (
            f"{mutation.name}: the anchor appears {mutated.count(before)} times (want 1)"
        )
        mutated = mutated.replace(before, after)
    try:
        target.write_text(mutated, encoding="utf-8")
        assert _sha(target) != original_hash, f"{mutation.name}: the mutation did not land"

        result = _run_owner_tests((mutation.owner_test,))
        owner_output = (result.stdout or "") + (result.stderr or "")
        assert result.returncode != 0, (
            f"{mutation.name}: the sabotage SURVIVED — {mutation.owner_test} still passes; "
            f"the invariant is unprotected ({mutation.note}). output={owner_output[-800:]}"
        )
    finally:
        target.write_bytes(original_bytes)
        assert _sha(target) == original_hash, (
            f"{mutation.name}: {mutation.path} was NOT restored — the tree is dirty"
        )


def test_the_unmutated_owner_tests_pass_after_all_restorations() -> None:
    """The control: with the source byte-exact, both owner tests pass — the sabotages above
    failed against LIVE guards, not against a broken pack."""
    result = _run_owner_tests((_OWNER_PLUGIN_EXHAUSTION, _OWNER_PLUGIN_SCOPES))
    combined = (result.stdout or "") + (result.stderr or "")
    assert result.returncode == 0, f"owner tests must pass unmutated: {combined[-800:]}"
