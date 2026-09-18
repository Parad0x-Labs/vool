"""A deterministic answer must not be preceded by inference bought to rediscover it.

The live defect, measured on a project turn asking "Read qa_test.txt and tell me exactly what it
says": **136.41s wall**, of which two provider calls took 76.623s and 59.615s, the workspace file
read took **0.0008s**, and everything after it took under 0.03s. The turn's own proof then reported
``backend: fast_path`` / ``fallback_reason: model_not_used``, and ``turn.trace_completed`` reported
``model_calls: 0`` beside two ``model.call_completed`` events. The semantic receipt had already
noticed the contradiction and said ``attempt_disagrees_with_self_report: true``.

Two independent defects produced that, and this file pins both.

**A. A routing gate spent a model call ahead of the lanes that answer for free.** The intent
arbiter ran at one position in the front door for two different signals. For AMBIGUITY -- two
families reading one message differently -- that position is right, and it keeps its model call.
For a NEAR-MISS -- nothing claimed, but the sentence names a tool-ish noun like "file" or "folder"
-- it meant a model was asked which lane owned a message the very next lanes would have claimed on
their own evidence. Measured on the untouched base, hermetically, counting provider invocations
rather than elapsed time:

    "read the qa_test.txt file and tell me exactly what it says"   arbiter call, then read in 0.0008s
    "List files in this project."                                  arbiter call, then folder overview
    "What files are in this folder?"                               arbiter call, then folder overview

**B. Nothing counted the calls.** The deterministic result builder wrote the literal ``0``; the
model-lane builder wrote capsule telemetry falling back to a BOOLEAN, so two calls read 1 and a
failed call read 0. `core/turn_model_call_ledger.py` holds the contract these tests enforce.

Every count below is taken at the PRODUCTION seams -- the provider execution boundary and the
arbiter's own HTTP post -- never from the field under test, so a receipt that agrees with the
instrument is agreeing with something it did not produce.
"""
from __future__ import annotations

import importlib
import os
from pathlib import Path
from typing import Any

import pytest

from tests.semantic_phase0._fixtures import (  # noqa: F401
    block_outbound_network,
    keep_the_checkout_clean,
    make_agent_module,
    pin_the_signing_key_passphrase,
    reseal_network_after_function_fixtures,
)


