from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from core.provider_routing import ProviderCapabilityTruth
from storage.db import get_connection


@dataclass(frozen=True)
class LocalInferenceBenchmarkFact:
    benchmark_id: str
    provider_id: str
    model_id: str
    backend: str
    benchmark_kind: str
    prompt_hash: str
    context_window: int
    tokens_per_second: float
    ttft_ms: float
    load_ms: float
    prompt_eval_ms: float
    eval_count: int
    processor: str
    quantization: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "vool.local_inference_benchmark.v1",
            "benchmark_id": self.benchmark_id,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "backend": self.backend,
            "benchmark_kind": self.benchmark_kind,
            "prompt_hash": self.prompt_hash,
            "context_window": self.context_window,
            "tokens_per_second": self.tokens_per_second,
            "ttft_ms": self.ttft_ms,
            "load_ms": self.load_ms,
            "prompt_eval_ms": self.prompt_eval_ms,
            "eval_count": self.eval_count,
            "processor": self.processor,
            "quantization": self.quantization,
            "created_at": self.created_at,
        }


def record_local_inference_benchmark(
    *,
    provider_id: str,
    model_id: str,
    backend: str,
    benchmark_kind: str,
    prompt: str = "",
    context_window: int = 0,
    tokens_per_second: float = 0.0,
    ttft_ms: float = 0.0,
    load_ms: float = 0.0,
    prompt_eval_ms: float = 0.0,
    eval_count: int = 0,
    processor: str = "",
    quantization: str = "",
    raw_result: dict[str, Any] | None = None,
) -> LocalInferenceBenchmarkFact:
    _init_local_inference_benchmark_table()
    created_at = _utcnow()
    prompt_hash = _prompt_hash(prompt)
    benchmark_id = hashlib.sha256(
        f"{provider_id}:{model_id}:{backend}:{benchmark_kind}:{prompt_hash}:{created_at}".encode()
    ).hexdigest()[:24]
    fact = LocalInferenceBenchmarkFact(
        benchmark_id=benchmark_id,
        provider_id=str(provider_id or "").strip(),
        model_id=str(model_id or "").strip(),
        backend=str(backend or "").strip(),
        benchmark_kind=str(benchmark_kind or "unknown").strip(),
        prompt_hash=prompt_hash,
        context_window=max(0, int(context_window or 0)),
        tokens_per_second=max(0.0, float(tokens_per_second or 0.0)),
        ttft_ms=max(0.0, float(ttft_ms or 0.0)),
        load_ms=max(0.0, float(load_ms or 0.0)),
        prompt_eval_ms=max(0.0, float(prompt_eval_ms or 0.0)),
        eval_count=max(0, int(eval_count or 0)),
        processor=str(processor or "").strip(),
        quantization=str(quantization or "").strip(),
        created_at=created_at,
    )
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO local_inference_benchmarks (
                benchmark_id, provider_id, model_id, backend, benchmark_kind,
                prompt_hash, context_window, tokens_per_second, ttft_ms, load_ms,
                prompt_eval_ms, eval_count, processor, quantization, raw_result_json,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fact.benchmark_id,
                fact.provider_id,
                fact.model_id,
                fact.backend,
                fact.benchmark_kind,
                fact.prompt_hash,
                fact.context_window,
                fact.tokens_per_second,
                fact.ttft_ms,
                fact.load_ms,
                fact.prompt_eval_ms,
                fact.eval_count,
                fact.processor,
                fact.quantization,
                json.dumps(raw_result or {}, sort_keys=True),
                fact.created_at,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return fact


def record_ollama_generate_benchmark(
    *,
    provider_id: str,
    model_id: str,
    prompt: str,
    response_payload: dict[str, Any],
    context_window: int = 0,
    processor: str = "",
    quantization: str = "",
) -> LocalInferenceBenchmarkFact:
    eval_count = max(0, int(response_payload.get("eval_count") or 0))
    eval_duration_ns = max(0.0, float(response_payload.get("eval_duration") or 0.0))
    tokens_per_second = (float(eval_count) / (eval_duration_ns / 1_000_000_000.0)) if eval_count and eval_duration_ns else 0.0
    load_ms = float(response_payload.get("load_duration") or 0.0) / 1_000_000.0
    prompt_eval_ms = float(response_payload.get("prompt_eval_duration") or 0.0) / 1_000_000.0
    ttft_ms = float(response_payload.get("first_token_duration") or response_payload.get("ttft_ms") or 0.0)
    if ttft_ms > 1_000_000:
        ttft_ms = ttft_ms / 1_000_000.0
    return record_local_inference_benchmark(
        provider_id=provider_id,
        model_id=model_id,
        backend="ollama",
        benchmark_kind="generate",
        prompt=prompt,
        context_window=context_window,
        tokens_per_second=tokens_per_second,
        ttft_ms=ttft_ms,
        load_ms=load_ms,
        prompt_eval_ms=prompt_eval_ms,
        eval_count=eval_count,
        processor=processor,
        quantization=quantization,
        raw_result=response_payload,
    )


def latest_local_inference_benchmarks(
    *,
    provider_ids: tuple[str, ...] | list[str] = (),
    limit: int = 64,
) -> tuple[LocalInferenceBenchmarkFact, ...]:
    _init_local_inference_benchmark_table()
    requested = tuple(str(item or "").strip() for item in provider_ids if str(item or "").strip())
    conn = get_connection()
    try:
        if requested:
            placeholders = ",".join("?" for _ in requested)
            rows = conn.execute(
                f"""
                SELECT *
                FROM local_inference_benchmarks
                WHERE provider_id IN ({placeholders})
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (*requested, max(1, int(limit or 64))),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT *
                FROM local_inference_benchmarks
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (max(1, int(limit or 64)),),
            ).fetchall()
    finally:
        conn.close()
    return tuple(_row_to_fact(dict(row)) for row in rows)


def hydrate_capability_truth_with_benchmarks(
    capability_truth: tuple[ProviderCapabilityTruth, ...] | list[ProviderCapabilityTruth],
) -> tuple[ProviderCapabilityTruth, ...]:
    capabilities = tuple(capability_truth or ())
    if not capabilities:
        return tuple()
    try:
        facts = latest_local_inference_benchmarks(
            provider_ids=tuple(item.provider_id for item in capabilities),
            limit=max(16, len(capabilities) * 4),
        )
    except Exception:
        return capabilities
    latest_by_provider: dict[str, LocalInferenceBenchmarkFact] = {}
    for fact in facts:
        latest_by_provider.setdefault(fact.provider_id, fact)
    hydrated: list[ProviderCapabilityTruth] = []
    for capability in capabilities:
        fact = latest_by_provider.get(capability.provider_id)
        if fact is None:
            hydrated.append(capability)
            continue
        hydrated.append(
            ProviderCapabilityTruth(
                provider_id=capability.provider_id,
                model_id=capability.model_id,
                role_fit=capability.role_fit,
                context_window=capability.context_window or fact.context_window,
                tool_support=capability.tool_support,
                structured_output_support=capability.structured_output_support,
                tokens_per_second=fact.tokens_per_second or capability.tokens_per_second,
                ram_budget_gb=capability.ram_budget_gb,
                vram_budget_gb=capability.vram_budget_gb,
                quantization=fact.quantization or capability.quantization,
                locality=capability.locality,
                privacy_class=capability.privacy_class,
                queue_depth=capability.queue_depth,
                max_safe_concurrency=capability.max_safe_concurrency,
                availability_state=capability.availability_state,
                circuit_open=capability.circuit_open,
                last_error=capability.last_error,
                measurement_source="local_inference_benchmark",
                measured_at=fact.created_at,
                # Preserve the free-cloud/tool_intent signal computed in
                # `provider_capability_truth_for_manifest` -- this reconstruction rebuilds every
                # other field explicitly, and a field silently missing here resets to the
                # dataclass default (False) on every turn after the manifest's first benchmark
                # fact is recorded, which would quietly undo the drone-role free-cloud priority
                # fix in `local_inference_autopilot.py` the first time this ran against a
                # provider that had already been benchmarked once (a real scenario for
                # `openrouter-byok`: `OpenAICompatibleAdapter._record_benchmark_best_effort`
                # records a benchmark fact after ANY real call, local or remote).
                is_verified_free_cloud_tool_capable=capability.is_verified_free_cloud_tool_capable,
            )
        )
    return tuple(hydrated)


def _init_local_inference_benchmark_table() -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS local_inference_benchmarks (
                benchmark_id TEXT PRIMARY KEY,
                provider_id TEXT NOT NULL,
                model_id TEXT NOT NULL,
                backend TEXT NOT NULL,
                benchmark_kind TEXT NOT NULL,
                prompt_hash TEXT NOT NULL,
                context_window INTEGER NOT NULL DEFAULT 0,
                tokens_per_second REAL NOT NULL DEFAULT 0,
                ttft_ms REAL NOT NULL DEFAULT 0,
                load_ms REAL NOT NULL DEFAULT 0,
                prompt_eval_ms REAL NOT NULL DEFAULT 0,
                eval_count INTEGER NOT NULL DEFAULT 0,
                processor TEXT NOT NULL DEFAULT '',
                quantization TEXT NOT NULL DEFAULT '',
                raw_result_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_local_inference_benchmarks_provider_created
            ON local_inference_benchmarks(provider_id, created_at DESC)
            """
        )
        conn.commit()
    finally:
        conn.close()


def _row_to_fact(row: dict[str, Any]) -> LocalInferenceBenchmarkFact:
    return LocalInferenceBenchmarkFact(
        benchmark_id=str(row.get("benchmark_id") or ""),
        provider_id=str(row.get("provider_id") or ""),
        model_id=str(row.get("model_id") or ""),
        backend=str(row.get("backend") or ""),
        benchmark_kind=str(row.get("benchmark_kind") or ""),
        prompt_hash=str(row.get("prompt_hash") or ""),
        context_window=int(row.get("context_window") or 0),
        tokens_per_second=float(row.get("tokens_per_second") or 0.0),
        ttft_ms=float(row.get("ttft_ms") or 0.0),
        load_ms=float(row.get("load_ms") or 0.0),
        prompt_eval_ms=float(row.get("prompt_eval_ms") or 0.0),
        eval_count=int(row.get("eval_count") or 0),
        processor=str(row.get("processor") or ""),
        quantization=str(row.get("quantization") or ""),
        created_at=str(row.get("created_at") or ""),
    )


def _prompt_hash(prompt: str) -> str:
    return hashlib.sha256(str(prompt or "").encode("utf-8")).hexdigest()[:16]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "LocalInferenceBenchmarkFact",
    "hydrate_capability_truth_with_benchmarks",
    "latest_local_inference_benchmarks",
    "record_local_inference_benchmark",
    "record_ollama_generate_benchmark",
]
