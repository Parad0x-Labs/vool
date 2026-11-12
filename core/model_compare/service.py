"""Bounded comparisons through real adapters and the existing spend authority."""
from __future__ import annotations

import json
import math
import os
import threading
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from core.model_compare.prepared import (
    PreparedReplay,
    format_compatible,
    has_images,
    input_digest,
    source_payload,
)

_LOCK = threading.RLock()


@dataclass
class Job:
    home: str
    source_id: str
    source_sha: str
    models: list[str]
    ident: str = field(default_factory=lambda: uuid.uuid4().hex)
    created: float = field(default_factory=time.monotonic)
    state: str = "running"
    results: list[dict] = field(default_factory=list)
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)


_JOBS: dict[str, Job] = {}


def models_for(preview_id: str) -> list[dict]:
    from core.chat_attachments import manifest_supports_images
    from core.disclosure.local_choice import local_models
    from core.disclosure.wire import get_preview
    from core.model_registry import ModelRegistry
    from core.model_selection_policy import charges_the_user, provider_cost_class

    source = get_preview(preview_id)
    if not source.ready or not source.sent or source.scope_version is None:
        raise ValueError("This answer has no comparable prepared request.")
    original = source_payload(source)
    images = has_images(original)
    local = {(m["provider"], m["id"]) for m in local_models(images=images)}
    native = source.request_format == 'ollama'
    result = []
    for manifest in ModelRegistry().list_manifests(enabled_only=True):
        if manifest.provider_id == source.provider:
            continue
        reason = ""
        if manifest.adapter_type in {'local_subprocess', 'local_model_path'}:
            from core.model_compare.subprocess_replay import availability
            reason = availability(manifest, images=images) or format_compatible(original, source_native=native, target_native=False)
        elif manifest.adapter_type not in {"openai_compatible", "local_qwen_provider", "cloud_fallback_provider"}:
            reason = "This model uses a different request format."
        elif (incompatible := format_compatible(original, source_native=native,
                target_native=ModelRegistry().build_adapter(manifest)._uses_native_ollama_chat())):
            reason = incompatible
        elif images and manifest_supports_images(manifest) is not True:
            reason = "This model cannot read the images in this task."
        elif provider_cost_class(manifest) == "free_local" and (manifest.provider_name, manifest.model_name) not in local:
            reason = "This local model is not ready for this task."
        elif provider_cost_class(manifest) == "remote_unknown":
            reason = "This model's spending policy is unknown."
        result.append({"id": manifest.provider_id, "model": manifest.model_name,
                       "provider": manifest.provider_name, "paid": charges_the_user(manifest),
                       "available": not reason, "reason": reason})
    return result


def start(preview_id: str, digest: str, models: Any) -> dict:
    from core.disclosure.wire import _home, get_preview

    if not isinstance(models, list) or not 1 <= len(models) <= 2 or any(not isinstance(m, str) for m in models) or len(set(models)) != len(models):
        raise ValueError("Choose one or two different models.")
    source = get_preview(preview_id)
    if source.digest != digest:
        raise ValueError("This prepared request changed.")
    available = {m["id"] for m in models_for(preview_id) if m["available"]}
    if not set(models) <= available:
        raise ValueError("One of these models is unavailable for this prepared request.")
    with _LOCK:
        for existing in _JOBS.values():
            if (existing.home == _home() and existing.source_id == preview_id and existing.source_sha == digest
                    and set(existing.models) == set(models) and time.monotonic() - existing.created <= 300):
                return {"id": existing.ident, "state": existing.state}
        for ident, job in list(_JOBS.items()):
            if time.monotonic() - job.created > 300 and job.state != 'running':
                del _JOBS[ident]
        if len(_JOBS) >= 32 or sum(j.state == 'running' for j in _JOBS.values()) >= 2:
            raise ValueError("Finish an open comparison first.")
        job = Job(_home(), preview_id, digest, list(models))
        _JOBS[job.ident] = job
    threading.Thread(target=_run, args=(job,), name="model-comparison", daemon=True).start()
    return {"id": job.ident, "state": job.state}


