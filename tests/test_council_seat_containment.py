"""Council seat containment laws — a seat may never mutate the operator's checkout.

The incident (2026-09-02, recorded as blocker 7 in
`validation-logs/unified-demo-candidate-20260902/REPORT.md`): a council run kept
executing in the background after the browser drive moved on, and its BUILDER seat made
a real, persistent edit to `core/agent_runtime/turn_planner_hook.py` in the production
checkout through the ordinary workspace tools. Nothing malfunctioned. Containment was
prose — the role brief says "You never apply the fix", the seat context says "You have
read-only tools", and the dispatch body carries `"mode": "plan"` as a request FIELD —
and none of those three is a boundary the runtime holds as state.

Every scenario here drives the REAL council state machine with an injected seat turn
that reaches the REAL runtime tool door (`execute_runtime_tool`) using the seat session
id the live dispatch derives. No model, no HTTP, no mocked permission layer: the seat
attempts the effect exactly the way the incident's seat did, and the assertion is on the
BYTES ON DISK plus the recorded outcome, never on prose in a report.
"""

from __future__ import annotations

import os

import pytest

from core.council.dispatch import seat_session_id
from core.council.orchestrator import CouncilOrchestrator, Seat
from core.runtime_execution_tools import execute_runtime_tool

ORIGINAL = "# the production planner hook\nJSON_SCHEMA = 'original'\n"


@pytest.fixture()
def isolated_store(tmp_path, monkeypatch):
    """Council run ledgers land under tmp_path, never the operator's data dir."""

    def _patched(*parts):
        base = tmp_path / "data"
        base.mkdir(parents=True, exist_ok=True)
        out = base
        for part in parts:
            out = out / part
        return out

    monkeypatch.setattr("core.council.run_store.data_path", _patched)
    return tmp_path


@pytest.fixture()
def operator_checkout(tmp_path):
    """A stand-in for the production checkout, holding the exact file the incident edited."""
    root = tmp_path / "operator-checkout"
    (root / "core" / "agent_runtime").mkdir(parents=True)
    victim = root / "core" / "agent_runtime" / "turn_planner_hook.py"
    victim.write_text(ORIGINAL, encoding="utf-8")
    return root


def _bench():
    return [
        Seat("s1", "builder", "model-a", True),
        Seat("s2", "falsifier", "model-b", True),
        Seat("s3", "reviewer", "model-c", True),
    ]


class SeatThatActs:
    """A seat turn double that performs ONE real runtime-tool call, then reports.

    This is the seat's tool loop reduced to its authority-bearing essentials: the same
    session id `core.council.dispatch.live_seat_turn` derives, the same workspace root
    `core.council.api.convene` hands the orchestrator, and the same public dispatch door
    every tool loop reaches. What comes back is kept so a test can assert on the runtime's
    own verdict as well as on the filesystem.
    """

    def __init__(self, intent, arguments, *, workspace_root, seat_id="s1", on_call=None):
        self.intent = intent
        self.arguments = arguments
        self.workspace_root = str(workspace_root)
        self.seat_id = seat_id
        self.on_call = on_call
        self.executions: list[object] = []
        self.contexts: list[dict] = []

    def __call__(self, seat, prompt, round_no, run_id):
        if seat.seat_id == self.seat_id and round_no == 1:
            if self.on_call is not None:
                self.on_call(run_id)
            context = {
                "workspace": self.workspace_root,
                "runtime_session_id": seat_session_id(run_id, seat),
                "council_run_id": run_id,
                "council_seat_id": seat.seat_id,
            }
            self.contexts.append(context)
            self.executions.append(
                execute_runtime_tool(self.intent, dict(self.arguments), source_context=context)
            )
        verdict = "\nVERDICT: AGREE" if round_no >= 2 else ""
        diagnosis = "DIAGNOSIS: the seat had authority nobody granted it.\nFIX: hold it as state."
        return {"text": diagnosis + verdict, "receipt_count": 0, "session_id": None}


def _drive(seats, workspace_root):
    run = CouncilOrchestrator(
        problem="a seat edited a production file nobody approved",
        seats=_bench(),
        seat_turn=seats,
        workspace_root=str(workspace_root),
    )
    run.run()
    return run


CONTAINED = "blocked_by_council_containment"


def _contained(execution) -> bool:
    """Whether the COUNCIL FENCE refused this call — not merely that it did not work.

    Deliberately stricter than "ok is False". Measured at the base commit, five of these
    scenarios already ended `ok=False` for reasons that have nothing to do with council
    authority: `../` and absolute paths die in the workspace path resolver, `machine.*`
    writes die on the machine lane's safe-roots list, and `sandbox.run_command` — which
    ACTUALLY EXECUTED the subprocess, printing `[SANDBOX RUN] Executing:` — merely reported
    a non-zero exit. A test satisfied by any of those would go green without a fence ever
    existing, so the assertion names the fence, and every scenario asserts the physical
    effect (bytes on disk, a file that was never created) alongside it.
    """
    return (
        execution is not None
        and not bool(getattr(execution, "ok", True))
        and str(getattr(execution, "status", "")) == CONTAINED
    )


