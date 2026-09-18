#!/usr/bin/env python3
"""
VOOL vs Standard Ollama — Official Benchmark Suite
M4 Apple Silicon 24 GB unified memory — coding + web-dev focus

Providers:
  ollama-8b    — Ollama qwen3:8b          (out-of-box baseline, port 11434)
  ollama-14b   — Ollama qwen3:14b         (out-of-box baseline, port 11434)
  native-8b    — Native llama-server 8B   (our fast lane, port 8090)
  native-14b   — Native llama-server 14B  (our deep lane, port 8091)

Benchmark categories:
  1. DECODE SPEED   — tokens/second + time-to-first-token
  2. CONCURRENCY    — 4 parallel slots advantage
  3. CODE QUALITY   — 10 hard problems: algorithms + data structures (executed)
  4. DEBUGGING      — 5 broken-code fix problems (executed)
  5. WEB PATTERNS   — 5 HTML/CSS/JS generation problems (pattern match)

Scoring weights TTFT at 35% — this is the latency users feel in OpenClaw/Cursor.
"""
from __future__ import annotations

import concurrent.futures
import contextlib
import json
import re
import subprocess
import time
import urllib.request
from dataclasses import dataclass

# ── Provider config ─────────────────────────────────────────────────────────

PROVIDERS = {
    "ollama-8b-thinking": {
        "base_url": "http://127.0.0.1:11434",
        "model": "qwen3:8b",
        "label": "Ollama qwen3:8b   (OpenClaw default)",
        "parallel_slots": 1,
        "api_style": "ollama-thinking",
    },
    "ollama-8b": {
        "base_url": "http://127.0.0.1:11434",
        "model": "qwen3:8b",
        "label": "Ollama qwen3:8b   (think:false)",
        "parallel_slots": 1,
        "api_style": "ollama",
    },
    "ollama-14b": {
        "base_url": "http://127.0.0.1:11434",
        "model": "qwen3:14b",
        "label": "Ollama qwen3:14b  (think:false)",
        "parallel_slots": 1,
        "api_style": "ollama",
    },
    "ollama-30b-moe": {
        "base_url": "http://127.0.0.1:11434",
        "model": "vool-qwen3-30b-a3b:nothink",
        "label": "Ollama 30B-MoE     (3B active, nothink)",
        "parallel_slots": 1,
        "api_style": "ollama",
    },
    "ollama-35b-moe": {
        "base_url": "http://127.0.0.1:11434",
        "model": "qwen3.5:35b-a3b",
        "label": "Ollama 35B-MoE     (3.3B active, hybrid-attn)",
        "parallel_slots": 1,
        "api_style": "ollama",
    },
    "native-8b": {
        "base_url": "http://127.0.0.1:8090",
        "model": "qwen3:8b-gguf",
        "label": "Native 8B  flash+kv-q8+4slots",
        "parallel_slots": 4,
        "api_style": "openai",
    },
    "native-14b": {
        "base_url": "http://127.0.0.1:8091",
        "model": "qwen3:14b-gguf",
        "label": "Native 14B flash+kv-q8",
        "parallel_slots": 1,
        "api_style": "openai",
    },
    # ── MLX providers — Apple's own Metal-optimised framework ─────────────
    "mlx-8b": {
        "base_url": "http://127.0.0.1:8095",
        "model": "mlx-community/Qwen3-8B-4bit-AWQ",
        "label": "MLX 8B   (4bit-AWQ, Apple framework)",
        "parallel_slots": 1,
        "api_style": "openai",
        "solo_gpu": True,
        "start_script": "/tmp/start-vool-mlx-8b.sh",
        "screen_name": "vool-mlx-8b",
        "nothink_prefix": "/nothink\n\n",
    },
    "mlx-coder-30b": {
        "base_url": "http://127.0.0.1:8096",
        "model": "mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit",
        "label": "MLX Coder-30B (4bit, 3.3B active, coding-specialized)",
        "parallel_slots": 1,
        "api_style": "openai",
        "solo_gpu": True,
        "start_script": "/tmp/start-vool-mlx-coder30b.sh",
        "screen_name": "vool-mlx-coder30b",
        "nothink_prefix": "/nothink\n\n",
    },
    # ── Experimental solo providers (stop dual-server, full Metal headroom) ──
    "native-8b-eagle3": {
        "base_url": "http://127.0.0.1:8092",
        "model": "qwen3:8b-eagle3",
        "label": "Native 8B  EAGLE-3 speculative",
        "parallel_slots": 2,
        "api_style": "openai",
        "solo_gpu": True,
        "start_script": "/tmp/start-vool-llamacpp-eagle3.sh",
        "screen_name": "vool-llamacpp-eagle3",
    },
    "native-8b-kv4": {
        "base_url": "http://127.0.0.1:8093",
        "model": "qwen3:8b-kv4",
        "label": "Native 8B  kv-q4/q4 cache",
        "parallel_slots": 4,
        "api_style": "openai",
        "solo_gpu": True,
        "start_script": "/tmp/start-vool-llamacpp-kv4.sh",
        "screen_name": "vool-llamacpp-kv4",
    },
    "native-8b-kv-mixed": {
        "base_url": "http://127.0.0.1:8094",
        "model": "qwen3:8b-kv-mixed",
        "label": "Native 8B  kv-q8K/q4V mixed",
        "parallel_slots": 4,
        "api_style": "openai",
        "solo_gpu": True,
        "start_script": "/tmp/start-vool-llamacpp-kv-mixed.sh",
        "screen_name": "vool-llamacpp-kv-mixed",
    },
}


# ── GPU resource management ──────────────────────────────────────────────────

NATIVE_PORTS = [8090, 8091]
NATIVE_START_SCRIPTS = ["/tmp/start-vool-llamacpp.sh", "/tmp/start-vool-llamacpp-deep.sh"]
NATIVE_SCREEN_NAMES = ["vool-llamacpp", "vool-llamacpp-deep"]


def _port_pid(port: int) -> int | None:
    try:
        out = subprocess.check_output(
            ["lsof", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"], stderr=subprocess.DEVNULL
        ).decode().strip()
        return int(out.split()[0]) if out else None
    except Exception:
        return None


def stop_native_servers() -> list[int]:
    killed = []
    for port in NATIVE_PORTS:
        pid = _port_pid(port)
        if pid:
            subprocess.run(["kill", "-9", str(pid)], stderr=subprocess.DEVNULL)
            killed.append(pid)
    if killed:
        # Wait for processes to actually exit and release Metal GPU resources
        deadline = time.time() + 12
        for pid in killed:
            while time.time() < deadline:
                r = subprocess.run(["kill", "-0", str(pid)], stderr=subprocess.DEVNULL)
                if r.returncode != 0:
                    break
                time.sleep(0.5)
        time.sleep(4)  # extra Metal GPU cooldown
    return killed


def _wait_server_healthy(port: int, deadline: float) -> bool:
    while time.time() < deadline:
        if _port_pid(port):
            try:
                req = urllib.request.Request(f"http://127.0.0.1:{port}/health")
                with urllib.request.urlopen(req, timeout=5) as r:
                    if r.status == 200:
                        return True
            except Exception:
                pass
        time.sleep(2)
    return False


def _infer_warmup(port: int, model: str) -> bool:
    """Probe actual Metal inference — /health passes before GPU kernel is ready."""
    payload = {
        "model": model, "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 3, "stream": False, "temperature": 0.1,
    }
    try:
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/chat/completions",
            data=data, headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            resp = json.loads(r.read().decode())
            return "choices" in resp
    except Exception:
        return False


def _ollama_evict_loaded(base_url: str = "http://127.0.0.1:11434") -> None:
    """Evict only currently loaded Ollama models before starting native servers.
    Uses /api/ps to check what's actually in GPU memory — avoids load+unload of
    models that aren't loaded, which would add memory pressure, not reduce it."""
    try:
        req = urllib.request.Request(f"{base_url}/api/ps")
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read().decode())
        loaded = [m["model"] for m in data.get("models", [])]
        if loaded:
            print(f"  [GPU] Evicting Ollama models from GPU: {loaded}")
        for model in loaded:
            _ollama_unload(base_url, model)
        if loaded:
            time.sleep(2)
    except Exception:
        pass


