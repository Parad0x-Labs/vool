"""Council seat amendment: no shell, no text filtering, and a cancellation that lands.

The first containment pass left one boundary that was not a boundary: a granted seat could
be given `sandbox_command`/`validation_command`, and what kept it inside its workspace was
the working directory plus a scan of the command TEXT for absolute paths. That is not
containment. `$HOME/x`, `${VAR}`, `$(…)`, backticks, an interpreter building the path at
runtime, a symlink swapped after the check, a hard link — every one of them defeats reading
the command line, and none of them is exotic.

So raw shell is not grantable at all. An approved builder seat gets exactly four typed
mutation tools, each of which resolves its target against the workspace root the fence
redirects, and nothing else. The command-text scan is deleted rather than kept as a
second-best: a check that cannot hold is worse than no check, because it reads like one.

Every test here drives the REAL orchestrator with a seat turn that reaches the REAL runtime
tool door, and asserts on bytes on disk plus the fence's own verdict.
"""

from __future__ import annotations

import os

import pytest

from core.council.orchestrator import CouncilOrchestrator, Seat
from core.runtime_execution_tools import execute_runtime_tool

ORIGINAL = "# the production planner hook\nJSON_SCHEMA = 'original'\n"
CONTAINED = "blocked_by_council_containment"


@pytest.fixture()
def isolated_store(tmp_path, monkeypatch):
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
def clean_containment():
    from core.council import containment

    containment.reset_for_tests()
    yield containment
    containment.reset_for_tests()


@pytest.fixture()
def operator_checkout(tmp_path):
    root = tmp_path / "operator-checkout"
    (root / "core" / "agent_runtime").mkdir(parents=True)
    (root / "core" / "agent_runtime" / "turn_planner_hook.py").write_text(ORIGINAL, encoding="utf-8")
    return root


def _bench():
    return [
        Seat("s1", "builder", "model-a", True),
        Seat("s2", "falsifier", "model-b", True),
        Seat("s3", "reviewer", "model-c", True),
    ]


def _contained(execution) -> bool:
    return (
        execution is not None
        and not bool(getattr(execution, "ok", True))
        and str(getattr(execution, "status", "")) == CONTAINED
    )


class SeatThatActs:
    """One real runtime-tool call from inside a real seat turn."""

    def __init__(self, intent, arguments, *, workspace_root, seat_id="s1", on_call=None):
        self.intent = intent
        self.arguments = arguments
        self.workspace_root = str(workspace_root)
        self.seat_id = seat_id
        self.on_call = on_call
        self.executions: list[object] = []

    def __call__(self, seat, prompt, round_no, run_id):
        from core.council.containment import seat_session_id_for

        if seat.seat_id == self.seat_id and round_no == 1:
            if self.on_call is not None:
                self.on_call(run_id)
            self.executions.append(
                execute_runtime_tool(
                    self.intent,
                    dict(self.arguments),
                    source_context={
                        "workspace": self.workspace_root,
                        "runtime_session_id": seat_session_id_for(run_id, seat.seat_id),
                    },
                )
            )
        tail = "\nVERDICT: AGREE" if round_no >= 2 else ""
        return {"text": "DIAGNOSIS: x\nFIX: y" + tail, "receipt_count": 0, "session_id": None}


def _run(operator_checkout, seats, *, grants=None, problem="amendment"):
    return CouncilOrchestrator(
        problem=problem,
        seats=_bench(),
        seat_turn=seats,
        workspace_root=str(operator_checkout),
        seat_grants=dict(grants or {}),
    )


# ============================================================ 1. shell is never grantable


SHELL_CLASSES = ("sandbox_command", "validation_command")


@pytest.mark.parametrize("shell_class", SHELL_CLASSES)
def test_a_shell_class_cannot_be_granted_through_the_api(clean_containment, operator_checkout, shell_class):
    """`containment.grant()` refuses rather than accepting-then-silently-ignoring."""
    run = _run(operator_checkout, lambda *a, **k: {"text": "x", "receipt_count": 0, "session_id": None})
    with pytest.raises(clean_containment.CouncilContainmentError):
        clean_containment.grant(run.run_id, "s1", shell_class)


