"""One bounded recovery path from a local model's tool output to the canonical ToolCall.

A valid tool intent must either execute exactly once or end in an honest typed failure. It must
never disappear because the one generated call was malformed. Before this module, that is exactly
what happened: the Ollama lane had no content fallback at all (`_invoke_ollama_chat` raised
"required native tool call is missing" without ever reading `message.content`), the
OpenAI-compatible lane recovered bare-JSON prose only, and no lane could read the dialects local
models actually speak when their native envelope fails:

  * Hermes / Qwen JSON-in-XML     <tool_call>{"name": ..., "arguments": {...}}</tool_call>
  * Qwen arg-pair XML             <tool_call>read<arg_key>path</arg_key><arg_value>x</arg_value>
  * qwen-coder function XML       <function=read><parameter=path>x</parameter></function>
    (the shape Ollama's own parser 500s on when it drifts — ollama/ollama#17276, #16383, #14834)
  * Gemma pythonic fences         ```tool_code\nread(path="x")\n```
  * ChatML-era bare JSON          {"name": "read", "arguments": {"path": "x"}}

Everything here reduces to the ONE existing canonical form — `CloudToolCall` — and is validated
by the ONE existing authority, `parse_native_tool_calls` (name resolution, argument schema,
duplicate/parallel structure). This module never grows a second validation contract and never
executes anything.

Hard rules, enforced by construction:

  * **Exactly one bounded repair attempt** (`MAX_REPAIR_ATTEMPTS = 1`) for recoverable
    syntax/schema defects: markdown-fence residue, Python literals, single quotes, trailing
    commas, unquoted keys, one string-unwrap of a JSON-string-encoded payload, and
    schema-directed scalar coercion ("120" -> 120 where the schema says integer). Each of these
    rewrites only what the model wrote.
  * **Never invent.** A missing name, a missing required argument, a truncated value, positional
    arguments with no parameter names — none of these are repairable, because completing them
    means fabricating content the model never produced. They become typed rejections.
  * **Never execute ambiguity.** A wrapper naming one tool around a body naming another, or a
    body carrying conflicting name keys, is rejected as ambiguous, not resolved by guessing.
  * **Identity is stable.** A call id the model sent survives repair verbatim; a call that
    arrived with no id gets a deterministic id derived from its resolved name, canonical
    arguments and position — so re-resolving identical output cannot mint a second identity, and
    a repair/retry cannot masquerade as a new call.

The visible typed states are `parsed`, `repaired`, `rejected` and `none` (no call expressed).
`executed` / `failed` remain the executor's to stamp (`ToolIntentExecution.status`); this module
ends where dispatch begins.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from core.cloud_provider_contract import CloudToolCall, CloudToolDefinition
from core.cloud_tool_call_contract import (
    DuplicateToolCallError,
    MalformedToolArgumentsError,
    ToolCallParseError,
    UnknownToolNameError,
    allowed_argument_types,
    parse_native_tool_calls,
)

MAX_REPAIR_ATTEMPTS = 1

STATE_PARSED = "parsed"
STATE_REPAIRED = "repaired"
STATE_REJECTED = "rejected"
STATE_NONE = "none"


class MissingToolNameError(ToolCallParseError):
    """A tool-call structure was expressed but carried no tool name anywhere."""


class AmbiguousToolCallError(ToolCallParseError):
    """One call structure named two different tools; executing either would be a guess."""


_REJECTION_ERRORS: dict[str, type[ToolCallParseError]] = {
    "missing_name": MissingToolNameError,
    "unknown_tool_name": UnknownToolNameError,
    "malformed_arguments": MalformedToolArgumentsError,
    "schema_validation_failed": MalformedToolArgumentsError,
    "duplicate_tool_call": DuplicateToolCallError,
    "ambiguous_tool_call": AmbiguousToolCallError,
}


@dataclass(frozen=True)
class ToolCallResolution:
    """The typed, reportable outcome of one recovery attempt."""

    state: str
    calls: tuple[CloudToolCall, ...] = ()
    dialect: str = ""
    repair_applied: str = ""
    rejection_kind: str = ""
    detail: str = ""

    def as_metadata(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "dialect": self.dialect,
            "repair_applied": self.repair_applied,
            "rejection_kind": self.rejection_kind,
            "detail": self.detail[:400],
            "call_count": len(self.calls),
            "call_ids": [call.call_id for call in self.calls],
        }

    def typed_error(self) -> ToolCallParseError | None:
        if self.state != STATE_REJECTED:
            return None
        error_cls = _REJECTION_ERRORS.get(self.rejection_kind, ToolCallParseError)
        return error_cls(self.detail or self.rejection_kind)


@dataclass
class _Candidate:
    name: str
    arguments: Any  # dict when already structured, str when still raw JSON text
    call_id: str = ""


class _RecoverableDefectError(Exception):
    """A defect the single bounded repair pass is allowed to try to fix."""

    def __init__(self, note: str):
        super().__init__(note)
        self.note = note


class _RejectionError(Exception):
    def __init__(self, kind: str, detail: str):
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


# --- text preparation -------------------------------------------------------------------------

_THINK_BLOCK_RE = re.compile(r"<think\b[^>]*>.*?</think>", re.IGNORECASE | re.DOTALL)
# A narrated tool RESULT is never a call; content inside one must not be mined for calls. An
# unterminated result block swallows to end-of-text: a model narrating a fake result rarely
# closes the tag, and nothing after a fabricated result is trustworthy call material.
_RESULT_BLOCK_RE = re.compile(
    r"<(function_results|tool_response|tool_result|observation)\b[^>]*>"
    r"(?:.*?</\1>|.*$)",
    re.IGNORECASE | re.DOTALL,
)

_WRAPPER_BLOCK_RE = re.compile(
    r"<(tool_call|tool_code|function_call|tool_use)\b[^>]*>(?P<body>.*?)</\1>",
    re.IGNORECASE | re.DOTALL,
)
_NAMED_FN_RE = re.compile(
    r"<(?:function|invoke)[=\s]+(?:name=)?[\"']?(?P<name>[A-Za-z0-9_.\-]{1,64})[\"']?\s*>"
    r"(?P<body>.*?)</(?:function|invoke)>",
    re.IGNORECASE | re.DOTALL,
)
_PARAM_PAIR_RE = re.compile(
    r"<parameter[=\s]+(?:name=)?[\"']?(?P<key>[A-Za-z0-9_.\-]{1,64})[\"']?\s*>"
    r"(?P<value>.*?)</parameter>",
    re.IGNORECASE | re.DOTALL,
)
_ARG_PAIR_RE = re.compile(
    r"<arg_key>(?P<key>.*?)</arg_key>\s*<arg_value>(?P<value>.*?)</arg_value>",
    re.IGNORECASE | re.DOTALL,
)
_LEADING_NAME_RE = re.compile(r"^\s*(?P<name>[A-Za-z0-9_.\-]{1,64})\s*(?=<|\{|$)")
_FENCE_RE = re.compile(
    r"```[ \t]*(?P<tag>tool_code|tool_call|json)[ \t]*\r?\n(?P<body>.*?)```",
    re.IGNORECASE | re.DOTALL,
)
_LONE_OPEN_TAG_RE = re.compile(
    r"<(tool_call|tool_code|function_call|tool_use|function=|invoke)\b", re.IGNORECASE
)
_PAYLOAD_EVIDENCE_RE = re.compile(r"[{]|<arg_key|<parameter|<function[=\s]", re.IGNORECASE)
_NAME_KEYS = ("intent", "name", "tool")


def _prepare_text(text: str) -> str:
    body = str(text or "")
    body = _THINK_BLOCK_RE.sub("", body)
    body = _RESULT_BLOCK_RE.sub("", body)
    return body


def _coerce_pair_scalar(raw: str) -> Any:
    """arg_key/arg_value and <parameter=> values arrive untyped; the two unambiguous scalar
    shapes are converted so a quoted integer does not fail schema validation for being text."""

    value = raw.strip()
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"null", "none"}:
        return None
    if re.fullmatch(r"-?\d{1,18}", value):
        return int(value)
    return value


def _first_json_value(text: str) -> tuple[Any, bool]:
    """(value, found). Tolerant scan for the first JSON object/array; never completes truncation."""

    body = str(text or "")
    start = min((i for i in (body.find("{"), body.find("[")) if i >= 0), default=-1)
    if start < 0:
        return None, False
    try:
        value, _ = json.JSONDecoder().raw_decode(body[start:])
    except ValueError:
        return None, False
    return value, True


def _candidate_from_call_object(payload: dict[str, Any], *, wrapper_name: str = "") -> _Candidate:
    names = [str(payload.get(key) or "").strip() for key in _NAME_KEYS]
    distinct = {name for name in names if name}
    if len(distinct) > 1:
        raise _RejectionError(
            "ambiguous_tool_call",
            f"call object carries conflicting tool names: {sorted(distinct)}",
        )
    body_name = next(iter(distinct), "")
    if wrapper_name and body_name and wrapper_name != body_name:
        raise _RejectionError(
            "ambiguous_tool_call",
            f"wrapper names {wrapper_name!r} but the body names {body_name!r}",
        )
    name = body_name or wrapper_name
    arguments = payload.get("arguments")
    if arguments is None:
        arguments = payload.get("parameters")
    if arguments is None and not any(key in payload for key in ("arguments", "parameters")):
        # `{"name": "x", "path": "y"}` — arguments inline beside the name.
        arguments = {k: v for k, v in payload.items() if k not in _NAME_KEYS}
    call_id = str(payload.get("id") or "").strip()
    return _Candidate(name=name, arguments=arguments, call_id=call_id)


def _candidates_from_json_value(value: Any, *, wrapper_name: str = "") -> list[_Candidate] | None:
    if isinstance(value, dict):
        if wrapper_name or any(str(value.get(key) or "").strip() for key in _NAME_KEYS):
            return [_candidate_from_call_object(value, wrapper_name=wrapper_name)]
        return None
    if isinstance(value, list):
        members = [item for item in value if isinstance(item, dict)]
        candidates = [
            _candidate_from_call_object(item)
            for item in members
            if any(str(item.get(key) or "").strip() for key in _NAME_KEYS)
        ]
        return candidates or None
    return None


def _dotted_call_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted_call_name(node.value)
        return f"{parent}.{node.attr}" if parent else ""
    return ""


def _candidates_from_python(body: str) -> list[_Candidate] | None:
    try:
        tree = ast.parse(body.strip(), mode="eval")
    except SyntaxError:
        return None
    root = tree.body
    nodes = list(root.elts) if isinstance(root, ast.List) else [root]
    candidates: list[_Candidate] = []
    for node in nodes:
        if isinstance(node, ast.Call) and _dotted_call_name(node.func) == "print" and len(node.args) == 1:
            node = node.args[0]  # Gemma's print(tool(...)) convention
        if not isinstance(node, ast.Call):
            return None
        name = _dotted_call_name(node.func)
        if not name:
            return None
        if node.args:
            # Positional arguments carry no parameter names; mapping them onto the schema would
            # be invention, not repair.
            raise _RejectionError(
                "malformed_arguments",
                f"pythonic call {name}(...) used positional arguments; parameter names are unrecoverable",
            )
        arguments: dict[str, Any] = {}
        for keyword in node.keywords:
            if keyword.arg is None:
                raise _RejectionError(
                    "malformed_arguments", f"pythonic call {name}(...) used **kwargs expansion"
                )
            try:
                arguments[keyword.arg] = ast.literal_eval(keyword.value)
            except (ValueError, SyntaxError):
                raise _RejectionError(
                    "malformed_arguments",
                    f"pythonic call argument {keyword.arg!r} is not a literal value",
                ) from None
        candidates.append(_Candidate(name=name, arguments=arguments))
    return candidates or None


def _candidates_from_block_body(body: str, *, wrapper_name: str = "") -> list[_Candidate] | None:
    """Candidates from the inside of one tool wrapper, or None when nothing parseable is there."""

    stripped = body.strip()
    if not stripped:
        return None

    # qwen-coder form: <function=name><parameter=key>value</parameter></function>
    fn_candidates: list[_Candidate] = []
    for match in _NAMED_FN_RE.finditer(stripped):
        fn_candidates.extend(
            _candidates_from_named_function(match.group("name"), match.group("body") or "")
        )
    if fn_candidates:
        return fn_candidates

    pairs = _ARG_PAIR_RE.findall(stripped)
    params = _PARAM_PAIR_RE.findall(stripped)
    leading = _LEADING_NAME_RE.match(stripped)
    leading_name = str(leading.group("name")).strip() if leading else ""
    if pairs or params:
        arguments = {str(k).strip(): _coerce_pair_scalar(str(v)) for k, v in [*pairs, *params]}
        name = leading_name or wrapper_name
        # A bare parameter list with no name anywhere is the measured qwen3-coder drift shape
        # (a <parameter> with no <function> wrapper): intent was expressed, identity was not.
        return [_Candidate(name=name, arguments=arguments)]

    value, found = _first_json_value(stripped)
    if found:
        via_json = _candidates_from_json_value(value, wrapper_name=wrapper_name)
        if via_json:
            return via_json
        if isinstance(value, str):
            # A JSON-string-encoded payload: recoverable by one unwrap in the repair pass.
            raise _RecoverableDefectError("string_encoded_payload")
        if wrapper_name and isinstance(value, dict):
            return [_Candidate(name=wrapper_name, arguments=value)]
        if isinstance(value, dict):
            # An explicit tool wrapper around an object with no name key: a call whose identity
            # is missing, not prose.
            return [_Candidate(name="", arguments=value)]
        return None

    if leading_name and stripped == leading_name:
        return [_Candidate(name=leading_name, arguments={})]
    return None


def _candidates_from_named_function(name: str, body: str) -> list[_Candidate]:
    wrapper_name = str(name or "").strip()
    stripped = body.strip()
    params = _PARAM_PAIR_RE.findall(stripped)
    if params:
        arguments = {str(k).strip(): _coerce_pair_scalar(str(v)) for k, v in params}
        return [_Candidate(name=wrapper_name, arguments=arguments)]
    value, found = _first_json_value(stripped)
    if found and isinstance(value, dict):
        candidate = _candidates_from_json_value(value, wrapper_name=wrapper_name)
        if candidate:
            return candidate
        return [_Candidate(name=wrapper_name, arguments=value)]
    if not stripped:
        return [_Candidate(name=wrapper_name, arguments={})]
    raise _RecoverableDefectError("unparseable_named_function_body")


@dataclass
class _Extraction:
    candidates: list[_Candidate]
    dialect: str
    markup_present: bool
    recoverable_note: str = ""


def _extract_candidates(text: str) -> _Extraction:
    # A prose SENTENCE about tool tags is not an expressed call. An open tag counts as tool
    # markup only when payload evidence follows it somewhere — a brace, an <arg_key>, a
    # <parameter> or a <function=> — which every measured genuine (even truncated) call has.
    open_tag = _LONE_OPEN_TAG_RE.search(text)
    markup_present = bool(open_tag and _PAYLOAD_EVIDENCE_RE.search(text[open_tag.end():]))
    recoverable_note = ""

    # Fenced blocks first: an explicit ```tool_code fence is the strongest marker Gemma emits.
    fence_candidates: list[_Candidate] = []
    for match in _FENCE_RE.finditer(text):
        body = match.group("body") or ""
        tag = match.group("tag").lower()
        via_python = _candidates_from_python(body)
        if via_python:
            fence_candidates.extend(via_python)
            continue
        value, found = _first_json_value(body)
        if found:
            via_json = _candidates_from_json_value(value)
            if via_json:
                fence_candidates.extend(via_json)
                continue
            if isinstance(value, str):
                recoverable_note = "string_encoded_payload"
            elif tag in {"tool_code", "tool_call"} and isinstance(value, dict):
                fence_candidates.append(_Candidate(name="", arguments=value))
            continue
        if tag in {"tool_code", "tool_call"} and body.strip():
            recoverable_note = recoverable_note or "unparseable_fence_body"
    if fence_candidates:
        return _Extraction(fence_candidates, "gemma_tool_code", True)
    if _FENCE_RE.search(text) and recoverable_note:
        return _Extraction([], "gemma_tool_code", True, recoverable_note)

    block_candidates: list[_Candidate] = []
    dialect = ""
    for match in _WRAPPER_BLOCK_RE.finditer(text):
        body = match.group("body") or ""
        try:
            found = _candidates_from_block_body(body)
        except _RecoverableDefectError as defect:
            recoverable_note = recoverable_note or defect.note
            continue
        if found:
            block_candidates.extend(found)
            dialect = dialect or (
                "qwen_xml" if (_ARG_PAIR_RE.search(body) or _PARAM_PAIR_RE.search(body)) else "hermes"
            )
        elif body.strip():
            # The wrapper is explicit tool markup; a body it cannot read is a defect the single
            # repair pass may try to fix, not silence.
            recoverable_note = recoverable_note or "unparseable_block_body"
    if block_candidates:
        return _Extraction(block_candidates, dialect or "hermes", True)
    if _WRAPPER_BLOCK_RE.search(text):
        return _Extraction([], "hermes", True, recoverable_note)

    named_candidates: list[_Candidate] = []
    for match in _NAMED_FN_RE.finditer(text):
        try:
            named_candidates.extend(
                _candidates_from_named_function(match.group("name"), match.group("body") or "")
            )
        except _RecoverableDefectError as defect:
            recoverable_note = recoverable_note or defect.note
    if named_candidates:
        return _Extraction(named_candidates, "qwen_coder", True)
    if markup_present:
        return _Extraction([], "", True, recoverable_note)

    # No tool markup anywhere: bare JSON is only read when it names a tool itself (ChatML-era
    # prompted style). Prose that merely contains data-JSON stays prose. A brace that fails to
    # parse at all is flagged recoverable — the repair pass only ever yields a call if the
    # repaired text names an offered tool, so narration cannot be promoted by the flag.
    value, found = _first_json_value(text)
    if found:
        candidates = _candidates_from_json_value(value)
        if candidates:
            return _Extraction(candidates, "json_prose", False)
        if isinstance(value, str):
            return _Extraction([], "json_prose", False, "string_encoded_payload")
    elif "{" in text:
        return _Extraction([], "json_prose", False, "unparseable_bare_json")
    return _Extraction([], "", False)


# --- the single deterministic repair pass -----------------------------------------------------

_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")
_UNQUOTED_KEY_RE = re.compile(r"([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:")
_PY_TRUE_RE = re.compile(r"\bTrue\b")
_PY_FALSE_RE = re.compile(r"\bFalse\b")
_PY_NONE_RE = re.compile(r"\bNone\b")


def repair_json_text(raw: str) -> str:
    """One deterministic pass over defective JSON text. Rewrites syntax only — never content."""

    text = str(raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[A-Za-z_]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    text = _PY_TRUE_RE.sub("true", text)
    text = _PY_FALSE_RE.sub("false", text)
    text = _PY_NONE_RE.sub("null", text)
    if "'" in text and '"' not in text:
        text = text.replace("'", '"')
    text = _TRAILING_COMMA_RE.sub(r"\1", text)
    text = _UNQUOTED_KEY_RE.sub(r'\1"\2":', text)
    try:
        value = json.loads(text)
    except ValueError:
        return text
    if isinstance(value, str):
        # One unwrap of a JSON-string-encoded payload. Exactly one: a doubly-encoded payload
        # stays defective, which is what keeps the repair bound honest.
        return value
    return text


def _coerce_to_schema(arguments: dict[str, Any], parameters: dict[str, Any]) -> dict[str, Any]:
    """Schema-directed scalar coercion: converts a present value's type, never adds a value."""

    properties = dict(parameters.get("properties") or {})
    coerced: dict[str, Any] = {}
    for key, value in arguments.items():
        allowed_types = allowed_argument_types(properties.get(key))
        if isinstance(value, str):
            text = value.strip()
            if "integer" in allowed_types and re.fullmatch(r"-?\d{1,18}", text):
                coerced[key] = int(text)
                continue
            if "number" in allowed_types and re.fullmatch(r"-?\d{1,18}(\.\d{1,18})?", text):
                coerced[key] = float(text)
                continue
            if "boolean" in allowed_types and text.lower() in {"true", "false"}:
                coerced[key] = text.lower() == "true"
                continue
        coerced[key] = value
    return coerced


