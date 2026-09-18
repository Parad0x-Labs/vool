from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import time
from dataclasses import replace
from typing import Any
from urllib.parse import urlparse

from adapters.base_adapter import ModelAdapter, ModelRequest, ModelResponse, ModelStreamChunk
from core import provider_http as requests
from core.cloud_provider_contract import CloudToolCall, CloudToolDefinition
from core.cloud_tool_call_contract import (
    DuplicateToolCallError,
    MalformedToolArgumentsError,
    ToolCallParseError,
    UnknownToolNameError,
    canonical_tool_call_text,
    openai_tool_payload,
    parse_native_tool_calls,
)
from core.compute_mode import get_active_compute_budget
from core.execution_requirements import assert_envelope_carries_tools
from core.local_model_admission import local_model_slot
from core.memory_prompt_builder import apply_memory_prefix_to_messages, build_memory_prefix_for_request
from core.model_output_guard import scrub_foreign_markers, strip_reasoning_block
from core.normalized_provider_result import (
    EmptyProviderResponseError,
    MalformedProviderResponseError,
)
from core.prompt_budget import PromptBudgetExceededError, fit_messages_to_context_window
from core.prompt_debug import dump_outbound_prompt
from core.provider_invocation_gateway import (
    seal_direct_provider_invocation,
    seal_provider_invocation,
)
from core.provider_verification import response_body_facts, response_header_facts
from core.runtime_flags import flag_enabled
from core.tool_call_recovery import (
    ToolCallResolution,
    canonical_intent_text,
    resolve_tool_calls,
)

logger = logging.getLogger("vool.model_adapter")


def _reasoning_disabled(request: Any) -> bool:
    """Whether this request declared that it must not spend its output budget reasoning.

    Reads the TYPED policy (`ModelRequest.reasoning_mode`), not a task name. The audit flag is
    still honoured for one reason: `metadata={"workspace_audit_turn": True}` is stamped by
    `core/memory_first_router._audit_turn_metadata` onto turns this adapter does not construct, and
    dropping it would silently re-arm the 180s think-timeout on the single-shot audit lane. It is a
    compatibility read of an existing stamp, not a second task-name check to be extended — a new
    caller sets `reasoning_mode` and nothing else.

    Never raises and never guesses: a request carrying neither is an ordinary turn.
    """
    from core.model_request_policy import REASONING_DISABLED, request_reasoning_mode

    if request_reasoning_mode(request) == REASONING_DISABLED:
        return True
    try:
        return bool(dict(getattr(request, "metadata", None) or {}).get("workspace_audit_turn"))
    except Exception:
        return False


# Headroom a thinking model needs before it starts answering. qwen3:4b was measured spending
# ~800-2000 tokens reasoning on a one-sentence question, so a budget sized for the answer alone
# is consumed entirely by reasoning and the reply comes back empty.
_THINKING_RESERVE_TOKENS = 2048

# Floor for a cloud turn that carries tools. A reasoning model writes its chain of thought into
# the same budget as the call, so a budget sized for the call alone is consumed before the call is
# reached. Measured on nemotron-3-nano: 512 tokens -> finish_reason "length" and no call; 3000 ->
# a clean tool_calls response. Sized above the observed reasoning length with headroom, and
# applied only to tool turns so ordinary chat keeps the caller's budget.
_CLOUD_TOOL_CALL_FLOOR_TOKENS = 3000

# Native-tool capability measured from a loopback server, keyed by (base_url, model). Process-scoped
# on purpose: a server's capability does not change under us, and re-probing per turn would tax every
# request to re-learn the same fact.
_NATIVE_TOOL_PROBE_CACHE: dict[str, bool] = {}


def _request_timeout(read_timeout: float) -> tuple[float, float]:
    """(connect, read) so an unreachable Ollama fails fast on connect instead of blocking the
    endpoint for the full generous read budget a cold generation needs."""
    return (min(10.0, read_timeout), read_timeout)


def _assert_response_timely(request: ModelRequest, response: Any) -> None:
    """Expiry revokes publication even when transport returned without timing out."""
    from core.provider_call_deadline import ProviderCallDeadlineExceededError, effective_timeout_seconds

    try:
        effective_timeout_seconds(request, 0.001)
    except ProviderCallDeadlineExceededError:
        response.close()
        raise


def _strict_tool_certification_loopback(base_url: str) -> bool:
    """Certification is stricter than the legacy fast probe: wildcard binds are not targets."""

    try:
        parsed = urlparse(str(base_url or "").strip())
        return parsed.scheme in {"http", "https"} and str(parsed.hostname or "").lower() in {
            "127.0.0.1",
            "localhost",
            "::1",
        }
    except (TypeError, ValueError):
        return False


class ProviderCredentialUnavailableError(RuntimeError):
    """A lane that REQUIRES a dispatch credential has none usable, refused BEFORE the wire.

    Measured live 2026-09-18: a lane whose saved OpenRouter key read as absent still sent the
    completion request unauthenticated, the provider answered ``401 No cookie auth credentials
    found``, and the surface reported a model failure. A stored label/index/manifest is not a
    usable credential: when this lane declares a BYOK credential slot, a missing or unreadable
    secret stops the dispatch HERE, as a typed refusal whose text names the stable
    ``provider_credential_unavailable`` code, so routing, the degraded-answer surface and the
    fault record all carry the actual cause instead of a remote rejection that never happened.
    """

    code = "provider_credential_unavailable"


def _credential_unavailable(credential_key: str, state: str, cause: str = "") -> ProviderCredentialUnavailableError:
    detail = f" ({cause})" if cause else ""
    return ProviderCredentialUnavailableError(
        f"provider_credential_unavailable: saved credential '{credential_key}' is {state}{detail}; "
        "nothing was sent — restore it under Settings → API Keys, then send the request again"
    )