class ProviderInvocations:
    """Every provider call this turn ENTERED, counted where it is entered.

    Two seams, because the runtime has two ways to reach a provider: the adapter path through
    `core.provider_execution_boundary.invoke_provider_execution_boundary`, and the arbiter's direct
    HTTP post. Both are recorded before the call, so a call that then fails is still counted -- that
    is the contract under test, not an accident of where the hook sits.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.timeline: list[str] = []

    @property
    def count(self) -> int:
        return len(self.calls)

    def note_tool(self, intent: str) -> None:
        self.timeline.append(f"tool:{intent}")

    def note_provider(self, label: str) -> None:
        self.calls.append(label)
        self.timeline.append(f"provider:{label}")


@pytest.fixture()
def invocations(monkeypatch: pytest.MonkeyPatch) -> ProviderInvocations:
    from core import provider_execution_boundary, runtime_execution_tools

    seen = ProviderInvocations()

    real_boundary = provider_execution_boundary.invoke_provider_execution_boundary

    def _boundary(instance: Any, name: str, *args: Any, **kwargs: Any) -> Any:
        if name in {"run_text_task", "run_structured_task", "stream_text_task"}:
            seen.note_provider(f"adapter:{name}")
        return real_boundary(instance, name, *args, **kwargs)

    for module_name in ("core.provider_execution_boundary", "core.memory_first_router"):
        module = importlib.import_module(module_name)
        if hasattr(module, "invoke_provider_execution_boundary"):
            monkeypatch.setattr(module, "invoke_provider_execution_boundary", _boundary)

    # The arbiter does `import requests` INSIDE `arbitrate`, so a module-attribute patch on
    # `core.intent_arbiter` is not on its path -- patched here on the client itself, which is the
    # object the runtime really reaches for. It raises rather than answering: the count is of calls
    # ENTERED, and a hermetic suite has no provider to answer them.
    import requests

    def _post(*args: Any, **kwargs: Any) -> Any:
        seen.note_provider("direct_post")
        raise RuntimeError("provider unreachable in tests")

    def _get(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("no discovery probe in tests")

    monkeypatch.setattr(requests, "post", _post)
    monkeypatch.setattr(requests, "get", _get)

    real_tool = runtime_execution_tools.execute_runtime_tool

    def _tool(intent: str, *args: Any, **kwargs: Any) -> Any:
        seen.note_tool(str(intent))
        return real_tool(intent, *args, **kwargs)

    # The authorized execution boundary passes its extra keyword parameters through to the real
    # executor, so the same recorder serves both names: the canonical seam, and the direct
    # executor still imported by lanes outside the gate-parity families.
    def _authorized(intent: str, *args: Any, **kwargs: Any) -> Any:
        seen.note_tool(str(intent))
        return real_tool(intent, *args, **kwargs)

    for module_name in (
        "core.runtime_execution_tools",
        "core.agent_runtime.fast_paths_utility",
        "core.agent_runtime.turn_frontdoor",
        "core.agent_runtime.fast_paths_machine",
        "core.agent_runtime.research_tool_loop_facade",
    ):
        module = importlib.import_module(module_name)
        if hasattr(module, "execute_authorized_runtime_tool"):
            monkeypatch.setattr(module, "execute_authorized_runtime_tool", _authorized)
        if hasattr(module, "execute_runtime_tool"):
            monkeypatch.setattr(module, "execute_runtime_tool", _tool)
    return seen


@pytest.fixture()
def arbiter_is_reachable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin a model tag so the arbiter reaches its post instead of failing open on discovery.

    Without this the tag probe fails behind the network seal, `arbitrate` returns None at
    ``no_model``, and a test asserting "the arbiter may still be consulted" would pass while
    proving nothing about the gate it means to exercise.
    """
    monkeypatch.setenv("VOOL_ARBITER_MODEL", "qwen3:0.6b")
    monkeypatch.setenv("VOOL_INTENT_ARBITER", "1")
    from core import intent_arbiter

    intent_arbiter.reset_breaker()


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """A real folder with real files. Every assertion below reads bytes that are actually there."""
    workspace = tmp_path / "vool-fastpath-project"
    (workspace / "src").mkdir(parents=True)
    (workspace / "qa_test.txt").write_text("FASTPATH-EVIDENCE-4471\nsecond line\n")
    (workspace / "package.json").write_text('{"name": "fastpath-probe", "version": "0.5.0"}\n')
    (workspace / "src" / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    return workspace


def _drive(
    make_agent_module: Any,  # noqa: F811 - the parameter is pytest's fixture request, not a redefinition
    project: Path,
    text: str,
    *,
    session: str,
    bind_workspace: bool = True,
    requested_model: str = "",
) -> dict[str, Any]:
    context: dict[str, Any] = {
        "surface": "api",
        "session_id": session,
        "runtime_session_id": session,
        "request_id": f"req-{session}",
    }
    if bind_workspace:
        context["workspace"] = str(project)
        context["workspace_root"] = str(project)
    if requested_model:
        context["requested_model"] = requested_model
    os.chdir(project)
    return make_agent_module().run_once(
        text, session_id_override=session, source_context=context
    )


# ---------------------------------------------------------------------------------------------
# 1 + 2 -- a deterministic operation buys no inference, and the ANSWER is the real bytes
# ---------------------------------------------------------------------------------------------


#: Which read tool answers is the front door's business (the workspace lane owns a project-relative
#: name, the machine lane owns a path it can resolve on disk); this file's business is that ONE of
#: them did, with no inference bought to find out.
_READ_TOOLS = {"workspace.read_file", "machine.read_file"}


@pytest.mark.parametrize(
    ("text", "expected_tools", "evidence"),
    [
        # The measured request, verbatim.
        ("Read qa_test.txt and tell me exactly what it says.", _READ_TOOLS, "FASTPATH-EVIDENCE-4471"),
        # The same request carrying the noun "file", which is what made it a near-miss and bought
        # the arbiter call. Same operation, same authority, and now the same cost.
        ("read the qa_test.txt file and tell me exactly what it says", _READ_TOOLS, "FASTPATH-EVIDENCE-4471"),
        ("Read package.json.", _READ_TOOLS, "fastpath-probe"),
        ("what does src/calc.py contain?", _READ_TOOLS, "def add"),
        ("Show git status.", {"workspace.git_status"}, ""),
    ],
)
def test_a_deterministic_project_read_enters_no_provider_call(
    make_agent_module, invocations, arbiter_is_reachable, project, text, expected_tools, evidence  # noqa: F811
) -> None:
    result = _drive(make_agent_module, project, text, session=f"det-read-{abs(hash(text)) % 99999}")

    assert invocations.count == 0, (
        f"{text!r} entered {invocations.count} provider call(s) before answering deterministically: "
        f"{invocations.timeline}"
    )
    assert any(f"tool:{name}" in invocations.timeline for name in expected_tools), invocations.timeline
    assert result["fast_path_hit"] is True
    assert result["model_calls"] == 0
    if evidence:
        assert evidence in str(result["response"])


@pytest.mark.parametrize(
    "text",
    [
        "List files in this project.",
        "What files are in this folder?",
        "list the files in this project",
    ],
)
def test_a_deterministic_directory_listing_enters_no_provider_call(
    make_agent_module, invocations, arbiter_is_reachable, project, text  # noqa: F811
) -> None:
    result = _drive(make_agent_module, project, text, session=f"det-list-{abs(hash(text)) % 99999}")

    assert invocations.count == 0, (
        f"{text!r} entered {invocations.count} provider call(s) to reach a deterministic listing: "
        f"{invocations.timeline}"
    )
    assert result["fast_path_hit"] is True
    assert result["model_calls"] == 0
    # The listing is of the real folder, not of a remembered one.
    assert "package.json" in str(result["response"]) or str(project) in str(result["response"])


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # The mis-anchoring: the `/` matched INSIDE `src/calc.py`, so the machine lane was handed
        # `/calc.py` -- the filesystem root -- and answered "I cannot read that path in this lane"
        # about a file in the bound project. A project-relative name is not this lane's to read.
        ("what does src/calc.py contain?", None),
        ("open src/calc.py", None),
        # ...and every path shape that IS this lane's keeps working, including the explicitly
        # relative forms, which a lookbehind that also excluded "." would have broken.
        ("read ~/Desktop/notes.txt", "~/Desktop/notes.txt"),
        ("open /tmp/session.log", "/tmp/session.log"),
        ("tell me what ./local/x.md says", "./local/x.md"),
        ("read ../up/one.txt", "../up/one.txt"),
    ],
)
def test_a_typed_path_is_read_from_the_start_of_the_token(text: str, expected: str | None) -> None:
    """A path the lane acts on must be the path the user typed, not a suffix of it."""
    from core.agent_runtime.fast_paths_machine import _extract_machine_file_read_target

    target = _extract_machine_file_read_target(text)
    assert (None if target is None else target["path"]) == expected