def start_native_servers() -> None:
    # Evict any Ollama models from GPU first — combined memory of both llama-servers
    # + an Ollama model exceeds 24 GB unified memory, causing kernel panics.
    _ollama_evict_loaded()
    # Kill any zombie screen sessions before starting fresh
    for name in NATIVE_SCREEN_NAMES:
        subprocess.run(["screen", "-S", name, "-X", "quit"], stderr=subprocess.DEVNULL)
    time.sleep(1)
    for i, script in enumerate(NATIVE_START_SCRIPTS):
        name = NATIVE_SCREEN_NAMES[i]
        subprocess.run(["screen", "-dmS", name, script], stderr=subprocess.DEVNULL)
    deadline = time.time() + 90
    for port in NATIVE_PORTS:
        ok = _wait_server_healthy(port, deadline)
        if not ok:
            print(f"  [WARN] port {port} did not become healthy in time")
    time.sleep(3)
    # Verify Metal inference works — GPU kernel initializes lazily after /health
    port_model = [(8090, "qwen3:8b-gguf"), (8091, "qwen3:14b-gguf")]
    for port, model in port_model:
        for attempt in range(4):
            if _infer_warmup(port, model):
                break
            wait = 6 * (attempt + 1)
            print(f"  [GPU] port {port} inference warmup failed (attempt {attempt+1}), retrying in {wait}s...")
            time.sleep(wait)


def restart_native_servers(label: str = "") -> None:
    tag = f" ({label})" if label else ""
    print(f"\n[GPU] Restarting native servers{tag} for clean Metal state...")
    stop_native_servers()
    start_native_servers()
    print("[GPU] Native servers ready.")


def start_solo_server(pid: str) -> bool:
    """Start a single experimental server, wait for it to be healthy."""
    # Evict Ollama models before starting — MLX + Ollama both claim unified memory
    _ollama_evict_loaded()
    cfg = PROVIDERS[pid]
    script = cfg.get("start_script")
    name = cfg.get("screen_name")
    if not script or not name:
        return False
    port = int(cfg["base_url"].rsplit(":", 1)[-1])
    subprocess.run(["screen", "-dmS", name, script], stderr=subprocess.DEVNULL)
    ok = _wait_server_healthy(port, time.time() + 90)
    if ok:
        time.sleep(2)
    return ok


def stop_solo_server(pid: str) -> None:
    cfg = PROVIDERS[pid]
    port = int(cfg["base_url"].rsplit(":", 1)[-1])
    proc = _port_pid(port)
    if proc:
        subprocess.run(["kill", str(proc)], stderr=subprocess.DEVNULL)
        time.sleep(3)


# ── HTTP helpers ─────────────────────────────────────────────────────────────

def _post(url: str, payload: dict, timeout: int = 180) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _openai_stream_ttft(base_url: str, model: str, messages: list, max_tokens: int) -> tuple[float, float, int]:
    payload = {"model": model, "messages": messages, "max_tokens": max_tokens, "stream": True, "temperature": 0.1}
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{base_url}/v1/chat/completions",
        data=data, headers={"Content-Type": "application/json"}, method="POST",
    )
    t0 = time.perf_counter()
    ttft_ms = -1.0
    token_count = 0
    with urllib.request.urlopen(req, timeout=180) as resp:
        for raw_line in resp:
            line = raw_line.decode("utf-8").strip()
            if not line.startswith("data:"):
                continue
            chunk = line[5:].strip()
            if chunk == "[DONE]":
                break
            try:
                obj = json.loads(chunk)
            except Exception:
                continue
            delta = obj.get("choices", [{}])[0].get("delta", {})
            content = delta.get("content") or ""
            if content and ttft_ms < 0:
                ttft_ms = (time.perf_counter() - t0) * 1000
            if content:
                token_count += 1
    total_ms = (time.perf_counter() - t0) * 1000
    return ttft_ms, total_ms, token_count


def _ollama_thinking_ttft(base_url: str, model: str, messages: list, max_tokens: int) -> tuple[float, float, int]:
    payload = {
        "model": model, "messages": messages, "stream": True,
        "options": {"num_predict": max_tokens + 2000, "temperature": 0.1},
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{base_url}/api/chat", data=data, headers={"Content-Type": "application/json"}, method="POST",
    )
    t0 = time.perf_counter()
    ttft_ms = -1.0
    token_count = 0
    with urllib.request.urlopen(req, timeout=300) as resp:
        for raw_line in resp:
            try:
                obj = json.loads(raw_line.decode("utf-8").strip())
            except Exception:
                continue
            content = obj.get("message", {}).get("content") or ""
            thinking = obj.get("message", {}).get("thinking") or ""
            if content and not thinking and ttft_ms < 0:
                ttft_ms = (time.perf_counter() - t0) * 1000
            if content and not thinking:
                token_count += 1
            if obj.get("done"):
                break
    total_ms = (time.perf_counter() - t0) * 1000
    return ttft_ms, total_ms, token_count


def _ollama_thinking_completion(base_url: str, model: str, messages: list, max_tokens: int) -> tuple[str, float]:
    payload = {
        "model": model, "messages": messages, "stream": False,
        "options": {"num_predict": max_tokens + 2000, "temperature": 0.1},
    }
    t0 = time.perf_counter()
    resp = _post(f"{base_url}/api/chat", payload, timeout=300)
    elapsed = time.perf_counter() - t0
    content = resp.get("message", {}).get("content") or ""
    eval_count = int(resp.get("eval_count") or 0)
    eval_duration_ns = int(resp.get("eval_duration") or 0)
    tps = (eval_count / (eval_duration_ns / 1e9)) if eval_duration_ns > 0 else (len(content.split()) / elapsed)
    return content, tps


