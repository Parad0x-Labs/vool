from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from typing import Any

from adapters.base_adapter import ModelRequest
from core import audit_logger
from core.candidate_knowledge_lane import build_task_hash, record_candidate_output
from core.model_health import circuit_is_open, record_provider_failure, record_provider_success
from core.model_registry import ModelRegistry
from core.model_trust import output_trust_score
from core.orchestration import TaskEnvelopeV1, provider_role_for_task_role
from core.output_validator import validate_provider_output
from core.provider_execution_boundary import invoke_provider_execution_boundary
from core.provider_routing import (
    ProviderRole,
    ProviderRoutingPlan,
    resolve_provider_routing_plan,
    resolve_provider_routing_plan_for_envelope,
)
from network.signer import get_local_peer_id
from storage.model_provider_manifest import ModelProviderManifest


@dataclass
class TeacherCandidate:
    task_kind: str
    provider_name: str
    model_name: str
    output_text: str
    confidence: float
    candidate_only: bool = True
    source_model_tag: str = ""
    provenance: dict[str, Any] = field(default_factory=dict)
    candidate_id: str | None = None
    provider_role: str = "auto"
    swarm_provider_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _PipelineCandidateResult:
    manifest: ModelProviderManifest
    output_text: str
    structured_output: Any
    confidence: float
    trust_score: float
    validation_state: str
    license_metadata: dict[str, Any]
    rank_index: int


@dataclass(frozen=True)
class _PipelineAttemptResult:
    manifest: ModelProviderManifest
    candidate: _PipelineCandidateResult | None
    status: str
    error: str | None = None
    health: dict[str, Any] = field(default_factory=dict)

    def provenance_payload(self) -> dict[str, Any]:
        return {
            "provider_id": self.manifest.provider_id,
            "provider_name": self.manifest.provider_name,
            "model_name": self.manifest.model_name,
            "status": self.status,
            "error": self.error,
            "health": dict(self.health or {}),
        }


