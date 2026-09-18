#!/usr/bin/env python3
"""
VOOL Inference Speed Benchmark — the commodity-hardware yardstick.

Measures, on the machine you actually run on (no GPU farm required), the levers
that move local-LLM latency and throughput:

  * generation throughput   — tokens/sec (Ollama's authoritative eval_count / eval_duration)
  * prefill throughput      — prompt tokens/sec (prompt_eval_count / prompt_eval_duration)
  * TTFT                     — wall time to first streamed content token
  * model load              — cold load_duration (first call) vs warm
  * PROMPT-CACHE REUSE       — the free win on the SAME box: a second call that
                               shares a long prefix should pay far less prefill.
                               This quantifies the benefit the prefix/KV-cache
                               wiring targets — no new hardware needed.

Everything here runs against a local Ollama (default qwen3:0.6b, already pulled).
It is eval-first infrastructure: build the yardstick, then optimize against it.

Usage:
  python -m tests.benchmarks.inference_speed_bench
  python -m tests.benchmarks.inference_speed_bench --model qwen3:0.6b
  python -m tests.benchmarks.inference_speed_bench --quick
  python -m tests.benchmarks.inference_speed_bench --json
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen3:0.6b"


def _post_stream(path: str, body: dict, timeout: float = 180.0):
    """Yield decoded JSON objects from a streaming Ollama endpoint."""
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA_URL}{path}", data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for raw in resp:
            raw = raw.strip()
            if raw:
                yield json.loads(raw)


def measure_generate(
    model: str,
    prompt: str,
    *,
    num_predict: int = 128,
    seed: int = 7,
    think: bool = False,
    keep_alive: str = "5m",
) -> dict:
    """Run one generation and return timing + throughput.

    tok/s come from Ollama's own nanosecond counters (eval_duration /
    prompt_eval_duration), which are independent of client-side jitter. TTFT is
    wall-clock to the first streamed chunk carrying any content (response OR
    thinking), so thinking models are timed fairly.
    """
    body = {
        "model": model,
        "prompt": prompt,
        "stream": True,
        "keep_alive": keep_alive,
        "options": {"num_predict": num_predict, "seed": seed, "temperature": 0},
    }
    # qwen3-family reasoning toggle; ignored by models that don't support it.
    body["think"] = think

    t0 = time.perf_counter()
    ttft = None
    final = {}
    for obj in _post_stream("/api/generate", body):
        if ttft is None and (obj.get("response") or obj.get("thinking")):
            ttft = time.perf_counter() - t0
        if obj.get("done"):
            final = obj
            break
    wall = time.perf_counter() - t0

    def _rate(count_key: str, dur_key: str):
        c = final.get(count_key)
        d = final.get(dur_key)  # nanoseconds
        if c and d:
            return c, c / (d / 1e9)
        return (c or 0), None

    gen_tok, gen_tps = _rate("eval_count", "eval_duration")
    pre_tok, pre_tps = _rate("prompt_eval_count", "prompt_eval_duration")
    return {
        "wall_s": round(wall, 3),
        "ttft_ms": round(ttft * 1000, 1) if ttft is not None else None,
        "gen_tok": gen_tok,
        "gen_tok_s": round(gen_tps, 1) if gen_tps else None,
        "prefill_tok": pre_tok,
        "prefill_tok_s": round(pre_tps, 1) if pre_tps else None,
        "load_ms": round(final.get("load_duration", 0) / 1e6, 1),
    }


def _median(vals):
    vals = [v for v in vals if v is not None]
    return round(statistics.median(vals), 1) if vals else None


def bench_throughput(model: str, sizes, runs: int) -> list[dict]:
    """Generation throughput at several output lengths (warm model)."""
    rows = []
    prompt = "Explain why local-first AI matters for ordinary users. Be concise and concrete."
    for n in sizes:
        samples = [measure_generate(model, prompt, num_predict=n) for _ in range(runs)]
        rows.append(
            {
                "num_predict": n,
                "gen_tok_s": _median([s["gen_tok_s"] for s in samples]),
                "ttft_ms": _median([s["ttft_ms"] for s in samples]),
                "wall_s": _median([s["wall_s"] for s in samples]),
            }
        )
    return rows


def bench_prefill_scaling(model: str) -> list[dict]:
    """Prefill throughput + TTFT as the prompt grows (the cost cache reuse saves)."""
    rows = []
    unit = "The deploy runs on port 8096 with the publicnode RPC. "
    for mult in (1, 20, 80):
        prompt = unit * mult + "\nSummarize the setup in one sentence."
        s = measure_generate(model, prompt, num_predict=24)
        rows.append(
            {
                "prompt_chars": len(prompt),
                "prefill_tok": s["prefill_tok"],
                "prefill_tok_s": s["prefill_tok_s"],
                "ttft_ms": s["ttft_ms"],
            }
        )
    return rows


def bench_thinking_toggle(model: str) -> dict:
    """Quantify the think:false latency win on a reasoning model.

    qwen3 emits hidden reasoning tokens by default; for most normie tasks that is
    pure latency the user pays before seeing an answer. Same prompt, same output
    cap, reasoning ON vs OFF — the wall-time gap is a free win the runtime can
    flip per role.
    """
    prompt = "What port does the deploy use if it's 8096? Answer in one short sentence."
    on = measure_generate(model, prompt, num_predict=256, think=True)
    off = measure_generate(model, prompt, num_predict=256, think=False)
    speedup = round(on["wall_s"] / off["wall_s"], 2) if off["wall_s"] else None
    return {
        "think_on_wall_s": on["wall_s"],
        "think_off_wall_s": off["wall_s"],
        "think_on_gen_tok": on["gen_tok"],
        "think_off_gen_tok": off["gen_tok"],
        "wall_speedup_x": speedup,
        "note": "think:false skips reasoning tokens; lower wall = faster answer for the user",
    }


def bench_prompt_cache_reuse(model: str) -> dict:
    """The on-this-box win: a long SHARED prefix should be cheap to re-prefill.

    Call 1 establishes a long context; call 2 reuses the same prefix with a new
    tail. Ollama keeps the model + KV context warm (keep_alive), so call 2's
    prefill should be markedly cheaper for the shared portion. We report the
    prefill wall + TTFT delta — the concrete payoff of prefix/KV-cache reuse.
    """
    shared_prefix = (
        "You are VOOL, a local agent. Context that stays constant across turns:\n"
        + ("- A durable project fact the assistant must remember across turns.\n" * 60)
    )
    first = measure_generate(model, shared_prefix + "\nQ1: State the first fact.", num_predict=24)
    second = measure_generate(model, shared_prefix + "\nQ2: State another fact.", num_predict=24)
    ttft1, ttft2 = first["ttft_ms"], second["ttft_ms"]
    speedup = round(ttft1 / ttft2, 2) if (ttft1 and ttft2) else None
    return {
        "shared_prefix_chars": len(shared_prefix),
        "call1_ttft_ms": ttft1,
        "call2_ttft_ms": ttft2,
        "call1_prefill_tok_s": first["prefill_tok_s"],
        "call2_prefill_tok_s": second["prefill_tok_s"],
        "ttft_speedup_x": speedup,
        "note": "call2 shares call1's prefix; lower TTFT / higher prefill tok/s = cache reuse working",
    }


def ollama_up() -> bool:
    try:
        urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=3)
        return True
    except (urllib.error.URLError, OSError):
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="VOOL inference speed yardstick")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--quick", action="store_true", help="fewer sizes / single run")
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON only")
    args = ap.parse_args()

    if not ollama_up():
        print("Ollama not reachable at 127.0.0.1:11434 — start it with `ollama serve`.", file=sys.stderr)
        return 2

    sizes = (32, 128) if args.quick else (32, 128, 256)
    runs = 1 if args.quick else 3

    # Warm the model once so cold load_duration doesn't skew throughput rows.
    warm = measure_generate(args.model, "warmup", num_predict=8)

    result = {
        "model": args.model,
        "warm_load_ms": warm["load_ms"],
        "throughput": bench_throughput(args.model, sizes, runs),
        "prefill_scaling": bench_prefill_scaling(args.model),
        "thinking_toggle": bench_thinking_toggle(args.model),
        "prompt_cache_reuse": bench_prompt_cache_reuse(args.model),
    }

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    print(f"\nNULLA inference speed — {args.model}  (warm load {result['warm_load_ms']}ms)")
    print("\nGeneration throughput (warm):")
    print(f"  {'num_predict':>11} {'gen tok/s':>10} {'TTFT ms':>9} {'wall s':>8}")
    for r in result["throughput"]:
        print(f"  {r['num_predict']:>11} {r['gen_tok_s']!s:>10} {r['ttft_ms']!s:>9} {r['wall_s']!s:>8}")
    print("\nPrefill scaling (cost that prefix-cache reuse avoids):")
    print(f"  {'prompt_chars':>12} {'prefill_tok':>11} {'prefill tok/s':>13} {'TTFT ms':>9}")
    for r in result["prefill_scaling"]:
        print(f"  {r['prompt_chars']:>12} {r['prefill_tok']!s:>11} {r['prefill_tok_s']!s:>13} {r['ttft_ms']!s:>9}")
    t = result["thinking_toggle"]
    print("\nThinking toggle (reasoning ON vs OFF, same task):")
    print(f"  think ON wall={t['think_on_wall_s']}s ({t['think_on_gen_tok']} tok)  "
          f"think OFF wall={t['think_off_wall_s']}s ({t['think_off_gen_tok']} tok)  speedup={t['wall_speedup_x']}x")
    c = result["prompt_cache_reuse"]
    print("\nPrompt-cache reuse (shared prefix, same box — the free win):")
    print(f"  call1 TTFT={c['call1_ttft_ms']}ms  call2 TTFT={c['call2_ttft_ms']}ms  speedup={c['ttft_speedup_x']}x")
    print(f"  call1 prefill={c['call1_prefill_tok_s']} tok/s  call2 prefill={c['call2_prefill_tok_s']} tok/s")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