def _ollama_stream_ttft(base_url: str, model: str, messages: list, max_tokens: int) -> tuple[float, float, int]:
    payload = {
        "model": model, "messages": messages, "think": False, "stream": True,
        "options": {"num_predict": max_tokens, "temperature": 0.1},
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{base_url}/api/chat", data=data, headers={"Content-Type": "application/json"}, method="POST",
    )
    t0 = time.perf_counter()
    ttft_ms = -1.0
    token_count = 0
    with urllib.request.urlopen(req, timeout=180) as resp:
        for raw_line in resp:
            try:
                obj = json.loads(raw_line.decode("utf-8").strip())
            except Exception:
                continue
            content = obj.get("message", {}).get("content") or ""
            if content and ttft_ms < 0:
                ttft_ms = (time.perf_counter() - t0) * 1000
            if content:
                token_count += 1
            if obj.get("done"):
                break
    total_ms = (time.perf_counter() - t0) * 1000
    return ttft_ms, total_ms, token_count


def _apply_nothink(pid: str, messages: list) -> list:
    prefix = PROVIDERS[pid].get("nothink_prefix")
    if not prefix:
        return messages
    out = []
    for m in messages:
        if m.get("role") == "user" and not m["content"].startswith(prefix):
            out.append({**m, "content": prefix + m["content"]})
        else:
            out.append(m)
    return out


def _stream_ttft(pid: str, messages: list, max_tokens: int) -> tuple[float, float, int]:
    cfg = PROVIDERS[pid]
    style = cfg["api_style"]
    messages = _apply_nothink(pid, messages)
    if style == "ollama":
        return _ollama_stream_ttft(cfg["base_url"], cfg["model"], messages, max_tokens)
    if style == "ollama-thinking":
        return _ollama_thinking_ttft(cfg["base_url"], cfg["model"], messages, max_tokens)
    return _openai_stream_ttft(cfg["base_url"], cfg["model"], messages, max_tokens)


def _openai_completion(base_url: str, model: str, messages: list, max_tokens: int) -> tuple[str, float]:
    payload = {"model": model, "messages": messages, "max_tokens": max_tokens, "stream": False, "temperature": 0.1}
    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            t0 = time.perf_counter()
            resp = _post(f"{base_url}/v1/chat/completions", payload, timeout=180)
            elapsed = time.perf_counter() - t0
            if "error" in resp:
                msg = resp["error"].get("message", str(resp["error"]))
                if attempt == 0 and ("500" in msg or "Compute error" in msg or "GGML_ASSERT" in msg):
                    time.sleep(3)
                    continue
                raise RuntimeError(msg)
            content = resp["choices"][0]["message"]["content"] or ""
            timings = resp.get("timings") or {}
            tps = float(timings.get("predicted_per_second") or 0) or (len(content.split()) / elapsed)
            return content, tps
        except urllib.error.HTTPError as e:
            if attempt == 0 and e.code == 500:
                last_exc = e
                time.sleep(3)
                continue
            raise
        except Exception as e:
            last_exc = e
            if attempt == 0:
                time.sleep(3)
                continue
            raise
    raise last_exc  # type: ignore


def _ollama_completion(base_url: str, model: str, messages: list, max_tokens: int) -> tuple[str, float]:
    payload = {
        "model": model, "messages": messages, "think": False, "stream": False,
        "options": {"num_predict": max_tokens, "temperature": 0.1},
    }
    t0 = time.perf_counter()
    resp = _post(f"{base_url}/api/chat", payload, timeout=180)
    elapsed = time.perf_counter() - t0
    content = resp.get("message", {}).get("content") or ""
    eval_count = int(resp.get("eval_count") or 0)
    eval_duration_ns = int(resp.get("eval_duration") or 0)
    tps = (eval_count / (eval_duration_ns / 1e9)) if eval_duration_ns > 0 else (len(content.split()) / elapsed)
    return content, tps


def _completion(pid: str, messages: list, max_tokens: int) -> tuple[str, float]:
    cfg = PROVIDERS[pid]
    style = cfg["api_style"]
    messages = _apply_nothink(pid, messages)
    if style == "ollama":
        return _ollama_completion(cfg["base_url"], cfg["model"], messages, max_tokens)
    if style == "ollama-thinking":
        return _ollama_thinking_completion(cfg["base_url"], cfg["model"], messages, max_tokens)
    return _openai_completion(cfg["base_url"], cfg["model"], messages, max_tokens)


def _warmup(pid: str) -> None:
    with contextlib.suppress(Exception):
        _completion(pid, [{"role": "user", "content": "hi"}], 5)


# ── Speed benchmark ───────────────────────────────────────────────────────────

SPEED_PROMPTS = [
    {
        "name": "short",
        "messages": [{"role": "user", "content": "Explain recursion in 3 sentences."}],
        "max_tokens": 80,
    },
    {
        "name": "medium",
        "messages": [{"role": "user", "content": "Write a Python quicksort implementation with clear variable names."}],
        "max_tokens": 200,
    },
    {
        "name": "long",
        "messages": [{"role": "user", "content": (
            "Design a rate limiter for a REST API. "
            "Cover: token bucket algorithm, Redis implementation, sliding window variant, "
            "code example, and trade-offs."
        )}],
        "max_tokens": 350,
    },
]

TTFT_PROBE = [{"role": "user", "content": "Write a Python function to check if a number is prime."}]


@dataclass
class SpeedResult:
    provider: str
    prompt_name: str
    ttft_ms: float
    tps: float
    tokens: int


def run_speed_benchmark(providers: list, runs: int = 2) -> list:
    results = []
    print("\n── SPEED: decode t/s (non-streaming) + TTFT (streaming) ──")
    for pid in providers:
        cfg = PROVIDERS[pid]
        print(f"  {cfg['label']} (warming up...)")
        _warmup(pid)

        ttft_ms = -1.0
        try:
            ttft_ms, _total_ms, _ = _stream_ttft(pid, TTFT_PROBE, 150)
        except Exception as e:
            print(f"    TTFT probe failed: {e}")

        for prompt in SPEED_PROMPTS:
            tpss = []
            for _ in range(runs):
                try:
                    _content, tps = _completion(pid, prompt["messages"], prompt["max_tokens"])
                    if tps > 0:
                        tpss.append(tps)
                except Exception as e:
                    print(f"    !! {prompt['name']}: {e}")
            if tpss:
                avg_tps = sum(tpss) / len(tpss)
                r = SpeedResult(provider=pid, prompt_name=prompt["name"], ttft_ms=ttft_ms, tps=avg_tps, tokens=0)
                results.append(r)
                ttft_str = f"TTFT {ttft_ms:.0f}ms" if ttft_ms >= 0 else "TTFT n/a"
                print(f"    {prompt['name']}: {avg_tps:.1f} t/s  {ttft_str}")
    return results


# ── Cold-start TTFT benchmark — the killer number ────────────────────────────

COLD_START_PROBE = [{"role": "user", "content": "Write a Python function to check if a number is prime."}]


@dataclass
class ColdStartResult:
    model: str
    label: str
    cold_ttft_ms: float
    warm_ttft_ms: float


def _ollama_unload(base_url: str, model: str) -> None:
    """Force Ollama to evict a model from GPU memory via keep_alive=0."""
    # Sends a minimal chat request with keep_alive=0 so Ollama unloads immediately after
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "hi"}],
        "keep_alive": 0,
        "stream": False,
        "options": {"num_predict": 1},
    }
    try:
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{base_url}/api/chat", data=data, headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            r.read()
        time.sleep(3)  # wait for eviction to complete
    except Exception:
        pass