# ---------------------------------------------------------------- invariant 1 + the incident


def test_seat_cannot_write_a_source_file_in_the_operator_checkout(isolated_store, operator_checkout):
    """THE INCIDENT, byte for byte: the builder seat rewrites the planner hook."""
    victim = operator_checkout / "core" / "agent_runtime" / "turn_planner_hook.py"
    seats = SeatThatActs(
        "workspace.write_file",
        {"path": "core/agent_runtime/turn_planner_hook.py", "content": "JSON_SCHEMA = 'swapped'\n"},
        workspace_root=operator_checkout,
    )
    _drive(seats, operator_checkout)

    assert victim.read_text(encoding="utf-8") == ORIGINAL, (
        "a council seat rewrote a file in the operator's checkout — this is the "
        "2026-09-02 turn_planner_hook.py incident reproduced"
    )
    assert _contained(seats.executions[0])


def test_seat_cannot_edit_in_place_through_replace_in_file(isolated_store, operator_checkout):
    """A targeted in-place swap is the same mutation with a different tool name."""
    victim = operator_checkout / "core" / "agent_runtime" / "turn_planner_hook.py"
    seats = SeatThatActs(
        "workspace.replace_in_file",
        {"path": "core/agent_runtime/turn_planner_hook.py",
         "old_text": "'original'", "new_text": "'swapped'"},
        workspace_root=operator_checkout,
    )
    _drive(seats, operator_checkout)

    assert victim.read_text(encoding="utf-8") == ORIGINAL
    assert _contained(seats.executions[0])


def test_seat_can_still_read_the_workspace(isolated_store, operator_checkout):
    """CONTROL — containment removes mutation, never the investigation itself."""
    seats = SeatThatActs(
        "workspace.read_file",
        {"path": "core/agent_runtime/turn_planner_hook.py"},
        workspace_root=operator_checkout,
    )
    _drive(seats, operator_checkout)

    execution = seats.executions[0]
    assert execution is not None and execution.ok, "a read-only seat must still be able to investigate"


# ---------------------------------------------------------------- invariant 4: the escapes


def test_seat_cannot_escape_through_path_traversal(isolated_store, operator_checkout, tmp_path):
    """`../` out of the workspace is the same write with a longer path."""
    outside = tmp_path / "outside.txt"
    seats = SeatThatActs(
        "workspace.write_file",
        {"path": "../outside.txt", "content": "escaped\n"},
        workspace_root=operator_checkout,
    )
    _drive(seats, operator_checkout)

    assert not outside.exists(), "a seat traversed out of its workspace"
    assert _contained(seats.executions[0])


def test_seat_cannot_escape_through_an_absolute_path(isolated_store, operator_checkout, tmp_path):
    outside = tmp_path / "absolute.txt"
    seats = SeatThatActs(
        "workspace.write_file",
        {"path": str(outside), "content": "escaped\n"},
        workspace_root=operator_checkout,
    )
    _drive(seats, operator_checkout)

    assert not outside.exists()
    assert _contained(seats.executions[0])


def test_seat_cannot_escape_through_a_symlinked_directory(isolated_store, operator_checkout, tmp_path):
    """A symlink inside the workspace pointing out of it is not a new door."""
    target_dir = tmp_path / "elsewhere"
    target_dir.mkdir()
    link = operator_checkout / "link"
    try:
        os.symlink(target_dir, link, target_is_directory=True)
    except (OSError, NotImplementedError):  # pragma: no cover - platform without symlinks
        pytest.skip("symlinks unavailable on this platform")
    seats = SeatThatActs(
        "workspace.write_file",
        {"path": "link/escaped.txt", "content": "escaped\n"},
        workspace_root=operator_checkout,
    )
    _drive(seats, operator_checkout)

    assert not (target_dir / "escaped.txt").exists(), "a seat wrote through a symlink out of its jail"
    assert _contained(seats.executions[0])


def test_seat_cannot_escape_through_an_argument_alias(isolated_store, operator_checkout):
    """`file_path` is `path` wearing a different name; containment must bind aliases first."""
    victim = operator_checkout / "core" / "agent_runtime" / "turn_planner_hook.py"
    seats = SeatThatActs(
        "workspace.write_file",
        {"file_path": "core/agent_runtime/turn_planner_hook.py", "content": "aliased\n"},
        workspace_root=operator_checkout,
    )
    _drive(seats, operator_checkout)

    assert victim.read_text(encoding="utf-8") == ORIGINAL
    assert _contained(seats.executions[0])


