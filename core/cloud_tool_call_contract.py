from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from copy import deepcopy
from typing import Any

from core.cloud_provider_contract import CloudToolCall, CloudToolDefinition

_NATIVE_NAME_LIMIT = 64
_NATIVE_NAME_RE = re.compile(r"[^A-Za-z0-9_-]+")


class ToolCallParseError(ValueError):
    """A tool-call envelope failed a specific, named check.

    Subclasses ``ValueError`` deliberately: every existing ``except ValueError`` catch site
    (both provider lanes wrap parse failures the same way) keeps working unchanged. New call
    sites can catch a specific subclass instead, to report a typed terminal state
    (UNKNOWN_TOOL_NAME, MALFORMED_TOOL_ARGUMENTS, DUPLICATE_TOOL_CALL) rather than generic prose.
    """


class UnknownToolNameError(ToolCallParseError):
    """A tool call named a function that was never offered this turn."""


class MalformedToolArgumentsError(ToolCallParseError):
    """A tool call's arguments were not valid JSON, or did not parse to an object."""


class DuplicateToolCallError(ToolCallParseError):
    """The same call (by id, or by name+arguments when no id was sent) appeared twice in one batch."""


def build_cloud_tool_definitions(specs: Iterable[dict[str, Any]]) -> tuple[CloudToolDefinition, ...]:
    """Translate the runtime catalog into deterministic, strict native function schemas.

    Runtime intent names contain dots, which OpenAI-compatible function names do not allow.
    The provider-visible name is therefore encoded while ``intent`` remains the sole execution
    identity. Duplicate runtime specs are merged instead of publishing ambiguous functions.
    """

    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for raw_spec in specs:
        if not isinstance(raw_spec, dict):
            continue
        intent = str(raw_spec.get("intent") or "").strip()
        if not intent:
            continue
        if intent not in merged:
            merged[intent] = {
                "description": str(raw_spec.get("description") or intent).strip(),
                "arguments": {},
            }
            order.append(intent)
        arguments = raw_spec.get("arguments")
        if isinstance(arguments, dict):
            merged[intent]["arguments"].update(arguments)
        declared = raw_spec.get("json_schema")
        if isinstance(declared, dict) and declared:
            previous = merged[intent].get("json_schema")
            if previous is not None and previous != declared:
                raise ValueError(f"conflicting declared schemas for runtime tool {intent}")
            merged[intent]["json_schema"] = deepcopy(declared)


    definitions: list[CloudToolDefinition] = []
    names: set[str] = set()
    for intent in order:
        item = merged[intent]
        native_name = _unique_native_name(intent, names)
        names.add(native_name)
        definitions.append(
            CloudToolDefinition(
                intent=intent,
                name=native_name,
                description=str(item["description"] or intent)[:1024],
                parameters=(deepcopy(item["json_schema"]) if item.get("json_schema")
                            else _parameters_for_intent(intent, dict(item["arguments"]))),
                # A registered schema may contain optional fields and open nested maps.
                # Preserve that contract; it does not promise OpenAI's strict schema subset.
                strict=not bool(item.get("json_schema")),
            )
        )
    return tuple(definitions)


def openai_tool_payload(definition: CloudToolDefinition) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": definition.name,
            "description": definition.description,
            "parameters": definition.parameters,
            "strict": bool(definition.strict),
        },
    }


def parse_native_tool_calls(
    raw_calls: Any,
    *,
    definitions: tuple[CloudToolDefinition, ...],
) -> tuple[CloudToolCall, ...]:
    """Validate a provider tool-call envelope and resolve it to runtime intent identity.

    Fails the WHOLE batch closed on any single bad member -- an unregistered name, malformed
    arguments, or a repeated call -- rather than executing the valid half of a reply that also
    invented or duplicated something. Each raises a distinct, named subclass of
    ``ToolCallParseError`` so a caller can report a typed terminal state instead of generic prose.
    """

    if not isinstance(raw_calls, list) or not raw_calls:
        raise ValueError("expected at least one native tool call")
    definition_by_name = {item.name: item for item in definitions}
    parsed: list[CloudToolCall] = []
    seen_call_ids: set[str] = set()
    seen_signatures: set[tuple[str, str]] = set()
    for raw_call in raw_calls:
        if not isinstance(raw_call, dict):
            raise ValueError("native tool call is not an object")
        if str(raw_call.get("type") or "function") != "function":
            raise ValueError("native tool call type is not function")
        function = raw_call.get("function")
        if not isinstance(function, dict):
            raise ValueError("native tool call has no function object")
        name = str(function.get("name") or "").strip()
        definition = definition_by_name.get(name)
        if definition is None:
            raise UnknownToolNameError(f"native tool call named an unregistered function: {name!r}")
        arguments = _parse_arguments(function.get("arguments"))
        _validate_arguments(arguments, definition.parameters)
        call_id = str(raw_call.get("id") or "").strip()
        _check_not_duplicate(call_id, name, arguments, seen_call_ids=seen_call_ids, seen_signatures=seen_signatures)
        parsed.append(
            CloudToolCall(
                call_id=call_id,
                intent=definition.intent,
                name=definition.name,
                arguments=arguments,
            )
        )
    return tuple(parsed)