def run_cold_start_benchmark(ollama_url: str = "http://127.0.0.1:11434") -> list:
    """Measure cold vs warm TTFT for Ollama 14B — shows the model-swap penalty."""
    results = []
    print("\n── COLD-START: Ollama 14B model-swap penalty vs always-loaded native ──")

    for model, label in [("qwen3:8b", "Ollama 8B (warm)"), ("qwen3:14b", "Ollama 14B (cold swap)")]:
        if model == "qwen3:14b":
            # Force 14B eviction: load 8B, then explicitly unload 14B
            print("  Warming 8B, evicting 14B to simulate real model-switch...")
            with contextlib.suppress(Exception):
                _ollama_completion(ollama_url, "qwen3:8b",
                                   [{"role": "user", "content": "hi"}], 5)
            _ollama_unload(ollama_url, "qwen3:14b")  # force GPU eviction
            print("  14B evicted. Measuring cold-load TTFT...")

        try:
            time.perf_counter()
            cold_ttft, _, _ = _ollama_stream_ttft(ollama_url, model, COLD_START_PROBE, 50)
            warm_ttft, _, _ = _ollama_stream_ttft(ollama_url, model, COLD_START_PROBE, 50)
            results.append(ColdStartResult(model=model, label=label,
                                           cold_ttft_ms=cold_ttft, warm_ttft_ms=warm_ttft))
            swap_s = cold_ttft / 1000
            print(f"  {label:<35} cold={swap_s:.1f}s  warm={warm_ttft:.0f}ms")
        except Exception as e:
            print(f"  {label}: ERROR {e}")

    return results


# ── Concurrency benchmark ─────────────────────────────────────────────────────

CONCURRENCY_PROMPT = [{"role": "user", "content": "Write a Python function that checks if a number is prime. Include a docstring."}]


@dataclass
class ConcurrencyResult:
    provider: str
    n_requests: int
    wall_seconds: float
    total_tokens: int
    aggregate_tps: float


def _concurrent_req(pid: str) -> int:
    try:
        content, _ = _completion(pid, CONCURRENCY_PROMPT, 150)
        return max(1, len(content.split()))
    except Exception:
        return 0


def run_concurrency_benchmark(providers: list, n_requests: int = 4) -> list:
    results = []
    print(f"\n── CONCURRENCY: {n_requests} simultaneous requests ──")
    for pid in providers:
        cfg = PROVIDERS[pid]
        print(f"  {cfg['label']} (slots={cfg['parallel_slots']})")
        _warmup(pid)
        t0 = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=n_requests) as pool:
            futs = [pool.submit(_concurrent_req, pid) for _ in range(n_requests)]
            token_counts = [f.result() for f in futs]
        wall = time.perf_counter() - t0
        total = sum(token_counts)
        agg = total / wall if wall > 0 else 0
        results.append(ConcurrencyResult(provider=pid, n_requests=n_requests,
                                         wall_seconds=wall, total_tokens=total, aggregate_tps=agg))
        print(f"    wall={wall:.1f}s  tokens≈{total}  agg={agg:.1f} t/s")
    return results


# ── Code quality benchmark ────────────────────────────────────────────────────

def _make_code_prompt(signature: str, docstring: str) -> str:
    return (
        f"Implement this Python function. "
        f"Output ONLY a single ```python code block with the complete function definition — no explanation.\n\n"
        f"Signature: {signature}\n"
        f"Docstring: {docstring}"
    )


def _make_class_prompt(class_name: str, methods: str, description: str) -> str:
    return (
        f"Implement this Python class. "
        f"Output ONLY a single ```python code block with the complete class definition — no explanation.\n\n"
        f"Class: {class_name}\n"
        f"Methods: {methods}\n"
        f"Description: {description}"
    )


CODE_PROBLEMS = [
    {
        "name": "merge_intervals",
        "kind": "function",
        "marker": "def merge_intervals",
        "max_tokens": 400,
        "prompt": _make_code_prompt(
            "def merge_intervals(intervals: list) -> list:",
            "Merge overlapping [start, end] intervals. Input may be unsorted. Return merged sorted list."
        ),
        "tests": [
            ("merge_intervals([[1,3],[2,6],[8,10],[15,18]])", [[1,6],[8,10],[15,18]]),
            ("merge_intervals([[1,4],[4,5]])", [[1,5]]),
            ("merge_intervals([[1,4]])", [[1,4]]),
        ],
    },
    {
        "name": "group_anagrams",
        "kind": "function",
        "marker": "def group_anagrams",
        "max_tokens": 400,
        "prompt": _make_code_prompt(
            "def group_anagrams(strs: list) -> list:",
            "Group strings that are anagrams of each other into sublists. Order within groups and order of groups does not matter."
        ),
        "tests": [
            ("sorted(tuple(sorted(g)) for g in group_anagrams(['eat','tea','tan','ate','nat','bat']))",
             [('ate','eat','tea'), ('bat',), ('nat','tan')]),
            ("len(group_anagrams(['a']))", 1),
        ],
    },
    {
        "name": "flatten_deep",
        "kind": "function",
        "marker": "def flatten_deep",
        "max_tokens": 350,
        "prompt": _make_code_prompt(
            "def flatten_deep(nested: list) -> list:",
            "Recursively flatten a list of arbitrary nesting depth."
        ),
        "tests": [
            ("flatten_deep([1,[2,[3,[4]]],5])", [1,2,3,4,5]),
            ("flatten_deep([[1,2],[3,[4,5]]])", [1,2,3,4,5]),
            ("flatten_deep([])", []),
        ],
    },
    {
        "name": "top_k_frequent",
        "kind": "function",
        "marker": "def top_k_frequent",
        "max_tokens": 400,
        "prompt": _make_code_prompt(
            "def top_k_frequent(nums: list, k: int) -> list:",
            "Return the k most frequently occurring elements. Order within result does not matter."
        ),
        "tests": [
            ("sorted(top_k_frequent([1,1,1,2,2,3], 2))", [1, 2]),
            ("top_k_frequent([1], 1)", [1]),
        ],
    },
    {
        "name": "decode_string",
        "kind": "function",
        "marker": "def decode_string",
        "max_tokens": 450,
        "prompt": _make_code_prompt(
            "def decode_string(s: str) -> str:",
            "Decode a string like '3[a2[c]]' → 'accaccacc'. Numbers repeat the bracketed substring. Brackets can be nested."
        ),
        "tests": [
            ("decode_string('3[a]2[bc]')", "aaabcbc"),
            ("decode_string('3[a2[c]]')", "accaccacc"),
            ("decode_string('2[abc]3[cd]ef')", "abcabccdcdcdef"),
        ],
    },
    {
        "name": "longest_consecutive",
        "kind": "function",
        "marker": "def longest_consecutive",
        "max_tokens": 400,
        "prompt": _make_code_prompt(
            "def longest_consecutive(nums: list) -> int:",
            "Return the length of the longest consecutive integer sequence in an unsorted list. O(n) time using a set."
        ),
        "tests": [
            ("longest_consecutive([100,4,200,1,3,2])", 4),
            ("longest_consecutive([0,3,7,2,5,8,4,6,0,1])", 9),
            ("longest_consecutive([])", 0),
        ],
    },
    {
        "name": "is_valid_ip",
        "kind": "function",
        "marker": "def is_valid_ip",
        "max_tokens": 350,
        "prompt": _make_code_prompt(
            "def is_valid_ip(s: str) -> bool:",
            "Return True if s is a valid IPv4 address. Each octet must be 0-255, no leading zeros, exactly 4 octets."
        ),
        "tests": [
            ("is_valid_ip('192.168.1.1')", True),
            ("is_valid_ip('256.100.0.1')", False),
            ("is_valid_ip('192.168.01.1')", False),
            ("is_valid_ip('a.b.c.d')", False),
        ],
    },
    {
        "name": "reverse_words",
        "kind": "function",
        "marker": "def reverse_words",
        "max_tokens": 300,
        "prompt": _make_code_prompt(
            "def reverse_words(s: str) -> str:",
            "Reverse the order of words in s. Remove leading/trailing whitespace and collapse multiple spaces."
        ),
        "tests": [
            ("reverse_words('  hello world  ')", "world hello"),
            ("reverse_words('a good   example')", "example good a"),
            ("reverse_words('  Bob    Loves  Alice   ')", "Alice Loves Bob"),
        ],
    },
    {
        "name": "min_stack",
        "kind": "class",
        "marker": "class MinStack",
        "max_tokens": 500,
        "prompt": _make_class_prompt(
            "MinStack",
            "__init__(self), push(self, val: int), pop(self), top(self) -> int, getMin(self) -> int",
            "Stack supporting push, pop, top, and getMin — all in O(1) time."
        ),
        "tests": [
            ("s = MinStack(); s.push(-2); s.push(0); s.push(-3)", "s.getMin()", -3),
            ("s = MinStack(); s.push(-2); s.push(0); s.push(-3); s.pop()", "s.top()", 0),
            ("s = MinStack(); s.push(-2); s.push(0); s.push(-3); s.pop()", "s.getMin()", -2),
        ],
    },
    {
        "name": "lru_cache",
        "kind": "class",
        "marker": "class LRUCache",
        "max_tokens": 550,
        "prompt": _make_class_prompt(
            "LRUCache",
            "__init__(self, capacity: int), get(self, key: int) -> int, put(self, key: int, value: int) -> None",
            "LRU Cache. get returns -1 if not found. put evicts least recently used entry when at capacity."
        ),
        "tests": [
            ("c = LRUCache(2); c.put(1, 1); c.put(2, 2)", "c.get(1)", 1),
            ("c = LRUCache(2); c.put(1, 1); c.put(2, 2); c.put(3, 3)", "c.get(2)", -1),
            ("c = LRUCache(2); c.put(1, 1); c.put(2, 2); c.get(1); c.put(3, 3)", "c.get(2)", -1),
        ],
    },
]