def test_seat_cannot_escape_through_an_alternate_tool_name(isolated_store, operator_checkout):
    """`machine.write_file` reaches the same disk by a different intent name."""
    victim = operator_checkout / "core" / "agent_runtime" / "turn_planner_hook.py"
    seats = SeatThatActs(
        "machine.write_file",
        {"path": str(victim), "content": "machine-door\n"},
        workspace_root=operator_checkout,
    )
    _drive(seats, operator_checkout)

    assert victim.read_text(encoding="utf-8") == ORIGINAL
    assert _contained(seats.executions[0])


def test_seat_cannot_run_a_mutating_shell_command(isolated_store, operator_checkout, tmp_path):
    """A subprocess is a write with an interpreter in front of it."""
    marker = tmp_path / "shelled.txt"
    seats = SeatThatActs(
        "sandbox.run_command",
        {"command": f"python3 -c \"open(r'{marker}','w').write('x')\""},
        workspace_root=operator_checkout,
    )
    _drive(seats, operator_checkout)

    assert not marker.exists(), "a seat ran a mutating shell command"
    # At base this call reached the interpreter and only reported a non-zero exit. The
    # fence has to refuse BEFORE dispatch, so the refusal — not the exit code — is the
    # assertion.
    assert _contained(seats.executions[0])


def test_seat_cannot_move_or_delete_through_the_machine_door(isolated_store, operator_checkout, tmp_path):
    victim = operator_checkout / "core" / "agent_runtime" / "turn_planner_hook.py"
    seats = SeatThatActs(
        "machine.move_path",
        {"source": str(victim), "destination": str(tmp_path / "moved.py")},
        workspace_root=operator_checkout,
    )
    _drive(seats, operator_checkout)

    assert victim.exists() and victim.read_text(encoding="utf-8") == ORIGINAL
    assert not (tmp_path / "moved.py").exists()
    assert _contained(seats.executions[0])


# ---------------------------------------------------------------- invariant 5: cancellation


def test_a_cancelled_run_fences_a_seat_still_in_flight(isolated_store, operator_checkout):
    """The incident's other half: the browser moved on, the seat kept going.

    The stop lands WHILE the seat turn is running, which is exactly the window the
    orchestrator's between-attempts flag cannot cover. The fence has to hold inside the
    turn, at the effect door.
    """
    victim = operator_checkout / "core" / "agent_runtime" / "turn_planner_hook.py"
    holder: dict[str, CouncilOrchestrator] = {}

    def _stop_first(_run_id):
        holder["run"].request_stop()

    seats = SeatThatActs(
        "workspace.write_file",
        {"path": "core/agent_runtime/turn_planner_hook.py", "content": "after-stop\n"},
        workspace_root=operator_checkout,
        on_call=_stop_first,
    )
    run = CouncilOrchestrator(
        problem="stop mid-turn",
        seats=_bench(),
        seat_turn=seats,
        workspace_root=str(operator_checkout),
    )
    holder["run"] = run
    run.run()

    assert victim.read_text(encoding="utf-8") == ORIGINAL, (
        "a seat mutated the checkout after the operator stopped the run"
    )
    assert _contained(seats.executions[0])


# ---------------------------------------------------------------- invariant 7: no collateral


def test_an_ordinary_non_council_session_is_untouched(tmp_path):
    """CONTROL — containment is a council fact; ordinary tools keep their behaviour."""
    root = tmp_path / "ordinary"
    root.mkdir()
    execution = execute_runtime_tool(
        "workspace.write_file",
        {"path": "notes.txt", "content": "ordinary\n"},
        source_context={"workspace": str(root), "runtime_session_id": "chat-session-1"},
    )
    assert execution is not None and execution.ok
    assert (root / "notes.txt").read_text(encoding="utf-8") == "ordinary\n"


# ------------------------------------------------- invariants 2, 3, 6, 8: the granted seat


@pytest.fixture()
def clean_containment():
    """Every test owns the fence registry outright — no run survives into the next test."""
    from core.council import containment

    containment.reset_for_tests()
    yield containment
    containment.reset_for_tests()


def _granted_run(operator_checkout, containment, *, classes=("workspace_write",), seats=None):
    """A run whose builder seat carries the operator's explicit grant for this run only."""
    run = CouncilOrchestrator(
        problem="a granted builder seat must still be unable to reach the checkout",
        seats=_bench(),
        seat_turn=seats,
        workspace_root=str(operator_checkout),
        seat_grants={"s1": tuple(classes)},
    )
    assert containment.binding_for_context(
        {"runtime_session_id": containment.seat_session_id_for(run.run_id, "s1")}
    ).granted == frozenset(classes)
    return run