# ---------------------------------------------------------------------------------------------
# 3 -- ambiguity still buys a model call. The fix removes speculation, not interpretation.
# ---------------------------------------------------------------------------------------------


def test_a_genuinely_ambiguous_request_still_reaches_the_model(
    make_agent_module, invocations, arbiter_is_reachable, project  # noqa: F811
) -> None:
    """Two families read this one message differently, so a model decides -- exactly as before.

    "check the reports folder on this machine and tell me the specs" is claimed by BOTH
    `find_folder` and `machine_specs`. Priority order would pick one by accident. This is the case
    the arbiter exists for and the one the fast-path rule must not take away.
    """
    from core.agent_runtime.intent_claims import is_ambiguous, probe_claims

    text = "check the reports folder on this machine and tell me the specs"
    assert is_ambiguous(probe_claims(text)), "the premise of this test stopped holding"

    _drive(make_agent_module, project, text, session="ambiguous-turn")

    assert "provider:direct_post" in invocations.timeline, (
        "an ambiguous message no longer reaches the arbiter: " f"{invocations.timeline}"
    )


def test_a_near_miss_nothing_deterministic_owns_still_reaches_the_arbiter(
    make_agent_module, invocations, arbiter_is_reachable, project  # noqa: F811
) -> None:
    """The near-miss arbiter moved one gate later; it did not stop existing.

    "fint the oken hunter folder" is the typo'd-verb case the near-miss branch was built for (it is
    the arbiter module's own worked example): no family claims it, no deterministic lane owns it,
    and without arbitration it falls to the model lane. It must still be arbitrated.
    """
    from core.agent_runtime.intent_claims import near_miss, probe_claims

    text = "fint the oken hunter folder"
    assert near_miss(text, probe_claims(text)), "the premise of this test stopped holding"

    _drive(make_agent_module, project, text, session="near-miss-unowned", bind_workspace=False)

    assert "provider:direct_post" in invocations.timeline, (
        "an unowned near-miss no longer reaches the arbiter: " f"{invocations.timeline}"
    )


