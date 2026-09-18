"""The pinned PAID build dispatches end to end under a real reservation -- or refuses before
the network names the authority's own code.

Drives the REAL router invoke loop (``_invoke_manifest`` with its paid gate, circuit, settle and
release paths) behind the builder's pinned generation, with a synthetic paid manifest and a
stub ADAPTER (the wire is the only stand-in; every gate, the ledger and the receipts are real).
This is the incident's exact shape: an HTML/script build request pinned to a paid model used to
refuse every generation with ``paid_call_not_authorized_or_reserved`` before any request was
sent.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest import mock

import pytest


@pytest.fixture()
def paid_home(tmp_path, monkeypatch):
    import os

    from storage.db import configure_default_db_path

    configure_default_db_path(os.path.join(tmp_path, "effect_budget.db"))
    from core import effect_budget

    effect_budget.reset_effect_budget_process_state()
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("VOOL_HOME", str(home))
    from core import runtime_paths

    runtime_paths.configure_runtime_home(home)
    from storage.migrations import run_migrations

    run_migrations()
    yield home
    effect_budget.reset_effect_budget_process_state()
    configure_default_db_path(None)


PAID_MODEL = "synth/paid-builder-model"


def _paid_manifest() -> SimpleNamespace:
    # A REMOTE base_url, like the byok lane in production: the authorship authority attests a
    # configured remote lane by the operator's own configuration, while a loopback endpoint is
    # held to the local tool-certification probe (which this synthetic lane cannot serve).
    return SimpleNamespace(
        provider_id="openrouter-byok:" + PAID_MODEL,
        model_name=PAID_MODEL,
        adapter_type="openai_compatible",
        source_type="http",
        runtime_config={"base_url": "https://synth-paid-lane.invalid/v1"},
        metadata={"cost_class": "paid_cloud"},
        provider_name="openrouter-byok",
        enabled=True,
    )


def _agent_with_paid_lane(paid_home, adapter_responses):
    """A real VoolAgent whose router serves the synthetic paid manifest from a stub adapter."""
    from apps.vool_agent import VoolAgent

    agent = VoolAgent(backend_name="test-backend", device="openclaw-test", persona_id="default")
    manifest = _paid_manifest()
    calls: list[str] = []

    def fake_build_adapter(built_manifest):
        calls.append(str(getattr(built_manifest, "model_name", "")))

        class _Adapter(SimpleNamespace):
            pass

        adapter = _Adapter(
            provider_id=manifest.provider_id,
            model_name=manifest.model_name,
        )
        adapter.run_text_task = lambda request, **kwargs: _next_response()
        adapter.invoke = lambda request, **kwargs: _next_response()
        return adapter

    def _next_response():
        from adapters.base_adapter import ModelResponse

        outcome = adapter_responses.pop(0) if adapter_responses else {"text": "print('generated')"}
        if isinstance(outcome, Exception):
            raise outcome
        return ModelResponse(
            output_text=str(outcome.get("text", "")),
            provider_id=manifest.provider_id,
            model_name=manifest.model_name,
            finish_reason="stop",
            usage={
                "prompt_tokens": 120,
                "completion_tokens": 80,
                "total_tokens": 200,
            },
            provider_metadata={},
        )

    agent.memory_router.registry.build_adapter = fake_build_adapter  # type: ignore[method-assign]
    return agent, manifest, calls


def _pinned_ctx(workspace: str) -> dict[str, object]:
    return {
        "workspace": workspace,
        "workspace_root": workspace,
        "operating_mode": "auto",
        "surface": "api",
        # The server-stamped owner-local trust key (``core.request_trust.OWNER_LOCAL_KEY``):
        # the reservation authority reads this, not a self-declared boolean.
        "_owner_local": True,
        "session_id": "paid-build-e2e",
        "runtime_session_id": "paid-build-e2e",
        "turn_id": "turn-paid-build-" + uuid.uuid4().hex[:8],
        "requested_model": PAID_MODEL,
        "model_selection": "pin",
    }


def _run_build(agent, manifest, tmp_path):
    from core.agent_runtime.builder import controller as agent_builder_controller

    task = SimpleNamespace(task_id="task-paid-build", prompt_tokens=100, max_output_tokens=512)
    recorder = _RecorderBuild()
    with (
        mock.patch.object(agent.memory_router, "_requested_model_manifest", return_value=manifest),
        mock.patch.object(
            agent.memory_router, "_dispatch_binding_is_current", return_value=True
        ),
        mock.patch("core.memory_first_router.should_probe_health", return_value=False),
        mock.patch(
            "core.agent_runtime.builder.app_builder.build_app_from_spec",
            side_effect=recorder,
        ),
        mock.patch(
            "core.agent_runtime.builder.app_builder.render_app_build_response",
            return_value="Built the file.",
        ),
    ):
        return (
            agent_builder_controller._run_model_build(
                agent,
                task=task,
                effective_input="build me a single-file python tool that prints the generated banner",
                classification={"task_class": "system_design"},
                interpretation=None,
                session_id="paid-build-e2e",
                source_context=_pinned_ctx(str(tmp_path / "ws")),
                target={"root_dir": "generated/tool", "platform": "generic", "language": "python"},
                load_active_persona_fn=lambda *a, **k: None,
            ),
            recorder,
        )


class _RecorderBuild:
    """Stands in for the build itself: one generation in; a file out ONLY when the generation
    produced content (an honest report, unlike a stub that claims files regardless)."""

    def __init__(self) -> None:
        self.generated: list[str] = []

    def __call__(self, *, request, target_rel, source_context, generate_fn, run_tool_fn, **kwargs):
        text = generate_fn("write banner.py with a banner function")
        self.generated.append(text)
        return SimpleNamespace(
            target_dir=target_rel,
            files_written=["banner.py"] if text.strip() else [],
            files_skipped=[],
            file_lines={},
            tests_ran=False,
            tests_passed=False,
            fix_rounds=0,
            expected_test_outcome="unspecified",
            subject_paths=(),
            test_returncode=None,
            test_command="",
            proof_failed=False,
            test_output="",
            handled=True,
            error="",
            scope_kind="open",
            authorized_paths=(),
            paths_refused=[],
            commands_refused=[],
        )


def test_a_pinned_paid_build_dispatches_settles_and_attributes(paid_home, tmp_path) -> None:
    agent, manifest, adapter_calls = _agent_with_paid_lane(paid_home, [{"text": "def banner(): return 'BANNER'"}])
    result, recorder = _run_build(agent, manifest, tmp_path)

    assert adapter_calls, (
        "the pinned paid model was never dispatched: "
        f"response={result.get('response')!r} details={result.get('details')!r}"
    )
    assert recorder.generated == ["def banner(): return 'BANNER'"]
    response = str(result.get("response") or "")
    assert PAID_MODEL in response and "the model you selected" in response, response
    assert result.get("success") is not False

    # The reservation really existed and really settled: the ledger holds a settled row for
    # this build's task, so the ceiling no longer counts against the caps and the real spend
    # does.
    from storage.db import get_connection

    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT model_call_id, status FROM model_spend_reservations WHERE task_id = ?",
            ("task-paid-build",),
        ).fetchall()
    finally:
        conn.close()
    assert rows, "no spend ledger row was written for the dispatched paid generation"
    assert any(str(row["status"]) == "settled" for row in rows), [str(row["status"]) for row in rows]


def test_a_reserved_call_that_fails_at_the_provider_is_released_and_named(paid_home, tmp_path) -> None:
    """The FIRST Agent Trace Viewer failure's shape: a reservation genuinely made, then the
    provider itself failed. The typed transport failure must surface as the cause -- not as an
    authorization refusal, and the reservation must not stay standing against the caps."""
    from core.model_spend_ledger import SpendLimits, reserve_spend

    agent, manifest, adapter_calls = _agent_with_paid_lane(paid_home, [RuntimeError("connection reset by peer")])
    result, recorder = _run_build(agent, manifest, tmp_path)

    assert adapter_calls, "the call was dispatched under a real reservation"
    response = str(result.get("response") or "")
    assert PAID_MODEL in response
    assert "paid_call_not_authorized_or_reserved" not in response, (
        "a provider transport failure was mislabelled as an authorization refusal"
    )
    assert result.get("success") is False
    details = dict((result.get("details") or {}).get("builder_pinned_refusal") or {})
    assert details.get("provider_contacted") is True, "the provider WAS contacted before it failed"
    assert "connection reset" in details.get("generation_error", ""), details


def test_an_exhausted_budget_refuses_before_the_network(paid_home, tmp_path) -> None:
    """Caps exhausted: the reservation is refused by the ledger, no adapter is built, and the
    operator sees the authority's own denial code with the provider stated as not contacted."""
    from core.model_spend_ledger import SpendLimits, reserve_spend, settle_spend

    # Consume the whole per-task cap for this task id with settled rows, each inside the
    # per-call ceiling the ledger itself enforces (4 x $0.25 = the $1.00 per-task cap).
    spend_task = "task-paid-build"
    limits = SpendLimits(per_call_usd=0.25, per_task_usd=1.0, daily_usd=5.0, monthly_usd=25.0)
    for index in range(4):
        eater_id = f"model-call-cap-eater-{index}"
        reserve_spend(
            model_call_id=eater_id,
            task_id=spend_task,
            subtask_id=f"cap-eater-{index}",
            model_id=PAID_MODEL,
            maximum_usd=0.25,
            limits=limits,
        )
        settle_spend(eater_id, actual_usd=0.25)

    agent, manifest, adapter_calls = _agent_with_paid_lane(paid_home, [])
    result, recorder = _run_build(agent, manifest, tmp_path)

    assert adapter_calls == [], "an exhausted budget must refuse BEFORE any adapter is built"
    assert recorder.generated == [""]
    response = str(result.get("response") or "")
    assert "per_task" in response, response
    assert "provider was not contacted" in response
    details = dict((result.get("details") or {}).get("builder_pinned_refusal") or {})
    assert details.get("provider_contacted") is False