@pytest.mark.parametrize("shell_class", SHELL_CLASSES)
def test_a_shell_class_cannot_be_granted_through_convene(clean_containment, operator_checkout, shell_class):
    from core.council import api as council_api

    status, payload = council_api.convene(
        {
            "problem": "let the builder run tests",
            "seats": [
                {"role_id": "builder", "model": "openrouter/x"},
                {"role_id": "falsifier", "model": "openrouter/y"},
                {"role_id": "reviewer", "model": "openrouter/z"},
            ],
            "seat_capabilities": {"s1": [shell_class]},
        },
        base_url="http://127.0.0.1:9",
        workspace_root=str(operator_checkout),
    )
    assert status == 400, payload
    assert shell_class in str(payload.get("error"))


@pytest.mark.parametrize("shell_class", SHELL_CLASSES)
def test_shell_is_absent_from_the_grantable_set(clean_containment, shell_class):
    assert shell_class not in clean_containment.GRANTABLE_SIDE_EFFECT_CLASSES
    assert shell_class in clean_containment.NEVER_GRANTABLE_SIDE_EFFECT_CLASSES


def test_a_shell_grant_forced_into_the_registry_is_still_refused(
    isolated_store, operator_checkout, clean_containment, tmp_path
):
    """The ceiling is enforced at the DOOR, not only by the granting API.

    A future registration path that forgets to narrow its input must not be able to hand a
    seat a shell. The grant is written straight into the registry after `run()` has re-armed,
    and the fence has to refuse it anyway.
    """
    marker = tmp_path / "shelled.txt"
    seats = SeatThatActs(
        "sandbox.run_command",
        {"command": f"python3 -c \"open(r'{marker}','w').write('x')\""},
        workspace_root=operator_checkout,
        on_call=lambda run_id: clean_containment._RUNS[run_id].grants.__setitem__(
            "s1", frozenset({"sandbox_command"})
        ),
    )
    run = _run(operator_checkout, seats)
    run.run()

    assert not marker.exists(), "a seat ran a subprocess"
    assert _contained(seats.executions[0])


@pytest.mark.parametrize(
    "intent,arguments",
    [
        ("sandbox.run_command", {"command": "sh -c 'echo x > /tmp/vool-seat-escape'"}),
        ("sandbox.run_command", {"command": "python3 -c \"import os; open(os.environ['HOME']+'/pwned','w')\""}),
        ("workspace.run_tests", {"command": "pytest"}),
        ("workspace.run_lint", {"command": "ruff check"}),
        ("workspace.run_formatter", {"command": "ruff format"}),
    ],
)
def test_no_interpreter_or_command_wrapper_is_reachable_by_a_granted_seat(
    isolated_store, operator_checkout, clean_containment, intent, arguments
):
    """Every command-shaped tool, with the widest grant the API will issue."""
    seats = SeatThatActs(intent, arguments, workspace_root=operator_checkout)
    run = _run(operator_checkout, seats, grants={"s1": tuple(clean_containment.GRANTABLE_SIDE_EFFECT_CLASSES)})
    run.run()

    assert _contained(seats.executions[0]), f"{intent} reached a subprocess"
    assert not os.path.exists("/tmp/vool-seat-escape")


def test_the_fence_carries_no_command_text_inspection(clean_containment):
    """The deleted check must stay deleted.

    Reading a command line for absolute paths was the residual risk this amendment exists to
    remove. Keeping it "as defence in depth" is how it comes back as a claimed boundary.
    """
    import inspect

    source = inspect.getsource(clean_containment)
    assert "_escaping_command" not in source
    assert "_COMMAND_ARG_KEYS" not in source


# ================================================= 2. only workspace-rooted typed tools


def test_the_grantable_tool_set_is_exactly_the_workspace_rooted_mutators(clean_containment):
    assert frozenset(
        {
            "workspace.write_file",
            "workspace.replace_in_file",
            "workspace.apply_unified_diff",
            "workspace.ensure_directory",
        }
    ) == clean_containment.SEAT_WORKSPACE_MUTATION_TOOLS