def _run(job: Job) -> None:
    from core.disclosure.wire import get_preview
    from core.model_registry import ModelRegistry

    try:
        for ident in job.models:
            if job.cancel_event.is_set():
                break
            source = get_preview(job.source_id)
            manifest = next((m for m in ModelRegistry().list_manifests(enabled_only=True) if m.provider_id == ident), None)
            if manifest is None:
                result = {"model": ident, "state": "refused", "error": "This model is no longer configured."}
            else:
                result = _run_one(job, source, manifest)
            with _LOCK:
                if job.state != 'running':
                    return
                job.results.append(result)
        with _LOCK:
            if job.state == 'running':
                job.state = "cancelled" if job.cancel_event.is_set() else "done"
    except Exception:
        with _LOCK:
            job.results.clear()
            job.state = "expired"


def _run_one(job: Job, source: Any, manifest: Any) -> dict:
    from adapters.base_adapter import ModelRequest
    from core import usage_meter
    from core.disclosure.wire import REVIEW_WAITS_KEY
    from core.model_compare.measurements import reported_bill, reported_count
    from core.model_registry import ModelRegistry
    from core.model_selection_policy import charges_the_user, reported_cost_class
    from core.paid_call_reservation import (
        release_owner_pick_paid_call,
        reserve_owner_pick_paid_call,
        settle_owner_pick_paid_call,
    )
    from core.remote_fetch_policy import remote_fetch_policy_scope
    from core.request_trust import OWNER_LOCAL_KEY

    original = source_payload(source)
    configured_timeout = float(manifest.runtime_config.get('timeout_seconds') or 60)
    replay = PreparedReplay(job.source_id, job.source_sha, manifest.provider_id, manifest.model_name,
                            min(60, max(.001, configured_timeout)))
    call_id = uuid.uuid4().hex
    context = {**source.replay_context, OWNER_LOCAL_KEY: True, "chat_id": source.chat_id,
               "session_id": source.chat_id, "project_id": source.project_id,
               "runtime_session_id": source.chat_id, "request_id": call_id,
               "task_id": 'compare-' + job.ident, "turn_id": call_id, "cancel_event": job.cancel_event,
               # The comparison runs on its own job thread, never a request worker, and its
               # previews are answered from the chat page's comparison view.
               REVIEW_WAITS_KEY: "wait"}
    context.pop('_cloud_projected', None)
    max_tokens = int(original.get('max_tokens') or original.get('max_completion_tokens') or (original.get('options') or {}).get('num_predict') or 512)
    if max_tokens <= 0:
        return {"model": manifest.model_name, "state": "refused", "error": "This prepared task has no bounded answer size."}
    request = ModelRequest(task_kind='summarize', prompt='', messages=original.get('messages', []),
        max_output_tokens=max_tokens, allow_provider_retry=False, allow_response_control_retry=False,
        trace_id=call_id, model_call_id=call_id, cancel_check=job.cancel_event.is_set, context={"chat_id": source.chat_id, "project_id": source.project_id},
        metadata={**{k: v for k, v in context.items() if k in {'local_only', 'local_only_mode', 'cloud_disabled'}},
                  '_comparison_prepared': replay})
    result: dict[str, Any] = {"provider": manifest.provider_name, "model": manifest.model_name,
                              "model_call_id": call_id, "state": "running", "answer": ""}
    authorization = None
    usage: dict = {}
    state = 'failed'
    usage_recorded = False
    try:
        with remote_fetch_policy_scope(context):
            from core.auto_local_only_mode import turn_is_local_only
            from core.model_selection_policy import provider_cost_class
            if (manifest.metadata.get('deployment_class') == 'cloud' or provider_cost_class(manifest) != 'free_local') and turn_is_local_only(context):
                raise ValueError("Local Only is on. This comparison stayed on your computer.")
            if manifest.metadata.get('deployment_class') == 'cloud' or provider_cost_class(manifest) != 'free_local':
                from core.policy_engine import get
                if get('network.outbound_enabled', False) is not True:
                    raise ValueError("Online access is off. Nothing was sent.")
            if charges_the_user(manifest):
                denial: dict[str, str] = {}
                authorization = reserve_owner_pick_paid_call(manifest=manifest,
                    task=SimpleNamespace(task_id=context['task_id'], prompt_tokens=len(source.body), max_output_tokens=max_tokens),
                    source_context=context, call_role='answer_generation', denial=denial)
                if authorization is None:
                    result['refusal_reason'] = 'spending'
                    raise ValueError("Spending limit refused this comparison: " + denial.get('reason', 'unavailable'))
                request.model_call_id = authorization.model_call_id
                result['spend_call_id'] = authorization.model_call_id
            adapter = ModelRegistry().build_adapter(manifest)
            output = []
            finish_reason = ''
            tool_requested = False
            if adapter.supports_streaming():
                from core.provider_execution_boundary import invoke_provider_execution_boundary
                for chunk in invoke_provider_execution_boundary(adapter, 'stream_text_task', request):
                    output.append(chunk.delta_text)
                    from core.model_compare.tool_observation import requested_tool
                    tool_requested |= requested_tool(chunk.raw_event) or (chunk.provider_metadata or {}).get('vool_tool_request_observed') is True
                    finish_reason = chunk.finish_reason or finish_reason
                    if chunk.usage is not None:
                        usage = dict(chunk.usage)
                    if sum(map(len, output)) > 200000:
                        raise ValueError("The comparison answer exceeded its size limit.")
                result['answer'] = ''.join(output)
                if tool_requested or finish_reason not in {'stop', 'end_turn', 'eos', 'completed'}:
                    raise ValueError("This model's answer was incomplete or requested a tool. No tool was run.")
            else:
                from core.provider_execution_boundary import invoke_provider_execution_boundary
                response = invoke_provider_execution_boundary(adapter, 'run_text_task', request)
                result['answer'], usage = response.output_text, dict(response.usage or {})
                from core.model_compare.tool_observation import requested_tool
                if response.error or response.tool_calls or requested_tool(response.raw_response) or response.finish_reason not in {'stop', 'end_turn', 'eos', 'completed'}:
                    if result['answer']:
                        result['partial'] = True
                    raise ValueError("This model did not return a complete text answer. No tool was run.")
            if job.cancel_event.is_set():
                raise ValueError("The comparison was stopped.")
            if not result['answer'].strip():
                raise ValueError("This model returned no text answer.")
            state = 'completed'
    except Exception as exc:
        # A stable type and controlled refusal are sufficient; provider exception
        # strings can contain URLs or credentials and never enter this owner view.
        result['error'] = str(exc) if isinstance(exc, ValueError) else 'This model failed or timed out (' + type(exc).__name__ + ').'
        if 'output' in locals() and output:
            result['answer'] = ''.join(output)
            result['partial'] = True
        from core.disclosure.wire import DisclosureRefused
        from core.egress_gate import EgressRefused
        refused = isinstance(exc, (ValueError, EgressRefused, DisclosureRefused))
        state = 'cancelled' if job.cancel_event.is_set() else 'refused' if refused else 'failed'
    finally:
        timing = request.metadata.get('_disclosure_timing')
        if timing is not None:
            timing.finish()
        actual = reported_bill(usage)
        prompt = reported_count(usage, 'prompt_eval_count', 'prompt_tokens', 'input_tokens')
        completion = reported_count(usage, 'eval_count', 'completion_tokens', 'output_tokens')
        usage_recorded = usage_meter.record_usage(provider_id=manifest.provider_id, model_id=manifest.model_name,
            cost_class=reported_cost_class(manifest), prompt_tokens=prompt or 0, output_tokens=completion or 0, usd_actual=actual,
            prompt_tokens_reported=prompt is not None,
            output_tokens_reported=completion is not None,
            usage_entry_id=call_id)
        if not usage_recorded:
            result['accounting_error'] = 'Usage measurements could not be saved.'
        if authorization is not None:
            if state == 'completed' or actual is not None:
                settle_owner_pick_paid_call(authorization, actual_usd=actual, source_context=context,
                                           call_role='answer_generation', invocation_id=call_id)
            else:
                release_owner_pick_paid_call(authorization, reason='call_failed' if timing and timing.dispatched is not None else 'pin_not_dispatched',
                                            source_context=context, call_role='answer_generation')
        settled_bill = None
        if authorization is not None:
            from core.model_spend_ledger import get_spend_reservation
            try:
                reservation = get_spend_reservation(authorization.model_call_id)
                result['spend_state'] = reservation.status if reservation is not None else 'unknown'
                if (reservation is not None and reservation.status in {'settled', 'cap_breached'}
                        and math.isfinite(reservation.actual_usd) and reservation.actual_usd >= 0):
                    settled_bill = reservation.actual_usd
            except Exception:
                result['spend_state'] = 'unknown'
                result['accounting_error'] = 'The final spending record could not be read.'
        result['sent'] = bool(timing and timing.dispatched is not None)
        result['state'] = state
        try:
            usage_meter.record_call_timing(model_call_id=call_id, usage_entry_id=call_id if usage_recorded else None,
                provider_id=manifest.provider_id, model_id=manifest.model_name, state=state,
                **(timing.public() if timing is not None else {'first_token_ms': None, 'total_ms': None}))
            result['measured'] = usage_meter.measured_call(call_id)
        except Exception:
            result['measured'] = None
            if usage_recorded:
                with suppress(Exception):
                    result['measured'] = usage_meter.measured_usage_entry(call_id)
            result['accounting_error'] = 'Usage measurements could not be read from the local ledger.'
        if settled_bill is not None:
            if result['measured'] is None:
                result['measured'] = {'first_token_ms': None, 'total_ms': None, 'state': 'timing_unrecorded',
                    'prompt_tokens': None, 'output_tokens': None, 'billed_usd': None}
            if result['measured']['billed_usd'] is None:
                result['measured']['billed_usd'] = settled_bill
                result['measured']['billed_source'] = 'spend_ledger'
        preview_id = request.metadata.get('_disclosure_preview_id')
        if preview_id:
            from core.disclosure.wire import get_preview
            try:
                actual_input = json.loads(get_preview(preview_id).body)
                result['input_sha256'] = input_digest(actual_input)
                result['same_input'] = result['input_sha256'] == input_digest(original)
            except (KeyError, ValueError):
                result['same_input'] = None
        elif request.metadata.get('_comparison_input_sha256'):
            result['input_sha256'] = request.metadata['_comparison_input_sha256']
            result['same_input'] = result['input_sha256'] == input_digest(original)
    return result


