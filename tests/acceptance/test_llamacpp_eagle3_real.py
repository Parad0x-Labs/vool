"""
Live acceptance tests for the native llama-server.

Tests are automatically skipped when the server is not running at 127.0.0.1:8090.
Start the server: /tmp/start-vool-llamacpp.sh

Hardware context: Apple M4 iMac (24 GB, 120 GB/s bandwidth).
  - 4.9 GB model (qwen3:8b Q4_K_M) → theoretical peak ~24.5 t/s decode
  - flash-attn ON, KV q8_0, parallel 4 slots, --reasoning off (nothink)
  - Real measured decode: 18-20 t/s single, ~820ms TTFT (14B was ~2000ms)

EAGLE-3 infrastructure: draft model still present at ~/.vool_local/models/qwen3_14b_eagle3_q8.gguf
  - Current server (8B) does not use EAGLE-3 (no draft for qwen3:8b arch)
  - Routing keeps qwen3:14b in Ollama for deep-lane tasks that need more capacity
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import pytest
import requests

_SERVER_BASE = "http://127.0.0.1:8090"
_MODEL = "qwen3:8b-gguf"

_DRAFT_GGUF = os.path.expanduser("~/.vool_local/models/qwen3_14b_eagle3_q8.gguf")
_START_SCRIPT = "/tmp/start-vool-llamacpp.sh"
_VOOL_HOME = str(Path.home())

_LOCAL_SETUP = pytest.mark.skipif(
    not os.path.exists(_DRAFT_GGUF),
    reason="requires local llama.cpp setup with eagle3 draft model at ~/.vool_local/",
)

# M4 + qwen3:8b Q4_K_M: 120 GB/s ÷ 4.9 GB = 24.5 t/s theoretical max decode
# Conservative gate: 10 t/s (real measured: 18-20 t/s single)
_MIN_DECODE_TPS = 10.0
# First-token gate: 8B is much faster than 14B was
_MAX_WARMUP_SECONDS = 4.0


def _server_healthy() -> bool:
    try:
        r = requests.get(f"{_SERVER_BASE}/health", timeout=1.5)
        return r.status_code == 200 and r.json().get("status") == "ok"
    except Exception:
        return False


_LIVE = pytest.mark.skipif(not _server_healthy(), reason="llama-server not running at 127.0.0.1:8090")


def _chat(prompt: str, max_tokens: int = 32) -> dict[str, Any]:
    r = requests.post(
        f"{_SERVER_BASE}/v1/chat/completions",
        json={
            "model": _MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
            "max_tokens": max_tokens,
            "stream": False,
        },
        timeout=90,
    )
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------------------
# Non-live structural contracts (always run)
# ---------------------------------------------------------------------------

@_LOCAL_SETUP
def test_eagle3_draft_model_file_exists_and_is_valid_size() -> None:
    draft = os.path.expanduser("~/.vool_local/models/qwen3_14b_eagle3_q8.gguf")
    assert os.path.exists(draft), f"EAGLE-3 draft GGUF missing at {draft}"
    size_mb = os.path.getsize(draft) / 1024 / 1024
    assert 400 < size_mb < 1000, f"unexpected draft size {size_mb:.0f} MB (expected 400-1000 MB)"


@pytest.mark.skipif(
    not os.path.exists(_START_SCRIPT),
    reason="requires llama.cpp start script at /tmp/start-vool-llamacpp.sh",
)
def test_llamacpp_start_script_references_8b_model() -> None:
    with open("/tmp/start-vool-llamacpp.sh") as f:
        content = f.read()
    assert "qwen3:8b-gguf" in content or "8b" in content.lower(), "start script must reference qwen3:8b model"
    assert "--parallel 4" in content, "start script must use --parallel 4"
    assert "--reasoning off" in content, "start script must disable reasoning (nothink mode)"


@_LOCAL_SETUP
def test_llamacpp_env_config_declares_8b_model() -> None:
    import subprocess
    result = subprocess.run(
        ["grep", "VOOL_LLAMACPP_MODEL=", ".vool_local/config/provider-env.sh"],
        capture_output=True, text=True,
        cwd=_VOOL_HOME,
    )
    assert "qwen3:8b-gguf" in result.stdout, f"provider-env.sh must declare qwen3:8b-gguf as model, got: {result.stdout!r}"


@_LOCAL_SETUP
def test_eagle3_acceleration_truth_detects_configured_status() -> None:
    from core.backend_acceleration_truth import backend_acceleration_proof
    proof = backend_acceleration_proof(
        backend="llama.cpp",
        env={
            "VOOL_LLAMACPP_SPEC_TYPE": "draft-eagle3",
            "VOOL_LLAMACPP_DRAFT_MODEL": os.path.expanduser("~/.vool_local/models/qwen3_14b_eagle3_q8.gguf"),
            "LLAMACPP_BASE_URL": "http://127.0.0.1:8090/v1",
            "VOOL_LLAMACPP_CACHE": "1",
        },
        probe=False,
    )
    assert proof.eagle_status in {"active", "configured_not_proven"}, (
        f"eagle_status should be active or configured_not_proven, got {proof.eagle_status!r}"
    )
    assert proof.eagle_proof["spec_type"] == "draft-eagle3"
    assert proof.eagle_proof["draft_model_exists"] is True


def test_eagle3_autopilot_receives_eagle3_active_flag_in_router() -> None:
    import inspect

    from core.local_inference_autopilot import build_local_inference_autopilot_plan
    sig = inspect.signature(build_local_inference_autopilot_plan)
    assert "eagle3_active" in sig.parameters, (
        "build_local_inference_autopilot_plan must accept eagle3_active parameter"
    )
    assert sig.parameters["eagle3_active"].default is False, (
        "eagle3_active must default to False for backwards compat"
    )


def test_eagle3_autopilot_boosts_llamacpp_score_when_active() -> None:
    from core.local_inference_autopilot import build_local_inference_autopilot_plan
    from core.provider_routing import ProviderCapabilityTruth

    capabilities = [
        ProviderCapabilityTruth(
            provider_id="llamacpp-local:qwen3:8b-gguf",
            model_id="qwen3:8b-gguf",
            role_fit="queen",
            context_window=8192,
            tool_support=("structured_json", "code_complex"),
            structured_output_support=True,
            tokens_per_second=18.0,
            ram_budget_gb=6.0,
            vram_budget_gb=0.0,
            quantization="Q4_K_M",
            locality="local",
            privacy_class="local_private",
            queue_depth=0,
            max_safe_concurrency=4,
        ),
        ProviderCapabilityTruth(
            provider_id="ollama-local:vool-qwen3-30b-a3b:nothink",
            model_id="vool-qwen3-30b-a3b:nothink",
            role_fit="queen",
            context_window=32768,
            tool_support=("structured_json",),
            structured_output_support=True,
            tokens_per_second=12.0,
            ram_budget_gb=20.0,
            vram_budget_gb=0.0,
            quantization="Q4_K_M",
            locality="local",
            privacy_class="local_private",
            queue_depth=0,
            max_safe_concurrency=1,
        ),
    ]

    plan_with = build_local_inference_autopilot_plan(
        user_text="write a recursive fibonacci function",
        task_kind="coding_help_complex",
        output_mode="text",
        provider_role="queen",
        capability_truth=capabilities,
        eagle3_active=True,
    )
    plan_without = build_local_inference_autopilot_plan(
        user_text="write a recursive fibonacci function",
        task_kind="coding_help_complex",
        output_mode="text",
        provider_role="queen",
        capability_truth=capabilities,
        eagle3_active=False,
    )

    assert plan_with.selected_provider_id == "llamacpp-local:qwen3:8b-gguf", (
        f"with eagle3_active, llamacpp must win deep lane, got {plan_with.selected_provider_id!r}"
    )
    assert plan_without.selected_provider_id == "llamacpp-local:qwen3:8b-gguf", (
        "llamacpp should win deep lane regardless due to specialist bonus"
    )
    # Eagle3 flags should appear in runtime_flags when active
    flags_with = plan_with.runtime_flags
    assert flags_with.get("speculative") == "draft-eagle3", (
        f"eagle3_active must set speculative=draft-eagle3 in runtime_flags, got {flags_with}"
    )
    flags_without = plan_without.runtime_flags
    assert "speculative" not in flags_without, (
        "without eagle3_active, speculative flag must not be set"
    )


# ---------------------------------------------------------------------------
# Live: health and model
# ---------------------------------------------------------------------------

@_LIVE
def test_eagle3_server_is_healthy() -> None:
    r = requests.get(f"{_SERVER_BASE}/health", timeout=3)
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


@_LIVE
def test_eagle3_server_exposes_correct_model() -> None:
    r = requests.get(f"{_SERVER_BASE}/v1/models", timeout=3)
    r.raise_for_status()
    payload = r.json()
    ids = [m.get("id") or m.get("name") for m in payload.get("data") or payload.get("models") or []]
    assert _MODEL in ids, f"expected {_MODEL!r} in {ids}"


# ---------------------------------------------------------------------------
# Live: throughput
# ---------------------------------------------------------------------------

@_LIVE
def test_eagle3_first_response_completes_within_time_gate() -> None:
    start = time.perf_counter()
    payload = _chat("ready", max_tokens=8)
    elapsed = time.perf_counter() - start
    assert elapsed < _MAX_WARMUP_SECONDS, (
        f"first response took {elapsed:.2f}s — exceeds gate of {_MAX_WARMUP_SECONDS}s"
    )
    usage = payload.get("usage") or {}
    assert int(usage.get("completion_tokens") or 0) > 0, "server returned 0 completion tokens"


@_LIVE
def test_decode_throughput_meets_minimum() -> None:
    _chat("ready", max_tokens=8)  # warmup

    prompt = "List the numbers from one to ten, one per line"
    start = time.perf_counter()
    payload = _chat(prompt, max_tokens=64)
    elapsed = time.perf_counter() - start

    usage = payload.get("usage") or {}
    ct = int(usage.get("completion_tokens") or 0)
    if ct == 0:
        content = (payload.get("choices") or [{}])[0].get("message", {}).get("content", "")
        ct = max(1, len(content.split()))

    tps = ct / max(elapsed, 0.001)
    print(f"\n  qwen3:8b decode: {ct} tokens in {elapsed:.2f}s = {tps:.1f} t/s  "
          f"(M4 theoretical max ~24.5 t/s, gate={_MIN_DECODE_TPS} t/s)")

    assert tps >= _MIN_DECODE_TPS, (
        f"throughput {tps:.1f} t/s below gate {_MIN_DECODE_TPS} t/s — "
        f"server may be misconfigured or GPU offload failing"
    )


@_LIVE
def test_multi_request_throughput_is_stable() -> None:
    _chat("ready", max_tokens=8)  # warmup

    prompts = [
        "Name three primary colors",
        "What language is Python written in?",
        "Capital of Germany?",
    ]
    rates: list[float] = []
    for prompt in prompts:
        start = time.perf_counter()
        payload = _chat(prompt, max_tokens=32)
        elapsed = time.perf_counter() - start
        ct = int((payload.get("usage") or {}).get("completion_tokens") or 1)
        rates.append(ct / max(elapsed, 0.001))

    avg = sum(rates) / len(rates)
    print(f"\n  qwen3:8b multi-shot: {[f'{r:.1f}' for r in rates]} t/s, avg={avg:.1f} t/s")

    assert avg >= _MIN_DECODE_TPS, f"avg {avg:.1f} t/s below gate {_MIN_DECODE_TPS} t/s"
    assert all(r >= _MIN_DECODE_TPS * 0.5 for r in rates), (
        f"some shots far below gate: {[f'{r:.1f}' for r in rates]}"
    )