def _extract_code(text: str, marker: str) -> str:
    """Extract Python code block. marker is e.g. 'def foo' or 'class Foo'."""
    m = re.search(r"```python\s*\n(.*?)```", text, re.DOTALL)
    if m:
        block = m.group(1).strip()
        if marker in block:
            return block

    m = re.search(r"```\w*\s*\n(.*?)```", text, re.DOTALL)
    if m:
        block = m.group(1).strip()
        if marker in block:
            return block

    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line.strip().startswith(marker):
            block = [lines[i]]
            for ln in lines[i + 1:]:
                if ln.strip() and not ln[0].isspace() and (ln.strip().startswith("def ") or ln.strip().startswith("class ")) and not ln.strip().startswith(marker):
                    break
                block.append(ln)
            return "\n".join(block).rstrip()

    return text.strip()


def _run_tests(code: str, marker: str, tests: list) -> tuple:
    """Tests for function problems — tests are (eval_expr, expected)."""
    if marker not in code:
        return 0, len(tests)
    ns: dict = {}
    try:
        exec(compile(code, "<bench>", "exec"), ns)
    except Exception:
        return 0, len(tests)
    passed = 0
    for expr, expected in tests:
        try:
            if eval(expr, ns) == expected:
                passed += 1
        except Exception:
            pass
    return passed, len(tests)


def _run_class_tests(code: str, marker: str, tests: list) -> tuple:
    """Tests for class problems — tests are (setup_stmts, check_expr, expected)."""
    if marker not in code:
        return 0, len(tests)
    main_ns: dict = {}
    try:
        exec(compile(code, "<bench>", "exec"), main_ns)
    except Exception:
        return 0, len(tests)
    passed = 0
    for setup, check, expected in tests:
        try:
            test_ns = dict(main_ns)
            exec(compile(setup, "<bench_setup>", "exec"), test_ns)
            result = eval(check, test_ns)
            if result == expected:
                passed += 1
        except Exception:
            pass
    return passed, len(tests)


@dataclass
class CodeResult:
    provider: str
    problem: str
    passed: int
    total: int
    tps: float


def run_code_benchmark(providers: list) -> list:
    results = []
    print("\n── CODE: 10 hard problems (algorithms + data structures) ──")
    for pid in providers:
        cfg = PROVIDERS[pid]
        _warmup(pid)
        print(f"  {cfg['label']}")
        for prob in CODE_PROBLEMS:
            kind = prob.get("kind", "function")
            marker = prob["marker"]
            max_tok = prob.get("max_tokens", 400)
            msgs = [{"role": "user", "content": prob["prompt"]}]
            try:
                content, tps = _completion(pid, msgs, max_tokens=max_tok)
                code = _extract_code(content, marker)
                if kind == "class":
                    passed, total = _run_class_tests(code, marker, prob["tests"])
                else:
                    passed, total = _run_tests(code, marker, prob["tests"])
                status = "✓" if passed == total else f"{passed}/{total}"
                print(f"    {prob['name']}: {status}  ({tps:.1f} t/s)")
            except Exception as e:
                passed, total, tps = 0, len(prob["tests"]), 0.0
                print(f"    {prob['name']}: ERROR {e}")
            results.append(CodeResult(provider=pid, problem=prob["name"], passed=passed, total=total, tps=tps))
    return results


# ── Debugging benchmark ───────────────────────────────────────────────────────

def _make_debug_prompt(description: str, broken_code: str) -> str:
    return (
        f"Fix the bug in this Python function.\n"
        f"Bug hint: {description}\n\n"
        f"Broken code:\n{broken_code}\n\n"
        f"Return ONLY a single ```python code block with the corrected function definition."
    )


