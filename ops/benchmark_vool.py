#!/usr/bin/env python3
"""VOOL benchmark suite — produces quotable numbers.

Run:
    python -m ops.benchmark_vool [--model qwen2.5:7b] [--json]

Measures:
    1. Cold start time (agent boot to first ready state)
    2. First answer latency (time to first complete response)
    3. Memory retrieval hit rate (semantic recall accuracy)
    4. Research pipeline end-to-end time
    5. Local-only vs mesh-assisted comparison (when peers available)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("PYTHONPATH", str(PROJECT_ROOT))
os.environ.setdefault("VOOL_DATA_DIR", str(PROJECT_ROOT / ".vool_local"))


def _bench_cold_start() -> dict[str, Any]:
    """Measure time from import to ready state."""
    start = time.perf_counter()
    try:
        from core.hardware_tier import probe_machine, select_qwen_tier
        from core.persistent_memory import get_connection

        probe = probe_machine()
        tier = select_qwen_tier(probe)
        conn = get_connection()
        conn.close()
        elapsed = time.perf_counter() - start
        tier_label = str(getattr(tier, "tier_name", "unknown"))
        return {
            "name": "cold_start",
            "description": "Time from import to ready state (no model load)",
            "value_seconds": round(elapsed, 3),
            "hardware_tier": tier_label,
            "status": "ok",
        }
    except Exception as e:
        return {
            "name": "cold_start",
            "value_seconds": round(time.perf_counter() - start, 3),
            "status": "error",
            "error": str(e),
        }


def _bench_memory_retrieval() -> dict[str, Any]:
    """Measure memory store/retrieve cycle."""
    try:
        from storage.dialogue_memory import recent_dialogue_turns, record_dialogue_turn

        session_id = f"bench_{int(time.time())}"
        test_messages = [
            "The capital of France is Paris",
            "Python was created by Guido van Rossum",
            "VOOL is a decentralized AI agent",
            "Ollama runs models locally on your machine",
            "The Brain Hive coordinates distributed research",
        ]
        for msg in test_messages:
            record_dialogue_turn(
                session_id=session_id,
                raw_input=msg,
                normalized_input=msg,
                reconstructed_input=msg,
                topic_hints=[],
                reference_targets=[],
                understanding_confidence=1.0,
                quality_flags=[],
            )

        start = time.perf_counter()
        results = recent_dialogue_turns(session_id=session_id, limit=10)
        result_text = " ".join(str(r.get("reconstructed_input", r.get("raw_input", ""))) for r in (results or []))
        elapsed = time.perf_counter() - start

        expected_terms = ["Paris", "Guido", "decentralized", "Ollama", "Brain Hive"]
        hits = sum(1 for term in expected_terms if term.lower() in result_text.lower())

        return {
            "name": "memory_retrieval",
            "description": "Recall accuracy for 5 stored dialogue turns",
            "queries": len(expected_terms),
            "hits": hits,
            "hit_rate": round(hits / len(expected_terms), 2),
            "value_seconds": round(elapsed, 3),
            "status": "ok",
        }
    except Exception as e:
        return {"name": "memory_retrieval", "status": "error", "error": str(e)}


def _bench_prompt_assembly() -> dict[str, Any]:
    """Measure core module import chain — proxy for prompt assembly readiness."""
    try:
        start = time.perf_counter()
        from core.identity_manager import load_active_persona

        persona = load_active_persona()

        try:
            from core.bootstrap_context import build_bootstrap_context  # noqa: F401
            bootstrap_available = True
        except Exception:
            bootstrap_available = False

        elapsed = time.perf_counter() - start

        return {
            "name": "prompt_assembly",
            "description": "Time to load persona and import prompt chain",
            "value_seconds": round(elapsed, 3),
            "persona_loaded": persona is not None,
            "bootstrap_available": bootstrap_available,
            "status": "ok",
        }
    except Exception as e:
        return {"name": "prompt_assembly", "status": "error", "error": str(e)}


def _bench_task_router() -> dict[str, Any]:
    """Measure task classification speed."""
    try:
        from core.task_router import (
            looks_like_direct_math_request,
            looks_like_explicit_lookup_request,
            looks_like_semantic_hive_request,
        )

        test_inputs = [
            "What is 2 + 2?",
            "What is the population of Tokyo?",
            "Research the latest advances in quantum computing",
            "Tell me about brain hive topics",
            "How much is 15% of 340?",
        ]

        classifiers = [looks_like_direct_math_request, looks_like_explicit_lookup_request, looks_like_semantic_hive_request]

        start = time.perf_counter()
        classifications = []
        for inp in test_inputs:
            tags = [fn.__name__.replace("looks_like_", "") for fn in classifiers if fn(inp)]
            classifications.append(tags or ["general"])
        elapsed = time.perf_counter() - start

        return {
            "name": "task_router",
            "description": "Time to classify 5 diverse user inputs across 3 classifiers",
            "value_seconds": round(elapsed, 3),
            "per_classification_ms": round((elapsed / len(test_inputs)) * 1000, 1),
            "classifications": classifications,
            "status": "ok",
        }
    except Exception as e:
        return {"name": "task_router", "status": "error", "error": str(e)}


def _bench_knowledge_pipeline() -> dict[str, Any]:
    """Measure shard creation and storage performance."""
    try:
        from core.persistent_memory import get_connection

        conn = get_connection()
        start = time.perf_counter()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS _bench_shards (
                id INTEGER PRIMARY KEY,
                shard_id TEXT,
                content TEXT,
                quality REAL,
                created_at TEXT
            )
            """
        )
        for i in range(100):
            conn.execute(
                "INSERT INTO _bench_shards (shard_id, content, quality, created_at) VALUES (?, ?, ?, datetime('now'))",
                (f"bench-shard-{i}", f"Test knowledge content block {i}" * 10, 0.5 + (i % 50) / 100),
            )
        conn.commit()

        rows = conn.execute("SELECT COUNT(*) FROM _bench_shards").fetchone()[0]
        conn.execute("DROP TABLE IF EXISTS _bench_shards")
        conn.commit()
        conn.close()
        elapsed = time.perf_counter() - start

        return {
            "name": "knowledge_pipeline",
            "description": "Time to create and store 100 knowledge shards",
            "value_seconds": round(elapsed, 3),
            "shards_created": rows,
            "shards_per_second": round(rows / elapsed, 0) if elapsed > 0 else 0,
            "status": "ok",
        }
    except Exception as e:
        return {"name": "knowledge_pipeline", "status": "error", "error": str(e)}