def test_a_granted_builder_seat_writes_only_inside_its_disposable_workspace(
    isolated_store, operator_checkout, clean_containment
):
    """Invariant 3: approval buys a workspace, never the operator's checkout."""
    victim = operator_checkout / "core" / "agent_runtime" / "turn_planner_hook.py"
    seats = SeatThatActs(
        "workspace.write_file",
        {"path": "core/agent_runtime/turn_planner_hook.py", "content": "candidate fix\n"},
        workspace_root=operator_checkout,
    )
    run = _granted_run(operator_checkout, clean_containment, seats=seats)
    run.run()

    execution = seats.executions[0]
    assert execution is not None and execution.ok, "an explicitly granted write must succeed"
    assert victim.read_text(encoding="utf-8") == ORIGINAL, (
        "the granted seat's write landed in the operator's checkout"
    )
    jail = clean_containment.seat_workspace(run.run_id, "s1")
    landed = jail / "core" / "agent_runtime" / "turn_planner_hook.py"
    assert landed.read_text(encoding="utf-8") == "candidate fix\n"
    assert not str(jail).startswith(str(operator_checkout))


def test_a_granted_seat_cannot_traverse_out_of_its_workspace(
    isolated_store, operator_checkout, clean_containment
):
    victim = operator_checkout / "core" / "agent_runtime" / "turn_planner_hook.py"
    seats = SeatThatActs(
        "workspace.write_file",
        {"path": "../../../../../../../../.." + str(victim), "content": "traversed\n"},
        workspace_root=operator_checkout,
    )
    run = _granted_run(operator_checkout, clean_containment, seats=seats)
    run.run()

    assert victim.read_text(encoding="utf-8") == ORIGINAL
    assert _contained(seats.executions[0])


def test_a_granted_seat_cannot_write_through_a_symlink_out_of_its_workspace(
    isolated_store, operator_checkout, clean_containment
):
    """A symlink planted INSIDE the jail is the jail's own escape hatch if paths are not
    resolved. `Path.resolve()` follows it, so the write is judged where it would land."""
    seats = SeatThatActs(
        "workspace.write_file",
        {"path": "bridge/escaped.py", "content": "escaped\n"},
        workspace_root=operator_checkout,
    )
    run = _granted_run(operator_checkout, clean_containment, seats=seats)
    jail = clean_containment.seat_workspace(run.run_id, "s1")
    try:
        os.symlink(operator_checkout, jail / "bridge", target_is_directory=True)
    except (OSError, NotImplementedError):  # pragma: no cover - platform without symlinks
        pytest.skip("symlinks unavailable on this platform")
    run.run()

    assert not (operator_checkout / "escaped.py").exists(), "a seat wrote through a symlinked jail"
    assert _contained(seats.executions[0])


def test_a_shell_class_cannot_be_granted_at_all(
    isolated_store, operator_checkout, clean_containment, tmp_path
):
    """Replaces the two tests that used to live here.

    They granted a seat `sandbox_command` and then asserted that the fence refused a command
    whose TEXT named an absolute path outside the jail — including through the `cmd` alias.
    Both passed. Both were testing a boundary that cannot hold: reading a command line tells
    you nothing about where a shell will actually write, and every serious escape
    (`$HOME/x`, `$(pwd)/..`, an interpreter composing the path at runtime) walks past it
    unread. The check is deleted rather than kept, so the tests that pinned it are replaced
    by the rule that took its place: a seat is never given a shell.

    The full attack surface is driven in `tests/test_council_seat_no_shell_and_cancellation.py`.
    """
    marker = tmp_path / "shelled.txt"
    seats = SeatThatActs(
        "sandbox.run_command",
        {"command": f"python3 -c \"open(r'{marker}','w').write('x')\""},
        workspace_root=operator_checkout,
    )
    run = CouncilOrchestrator(
        problem="an operator asking for a shell seat",
        seats=_bench(),
        seat_turn=seats,
        workspace_root=str(operator_checkout),
        seat_grants={"s1": ("sandbox_command", "validation_command")},
    )
    binding = clean_containment.binding_for_context(
        {"runtime_session_id": clean_containment.seat_session_id_for(run.run_id, "s1")}
    )
    assert binding is not None and binding.granted == frozenset(), (
        "a declared shell grant produced authority"
    )
    run.run()
    assert not marker.exists()
    assert _contained(seats.executions[0])