@pytest.mark.parametrize(
    "intent,arguments",
    [
        # `workspace_write` by declaration, but NOT resolved against source_context's
        # workspace_root: `_write_machine_file`/`_ensure_machine_directory` take no
        # workspace_root at all, and `media.*`/`skill.*`/`operator.*` resolve against a
        # machine home, a media store, a skills dir and an external lane respectively. The
        # fence's workspace redirect cannot reach any of them, so a grant must not either.
        ("machine.write_file", {"path": "~/Desktop/pwned.txt", "content": "x"}),
        ("machine.ensure_directory", {"path": "~/Desktop/pwned-dir"}),
        ("machine.move_path", {"source": "~/Desktop/a", "destination": "~/Desktop/b"}),
        ("skill.create", {"name": "pwn", "description": "d", "instructions": "i"}),
        ("workspace.rollback_last_change", {}),
    ],
)
def test_a_workspace_write_tool_that_ignores_the_workspace_root_is_refused(
    isolated_store, operator_checkout, clean_containment, intent, arguments
):
    seats = SeatThatActs(intent, arguments, workspace_root=operator_checkout)
    run = _run(operator_checkout, seats, grants={"s1": ("workspace_write",)})
    run.run()

    assert _contained(seats.executions[0]), f"{intent} is not rooted in the seat workspace"


@pytest.mark.parametrize(
    "intent,arguments,check",
    [
        ("workspace.write_file", {"path": "a/b.py", "content": "made\n"}, "a/b.py"),
        ("workspace.ensure_directory", {"path": "made/dir"}, "made/dir"),
    ],
)
def test_an_approved_builder_creates_only_inside_its_disposable_workspace(
    isolated_store, operator_checkout, clean_containment, intent, arguments, check
):
    seats = SeatThatActs(intent, arguments, workspace_root=operator_checkout)
    run = _run(operator_checkout, seats, grants={"s1": ("workspace_write",)})
    run.run()

    execution = seats.executions[0]
    assert execution is not None and execution.ok, execution
    jail = clean_containment.seat_workspace(run.run_id, "s1")
    assert (jail / check).exists()
    assert not (operator_checkout / check).exists()


def test_an_approved_builder_edits_only_inside_its_disposable_workspace(
    isolated_store, operator_checkout, clean_containment
):
    """Create then edit then patch, all through typed tools, all inside the jail."""
    from core.council.containment import seat_session_id_for

    calls: list[object] = []

    def seat_turn(seat, prompt, round_no, run_id):
        if seat.seat_id == "s1" and round_no == 1:
            ctx = {
                "workspace": str(operator_checkout),
                "runtime_session_id": seat_session_id_for(run_id, "s1"),
            }
            calls.append(execute_runtime_tool(
                "workspace.write_file", {"path": "cand.py", "content": "one\ntwo\n"}, source_context=ctx))
            calls.append(execute_runtime_tool(
                "workspace.replace_in_file",
                {"path": "cand.py", "old_text": "two", "new_text": "three"},
                source_context=ctx))
        tail = "\nVERDICT: AGREE" if round_no >= 2 else ""
        return {"text": "DIAGNOSIS: x\nFIX: y" + tail, "receipt_count": 0, "session_id": None}

    run = _run(operator_checkout, seat_turn, grants={"s1": ("workspace_write",)})
    run.run()

    assert all(c is not None and c.ok for c in calls), calls
    jail = clean_containment.seat_workspace(run.run_id, "s1")
    assert (jail / "cand.py").read_text(encoding="utf-8") == "one\nthree\n"
    assert not (operator_checkout / "cand.py").exists()


# ================================================= 3. the escapes, with the grant in hand


@pytest.mark.parametrize(
    "raw",
    [
        "$HOME/pwned.txt",
        "${HOME}/pwned.txt",
        "$(echo /tmp)/pwned.txt",
        "`echo /tmp`/pwned.txt",
        "~/pwned.txt",
        "../../../../../../../../etc/pwned.txt",
        "a/../../../pwned.txt",
    ],
)
def test_shell_syntax_in_a_path_never_reaches_a_shell_and_never_escapes(
    isolated_store, operator_checkout, clean_containment, raw
):
    """No interpreter ever sees these strings — there is no interpreter a seat can reach.

    A typed tool treats them as literal path components. `~` is expanded by the fence and
    then contained; `$HOME`, `${…}`, `$(…)` and backticks are expanded by nothing at all, so
    they are ordinary (silly) directory names. Either way the only question that matters is
    where the byte lands, and the answer has to be "inside the jail, or nowhere".
    """
    home = os.path.expanduser("~")
    seats = SeatThatActs(
        "workspace.write_file", {"path": raw, "content": "escaped\n"}, workspace_root=operator_checkout
    )
    run = _run(operator_checkout, seats, grants={"s1": ("workspace_write",)})
    run.run()

    assert not os.path.exists(os.path.join(home, "pwned.txt"))
    assert not os.path.exists("/etc/pwned.txt")
    jail = clean_containment.seat_workspace(run.run_id, "s1")
    execution = seats.executions[0]
    if execution is not None and execution.ok:
        landed = [p for p in jail.rglob("*") if p.is_file()]
        assert landed, "the call reported success but wrote nothing"
        for path in landed:
            assert str(path.resolve()).startswith(str(jail.resolve()))
    else:
        assert _contained(execution), execution


