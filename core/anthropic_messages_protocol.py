"""Anthropic Messages as a wire dialect of VOOL's one provider contract.

VOOL assembles every chat request in one shape -- the OpenAI chat-completions payload built by
``adapters/openai_compatible_adapter.py`` (egress projection, native tool schemas, output budget) --
and validates every answer through one set of readers (``_extract_openai_text``,
``_extract_native_openai_tool_result``, ``core.cloud_tool_call_contract.parse_native_tool_calls``).
An Anthropic-protocol endpoint must get those same tools, the same validation and the same failure
types, not a second implementation that drifts. So this module only TRANSLATES:

* :func:`openai_request_to_anthropic` -- the built OpenAI payload into a Messages request;
* :func:`anthropic_response_to_openai` -- a Messages response back into the OpenAI-shaped completion
  the shared readers already validate (text, ``tool_calls``, ``finish_reason``, ``usage``);
* :class:`AnthropicStreamAssembler` -- the Messages event stream into OpenAI-shaped delta events, plus
  a finalization that REQUIRES ``message_stop``.

Nothing is dropped quietly. A request field the Messages API cannot carry raises
:class:`ProtocolTranslationError` before anything is sent; an ``error`` event raises
:class:`AnthropicStreamError`; a stream that ends before ``message_stop`` raises
:class:`IncompleteStreamError` carrying what did arrive. Usage keys the provider did not report are
left out, never filled with zero.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from core.normalized_provider_result import MalformedProviderResponseError

_ALLOWED_REQUEST_FIELDS = frozenset(
    {
        "model",
        "messages",
        "max_tokens",
        "max_completion_tokens",
        "temperature",
        "top_p",
        "stream",
        "stream_options",
        "tools",
        "tool_choice",
        "stop",
    }
)

_STOP_REASONS = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
    "tool_use": "tool_calls",
    "refusal": "content_filter",
    "pause_turn": "pause_turn",
}


class ProtocolTranslationError(ValueError):
    """The request carries something the Messages dialect cannot express. Raised before sending."""

    def __init__(self, code: str) -> None:
        self.code = str(code)
        super().__init__(f"anthropic_protocol_translation:{self.code}")


class AnthropicStreamError(RuntimeError):
    """The provider sent an ``error`` event mid-stream."""

    def __init__(self, error_type: str, *, partial_text_chars: int) -> None:
        self.error_type = str(error_type or "unknown")[:64]
        self.partial_text_chars = int(partial_text_chars)
        super().__init__(f"anthropic stream error event: {self.error_type}")


class IncompleteStreamError(MalformedProviderResponseError):
    """A stream ended before its protocol's terminal marker. What arrived is kept, as evidence."""

    def __init__(self, protocol: str, *, partial_text: str, usage: Mapping[str, Any] | None, events_seen: int) -> None:
        self.protocol = str(protocol)
        self.partial_text = str(partial_text or "")
        self.usage = dict(usage or {})
        self.events_seen = int(events_seen)
        super().__init__(
            f"malformed provider response: {self.protocol} stream ended before completion "
            f"({self.events_seen} events, {len(self.partial_text)} chars received)"
        )


def _text_of(content: Any, *, where: str) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text") or ""))
            else:
                raise ProtocolTranslationError(f"non_text_part_in_{where}")
        return "\n".join(parts)
    raise ProtocolTranslationError(f"content_shape_unrecognized_in_{where}")


def _image_block(part: Mapping[str, Any]) -> dict[str, Any]:
    image = part.get("image_url")
    url = str((image or {}).get("url") if isinstance(image, Mapping) else image or "")
    if url.startswith("data:") and ";base64," in url:
        header, data = url.split(";base64,", 1)
        media_type = header[len("data:") :]
        if not media_type.startswith("image/") or not data:
            raise ProtocolTranslationError("image_data_url_invalid")
        return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}
    if url.startswith("https://"):
        return {"type": "image", "source": {"type": "url", "url": url}}
    raise ProtocolTranslationError("image_url_not_expressible")