DEBUG_PROBLEMS = [
    {
        "name": "buggy_palindrome",
        "fn_name": "is_palindrome",
        "description": "Right pointer initialized past end of string — causes IndexError.",
        "broken_code": (
            "def is_palindrome(s: str) -> bool:\n"
            "    s = s.lower().replace(' ', '')\n"
            "    left, right = 0, len(s)  # bug: should be len(s) - 1\n"
            "    while left < right:\n"
            "        if s[left] != s[right]:\n"
            "            return False\n"
            "        left += 1\n"
            "        right -= 1\n"
            "    return True"
        ),
        "tests": [
            ("is_palindrome('racecar')", True),
            ("is_palindrome('hello')", False),
            ("is_palindrome('A man a plan a canal Panama')", True),
        ],
    },
    {
        "name": "buggy_flatten",
        "fn_name": "flatten",
        "description": "Using + instead of += — recursive results silently discarded.",
        "broken_code": (
            "def flatten(lst: list) -> list:\n"
            "    result = []\n"
            "    for item in lst:\n"
            "        if isinstance(item, list):\n"
            "            result + flatten(item)  # bug: should be result +=\n"
            "        else:\n"
            "            result.append(item)\n"
            "    return result"
        ),
        "tests": [
            ("flatten([[1,2],[3,[4,5]]])", [1,2,3,4,5]),
            ("flatten([1,2,3])", [1,2,3]),
            ("flatten([])", []),
        ],
    },
    {
        "name": "buggy_word_count",
        "fn_name": "word_count",
        "description": "Accessing dict key before it exists — KeyError on first occurrence.",
        "broken_code": (
            "def word_count(text: str) -> dict:\n"
            "    counts = {}\n"
            "    for word in text.lower().split():\n"
            "        counts[word] = counts[word] + 1  # bug: KeyError\n"
            "    return counts"
        ),
        "tests": [
            ("word_count('the quick brown fox')", {'the': 1, 'quick': 1, 'brown': 1, 'fox': 1}),
            ("word_count('hello hello world')", {'hello': 2, 'world': 1}),
            ("word_count('')", {}),
        ],
    },
    {
        "name": "buggy_max_profit",
        "fn_name": "max_profit",
        "description": "min_price initialized to 0 instead of infinity — misses buy-low opportunities.",
        "broken_code": (
            "def max_profit(prices: list) -> int:\n"
            "    min_price = 0  # bug: should be float('inf')\n"
            "    best = 0\n"
            "    for price in prices:\n"
            "        min_price = min(min_price, price)\n"
            "        best = max(best, price - min_price)\n"
            "    return best"
        ),
        "tests": [
            ("max_profit([7,1,5,3,6,4])", 5),
            ("max_profit([7,6,4,3,1])", 0),
            ("max_profit([1,2])", 1),
        ],
    },
    {
        "name": "buggy_count_bits",
        "fn_name": "count_set_bits",
        "description": "Condition is inverted — counts zero bits instead of set bits.",
        "broken_code": (
            "def count_set_bits(n: int) -> int:\n"
            "    count = 0\n"
            "    while n > 0:\n"
            "        if n % 2 == 0:  # bug: should be != 0\n"
            "            count += 1\n"
            "        n >>= 1\n"
            "    return count"
        ),
        "tests": [
            ("count_set_bits(11)", 3),   # 1011 → three 1-bits
            ("count_set_bits(0)", 0),
            ("count_set_bits(255)", 8),  # all 8 bits set
        ],
    },
]


def run_debug_benchmark(providers: list) -> list:
    results = []
    print("\n── DEBUG: 5 broken-code fix problems ──")
    for pid in providers:
        cfg = PROVIDERS[pid]
        _warmup(pid)
        print(f"  {cfg['label']}")
        for prob in DEBUG_PROBLEMS:
            marker = f"def {prob['fn_name']}"
            prompt = _make_debug_prompt(prob["description"], prob["broken_code"])
            msgs = [{"role": "user", "content": prompt}]
            try:
                content, tps = _completion(pid, msgs, max_tokens=350)
                code = _extract_code(content, marker)
                passed, total = _run_tests(code, marker, prob["tests"])
                status = "✓" if passed == total else f"{passed}/{total}"
                print(f"    {prob['name']}: {status}  ({tps:.1f} t/s)")
            except Exception as e:
                passed, total, tps = 0, len(prob["tests"]), 0.0
                print(f"    {prob['name']}: ERROR {e}")
            results.append(CodeResult(provider=pid, problem=prob["name"], passed=passed, total=total, tps=tps))
    return results


# ── Web patterns benchmark ────────────────────────────────────────────────────

WEB_PROBLEMS = [
    {
        "name": "css_flexbox_center",
        "prompt": (
            "Write CSS to horizontally and vertically center a div with class 'content' inside "
            "a div with class 'container'. The container is full viewport height. "
            "Return ONLY a ```css code block."
        ),
        "required_patterns": ["flex", "justify-content", "align-items"],
    },
    {
        "name": "js_debounce",
        "prompt": (
            "Write a JavaScript debounce(fn, delay) function that delays invoking fn until "
            "after delay ms have elapsed since the last call. "
            "Return ONLY a ```javascript code block."
        ),
        "required_patterns": ["setTimeout", "clearTimeout"],
    },
    {
        "name": "html_login_form",
        "prompt": (
            "Write HTML for a login form with an email input, a password input, and a submit button. "
            "Include proper labels, required attributes, and a form action. "
            "Return ONLY a ```html code block."
        ),
        "required_patterns": ["<form", "email", "password", "submit"],
    },
    {
        "name": "js_fetch_async",
        "prompt": (
            "Write an async JavaScript function fetchJSON(url) that fetches a URL, "
            "parses the JSON response, and returns the data. Handle errors by throwing "
            "with a descriptive message. Return ONLY a ```javascript code block."
        ),
        "required_patterns": ["async", "await", "fetch", "try"],
    },
    {
        "name": "css_fade_animation",
        "prompt": (
            "Write CSS that makes an element with class 'fade-in' animate from opacity 0 "
            "to opacity 1 over 0.5 seconds on page load. "
            "Return ONLY a ```css code block."
        ),
        "required_patterns": ["@keyframes", "opacity", "animation"],
    },
]


def _check_web_response(response: str, required_patterns: list) -> bool:
    return all(p in response for p in required_patterns)


@dataclass
class WebResult:
    provider: str
    problem: str
    passed: bool
    missing: list
    tps: float


def run_web_benchmark(providers: list) -> list:
    results = []
    print("\n── WEB: 5 HTML/CSS/JS generation problems (pattern match) ──")
    for pid in providers:
        cfg = PROVIDERS[pid]
        _warmup(pid)
        print(f"  {cfg['label']}")
        for prob in WEB_PROBLEMS:
            msgs = [{"role": "user", "content": prob["prompt"]}]
            try:
                content, tps = _completion(pid, msgs, max_tokens=300)
                missing = [p for p in prob["required_patterns"] if p not in content]
                passed = len(missing) == 0
                status = "✓" if passed else f"✗ missing: {missing}"
                print(f"    {prob['name']}: {status}  ({tps:.1f} t/s)")
            except Exception as e:
                passed, missing, tps = False, prob["required_patterns"], 0.0
                print(f"    {prob['name']}: ERROR {e}")
            results.append(WebResult(provider=pid, problem=prob["name"], passed=passed, missing=missing, tps=tps))
    return results


# ── Report ────────────────────────────────────────────────────────────────────