# --- validation through the one shared authority ----------------------------------------------


def _definition_for(name: str, definitions: tuple[CloudToolDefinition, ...]) -> CloudToolDefinition | None:
    for definition in definitions:
        if name == str(definition.name) or name == str(definition.intent):
            return definition
    return None


def _canonical_args_key(arguments: dict[str, Any]) -> str:
    return json.dumps(arguments, sort_keys=True, default=str)


def _validate_candidates(
    candidates: list[_Candidate],
    definitions: tuple[CloudToolDefinition, ...],
    *,
    coerce: bool,
) -> tuple[CloudToolCall, ...]:
    """Resolve, structure, dedupe and schema-validate candidates via parse_native_tool_calls."""

    structured: list[tuple[CloudToolDefinition, dict[str, Any], str]] = []
    for candidate in candidates:
        name = str(candidate.name or "").strip()
        if not name:
            raise _RejectionError("missing_name", "a tool call was expressed with no tool name")
        definition = _definition_for(name, definitions)
        if definition is None:
            raise _RejectionError(
                "unknown_tool_name", f"tool call named an unregistered tool: {name!r}"
            )
        arguments = candidate.arguments
        if arguments is None:
            arguments = {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except ValueError:
                raise _RecoverableDefectError("argument_json_syntax") from None
            if not isinstance(arguments, dict):
                raise _RecoverableDefectError("argument_json_syntax")
        if not isinstance(arguments, dict):
            raise _RejectionError(
                "malformed_arguments", f"tool call {name!r} arguments are not an object"
            )
        if coerce:
            arguments = _coerce_to_schema(arguments, definition.parameters)
        structured.append((definition, arguments, candidate.call_id))

    # A stuttered identical block is one intent, not two side effects and not a duplicate defect.
    deduped: list[tuple[CloudToolDefinition, dict[str, Any], str]] = []
    seen: set[tuple[str, str]] = set()
    for definition, arguments, call_id in structured:
        key = (definition.name, _canonical_args_key(arguments))
        if key in seen:
            continue
        seen.add(key)
        deduped.append((definition, arguments, call_id))

    raw_calls = [
        {
            "id": call_id,
            "type": "function",
            "function": {"name": definition.name, "arguments": arguments},
        }
        for definition, arguments, call_id in deduped
    ]
    try:
        calls = parse_native_tool_calls(raw_calls, definitions=definitions)
    except ToolCallParseError:
        raise
    except ValueError as exc:
        if not coerce and _any_scalar_coercible(deduped):
            raise _RecoverableDefectError("schema_scalar_coercion") from exc
        raise _RejectionError("schema_validation_failed", str(exc)) from exc
    return _with_stable_ids(calls)


def _any_scalar_coercible(
    structured: list[tuple[CloudToolDefinition, dict[str, Any], str]],
) -> bool:
    for definition, arguments, _ in structured:
        if _coerce_to_schema(arguments, definition.parameters) != arguments:
            return True
    return False


def _with_stable_ids(calls: tuple[CloudToolCall, ...]) -> tuple[CloudToolCall, ...]:
    finalized: list[CloudToolCall] = []
    for index, call in enumerate(calls):
        if call.call_id:
            finalized.append(call)
            continue
        digest = hashlib.sha256(
            f"{call.name}|{_canonical_args_key(call.arguments)}|{index}".encode()
        ).hexdigest()[:12]
        finalized.append(
            CloudToolCall(
                call_id=f"rec_{digest}", intent=call.intent, name=call.name, arguments=call.arguments
            )
        )
    return tuple(finalized)


# --- native-envelope repair -------------------------------------------------------------------


def _repaired_native_entries(raw_calls: Any) -> tuple[list[Any], bool]:
    """The same envelope with defective argument STRINGS repaired. Ids and names untouched."""

    if not isinstance(raw_calls, list):
        return raw_calls, False
    repaired_any = False
    rebuilt: list[Any] = []
    for entry in raw_calls:
        if not isinstance(entry, dict):
            rebuilt.append(entry)
            continue
        entry = dict(entry)
        function = entry.get("function")
        holder = dict(function) if isinstance(function, dict) else entry
        arguments = holder.get("arguments")
        if isinstance(arguments, str):
            try:
                json.loads(arguments)
            except ValueError:
                repaired = repair_json_text(arguments)
                if repaired != arguments:
                    holder["arguments"] = repaired
                    repaired_any = True
        if isinstance(function, dict):
            entry["function"] = holder
        else:
            entry.update(holder)
        rebuilt.append(entry)
    return rebuilt, repaired_any


# --- entry point ------------------------------------------------------------------------------

NativeParseFn = Callable[[Any], tuple[CloudToolCall, ...]]


def resolve_tool_calls(
    *,
    content: str,
    raw_native_calls: Any = None,
    definitions: tuple[CloudToolDefinition, ...],
    native_parse_fn: NativeParseFn | None = None,
) -> ToolCallResolution:
    """Resolve one model reply into canonical tool calls, or a typed refusal.

    Priority: a valid native envelope wins; a malformed one gets the single bounded repair; only
    then is `content` read, through every supported text dialect, with the same repair budget
    shared across both phases.
    """

    repair_budget = MAX_REPAIR_ATTEMPTS
    repair_notes: list[str] = []
    native_rejection: _RejectionError | None = None

    if isinstance(raw_native_calls, list) and raw_native_calls:
        parse_fn = native_parse_fn or (
            lambda raw: parse_native_tool_calls(raw, definitions=definitions)
        )
        try:
            calls = parse_fn(raw_native_calls)
            if calls:
                return ToolCallResolution(STATE_PARSED, _with_stable_ids(tuple(calls)), "native")
        except DuplicateToolCallError as exc:
            # A repeated call id is an identity defect; "repairing" it would mint a second
            # identity for what may be one intended side effect.
            return ToolCallResolution(
                STATE_REJECTED, (), "native", rejection_kind="duplicate_tool_call", detail=str(exc)
            )
        except UnknownToolNameError as exc:
            native_rejection = _RejectionError("unknown_tool_name", str(exc))
        except (ToolCallParseError, ValueError) as exc:
            native_rejection = _RejectionError("malformed_arguments", str(exc))
            if repair_budget > 0:
                rebuilt, changed = _repaired_native_entries(raw_native_calls)
                if changed:
                    repair_budget -= 1
                    repair_notes.append("native_argument_json_syntax")
                    try:
                        calls = parse_fn(rebuilt)
                        if calls:
                            return ToolCallResolution(
                                STATE_REPAIRED,
                                _with_stable_ids(tuple(calls)),
                                "native",
                                repair_applied=";".join(repair_notes),
                            )
                    except (ToolCallParseError, ValueError) as second:
                        native_rejection = _RejectionError("malformed_arguments", str(second))

    text = _prepare_text(content)
    try:
        extraction = _extract_candidates(text)
    except _RejectionError as exc:
        # Ambiguity and positional-argument defects surface during extraction itself; they are
        # verdicts, not parse gaps, and no repair may guess past them.
        return ToolCallResolution(
            STATE_REJECTED, (), "", rejection_kind=exc.kind, detail=exc.detail
        )
    candidates = extraction.candidates
    recoverable_note = extraction.recoverable_note
    rejection: _RejectionError | None = None

    if candidates or recoverable_note:
        coerce = False
        while True:
            if candidates:
                try:
                    calls = _validate_candidates(candidates, definitions, coerce=coerce)
                    state = STATE_REPAIRED if repair_notes else STATE_PARSED
                    return ToolCallResolution(
                        calls=calls,
                        state=state,
                        dialect=extraction.dialect,
                        repair_applied=";".join(repair_notes),
                    )
                except _RejectionError as exc:
                    rejection = exc
                    break
                except _RecoverableDefectError as defect:
                    recoverable_note = defect.note
            if repair_budget <= 0:
                rejection = rejection or _RejectionError(
                    "malformed_arguments",
                    f"tool call defect not recoverable within the repair bound ({recoverable_note})",
                )
                break
            repair_budget -= 1
            repair_notes.append(recoverable_note or "syntax_repair")
            if recoverable_note == "schema_scalar_coercion":
                coerce = True
            else:
                repaired_text = _repair_extraction_text(text)
                repaired_extraction = _extract_candidates(repaired_text)
                if repaired_extraction.candidates:
                    candidates = repaired_extraction.candidates
                    extraction = repaired_extraction
                elif not candidates:
                    if not extraction.markup_present:
                        # Bare text whose brace never became a call even after repair: nothing
                        # was expressed clearly enough to reject as a call — report no call.
                        break
                    rejection = _RejectionError(
                        "malformed_arguments",
                        f"tool markup present but no complete call is recoverable ({recoverable_note})",
                    )
                    break
                coerce = True  # the one pass also covers scalar coercion
            recoverable_note = ""

    if rejection is not None:
        return ToolCallResolution(
            STATE_REJECTED,
            (),
            extraction.dialect,
            repair_applied=";".join(repair_notes),
            rejection_kind=rejection.kind,
            detail=rejection.detail,
        )
    if extraction.markup_present:
        return ToolCallResolution(
            STATE_REJECTED,
            (),
            extraction.dialect,
            repair_applied=";".join(repair_notes),
            rejection_kind="malformed_arguments",
            detail="tool markup present but no complete call could be read from it",
        )
    if native_rejection is not None:
        return ToolCallResolution(
            STATE_REJECTED,
            (),
            "native",
            repair_applied=";".join(repair_notes),
            rejection_kind=native_rejection.kind,
            detail=native_rejection.detail,
        )
    return ToolCallResolution(STATE_NONE)


def _repair_extraction_text(text: str) -> str:
    """Apply the single textual repair inside every tool wrapper/fence body, leaving prose alone."""

    def _fix_wrapper(match: re.Match[str]) -> str:
        tag = match.group(1)
        return f"<{tag}>{repair_json_text(match.group('body') or '')}</{tag}>"

    repaired = _WRAPPER_BLOCK_RE.sub(_fix_wrapper, text)
    if repaired == text:
        repaired = _FENCE_RE.sub(
            lambda match: "```{tag}\n{body}```".format(
                tag=match.group("tag"), body=repair_json_text(match.group("body") or "")
            ),
            text,
        )
    if repaired == text:
        repaired = repair_json_text(text)
    return repaired


def canonical_intent_text(call: CloudToolCall) -> str:
    """The runtime's executable text form of one recovered call (no id: identity travels in the
    typed calls tuple, and the Ollama lane's native path formats exactly this shape)."""

    return json.dumps(
        {"intent": call.intent, "arguments": dict(call.arguments)}, ensure_ascii=False
    )


__all__ = [
    "MAX_REPAIR_ATTEMPTS",
    "AmbiguousToolCallError",
    "MissingToolNameError",
    "ToolCallResolution",
    "canonical_intent_text",
    "repair_json_text",
    "resolve_tool_calls",
]