@pytest.mark.parametrize("alias", ["filepath", "filename", "file", "folder", "dir", "file_path"])
def test_no_argument_alias_reaches_outside_the_jail(
    isolated_store, operator_checkout, clean_containment, alias
):
    victim = operator_checkout / "core" / "agent_runtime" / "turn_planner_hook.py"
    seats = SeatThatActs(
        "workspace.write_file", {alias: str(victim), "content": "aliased\n"}, workspace_root=operator_checkout
    )
    run = _run(operator_checkout, seats, grants={"s1": ("workspace_write",)})
    run.run()

    assert victim.read_text(encoding="utf-8") == ORIGINAL
    assert _contained(seats.executions[0])


def test_a_hard_link_inside_the_jail_is_refused(
    isolated_store, operator_checkout, clean_containment
):
    """`Path.resolve()` cannot see a hard link — the second name IS the file.

    No typed tool a seat can reach creates one, so this is defence against a door that does
    not exist yet rather than a live escape. It is cheap and it is a real OS fact
    (`st_nlink`), not a guess about a string, so it goes in.
    """
    victim = operator_checkout / "core" / "agent_runtime" / "turn_planner_hook.py"
    run = _run(operator_checkout, None, grants={"s1": ("workspace_write",)})
    jail = clean_containment.seat_workspace(run.run_id, "s1")
    try:
        os.link(victim, jail / "linked.py")
    except (OSError, NotImplementedError):  # pragma: no cover
        pytest.skip("hard links unavailable here")

    seats = SeatThatActs(
        "workspace.replace_in_file",
        {"path": "linked.py", "old_text": "original", "new_text": "swapped"},
        workspace_root=operator_checkout,
    )
    run.seat_turn = seats
    run.run()

    assert victim.read_text(encoding="utf-8") == ORIGINAL
    assert _contained(seats.executions[0])


def test_a_symlink_swapped_in_after_the_fence_check_still_cannot_escape(
    isolated_store, operator_checkout, clean_containment, monkeypatch
):
    """TOCTOU: the path is clean when the fence resolves it and a symlink by the time the
    handler runs. The fence is not the last check — the handler re-resolves against the
    redirected root — so the swap loses to the second one."""
    from core.council import containment as containment_module

    victim = operator_checkout / "core" / "agent_runtime" / "turn_planner_hook.py"
    run = _run(operator_checkout, None, grants={"s1": ("workspace_write",)})
    jail = clean_containment.seat_workspace(run.run_id, "s1")
    real_evaluate = containment_module.evaluate

    def _swap_after_check(intent, arguments, source_context):
        verdict = real_evaluate(intent, arguments, source_context)
        if verdict.workspace is not None and not verdict.refusal:
            link = jail / "swap"
            if not link.exists():
                try:
                    os.symlink(operator_checkout / "core" / "agent_runtime", link, target_is_directory=True)
                except (OSError, NotImplementedError):  # pragma: no cover
                    pytest.skip("symlinks unavailable here")
        return verdict

    monkeypatch.setattr("core.runtime_execution_tools.council_containment_evaluate", _swap_after_check, raising=False)
    monkeypatch.setattr(containment_module, "evaluate", _swap_after_check)

    seats = SeatThatActs(
        "workspace.write_file",
        {"path": "swap/turn_planner_hook.py", "content": "swapped\n"},
        workspace_root=operator_checkout,
    )
    run.seat_turn = seats
    run.run()

    assert victim.read_text(encoding="utf-8") == ORIGINAL