class ModelTeacherPipeline:
    def __init__(self, registry: ModelRegistry | None = None) -> None:
        self.registry = registry or ModelRegistry()

    def run(
        self,
        *,
        task_kind: str,
        prompt: str,
        system_prompt: str | None = None,
        context: dict[str, Any] | None = None,
        preferred_provider: str | None = None,
        preferred_model: str | None = None,
        trace_id: str | None = None,
        output_mode: str = "plain_text",
        provider_role: ProviderRole = "auto",
        swarm_size: int | None = None,
        allow_paid_fallback: bool | None = None,
        task_envelope: TaskEnvelopeV1 | None = None,
    ) -> TeacherCandidate | None:
        resolved_role = provider_role_for_task_role(task_envelope.role) if task_envelope else provider_role
        requested_swarm_size = self._requested_swarm_size(task_envelope=task_envelope, swarm_size=swarm_size)
        requested_allow_paid = (
            allow_paid_fallback
            if allow_paid_fallback is not None
            else self._envelope_bool_constraint(task_envelope, "allow_paid_fallback")
        )
        plan = (
            resolve_provider_routing_plan_for_envelope(
                self.registry,
                envelope=task_envelope,
                task_kind=task_kind,
                output_mode=output_mode,
                preferred_provider=preferred_provider,
                preferred_model=preferred_model,
                allow_paid_fallback=requested_allow_paid,
                swarm_size=requested_swarm_size,
                min_trust=0.0,
            )
            if task_envelope is not None
            else resolve_provider_routing_plan(
                self.registry,
                task_kind=task_kind,
                output_mode=output_mode,
                role=resolved_role,
                preferred_provider=preferred_provider,
                preferred_model=preferred_model,
                allow_paid_fallback=requested_allow_paid,
                swarm_size=int(requested_swarm_size or 1),
                min_trust=0.0,
            )
        )
        if not plan.selected:
            return None
        request = ModelRequest(
            task_kind=task_kind,
            prompt=prompt,
            system_prompt=system_prompt,
            context={
                **dict(context or {}),
                **({"task_envelope": task_envelope.to_dict()} if task_envelope is not None else {}),
            },
            output_mode=output_mode,
            trace_id=trace_id,
        )
        results, failed_attempts = self._collect_candidate_results(
            plan=plan,
            request=request,
            output_mode=output_mode,
            trace_id=trace_id,
        )
        if not results:
            return None
        execution_metadata = self._execution_metadata(plan, results=results, failed_attempts=failed_attempts)
        winner = max(results, key=lambda item: (item.trust_score, item.confidence, -item.rank_index))
        swarm_provider_ids = [item.manifest.provider_id for item in results]
        failed_attempt_payloads = [item.provenance_payload() for item in failed_attempts]
        attempted_provider_ids = [item.manifest.provider_id for item in results] + [
            item.manifest.provider_id for item in failed_attempts
        ]
        candidate_id = record_candidate_output(
            task_hash=build_task_hash(normalized_input=prompt, task_class=task_kind, output_mode=output_mode),
            task_id=None,
            trace_id=trace_id,
            task_class=task_kind,
            task_kind=task_kind,
            output_mode=output_mode,
            provider_name=winner.manifest.provider_name,
            model_name=winner.manifest.model_name,
            raw_output=winner.output_text,
            normalized_output=winner.output_text,
            structured_output=winner.structured_output,
            confidence=winner.confidence,
            trust_score=winner.trust_score,
            validation_state=winner.validation_state,
            metadata={
                "teacher_pipeline": True,
                "provider_role": plan.role,
                "swarm_size": plan.swarm_size,
                "effective_swarm_size": execution_metadata["effective_swarm_size"],
                "swarm_provider_ids": swarm_provider_ids,
                "attempted_provider_ids": attempted_provider_ids,
                "failed_attempts": failed_attempt_payloads,
                "task_envelope": dict(plan.task_envelope or {}),
                "capability_truth": [item.to_dict() for item in plan.capability_truth],
                "routing_requirements": dict(plan.routing_requirements or {}),
                "rejected_candidates": list(plan.rejected_candidates),
                "selection_notes": list(plan.selection_notes),
                "capacity_backoff_applied": execution_metadata["capacity_backoff_applied"],
                "capacity_backoff_notes": list(execution_metadata["capacity_backoff_notes"]),
            },
            provenance={
                **winner.license_metadata,
                "provider_name": winner.manifest.provider_name,
                "model_name": winner.manifest.model_name,
                "local_peer_id": get_local_peer_id(),
                "task_kind": task_kind,
                "provider_role": plan.role,
                "swarm_provider_ids": swarm_provider_ids,
                "attempted_provider_ids": attempted_provider_ids,
                "failed_attempts": failed_attempt_payloads,
                "task_envelope": dict(plan.task_envelope or {}),
                "routing_requirements": dict(plan.routing_requirements or {}),
                "rejected_candidates": list(plan.rejected_candidates),
                "selection_notes": list(plan.selection_notes),
                "capacity_backoff_applied": execution_metadata["capacity_backoff_applied"],
                "capacity_backoff_notes": list(execution_metadata["capacity_backoff_notes"]),
                "effective_swarm_size": execution_metadata["effective_swarm_size"],
            },
        )
        candidate = TeacherCandidate(
            task_kind=task_kind,
            provider_name=winner.manifest.provider_name,
            model_name=winner.manifest.model_name,
            output_text=winner.output_text,
            confidence=winner.confidence,
            source_model_tag=f"{winner.manifest.provider_name}:{winner.manifest.model_name}",
            provenance={
                "provider_name": winner.manifest.provider_name,
                "model_name": winner.manifest.model_name,
                "source_type": winner.manifest.source_type,
                "license_name": winner.manifest.license_name,
                "license_reference": winner.manifest.resolved_license_reference,
                "weight_location": winner.manifest.weight_location,
                "redistribution_allowed": winner.manifest.redistribution_allowed,
                "local_peer_id": get_local_peer_id(),
                "task_kind": task_kind,
                "runtime_dependency": winner.manifest.runtime_dependency,
                "provider_role": plan.role,
                "swarm_provider_ids": swarm_provider_ids,
                "attempted_provider_ids": attempted_provider_ids,
                "failed_attempts": failed_attempt_payloads,
                "task_envelope": dict(plan.task_envelope or {}),
                "capability_truth": [item.to_dict() for item in plan.capability_truth],
                "routing_requirements": dict(plan.routing_requirements or {}),
                "rejected_candidates": list(plan.rejected_candidates),
                "selection_notes": list(plan.selection_notes),
                "capacity_backoff_applied": execution_metadata["capacity_backoff_applied"],
                "capacity_backoff_notes": list(execution_metadata["capacity_backoff_notes"]),
                "effective_swarm_size": execution_metadata["effective_swarm_size"],
            },
            candidate_id=candidate_id,
            provider_role=plan.role,
            swarm_provider_ids=swarm_provider_ids,
        )
        audit_logger.log(
            "model_teacher_candidate_generated",
            target_id=candidate.source_model_tag,
            target_type="model_provider",
            trace_id=trace_id,
            details={
                "task_kind": candidate.task_kind,
                "candidate_only": True,
                "confidence": candidate.confidence,
                "provider_name": candidate.provider_name,
                "model_name": candidate.model_name,
                "candidate_id": candidate_id,
                "provider_role": plan.role,
                "swarm_provider_ids": swarm_provider_ids,
                "effective_swarm_size": execution_metadata["effective_swarm_size"],
                "capacity_backoff_applied": execution_metadata["capacity_backoff_applied"],
            },
        )
        return candidate

    def _collect_candidate_results(
        self,
        *,
        plan: ProviderRoutingPlan,
        request: ModelRequest,
        output_mode: str,
        trace_id: str | None,
    ) -> tuple[list[_PipelineCandidateResult], list[_PipelineAttemptResult]]:
        manifests, max_workers, _ = self._execution_manifests(plan)
        if not manifests:
            return [], []
        if len(manifests) == 1:
            attempt = self._run_candidate(
                manifests[0],
                request=request,
                output_mode=output_mode,
                trace_id=trace_id,
                rank_index=0,
            )
            return (
                [attempt.candidate] if attempt.candidate is not None else [],
                [] if attempt.candidate is not None else [attempt],
            )

        results: list[_PipelineCandidateResult] = []
        failed_attempts: list[_PipelineAttemptResult] = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {
                executor.submit(
                    self._run_candidate,
                    manifest,
                    request=request,
                    output_mode=output_mode,
                    trace_id=trace_id,
                    rank_index=index,
                ): manifest.provider_id
                for index, manifest in enumerate(manifests)
            }
            for future in as_completed(future_map):
                attempt = future.result()
                if attempt.candidate is not None:
                    results.append(attempt.candidate)
                else:
                    failed_attempts.append(attempt)
        results.sort(key=lambda item: item.rank_index)
        failed_attempts.sort(key=lambda item: item.manifest.provider_id)
        return results, failed_attempts

    @staticmethod
    def _envelope_bool_constraint(task_envelope: TaskEnvelopeV1 | None, key: str) -> bool | None:
        if task_envelope is None:
            return None
        if key not in task_envelope.model_constraints:
            return None
        return bool(task_envelope.model_constraints.get(key))

    @staticmethod
    def _requested_swarm_size(*, task_envelope: TaskEnvelopeV1 | None, swarm_size: int | None) -> int | None:
        if swarm_size is not None:
            return int(swarm_size)
        if task_envelope is None:
            return None
        raw = task_envelope.model_constraints.get("swarm_size")
        if raw in {None, ""}:
            return None
        return max(1, int(raw))

    def _execution_manifests(
        self,
        plan: ProviderRoutingPlan,
    ) -> tuple[list[ModelProviderManifest], int, list[str]]:
        manifests = list(plan.candidates)
        if not manifests or not plan.capability_truth:
            return manifests, max(1, min(plan.swarm_size, len(manifests) or 1)), []
        capability_by_id = {item.provider_id: item for item in plan.capability_truth}
        ready: list[ModelProviderManifest] = []
        degraded: list[ModelProviderManifest] = []
        for manifest in manifests:
            capability = capability_by_id.get(manifest.provider_id)
            if capability is None:
                ready.append(manifest)
                continue
            queue_pressure = float(capability.queue_depth) / float(max(1, capability.max_safe_concurrency))
            if queue_pressure >= 1.0:
                degraded.append(manifest)
            else:
                ready.append(manifest)
        if ready and degraded:
            selected = ready[: max(1, min(plan.swarm_size, len(ready)))]
            return selected, max(1, min(plan.swarm_size, len(selected))), ["skipped_saturated_candidates"]
        if degraded:
            selected = degraded[:1]
            return selected, 1, ["reduced_to_single_degraded_lane"]
        return manifests, max(1, min(plan.swarm_size, len(manifests))), []

    def _execution_metadata(
        self,
        plan: ProviderRoutingPlan,
        *,
        results: list[_PipelineCandidateResult],
        failed_attempts: list[_PipelineAttemptResult],
    ) -> dict[str, Any]:
        executed_manifests, _, backoff_notes = self._execution_manifests(plan)
        return {
            "effective_swarm_size": len(results),
            "failed_attempt_count": len(failed_attempts),
            "capacity_backoff_applied": bool(backoff_notes) or len(executed_manifests) < len(plan.candidates),
            "capacity_backoff_notes": backoff_notes,
        }

    def _run_candidate(
        self,
        manifest: ModelProviderManifest,
        *,
        request: ModelRequest,
        output_mode: str,
        trace_id: str | None,
        rank_index: int,
    ) -> _PipelineAttemptResult:
        try:
            if circuit_is_open(manifest.provider_id):
                return _PipelineAttemptResult(
                    manifest=manifest,
                    candidate=None,
                    status="circuit_open",
                    error="provider circuit is open",
                )
            adapter = self.registry.build_adapter(manifest)
            health = dict(invoke_provider_execution_boundary(adapter, "health_check") or {})
            if not bool(health.get("ok", False)):
                error = str(health.get("error") or "health_check_failed")
                record_provider_failure(manifest.provider_id, error=error)
                return _PipelineAttemptResult(
                    manifest=manifest,
                    candidate=None,
                    status="health_check_failed",
                    error=error,
                    health=health,
                )
            response = invoke_provider_execution_boundary(adapter, "invoke", request)
            raw_output = str(response.output_text or "").strip()
            if not raw_output:
                record_provider_failure(manifest.provider_id, error="empty_output")
                return _PipelineAttemptResult(
                    manifest=manifest,
                    candidate=None,
                    status="empty_output",
                    error="empty_output",
                    health=health,
                )
            validation = validate_provider_output(
                provider_id=manifest.provider_id,
                output_mode=output_mode,
                raw_text=raw_output,
                trace_id=trace_id,
            )
            normalized_output = str(validation.normalized_text or raw_output).strip()
            if not normalized_output:
                record_provider_failure(manifest.provider_id, error="normalized_output_empty")
                return _PipelineAttemptResult(
                    manifest=manifest,
                    candidate=None,
                    status="normalized_output_empty",
                    error="normalized_output_empty",
                    health=health,
                )
            trust = output_trust_score(
                manifest=manifest,
                raw_confidence=float(response.confidence or 0.5),
                contract_ok=validation.ok,
                trust_penalty=validation.trust_penalty,
                freshness_score=1.0,
            )
            confidence = max(0.0, min(1.0, float(response.confidence or 0.5) - validation.trust_penalty))
            record_provider_success(manifest.provider_id)
            return _PipelineAttemptResult(
                manifest=manifest,
                candidate=_PipelineCandidateResult(
                    manifest=manifest,
                    output_text=normalized_output,
                    structured_output=validation.structured_output,
                    confidence=confidence,
                    trust_score=trust,
                    validation_state="valid" if validation.ok else "contract_failed",
                    license_metadata=adapter.get_license_metadata(),
                    rank_index=rank_index,
                ),
                status="completed",
                health=health,
            )
        except Exception as exc:
            message = str(exc)
            record_provider_failure(
                manifest.provider_id,
                error=message or "invoke_failed",
                timeout="timeout" in message.lower(),
            )
            return _PipelineAttemptResult(
                manifest=manifest,
                candidate=None,
                status="invoke_failed",
                error=message or "invoke_failed",
            )

    def summarize(self, text: str, *, trace_id: str | None = None) -> TeacherCandidate | None:
        return self.run(task_kind="summarization", prompt=text, trace_id=trace_id)

    def classify(self, text: str, *, trace_id: str | None = None) -> TeacherCandidate | None:
        return self.run(task_kind="classification", prompt=text, trace_id=trace_id, output_mode="json_object")

    def generate_candidate_shard(self, text: str, *, trace_id: str | None = None) -> TeacherCandidate | None:
        return self.run(task_kind="candidate_shard_generation", prompt=text, trace_id=trace_id, output_mode="json_object")

    def normalization_assist(self, text: str, *, trace_id: str | None = None) -> TeacherCandidate | None:
        return self.run(task_kind="normalization_assist", prompt=text, trace_id=trace_id, output_mode="summary_block")

    @staticmethod
    def as_candidate_record(candidate: TeacherCandidate) -> dict[str, Any]:
        return asdict(candidate)