def test_a_grant_of_one_class_does_not_buy_another(
    isolated_store, operator_checkout, clean_containment, tmp_path
):
    """Grants are per class. A write grant is not a shell grant."""
    marker = tmp_path / "not-run.txt"
    seats = SeatThatActs(
        "sandbox.run_command",
        {"command": f"python3 -c \"open(r'{marker}','w').write('x')\""},
        workspace_root=operator_checkout,
    )
    run = _granted_run(operator_checkout, clean_containment, classes=("workspace_write",), seats=seats)
    run.run()

    assert not marker.exists()
    assert _contained(seats.executions[0])


def test_spending_and_sending_classes_are_never_grantable(clean_containment, operator_checkout):
    """Invariant 2's ceiling: some classes are not the operator's to delegate to a seat."""
    run = CouncilOrchestrator(
        problem="ceiling",
        seats=_bench(),
        seat_turn=lambda *a, **k: {"text": "x", "receipt_count": 0, "session_id": None},
        workspace_root=str(operator_checkout),
    )
    for forbidden in ("wallet_spend", "credit_spend", "network_send", "network_publish",
                      "runtime_capability_change", "task_orchestration", ""):
        with pytest.raises(clean_containment.CouncilContainmentError):
            clean_containment.grant(run.run_id, "s1", forbidden)


def test_a_run_declaring_an_ungrantable_class_gets_no_authority_from_it(
    isolated_store, operator_checkout, clean_containment
):
    """A run cannot widen its own ceiling by declaring a class in `seat_grants`."""
    run = CouncilOrchestrator(
        problem="self-declared authority",
        seats=_bench(),
        seat_turn=lambda *a, **k: {"text": "x", "receipt_count": 0, "session_id": None},
        workspace_root=str(operator_checkout),
        seat_grants={"s1": ("wallet_spend", "network_send", "task_orchestration")},
    )
    binding = clean_containment.binding_for_context(
        {"runtime_session_id": clean_containment.seat_session_id_for(run.run_id, "s1")}
    )
    assert binding is not None and binding.granted == frozenset()


def test_every_attempted_effect_leaves_a_terminal_row(
    isolated_store, operator_checkout, clean_containment
):
    """Invariant 6: refused, executed and cancelled are all answerable from the ledger."""
    refused = SeatThatActs(
        "workspace.write_file",
        {"path": "core/agent_runtime/turn_planner_hook.py", "content": "no\n"},
        workspace_root=operator_checkout,
    )
    ungranted = CouncilOrchestrator(
        problem="ledger — refused",
        seats=_bench(),
        seat_turn=refused,
        workspace_root=str(operator_checkout),
    )
    ungranted.run()
    rows = clean_containment.effect_ledger(ungranted.run_id)
    assert [row["outcome"] for row in rows] == [clean_containment.OUTCOME_REFUSED]
    assert rows[0]["seat_id"] == "s1"
    assert rows[0]["intent"] == "workspace.write_file"
    assert rows[0]["side_effect_class"] == "workspace_write"

    executed = SeatThatActs(
        "workspace.write_file",
        {"path": "candidate.py", "content": "yes\n"},
        workspace_root=operator_checkout,
    )
    granted = _granted_run(operator_checkout, clean_containment, seats=executed)
    granted.run()
    assert [row["outcome"] for row in clean_containment.effect_ledger(granted.run_id)] == [
        clean_containment.OUTCOME_EXECUTED
    ]

    cancelled_seat = SeatThatActs(
        "workspace.write_file",
        {"path": "candidate.py", "content": "too late\n"},
        workspace_root=operator_checkout,
    )
    holder: dict[str, CouncilOrchestrator] = {}
    cancelled_seat.on_call = lambda _run_id: holder["run"].request_stop()
    stopped = CouncilOrchestrator(
        problem="ledger — cancelled",
        seats=_bench(),
        seat_turn=cancelled_seat,
        workspace_root=str(operator_checkout),
        seat_grants={"s1": ("workspace_write",)},
    )
    holder["run"] = stopped
    stopped.run()
    assert [row["outcome"] for row in clean_containment.effect_ledger(stopped.run_id)] == [
        clean_containment.OUTCOME_CANCELLED
    ]


def test_reads_do_not_flood_the_effect_ledger(isolated_store, operator_checkout, clean_containment):
    """CONTROL — the ledger answers "what did a seat try to CHANGE", not "what did it look at"."""
    seats = SeatThatActs(
        "workspace.read_file",
        {"path": "core/agent_runtime/turn_planner_hook.py"},
        workspace_root=operator_checkout,
    )
    run = CouncilOrchestrator(
        problem="reads", seats=_bench(), seat_turn=seats, workspace_root=str(operator_checkout)
    )
    run.run()
    assert clean_containment.effect_ledger(run.run_id) == ()