# ---------------------------------------------------------------------------------------------
# 4 + 5 -- the receipt equals the ledger, and a FAILED attempt is in both
# ---------------------------------------------------------------------------------------------


def test_the_reported_model_calls_equal_the_provider_calls_actually_entered(
    make_agent_module, invocations, arbiter_is_reachable, project  # noqa: F811
) -> None:
    """One arbitrated turn: the instrument sees one call, and the turn reports one.

    The arbiter's post RAISES here (the network is sealed), which is the point. Under the old
    accounting this turn reported `model_calls: 0` twice over -- the deterministic builder wrote a
    literal zero, and the model-lane builder's boolean could not see a failed call either.
    """
    result = _drive(
        make_agent_module,
        project,
        "check the reports folder on this machine and tell me the specs",
        session="accounting-equal",
    )

    assert invocations.count >= 1, invocations.timeline
    assert result["model_calls"] == invocations.count, (
        f"turn reported model_calls={result['model_calls']} against "
        f"{invocations.count} entered provider call(s): {invocations.timeline}"
    )


def test_a_provider_call_that_fails_is_still_counted(
    make_agent_module, invocations, arbiter_is_reachable, project  # noqa: F811
) -> None:
    """The documented rule, stated as a test: an attempt is counted when it is ENTERED.

    A call that was made and then failed spent the input and the wall clock. A count that dropped
    it would report a 136-second turn as free.
    """
    from core import intent_arbiter

    result = _drive(
        make_agent_module,
        project,
        "check the reports folder on this machine and tell me the specs",
        session="accounting-failed",
    )

    assert intent_arbiter.last_failure().startswith("request:"), (
        f"this turn's arbiter call did not fail, so it proves nothing: {intent_arbiter.last_failure()!r}"
    )
    assert invocations.count >= 1
    assert result["model_calls"] >= 1, "a failed provider call was dropped from the turn's count"