def test_the_operator_checkout_is_byte_identical_after_every_attack(
    isolated_store, operator_checkout, clean_containment, tmp_path
):
    """One run, every attack in sequence, one hash of the whole tree before and after."""
    import hashlib

    def tree_hash(root):
        digest = hashlib.sha256()
        for path in sorted(p for p in root.rglob("*")):
            digest.update(str(path.relative_to(root)).encode())
            if path.is_file() and not path.is_symlink():
                digest.update(path.read_bytes())
        return digest.hexdigest()

    before = tree_hash(operator_checkout)
    victim = operator_checkout / "core" / "agent_runtime" / "turn_planner_hook.py"
    attacks = [
        ("workspace.write_file", {"path": str(victim), "content": "x"}),
        ("workspace.write_file", {"path": "../operator-checkout/core/agent_runtime/turn_planner_hook.py", "content": "x"}),
        ("workspace.replace_in_file", {"path": str(victim), "old_text": "original", "new_text": "x"}),
        ("workspace.apply_unified_diff", {"patch": "--- a\n+++ b\n"}),
        ("machine.write_file", {"path": str(victim), "content": "x"}),
        ("machine.move_path", {"source": str(victim), "destination": str(tmp_path / "gone.py")}),
        ("sandbox.run_command", {"command": f"rm -f {victim}"}),
        ("workspace.run_tests", {"command": f"python3 -c \"open(r'{victim}','w').write('x')\""}),
        ("skill.install", {"name": "x"}),
        ("orchestration.execute_envelope", {"task_envelope": {"task_id": "t", "role": "builder"}}),
    ]

    from core.council.containment import seat_session_id_for

    def seat_turn(seat, prompt, round_no, run_id):
        if seat.seat_id == "s1" and round_no == 1:
            ctx = {
                "workspace": str(operator_checkout),
                "runtime_session_id": seat_session_id_for(run_id, "s1"),
            }
            for intent, arguments in attacks:
                execute_runtime_tool(intent, dict(arguments), source_context=ctx)
        tail = "\nVERDICT: AGREE" if round_no >= 2 else ""
        return {"text": "DIAGNOSIS: x\nFIX: y" + tail, "receipt_count": 0, "session_id": None}

    run = _run(operator_checkout, seat_turn, grants={"s1": ("workspace_write",)})
    run.run()

    assert tree_hash(operator_checkout) == before
    assert victim.read_text(encoding="utf-8") == ORIGINAL


# ============================================================ 4. cancellation that lands


class _Stream:
    """A seat-turn response that yields its first line, then blocks until released.

    Standing in for the real thing: `live_seat_turn` iterates the streaming body, so a seat
    turn is "in flight" for exactly as long as this iterator has not finished. Closing it is
    what a cancellation has to do; `closed` records whether anything did.
    """

    def __init__(self, lines, gate):
        self.lines = list(lines)
        self.gate = gate
        self.closed = False
        self.status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def close(self):
        self.closed = True
        self.gate.set()

    def __iter__(self):
        yield from self.lines
        self.gate.wait(5)
        if self.closed:
            raise ConnectionError("stream closed under the reader")


def test_stopping_a_run_cancels_the_in_flight_seat_turn_at_the_server(
    isolated_store, operator_checkout, clean_containment, monkeypatch
):
    """The council dispatches each seat as a real /api/chat turn carrying `session_id` and
    `turn_id`. The server registers exactly that pair in `core.live_turns`, whose
    `request_cancel` sets the Event the router's cancel check reads. So a seat turn IS
    cancellable at the server, in-process — the first pass simply never asked."""
    import json
    import threading

    from core.council import containment
    from core.council.dispatch import live_seat_turn_factory
    from core.council.orchestrator import Seat

    cancelled: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "core.live_turns.request_cancel",
        lambda session_id, turn_id: cancelled.append((session_id, turn_id)) or "cancelled",
    )

    gate = threading.Event()
    stream = _Stream([json.dumps({"message": {"content": "partial"}}).encode() + b"\n"], gate)
    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout=None: stream)

    run_id = "council-cancel01"
    containment.register_run(run_id, seat_ids=["s1"])
    # No pinned cloud model on this seat: `ensure_seat_model_pinned` would make its own
    # round trip through the same faked urlopen before the seat turn ever opens, and this
    # test is about the turn. The spend wall on a fenced run is proven separately, below.
    seat = Seat("s1", "builder", "", True)
    turn = live_seat_turn_factory("http://127.0.0.1:9", "cap")

    result: dict = {}
    worker = threading.Thread(
        target=lambda: result.setdefault("err", _capture(turn, seat, run_id)), daemon=True
    )
    worker.start()
    assert _wait_for(lambda: containment.inflight_seat_turns(run_id), 5), "the turn never registered as in flight"

    containment.fence_run(run_id, reason="stopped by the operator")
    worker.join(timeout=10)

    assert cancelled == [(containment.seat_session_id_for(run_id, "s1"), f"{run_id}-r1-s1")], cancelled
    assert stream.closed, "the in-flight stream was never closed"
    assert not worker.is_alive()