def test_two_concurrent_runs_do_not_share_authority_or_workspace(
    isolated_store, operator_checkout, clean_containment
):
    """Invariant 8: a grant is a property of ONE run, and nothing is global."""
    victim = operator_checkout / "core" / "agent_runtime" / "turn_planner_hook.py"
    granted_seat = SeatThatActs(
        "workspace.write_file", {"path": "a.py", "content": "A\n"}, workspace_root=operator_checkout
    )
    plain_seat = SeatThatActs(
        "workspace.write_file",
        {"path": "core/agent_runtime/turn_planner_hook.py", "content": "B\n"},
        workspace_root=operator_checkout,
    )
    granted = _granted_run(operator_checkout, clean_containment, seats=granted_seat)
    plain = CouncilOrchestrator(
        problem="the second, ungranted run",
        seats=_bench(),
        seat_turn=plain_seat,
        workspace_root=str(operator_checkout),
    )

    # Interleaved: the second run is registered and running while the first still holds a
    # grant. Neither may see the other's authority.
    granted.run()
    plain.run()

    assert granted_seat.executions[0].ok
    assert _contained(plain_seat.executions[0]), "the ungranted run inherited a grant"
    assert victim.read_text(encoding="utf-8") == ORIGINAL
    jail_a = clean_containment.seat_workspace(granted.run_id, "s1")
    jail_b = clean_containment.seat_workspace(plain.run_id, "s1")
    assert jail_a != jail_b
    assert (jail_a / "a.py").exists() and not (jail_b / "a.py").exists()


def test_the_fence_survives_a_context_that_lost_the_session_id(
    isolated_store, operator_checkout, clean_containment
):
    """A sub-task envelope that rebuilds its own context must not launder a seat's identity.

    `core/orchestration/executor.py` calls the runtime tool door directly with whatever
    context the envelope carries. The turn's effect ledger froze the originating context, so
    the fence finds the seat even when this call's own context no longer names it.
    """
    from core.effect_gateway import close_effect_receipt_scope, open_effect_receipt_scope

    victim = operator_checkout / "core" / "agent_runtime" / "turn_planner_hook.py"
    run = CouncilOrchestrator(
        problem="a laundered sub-context",
        seats=_bench(),
        seat_turn=lambda *a, **k: {"text": "x", "receipt_count": 0, "session_id": None},
        workspace_root=str(operator_checkout),
    )
    seat_context = {
        "workspace": str(operator_checkout),
        "runtime_session_id": clean_containment.seat_session_id_for(run.run_id, "s1"),
    }
    open_effect_receipt_scope(seat_context)
    try:
        laundered = {"workspace": str(operator_checkout)}  # no session id at all
        execution = execute_runtime_tool(
            "workspace.write_file",
            {"path": "core/agent_runtime/turn_planner_hook.py", "content": "laundered\n"},
            source_context=laundered,
        )
    finally:
        close_effect_receipt_scope()

    assert victim.read_text(encoding="utf-8") == ORIGINAL
    assert _contained(execution)


def test_convene_registers_containment_for_the_seats_it_creates(
    isolated_store, operator_checkout, clean_containment, monkeypatch, request
):
    """The wiring: a run convened through the real API door is fenced, and stop fences it."""
    from core.council import api as council_api

    # This test registers a LIVE run in the module-level registry. Leaving it there makes the
    # next convene anywhere in the session a 409 `council_already_live` — measured, five
    # unrelated council API tests went red. The registry is restored on the way out.
    council_api.forget_all_runs_for_tests()
    request.addfinalizer(council_api.forget_all_runs_for_tests)
    monkeypatch.setattr(council_api.pin_lock, "acquire", lambda run_id: "cap")
    monkeypatch.setattr(council_api.pin_lock, "release", lambda run_id: None)
    monkeypatch.setattr(council_api.pin_lock, "note_state", lambda run_id, state: None)
    def _register_without_a_thread(orchestrator, base_url, capability):
        # Exactly `_start_run_thread`'s registry half. The thread half would drive real seat
        # turns over HTTP, which is not what this test is about; the live registry is, because
        # `stop()` reads it.
        council_api._RUNS[orchestrator.run_id] = orchestrator

    monkeypatch.setattr(council_api, "_start_run_thread", _register_without_a_thread)

    status, payload = council_api.convene(
        {"problem": "who edited the planner hook", "seats": [
            {"role_id": "builder", "model": "openrouter/x"},
            {"role_id": "falsifier", "model": "openrouter/y"},
            {"role_id": "reviewer", "model": "openrouter/z"},
        ]},
        base_url="http://127.0.0.1:9",
        workspace_root=str(operator_checkout),
    )
    assert status == 200, payload
    run_id = payload["run_id"]
    session = clean_containment.seat_session_id_for(run_id, "s1")
    binding = clean_containment.binding_for_context({"runtime_session_id": session})
    assert binding is not None and binding.live and binding.granted == frozenset()

    assert council_api.stop(run_id)[0] == 200
    fenced = clean_containment.binding_for_context({"runtime_session_id": session})
    assert fenced is not None and not fenced.live


