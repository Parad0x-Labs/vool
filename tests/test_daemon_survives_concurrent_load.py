"""The daemon must stay answerable, and honest, when several turns arrive at once.

Two measured failures, one afternoon, on the installed daemon (2026-07-31).

**1. Liveness queued behind work, and the supervisor killed a healthy server.**
Every request went through `starlette.concurrency.run_in_threadpool`, which shares anyio's DEFAULT
thread limiter -- 40 tokens for the whole process -- and a buffered `stream:false` chat turn holds
its token for the entire turn. Driving N concurrent `POST /api/chat` while probing `/healthz` the way
`Start_VOOL.sh` does (`curl -sf --max-time 2`, every 5s, three strikes and SIGTERM):

    N=20 -> 0 failed probes
    N=40 -> 1 failed probe
    N=48 -> probes 2,3,4,5,6 ALL hit the 2.0s cap; ~30s with no answer at all

The installed app runs that supervisor (`VOOL_LAUNCHD_SUPERVISOR=1`, set by the launchd unit in
installer/install_vool.sh), so at N=48 the third strike lands on probe 4 and a working daemon is
terminated mid-batch. That is the reported "it crashed and I lost the whole test batch" -- there is
no crash and no traceback, because the process was killed from outside.

**2. Half the turns died at ~60s having never been generated for.**
Nothing limited how many local generations were dispatched at once, so all of them went to Ollama
together and queued THERE, where the daemon cannot see them. Meanwhile each one's read timeout, which
`core/memory_first_router.py` clips to the remaining 60s ordinary-lane budget, ran from the moment it
was sent:

    N=24 -> 7 of 24 answered "I couldn't get a usable model response in this run" at ~64s
    N=32 -> 14 of 32, same

with `HTTPConnectionPool(... port=11434): Read timed out. (read timeout=59.99)` in every ledger.

These tests drive both mechanisms rather than asserting the shape of the source, because both bugs
were invisible in the source and only showed up as timing.
"""
from __future__ import annotations

import threading
import time

import pytest

from core import local_model_admission
from core.local_model_admission import LocalModelLaneSaturatedError, local_model_slot
from core.web.api import app as api_app
from core.web.api.service import ApiResponse, json_response

# --------------------------------------------------------------------------------------
# 1. Liveness has its own capacity and cannot be starved by chat
# --------------------------------------------------------------------------------------


def test_liveness_and_work_do_not_share_a_limiter() -> None:
    """One shared limiter is the whole bug: chat holds every token and health gets none."""

    assert api_app._HEALTH_LIMITER is not api_app._WORK_LIMITER
    # Work must stay strictly under anyio's process-wide default (40) so a saturated chat lane
    # cannot exhaust the pool that everything else in the process also draws from.
    assert api_app._WORK_LIMITER.total_tokens < 40
    assert api_app._HEALTH_LIMITER.total_tokens >= 1


@pytest.mark.parametrize("path", ["/healthz", "/v1/healthz", "/healthz/", "/readyz"])
def test_the_supervisors_probe_path_is_recognised_as_liveness(path: str) -> None:
    """`Start_VOOL.sh` probes `/healthz`. If this misses it, the fix protects nothing."""

    assert api_app._is_liveness_path(path) is True


@pytest.mark.parametrize("path", ["/api/chat", "/v1/chat/completions", "/api/tags"])
def test_real_work_is_not_treated_as_liveness(path: str) -> None:
    assert api_app._is_liveness_path(path) is False


def test_healthz_answers_while_more_chat_turns_are_in_flight_than_the_work_lane_holds() -> None:
    """The measured failure, reproduced in-process and then required not to happen.

    `_WORK_LIMITER.total_tokens + 8` chat turns are parked inside the dispatcher, which is more than
    the work lane can run at once and (with the old single-limiter code) more than enough to leave
    `/healthz` with no thread. The probe then gets the same 2s budget the shipped supervisor gives
    it. Answering inside that budget is the entire difference between a live daemon and a SIGTERM.
    """
    import anyio
    import httpx

    from core.web.api.app import create_api_app

    parked = threading.Event()
    entered = threading.Semaphore(0)

    def blocking_post(**kwargs) -> ApiResponse:
        entered.release()
        parked.wait(30)
        return json_response(200, {"ok": True})

    def fast_get(**kwargs) -> ApiResponse:
        return json_response(200, {"ok": True, "healthz": True})

    app = create_api_app(
        runtime=None,
        model_name="test",
        get_dispatcher=fast_get,
        post_dispatcher=blocking_post,
    )
    turns = api_app._WORK_LIMITER.total_tokens + 8
    probe_latency: list[float] = []
    probe_status: list[int] = []

    async def drive() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            async with anyio.create_task_group() as chat:
                for _ in range(turns):
                    chat.start_soon(_post, client)
                # Wait until the work lane is genuinely full before probing.
                await anyio.to_thread.run_sync(_wait_for_saturation, entered)
                started = time.perf_counter()
                probe = await client.get("/healthz", timeout=2.0)
                probe_latency.append(time.perf_counter() - started)
                probe_status.append(probe.status_code)
                parked.set()

    async def _post(client) -> None:
        await client.post("/api/chat", json={"messages": []}, timeout=60.0)

    try:
        anyio.run(drive)
    finally:
        parked.set()

    assert probe_status == [200]
    assert probe_latency[0] < 2.0, (
        f"/healthz took {probe_latency[0]:.2f}s with {turns} chat turns in flight. The shipped "
        "supervisor gives it 2.0s and SIGTERMs the daemon after three misses."
    )