def _user_blocks(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    if isinstance(content, list):
        blocks: list[dict[str, Any]] = []
        for part in content:
            if not isinstance(part, Mapping):
                raise ProtocolTranslationError("content_part_not_an_object")
            kind = part.get("type")
            if kind == "text":
                text = str(part.get("text") or "")
                if text:
                    blocks.append({"type": "text", "text": text})
            elif kind == "image_url":
                blocks.append(_image_block(part))
            else:
                raise ProtocolTranslationError(f"unsupported_content_part:{str(kind)[:32]}")
        return blocks
    if content is None:
        return []
    raise ProtocolTranslationError("content_shape_unrecognized")


def _tool_use_blocks(tool_calls: Any) -> list[dict[str, Any]]:
    if tool_calls is None:
        return []
    if not isinstance(tool_calls, list):
        raise ProtocolTranslationError("assistant_tool_calls_not_a_list")
    blocks: list[dict[str, Any]] = []
    for call in tool_calls:
        if not isinstance(call, Mapping) or not isinstance(call.get("function"), Mapping):
            raise ProtocolTranslationError("assistant_tool_call_malformed")
        function = call["function"]
        raw_arguments = function.get("arguments")
        if isinstance(raw_arguments, str):
            try:
                arguments = json.loads(raw_arguments) if raw_arguments.strip() else {}
            except ValueError as exc:
                raise ProtocolTranslationError("assistant_tool_call_arguments_not_json") from exc
        elif isinstance(raw_arguments, Mapping):
            arguments = dict(raw_arguments)
        elif raw_arguments is None:
            arguments = {}
        else:
            raise ProtocolTranslationError("assistant_tool_call_arguments_invalid")
        if not isinstance(arguments, dict):
            raise ProtocolTranslationError("assistant_tool_call_arguments_not_an_object")
        call_id = str(call.get("id") or "").strip()
        name = str(function.get("name") or "").strip()
        if not call_id or not name:
            raise ProtocolTranslationError("assistant_tool_call_missing_id_or_name")
        blocks.append({"type": "tool_use", "id": call_id, "name": name, "input": arguments})
    return blocks


def _append(messages: list[dict[str, Any]], role: str, blocks: list[dict[str, Any]]) -> None:
    if not blocks:
        return
    if messages and messages[-1]["role"] == role:
        messages[-1]["content"].extend(blocks)
    else:
        messages.append({"role": role, "content": list(blocks)})


def _tools(tools: Any) -> list[dict[str, Any]]:
    if not isinstance(tools, list):
        raise ProtocolTranslationError("tools_not_a_list")
    translated: list[dict[str, Any]] = []
    for tool in tools:
        function = tool.get("function") if isinstance(tool, Mapping) else None
        if not isinstance(tool, Mapping) or tool.get("type") != "function" or not isinstance(function, Mapping):
            raise ProtocolTranslationError("tool_definition_not_a_function")
        name = str(function.get("name") or "").strip()
        schema = function.get("parameters")
        if not name or not isinstance(schema, Mapping):
            raise ProtocolTranslationError("tool_definition_missing_name_or_schema")
        entry: dict[str, Any] = {"name": name, "input_schema": dict(schema)}
        description = str(function.get("description") or "").strip()
        if description:
            entry["description"] = description
        translated.append(entry)
    return translated


def _tool_choice(choice: Any) -> dict[str, Any]:
    if choice == "required":
        return {"type": "any"}
    if choice == "auto":
        return {"type": "auto"}
    if choice == "none":
        return {"type": "none"}
    if isinstance(choice, Mapping) and choice.get("type") == "function":
        name = str((choice.get("function") or {}).get("name") or "").strip() if isinstance(choice.get("function"), Mapping) else ""
        if name:
            return {"type": "tool", "name": name}
    raise ProtocolTranslationError("tool_choice_not_expressible")


def openai_request_to_anthropic(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Translate one built OpenAI chat payload into a Messages request, or refuse."""
    if not isinstance(payload, Mapping):
        raise ProtocolTranslationError("payload_not_an_object")
    if "response_format" in payload:
        raise ProtocolTranslationError("response_format_not_expressible")
    if "reasoning" in payload:
        raise ProtocolTranslationError("reasoning_control_not_expressible")
    unknown = sorted(set(payload) - _ALLOWED_REQUEST_FIELDS)
    if unknown:
        raise ProtocolTranslationError("unsupported_request_fields:" + ",".join(unknown))
    model = str(payload.get("model") or "").strip()
    if not model:
        raise ProtocolTranslationError("model_missing")
    max_tokens = payload.get("max_tokens", payload.get("max_completion_tokens"))
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens < 1:
        # The Messages API requires an explicit output ceiling; so does every paid bound built on it.
        raise ProtocolTranslationError("max_tokens_required")
    raw_messages = payload.get("messages")
    if not isinstance(raw_messages, list) or not raw_messages:
        raise ProtocolTranslationError("messages_missing")

    system_parts: list[str] = []
    messages: list[dict[str, Any]] = []
    for raw in raw_messages:
        if not isinstance(raw, Mapping):
            raise ProtocolTranslationError("message_not_an_object")
        role = str(raw.get("role") or "")
        if role in {"system", "developer"}:
            text = _text_of(raw.get("content"), where="system")
            if text.strip():
                system_parts.append(text)
            continue
        if role == "user":
            _append(messages, "user", _user_blocks(raw.get("content")))
        elif role == "assistant":
            blocks = [{"type": "text", "text": text} for text in [_text_of(raw.get("content"), where="assistant")] if text]
            blocks.extend(_tool_use_blocks(raw.get("tool_calls")))
            _append(messages, "assistant", blocks)
        elif role == "tool":
            call_id = str(raw.get("tool_call_id") or "").strip()
            if not call_id:
                raise ProtocolTranslationError("tool_result_missing_call_id")
            _append(
                messages,
                "user",
                [{"type": "tool_result", "tool_use_id": call_id, "content": _text_of(raw.get("content"), where="tool_result")}],
            )
        else:
            raise ProtocolTranslationError(f"unsupported_message_role:{role[:32]}")
    if not messages or messages[0]["role"] != "user":
        raise ProtocolTranslationError("first_message_must_be_user")

    body: dict[str, Any] = {"model": model, "max_tokens": max_tokens, "messages": messages}
    if system_parts:
        body["system"] = "\n\n".join(system_parts)
    for key in ("temperature", "top_p"):
        if payload.get(key) is not None:
            body[key] = payload[key]
    stop = payload.get("stop")
    if stop:
        body["stop_sequences"] = [str(item) for item in (stop if isinstance(stop, list) else [stop])]
    if payload.get("stream"):
        body["stream"] = True
    if payload.get("tools"):
        body["tools"] = _tools(payload["tools"])
        if payload.get("tool_choice") is not None:
            body["tool_choice"] = _tool_choice(payload["tool_choice"])
    elif payload.get("tool_choice") not in (None, "none", "auto"):
        raise ProtocolTranslationError("tool_choice_without_tools")
    return body


#: The first-party Claude API. A lane whose base URL is this host speaks the Messages dialect at
#: ``/messages`` with the key in ``x-api-key`` -- never the OpenAI-compatibility endpoint, which
#: rejects what VOOL's one payload carries on current models (``temperature`` beside ``top_p``,
#: ``response_format: json_object``).
CLAUDE_API_HOST = "api.anthropic.com"
CLAUDE_MESSAGES_PATH = "/messages"
CLAUDE_API_VERSION = "2023-06-01"
CLAUDE_EFFORTS = ("low", "medium", "high", "xhigh", "max")

#: Schema keywords the Messages structured-output grammar does not accept. They are dropped from
#: the schema SENT; the answer is still validated by VOOL against the full contract afterwards, so
#: nothing the contract demands is given up.
_UNSUPPORTED_SCHEMA_KEYWORDS = frozenset(
    {
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minLength",
        "maxLength",
        "pattern",
        "maxItems",
        "uniqueItems",
        "minProperties",
        "maxProperties",
    }
)
_SUPPORTED_STRING_FORMATS = frozenset(
    {"date-time", "time", "date", "duration", "email", "hostname", "uri", "ipv4", "ipv6", "uuid"}
)

_JSON_OBJECT_INSTRUCTION = (
    "Reply with exactly one JSON object and nothing else: no prose before or after it, no code fences."
)
_TOOL_REQUIRED_INSTRUCTION = (
    "Answer this turn by calling one of the provided tools. Do not answer in prose instead of the call."
)


def claude_output_schema(schema: Any) -> dict[str, Any] | None:
    """``schema`` in the Messages structured-output grammar, or None when it cannot be expressed.

    The grammar requires ``additionalProperties: false`` on every object. An object that declares
    its properties and merely leaves extra keys open is tightened to its declared keys -- every
    answer that fits the tightened schema also fits the original. An object that is ONLY an open
    map (no declared properties: ``{"type": "object", "additionalProperties": true}``) cannot be
    tightened without changing what it means, so the whole schema is reported inexpressible and
    the caller asks for JSON in the instructions instead.
    """
    if not isinstance(schema, Mapping):
        return None
    seen_refs = "$ref" in json.dumps(schema, default=str)
    if seen_refs:
        return None  # recursion cannot be ruled out cheaply; the instructed form is exact enough

    def _convert(node: Any) -> Any:
        if isinstance(node, list):
            return [_convert(item) for item in node]
        if not isinstance(node, Mapping):
            return node
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key in _UNSUPPORTED_SCHEMA_KEYWORDS:
                continue
            if key == "minItems" and not (isinstance(value, int) and value in (0, 1)):
                continue
            if key == "format" and str(value) not in _SUPPORTED_STRING_FORMATS:
                continue
            if key in {"properties", "$defs", "definitions"} and isinstance(value, Mapping):
                out[key] = {str(name): _convert(sub) for name, sub in value.items()}
            elif key in {"items", "anyOf", "allOf", "oneOf", "not", "additionalProperties"}:
                out[key] = _convert(value)
            else:
                out[key] = value
        is_object = out.get("type") == "object" or "properties" in out
        if is_object:
            extra = out.get("additionalProperties")
            if extra is not None and extra is not False:
                if not out.get("properties"):
                    raise ValueError("open_map")
            out["additionalProperties"] = False
        return out

    try:
        converted = _convert(schema)
    except ValueError:
        return None
    return converted if isinstance(converted, dict) else None


def _claude_effort(reasoning: Any) -> str | None:
    """The Messages ``output_config.effort`` for the payload's ``reasoning`` control.

    Current Claude models think adaptively and some cannot turn thinking off at all, so a request
    that wants no reasoning is sent at the lowest effort rather than with a disable flag the model
    would refuse.
    """
    if not isinstance(reasoning, Mapping):
        return None
    if reasoning.get("enabled") is False:
        return "low"
    effort = str(reasoning.get("effort") or "").strip().lower()
    if effort in {"none", "minimal"}:
        return "low"
    return effort if effort in CLAUDE_EFFORTS else None


def openai_request_to_claude_api(payload: Mapping[str, Any]) -> dict[str, Any]:
    """The built OpenAI chat payload as a first-party Claude API Messages request, or refuse.

    The same translation as :func:`openai_request_to_anthropic`, plus what the first-party API
    requires of current models (Claude Haiku 5.5, Sonnet 5.5, Opus 5.5):

    * no ``temperature`` / ``top_p`` -- current models reject non-default sampling values;
    * structured output through ``output_config.format`` (``json_schema``) when the contract's
      schema can be expressed there, otherwise one plain instruction to answer with JSON only;
      ``response_format: json_object`` has no Messages form and is never sent;
    * ``tool_choice`` ``auto`` -- forced tool choice is refused by current models -- with an
      instruction that a tool call is the answer when the payload required one;
    * the ``reasoning`` control as ``output_config.effort``.

    Whatever the answer looks like, VOOL's readers validate it against the full contract after;
    a reply that is not the JSON or the tool call the turn needed fails there, by name.
    """
    if not isinstance(payload, Mapping):
        raise ProtocolTranslationError("payload_not_an_object")
    working = dict(payload)
    working.pop("temperature", None)
    working.pop("top_p", None)
    reasoning = working.pop("reasoning", None)
    response_format = working.pop("response_format", None)
    tool_choice = working.pop("tool_choice", None)
    instructions: list[str] = []
    output_config: dict[str, Any] = {}

    if working.get("tools"):
        if tool_choice == "required" or (isinstance(tool_choice, Mapping) and tool_choice.get("type") == "function"):
            named = ""
            if isinstance(tool_choice, Mapping) and isinstance(tool_choice.get("function"), Mapping):
                named = str(tool_choice["function"].get("name") or "").strip()
            instructions.append(
                f"Answer this turn by calling the {named} tool." if named else _TOOL_REQUIRED_INSTRUCTION
            )
            working["tool_choice"] = "auto"
        elif tool_choice is not None:
            working["tool_choice"] = tool_choice
    elif tool_choice not in (None, "none", "auto"):
        raise ProtocolTranslationError("tool_choice_without_tools")

    if response_format is not None:
        if not isinstance(response_format, Mapping):
            raise ProtocolTranslationError("response_format_not_an_object")
        kind = response_format.get("type")
        if kind == "json_schema":
            spec = response_format.get("json_schema") if isinstance(response_format.get("json_schema"), Mapping) else {}
            schema = claude_output_schema(spec.get("schema"))
            if schema is not None:
                output_config["format"] = {"type": "json_schema", "schema": schema}
            else:
                instructions.append(
                    _JSON_OBJECT_INSTRUCTION
                    + " It must match this JSON schema: "
                    + json.dumps(spec.get("schema"), sort_keys=True, separators=(",", ":"))
                )
        elif kind == "json_object":
            instructions.append(_JSON_OBJECT_INSTRUCTION)
        elif kind != "text":
            raise ProtocolTranslationError("response_format_not_expressible")

    body = openai_request_to_anthropic(working)
    effort = _claude_effort(reasoning)
    if effort:
        output_config["effort"] = effort
    if output_config:
        body["output_config"] = output_config
    if instructions:
        body["system"] = "\n\n".join([part for part in [body.get("system", ""), *instructions] if part])
    return body


def unfenced_json_text(text: str) -> str:
    """``text`` without one Markdown code fence wrapped around the whole reply.

    Asked for JSON by instruction (the contract had no schema the structured-output grammar can
    carry), Claude can still wrap the object in ```json ... ```. Only a fence around the WHOLE
    reply is removed; anything else is returned as written, for the contract to judge.
    """
    stripped = str(text or "").strip()
    if not (stripped.startswith("```") and stripped.endswith("```")) or len(stripped) < 6:
        return text
    inner = stripped[3:-3]
    first_newline = inner.find("\n")
    if first_newline < 0:
        return text
    language = inner[:first_newline].strip().lower()
    if language not in {"", "json"} or "```" in inner:
        return text
    return inner[first_newline + 1 :].strip()


def claude_api_error_message(status_code: int, body: Any) -> str:
    """One plain sentence for a Claude API refusal, for the person and the failure record.

    Never carries the key or the request; only the API's own error type and message.
    """
    error = body.get("error") if isinstance(body, Mapping) and isinstance(body.get("error"), Mapping) else {}
    kind = str(error.get("type") or "").strip()
    message = str(error.get("message") or "").strip()[:300]
    if status_code == 401 or kind == "authentication_error":
        return "Claude refused the API key (401). Check the key in Settings > Models, or paste a new one."
    if status_code == 403 or kind == "permission_error":
        return f"Claude says this key may not do that (403): {message or 'permission denied'}."
    if status_code == 404 or kind == "not_found_error":
        return f"Claude does not know that model (404): {message or 'model not found'}. Pick a model from the list."
    if status_code == 429 or kind == "rate_limit_error":
        return "Claude is rate limiting this key (429). Wait a moment and try again."
    if status_code == 400 and "credit balance" in message.lower():
        return "The Claude account has no credit left (400). Add credit in the Claude Console."
    if status_code >= 500 or kind in {"api_error", "overloaded_error"}:
        return f"Claude is having trouble right now ({status_code}). Try again shortly."
    return f"Claude rejected the request ({status_code}{', ' + kind if kind else ''}): {message or 'no detail given'}."


#: The sentences :func:`claude_api_error_message` writes for refusals a retry cannot fix.
_CLAUDE_FINAL_REFUSALS = (
    "Claude refused the API key",
    "Claude says this key may not",
    "Claude does not know that model",
    "The Claude account has no credit left",
)


def claude_refusal_sentence(error: Any) -> str:
    """The plain sentence a Claude API refusal carried, when it is one a retry cannot fix.

    Only text this module wrote is ever returned (it names the API's own error message, never the
    key, the request or a URL), so a reply surface may show it as is."""
    text = " ".join(str(error or "").split())
    for start in _CLAUDE_FINAL_REFUSALS:
        index = text.find(start)
        if index >= 0:
            sentence = text[index:]
            cut = sentence.find(" [retry_after=")
            return (sentence[:cut] if cut >= 0 else sentence)[:400]
    return ""


def normalize_anthropic_usage(*sources: Mapping[str, Any] | None) -> dict[str, Any]:
    """Merge Messages usage blocks (start + delta) into VOOL's usage keys, reported keys only.

    ``input_tokens`` in the Messages API EXCLUDES cache reads and writes, which are reported beside
    it; the billable prompt total is their sum, and that is what ``prompt_tokens`` carries. The raw
    Anthropic keys are kept alongside, so nothing is lost in the merge.
    """
    merged: dict[str, int] = {}
    for source in sources:
        for key, value in dict(source or {}).items():
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                continue
            merged[str(key)] = value
    usage: dict[str, Any] = {}
    for key in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"):
        if key in merged:
            usage[key] = merged[key]
    prompt_parts = [merged[key] for key in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens") if key in merged]
    if "input_tokens" in merged:
        usage["prompt_tokens"] = sum(prompt_parts)
    if "output_tokens" in merged:
        usage["completion_tokens"] = merged["output_tokens"]
    if "prompt_tokens" in usage and "completion_tokens" in usage:
        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
    return usage


def _openai_tool_call(block: Mapping[str, Any]) -> dict[str, Any]:
    call_id = str(block.get("id") or "").strip()
    name = str(block.get("name") or "").strip()
    arguments = block.get("input")
    if not call_id or not name or not isinstance(arguments, Mapping):
        raise MalformedProviderResponseError("malformed provider response: anthropic tool_use block is incomplete")
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(dict(arguments), sort_keys=True, separators=(",", ":"))},
    }


def anthropic_response_to_openai(payload: Any) -> dict[str, Any]:
    """A Messages response as the OpenAI-shaped completion the shared readers validate."""
    if not isinstance(payload, Mapping):
        raise MalformedProviderResponseError("malformed provider response: anthropic response is not an object")
    if payload.get("type") == "error":
        error = payload.get("error") if isinstance(payload.get("error"), Mapping) else {}
        raise MalformedProviderResponseError(
            f"malformed provider response: anthropic error body ({str(error.get('type') or 'unknown')[:64]})"
        )
    content = payload.get("content")
    if not isinstance(content, list):
        raise MalformedProviderResponseError("malformed provider response: anthropic content is not a list")
    texts: list[str] = []
    calls: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, Mapping):
            raise MalformedProviderResponseError("malformed provider response: anthropic content block is not an object")
        kind = block.get("type")
        if kind == "text":
            texts.append(str(block.get("text") or ""))
        elif kind == "tool_use":
            calls.append(_openai_tool_call(block))
        elif kind in {"thinking", "redacted_thinking"}:
            continue  # reasoning is not answer text; it is never promoted into content
        else:
            raise MalformedProviderResponseError(
                f"malformed provider response: anthropic content block type {str(kind)[:32]!r} was not requested"
            )
    message: dict[str, Any] = {"role": "assistant", "content": "".join(texts)}
    if calls:
        message["tool_calls"] = calls
    stop_reason = payload.get("stop_reason")
    return {
        "id": str(payload.get("id") or ""),
        "object": "chat.completion",
        "model": payload.get("model"),
        "choices": [{"index": 0, "message": message, "finish_reason": _STOP_REASONS.get(str(stop_reason), "") if stop_reason else ""}],
        "usage": normalize_anthropic_usage(payload.get("usage") if isinstance(payload.get("usage"), Mapping) else None),
    }


class AnthropicStreamAssembler:
    """Feed SSE lines; get OpenAI-shaped delta events back; finalize only after ``message_stop``."""

    def __init__(self) -> None:
        self._data_lines: list[str] = []
        self._events = 0
        self._text: list[str] = []
        self._blocks: dict[int, dict[str, Any]] = {}
        self._start_usage: dict[str, Any] = {}
        self._delta_usage: dict[str, Any] = {}
        self._stop_reason: str | None = None
        self._finished = False
        self.model: str | None = None
        self.message_id = ""

    @property
    def finished(self) -> bool:
        return self._finished

    @property
    def partial_text(self) -> str:
        return "".join(self._text)

    def feed_line(self, raw_line: str | bytes | None) -> list[dict[str, Any]]:
        line = raw_line.decode("utf-8", errors="replace") if isinstance(raw_line, bytes) else str(raw_line or "")
        line = line.rstrip("\r\n")
        if line == "":
            return self._flush()
        if line.startswith(":"):
            return []
        if line.startswith("data:"):
            self._data_lines.append(line[5:].lstrip())
        return []

    def _flush(self) -> list[dict[str, Any]]:
        if not self._data_lines:
            return []
        raw = "\n".join(self._data_lines)
        self._data_lines = []
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise MalformedProviderResponseError("malformed provider response: anthropic stream event is not JSON") from exc
        if not isinstance(data, dict):
            raise MalformedProviderResponseError("malformed provider response: anthropic stream event is not an object")
        self._events += 1
        return self._dispatch(data)

    def _dispatch(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        if self._finished:
            raise MalformedProviderResponseError("malformed provider response: anthropic stream continued after message_stop")
        kind = data.get("type")
        if kind == "message_start":
            message = data.get("message") if isinstance(data.get("message"), Mapping) else {}
            self.model = str(message.get("model")) if message.get("model") else None
            self.message_id = str(message.get("id") or "")
            if isinstance(message.get("usage"), Mapping):
                self._start_usage = dict(message["usage"])
            return []
        if kind == "content_block_start":
            index = data.get("index")
            block = data.get("content_block")
            if not isinstance(index, int) or not isinstance(block, Mapping):
                raise MalformedProviderResponseError("malformed provider response: anthropic content_block_start is incomplete")
            self._blocks[index] = {"type": block.get("type"), "id": block.get("id"), "name": block.get("name"), "json": [], "closed": False}
            text = str(block.get("text") or "") if block.get("type") == "text" else ""
            if text:
                self._text.append(text)
                return [{"choices": [{"index": 0, "delta": {"content": text}}], "model": self.model}]
            return []
        if kind == "content_block_delta":
            index = data.get("index")
            delta = data.get("delta") if isinstance(data.get("delta"), Mapping) else {}
            block = self._blocks.get(index) if isinstance(index, int) else None
            if block is None:
                raise MalformedProviderResponseError("malformed provider response: anthropic delta for an unopened block")
            delta_type = delta.get("type")
            if delta_type == "text_delta":
                text = str(delta.get("text") or "")
                if text:
                    self._text.append(text)
                    return [{"choices": [{"index": 0, "delta": {"content": text}}], "model": self.model}]
                return []
            if delta_type == "input_json_delta":
                block["json"].append(str(delta.get("partial_json") or ""))
                return []
            return []  # thinking/signature deltas: reasoning, never answer text
        if kind == "content_block_stop":
            index = data.get("index")
            block = self._blocks.get(index) if isinstance(index, int) else None
            if block is None:
                raise MalformedProviderResponseError("malformed provider response: anthropic stop for an unopened block")
            block["closed"] = True
            return []
        if kind == "message_delta":
            delta = data.get("delta") if isinstance(data.get("delta"), Mapping) else {}
            if delta.get("stop_reason"):
                self._stop_reason = str(delta["stop_reason"])
            if isinstance(data.get("usage"), Mapping):
                self._delta_usage.update(dict(data["usage"]))
            return []
        if kind == "message_stop":
            self._finished = True
            return []
        if kind == "ping":
            return []
        if kind == "error":
            error = data.get("error") if isinstance(data.get("error"), Mapping) else {}
            raise AnthropicStreamError(str(error.get("type") or "unknown"), partial_text_chars=len(self.partial_text))
        return []  # the protocol reserves the right to add event types; unknown ones carry no answer

    def usage(self) -> dict[str, Any]:
        return normalize_anthropic_usage(self._start_usage, self._delta_usage)

    def finalize(self) -> dict[str, Any]:
        """The terminal facts, or :class:`IncompleteStreamError` when ``message_stop`` never came."""
        tail = self._flush()
        if tail:  # pragma: no cover - a data event with no trailing blank line at end of stream
            pass
        if not self._finished:
            raise IncompleteStreamError(
                "anthropic", partial_text=self.partial_text, usage=self.usage(), events_seen=self._events
            )
        calls: list[dict[str, Any]] = []
        for index in sorted(self._blocks):
            block = self._blocks[index]
            if block["type"] != "tool_use":
                continue
            if not block["closed"]:
                raise MalformedProviderResponseError("malformed provider response: anthropic tool_use block never closed")
            raw = "".join(block["json"]).strip()
            try:
                arguments = json.loads(raw) if raw else {}
            except ValueError as exc:
                raise MalformedProviderResponseError("malformed provider response: anthropic tool_use input is not JSON") from exc
            calls.append(_openai_tool_call({"id": block["id"], "name": block["name"], "input": arguments}))
        return {
            "text": self.partial_text,
            "tool_calls": calls,
            "finish_reason": _STOP_REASONS.get(str(self._stop_reason), "") if self._stop_reason else "",
            "usage": self.usage(),
            "model": self.model,
            "message_id": self.message_id,
        }


__all__ = [
    "CLAUDE_API_HOST",
    "CLAUDE_API_VERSION",
    "CLAUDE_MESSAGES_PATH",
    "AnthropicStreamAssembler",
    "AnthropicStreamError",
    "IncompleteStreamError",
    "ProtocolTranslationError",
    "anthropic_response_to_openai",
    "claude_api_error_message",
    "claude_output_schema",
    "claude_refusal_sentence",
    "normalize_anthropic_usage",
    "openai_request_to_anthropic",
    "openai_request_to_claude_api",
    "unfenced_json_text",
]