def read(ident: str) -> dict:
    from core.disclosure.wire import _home, get_preview

    with _LOCK:
        job = _JOBS.get(ident)
    if job is None or job.home != _home() or time.monotonic() - job.created > 600:
        raise KeyError('comparison expired')
    source = get_preview(job.source_id)
    if source.digest != job.source_sha:
        raise KeyError('comparison expired')
    from core.model_compare.original import measurements
    original_measured = measurements(source)
    with _LOCK:
        return {"id": job.ident, "state": job.state, "original": source.output_text,
                "original_measured": original_measured,
                "original_model": json.loads(source.body).get('model'), "stopping": job.cancel_event.is_set() and job.state == "running", "results": [dict(r) for r in job.results]}


def prefer(ident: str, model: str) -> dict:
    from core.runtime_paths import active_config_home_dir

    job = read(ident)
    valid = {job['original_model'], *(r['model'] for r in job['results'] if r['state'] == 'completed')}
    if model not in valid:
        raise ValueError('Choose an answer that completed.')
    path = active_config_home_dir() / 'model-comparison-preference.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({'model': model, 'chosen_at': time.time()})
    with _LOCK:
        fd = os.open(path.with_suffix('.tmp'), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write(payload)
        path.with_suffix('.tmp').replace(path)
        path.chmod(0o600)
    return {'ok': True, 'routing_changed': False}


def invalidate_source(preview_id: str) -> None:
    with _LOCK:
        for job in _JOBS.values():
            if job.source_id == preview_id:
                job.results.clear()
                job.state = 'expired'
                job.cancel_event.set()


def cancel(ident: str) -> dict:
    read(ident)
    with _LOCK:
        _JOBS[ident].cancel_event.set()
    return {'ok': True, 'state': 'stopping'}