def test_the_router_counts_an_adapter_call_it_actually_entered() -> None:
    """The other seam, driven through `MemoryFirstRouter._invoke_manifest` itself.

    Not through a hand-rolled `before_call`: that would prove the ledger can count, while leaving
    the line that calls it -- the one a refactor would drop -- covered by nothing. So a real
    manifest is registered, the ADAPTER is stubbed (a provider that raises is still a provider that
    was called), and the router's own code path runs. Each of the two invocations is one entered
    call, which is the contract: retries and failures each count once.
    """
    from unittest import mock

    from adapters.base_adapter import ModelRequest
    from core.memory_first_router import MemoryFirstRouter
    from core.model_health import reset_provider_health
    from core.model_registry import ModelRegistry
    from core.task_router import create_task_record
    from core.turn_model_call_ledger import begin_turn, reset_for_tests, turn_model_calls
    from storage.migrations import run_migrations

    run_migrations()
    reset_provider_health()
    reset_for_tests()

    registry = ModelRegistry()
    manifest = registry.register_manifest(
        {
            "provider_name": "ledger-probe-http",
            "model_name": "ledger-probe",
            "source_type": "http",
            "adapter_type": "local_qwen_provider",
            "license_name": "Apache-2.0",
            "license_reference": "https://www.apache.org/licenses/LICENSE-2.0",
            "weight_location": "user-supplied",
            "weights_bundled": False,
            "redistribution_allowed": True,
            "runtime_dependency": "openai-compatible-local-runtime",
            "capabilities": ["summarize"],
            "runtime_config": {"base_url": "http://127.0.0.1:1"},
            "enabled": True,
            "metadata": {"orchestration_role": "drone"},
        }
    )
    # `core.final_answer_authorship` refuses an uncertified LOOPBACK model before its adapter is
    # built, so without this the probe below counts zero calls and proves nothing about the
    # ledger. Certifying it is what an operator does; it does not bypass the authority. Same
    # remedy the sibling accounting probe already carries in
    # `tests/test_v050_fastpath_authority_and_call_accounting.py`.
    from tests._authorship_certification import certify_for_authorship

    certify_for_authorship(manifest)
    router = MemoryFirstRouter(registry)

    class _Adapter:
        def run_text_task(self, request: Any) -> Any:
            raise RuntimeError("provider unreachable")

        def supports_streaming(self) -> bool:
            return False

    context: dict[str, Any] = {"request_id": "router-seam"}
    begin_turn(context)
    task = create_task_record("count this call")
    request = ModelRequest(task_kind="summarize", prompt="count this call")

    try:
        with mock.patch.object(registry, "build_adapter", return_value=_Adapter()), mock.patch(
            "core.memory_first_router.should_probe_health", return_value=False
        ):
            for _attempt in range(2):
                router._invoke_manifest(
                    manifest=manifest,
                    request=request,
                    output_mode="plain_text",
                    task=task,
                    source_context=context,
                )
    finally:
        # This probe manifest is the only thing this test leaves in the shared store, and a stray
        # enabled provider is exactly the kind of cross-test coupling that shows up as one shard
        # failing and the same file passing alone. Removed by name, not by truncating the table.
        from storage.db import get_connection

        connection = get_connection()
        try:
            connection.execute(
                "DELETE FROM model_provider_manifests WHERE provider_name = ?",
                ("ledger-probe-http",),
            )
            connection.commit()
        finally:
            connection.close()

    assert turn_model_calls(context) == 2, "the router entered two provider calls and counted them as " f"{turn_model_calls(context)}"