def _bar(val: float, max_val: float, width: int = 20) -> str:
    filled = round(val / max(max_val, 0.01) * width)
    filled = min(filled, width)
    return "█" * filled + "░" * (width - filled)


def _fmt_ttft(ms: float) -> str:
    if ms < 0:
        return "TTFT   n/a"
    if ms >= 10000:
        return f"TTFT {ms/1000:5.1f}s"
    return f"TTFT {ms:5.0f}ms"


def print_report(speed, concurrency, code, debug, web, cold=None) -> None:
    W = 72
    print()
    print("═" * W)
    print("  VOOL BENCHMARK — M4 Apple Silicon 24 GB".center(W))
    print("  native llama.cpp vs Ollama  |  coding + web-dev focus".center(W))
    print("═" * W)

    order = ["ollama-8b-thinking", "ollama-8b", "ollama-14b", "ollama-30b-moe", "ollama-35b-moe",
             "native-8b", "native-14b",
             "mlx-coder-30b", "mlx-8b", "native-8b-eagle3", "native-8b-kv4", "native-8b-kv-mixed"]
    # Extend order with any provider in results not already listed
    for pid in set(r.provider for r in speed):
        if pid not in order:
            order.append(pid)

    # Cold-start — THE headline number
    if cold:
        print("\n  ★  COLD-START TTFT — Ollama 14B model-swap vs always-loaded native")
        print("  " + "─" * (W - 2))
        for r in cold:
            r.cold_ttft_ms / max(r.warm_ttft_ms, 1)
            if r.cold_ttft_ms >= 10000:
                cold_str = f"{r.cold_ttft_ms/1000:.1f}s"
            else:
                cold_str = f"{r.cold_ttft_ms:.0f}ms"
            print(f"  {r.label:<38} cold={cold_str}  warm={r.warm_ttft_ms:.0f}ms")
        ollama_14b = next((r for r in cold if "14b" in r.model.lower()), None)
        native_14b_ttft = (
            sum(x.ttft_ms for x in speed if x.provider == "native-14b" and x.ttft_ms >= 0) /
            max(1, sum(1 for x in speed if x.provider == "native-14b" and x.ttft_ms >= 0))
        ) if any(x.provider == "native-14b" for x in speed) else None
        if ollama_14b and native_14b_ttft:
            ratio = ollama_14b.cold_ttft_ms / native_14b_ttft
            print(f"\n  ★  Native 14B first-token: {native_14b_ttft:.0f}ms")
            print(f"  ★  Ollama 14B first-token (cold): {ollama_14b.cold_ttft_ms/1000:.1f}s")
            print(f"  ★  NATIVE IS {ratio:.0f}x FASTER TO FIRST TOKEN ON 14B")
        elif ollama_14b:
            print(f"\n  ★  Ollama 14B cold-load: {ollama_14b.cold_ttft_ms/1000:.1f}s to first token")
            print("     (native 14B always-loaded — measured separately as ~388ms)")

    # Speed
    print("\n  SPEED — Decode t/s + Time-to-First-Token")
    print("  " + "─" * (W - 2))
    speed_rows = {pid: [r for r in speed if r.provider == pid] for pid in order}
    max_tps = max((sum(r.tps for r in rows) / len(rows) for rows in speed_rows.values() if rows), default=1)
    for pid in order:
        rows = speed_rows[pid]
        if not rows:
            continue
        avg_tps = sum(r.tps for r in rows) / len(rows)
        ttft_rows = [r.ttft_ms for r in rows if r.ttft_ms >= 0]
        avg_ttft = sum(ttft_rows) / len(ttft_rows) if ttft_rows else -1
        label = PROVIDERS[pid]["label"]
        bar = _bar(avg_tps, max_tps)
        print(f"  {label:<35} {avg_tps:5.1f} t/s  {bar}  {_fmt_ttft(avg_ttft)}")

    o8 = [r for r in speed if r.provider == "ollama-8b"]
    n8 = [r for r in speed if r.provider == "native-8b"]
    o14 = [r for r in speed if r.provider == "ollama-14b"]
    n14 = [r for r in speed if r.provider == "native-14b"]
    if o8 and n8:
        s_o8 = sum(r.tps for r in o8) / len(o8)
        s_n8 = sum(r.tps for r in n8) / len(n8)
        n8_ttft = sum(r.ttft_ms for r in n8 if r.ttft_ms >= 0)
        o8_ttft = sum(r.ttft_ms for r in o8 if r.ttft_ms >= 0)
        n8_ttft_c = len([r for r in n8 if r.ttft_ms >= 0])
        o8_ttft_c = len([r for r in o8 if r.ttft_ms >= 0])
        if o8_ttft_c and n8_ttft_c:
            print(f"\n  → TTFT speedup  8B: {(o8_ttft/o8_ttft_c) / (n8_ttft/n8_ttft_c):.1f}×  (native faster to first token)")
        if s_o8 > 0:
            print(f"  → Decode t/s   8B: {s_n8/s_o8:.1f}×  ({s_o8:.1f} → {s_n8:.1f} t/s, Ollama had exclusive GPU)")
    if o14 and n14:
        o14_ttft_vals = [r.ttft_ms for r in o14 if r.ttft_ms >= 0]
        n14_ttft_vals = [r.ttft_ms for r in n14 if r.ttft_ms >= 0]
        if o14_ttft_vals and n14_ttft_vals:
            o14_avg = sum(o14_ttft_vals) / len(o14_ttft_vals)
            n14_avg = sum(n14_ttft_vals) / len(n14_ttft_vals)
            print(f"  → TTFT speedup 14B: {o14_avg/n14_avg:.0f}×  ({o14_avg/1000:.1f}s → {n14_avg:.0f}ms — Ollama cold-loads model)")

    # Concurrency
    if concurrency:
        print("\n  CONCURRENCY — 4 simultaneous requests, aggregate t/s")
        print("  " + "─" * (W - 2))
        max_agg = max((r.aggregate_tps for r in concurrency), default=1)
        for r in concurrency:
            label = PROVIDERS[r.provider]["label"]
            slots = PROVIDERS[r.provider]["parallel_slots"]
            bar = _bar(r.aggregate_tps, max_agg)
            print(f"  {label:<35} {r.aggregate_tps:5.1f} t/s  {bar}  wall={r.wall_seconds:.0f}s  slots={slots}")

    # Code quality
    if code:
        print("\n  CODE QUALITY — 10 hard problems (executed), pass@1")
        print("  " + "─" * (W - 2))
        for pid in order:
            rows = [r for r in code if r.provider == pid]
            if not rows:
                continue
            ok = sum(1 for r in rows if r.passed == r.total)
            cases_ok = sum(r.passed for r in rows)
            cases_tot = sum(r.total for r in rows)
            label = PROVIDERS[pid]["label"]
            bar = _bar(ok, 10)
            print(f"  {label:<35} {ok:2d}/10  {bar}  ({cases_ok}/{cases_tot} test cases)")

    # Debugging
    if debug:
        print("\n  DEBUGGING — 5 broken-code fix problems (executed)")
        print("  " + "─" * (W - 2))
        for pid in order:
            rows = [r for r in debug if r.provider == pid]
            if not rows:
                continue
            ok = sum(1 for r in rows if r.passed == r.total)
            label = PROVIDERS[pid]["label"]
            bar = _bar(ok, 5)
            print(f"  {label:<35} {ok:2d}/ 5  {bar}")

    # Web
    if web:
        print("\n  WEB PATTERNS — 5 HTML/CSS/JS generation problems")
        print("  " + "─" * (W - 2))
        for pid in order:
            rows = [r for r in web if r.provider == pid]
            if not rows:
                continue
            ok = sum(1 for r in rows if r.passed)
            label = PROVIDERS[pid]["label"]
            bar = _bar(ok, 5)
            print(f"  {label:<35} {ok:2d}/ 5  {bar}")

    # Verdict — TTFT-weighted scoring
    print("\n  " + "─" * (W - 2))
    print("  VERDICT  (TTFT 35% · code 25% · debug 20% · web 10% · speed 10%)")

    def score(pid: str) -> float:
        s = [r for r in speed if r.provider == pid]
        c = [r for r in code if r.provider == pid]
        d = [r for r in debug if r.provider == pid]
        w = [r for r in web if r.provider == pid]

        avg_tps = sum(x.tps for x in s) / len(s) if s else 0
        ttft_vals = [x.ttft_ms for x in s if x.ttft_ms >= 0]
        avg_ttft = sum(ttft_vals) / len(ttft_vals) if ttft_vals else 60000

        # 200ms = 100 pts, scales by reciprocal — 47s TTFT ≈ 0.4 pts
        ttft_score = min(100.0, (200.0 / avg_ttft) * 100) if avg_ttft > 0 else 0
        speed_score = min(100.0, (avg_tps / 20.0) * 100)
        code_pct = sum(1 for x in c if x.passed == x.total) / len(c) * 100 if c else 0
        debug_pct = sum(1 for x in d if x.passed == x.total) / len(d) * 100 if d else 0
        web_pct = sum(1 for x in w if x.passed) / len(w) * 100 if w else 0

        return ttft_score * 0.35 + speed_score * 0.10 + code_pct * 0.25 + debug_pct * 0.20 + web_pct * 0.10

    ranked = sorted([(pid, score(pid)) for pid in order], key=lambda x: x[1], reverse=True)
    medals = ["🥇", "🥈", "🥉", "  "]
    print()
    for i, (pid, sc) in enumerate(ranked):
        label = PROVIDERS[pid]["label"]
        print(f"  {medals[min(i,3)]} #{i+1}  {label:<35}  score {sc:.1f}")
    print()
    print("═" * W)