def _check_not_duplicate(
    call_id: str,
    name: str,
    arguments: dict[str, Any],
    *,
    seen_call_ids: set[str],
    seen_signatures: set[tuple[str, str]],
) -> None:
    """Deterministic duplicate detection: by call_id when the provider sent one (Ollama does not
    always), else by an exact (name, canonicalized-arguments) match. A genuinely different call
    to the SAME tool with DIFFERENT arguments is not a duplicate and must not be rejected."""

    if call_id:
        if call_id in seen_call_ids:
            raise DuplicateToolCallError(f"duplicate native tool call id: {call_id!r}")
        seen_call_ids.add(call_id)
        return
    signature = (name, json.dumps(arguments, sort_keys=True, default=str))
    if signature in seen_signatures:
        raise DuplicateToolCallError(f"duplicate native tool call: {name!r} with identical arguments")
    seen_signatures.add(signature)


def canonical_tool_call_text(call: CloudToolCall) -> str:
    payload: dict[str, Any] = {"intent": call.intent, "arguments": call.arguments}
    if call.call_id:
        payload["_native_tool_call_id"] = call.call_id
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _unique_native_name(intent: str, existing: set[str]) -> str:
    base = _NATIVE_NAME_RE.sub("_", intent.replace(".", "__")).strip("_") or "tool"
    if len(base) > _NATIVE_NAME_LIMIT:
        digest = hashlib.sha256(intent.encode("utf-8")).hexdigest()[:10]
        base = f"{base[: _NATIVE_NAME_LIMIT - 11]}_{digest}"
    candidate = base
    if candidate in existing:
        digest = hashlib.sha256(intent.encode("utf-8")).hexdigest()[:10]
        candidate = f"{base[: _NATIVE_NAME_LIMIT - 11]}_{digest}"
    return candidate


def _parameters_for_intent(intent: str, arguments: dict[str, Any]) -> dict[str, Any]:
    # A direct response is the native no-more-tools branch and must carry its answer.
    if intent == "respond.direct" and "message" not in arguments:
        arguments["message"] = "string"

    properties: dict[str, Any] = {}
    required: list[str] = []
    for key, descriptor in arguments.items():
        argument_name = str(key or "").strip()
        if not argument_name:
            continue
        schema, optional = _argument_schema(descriptor)
        if optional:
            value_type = schema.get("type")
            if isinstance(value_type, str):
                schema["type"] = [value_type, "null"]
            elif isinstance(value_type, list) and "null" not in value_type:
                schema["type"] = [*value_type, "null"]
            elif isinstance(schema.get("anyOf"), list) and {"type": "null"} not in schema["anyOf"]:
                schema["anyOf"] = [*schema["anyOf"], {"type": "null"}]
        properties[argument_name] = schema
        # Strict OpenAI schemas require every property in required; optionality is encoded by null.
        required.append(argument_name)
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


_HINT_TYPE_WORDS: dict[str, str] = {
    "string": "string", "str": "string", "text": "string", "path": "string",
    "integer": "integer", "int": "integer", "number": "number", "float": "number",
    "boolean": "boolean", "bool": "boolean",
    "list": "array", "array": "array", "object": "object", "dict": "object", "mapping": "object",
}
_HINT_UNION_MEMBER_RE = re.compile(r"(?P<word>[a-z_]+)(?:\[(?P<item>[a-z_]+)\])?$")


def _declared_type_union(lowered_hint: str) -> list[dict[str, Any]]:
    """The member schemas of a hint that OPENS with a type union, in declared order; [] otherwise.

    Only a union of type words counts ("string|list[string]", "dict|str (optional)"). A value list
    ("normal|dark-null", "ok|error") names values, not types, and keeps the single-type reading.
    """
    head = lowered_hint.split(None, 1)[0].split("(", 1)[0] if lowered_hint.strip() else ""
    if "|" not in head:
        return []
    members: list[dict[str, Any]] = []
    for token in head.split("|"):
        match = _HINT_UNION_MEMBER_RE.match(token.strip())
        json_type = _HINT_TYPE_WORDS.get(match.group("word")) if match else None
        if match is None or json_type is None:
            return []
        member: dict[str, Any] = {"type": json_type}
        if json_type == "array":
            item_type = _HINT_TYPE_WORDS.get(match.group("item") or "")
            member["items"] = {"type": item_type} if item_type else {}
        if json_type == "object":
            member["additionalProperties"] = True
        if member not in members:
            members.append(member)
    return members if len(members) > 1 else []