def test_the_terminal_turn_trace_reports_the_calls_the_turn_made() -> None:
    """`turn.trace_completed.model_calls` is the requirement's literal wording, so pin it there.

    Driven through the real `run_agent` and the real deterministic result builder; only WHICH lane
    answered is stood in for. Two provider calls are recorded into this turn's ledger exactly as the
    router and the arbiter record them, the fast-path builder produces the result, and the terminal
    trace is read off the emitted event. Before this branch that trace said 0 for this exact shape.
    """
    from types import SimpleNamespace
    from unittest import mock

    from core.agent_runtime.fast_command_surface import _fast_path_route_metadata
    from core.turn_model_call_ledger import begin_turn, record_provider_call, reset_for_tests
    from core.web.api.runtime import RuntimeServices, run_agent

    reset_for_tests()
    runtime = RuntimeServices(display_name="VOOL")
    runtime.agent = mock.Mock()

    def _run_once(_text: str, *, source_context: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        begin_turn(source_context)
        record_provider_call(source_context)  # routing gate
        record_provider_call(source_context)  # and the call it did not save
        return {
            "response": "read from the file, not from a model",
            **_fast_path_route_metadata(
                "workspace_runtime_fast_path", source_context=source_context
            ),
        }

    runtime.agent.run_once.side_effect = _run_once
    policy = SimpleNamespace(
        chat_id="chat:fastpath-trace",
        project_id="",
        namespace_state="active",
        allow_project_context=False,
        allow_user_profile_context=False,
        allow_action_receipts=False,
        imported_chat_ids=frozenset(),
        imported_project_ids=frozenset(),
    )
    captured: list[dict[str, Any]] = []

    def _emit(_context: dict[str, Any], *, event_type: str, message: str, details: dict[str, Any]) -> None:
        captured.append({"event_type": event_type, "details": details})

    with mock.patch(
        "core.context_scope.ContextAccessPolicy.for_request", return_value=policy
    ), mock.patch("core.runtime_task_events.emit_runtime_event", side_effect=_emit):
        run_agent(
            runtime,
            "Read qa_test.txt and tell me exactly what it says.",
            session_id="chat:fastpath-trace",
            source_context={"request_id": "request-fastpath-trace"},
        )

    trace = captured[-1]
    assert trace["event_type"] == "turn.trace_completed"
    assert trace["details"]["model_calls"] == 2


def test_a_second_turn_on_a_reused_context_does_not_inherit_the_first_turns_count() -> None:
    """A caller that reuses one context dict must not accumulate. Keyed on the request identity."""
    from core.turn_model_call_ledger import (
        begin_turn,
        record_provider_call,
        reset_for_tests,
        turn_model_calls,
    )

    reset_for_tests()
    context: dict[str, Any] = {"request_id": "turn-one"}
    begin_turn(context)
    record_provider_call(context)
    record_provider_call(context)
    assert turn_model_calls(context) == 2

    context["request_id"] = "turn-two"
    begin_turn(context)
    assert turn_model_calls(context) == 0

    # ...and a NESTED sub-turn, which re-enters with the same identity, keeps counting into the
    # same ledger rather than opening a second one and losing the outer turn's calls.
    record_provider_call(context)
    begin_turn(context)
    record_provider_call(context)
    assert turn_model_calls(context) == 2


# ---------------------------------------------------------------------------------------------
# 6 -- the tool a request resolves to does not depend on which model the turn is pinned to
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "requested_model", ["", "vool:latest", "auto", "qwen3:8b", "openrouter/free-cloud-model"]
)
def test_tooling_is_identical_whichever_model_the_turn_is_pinned_to(
    make_agent_module, invocations, arbiter_is_reachable, project, requested_model  # noqa: F811
) -> None:
    """Local pin, cloud pin, or none: same tool, same bytes, same provider cost.

    The rule this defends is that the model choice may change the QUALITY of a model-written answer
    and nothing else. A deterministic read is not a model-written answer, so all five turns must be
    indistinguishable.
    """
    result = _drive(
        make_agent_module,
        project,
        "Read qa_test.txt and tell me exactly what it says.",
        session=f"parity-{abs(hash(requested_model)) % 99999}",
        requested_model=requested_model,
    )

    assert "tool:workspace.read_file" in invocations.timeline, invocations.timeline
    assert invocations.count == 0, invocations.timeline
    assert "FASTPATH-EVIDENCE-4471" in str(result["response"])
    assert result["model_calls"] == 0
