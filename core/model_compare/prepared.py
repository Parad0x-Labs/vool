"""An in-process capability for the exact prepared input, never caller JSON."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
from dataclasses import dataclass
from typing import Any


def input_digest(payload: dict) -> str:
    from core.chat_attachments import flatten_message_for_ollama
    # The existing native adapter's text join and image ordering define this
    # portable input comparison; generation controls remain visible per preview.
    messages = [flatten_message_for_ollama(copy.deepcopy(m)) for m in payload.get("messages", [])]
    values = {"messages": messages, **{k: v for k, v in payload.items() if k in {"system", "prompt", "tools", "tool_choice", "response_format", "format"}}}
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def has_images(payload: dict) -> bool:
    return any(m.get("images") or any(isinstance(p, dict) and p.get("type") == "image_url" for p in (m.get("content") if isinstance(m.get("content"), list) else []))
               for m in payload.get("messages", []))


def source_payload(source) -> dict:
    original = json.loads(source.body)
    if not isinstance(original, dict) or not isinstance(original.get("messages"), list):
        raise ValueError("This prepared request has no readable conversation input.")
    if source.request_format in {"openai", "ollama"}:
        return original
    if source.request_format != "anthropic":
        raise ValueError("This prepared request uses an unsupported input format.")
    # The existing UsePod translator emits this bounded text subset. Never
    # discard an unknown setting, tool history or content block to make it fit.
    allowed = {"model", "messages", "system", "max_tokens", "temperature", "top_p", "stop_sequences", "stream"}
    if set(original) - allowed:
        raise ValueError("These Messages settings cannot be compared with this request format.")
    result = {key: copy.deepcopy(value) for key, value in original.items()
              if key in {"model", "max_tokens", "temperature", "top_p", "stream"}}
    messages = []
    if "system" in original:
        if not isinstance(original["system"], str):
            raise ValueError("This system input cannot use the comparison request format.")
        messages.append({"role": "system", "content": original["system"]})
    for message in original.get("messages", []):
        if (not isinstance(message, dict) or set(message) - {"role", "content"}
                or message.get("role") not in {"user", "assistant"}):
            raise ValueError("This tool history needs its original request format.")
        content = message.get("content")
        if not isinstance(content, str) and (not isinstance(content, list) or any(
                not isinstance(block, dict) or set(block) != {"type", "text"}
                or block.get("type") != "text" or not isinstance(block.get("text"), str)
                for block in content)):
            raise ValueError("These input blocks need their original request format.")
        messages.append(copy.deepcopy(message))
    if not messages:
        raise ValueError("This prepared request contains no comparable input.")
    result["messages"] = messages
    if "stop_sequences" in original:
        result["stop"] = copy.deepcopy(original["stop_sequences"])
    return result


def format_compatible(original: dict, *, source_native: bool, target_native: bool) -> str:
    tools = original.get("tools") or []
    if not isinstance(tools, list) or any(not isinstance(tool, dict) or tool.get("type") != "function"
                                          or not isinstance(tool.get("function"), dict) for tool in tools):
        return "Provider-run tools need their original approval and cannot be compared here."
    # A provider-run search can execute and charge on receipt. Only inert
    # function definitions are replayable; comparison never dispatches a call.
    controlled = tools or original.get("tool_choice") or original.get("response_format") or original.get("format")
    history = any(m.get("tool_calls") or m.get("role") == "tool" for m in original.get("messages", []))
    if source_native != target_native and (controlled or history):
        return "Choose a model with the same request format for these tools or output settings."
    if source_native != target_native:
        allowed = ({"model", "messages", "stream", "options", "keep_alive"} if source_native else
                   {"model", "messages", "stream", "stream_options", "max_tokens", "max_completion_tokens",
                    "temperature", "top_p", "stop", "options"})
        options = original.get("options") or {}
        # Runner sizing stays with the selected model's resource authority.
        # Unknown generation controls cannot disappear during conversion.
        option_keys = {"num_ctx", "num_thread", "num_gpu"} | ({"temperature", "top_p", "stop", "num_predict"} if source_native else set())
        if (set(original) - allowed or not isinstance(options, dict) or set(options) - option_keys):
            return "Choose a model with the same request format for these generation settings."
    return ""


def portable_messages(payload: dict, *, native: bool) -> list[dict]:
    from core.chat_attachments import flatten_message_for_ollama, sniff_media_type
    messages = copy.deepcopy(payload.get("messages", []))
    result = []
    for message in messages:
        if native:
            flat = flatten_message_for_ollama(message)
            if isinstance(flat.get("content"), list):
                raise ValueError("This input cannot use the selected model's request format.")
            result.append(flat)
        else:
            images = message.pop("images", [])
            if images:
                parts = [{"type": "text", "text": str(message.get("content") or "")}]
                for encoded in images:
                    media = sniff_media_type(base64.b64decode(encoded, validate=True))
                    if media not in {"image/png", "image/jpeg", "image/webp"}:
                        raise ValueError("This image format cannot be compared here.")
                    parts.append({"type": "image_url", "image_url": {"url": f"data:{media};base64,{encoded}"}})
                message["content"] = parts
            result.append(message)
    return result


@dataclass(frozen=True)
class PreparedReplay:
    preview_id: str
    digest: str
    target_provider: str
    target_model: str
    timeout_seconds: float = 60

    def source(self):
        from core.disclosure.wire import get_preview
        item = get_preview(self.preview_id)
        source_payload(item)  # Validate format without silently dropping input.
        if item.digest != self.digest or not item.ready or not item.sent:
            raise ValueError("The prepared request is no longer available.")
        from core.disclosure.rules import read_rules
        if read_rules(item.project_id)["revision"] != item.rule_revision:
            raise ValueError("Project rules changed. Prepare this task again before comparing.")
        return item

    def apply(self, payload: dict, *, provider: str, url: str) -> dict:
        if provider != self.target_provider or str(payload.get("model") or "") != self.target_model:
            raise ValueError("The comparison model changed. Nothing was sent.")
        source = self.source()
        original = source_payload(source)
        # Use the existing native text/image carrier when the protocols differ.
        # Historical executable tool carriers need their original protocol.
        source_native = source.request_format == 'ollama'
        target_native = url.rstrip('/').endswith('/api/chat')
        reason = format_compatible(original, source_native=source_native, target_native=target_native)
        if reason:
            raise ValueError(reason)
        if source_native != target_native:
            if any(m.get("tool_calls") or m.get("role") == "tool" for m in original.get("messages", [])):
                raise ValueError("Choose a model with the same request format for this tool history.")
            result = copy.deepcopy(payload)
            result["messages"] = portable_messages(original, native=target_native)
            controls = original.get("options", {}) if source_native else original
            destination_controls = result.setdefault("options", {}) if target_native else result
            for key in ("temperature", "top_p", "stop"):
                if key in controls:
                    destination_controls[key] = copy.deepcopy(controls[key])
            ceiling = controls.get("num_predict") if source_native else original.get("max_tokens", original.get("max_completion_tokens"))
            if ceiling is not None:
                destination_controls["num_predict" if target_native else "max_tokens"] = ceiling
        else:
            result = copy.deepcopy(original)
        result["model"] = payload["model"]
        result["stream"] = bool(payload.get("stream"))
        if result["stream"] and not target_native:
            result["stream_options"] = {"include_usage": True}
        else:
            result.pop("stream_options", None)
        return result


def replay_for(request: Any) -> PreparedReplay | None:
    value = (getattr(request, "metadata", None) or {}).get("_comparison_prepared")
    return value if isinstance(value, PreparedReplay) else None