def _capture(turn, seat, run_id):
    try:
        turn(seat, "prompt", 1, run_id)
        return "returned"
    except Exception as exc:  # the test asserts on the type name
        return f"{type(exc).__name__}"


def _wait_for(predicate, seconds):
    import time as _time

    deadline = _time.time() + seconds
    while _time.time() < deadline:
        if predicate():
            return True
        _time.sleep(0.01)
    return False


def test_no_further_paid_turn_is_dispatched_after_a_stop(
    isolated_store, operator_checkout, clean_containment, monkeypatch
):
    """A fenced run must not reach the network at all — not for the seat turn, and not for
    the model PIN that precedes it. A pin is itself a request, and a seat turn on a paid
    model is money."""
    from core.council import containment
    from core.council.dispatch import live_seat_turn_factory
    from core.council.orchestrator import Seat

    requests: list[str] = []

    def _urlopen(request, timeout=None):
        requests.append(request.full_url)
        raise AssertionError("a fenced run reached the network")

    monkeypatch.setattr("urllib.request.urlopen", _urlopen)

    run_id = "council-cancel02"
    containment.register_run(run_id, seat_ids=["s1"])
    containment.fence_run(run_id, reason="stopped by the operator")

    turn = live_seat_turn_factory("http://127.0.0.1:9", "cap")
    with pytest.raises(Exception) as caught:
        turn(Seat("s1", "builder", "vendor/alpha", True), "prompt", 1, run_id)

    assert requests == [], f"a fenced run still spent: {requests}"
    assert getattr(caught.value, "attempt_outcome", "") == "CANCELLED", caught.value


def test_a_seat_result_that_arrives_after_a_stop_is_discarded(
    isolated_store, operator_checkout, clean_containment
):
    """A late report is not a report. The seat turn completes normally — the stop landed
    while it was in flight — and its text must never become a landed report the council
    then adjudicates on."""
    holder: dict = {}

    def seat_turn(seat, prompt, round_no, run_id):
        if seat.seat_id == "s1" and round_no == 1:
            holder["run"].request_stop()
            return {
                "text": "DIAGNOSIS: a late answer\nFIX: should never land\nVERDICT: AGREE",
                "receipt_count": 3,
                "session_id": "openclaw:late",
            }
        return {"text": "DIAGNOSIS: x\nFIX: y", "receipt_count": 0, "session_id": None}

    run = _run(operator_checkout, seat_turn, problem="late result")
    holder["run"] = run
    outcome = run.run()

    assert outcome["result"] == "stopped", outcome
    landed = [r for round_reports in run.rounds for r in round_reports if r.status == "landed"]
    assert landed == [], f"a report landed after the stop: {[r.seat_id for r in landed]}"
    texts = [getattr(r, "text", "") or "" for round_reports in run.rounds for r in round_reports]
    assert not any("a late answer" in t for t in texts), "the late text was kept"


def test_the_cancellation_is_durable_in_the_runs_own_ledger(
    isolated_store, operator_checkout, clean_containment
):
    from core.council.run_store import CouncilRunStore

    holder: dict = {}
    seats = SeatThatActs(
        "workspace.write_file",
        {"path": "cand.py", "content": "too late\n"},
        workspace_root=operator_checkout,
        on_call=lambda _run_id: holder["run"].request_stop(),
    )
    run = _run(operator_checkout, seats, grants={"s1": ("workspace_write",)})
    holder["run"] = run
    run.run()

    rows = [r for r in CouncilRunStore(run.run_id).read_events() if r.get("type") == "seat_effect"]
    assert [r["outcome"] for r in rows] == ["cancelled"], rows
    attempts = [r for r in CouncilRunStore(run.run_id).read_events() if r.get("type") == "seat_attempt"]
    assert any(r.get("outcome") == "CANCELLED" for r in attempts), attempts


