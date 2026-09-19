"""CP2 FINAL INTERACTION GATE — the whole chain in ONE process, ONE turn.

selection -> skill injection -> authorization -> budget -> execution ->
Blackbox -> receipt -> answer. Not eight unit proofs: one scripted model turn
through the production tool loop, with the native skill library live, an
active command budget of exactly one unit, a real shell mutation, and every
authority's durable truth asserted from its own store (the budget DB, the
Blackbox journal, the execution-record store) — never from the reply text
alone.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from tests.test_p0_toolchain_served_drive import _call, _drive


@pytest.fixture()
def chain_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from core.blackbox import store as store_module
    from core.blackbox.coverage.cas_keys import CasKeyring
    from core.mode_permission_policy import reset_mode_permission_state
    from tests._toolchain_fixtures import reset_toolchain_state

    home = tmp_path / "home"
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_STATE_DIR", str(home))
    # the REAL native skill library (the repo's own skills/ root) is the point: selection
    # must find the doctrine the turn needs, not a fixture copy
    blackbox_dir = tmp_path / "blackbox"
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(blackbox_dir))
    keys_file = tmp_path / "cas-keys.json"
    keys_file.write_text(CasKeyring.mint().to_json(), encoding="utf-8")
    monkeypatch.setenv("VOOL_BLACKBOX_CAS_KEYS_FILE", str(keys_file))

    from storage.db import configure_default_db_path

    configure_default_db_path(str(tmp_path / "chain.db"))
    from core import effect_budget

    effect_budget.reset_effect_budget_process_state()
    reset_mode_permission_state()
    reset_toolchain_state()
    yield tmp_path
    effect_budget.reset_effect_budget_process_state()
    configure_default_db_path(None)
    reset_mode_permission_state()
    reset_toolchain_state()
    store_module.reset_default_store()


def test_selection_injection_authorization_budget_execution_blackbox_receipt_answer(chain_world, tmp_path):
    from core import effect_budget as eb
    from core import execution_records
    from core.effect_gateway import close_effect_receipt_scope, open_effect_receipt_scope
    from core.mode_permission_policy import set_active_mode

    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "seed.txt").write_bytes(b"chain-proof-bytes")

    # THE BUDGET: one command unit for this session — the shell mutation below must be the
    # only thing that can spend it, and it must actually spend it.
    token = eb.grant_operator_budget_authority("cp2 chain interaction grant")
    eb.apply_operator_adjustment(
        token,
        [eb.BudgetAdjustment(budget_class="command", scope=eb.SCOPE_SESSION, new_limit=1, window_seconds=0.0, note="cp2 chain")],
    )

    session_id = f"openclaw:{uuid.uuid4().hex[:20]}"
    execution_records.clear(session_id)
    set_active_mode(session_id, "auto")

    user_text = (
        "The failing test reproduces. Run the focused shell check cp seed.txt proof.txt in the "
        "workspace to confirm the failure evidence, then answer."
    )
    closing = "The shell check ran and the evidence is copied; the chain closed."
    scripts = [
        [_call("sandbox.run_command", command="cp seed.txt proof.txt", cwd=str(workspace))],
    ]

    from core.runtime_execution_tools import with_mutation_coverage

    open_effect_receipt_scope({"session_id": session_id, "workspace_root": str(workspace)})
    try:
        result, router, events, _context = _drive(
            scripts, user_text=user_text, workspace=workspace, session_id=session_id
        )
    finally:
        close_effect_receipt_scope()

    details = dict(result.get("details") or {})

    # 1. SELECTION — the native library seated a doctrine for a debugging-class turn with
    #    failure demand signals (names recorded at the production assembly seam).
    selected = [name for names in router.skill_names for name in names]
    assert selected, "no native skill was selected for a reproduction-shaped debugging turn"

    # 2. SKILL INJECTION — the selected guidance carries the FULL body, and the seam stamped
    #    its provenance on the round context.
    from core.tool_offer_assembly import assemble_tool_offer

    offer = assemble_tool_offer(
        user_text=user_text,
        task_class="debugging",
        source_context={"workspace_root": str(workspace), "runtime_session_id": session_id},
    )
    skills = {str(s.get("name") or ""): s for s in offer.skill_guidance.skills}
    assert skills, "the offer carries no skill guidance"
    injected = [name for name, s in skills.items() if int(s.get("chars") or 0) > 200]
    assert injected, f"the selected skill's full body did not ride the offer: {list(skills)}"
    # the bodies ride the provider-bound guidance text (the injection lane's byte-exact proof
    # covers the wire; here the full text must be present and non-trivial)
    assert len(offer.skill_guidance.text) >= 200
    assert router.stamps and all(s.get("provenance") == "core.tool_offer_assembly" for s in router.stamps if s)

    # 3. AUTHORIZATION + 5. EXECUTION — auto mode authorized the declared command actions and
    #    the shell really ran: the bytes exist only because the child executed.
    assert (workspace / "proof.txt").read_bytes() == b"chain-proof-bytes"

    # 4. BUDGET — exactly one command unit reserved AND consumed by this turn; no second row.
    command_rows = [r for r in eb.reservation_rows() if r["budget_class"] == "command"]
    assert len(command_rows) == 1, command_rows
    assert command_rows[0]["state"] == eb.RESERVATION_CONSUMED, command_rows

    # 6. BLACKBOX — the coverage pair journaled with the drift naming the mutated path, and
    #    the hash chain verifies.
    from core.blackbox.store import default_store

    entries = default_store().entries()
    kinds = {str(e.get("kind")) for e in entries}
    assert "coverage_scan_intended" in kinds and "coverage_scan_terminal" in kinds, kinds
    terminal = [e for e in entries if e.get("kind") == "coverage_scan_terminal"][-1]
    drift_paths = {str(row.get("path")) for row in terminal.get("drift", [])}
    assert any(p.endswith("proof.txt") for p in drift_paths), sorted(drift_paths)
    assert default_store().verify().ok, default_store().verify().reason

    # 7. RECEIPT — the execution-record store holds the shell's receipt, ok, with the
    #    observation the loop fed back to the model.
    records = {r.intent: r for r in execution_records.records_for(session_id)}
    assert "sandbox.run_command" in records, sorted(records)
    shell = records["sandbox.run_command"]
    assert shell.ok, getattr(shell, "status", "?")

    # 8. ANSWER — the turn published a grounded closure AFTER the work: the step is in the
    #    published tool_steps, the loop completed on the grounded reply, and the response
    #    carries the closure — not a refusal standing in for success.
    steps = [str(s) for s in details.get("tool_steps") or []]
    assert "sandbox.run_command" in steps, steps
    completed = [e for e in events if e.get("event_type") == "tool_loop_completed"]
    assert completed, "the loop never closed on a grounded reply"
    published = str(result.get("response") or "")
    assert closing[:20] in published or "grounded" in str(completed[0].get("message") or ""), (
        f"the final answer did not publish the closure: {published[:200]}"
    )
