from contextlib import contextmanager
from unittest import mock

import pytest

from core.provider_call_deadline import PROVIDER_DEADLINE_KEY, ProviderCallDeadlineExceededError
from tests.test_openai_compatible_adapter import _cloud_adapter, _native_tool_request, _ollama_adapter, _request


@pytest.mark.parametrize("local", [False, True])
@pytest.mark.parametrize("streaming", [False, True])
def test_expired_provider_response_cannot_be_published(local, streaming):
    clock = [10.0]
    request = _request("Describe the basalt sample.")
    request.metadata[PROVIDER_DEADLINE_KEY] = 11.0
    adapter = _ollama_adapter() if local else _cloud_adapter()
    response = mock.Mock()
    response.json.return_value = {
        "message": {"content": "late"},
        "choices": [{"message": {"content": "late"}}],
    }
    response.iter_lines.return_value = [
        '{"message":{"content":"late"},"done":true}' if local else
        'data: {"choices":[{"delta":{"content":"late"}}]}',
    ]

    def post(*args, **kwargs):
        clock[0] = 12.0
        return response

    with mock.patch("core.runtime_active_clock.time.monotonic", side_effect=lambda: clock[0]), \
         mock.patch("adapters.openai_compatible_adapter.requests.post", side_effect=post):
        with pytest.raises(ProviderCallDeadlineExceededError):
            if streaming:
                list(adapter.stream_text_task(request))
            else:
                adapter.run_text_task(request)
    response.close.assert_called()


def test_local_admission_wait_cannot_authorize_a_post_after_deadline():
    clock = [20.0]
    request = _request("Explain a new alloy.")
    request.metadata[PROVIDER_DEADLINE_KEY] = 21.0

    @contextmanager
    def occupied_slot(**kwargs):
        clock[0] = 22.0
        yield

    with mock.patch("core.runtime_active_clock.time.monotonic", side_effect=lambda: clock[0]), \
         mock.patch("adapters.openai_compatible_adapter.local_model_slot", occupied_slot), \
         mock.patch("adapters.openai_compatible_adapter.requests.post") as post:
        with pytest.raises(ProviderCallDeadlineExceededError):
            _ollama_adapter().run_text_task(request)
    post.assert_not_called()


def test_retry_uses_remaining_budget_instead_of_rearming_first_timeout():
    clock = [30.0]
    request = _native_tool_request()
    request.metadata[PROVIDER_DEADLINE_KEY] = 35.0
    malformed = mock.Mock()
    malformed.json.return_value = {"choices": [{"message": {"content": "not a tool call"}}]}
    observed = []

    def post(*args, **kwargs):
        observed.append(kwargs["timeout"][1])
        clock[0] += 2.0
        return malformed

    with mock.patch("core.runtime_active_clock.time.monotonic", side_effect=lambda: clock[0]), \
         mock.patch("adapters.openai_compatible_adapter.requests.post", side_effect=post):
        with pytest.raises(RuntimeError):
            _cloud_adapter(model_name="vendor/tool-model:free", verified_free=True).run_structured_task(request)
    assert observed == [5.0, 3.0]


@pytest.mark.parametrize("late_frame", [False, True])
def test_stream_cannot_emit_late_content_or_a_success_terminal(late_frame):
    clock = [40.0]
    request = _request("Explain the tide measurements.")
    request.metadata[PROVIDER_DEADLINE_KEY] = 41.0
    response = mock.Mock()

    def frames(**kwargs):
        yield 'data: {"choices":[{"delta":{"content":"timely"}}]}'
        clock[0] = 42.0
        if late_frame:
            yield 'data: {"choices":[{"delta":{"content":"late"}}]}'

    response.iter_lines.side_effect = frames
    with mock.patch("core.runtime_active_clock.time.monotonic", side_effect=lambda: clock[0]), \
         mock.patch("adapters.openai_compatible_adapter.requests.post", return_value=response):
        chunks = _cloud_adapter().stream_text_task(request)
        assert next(chunks).delta_text == "timely"
        with pytest.raises(ProviderCallDeadlineExceededError):
            next(chunks)
    response.close.assert_called()


def test_conductor_keeps_same_worker_across_price_wait_longer_than_execution_budget(monkeypatch):
    from core import runtime_active_clock as clock
    from core.conductor import scheduler
    from core.conductor.graph import ConductorGraph
    from core.conductor.node import ConductorNode, NodeLifecycle, NodeOutcome
    from core.conductor.planner import ConductorPlan
    ticks = [100.0]
    monkeypatch.setattr(clock.time, 'monotonic', lambda: ticks[0])
    operations = []
    def run_one(node, ctx, **kwargs):
        operations.append('completed effect')
        with clock.paused():
            ticks[0] += 3600
        operations.append('next unsent call')
        return NodeOutcome(node=node, state=NodeLifecycle.SUCCEEDED, result={})
    monkeypatch.setattr(scheduler, '_run_one', run_one)
    plan = ConductorPlan('pause', 'task', ConductorGraph((ConductorNode(node_id='one', operation='calculation', request_text='1+1'),)))
    with clock.task_clock():
        result = scheduler.run_conductor_plan(plan, plan_deadline_s=5)
    assert result[0].state == NodeLifecycle.SUCCEEDED
    assert operations == ['completed effect','next unsent call']