def test_two_concurrent_runs_keep_separate_workspaces_and_separate_fences(
    isolated_store, operator_checkout, clean_containment
):
    """Stopping one run must not fence the other, and neither may see the other's jail."""
    a_seat = SeatThatActs(
        "workspace.write_file", {"path": "a.py", "content": "A\n"}, workspace_root=operator_checkout
    )
    b_seat = SeatThatActs(
        "workspace.write_file", {"path": "b.py", "content": "B\n"}, workspace_root=operator_checkout
    )
    run_a = _run(operator_checkout, a_seat, grants={"s1": ("workspace_write",)}, problem="run A")
    run_b = _run(operator_checkout, b_seat, grants={"s1": ("workspace_write",)}, problem="run B")

    clean_containment.fence_run(run_a.run_id, reason="A was stopped")
    run_b.run()

    assert b_seat.executions[0].ok, "stopping run A fenced run B"
    jail_a = clean_containment.seat_workspace(run_a.run_id, "s1")
    jail_b = clean_containment.seat_workspace(run_b.run_id, "s1")
    assert jail_a != jail_b
    assert (jail_b / "b.py").exists() and not (jail_a / "b.py").exists()
    assert clean_containment.effect_ledger(run_a.run_id) == ()


# ============================================================ 5. the invariant, and the brief


def test_every_declared_class_is_either_read_only_grantable_or_never_grantable(clean_containment):
    """No class may be silently unclassified.

    `NEVER_GRANTABLE_SIDE_EFFECT_CLASSES` is a written list, and a written list goes stale.
    This is what keeps it honest: every class any contract in this build declares has to fall
    into exactly one of the three buckets, so a new class shows up here as a failure rather
    than as a seat quietly refused (or, worse, quietly allowed) for reasons nobody named.
    """
    from core.runtime_tool_contracts import runtime_tool_contract_map

    declared = {
        str(getattr(contract, "side_effect_class", "") or "").strip()
        for contract in runtime_tool_contract_map().values()
    }
    declared.discard("")
    buckets = (
        {clean_containment.READ_ONLY_CLASS}
        | clean_containment.GRANTABLE_SIDE_EFFECT_CLASSES
        | clean_containment.NEVER_GRANTABLE_SIDE_EFFECT_CLASSES
    )
    assert declared <= buckets, f"unclassified side-effect classes: {sorted(declared - buckets)}"
    assert not (
        clean_containment.GRANTABLE_SIDE_EFFECT_CLASSES
        & clean_containment.NEVER_GRANTABLE_SIDE_EFFECT_CLASSES
    )
    assert clean_containment.READ_ONLY_CLASS not in clean_containment.GRANTABLE_SIDE_EFFECT_CLASSES


def test_every_grantable_tool_actually_lands_inside_the_redirected_workspace(
    isolated_store, operator_checkout, clean_containment
):
    """The allowlist's claim is that these four resolve against the redirected root. Proven
    by driving each one and looking at where the byte went — not by reading the dispatcher."""
    from core.council.containment import seat_session_id_for

    written: dict[str, object] = {}

    def seat_turn(seat, prompt, round_no, run_id):
        if seat.seat_id == "s1" and round_no == 1:
            ctx = {
                "workspace": str(operator_checkout),
                "runtime_session_id": seat_session_id_for(run_id, "s1"),
            }
            written["ensure"] = execute_runtime_tool(
                "workspace.ensure_directory", {"path": "pkg"}, source_context=ctx)
            written["write"] = execute_runtime_tool(
                "workspace.write_file", {"path": "pkg/m.py", "content": "a\nb\n"}, source_context=ctx)
            written["replace"] = execute_runtime_tool(
                "workspace.replace_in_file",
                {"path": "pkg/m.py", "old_text": "b", "new_text": "c"}, source_context=ctx)
            written["patch"] = execute_runtime_tool(
                "workspace.apply_unified_diff",
                {"patch": "--- a/pkg/m.py\n+++ b/pkg/m.py\n@@ -1,2 +1,2 @@\n a\n-c\n+d\n"},
                source_context=ctx)
        tail = "\nVERDICT: AGREE" if round_no >= 2 else ""
        return {"text": "DIAGNOSIS: x\nFIX: y" + tail, "receipt_count": 0, "session_id": None}

    run = _run(operator_checkout, seat_turn, grants={"s1": ("workspace_write",)})
    run.run()

    assert len(written) == len(clean_containment.SEAT_WORKSPACE_MUTATION_TOOLS), (
        "every allowlisted tool must be exercised here, or the allowlist has an untested member"
    )
    assert all(r is not None and r.ok for r in written.values()), written
    jail = clean_containment.seat_workspace(run.run_id, "s1")
    assert (jail / "pkg" / "m.py").read_text(encoding="utf-8") == "a\nd\n", written["patch"]
    assert not (operator_checkout / "pkg").exists()