class OpenAICompatibleAdapter(ModelAdapter):
    def supports_streaming(self) -> bool:
        return True

    def validate_runtime(self) -> list[str]:
        warnings: list[str] = []
        base_url = str(self.manifest.runtime_config.get("base_url") or "").strip()
        if not base_url:
            warnings.append(f"{self.manifest.provider_id}: missing runtime_config.base_url")
        return warnings

    def health_check(self) -> dict[str, Any]:
        try:
            # Health is a real keyed request (Test): it travels on the SAME coherent dispatch
            # binding as completion — a stale lane freeze refuses here rather than sending the
            # current key to the lane's old destination.
            base_url, dispatch_key = self._dispatch_binding()
        except Exception as exc:
            from core.cloud_providers import StaleProviderBindingError

            payload = {"ok": False, "provider_id": self.manifest.provider_id, "error": str(exc)}
            if isinstance(exc, StaleProviderBindingError):
                # Typed for the route authority (the health block surfaces `reason` as the
                # failure code); health_check itself keeps its no-raise contract.
                payload["reason"] = "stale_provider_binding"
            if isinstance(exc, ProviderCredentialUnavailableError):
                # Same typed surface as the stale binding: the route authority reads `reason`,
                # and the error text carries the stable code plus the recovery action.
                payload["reason"] = ProviderCredentialUnavailableError.code
            return payload
        if not base_url:
            return {"ok": False, "provider_id": self.manifest.provider_id, "error": "missing_base_url"}
        health_path = str(self.manifest.runtime_config.get("health_path") or "/v1/models")
        timeout_seconds = float(self.manifest.runtime_config.get("health_timeout_seconds") or 3.0)
        try:
            response = requests.get(
                f"{base_url}{health_path}",
                headers=self._headers(base_url=base_url, api_key=dispatch_key),
                timeout=timeout_seconds,
            )
            response.raise_for_status()
            return {"ok": True, "provider_id": self.manifest.provider_id, "status_code": response.status_code}
        except Exception as exc:
            return {"ok": False, "provider_id": self.manifest.provider_id, "error": str(exc)}

    def prewarm(self) -> dict[str, Any]:
        prewarm_config = dict(self.manifest.runtime_config.get("prewarm") or {})
        if not prewarm_config:
            return super().prewarm()

        strategy = str(prewarm_config.get("strategy") or "").strip().lower()
        if strategy not in {"ollama_generate", "ollama_chat"}:
            return {
                "ok": False,
                "provider_id": self.manifest.provider_id,
                "status": "error",
                "error": f"unsupported_prewarm_strategy:{strategy or 'missing'}",
            }

        runtime_family = str(self.manifest.metadata.get("runtime_family") or "").strip().lower()
        if runtime_family != "ollama":
            return {
                "ok": True,
                "provider_id": self.manifest.provider_id,
                "status": "skipped",
                "reason": "not_ollama_runtime",
                "strategy": strategy,
            }

        base_url = str(self.manifest.runtime_config.get("base_url") or "").rstrip("/")
        if not base_url:
            return {
                "ok": False,
                "provider_id": self.manifest.provider_id,
                "status": "error",
                "error": "missing_base_url",
                "strategy": strategy,
            }

        endpoint, payload = self._ollama_prewarm_request(
            base_url=base_url,
            strategy=strategy,
            prewarm_config=prewarm_config,
        )

        timeout_seconds = float(
            prewarm_config.get("timeout_seconds")
            or self.manifest.runtime_config.get("health_timeout_seconds")
            or 15.0
        )
        try:
            headers = self._headers()
            permit = seal_direct_provider_invocation(
                provider_id=self.manifest.provider_id,
                model_id=str(payload.get("model") or ""),
                operation="prewarm",
                payload=payload,
                request_id=(
                    f"prewarm:{self.manifest.provider_id}:{strategy}"
                ),
                header_names=tuple(headers),
            )
            response = requests.post(
                endpoint,
                json=permit.consume(),
                headers=headers,
                timeout=timeout_seconds,
            )
            response.raise_for_status()
            data = response.json()
            return {
                "ok": True,
                "provider_id": self.manifest.provider_id,
                "status": "prewarmed",
                "strategy": strategy,
                "keep_alive": payload["keep_alive"],
                "load_duration": data.get("load_duration"),
                "total_duration": data.get("total_duration"),
            }
        except requests.exceptions.Timeout:
            return {
                "ok": True,
                "provider_id": self.manifest.provider_id,
                "status": "timed_out",
                "strategy": strategy,
                "reason": "cold_start_timeout",
                "keep_alive": payload["keep_alive"],
                "timeout_seconds": timeout_seconds,
            }
        except Exception as exc:
            return {
                "ok": False,
                "provider_id": self.manifest.provider_id,
                "status": "error",
                "strategy": strategy,
                "error": str(exc),
            }

    def _ollama_prewarm_request(
        self,
        *,
        base_url: str,
        strategy: str,
        prewarm_config: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        keep_alive = prewarm_config.get("keep_alive", "10m")
        native_base_url = _native_ollama_base_url(base_url)
        if strategy == "ollama_chat":
            message = prewarm_config.get("message")
            if message is None:
                message = prewarm_config.get("prompt")
            options = dict(prewarm_config.get("options") or {})
            options.setdefault("num_predict", 1)
            # Prewarm exists to have the SERVING runner resident; a prewarm on a differently
            # keyed runner is 9 GB loaded twice. Configured values win, the seam fills the rest.
            for key, value in self._ollama_runner_options(None).items():
                options.setdefault(key, value)
            payload: dict[str, Any] = {
                "model": self.manifest.model_name,
                "messages": [{"role": "user", "content": " " if message is None else str(message)}],
                "stream": False,
                "keep_alive": keep_alive,
                "options": options,
            }
            think_flag = self._ollama_think_flag()
            if think_flag is not None:
                payload["think"] = think_flag
            return f"{native_base_url}/api/chat", payload

        prompt = prewarm_config.get("prompt")
        payload = {
            "model": self.manifest.model_name,
            "prompt": " " if prompt is None else str(prompt),
            "stream": False,
            "keep_alive": keep_alive,
        }
        if "raw" in prewarm_config:
            payload["raw"] = bool(prewarm_config.get("raw"))
        if isinstance(prewarm_config.get("options"), dict) and prewarm_config.get("options"):
            payload["options"] = dict(prewarm_config["options"])
        return f"{native_base_url}/api/generate", payload

    def _assert_local_only_allows(self, request: ModelRequest | None = None) -> None:
        """Refuse to put bytes on the wire for a non-local endpoint under Local Only.

        This adapter serves BOTH lanes — a loopback Ollama and the BYOK OpenRouter burst share one
        class, differing only by `runtime_config.base_url`. So the deepest check cannot ask what
        kind of adapter this is; it asks where this call is actually going, which is the only
        question that cannot be answered wrongly by a mislabelled manifest.

        The mode is read from the request's own metadata (stamped by
        `MemoryFirstRouter._invoke_manifest`) because the adapter runs on whichever worker thread
        the conductor or planner put it on, where ambient state set on the API thread is absent.
        """
        from core.auto_local_only_mode import assert_endpoint_allowed

        metadata = getattr(request, "metadata", None) if request is not None else None
        assert_endpoint_allowed(
            str(self.manifest.runtime_config.get("base_url") or ""),
            lane=str(self.manifest.provider_id or "openai_compatible"),
            source_context=metadata if isinstance(metadata, dict) else None,
        )

    def run_text_task(self, request: ModelRequest) -> ModelResponse:
        return self._invoke_http(request, force_json=False)

    def run_structured_task(self, request: ModelRequest) -> ModelResponse:
        return self._invoke_http(request, force_json=True)

    def stream_text_task(self, request: ModelRequest):
        self._assert_local_only_allows(request)
        if self._uses_native_ollama_chat():
            return self._stream_ollama_chat(request)
        return self._stream_openai_compatible(request)

    def invoke(self, request: ModelRequest) -> ModelResponse:
        force_json = request.output_mode in {"json_object", "action_plan", "tool_intent", "summary_block"}
        return self._invoke_http(request, force_json=force_json)

    def _invoke_http(self, request: ModelRequest, *, force_json: bool) -> ModelResponse:
        self._assert_local_only_allows(request)
        if self._uses_native_ollama_chat():
            return self._invoke_ollama_chat(request, force_json=force_json)
        return self._invoke_openai_compatible(request, force_json=force_json)

    def _invoke_openai_compatible(self, request: ModelRequest, *, force_json: bool) -> ModelResponse:
        if request.is_cancelled():
            raise RuntimeError("model_call_cancelled")
        # ONE coherent binding for the whole dispatch (URL, headers, and every same-lane retry
        # below): a replaceable custom endpoint cannot combine a stale manifest destination
        # with the current key (review F3), and a mid-dispatch replacement cannot split this
        # request across two pairs.
        base_url, dispatch_key = self._dispatch_binding()
        if not base_url:
            raise RuntimeError(f"{self.manifest.provider_id}: missing runtime_config.base_url")
        api_path = str(self.manifest.runtime_config.get("api_path") or "/v1/chat/completions")
        payload = self._build_openai_payload(request, force_json=force_json, stream=False)
        # Final pre-invocation boundary: the ACTUAL built envelope, not the request or the ranked
        # manifest's declared capabilities. Raises RequiredToolsNotOfferedError, caught distinctly
        # by the router (never surfaced as ordinary model prose), if this call is about to leave
        # for the wire with neither native tools nor a structured-output fallback despite the turn
        # requiring one.
        assert_envelope_carries_tools(
            tools_required=bool(getattr(request, "tools_required", False)),
            offered_tool_count=len(request.tools or ()),
            native_tools_in_envelope=bool(payload.get("tools")),
            structured_fallback_in_envelope=bool(payload.get("response_format")),
            lane_name=self.manifest.provider_id,
        )
        from core.provider_call_deadline import effective_timeout_seconds

        timeout_seconds = effective_timeout_seconds(
            request,
            float(self.manifest.runtime_config.get("timeout_seconds") or 30.0),
        )
        # Same gate as _build_openai_payload: only a request that actually carries tool definitions
        # takes the native tool-calling path. A tool_intent turn with no tools is a structured-output
        # turn (json_schema), not a malformed tool request.
        native_tools = self._native_tools(request) if getattr(request, "tools", None) else ()
        max_attempts = 2 if native_tools and self._is_verified_free_openrouter_lane() else 1
        # The mode the RESPONSE carries. Normally the request's, but a repaired prose tool call is a
        # call and not an answer, and only this layer knows a repair happened.
        response_output_mode = request.output_mode
        headers = self._headers(base_url=base_url, api_key=dispatch_key)
        data: dict[str, Any] = {}
        output_text = ""
        native_tool_calls: tuple[CloudToolCall, ...] = ()
        for attempt_index in range(max_attempts):
            timeout_seconds = effective_timeout_seconds(request, timeout_seconds)
            permit = seal_provider_invocation(
                request=request,
                provider_id=self.manifest.provider_id,
                model_id=self.manifest.model_name,
                operation="structured" if force_json else "chat",
                payload=payload,
                header_names=tuple(headers),
            )
            response = requests.post(
                f"{base_url}{api_path}",
                json=permit.consume(),
                headers=headers,
                timeout=_request_timeout(timeout_seconds),
            )
            _assert_response_timely(request, response)
            _raise_for_status_with_cause(response)
            if request.is_cancelled():
                raise RuntimeError("model_call_cancelled")
            data = response.json()
            if not native_tools:
                # A cloud reasoning model that has no separate reasoning channel writes its
                # `<think>` block into the content. Dropped here, at the single-shot boundary,
                # so the answer that follows it is what the rest of the turn sees. Only ever a
                # LEADING block — a `<think>` further in is content about the tags, and
                # `strip_reasoning_block` leaves that whole.
                output_text = strip_reasoning_block(_extract_openai_text(data))
                # The prompted lane. The model was told about its tools in prose, so a call it
                # writes arrives as prose too — and without this it is returned to the user as
                # chat. Only ever attempted when tools were genuinely offered on this turn:
                # _tool_call_text_from_content validates the name against the offered set, and
                # with an empty set that check cannot reject anything.
                offered = tuple(
                    item for item in (request.tools or ()) if isinstance(item, CloudToolDefinition)
                )
                # `output_mode == "tool_intent"` was the wrong discriminator, in both directions.
                # It let a readable tool call reach the user as prose on every other lane; and it
                # would have been unsafe to simply drop, because on a chat turn a reply can be prose
                # that merely CONTAINS JSON — "Here you go: {"intent": ...}" answers "echo that",
                # and converting it into a dispatched call throws the answer away.
                #
                # What separates the two is not the requested mode but whether any ANSWER survives.
                # A reply that is nothing but a tool call has no answer in it, so repairing it costs
                # the operator nothing and gains them the tool run they asked for. A reply with
                # prose around the call is an answer, and is left exactly as the model wrote it.
                #
                # `offered` stays the outer gate: `_tool_call_text_from_content` validates the name
                # against the offered set, and an empty set cannot reject anything.
                repaired_call_recovered = False
                if offered and (
                    request.output_mode == "tool_intent"
                    or not scrub_foreign_markers(output_text).strip()
                ):
                    repaired = _repaired_tool_call_from_payload(data, definitions=offered)
                    if repaired:
                        output_text = repaired
                        repaired_call_recovered = True
                        # A repaired call is not prose. Saying so on the RESPONSE is what stops the
                        # contract validating this JSON as a plain-text answer and showing it.
                        response_output_mode = "tool_intent"
                if not repaired_call_recovered and not output_text.strip():
                    # `response_output_mode` is NOT the right signal here -- it is initialized to
                    # `request.output_mode` (line above the loop), so a request that already ASKED
                    # for tool_intent starts equal to "tool_intent" whether or not a repair ever
                    # ran. `repaired_call_recovered` tracks the real thing: did this response
                    # actually yield usable content, one way or the other.
                    # SWITCHBOARD repair: an HTTP 200 with valid JSON but empty/whitespace-only
                    # `content` -- and, checked just above, no tool call recoverable from it
                    # either -- used to become a "successful" ModelResponse(text="", error=None).
                    # Downstream (the synthesis/grounding layer) could not tell that apart from a
                    # provider that was never asked anything, and treated it as a normal, if
                    # unhelpful, answer. The three CloudProviderAdapter classes already refuse this
                    # shape (adapters/{cloudflare_workers_ai,openrouter_cloud,
                    # generic_openai_cloud}_provider.py); this is the primary System A path's
                    # equivalent.
                    if bool(getattr(request, "tools_required", False)):
                        # Tools were required for this turn; we are in the NON-native branch only
                        # because assert_envelope_carries_tools (core/execution_requirements.py)
                        # already verified a structured-output fallback was offered instead
                        # (native support absent). The model was asked to answer through that
                        # contract and produced nothing -- a defective/absent call, not merely "no
                        # text": classified MALFORMED_TOOL_CALL, not the plain-chat EMPTY_PROVIDER_
                        # RESPONSE case below.
                        raise MalformedToolArgumentsError(
                            "native tool call required but the response produced no usable content"
                        )
                    empty = EmptyProviderResponseError(
                        "provider response has no usable text and no tool call"
                    )
                    # The reply's own facts ride the error (ids, finish reason, usage, reasoning
                    # tokens, which fields carried text) so the failure record can say WHY it was
                    # empty -- never its text. See `core.normalized_provider_result`.
                    try:
                        from core.normalized_provider_result import empty_reply_diagnostics

                        empty.diagnostics = empty_reply_diagnostics(  # type: ignore[attr-defined]
                            data, max_tokens_sent=payload.get("max_tokens")
                        )
                    except Exception:
                        pass
                    raise empty
                break
            try:
                output_text, native_tool_calls = _extract_native_openai_tool_result(
                    data, definitions=native_tools, metadata=request.metadata
                )
                break
            except ToolCallParseError:
                # SWITCHBOARD repair: a NAMED tool-call defect (UnknownToolNameError /
                # MalformedToolArgumentsError / DuplicateToolCallError) must reach the router as
                # itself, not get flattened into a generic RuntimeError the way the plain
                # ValueError branch below does. Ollama's lane already preserves these types; this
                # branch used to catch the shared ValueError base FIRST and destroy the subclass
                # before core.memory_first_router's dedicated ToolCallParseError handler ever saw
                # it -- the identical malformed call classified MALFORMED_TOOL_CALL on Ollama and
                # MALFORMED_PROVIDER_RESPONSE (wrong: the wire format was fine, the MODEL's call
                # was bad) on this lane.
                if attempt_index + 1 >= max_attempts:
                    raise
            except ValueError as exc:
                if attempt_index + 1 >= max_attempts:
                    raise RuntimeError(f"malformed provider response: {exc}") from exc
        usage = dict(data.get("usage") or {})
        # `data` is reassigned fresh at the top of each retry-loop iteration (line ~313 above) and
        # only the LAST attempt's value survives to this point -- either the loop broke on success,
        # or it raised on final failure and this line is never reached. Reading `data.get("model")`
        # here is therefore already scoped to the successful attempt; no separate per-attempt
        # capture is needed to avoid a stale value leaking from an earlier failed attempt. This is
        # genuine provider-response evidence, distinct from `model_name` below (the runtime's own
        # selection, from the manifest) -- never fabricated when the field is absent.
        raw_attested_model = data.get("model")
        provider_attested_model = str(raw_attested_model) if raw_attested_model else None
        choices = list(data.get("choices") or [])
        first_choice = choices[0] if choices and isinstance(choices[0], dict) else {}
        finish_reason = str(first_choice.get("finish_reason") or "").strip()
        return ModelResponse(
            output_text=output_text,
            confidence=float(self.manifest.metadata.get("confidence_baseline") or 0.65),
            raw_response=data,
            provider_metadata={"response_headers": response_header_facts(getattr(response, "headers", None))},
            usage=usage,
            provider_id=self.manifest.provider_id,
            model_name=self.manifest.model_name,
            output_mode=response_output_mode,
            tool_calls=native_tool_calls,
            provider_attested_model=provider_attested_model,
            finish_reason=finish_reason,
            effective_max_output_tokens=(
                int(payload.get("max_tokens")) if payload.get("max_tokens") else None
            ),
        )

    def _gate_local_model_load(self) -> None:
        """Resource-governor gate (Phase 3): refuse a local model LOAD that cannot fit in free RAM.

        A model that is already resident is never gated (serving it costs no new RAM), and any
        governor error fails soft (proceed). When the load genuinely does not fit even after the
        governor reclaims other idle models, raising here turns "the whole Mac swap-freezes" into
        an ordinary failed call the routing fallback machinery already knows how to degrade.
        """
        try:
            from core.resource_governor import plan_model_load

            decision = plan_model_load(str(self.manifest.model_name or ""))
        except Exception:
            return
        if decision.ok:
            return
        # The headroom floor is part of the arithmetic and has to be IN the sentence. Measured live
        # 2026-08-03: "loading qwen3:14b (~9.3 GB) does not fit the ~10.3 GB of free RAM" - which
        # reads as false, because 9.3 does fit in 10.3. The gate was right; it needs
        # `footprint + floor` free, not `footprint`. A refusal whose stated reason looks wrong sends
        # the reader looking for a bug in the gate instead of at their own memory pressure.
        from core.resource_governor import _LOAD_SAFETY_FLOOR_GB

        raise RuntimeError(
            f"{self.manifest.provider_id}: model_load_gated_low_memory — loading "
            f"{self.manifest.model_name} needs ~{decision.footprint_gb:.1f} GB plus a "
            f"{_LOAD_SAFETY_FLOOR_GB:.1f} GB headroom floor "
            f"(~{decision.footprint_gb + _LOAD_SAFETY_FLOOR_GB:.1f} GB in total), and only "
            f"~{decision.usable_after_gb:.1f} GB is free even after reclaiming; refusing to "
            "swap-freeze the machine"
        )

    def _invoke_ollama_chat(self, request: ModelRequest, *, force_json: bool) -> ModelResponse:
        if request.is_cancelled():
            raise RuntimeError("model_call_cancelled")
        base_url = _native_ollama_base_url(str(self.manifest.runtime_config.get("base_url") or "").rstrip("/"))
        if not base_url:
            raise RuntimeError(f"{self.manifest.provider_id}: missing runtime_config.base_url")
        self._gate_local_model_load()
        payload = self._build_ollama_payload(request, force_json=force_json, stream=False)
        # Final pre-invocation boundary -- see the identical guard in _invoke_openai_compatible.
        # `format` (not `response_format`) is Ollama's structured-output-fallback key.
        assert_envelope_carries_tools(
            tools_required=bool(getattr(request, "tools_required", False)),
            offered_tool_count=len(request.tools or ()),
            native_tools_in_envelope=bool(payload.get("tools")),
            structured_fallback_in_envelope=bool(payload.get("format")),
            lane_name=self.manifest.provider_id,
        )
        from core.provider_call_deadline import effective_timeout_seconds

        timeout_seconds = effective_timeout_seconds(
            request,
            float(self.manifest.runtime_config.get("timeout_seconds") or 30.0),
        )
        headers = self._headers()
        permit = seal_provider_invocation(
            request=request,
            provider_id=self.manifest.provider_id,
            model_id=self.manifest.model_name,
            operation="structured" if force_json else "chat",
            payload=payload,
            header_names=tuple(headers),
        )
        # Admission BEFORE the socket, so a queued turn does not burn its own read timeout waiting
        # for a model that is busy with somebody else's turn. Measured at N=24 concurrent without
        # this: 7 of 24 died on `Read timed out (read timeout=59.99)` having never been generated
        # for. See core/local_model_admission.py.
        with local_model_slot(provider_id=self.manifest.provider_id):
            timeout_seconds = effective_timeout_seconds(request, timeout_seconds)
            body = permit.consume()
            response = requests.post(
                f"{base_url}/api/chat",
                json=body,
                headers=headers,
                timeout=_request_timeout(timeout_seconds),
            )
            _assert_response_timely(request, response)
            _raise_for_status_with_cause(response)
        if request.is_cancelled():
            raise RuntimeError("model_call_cancelled")
        data = response.json()
        self._record_benchmark_best_effort(request=request, response_payload=data)
        # A native tool call arrives beside `content`, not inside it, so it has to be read first
        # or a model that emits a call plus a sentence of preamble looks like it only chatted.
        native_tools = self._ollama_native_tools(request)
        output_text = ""
        native_tool_calls: tuple[CloudToolCall, ...] = ()
        if native_tools:
            native_tool_calls, output_text = _ollama_tool_calls_with_recovery(
                data, definitions=native_tools, request=request
            )
        if not output_text:
            # `message.content` only — `message.thinking` is where `think: true` puts the reasoning
            # and it is never the answer. Stripping a leading `<think>` block on top covers the
            # older-Ollama / parser-off case where the block lands in `content` anyway. The STREAM
            # path deliberately does not do this: tags span NDJSON frames, so a per-frame strip
            # would cut a block it cannot see the end of.
            output_text = strip_reasoning_block(_extract_ollama_chat_text(data))
        if bool(getattr(request, "tools_required", False)) and native_tools and not native_tool_calls:
            # SWITCHBOARD repair: `_extract_ollama_tool_calls` deliberately never raises on an
            # absent/empty `tool_calls` array (see its own docstring) -- a local turn that carries
            # no tools is an ordinary conversational one, and elsewhere that silence is correct.
            # But when THIS turn required a tool call and none arrived -- `tool_calls: []`, no
            # `tool_calls` key at all, or the model answering with ordinary prose instead of
            # calling -- that used to fall straight through to a "successful" ModelResponse
            # carrying whatever text (or none) came back, leaving a later output validator to
            # notice the missing call. The OpenAI-compatible cloud lane's native path already
            # fails closed here (_extract_native_openai_tool_result raises "required native tool
            # call is missing" the moment `tool_calls` is absent/empty and `content` doesn't parse
            # as a call); this makes the Ollama lane raise the identical, identically-classified
            # error at the same boundary instead of deferring it downstream.
            raise MalformedToolArgumentsError("required native tool call is missing")
        usage = {
            "prompt_eval_count": data.get("prompt_eval_count"),
            "prompt_eval_duration": data.get("prompt_eval_duration"),
            "eval_count": data.get("eval_count"),
            "eval_duration": data.get("eval_duration"),
            "total_duration": data.get("total_duration"),
            "load_duration": data.get("load_duration"),
        }
        # Ollama's /api/chat response carries a top-level "model" field, same contract as the
        # OpenAI-compatible lane above -- genuine provider-response evidence, distinct from
        # `model_name` (the runtime's own manifest-selected model).
        raw_attested_model = data.get("model")
        provider_attested_model = str(raw_attested_model) if raw_attested_model else None
        finish_reason = str(data.get("done_reason") or "").strip()
        return ModelResponse(
            output_text=output_text,
            confidence=float(self.manifest.metadata.get("confidence_baseline") or 0.65),
            raw_response=data,
            usage={key: value for key, value in usage.items() if value is not None},
            provider_id=self.manifest.provider_id,
            model_name=self.manifest.model_name,
            output_mode=request.output_mode,
            tool_calls=native_tool_calls,
            provider_attested_model=provider_attested_model,
            finish_reason=finish_reason,
            effective_max_output_tokens=(
                int(dict(payload.get("options") or {}).get("num_predict"))
                if dict(payload.get("options") or {}).get("num_predict")
                else None
            ),
        )

    def _stream_openai_compatible(self, request: ModelRequest):
        # The same ONE coherent dispatch binding as the buffered path (review F3): the stream
        # URL and its Authorization header are one snapshot, never manifest-URL + current-key.
        base_url, dispatch_key = self._dispatch_binding()
        if not base_url:
            raise RuntimeError(f"{self.manifest.provider_id}: missing runtime_config.base_url")
        api_path = str(self.manifest.runtime_config.get("api_path") or "/v1/chat/completions")
        payload = self._build_openai_payload(request, force_json=False, stream=True)
        from core.provider_call_deadline import effective_timeout_seconds

        timeout_seconds = effective_timeout_seconds(
            request,
            float(self.manifest.runtime_config.get("timeout_seconds") or 30.0),
        )
        headers = self._headers(base_url=base_url, api_key=dispatch_key)
        permit = seal_provider_invocation(
            request=request,
            provider_id=self.manifest.provider_id,
            model_id=self.manifest.model_name,
            operation="stream",
            payload=payload,
            header_names=tuple(headers),
        )
        response = requests.post(
            f"{base_url}{api_path}",
            json=permit.consume(),
            headers=headers,
            timeout=_request_timeout(timeout_seconds),
            stream=True,
        )
        _assert_response_timely(request, response)
        _raise_for_status_with_cause(response)
        # SSE is UTF-8 by spec, but without an explicit charset `requests` falls back to ISO-8859-1
        # for text/* — which mangles every multi-byte char (— became â€œ-style mojibake in replies).
        response.encoding = "utf-8"

        def _iter_chunks():
            stream_usage: dict[str, Any] = {}
            response_metadata = {}
            # OpenAI-compatible streams typically repeat "model" on every frame, but the contract
            # here only promises it arrives on SOME frame -- captured once, the first time it's
            # seen, and carried on every chunk from then on (including the terminal one). Stays
            # None for the whole stream if the provider never sends it.
            attested_model: str | None = None
            finish_reason = ""
            try:
                for raw_line in response.iter_lines(decode_unicode=True):
                    _assert_response_timely(request, response)
                    if request.is_cancelled():
                        raise RuntimeError("model_call_cancelled")
                    line = _normalize_stream_line(raw_line)
                    if not line:
                        continue
                    event = _parse_stream_line(line)
                    if event is None:
                        continue
                    if event == "__DONE__":
                        break
                    response_metadata.update(response_body_facts(event))
                    if isinstance(event, dict) and event.get("usage"):
                        # The terminal usage frame carries choices:[] (no delta) plus tokens + cost.
                        stream_usage = dict(event["usage"])
                    if attested_model is None and isinstance(event, dict) and event.get("model"):
                        attested_model = str(event["model"])
                    if isinstance(event, dict):
                        choices = list(event.get("choices") or [])
                        first_choice = choices[0] if choices and isinstance(choices[0], dict) else {}
                        if first_choice.get("finish_reason"):
                            finish_reason = str(first_choice["finish_reason"])
                    delta_text = _extract_stream_delta_text(event)
                    if delta_text:
                        yield ModelStreamChunk(
                            delta_text=delta_text,
                            raw_event=event,
                            done=False,
                            provider_attested_model=attested_model,
                            finish_reason=finish_reason,
                        )
                _assert_response_timely(request, response)
            finally:
                response.close()
            yield ModelStreamChunk(
                delta_text="",
                done=True,
                usage=stream_usage or None,
                provider_metadata={"response_headers": response_header_facts(getattr(response, "headers", None)),
                                   "response_metadata": response_metadata},
                provider_attested_model=attested_model,
                finish_reason=finish_reason,
            )

        return _iter_chunks()

    def _stream_ollama_chat(self, request: ModelRequest):
        base_url = _native_ollama_base_url(str(self.manifest.runtime_config.get("base_url") or "").rstrip("/"))
        if not base_url:
            raise RuntimeError(f"{self.manifest.provider_id}: missing runtime_config.base_url")
        self._gate_local_model_load()
        payload = self._build_ollama_payload(request, force_json=False, stream=True)
        from core.provider_call_deadline import effective_timeout_seconds

        timeout_seconds = effective_timeout_seconds(
            request,
            float(self.manifest.runtime_config.get("timeout_seconds") or 30.0),
        )
        headers = self._headers()
        permit = seal_provider_invocation(
            request=request,
            provider_id=self.manifest.provider_id,
            model_id=self.manifest.model_name,
            operation="stream",
            payload=payload,
            header_names=tuple(headers),
        )
        # A STREAM generates for as long as it is being read, so the slot is held for the whole
        # generator, not just for the POST. Entered manually rather than with `with` because the
        # generator outlives this function; `_iter_chunks`'s `finally` is the single release point,
        # and it runs on exhaustion, on `close()` and on an exception alike.
        slot = local_model_slot(provider_id=self.manifest.provider_id)
        slot.__enter__()
        try:
            timeout_seconds = effective_timeout_seconds(request, timeout_seconds)
            response = requests.post(
                f"{base_url}/api/chat",
                json=permit.consume(),
                headers=headers,
                timeout=_request_timeout(timeout_seconds),
                stream=True,
            )
            _assert_response_timely(request, response)
            _raise_for_status_with_cause(response)
        except BaseException:
            slot.__exit__(None, None, None)
            raise
        response.encoding = "utf-8"  # NDJSON stream is UTF-8; avoid the ISO-8859-1 fallback mojibake

        def _iter_chunks():
            stream_usage: dict[str, Any] = {}
            # Same contract as the OpenAI-compatible stream above: captured once, the first time
            # Ollama's NDJSON frame carries "model", carried on every chunk from then on.
            attested_model: str | None = None
            finish_reason = ""
            try:
                for raw_line in response.iter_lines(decode_unicode=True):
                    _assert_response_timely(request, response)
                    if request.is_cancelled():
                        raise RuntimeError("model_call_cancelled")
                    line = _normalize_stream_line(raw_line)
                    if not line:
                        continue
                    event = _parse_stream_line(line)
                    if not isinstance(event, dict):
                        continue
                    if attested_model is None and event.get("model"):
                        attested_model = str(event["model"])
                    if event.get("done_reason"):
                        finish_reason = str(event["done_reason"])
                    delta_text = _extract_ollama_chat_text(event)
                    if delta_text:
                        yield ModelStreamChunk(
                            delta_text=delta_text,
                            raw_event=event,
                            done=False,
                            provider_attested_model=attested_model,
                            finish_reason=finish_reason,
                        )
                    if bool(event.get("done")):
                        self._record_benchmark_best_effort(
                            request=request,
                            response_payload=event,
                        )
                        # The final Ollama frame carries token counts for the whole generation.
                        stream_usage = {
                            key: event.get(key)
                            for key in (
                                "prompt_eval_count",
                                "eval_count",
                                "prompt_eval_duration",
                                "eval_duration",
                                "total_duration",
                                "load_duration",
                            )
                            if event.get(key) is not None
                        }
                        break
                _assert_response_timely(request, response)
            finally:
                response.close()
                slot.__exit__(None, None, None)
            yield ModelStreamChunk(
                delta_text="",
                done=True,
                usage=stream_usage or None,
                provider_attested_model=attested_model,
                finish_reason=finish_reason,
            )

        return _iter_chunks()

    def _record_benchmark_best_effort(self, *, request: ModelRequest, response_payload: dict[str, Any]) -> None:
        """Feed real tok/s from this Ollama call into the local inference benchmark
        table, so core.local_inference_autopilot's live routing scores providers on
        actual measured speed instead of static/zero manifest metadata. Never lets
        a benchmark-recording failure affect the real chat response."""
        try:
            from core.local_inference_evidence import record_ollama_generate_benchmark

            record_ollama_generate_benchmark(
                provider_id=self.manifest.provider_id,
                model_id=self.manifest.model_name,
                prompt=request.prompt,
                response_payload=response_payload,
            )
        except Exception:
            logger.debug("Failed to record local inference benchmark", exc_info=True)

    def _build_openai_payload(self, request: ModelRequest, *, force_json: bool, stream: bool) -> dict[str, Any]:
        generation_profile = dict(request.metadata.get("generation_profile") or {})
        context_window = self._ollama_context_window(request) if self._runtime_family() == "ollama" else 0
        max_output_tokens = (
            int(request.max_output_tokens)
            if request.max_output_tokens is not None
            else int(generation_profile["max_output_tokens"])
            if generation_profile.get("max_output_tokens") is not None
            else 0
        )
        messages = _request_messages_with_memory(
            request,
            context_window=context_window,
            output_reserve_tokens=max_output_tokens,
            supports_images=self._supports_image_input(),
        )
        # R-9/H-9 ADAPTER-IS-PROJECTION: this class serves BOTH a loopback
        # Ollama and remote BYOK endpoints. A NON-LOCAL destination is a cloud
        # egress boundary — messages are projected through the ONE canonical
        # egress gate (LOCAL_ONLY/SECRET segments dropped, unclassified
        # segment stamps refused) exactly like the generic cloud provider.
        try:
            _r9_host = (urlparse(
                str(self.manifest.runtime_config.get("base_url") or "")
            ).hostname or "").lower()
        except Exception:
            _r9_host = ""
        if _r9_host not in {"127.0.0.1", "localhost", "::1", "0.0.0.0"}:
            from core.egress_gate import project_messages_for_destination

            messages = project_messages_for_destination(
                messages, destination_class="cloud_provider"
            )
        payload: dict[str, Any] = {
            "model": self.manifest.model_name,
            "messages": messages,
            "temperature": request.temperature
            if request.temperature is not None
            else generation_profile.get("temperature", self.manifest.runtime_config.get("temperature", 0.2)),
        }
        if stream:
            payload["stream"] = True
            if self._runtime_family() != "ollama":
                # OpenAI/OpenRouter only emit the terminal usage block (tokens + real cost) on a
                # stream when explicitly asked; without this a streamed cloud turn reports no usage.
                payload["stream_options"] = {"include_usage": True}
        max_output_tokens = self._cloud_tool_output_budget(request, max_output_tokens)
        if max_output_tokens > 0:
            payload["max_tokens"] = max_output_tokens
        # The cloud twin of the `think: false` stamp (c556820), now driven by the request's typed
        # `reasoning_mode` rather than a task name. Measured 2026-08-01 on the stepped audit's
        # bounded nominate call: `nemotron-3-ultra-550b-a55b:free` spent EXACTLY its whole widened
        # budget (700 asked + 2048 thinking reserve = 2748 output tokens) reasoning, twice, and
        # returned no usable content either time. Sent only where the knob is known to be safe: a
        # provider that declared reasoning in `supported_parameters`, or the OpenRouter lane, whose
        # request schema documents `reasoning` and which tolerates it on any model — the live
        # nemotron manifest declares no supported_parameters at all, so the declaration alone would
        # never fire for the exact model that needs it. A strict OpenAI-compatible server that never
        # declared it may 400 on unknown fields, so no wider default.
        if (
            _reasoning_disabled(request)
            and self._runtime_family() != "ollama"
            and (self._declares_reasoning_support() or self._is_openrouter_lane())
        ):
            # Endpoint capability owns this control, not a model-name heuristic.
            # Unknown catalog metadata must not erase an explicit disabled policy.
            payload["reasoning"] = {"enabled": False}
        if generation_profile.get("top_p") is not None:
            payload["top_p"] = float(generation_profile["top_p"])
        if self._runtime_family() == "ollama":
            payload["options"] = self._ollama_runner_options(request)
        stop_sequences = [str(item) for item in list(generation_profile.get("stop_sequences") or []) if str(item or "").strip()]
        if stop_sequences:
            payload["stop"] = stop_sequences
        # Native provider tool-calling and provider-native structured output are COMPLEMENTARY, not
        # alternatives. When the request carries real tools, send them as `tools` and pin NO
        # response_format -- the model must stay free to emit a tool call. Otherwise fall back to the
        # strongest structured-output contract this provider advertises (json_schema, then
        # json_object). This is the union of the two parallel fixes; neither is dropped.
        # Gate on the request ACTUALLY carrying tools: a tool_intent turn that ships real tool
        # definitions takes the native tool-calling path, while one that ships none is a structured
        # -output turn and belongs to the json_schema path below. _native_tools still raises loudly
        # for a request that carries tools which are all malformed -- that check is preserved.
        native_tools = self._native_tools(request) if getattr(request, "tools", None) else ()
        if native_tools:
            supports_strict = bool(self.manifest.runtime_config.get("supports_strict_tool_calls", False))
            payload["tools"] = [
                openai_tool_payload(replace(tool, strict=supports_strict))
                for tool in native_tools
            ]
            payload["tool_choice"] = request.tool_choice or "required"
        else:
            schema = (request.contract or {}).get("json_schema")
            if force_json and schema and bool(self.manifest.runtime_config.get("supports_json_schema", False)):
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": f"vool_{request.output_mode}",
                        "strict": bool(self.manifest.runtime_config.get("supports_strict_json_schema", False)),
                        "schema": schema,
                    },
                }
            elif force_json and bool(self.manifest.runtime_config.get("supports_json_mode", False)):
                payload["response_format"] = {"type": "json_object"}
        # Opt-in wire capture (VOOL_DEBUG_PROMPT=1). Off by default; one env read when off.
        dump_outbound_prompt(
            payload,
            lane="openai",
            provider_id=str(getattr(self.manifest, "provider_id", "") or ""),
            model=str(getattr(self.manifest, "model_name", "") or ""),
            extra={"output_mode": getattr(request, "output_mode", ""), "force_json": force_json, "stream": stream},
        )
        return payload

    def _build_ollama_payload(self, request: ModelRequest, *, force_json: bool, stream: bool) -> dict[str, Any]:
        generation_profile = dict(request.metadata.get("generation_profile") or {})
        options: dict[str, Any] = {
            "temperature": request.temperature
            if request.temperature is not None
            else generation_profile.get("temperature", self.manifest.runtime_config.get("temperature", 0.2)),
        }
        if request.max_output_tokens is not None:
            options["num_predict"] = int(request.max_output_tokens)
        elif generation_profile.get("max_output_tokens") is not None:
            options["num_predict"] = int(generation_profile["max_output_tokens"])
        # Only ever widen a budget the caller actually set: absent means "provider default", and
        # writing a computed 0 into it would ask the model for an empty answer.
        if int(options.get("num_predict") or 0) > 0:
            options["num_predict"] = self._thinking_aware_output_budget(
                options["num_predict"],
                context_window=self._ollama_context_window(request),
                request=request,
            )
        if generation_profile.get("top_p") is not None:
            options["top_p"] = float(generation_profile["top_p"])
        stop_sequences = [str(item) for item in list(generation_profile.get("stop_sequences") or []) if str(item or "").strip()]
        if stop_sequences:
            options["stop"] = stop_sequences
        options.update(self._ollama_runner_options(request))
        context_window = self._ollama_context_window(request)
        messages = _request_messages_with_memory(
            request,
            context_window=context_window,
            output_reserve_tokens=int(options.get("num_predict") or 0),
            supports_images=self._supports_image_input(),
        )
        # Native Ollama speaks a string `content` plus a separate `images` list; the OpenAI content
        # parts the authority rendered are flattened into exactly that shape here, at the wire.
        from core.chat_attachments import flatten_message_for_ollama

        messages = [flatten_message_for_ollama(message) for message in messages]
        payload: dict[str, Any] = {
            "model": self.manifest.model_name,
            "messages": messages,
            "stream": bool(stream),
            "options": options,
        }
        think_flag = self._ollama_think_flag(request)
        if think_flag is not None:
            payload["think"] = think_flag
        keep_alive = str(self.manifest.runtime_config.get("keep_alive") or "").strip()
        if keep_alive:
            payload["keep_alive"] = keep_alive
        # Ollama's /api/chat speaks the OpenAI `tools` shape. Without this the local lane can
        # only be told about tools in prose, so a small model has to reproduce a JSON envelope
        # from a text description instead of being decoded into one.
        native_tools = self._ollama_native_tools(request)
        if native_tools:
            # Sent unstrict deliberately. Ollama accepts `strict` (verified: it returns 200 and
            # still emits the call) but does not enforce it the way a strict cloud schema does,
            # so claiming strictness here would advertise a guarantee nothing upholds. The
            # argument check happens on our side instead — that is what json_schema_lite is for.
            payload["tools"] = [
                openai_tool_payload(replace(tool, strict=False)) for tool in native_tools
            ]
            if request.tool_choice:
                payload["tool_choice"] = request.tool_choice

        # `format` and `tools` are mutually exclusive on Ollama, and sending both is far worse
        # than redundant. Measured 2026-07-28 on qwen3:8b with 57 tool definitions,
        # tool_choice="required" and the tool_intent json_schema in one body: the grammar
        # suppresses native tool calling entirely — zero `tool_calls` — and the model degenerates
        # in the content channel, repeating `"arguments": {"path": ...}` for 222 seconds until
        # num_predict cuts it off with done_reason="length" and invalid JSON. The turn died on a
        # 60s provider budget, and the budget was then spent, so no second candidate was tried.
        # The identical body with `format` removed returned a clean native tool call in 35s.
        #
        # Native tools win: a tool call is the stronger, self-describing form of the same answer,
        # and each tool carries its own argument schema, so the generic envelope grammar adds
        # nothing the catalog does not already say.
        schema = (request.contract or {}).get("json_schema")
        if native_tools:
            pass
        elif force_json and schema and bool(self.manifest.runtime_config.get("supports_json_schema", False)):
            payload["format"] = schema
        elif force_json and bool(self.manifest.runtime_config.get("supports_json_mode", False)):
            payload["format"] = "json"
        # Opt-in wire capture (VOOL_DEBUG_PROMPT=1). Off by default; one env read when off.
        dump_outbound_prompt(
            payload,
            lane="ollama",
            provider_id=str(getattr(self.manifest, "provider_id", "") or ""),
            model=str(getattr(self.manifest, "model_name", "") or ""),
            extra={"output_mode": getattr(request, "output_mode", ""), "force_json": force_json, "stream": stream},
        )
        return payload

    def _ollama_native_tools(self, request: ModelRequest) -> tuple[CloudToolDefinition, ...]:
        """Tool definitions to publish natively on the Ollama lane, or empty to stay on prose.

        Unlike the cloud path this never raises when the list is empty. A local turn that
        carries no tools is an ordinary conversational turn, and the model must still answer it.
        """

        if not flag_enabled("ollama_native_tools"):
            return ()
        if self._runtime_family() != "ollama":
            return ()
        return tuple(item for item in (request.tools or ()) if isinstance(item, CloudToolDefinition))

    def _uses_native_ollama_chat(self) -> bool:
        if self._runtime_family() != "ollama":
            return False
        return not bool(str(self.manifest.runtime_config.get("api_path") or "").strip())

    def _runtime_family(self) -> str:
        return str(self.manifest.metadata.get("runtime_family") or "").strip().lower()

    def _ollama_runner_options(self, request: ModelRequest | None = None) -> dict[str, Any]:
        """The option keys that decide WHICH runner Ollama serves this model on.

        Ollama keys a loaded runner on the model AND on `num_ctx`, `num_thread` and `num_gpu`
        (among others): a request whose values differ from the resident runner's reloads the
        model. Measured 2026-09-10 on the s51 rig (6249a0d9): certification stamped `num_ctx`
        alone, the first chat call after it stamped `num_thread` as well, and qwen3:8b (9.0 GB)
        reloaded for 14.0 s inside the planner's single-attempt budget -- the conductor declined
        and the plain lane refused the whole turn. Direct timing of the same clause split:
        20.3 s on a cold runner (load 14.0 s + prompt eval 5.0 s + 1.1 s generation), 1.4 s on
        the warm one. Every native-ollama payload the runtime sends -- serving, certification,
        prewarm -- takes these keys from this one seam so they all name ONE runner.
        """
        budget = get_active_compute_budget()
        options: dict[str, Any] = {"num_thread": int(max(1, budget.cpu_threads))}
        context_window = self._ollama_context_window(request)
        if context_window > 0:
            options["num_ctx"] = context_window
        num_gpu = self._ollama_num_gpu()
        if num_gpu is not None:
            options["num_gpu"] = num_gpu
        return options

    def _ollama_context_window(self, request: ModelRequest | None = None) -> int:
        raw = self.manifest.runtime_config.get("context_window") or self.manifest.metadata.get("context_window") or 0
        try:
            configured = max(0, int(raw or 0))
            requested = int((request.metadata if request else {}).get("num_ctx") or 0)
            if requested > 0 and configured > 0:
                return min(requested, configured)
            return requested if requested > 0 else configured
        except (TypeError, ValueError):
            return 0

    def _ollama_num_gpu(self) -> int | None:
        # Explicit GPU-layer count for the Ollama lane. None means "unset" so
        # Ollama decides layers as before; 0 forces pure CPU. 0 is falsy, so
        # callers must branch on `is not None`, never on truthiness.
        raw = self.manifest.runtime_config.get("num_gpu")
        if raw is None:
            return None
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            return None

    def _ollama_think_flag(self, request: Any = None) -> bool | None:
        """The `think` value for this model, or None to omit the key.

        The typed call policy wins: ``disabled`` is false and ``required`` is true. ``auto`` uses
        the provider's persisted preference, then the user preference when a legacy manifest has
        no value. This keeps Auto genuinely adaptive instead of silently treating every trivial
        local turn as deep reasoning.

        Non-thinking models must omit the key entirely: qwen2.5:7b answers HTTP 400 when it is
        present at all.
        """
        if self._is_thinking_capable_model():
            from core.model_request_policy import REASONING_REQUIRED, request_reasoning_mode

            reasoning_mode = request_reasoning_mode(request)
            if _reasoning_disabled(request):
                return False
            if reasoning_mode == REASONING_REQUIRED:
                return True
            configured = self.manifest.runtime_config.get("think")
            if configured is not None:
                return bool(configured)
            from core.reasoning_mode import deep_reasoning_enabled

            return deep_reasoning_enabled()
        configured = self.manifest.runtime_config.get("think")
        return bool(configured) if configured is not None else None

    def _is_thinking_capable_model(self) -> bool:
        """Models that emit reasoning before the answer, and so need budget for both.

        Was qwen3-only, then marker-list + manifest-declaration, which is how the fix kept
        missing the models that actually hit it: `nvidia/nemotron-3-ultra-550b-a55b:free`
        returned empty content on plain_text turns at max_tokens 284/440 and matched nothing
        here, and in 2026-09-16's capture `deepseek/deepseek-v4-flash-0731` and
        `z-ai/glm-5.3-flash` failed the same way on the byok lane — OpenRouter publishes their
        `reasoning` parameter in the catalog, but the byok manifest carried no declaration and
        no marker matched, so their wire ceiling stayed the prompt table's 240–520 tokens and
        was spent entirely on reasoning. The decision now lives in ONE authority
        (`core.output_budget_policy.manifest_declares_reasoning`) that reads the manifest
        declaration, the cached catalog row on the byok lane, and the legacy markers, and is
        the same function the paid reservation's lane resolution uses.
        """

        from core.output_budget_policy import manifest_declares_reasoning

        return manifest_declares_reasoning(self.manifest)

    def _declares_reasoning_support(self) -> bool:
        declared = {
            str(item).strip().lower()
            for item in (self.manifest.metadata.get("supported_parameters") or ())
        }
        if {"reasoning", "include_reasoning"} & declared:
            return True
        # The byok lane's declaration source is the provider catalog, which is where OpenRouter
        # publishes `supported_parameters` per model. Cache-only: deciding whether to send the
        # `reasoning` knob must never block a turn on a catalog refresh, and an absent row keeps
        # the manifest's own (absent) declaration.
        if self._is_openrouter_lane():
            try:
                from core.openrouter_catalog import cached_catalog_row

                row = cached_catalog_row(str(self.manifest.model_name or ""))
            except Exception:
                row = None
            if row is not None:
                catalog_parameters = {str(item).strip().lower() for item in row.supported_parameters}
                return bool({"reasoning", "include_reasoning"} & catalog_parameters)
        return False

    def _cloud_tool_output_budget(self, request: ModelRequest, requested: int) -> int:
        """Give a cloud tool turn room to reason before it emits the call.

        `_max_output_tokens` allots 700 tokens to a `tool_intent` turn, and the Ollama lane then
        adds a 2048-token thinking reserve on top. The cloud lane never did, so a reasoning model
        spent the 700 on chain-of-thought and the response came back `finish_reason: "length"`
        with no `tool_calls` — which the runtime reported as "malformed provider response:
        required native tool call is missing" and the user saw as a failed turn.

        Measured 2026-07-28 against `nvidia/nemotron-3-nano-30b-a3b:free` on two phrasings:
        at max_tokens=512 one returned `finish_reason: length` and no call; at 3000 both returned
        `finish_reason: tool_calls`. The model was capable at both budgets — only the room differed.

        A reasoning model needs the same room on an ORDINARY CHAT turn, and scoping this to tool
        turns was too narrow. Measured 2026-07-28 against
        `nvidia/nemotron-3-ultra-550b-a55b:free`: plain_text turns went out at max_tokens 284
        ("Hey"), 440, 356 — the model spent them reasoning and returned empty content, which the
        user saw as "I couldn't get a usable model response" and, on one turn, as a blank reply.
        The turns that happened to get more room answered fine.

        A chat turn takes the smaller `_THINKING_RESERVE_TOKENS` rather than the tool-call floor:
        enough to think before speaking, without turning every greeting into a 3000-token budget.
        Non-reasoning models are untouched — their budget already describes their whole output.

        EXCEPTION, added after a live audit of this exact function (2026-08-05): the reserve is
        skipped when the call both (a) declared `reasoning_mode="disabled"` AND (b) is on a lane
        where that declaration actually reaches the wire as `payload["reasoning"] = {"enabled":
        False}` — the same `_declares_reasoning_support() or self._is_openrouter_lane()` gate used
        a few lines up in `_build_openai_payload` to decide whether to SEND that flag. Reusing the
        identical gate here (rather than keying on `_reasoning_disabled(request)` alone) matters:
        `reasoning_mode="disabled"` is caller INTENT, and on a lane that never declared reasoning
        support and is not OpenRouter, the wire flag is never sent, so the model can still reason
        exactly as before and dropping the reserve there would reopen the empty-completion failure
        this function exists to prevent. Only skip the reserve where the intent and the wire agree.
        Verified live 2026-08-05, `stepped_audit.py`'s nominate call (which sets
        `reasoning_mode="disabled"` on every call, `stepped_audit.py:410`) against
        `nvidia/nemotron-3-ultra-550b-a55b:free` on the openrouter-byok lane: 12/12 real trials at
        ceilings 3000/5000/8000 finished `finish_reason: "stop"` with NO reasoning burn observed at
        all (largest completion 2579 tokens, well under even the smallest un-reserved ceiling) —
        i.e. once the disable flag is honoured on the wire, this reserve was never once needed for
        that call in that experiment. A call that leaves `reasoning_mode` at `"auto"` or
        `"required"` is untouched by this branch and still gets the reserve exactly as before.
        """

        if not flag_enabled("cloud_tool_reasoning_reserve"):
            return requested
        if requested <= 0 or self._runtime_family() == "ollama":
            return requested
        # Keyed on the tools being ATTACHED, not on the classifier's label for the turn. A turn that
        # carries schemas can emit a call, and the room a model needs to reach one does not change
        # because the turn was labelled `plain_text` -- `core/memory_first_router.py` attaches the
        # full catalog with `tool_choice: "auto"` on an ordinary turn under the
        # `plain_text_tool_catalog` flag, and that turn resolved to 240 tokens against a measurement
        # that says 512 already ends `finish_reason: "length"` with no call. Keying on the label
        # would have handed the strongest lane the smallest budget the moment that flag went on.
        # This costs nothing when the model just talks: `max_tokens` is a ceiling and only emitted
        # tokens are billed, so a conversational turn under `auto` is priced exactly as before.
        if getattr(request, "tools", None):
            return max(requested, _CLOUD_TOOL_CALL_FLOOR_TOKENS)
        if self._is_thinking_capable_model():
            if _reasoning_disabled(request) and (
                self._declares_reasoning_support() or self._is_openrouter_lane()
            ):
                return requested
            # The chat-turn reserve now resolves through the ONE lane policy
            # (`core.output_budget_policy`) with the manifest's real capability, instead of the
            # flat `requested + 2048`. Same number as before on unclassified lanes (no cost-class
            # target, thinking reserve added on top of the base); on a lane that declares its
            # cost class — the byok burst lane — the answer itself is first lifted to that class's
            # target (a paid chat turn to 760, a verified-free one to 1800) and the reserve is
            # added on top, exactly what `core.cloud_broker.lane_output_budget` already does for
            # the AUTO path. The paid reservation sizes the completion tokens it holds from the
            # same function, so the funds cover the ceiling actually sent.
            from core.output_budget_policy import lane_resolved_output_tokens

            return lane_resolved_output_tokens(
                self.manifest,
                base_tokens=requested,
                output_mode=str(getattr(request, "output_mode", "") or "plain_text"),
            )
        # A NON-reasoning chat turn on a lane that declares its cost class resolves through the
        # same ONE lane policy. Measured 2026-09-15/16 on a pinned paid byok lane: design-review
        # answers went out at the prompt layer's chat table (240-520 tokens -- sized when every
        # lane was a small local model) and came back cut mid-sentence with `finish_reason:
        # "length"`, which the runtime then had to fail honestly and the user had to re-ask --
        # paying the full input twice for one answer. `max_tokens` is a ceiling and only emitted
        # tokens are billed, so the lift costs nothing when the answer is short, and the policy's
        # own targets bound it (paid 760, verified-free 1800) before the physical caps.
        from core.output_budget_policy import FREE_CLOUD, PAID_CLOUD, lane_resolved_output_tokens, manifest_lane_capability

        if manifest_lane_capability(self.manifest).cost_class in {FREE_CLOUD, PAID_CLOUD}:
            return lane_resolved_output_tokens(
                self.manifest,
                base_tokens=requested,
                output_mode=str(getattr(request, "output_mode", "") or "plain_text"),
            )
        return requested

    def _thinking_aware_output_budget(
        self, num_predict: Any, *, context_window: int, request: Any = None
    ) -> int:
        """Raise a thinking model's output budget to cover the reasoning that precedes the answer.

        A thinking model spends the budget reasoning before it writes a single word of the answer,
        and the reasoning is not free: measured on qwen3:4b, a 200-token budget produced 802
        characters of reasoning and an EMPTY `content`. The caller's budget describes the answer it
        wants, so the reasoning is paid for on top of it or the user is handed a blank reply.

        The reserve is bounded by the window it has to live in. Reserving output tokens takes them
        away from the prompt, so an unbounded reserve against a small window leaves nothing for the
        system prompt and the turn, and the call fails closed instead of answering. Non-thinking
        models are left exactly as the caller asked.

        When the effective Ollama flag is false there are no reasoning tokens to reserve, whether
        that came from a request-level disable or Auto's saved preference. Required reasoning and
        legacy callers without a resolved flag keep the reserve.
        """
        try:
            requested = int(num_predict)
        except (TypeError, ValueError):
            requested = 0
        if requested <= 0 or not self._is_thinking_capable_model():
            return requested
        if request is not None and self._ollama_think_flag(request) is False:
            return requested
        reserve = _THINKING_RESERVE_TOKENS
        if context_window > 0:
            reserve = min(reserve, max(0, context_window // 2 - requested))
        return requested + max(0, reserve)

    def _headers(self, *, base_url: str | None = None, api_key: str | None = None) -> dict[str, str]:
        """Build the request headers. ``base_url``/``api_key`` carry a DISPATCH SNAPSHOT
        (from ``_dispatch_binding``) so the Authorization header and the URL the request
        travels to are the same coherent binding; without them the lane's own resolution
        applies, unchanged."""
        headers: dict[str, str] = {"Content-Type": "application/json"}
        headers.update({str(k): str(v) for k, v in dict(self.manifest.runtime_config.get("headers") or {}).items()})
        if api_key is None:
            api_key = self._resolve_api_key()
        transport_base = base_url if base_url is not None else str(
            self.manifest.runtime_config.get("base_url") or ""
        ).strip()
        if api_key and self._key_transport_is_safe(transport_base):
            headers["Authorization"] = f"Bearer {api_key}"
        if self._is_openrouter_lane():
            # The manifest can contain legacy or caller-supplied headers. The canonical factory
            # must run last so no request path can clobber VOOL attribution on the wire.
            from core.runtime_provider_defaults import apply_openrouter_attribution_headers

            headers = apply_openrouter_attribution_headers(headers)
        return headers

    def _is_openrouter_lane(self) -> bool:
        provider_name = str(getattr(self.manifest, "provider_name", "") or "").strip().lower()
        base_url = str(self.manifest.runtime_config.get("base_url") or "").strip()
        hostname = str(urlparse(base_url).hostname or "").strip().lower()
        return provider_name == "openrouter-byok" or hostname == "openrouter.ai"

    def _supports_native_tools(self) -> bool:
        """Whether this provider gets native function schemas, decided by capability.

        The pre-existing rule was `hostname == openrouter.ai`, which meant a user's own OpenAI,
        Anthropic, Groq, DeepSeek or Gemini key received no native tools at all and fell back to
        describing the catalog in prose — the weakest lane given to the strongest models.

        Order of authority: an explicit manifest setting, then the declared capability, then the
        historical hostname rule as a floor so nothing that worked before stops working.
        """

        if not flag_enabled("capability_tool_dialect"):
            return self._is_openrouter_lane()

        configured = str(self.manifest.runtime_config.get("tool_dialect") or "").strip().lower()
        if configured in {"native", "prompted"}:
            return configured == "native"

        declared = {
            str(item).strip().lower()
            for item in (self.manifest.metadata.get("tool_support") or ())
        }
        if "tool_calls" in declared:
            return True

        # `supported_parameters` is what OpenRouter's own catalog publishes PER MODEL, fetched live
        # by core/openrouter_catalog.py. Because it comes from the provider it is authoritative in
        # BOTH directions: if it is published and omits "tools", this model really does not take
        # native schemas.
        supported = {
            str(item).strip().lower()
            for item in (self.manifest.metadata.get("supported_parameters") or ())
        }
        if supported:
            return "tools" in supported

        # `tool_support` above is NOT authoritative in the negative direction. It is a
        # hand-maintained list in core/runtime_provider_defaults.py, and it was simply never
        # updated for lanes that gained tool calling later: llamacpp-local, mlx-local, kimi-remote,
        # openai-compatible-remote and tether-remote all ship
        # `tool_support: ["structured_json", "code_complex"]` and so received ZERO native schemas —
        # two LOCAL lanes and the user's own OpenAI/Anthropic/Groq key handed the weakest dialect
        # VOOL has, decided by a static string rather than by anything the server said.
        # With no published capability, discover it instead of guessing.
        probed = self._probe_native_tool_support()
        if probed is not None:
            return probed
        # An OpenAI-compatible endpoint we could not reach or probe: assume the protocol it claims
        # to speak. `tools` has been part of that protocol for years, and the failure mode of being
        # wrong is one rejected request that demotes the lane - against a permanent capability loss
        # for being wrong the other way.
        return True

    def _probe_native_tool_support(self) -> bool | None:
        """Ask a LOOPBACK server whether it takes native tool schemas. `None` when not probeable.

        Only local servers are probed, and only over loopback. A probe is a real request, so
        probing a remote lane would spend the user's money to answer a question about capability -
        never acceptable as a side effect of routing. Local servers cost nothing, so the answer is
        measured there instead of assumed.

        The probe is one `max_tokens: 1` request carrying a trivial tool. A server that does not
        understand `tools` rejects the body (400/422); one that does returns normally. The result is
        cached per (base_url, model) for the process - capability does not change under us, and a
        probe per turn would be a tax on every request.
        """

        base_url = str(self.manifest.runtime_config.get("base_url") or "").strip().rstrip("/")
        if not base_url:
            return None
        try:
            host = (urlparse(base_url).hostname or "").lower()
        except Exception:
            return None
        if host not in {"127.0.0.1", "localhost", "::1", "0.0.0.0"}:
            return None  # remote: a probe would be a billable call

        cache_key = f"{base_url}|{self.manifest.model_name}"
        cached = _NATIVE_TOOL_PROBE_CACHE.get(cache_key)
        if cached is not None:
            return cached

        api_path = str(self.manifest.runtime_config.get("api_path") or "/v1/chat/completions")
        payload = {
            "model": self.manifest.model_name,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "vool__capability_probe",
                        "description": "Capability probe. Never called.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
        }
        try:
            # A real keyed request (discovery): it travels on the same coherent dispatch
            # binding as completion. A stale custom-lane freeze answers "not probeable" rather
            # than sending the current key to the lane's old destination.
            probe_base, probe_key = self._dispatch_binding()
        except Exception:
            return None
        if probe_base.rstrip("/") != base_url.rstrip("/"):
            return None  # the manifest destination is stale: not this probe's call to re-route
        try:
            response = requests.post(
                f"{base_url}{api_path}",
                json=payload,
                headers=self._headers(base_url=base_url, api_key=probe_key),
                timeout=20,
            )
            # 400/422 is the server saying it does not understand `tools`. Anything else - including
            # a 500 or a rate limit - is not evidence about tool support, so it is not cached as a
            # negative; the lane stays optimistic and can be probed again later.
            if response.status_code in (400, 422):
                supported = False
            elif response.ok:
                supported = True
            else:
                return None
        except Exception:
            return None  # unreachable server says nothing about its capability

        _NATIVE_TOOL_PROBE_CACHE[cache_key] = supported
        return supported

    def tool_certification_backend_version(self) -> str:
        """Best-effort local backend version for an explicit certification fingerprint.

        This method is never called from routing. It refuses every non-loopback endpoint before
        I/O and returns ``unknown`` rather than weakening that boundary.
        """

        base_url = str(self.manifest.runtime_config.get("base_url") or "").strip().rstrip("/")
        if not _strict_tool_certification_loopback(base_url):
            return "unknown"
        configured = str(
            self.manifest.metadata.get("backend_version")
            or self.manifest.runtime_config.get("backend_version")
            or ""
        ).strip()
        if configured:
            return configured
        if self._runtime_family() != "ollama":
            return "unknown"
        try:
            response = requests.get(
                f"{_native_ollama_base_url(base_url)}/api/version",
                headers=self._headers(),
                timeout=_request_timeout(5.0),
            )
            if response.ok:
                return str(dict(response.json() or {}).get("version") or "unknown")[:128]
        except Exception:
            pass
        return "unknown"

    def tool_certification_runtime_identity(self) -> dict[str, str]:
        """Read non-generative loopback metadata used to invalidate stale certifications.

        Raw templates are never returned or persisted; only their SHA-256 digest crosses this
        boundary. Failure is represented as ``unknown`` rather than guessed identity.
        """

        identity = {
            "backend_version": self.tool_certification_backend_version(),
            "model_digest": str(
                self.manifest.metadata.get("model_digest")
                or self.manifest.metadata.get("digest")
                or self.manifest.runtime_config.get("model_digest")
                or "unknown"
            ),
            "template_hash": str(
                self.manifest.metadata.get("chat_template_hash")
                or self.manifest.metadata.get("template_hash")
                or self.manifest.runtime_config.get("chat_template_hash")
                or "unknown"
            ),
            "quantization": str(
                self.manifest.metadata.get("quantization")
                or self.manifest.runtime_config.get("quantization")
                or "unknown"
            ),
        }
        base_url = str(self.manifest.runtime_config.get("base_url") or "").strip().rstrip("/")
        if not _strict_tool_certification_loopback(base_url) or self._runtime_family() != "ollama":
            return identity
        native_base = _native_ollama_base_url(base_url)
        try:
            tags_response = requests.get(
                f"{native_base}/api/tags",
                headers=self._headers(),
                timeout=_request_timeout(5.0),
            )
            if tags_response.ok:
                models = list(dict(tags_response.json() or {}).get("models") or [])
                for row in models:
                    if not isinstance(row, dict):
                        continue
                    names = {str(row.get("name") or ""), str(row.get("model") or "")}
                    if self.manifest.model_name not in names:
                        continue
                    identity["model_digest"] = str(row.get("digest") or identity["model_digest"])
                    details = dict(row.get("details") or {})
                    identity["quantization"] = str(
                        details.get("quantization_level") or identity["quantization"]
                    )
                    break
        except Exception:
            pass
        try:
            show_response = requests.post(
                f"{native_base}/api/show",
                json={"model": self.manifest.model_name},
                headers=self._headers(),
                timeout=_request_timeout(8.0),
            )
            if show_response.ok:
                show = dict(show_response.json() or {})
                template = str(show.get("template") or "")
                if template:
                    identity["template_hash"] = hashlib.sha256(template.encode()).hexdigest()
                details = dict(show.get("details") or {})
                identity["quantization"] = str(
                    details.get("quantization_level") or identity["quantization"]
                )
        except Exception:
            pass
        return identity

    def tool_certification_exchange(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: tuple[CloudToolDefinition, ...],
        tool_choice: str | dict[str, Any] | None,
        max_output_tokens: int,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        """One explicit sealed exchange for the observe-only local tool certification suite.

        The method intentionally does not call ``_supports_native_tools``: that function is a
        routing-time transport check, while certification is manual and must measure the actual
        model even when the optimistic routing check is inconclusive.
        """

        base_url = str(self.manifest.runtime_config.get("base_url") or "").strip().rstrip("/")
        if not _strict_tool_certification_loopback(base_url):
            raise ValueError("tool certification is restricted to a loopback model endpoint")
        safe_messages = [dict(item) for item in messages if isinstance(item, dict)]
        max_tokens = max(1, min(int(max_output_tokens or 1), 1024))
        headers = self._headers()
        if self._uses_native_ollama_chat():
            dialect = "ollama"
            endpoint = f"{_native_ollama_base_url(base_url)}/api/chat"
            # The probe must measure the model in the mode the runtime actually serves it in.
            # Every other native-ollama payload here stamps the think flag (`_ollama_think_flag`)
            # and gives a thinking model room to reason before its answer; without them a qwen3
            # model spends the whole probe budget on reasoning and returns an empty answer with no
            # tool calls -- a configuration no serving path ever uses, reported as if the model
            # itself were incapable.
            think_flag = self._ollama_think_flag()
            num_predict = (
                self._thinking_aware_output_budget(max_tokens, context_window=0)
                if think_flag
                else max_tokens
            )
            # The sizing half of the same seam. The think flag and the output budget above were
            # added so the probe measures the model in the mode the runtime serves it in; num_ctx
            # was still missing, so ollama loaded the probe at the model's NATIVE context while
            # every serving path loads it sized. Measured (FINDINGS F12): certification held
            # qwen3:8b at ctx=40960 / 10.63 GiB against ctx=8192 / 6.10 GiB when serving -- 4.5 GiB
            # of a 24 GiB host, spent certifying a configuration the product never runs, and
            # colliding with the lanes a turn needs. Certification stays sealed and unchanged: the
            # same four stages, the same tools, the same verdict -- measured at the size it serves.
            # ... and the RUNNER half: `num_ctx` alone still named a different runner than
            # serving (which also stamps `num_thread`/`num_gpu`), so the first chat call after
            # a certification reloaded the model (14 s on qwen3:8b) inside the planner's budget.
            # One seam, one runner -- see `_ollama_runner_options`.
            options: dict[str, Any] = {"num_predict": num_predict, "temperature": 0}
            options.update(self._ollama_runner_options(None))
            payload: dict[str, Any] = {
                "model": self.manifest.model_name,
                "messages": safe_messages,
                "stream": False,
                "options": options,
            }
            if think_flag is not None:
                payload["think"] = think_flag
            if tools:
                payload["tools"] = [openai_tool_payload(replace(tool, strict=False)) for tool in tools]
                if tool_choice:
                    payload["tool_choice"] = tool_choice
        else:
            dialect = "openai"
            api_path = str(self.manifest.runtime_config.get("api_path") or "/v1/chat/completions")
            endpoint = f"{base_url}{api_path}"
            payload = {
                "model": self.manifest.model_name,
                "messages": safe_messages,
                "stream": False,
                "temperature": 0,
                "max_tokens": max_tokens,
            }
            if tools:
                payload["tools"] = [openai_tool_payload(tool) for tool in tools]
                if tool_choice:
                    payload["tool_choice"] = tool_choice
        permit = seal_direct_provider_invocation(
            provider_id=self.manifest.provider_id,
            model_id=self.manifest.model_name,
            operation="local_tool_certification",
            payload=payload,
            request_id=f"tool-certification-{os.urandom(8).hex()}",
            max_output_tokens=max_tokens,
            header_names=tuple(headers),
        )
        started = time.perf_counter()
        response = requests.post(
            endpoint,
            json=permit.consume(),
            headers=headers,
            timeout=_request_timeout(max(1.0, min(float(timeout_seconds or 90.0), 300.0))),
        )
        latency_ms = (time.perf_counter() - started) * 1000.0
        try:
            body = response.json()
        except Exception:
            body = {"error": "non_json_response"}
        return {
            "status_code": int(response.status_code),
            "body": body if isinstance(body, dict) else {"error": "non_object_response"},
            "latency_ms": latency_ms,
            "dialect": dialect,
        }

    def _native_tools(self, request: ModelRequest) -> tuple[CloudToolDefinition, ...]:
        if request.output_mode != "tool_intent" or not self._supports_native_tools():
            return ()
        tools = tuple(item for item in request.tools if isinstance(item, CloudToolDefinition))
        if not tools:
            raise RuntimeError("malformed tool request: native runtime tool definitions are missing")
        return tools

    def _is_verified_free_openrouter_lane(self) -> bool:
        return self._is_openrouter_lane() and bool(
            str(self.manifest.model_name or "").strip().lower().endswith(":free")
            or self.manifest.metadata.get("verified_free")
        )

    def _key_transport_is_safe(self, base_url: str | None = None) -> bool:
        """Whether the bearer key may be sent to this provider's base_url.

        A key is only attached over HTTPS, or over HTTP to a loopback host (a legitimate local
        model server). A misconfigured ``*_BASE_URL`` pointing at a plaintext, non-loopback
        host would otherwise leak the key in cleartext to an arbitrary endpoint, so the key is
        withheld and a warning logged rather than transmitted insecurely. ``base_url`` is the
        destination the key would ACTUALLY travel to (a dispatch snapshot); the manifest's
        frozen URL is only the default.
        """
        from core.cloud_providers import is_safe_key_transport

        target = base_url if base_url is not None else str(
            self.manifest.runtime_config.get("base_url") or ""
        ).strip()
        # One canonical gate (shared with the credentials endpoint + connection probe): HTTPS to
        # any host, or HTTP only to a genuine loopback IP. A raw host.startswith("127.") would
        # accept http://127.0.0.1.evil.com — is_safe_key_transport checks real IP membership.
        if is_safe_key_transport(target):
            return True
        logger.warning(
            "%s: refusing to send the API key over an insecure transport (%s); "
            "point *_BASE_URL at an https:// endpoint (or a loopback host) to enable the key.",
            self.manifest.provider_id,
            target or "<empty>",
        )
        return False

    def _supports_image_input(self) -> bool | None:
        """Whether THIS model is known to read images: True/False when stated, None when unknown.

        The answer comes from the attachment authority so every adapter reads the same rule: a
        manifest capability, an explicit modality, or (for the OpenRouter burst lane) the live
        catalog row. Unknown is never rounded up to yes.
        """
        from core.chat_attachments import manifest_supports_images

        try:
            return manifest_supports_images(self.manifest)
        except Exception:
            return None

    def _resolve_api_key(self) -> str:
        """Resolve the bearer key LENIENTLY (headers may legitimately carry none on keyless lanes).

        ``api_key_env`` keeps server/CI deploys working from the environment; ``credential_key``
        lets a user bring their own key (BYOK) via the local encrypted credential store without
        putting a secret in the environment or a manifest. The environment wins when both are set.
        A dispatch that REQUIRES a credential does not read this method — it reads
        ``_dispatch_credential``, which refuses before the wire instead of degrading to an
        anonymous request.
        """
        api_key_env = str(self.manifest.runtime_config.get("api_key_env") or "").strip()
        if api_key_env and os.getenv(api_key_env):
            return str(os.getenv(api_key_env) or "")
        credential_key = str(self.manifest.runtime_config.get("credential_key") or "").strip()
        if credential_key:
            try:
                from core import credential_store

                return str(credential_store.get_credential(credential_key) or "")
            except Exception:
                return ""
        return ""

    def _dispatch_credential(self, credential_key: str) -> str:
        """The STRICT reader a dispatch uses: a lane that declared a BYOK credential slot
        refuses before the wire when that secret is absent or unreadable.

        Environment keys still win with no vault read (a server deploy must not depend on the
        desktop vault), and a lane with NO credential slot keeps the keyless behaviour it always
        had — a local runner, or an explicitly supported anonymous route. Only the lane whose own
        manifest says "this lane's credential lives in slot X" treats a missing X as a dispatch
        refusal, which is the one case where sending anonymously would turn a local credential
        problem into a remote authentication rejection the user cannot act on from the message.
        """
        api_key_env = str(self.manifest.runtime_config.get("api_key_env") or "").strip()
        if api_key_env and os.getenv(api_key_env):
            return str(os.getenv(api_key_env) or "")
        if not credential_key:
            return ""
        from core import credential_store

        try:
            value = credential_store.get_credential(credential_key)
        except credential_store.CredentialReadError as exc:
            raise _credential_unavailable(credential_key, "unreadable", cause=str(exc)) from exc
        except Exception as exc:
            # Fail closed: a vault that cannot be consulted is not permission to send anonymous.
            raise _credential_unavailable(credential_key, "unreadable", cause=exc.__class__.__name__) from exc
        if not str(value or "").strip():
            raise _credential_unavailable(credential_key, "not present")
        return str(value)

    def _is_custom_managed_lane(self) -> bool:
        """Whether this manifest's destination+credential are the custom provider's REPLACEABLE
        pair, managed by the credential transaction — rather than a fixed vendor endpoint, an
        explicitly non-custom manifest credential, or a local runner. Only lanes the BYOK
        registrar minted for the custom provider (``custom-byok`` carrying the custom slot)
        qualify; every other lane keeps its existing key resolution untouched. One definition, shared
        with the route authority's dispatch resolution (``core.cloud_providers``)."""
        from core.cloud_providers import manifest_uses_committed_custom_pair

        return manifest_uses_committed_custom_pair(self.manifest)

    def _dispatch_binding(self) -> tuple[str, str]:
        """ONE coherent (base_url, api_key) snapshot for a dispatch on this lane.

        For every lane except the custom BYOK endpoint, the destination is fixed by the
        provider table at registration and the manifest's frozen base_url IS the lane's
        destination; the current key resolves exactly as before.

        The CUSTOM lane's destination is user-supplied and replaceable. The manifest froze
        whatever base URL was committed when the lane registered, while a fresh key read would
        return whatever is committed NOW — an adapter holding endpoint A must never attach
        replacement key B (review F3). The custom lane therefore resolves its binding through
        the ONE pair authority (``resolved_custom_pair``, admitted under the credential
        transaction lock) and REFUSES a stale freeze: the current key never travels to the
        lane's old destination, and the request is never silently redirected to a destination
        it was not authorized for. The route authority re-resolves the lane (re-registration
        against the committed pair) and retries with the fresh manifest; an unresolved or
        incoherent committed pair refuses here the same way, as an actionable typed error.

        The snapshot is taken once per dispatch and preserved through headers, URL, routing
        and the call itself (including a same-lane retry) — a mid-flight replacement cannot
        split one request across two bindings, and no lock is held across the network."""
        base_url = str(self.manifest.runtime_config.get("base_url") or "").rstrip("/")
        if not self._is_custom_managed_lane():
            credential_key = str(self.manifest.runtime_config.get("credential_key") or "").strip()
            return base_url, self._dispatch_credential(credential_key)
        from core.cloud_providers import StaleProviderBindingError, resolved_custom_pair

        committed_base, committed_key = resolved_custom_pair()
        if not committed_base or committed_base.rstrip("/") != base_url.rstrip("/"):
            raise StaleProviderBindingError(
                f"{self.manifest.provider_id}: this lane was registered for "
                f"{base_url or '<none>'} but the committed credential binding now names "
                f"{committed_base or '<none>'}; the current key was NOT sent to the old "
                "destination and the request was NOT redirected — the lane must be "
                "re-registered against the committed pair (route authority re-resolution)"
            )
        return base_url, committed_key

    def verify_dispatch_binding(self) -> None:
        """Preflight the coherent dispatch binding with NO network: raises
        ``StaleProviderBindingError`` (or the pair authority's typed refusal) when this lane's
        frozen destination no longer matches the committed credential binding. The route
        authority's seam — a refusal here is re-resolved by re-registering the lane, never by
        sending anything to the stale destination."""
        self._dispatch_binding()


_HTTP_CAUSE_EXCERPT_CHARS = 300


def _raise_for_status_with_cause(response: Any) -> None:
    """`raise_for_status()`, with the provider's OWN reason for a 4xx/5xx on the error.

    Measured 2026-09-02 on the served composer: three cloud attempts for one turn each ended as
    "400 Client Error: Bad Request for url: .../chat/completions" -- the status line, and nothing
    of the JSON body in which OpenRouter had actually said what was wrong with the request. The
    body's first characters ride the message (secrets redacted, bounded), so the Activity row and
    the operator's bubble name the cause instead of the status code. Same exception class, so
    every classifier downstream keeps working.
    """
    from core.secret_redaction import redact_secrets

    failure: Any = None
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        failure = exc
    if failure is None:
        return
    excerpt = ""
    try:
        raw = str(getattr(response, "text", "") or "")
    except Exception:
        raw = ""
    if raw:
        excerpt = " ".join(redact_secrets(raw).split())[:_HTTP_CAUSE_EXCERPT_CHARS]
    # requests' own message names the full request URL, and a URL can carry a credential (UsePod's
    # token is a path segment). The message is rebuilt redacted and raised OUTSIDE the except block,
    # so the original exception -- URL and all -- is neither the error raised nor chained onto it.
    message = redact_secrets(str(failure))
    if excerpt:
        message = f"{message} — provider said: {excerpt}"
    # A 429's Retry-After is TYPED provider evidence, and the error string is the one channel
    # this attempt's reason travels through to the turn's decision details. Carrying it as a
    # greppable marker lets the wording surface say "the provider said to wait Ns" instead of
    # guessing that a short wait fixes every quota refusal. Only the numeric seconds form is
    # parsed; an HTTP-date form stays out rather than being approximated.
    if getattr(response, "status_code", None) == 429:
        with contextlib.suppress(Exception):
            header = str(response.headers.get("Retry-After") or "").strip()
            if header.isdigit() and 0 < int(header) <= 86400:
                message = f"{message} [retry_after={int(header)}s]"
    raise requests.HTTPError(message, response=getattr(failure, "response", None))


def _build_messages(system_prompt: str | None, prompt: str, *, attachments: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    attachment_entries = list(attachments or [])
    if not attachment_entries:
        messages.append({"role": "user", "content": prompt})
        return messages

    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for attachment in attachment_entries:
        kind = str(attachment.get("kind") or "").lower()
        if kind == "image":
            url = str(attachment.get("url") or attachment.get("path") or "").strip()
            if url:
                content.append({"type": "image_url", "image_url": {"url": url}})
        elif kind == "video":
            transcript = str(attachment.get("transcript") or attachment.get("caption") or "").strip()
            label = str(attachment.get("label") or "Video evidence").strip()
            if transcript:
                content.append({"type": "text", "text": f"{label} transcript: {transcript}"})
            else:
                content.append({"type": "text", "text": f"{label}: video evidence provided but no transcript was available."})
        else:
            snippet = str(attachment.get("text") or attachment.get("caption") or "").strip()
            if snippet:
                content.append({"type": "text", "text": snippet})
    messages.append({"role": "user", "content": content})
    return messages


def _request_messages_with_memory(
    request: ModelRequest,
    *,
    context_window: int = 0,
    output_reserve_tokens: int = 0,
    supports_images: bool | None = None,
) -> list[dict[str, Any]]:
    if request.messages:
        # The chat lane always arrives with its messages built. The turn's attachments (the
        # canonical `ModelRequest.attachments` carrier) are rendered onto the CURRENT user message
        # here, at the wire, by the ONE authority: text as data, images only when THIS model is
        # known to read them. A text-only request is returned byte-identical.
        from core.chat_attachments import apply_to_provider_messages

        messages, delivery = apply_to_provider_messages(
            request.messages, request.attachments, supports_images=supports_images
        )
        if delivery:
            request.metadata["attachment_delivery"] = delivery
    else:
        messages = _build_messages(request.system_prompt, request.prompt, attachments=request.attachments)
    memory_prefix = build_memory_prefix_for_request(request)
    messages = apply_memory_prefix_to_messages(messages, request, prefix=memory_prefix)
    if context_window <= 0:
        return messages
    try:
        result = fit_messages_to_context_window(
            messages,
            num_ctx=context_window,
            output_reserve_tokens=output_reserve_tokens,
            protected_system_prompt=str(request.system_prompt or ""),
            memory_prefix=memory_prefix,
        )
    except PromptBudgetExceededError as exc:
        request.metadata["prompt_budget"] = {
            **exc.telemetry,
            "error": str(exc),
        }
        logger.error("Rejected unsafe Ollama prompt: %s", exc)
        raise
    request.metadata["prompt_budget"] = result.telemetry
    if result.telemetry["status"] == "trimmed":
        logger.warning("Trimmed Ollama prompt to preserve system instructions: %s", result.telemetry)
    return result.messages


def _provider_error_detail(payload: dict[str, Any]) -> str:
    """The provider's own error, when an HTTP 200 body carries one instead of choices.

    OpenRouter answers an upstream outage with 200 and ``{"error": {"message", "code"}}``.
    Measured on a651de73 (2026-09-11): the planner's pre-classification call failed twice as
    "did not include choices" while the body said "Upstream error from Nvidia: Internal server
    error" (502, provider_unavailable) -- the one fact the trace needed was the one it dropped.
    """
    error = payload.get("error")
    if isinstance(error, dict):
        message = str(error.get("message") or "").strip()
        code = str(error.get("code") or "").strip()
        metadata = error.get("metadata") if isinstance(error.get("metadata"), dict) else {}
        kind = str(metadata.get("error_type") or "").strip()
        parts = [part for part in (f"code {code}" if code else "", kind, message) if part]
        return "; ".join(parts)[:300]
    if isinstance(error, str) and error.strip():
        return error.strip()[:300]
    return ""


def _extract_openai_text(payload: dict[str, Any]) -> str:
    raw_choices = payload.get("choices") if "choices" in payload else None
    if raw_choices is None or raw_choices == []:
        detail = _provider_error_detail(payload)
        raise EmptyProviderResponseError(
            "OpenAI-compatible response did not include choices."
            + (f" Provider error: {detail}" if detail else "")
        )
    if not isinstance(raw_choices, list):
        raise MalformedProviderResponseError(
            "OpenAI-compatible response choices must be a list."
        )
    if not isinstance(raw_choices[0], dict):
        raise MalformedProviderResponseError(
            "OpenAI-compatible response choice must be an object."
        )
    raw_message = raw_choices[0].get("message")
    if not isinstance(raw_message, dict):
        raise MalformedProviderResponseError(
            "OpenAI-compatible response did not include textual content: "
            "the choice had no message object."
        )
    message = dict(raw_message)
    content = message.get("content")
    # Some providers duplicate an unfinished reasoning channel into content. Channel
    # identity is evidence of no separate answer, not a prose-style heuristic.
    reasoning = message.get("reasoning")
    if isinstance(content, str) and isinstance(reasoning, str) and reasoning.strip():
        if content.strip() == reasoning.strip():
            return ""
    if content is None and "content" in message:
        # A well-formed message with `content: null` is an ABSENT answer, not a wrong-typed one:
        # the caller reads "" as the empty completion it is and raises EmptyProviderResponseError
        # with the reply's own facts. Measured served 2026-09-16 (candidate 035dea9b): a null
        # content with no reasoning channel fell through to the MALFORMED branch below, so the
        # failure record said "did not include textual content", carried no diagnostics, and the
        # empty reply could not be told from a wrong-typed body. A message with NO content key
        # at all keeps the malformed reading below (its shape is not the documented one).
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(item.get("text") or "") for item in content if isinstance(item, dict)).strip()
    raise MalformedProviderResponseError(
        "OpenAI-compatible response did not include textual content."
    )


def _repaired_tool_call_from_payload(
    payload: dict[str, Any],
    *,
    definitions: tuple[CloudToolDefinition, ...],
) -> str:
    """A tool call recovered from an OpenAI-shaped reply's `content`, or "" if there is none.

    Used on lanes that were never given native schemas, where a call can only ever arrive as
    prose. Returns "" for ordinary chat, so a conversational turn is untouched.
    """

    choices = list(payload.get("choices") or [])
    if not choices or not isinstance(choices[0], dict):
        return ""
    message = choices[0].get("message")
    if not isinstance(message, dict):
        return ""
    # A native envelope wins if one is somehow present; only fall to prose when it is not.
    raw_calls = message.get("tool_calls")
    if isinstance(raw_calls, list) and raw_calls:
        try:
            if len(raw_calls) != 1:
                raise ValueError("prompted-lane recovery requires one native tool call")
            return canonical_tool_call_text(
                parse_native_tool_calls(raw_calls, definitions=definitions)[0]
            )
        except (ValueError, IndexError):
            pass
    return _tool_call_text_from_content(message, definitions=definitions)


def _extract_native_openai_tool_text(
    payload: dict[str, Any],
    *,
    definitions: tuple[CloudToolDefinition, ...],
) -> str:
    return _extract_native_openai_tool_result(payload, definitions=definitions)[0]


def _extract_native_openai_tool_result(
    payload: dict[str, Any],
    *,
    definitions: tuple[CloudToolDefinition, ...],
    metadata: dict[str, Any] | None = None,
) -> tuple[str, tuple[CloudToolCall, ...]]:
    raw_choices = payload.get("choices") if "choices" in payload else None
    if raw_choices is None or raw_choices == []:
        raise EmptyProviderResponseError("native tool response did not include choices")
    if not isinstance(raw_choices, list) or not isinstance(raw_choices[0], dict):
        raise MalformedProviderResponseError(
            "native tool response choices must be a list of objects"
        )
    choices = raw_choices
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise MalformedProviderResponseError(
            "native tool response did not include a message object"
        )
    raw_calls = message.get("tool_calls")
    if raw_calls is None:
        # Plenty of models -- including the free OpenRouter tier -- answer a tool request with the
        # tool call in `content` instead of a native `tool_calls` entry. Measured: nemotron-3
        # returned exactly {"intent": "machine.read_file", "arguments": {"path": "...", "max_lines":
        # 200}}, a valid call the runtime can execute, and this raised "required native tool call is
        # missing" and threw it away -- the user got "I couldn't map that cleanly to a real action"
        # 4 times out of 4 while the model was right every time. The strict native path stays the
        # preferred one; this is the compatibility fallback for models without it, and it reads
        # every supported text dialect (Hermes/Qwen XML, Gemma fences, bare JSON), not JSON alone.
        resolution = resolve_tool_calls(
            content=_message_content_text(message), raw_native_calls=None, definitions=definitions
        )
        if metadata is not None:
            metadata["tool_call_resolution"] = resolution.as_metadata()
        if resolution.calls:
            return canonical_intent_text(resolution.calls[0]), resolution.calls
        raise ValueError("required native tool call is missing")
    try:
        calls = parse_native_tool_calls(raw_calls, definitions=definitions)
    except ValueError:
        # `tool_calls` was present but the envelope did not satisfy the strict contract — an
        # empty list or a malformed entry. Before failing the turn, apply the bounded recovery:
        # one deterministic repair of the defective envelope, then `content` through every
        # supported dialect — a model that sent a broken envelope has often ALSO written the call
        # as prose, and discarding it costs the user the turn while the model was right.
        # Re-raises below if nothing is recoverable.
        resolution = resolve_tool_calls(
            content=_message_content_text(message),
            raw_native_calls=raw_calls,
            definitions=definitions,
        )
        if metadata is not None:
            metadata["tool_call_resolution"] = resolution.as_metadata()
        if resolution.calls:
            return canonical_intent_text(resolution.calls[0]), resolution.calls
        raise
    if metadata is not None:
        metadata["tool_call_resolution"] = ToolCallResolution(
            "parsed", calls, "native"
        ).as_metadata()
    return canonical_tool_call_text(calls[0]), calls


def _first_json_value(text: str) -> Any:
    """Parse the first JSON object or array embedded in a model reply, else None.

    Tolerates a truncated tail (finish_reason "length"): the opening element is decoded even when the
    document as a whole never closes.
    """
    body = str(text or "").strip()
    if not body:
        return None
    start = min((i for i in (body.find("{"), body.find("[")) if i >= 0), default=-1)
    if start < 0:
        return None
    decoder = json.JSONDecoder()
    try:
        value, _ = decoder.raw_decode(body[start:])
        return value
    except Exception:
        pass
    # Truncated: recover the first complete element of an unterminated array.
    if body[start] == "[":
        try:
            value, _ = decoder.raw_decode(body[start + 1 :].lstrip())
            return [value]
        except Exception:
            return None
    return None


def _message_content_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, list):  # some providers return content parts
        content = "\n".join(str(part.get("text") or "") for part in content if isinstance(part, dict))
    return str(content or "")


def _tool_call_text_from_content(
    message: dict[str, Any],
    *,
    definitions: tuple[CloudToolDefinition, ...] = (),
) -> str:
    """A tool call the model wrote as JSON prose, normalised to the runtime's canonical text.

    Accepts only a JSON object naming a tool that was ACTUALLY OFFERED on this turn. Ordinary prose,
    a refusal, a half-written object, or an invented tool name ({"tool": "bash", ...}) all stay
    "no tool call", so a hallucinated turn is still malformed and still retried or failed loudly.
    """
    content = message.get("content")
    if isinstance(content, list):  # some providers return content parts
        content = "\n".join(str(part.get("text") or "") for part in content if isinstance(part, dict))
    text = str(content or "").strip()
    if not text:
        return ""
    parsed = _first_json_value(text)
    # A model may emit a LIST of calls -- measured: nemotron-3 returned
    # [{"name": "respond__direct", "parameters": {...}}, ...] repeated until it hit the token limit
    # (finish_reason "length"), with the CORRECT grounded answer sitting in the first element. Take
    # the first well-formed call; the runtime executes one intent per step anyway.
    if isinstance(parsed, list):
        parsed = next((item for item in parsed if isinstance(item, dict)), None)
    if not isinstance(parsed, dict):
        return ""
    intent = str(parsed.get("intent") or parsed.get("name") or parsed.get("tool") or "").strip()
    if not intent:
        return ""
    arguments = parsed.get("arguments")
    if arguments is None:
        arguments = parsed.get("parameters")
    if not isinstance(arguments, dict):
        arguments = {}
    offered = {str(d.intent) for d in definitions} | {str(d.name) for d in definitions}
    if offered and intent not in offered:
        return ""  # a name the model invented is not a tool call
    for definition in definitions:
        if intent == str(definition.name) and str(definition.intent):
            intent = str(definition.intent)  # provider-side name -> runtime intent
            break
    return json.dumps({"intent": intent, "arguments": arguments}, ensure_ascii=False)


def _extract_ollama_tool_call_text(
    payload: dict[str, Any],
    *,
    definitions: tuple[CloudToolDefinition, ...] = (),
) -> str:
    """A native Ollama tool call, normalised to the runtime's canonical text.

    Ollama's envelope is OpenAI-shaped with one difference that matters: `arguments` arrives as
    a JSON **object**, where OpenAI sends a JSON **string**. Both are accepted here, because the
    same adapter class serves both and a model behind an OpenAI-compatible proxy may send either.

    A name the model invented is not a tool call. Returning "" puts the turn back on the prose
    path, where the repair parser gets its chance, rather than dispatching something unoffered.
    """

    calls = _extract_ollama_tool_calls(payload, definitions=definitions)
    if not calls:
        return ""
    return json.dumps(
        {"intent": calls[0].intent, "arguments": dict(calls[0].arguments)}, ensure_ascii=False
    )


def _ollama_tool_calls_with_recovery(
    payload: dict[str, Any],
    *,
    definitions: tuple[CloudToolDefinition, ...],
    request: ModelRequest,
) -> tuple[tuple[CloudToolCall, ...], str]:
    """(calls, canonical text) for the Ollama lane, with the bounded recovery path attached.

    The live defect this closes (gold/ETH, local Qwen): a turn that required a tool call got one
    — expressed as a text dialect in `message.content`, or as a native envelope with one syntax
    defect — and this lane, which had no content fallback at all, abandoned it. Recovery runs
    only on turns that asked for a tool (`tools_required` or `output_mode == "tool_intent"`), so
    an ordinary chat reply that merely contains JSON is never turned into an execution. Every
    outcome is stamped on `request.metadata["tool_call_resolution"]` as a typed state
    (parsed / repaired / rejected / none); a rejection on a tools-required turn raises the
    resolution's own typed error instead of silently dropping the intent.
    """

    recovery_wanted = bool(getattr(request, "tools_required", False)) or (
        request.output_mode == "tool_intent"
    )
    message = dict(payload.get("message") or {})
    try:
        calls = _extract_ollama_tool_calls(payload, definitions=definitions)
    except ToolCallParseError as exc:
        if not recovery_wanted:
            raise
        resolution = resolve_tool_calls(
            content=_extract_ollama_chat_text(payload),
            raw_native_calls=message.get("tool_calls"),
            definitions=definitions,
            native_parse_fn=lambda raw: _extract_ollama_tool_calls(
                {"message": {"tool_calls": raw}}, definitions=definitions
            ),
        )
        request.metadata["tool_call_resolution"] = resolution.as_metadata()
        if resolution.calls:
            return resolution.calls, canonical_intent_text(resolution.calls[0])
        raise resolution.typed_error() or exc
    if calls:
        request.metadata["tool_call_resolution"] = ToolCallResolution(
            "parsed", calls, "native"
        ).as_metadata()
        return calls, canonical_intent_text(calls[0])
    if not recovery_wanted:
        return (), ""
    resolution = resolve_tool_calls(
        content=_extract_ollama_chat_text(payload),
        raw_native_calls=None,
        definitions=definitions,
    )
    request.metadata["tool_call_resolution"] = resolution.as_metadata()
    if resolution.calls:
        return resolution.calls, canonical_intent_text(resolution.calls[0])
    if resolution.state == "rejected":
        # A rejected recovery means tool markup EXPRESSED a call and the call is broken. On any
        # tool_intent turn (recovery_wanted is the gate that got us here), handing that markup
        # downstream as prose reproduces the silent-abandonment path: the output guard strips
        # the block, the contract fails json.loads, and the turn dies as "missing_intent" with
        # the typed cause invisible. Raise the typed error instead — the router already
        # classifies it (MALFORMED_TOOL_CALL) and reports it per lane-equivalence.
        typed = resolution.typed_error()
        if typed is not None:
            raise typed
    return (), ""


def _extract_ollama_tool_calls(
    payload: dict[str, Any],
    *,
    definitions: tuple[CloudToolDefinition, ...] = (),
) -> tuple[CloudToolCall, ...]:
    """EVERY native Ollama tool call in the reply, not just the first one.

    Ollama emits parallel calls as a matter of course - measured 2026-08-02, qwen3:8b answered
    "read README.md and SECURITY.md" with two `tool_calls` in one reply.

    Fail-closed, matching the cloud contract in `core.cloud_tool_call_contract.parse_native_tool_calls`
    exactly (this function used to hand-roll its own, looser rules -- an unknown name silently
    returned an EMPTY batch, unparseable arguments silently became `{}`, and nothing checked for a
    repeated call). All three now raise a specific, named `ToolCallParseError` subclass instead:
    a malformed or hallucinated call must never be indistinguishable from "the model said nothing"
    or "the model made an empty-argument call that happened to succeed." One member failing still
    fails the WHOLE batch, not just that member -- executing the valid half of a reply that also
    invented or duplicated a call would dispatch work alongside something never offered.

    The one genuine Ollama-specific tolerance kept here, not in the shared cloud parser: an entry
    may omit the OpenAI `function` wrapper and carry `name`/`arguments` directly (measured live);
    that is a real wire shape, not a malformed one.
    """

    message = dict(payload.get("message") or {})
    raw_calls = message.get("tool_calls")
    if not isinstance(raw_calls, list) or not raw_calls:
        return ()

    offered = {str(item.intent) for item in definitions} | {str(item.name) for item in definitions}
    intent_by_name = {
        str(item.name): str(item.intent) for item in definitions if str(item.intent)
    }

    parsed: list[CloudToolCall] = []
    seen_call_ids: set[str] = set()
    seen_signatures: set[tuple[str, str]] = set()
    for entry in raw_calls:
        if not isinstance(entry, dict):
            raise MalformedToolArgumentsError(f"native Ollama tool call entry is not an object: {entry!r}")
        function = entry.get("function") if isinstance(entry.get("function"), dict) else entry
        name = str((function or {}).get("name") or "").strip()
        if not name:
            raise MalformedToolArgumentsError("native Ollama tool call carried no function name")
        if offered and name not in offered:
            raise UnknownToolNameError(f"native Ollama tool call named an unregistered function: {name!r}")

        arguments = _parse_ollama_tool_arguments(name, (function or {}).get("arguments"))

        call_id = str(entry.get("id") or "").strip()
        if call_id:
            if call_id in seen_call_ids:
                raise DuplicateToolCallError(f"duplicate native Ollama tool call id: {call_id!r}")
            seen_call_ids.add(call_id)
        else:
            signature = (name, json.dumps(arguments, sort_keys=True, default=str))
            if signature in seen_signatures:
                raise DuplicateToolCallError(f"duplicate native Ollama tool call: {name!r} with identical arguments")
            seen_signatures.add(signature)

        parsed.append(
            CloudToolCall(
                call_id=call_id,
                intent=intent_by_name.get(name, name),
                name=name,
                arguments=arguments,
            )
        )
    return tuple(parsed)


def _parse_ollama_tool_arguments(name: str, raw_arguments: Any) -> dict[str, Any]:
    """Ollama sends `arguments` as a JSON OBJECT where OpenAI sends a JSON STRING. Both are
    accepted: the same adapter class serves both, and a model behind an OpenAI-compatible proxy
    may send either. Anything that is neither -- or a string that fails to parse, or parses to
    something other than an object -- is a malformed call, raised rather than silently defaulted
    to `{}` (a defaulted-empty call is indistinguishable downstream from a genuine, correct
    zero-argument call, which is a real and common case handled separately below)."""

    if raw_arguments is None:
        return {}
    if isinstance(raw_arguments, dict):
        return dict(raw_arguments)
    if not isinstance(raw_arguments, str):
        raise MalformedToolArgumentsError(
            f"native Ollama tool call {name!r} carried arguments that are neither JSON text nor an "
            f"object: {type(raw_arguments).__name__}"
        )
    stripped = raw_arguments.strip()
    if not stripped:
        return {}
    try:
        value = json.loads(stripped)
    except (TypeError, ValueError) as exc:
        raise MalformedToolArgumentsError(
            f"native Ollama tool call {name!r} carried unparseable JSON arguments"
        ) from exc
    if not isinstance(value, dict):
        raise MalformedToolArgumentsError(
            f"native Ollama tool call {name!r} arguments parsed to a non-object JSON value"
        )
    return value


def _extract_ollama_chat_text(payload: dict[str, Any]) -> str:
    message = dict(payload.get("message") or {})
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(item.get("text") or "") for item in content if isinstance(item, dict)).strip()
    return ""


def _parse_stream_line(line: str) -> dict[str, Any] | str | None:
    text = str(line or "").strip()
    if not text:
        return None
    if text.startswith("data:"):
        text = text[5:].strip()
    if text == "[DONE]":
        return "__DONE__"
    try:
        return json.loads(text)
    except Exception:
        return None


def _extract_stream_delta_text(payload: dict[str, Any]) -> str:
    choices = list(payload.get("choices") or [])
    if not choices:
        return ""
    choice = dict(choices[0] or {})
    delta = dict(choice.get("delta") or {})
    content = delta.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(item.get("text") or "") for item in content if isinstance(item, dict)).strip()
    message = dict(choice.get("message") or {})
    fallback = message.get("content")
    if isinstance(fallback, str):
        return fallback
    return ""


def _normalize_stream_line(raw_line: Any) -> str:
    if raw_line is None:
        return ""
    if isinstance(raw_line, bytes):
        return raw_line.decode("utf-8", errors="replace").strip()
    return str(raw_line).strip()


def _native_ollama_base_url(base_url: str) -> str:
    clean = str(base_url or "").rstrip("/")
    if clean.endswith("/v1"):
        return clean[:-3]
    return clean