def _argument_schema(descriptor: Any) -> tuple[dict[str, Any], bool]:
    if isinstance(descriptor, dict):
        schema = dict(descriptor)
        optional = bool(schema.pop("optional", False))
        schema.setdefault("type", "string")
        return schema, optional

    text = str(descriptor or "string").strip()
    lowered = text.lower()
    optional = "optional" in lowered or "ignored" in lowered
    union = _declared_type_union(lowered)
    if union:
        # A declared union reaches the model as every member, not as its first word: email.draft.save
        # declares "to": "string|list[string]" and its handler takes both, yet a served model's list
        # was refused as "has type array, expected ['string']". anyOf is the union form the strict
        # tool-schema subset documents (nested, never at the root); a bare type list is documented
        # only as the nullable form.
        return {"anyOf": union, "description": text[:512]}, optional
    if lowered.startswith("boolean"):
        value_type: str | list[str] = "boolean"
    elif lowered.startswith(("integer", "int ")):
        value_type = "integer"
    elif lowered.startswith("number"):
        value_type = "number"
    elif lowered.startswith(("list", "array")):
        value_type = "array"
    elif lowered.startswith(("object", "task_envelope")):
        value_type = "object"
    else:
        value_type = "string"
    schema: dict[str, Any] = {"type": value_type, "description": text[:512]}
    if value_type == "array":
        schema["items"] = {"type": "string"} if "string" in lowered else {}
    if value_type == "object":
        schema["additionalProperties"] = True
    return schema, optional


def _parse_arguments(raw_arguments: Any) -> dict[str, Any]:
    if raw_arguments is None or raw_arguments == "":
        return {}
    if isinstance(raw_arguments, dict):
        return dict(raw_arguments)
    if not isinstance(raw_arguments, str):
        raise MalformedToolArgumentsError("native tool arguments are neither JSON text nor an object")
    try:
        parsed = json.loads(raw_arguments)
    except Exception as exc:
        raise MalformedToolArgumentsError("native tool arguments are invalid JSON") from exc
    if not isinstance(parsed, dict):
        raise MalformedToolArgumentsError("native tool arguments are not an object")
    return parsed


def allowed_argument_types(property_schema: Any) -> set[str]:
    """The JSON types one parameter schema admits: its ``type`` (a name or a list), or the member
    types of its ``anyOf`` union. The one reading the validator and the argument coercion share."""
    if not isinstance(property_schema, dict):
        return set()
    declared = property_schema.get("type")
    if declared is None and isinstance(property_schema.get("anyOf"), list):
        found: set[str] = set()
        for member in property_schema["anyOf"]:
            found |= allowed_argument_types(member)
        return found
    return {str(item) for item in (declared if isinstance(declared, list) else [declared]) if item}


def _validate_arguments(arguments: dict[str, Any], schema: dict[str, Any]) -> None:
    properties = dict(schema.get("properties") or {})
    unknown = sorted(set(arguments) - set(properties))
    if unknown and schema.get("additionalProperties") is False:
        raise ValueError(f"native tool arguments contain unknown keys: {','.join(unknown)}")
    for key in list(schema.get("required") or []):
        if key in arguments:
            continue
        # _parameters_for_intent's OWN documented convention: an "optional" argument is encoded
        # as a NULLABLE type with the key still listed in `required` (OpenAI strict-mode schemas
        # require every property be in `required`; optionality is expressed via the type union
        # instead). That convention describes the WIRE schema sent to the provider -- it must not
        # also mean a model that omits the key entirely (rather than sending it as literal null)
        # gets rejected here. Every non-strict lane (Ollama is sent strict=False explicitly) and
        # many strict-mode providers in practice simply omit an argument the model chose not to
        # set, so treating omission as a hard validation failure would reject a genuinely optional
        # argument across the WHOLE tool catalog, not just one field.
        if "null" in allowed_argument_types(properties.get(key)):
            continue
        raise ValueError(f"native tool arguments are missing required key: {key}")
    for key, value in arguments.items():
        property_schema = properties.get(key)
        if not isinstance(property_schema, dict):
            continue
        allowed_types = allowed_argument_types(property_schema)
        if value is None and "null" in allowed_types:
            continue
        actual_type = _json_type(value)
        if actual_type not in allowed_types and not (actual_type == "integer" and "number" in allowed_types):
            raise ValueError(f"native tool argument {key} has type {actual_type}, expected {sorted(allowed_types)}")


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


__all__ = [
    "DuplicateToolCallError",
    "MalformedToolArgumentsError",
    "ToolCallParseError",
    "UnknownToolNameError",
    "allowed_argument_types",
    "build_cloud_tool_definitions",
    "canonical_tool_call_text",
    "openai_tool_payload",
    "parse_native_tool_calls",
]