def test_the_seat_brief_never_promises_a_shell(isolated_store, operator_checkout, clean_containment):
    """The brief a granted seat is prompted with has to match what the fence will do.

    The first pass shipped a seat context that said "You have read-only tools" while the
    tools were not read-only. The failure mode is the same in reverse: telling a granted seat
    it may run its tests, when the runtime will refuse every command, wastes the whole turn
    on a plan that cannot execute.
    """
    prompts: list[str] = []

    def seat_turn(seat, prompt, round_no, run_id):
        prompts.append(prompt)
        tail = "\nVERDICT: AGREE" if round_no >= 2 else ""
        return {"text": "DIAGNOSIS: x\nFIX: y" + tail, "receipt_count": 0, "session_id": None}

    run = _run(operator_checkout, seat_turn, grants={"s1": ("workspace_write",)})
    run.run()

    builder = next(p for p in prompts if "BUILDER" in p)
    assert "workspace.write_file" in builder, builder
    assert "cannot run a shell" in builder, builder
    assert "disposable workspace" in builder, builder


# ============================================================ 6. the sub-envelope door


def test_a_seat_cannot_reach_the_envelope_executor_at_all(
    isolated_store, operator_checkout, clean_containment
):
    """`core/orchestration/executor.py` runs a whole graph of runtime tool steps, and it
    rebuilds each step's context from parts (`executor.py:160-170`) — including OVERWRITING
    `runtime_session_id` with whatever the event context carries, which for a sub-step is
    not the seat's. `core.orchestration.execute_task_envelope` has exactly one caller in the
    tree (`_execute_task_envelope_intent`), reached only from the `orchestration.execute_envelope`
    dispatch branch, so closing that one intent closes the whole executor to a seat."""
    seats = SeatThatActs(
        "orchestration.execute_envelope",
        {"task_envelope": {"task_id": "t1", "role": "builder", "inputs": {}}},
        workspace_root=operator_checkout,
    )
    run = _run(operator_checkout, seats, grants={"s1": ("workspace_write",)})
    run.run()

    assert _contained(seats.executions[0]), "the envelope executor was reachable from a seat"


def test_a_context_rebuilt_the_way_the_envelope_executor_rebuilds_one_is_still_fenced(
    isolated_store, operator_checkout, clean_containment
):
    """Defence in depth for the door above, in the exact shape that door would produce.

    `executor.py` sets `session_id` to the ENVELOPE's task id and then overwrites
    `runtime_session_id` with the event context's — empty, for a step that has none. A fence
    that only looked at those two fields would see a stranger and stand aside. It also reads
    the session id frozen onto the turn's effect ledger, which the sub-step inherits through
    `copy_context`, so losing both fields costs the lookup and not the containment."""
    from core.effect_gateway import close_effect_receipt_scope, open_effect_receipt_scope

    victim = operator_checkout / "core" / "agent_runtime" / "turn_planner_hook.py"
    run = _run(operator_checkout, None)
    seat_context = {
        "workspace": str(operator_checkout),
        "runtime_session_id": clean_containment.seat_session_id_for(run.run_id, "s1"),
    }
    open_effect_receipt_scope(seat_context)
    try:
        rebuilt = {
            "workspace": str(operator_checkout),
            "session_id": "envelope-task-1",   # executor.py:163
            "runtime_session_id": "",          # executor.py:170, overwritten and empty
            "task_id": "envelope-task-1",
        }
        execution = execute_runtime_tool(
            "workspace.write_file",
            {"path": "core/agent_runtime/turn_planner_hook.py", "content": "via envelope\n"},
            source_context=rebuilt,
        )
    finally:
        close_effect_receipt_scope()

    assert victim.read_text(encoding="utf-8") == ORIGINAL
    assert _contained(execution)