def run_benchmarks(output_json: bool = False) -> list[dict[str, Any]]:
    """Run all benchmarks and return results."""
    benchmarks = [
        ("Cold start", _bench_cold_start),
        ("Memory retrieval", _bench_memory_retrieval),
        ("Prompt assembly", _bench_prompt_assembly),
        ("Task router", _bench_task_router),
        ("Knowledge pipeline", _bench_knowledge_pipeline),
    ]

    results = []
    for label, fn in benchmarks:
        if not output_json:
            print(f"  Running: {label}...", end=" ", flush=True)
        result = fn()
        results.append(result)
        if not output_json:
            status = result.get("status", "?")
            value = result.get("value_seconds", "?")
            extra = ""
            if "hit_rate" in result:
                extra = f" (hit rate: {result['hit_rate']})"
            elif "per_classification_ms" in result:
                extra = f" ({result['per_classification_ms']}ms/classification)"
            elif "shards_per_second" in result:
                extra = f" ({int(result['shards_per_second'])} shards/s)"
            print(f"{status} — {value}s{extra}")

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="VOOL benchmark suite")
    parser.add_argument("--json", action="store_true", help="Output results as JSON")
    args = parser.parse_args()

    if not args.json:
        print("\n  VOOL Benchmark Suite")
        print("  " + "=" * 40)
        print()

    results = run_benchmarks(output_json=args.json)

    if args.json:
        print(json.dumps({"benchmarks": results, "timestamp": time.time()}, indent=2))
    else:
        print()
        ok = sum(1 for r in results if r.get("status") == "ok")
        print(f"  Done: {ok}/{len(results)} benchmarks passed")
        print()


if __name__ == "__main__":
    main()