def test_a_refused_effect_reaches_the_runs_own_durable_ledger(
    isolated_store, operator_checkout, clean_containment
):
    """Invariant 6, the half that outlives the process.

    The incident's write was found by a clean-tree gate hours after the run. A refusal that
    exists only in the refusing process is not a record — this one is readable from
    `/api/council/events` after the daemon that refused it is gone.
    """
    from core.council.run_store import CouncilRunStore

    seats = SeatThatActs(
        "workspace.write_file",
        {"path": "core/agent_runtime/turn_planner_hook.py", "content": "no\n"},
        workspace_root=operator_checkout,
    )
    run = CouncilOrchestrator(
        problem="durable refusal",
        seats=_bench(),
        seat_turn=seats,
        workspace_root=str(operator_checkout),
    )
    run.run()

    rows = [
        row for row in CouncilRunStore(run.run_id).read_events()
        if row.get("type") == "seat_effect"
    ]
    assert len(rows) == 1, rows
    assert rows[0]["seat_id"] == "s1"
    assert rows[0]["intent"] == "workspace.write_file"
    assert rows[0]["outcome"] == clean_containment.OUTCOME_REFUSED


# ------------------------------------------------- gaps the sabotage sweep found first
#
# Three fence branches were, as first written, indistinguishable from their neighbours:
# disabling the ceiling check, the liveness check or the alias binding turned NO test red,
# because in every scenario the suite covered, the grant check refused the same call one
# line later. A branch nothing can tell apart from its backstop is a branch nobody is
# testing. Each of the three now has a scenario that reaches it with the backstop satisfied.


def test_the_ceiling_holds_even_when_a_grant_names_an_ungrantable_class(
    isolated_store, operator_checkout, clean_containment
):
    """`_normalize_grants` drops an ungrantable class, so no ORDINARY path puts one in a
    seat's grant — which is exactly why the fence's own ceiling check was untestable and
    therefore unproven. This writes one straight into the registry, the way a future
    registration path that forgets to normalize would, and requires the fence to refuse it
    anyway. Money and sends are not the operator's to delegate at any breadth, and that has
    to be true of the door, not only of the doorman."""
    def _inject(run_id):
        # AFTER `run()` has re-armed. Injecting before construction is pointless: `run()`
        # re-registers from `seat_grants` on entry and would wipe it — which is precisely
        # what made the first version of this test pass under sabotage.
        clean_containment._RUNS[run_id].grants["s1"] = frozenset({"task_orchestration"})

    seats = SeatThatActs(
        "orchestration.execute_envelope",
        {"task_envelope": {"task_id": "t1", "role": "builder"}},
        workspace_root=operator_checkout,
        on_call=_inject,
    )
    run = CouncilOrchestrator(
        problem="a grant that should not exist",
        seats=_bench(),
        seat_turn=seats,
        workspace_root=str(operator_checkout),
    )
    run.run()

    assert _contained(seats.executions[0]), (
        "the fence honoured a grant for a class that is never grantable"
    )


def test_a_granted_seat_cannot_escape_through_an_aliased_path_argument(
    isolated_store, operator_checkout, clean_containment
):
    """The alias binding only matters once a seat HAS a grant.

    For an ungranted seat every write is refused before any path is read, so `file_path`
    versus `path` changes nothing — which is why removing the binding turned no test red.
    With a grant in hand the jail check is the only thing left, and it reads canonical
    argument names: an unbound alias would hand it an empty path list and wave the write
    through to the operator's checkout."""
    victim = operator_checkout / "core" / "agent_runtime" / "turn_planner_hook.py"
    # `filepath`, not `file_path`: the fence's own path-key list already carries `file_path`,
    # `target_path`, `source_path` and `destination_path`, so those four are caught whether
    # aliases are bound or not. `filepath` — like `filename`, `folder`, `dir`, `cmd` and
    # `shell_command` — reaches the canonical field ONLY through the alias table, and is
    # therefore what actually tests the binding.
    seats = SeatThatActs(
        "workspace.write_file",
        {"filepath": str(victim), "content": "aliased past the jail\n"},
        workspace_root=operator_checkout,
    )
    run = _granted_run(operator_checkout, clean_containment, seats=seats)
    run.run()

    assert victim.read_text(encoding="utf-8") == ORIGINAL
    assert _contained(seats.executions[0])