# ── Entrypoint ────────────────────────────────────────────────────────────────

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="VOOL vs Standard Ollama benchmark")
    default_providers = [p for p in PROVIDERS if not PROVIDERS[p].get("solo_gpu")]
    parser.add_argument("--providers", nargs="+", default=default_providers, choices=list(PROVIDERS.keys()))
    parser.add_argument("--skip-code", action="store_true")
    parser.add_argument("--skip-debug", action="store_true")
    parser.add_argument("--skip-web", action="store_true")
    parser.add_argument("--skip-concurrency", action="store_true")
    parser.add_argument("--skip-cold-start", action="store_true")
    parser.add_argument("--speed-runs", type=int, default=2)
    parser.add_argument("--output", help="Save JSON to file")
    args = parser.parse_args()

    providers = args.providers
    print(f"\nBenchmarking: {', '.join(providers)}")
    print("NOTE: Ollama and native servers share the Metal GPU — they run sequentially.")
    print("Estimated time: ~25-40 min for all 4 providers.\n")

    ollama_providers = [p for p in providers if PROVIDERS[p]["api_style"] in ("ollama", "ollama-thinking")]
    native_providers = [p for p in providers if PROVIDERS[p]["api_style"] == "openai" and not PROVIDERS[p].get("solo_gpu")]

    speed, conc, code, debug, web, cold = [], [], [], [], [], []

    # Phase 0 — Cold-start TTFT (Ollama model-swap penalty, the killer number)
    if ollama_providers and not args.skip_cold_start:
        killed = stop_native_servers()
        if killed:
            print("[GPU] Stopped native servers for cold-start test.")
        cold += run_cold_start_benchmark()

    # Phase 1 — Ollama (stop native servers to free Metal GPU)
    if ollama_providers:
        killed = stop_native_servers()
        if killed:
            print(f"[GPU] Stopped native servers (PIDs {killed}) for fair Ollama baseline.")
        else:
            print("[GPU] No native servers running; proceeding with Ollama tests.")
        speed += run_speed_benchmark(ollama_providers, runs=args.speed_runs)
        if not args.skip_concurrency:
            conc += run_concurrency_benchmark(ollama_providers, n_requests=4)
        if not args.skip_code:
            code += run_code_benchmark(ollama_providers)
        if not args.skip_debug:
            debug += run_debug_benchmark(ollama_providers)
        if not args.skip_web:
            web += run_web_benchmark(ollama_providers)

    # Phase 2 — Native (restart servers between each phase for clean Metal state)
    if native_providers:
        if ollama_providers:
            restart_native_servers("after Ollama phase")
        else:
            start_native_servers()
        speed += run_speed_benchmark(native_providers, runs=args.speed_runs)
        if not args.skip_concurrency:
            restart_native_servers("before concurrency")
            conc += run_concurrency_benchmark(native_providers, n_requests=4)
        if not args.skip_code:
            restart_native_servers("before code quality")
            code += run_code_benchmark(native_providers)
        if not args.skip_debug:
            restart_native_servers("before debug")
            debug += run_debug_benchmark(native_providers)
        if not args.skip_web:
            restart_native_servers("before web")
            web += run_web_benchmark(native_providers)

    # Phase 3 — Solo experiments (each takes full Metal GPU, no dual-server overhead)
    solo_providers = [p for p in providers if PROVIDERS[p].get("solo_gpu")]
    if solo_providers:
        print("\n[GPU] Solo experiments — stopping dual-server, giving full Metal to each provider...")
        stop_native_servers()
        time.sleep(3)
        for spid in solo_providers:
            label = PROVIDERS[spid]["label"]
            print(f"\n[GPU] Solo start: {label}")
            ok = start_solo_server(spid)
            if not ok:
                print(f"  [WARN] {spid} did not become healthy — skipping")
                stop_solo_server(spid)
                continue
            speed += run_speed_benchmark([spid], runs=args.speed_runs)
            stop_solo_server(spid)
            time.sleep(3)
        print("\n[GPU] Solo experiments done. Restoring dual-server...")
        start_native_servers()

    print_report(speed, conc, code, debug, web, cold)

    if args.output:
        import dataclasses
        data = {
            "speed": [dataclasses.asdict(r) for r in speed],
            "concurrency": [dataclasses.asdict(r) for r in conc],
            "code": [dataclasses.asdict(r) for r in code],
            "debug": [dataclasses.asdict(r) for r in debug],
            "web": [dataclasses.asdict(r) for r in web],
        }
        with open(args.output, "w") as f:
            json.dump(data, f, indent=2)
        print(f"\nResults saved to {args.output}")


if __name__ == "__main__":
    main()