def _wait_for_saturation(entered: threading.Semaphore) -> None:
    """Block until the work lane is full, so the probe is not measured against an idle server."""
    for _ in range(api_app._WORK_LIMITER.total_tokens):
        assert entered.acquire(timeout=10), "chat turns never reached the dispatcher"


# --------------------------------------------------------------------------------------
# 2. Local generations are admitted, not dumped on Ollama all at once
# --------------------------------------------------------------------------------------


def test_admission_never_lets_more_than_the_ceiling_run_at_once(monkeypatch) -> None:
    monkeypatch.setenv("VOOL_LOCAL_MODEL_CONCURRENCY", "3")
    monkeypatch.setenv("VOOL_LOCAL_MODEL_QUEUE_WAIT", "30")

    live = 0
    peak = 0
    lock = threading.Lock()

    def one() -> None:
        nonlocal live, peak
        with local_model_slot(provider_id="test"):
            with lock:
                live += 1
                peak = max(peak, live)
            time.sleep(0.05)
            with lock:
                live -= 1

    threads = [threading.Thread(target=one) for _ in range(24)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert peak <= 3, f"{peak} local generations ran at once against a ceiling of 3"
    assert peak >= 2, "the gate serialised everything; Ollama batches and this throws that away"
    assert local_model_admission.lane_snapshot()["in_flight"] == 0


def test_a_queue_that_never_drains_is_named_saturation_not_a_timeout(monkeypatch) -> None:
    """The operator must never be handed a read timeout to interpret when the cause is our queue."""

    monkeypatch.setenv("VOOL_LOCAL_MODEL_CONCURRENCY", "1")
    monkeypatch.setenv("VOOL_LOCAL_MODEL_QUEUE_WAIT", "30")

    holding = threading.Event()
    release = threading.Event()

    def hold() -> None:
        with local_model_slot(provider_id="held"):
            holding.set()
            release.wait(30)

    holder = threading.Thread(target=hold)
    holder.start()
    try:
        assert holding.wait(10)
        with pytest.raises(LocalModelLaneSaturatedError) as excinfo:
            with local_model_slot(provider_id="ollama-local:qwen3:8b", timeout_seconds=0.2):
                pass
    finally:
        release.set()
        holder.join(timeout=10)

    message = str(excinfo.value)
    assert "saturated" in message
    assert "ollama-local:qwen3:8b" in message
    assert local_model_admission.lane_snapshot()["in_flight"] == 0


def test_the_ollama_chat_lane_actually_holds_a_slot(monkeypatch) -> None:
    """A gate nothing calls is not a gate. This drives the real adapter method.

    `requests.post` is replaced with a probe that records how many callers are inside it at once, so
    a sabotage that removes the `with local_model_slot(...)` from the adapter shows up as 8 concurrent
    posts against a ceiling of 2 -- which is precisely the shape of the live failure.
    """
    monkeypatch.setenv("VOOL_LOCAL_MODEL_CONCURRENCY", "2")
    monkeypatch.setenv("VOOL_LOCAL_MODEL_QUEUE_WAIT", "30")

    from adapters import openai_compatible_adapter as adapter_module
    from adapters.base_adapter import ModelRequest
    from storage.model_provider_manifest import ModelProviderManifest

    live = 0
    peak = 0
    lock = threading.Lock()

    class _Resp:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"message": {"content": "ok"}, "done": True}

    def fake_post(*args, **kwargs):
        nonlocal live, peak
        with lock:
            live += 1
            peak = max(peak, live)
        time.sleep(0.05)
        with lock:
            live -= 1
        return _Resp()

    monkeypatch.setattr(adapter_module.requests, "post", fake_post)

    manifest = ModelProviderManifest(
        provider_name="ollama-local",
        model_name="qwen3:8b",
        source_type="http",
        adapter_type="openai_compatible",
        license_name="Apache-2.0",
        license_reference="https://ollama.com/library/qwen3",
        weight_location="external",
        runtime_dependency="ollama",
        capabilities=["summarize", "structured_json"],
        runtime_config={"base_url": "http://127.0.0.1:11434", "timeout_seconds": 30},
    )
    adapter = adapter_module.OpenAICompatibleAdapter(manifest)
    monkeypatch.setattr(adapter, "_gate_local_model_load", lambda: None)

    def call() -> None:
        adapter._invoke_ollama_chat(
            ModelRequest(task_kind="chat", prompt="hi", messages=[{"role": "user", "content": "hi"}]),
            force_json=False,
        )

    threads = [threading.Thread(target=call) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert peak <= 2, (
        f"{peak} concurrent POSTs reached Ollama against a ceiling of 2 — the adapter is not "
        "holding an admission slot, so every turn queues inside Ollama and burns its own timeout"
    )