def test_a_cancelled_run_fences_a_granted_seat_still_in_flight(
    isolated_store, operator_checkout, clean_containment
):
    """The liveness fence, with the grant check satisfied so it cannot do the work.

    This is the incident's own shape at full strength: a seat the operator DID authorize,
    already inside its turn, when the run is stopped. `_dispatch_seat` reads the stop flag
    between attempts and this seat is past that point, so the only thing that can refuse the
    write is the fence at the effect door."""
    holder: dict[str, CouncilOrchestrator] = {}
    seats = SeatThatActs(
        "workspace.write_file",
        {"path": "candidate.py", "content": "too late\n"},
        workspace_root=operator_checkout,
        on_call=lambda _run_id: holder["run"].request_stop(),
    )
    run = _granted_run(operator_checkout, clean_containment, seats=seats)
    holder["run"] = run
    run.run()

    execution = seats.executions[0]
    assert _contained(execution), "a granted seat kept writing after the run was stopped"
    assert "stopped this council run" in str(execution.response_text)
    jail = clean_containment.seat_workspace(run.run_id, "s1")
    assert not (jail / "candidate.py").exists()
    assert [row["outcome"] for row in clean_containment.effect_ledger(run.run_id)] == [
        clean_containment.OUTCOME_CANCELLED
    ]


# ------------------------------------------------- the invariant, over the whole tool surface


def test_every_contracted_mutating_tool_is_refused_for_a_default_seat(
    isolated_store, operator_checkout, clean_containment
):
    """The closed-set form of invariant 1, asserted over the REAL contract registry.

    Not a list of tool names to keep in sync — an enumeration of every intent this build
    declares, checked against the fence's verdict. A write tool added tomorrow joins
    `workspace_write` and appears here on its first run; a tool with a class this build has
    never heard of fails closed. Nothing has to be added to this test for either to hold,
    which is the whole point of classifying by declared side effect instead of by name.
    """
    from core.runtime_tool_contracts import runtime_tool_contract_map

    run = CouncilOrchestrator(
        problem="the whole tool surface",
        seats=_bench(),
        seat_turn=lambda *a, **k: {"text": "x", "receipt_count": 0, "session_id": None},
        workspace_root=str(operator_checkout),
    )
    context = {
        "workspace": str(operator_checkout),
        "runtime_session_id": clean_containment.seat_session_id_for(run.run_id, "s1"),
    }

    permitted, refused = [], []
    for intent, contract in sorted(runtime_tool_contract_map().items()):
        verdict = clean_containment.evaluate(intent, {}, context)
        assert verdict.contained, intent
        (refused if verdict.refusal else permitted).append(
            (intent, str(getattr(contract, "side_effect_class", "") or ""))
        )

    assert permitted, "a seat that can investigate nothing is not a seat"
    assert {effect for _, effect in permitted} == {clean_containment.READ_ONLY_CLASS}, (
        f"a default seat was permitted a non-read-only tool: {permitted}"
    )
    assert refused, "the registry declares no mutating tools at all — the fixture is wrong"
    assert clean_containment.READ_ONLY_CLASS not in {effect for _, effect in refused}


def test_an_unknown_side_effect_class_fails_closed(
    isolated_store, operator_checkout, clean_containment
):
    """An intent no registry knows declares `""`, and `""` is not grantable."""
    run = CouncilOrchestrator(
        problem="an intent from the future",
        seats=_bench(),
        seat_turn=lambda *a, **k: {"text": "x", "receipt_count": 0, "session_id": None},
        workspace_root=str(operator_checkout),
        seat_grants={"s1": ("workspace_write", "sandbox_command")},
    )
    verdict = clean_containment.evaluate(
        "future.tool_nobody_declared",
        {"path": "anything"},
        {"runtime_session_id": clean_containment.seat_session_id_for(run.run_id, "s1")},
    )
    assert verdict.contained and verdict.refusal
    assert "undeclared" in verdict.refusal


def test_no_real_tool_falls_into_the_fail_closed_branch(
    isolated_store, operator_checkout, clean_containment
):
    """Fail-closed must only ever catch tools that do not exist.

    `evaluate` refuses an undeclared side-effect class outright, which is the right default
    for something arriving from the future — and exactly the wrong outcome for a real tool
    whose contract forgot to declare one. Every intent the registry knows is checked here, so
    a new tool landing without a declared class is caught by this test rather than by a
    council seat mysteriously unable to use it.
    """
    from core import tool_registry
    from core.tool_argument_aliases import side_effect_class_for_intent

    undeclared = sorted(
        intent for intent in tool_registry._all_intents_locked()
        if not str(side_effect_class_for_intent(intent) or "").strip()
    )
    assert undeclared == [], (
        f"these registered tools declare no side-effect class, so a council seat is refused "
        f"them regardless of any grant: {undeclared}"
    )
