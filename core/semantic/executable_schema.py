"""Deterministic executable argument schemas, DERIVED from the registered tool contracts.

Admission used to reject only UNDECLARED argument keys, because a contract's ``input_schema`` values
are prose ("integer optional (default 2000, ceiling 50000)") and nobody wanted to fabricate a type
system beside them. The prose is mechanically shaped, though: its first word is a type, ``optional``
marks a key as not required, and a ``ceiling N`` names a bound. This module reads exactly that --
one derivation, from the contract that already governs execution, so there is no second registry
of hand-written schemas to drift -- and adds three things prose cannot carry:

* **required keys and types**, so a proposal missing ``path`` or passing a list for ``query`` is
  refused before any policy is consulted;
* **domain validators** keyed by argument NAME (a path has no NUL byte and is not empty, a query
  is not empty, a count is a positive integer under its ceiling) -- deterministic, small, listed;
* **graph/slot/source binding**: a string argument that names something must be derivable from
  the request -- present in the request's source text, in one of the slot's mention surfaces, or
  produced by a prior slot's result. A model that argues ``path="/etc/shadow"`` on a request that
  named ``notes.txt`` has invented an operand, and permission being granted for ``read_file`` is
  not proof the call is the one the user asked for.

A permitted operation may still be semantically wrong; this is the check that says so. Where a
contract's prose cannot be read, the schema says ``typed=False`` and admission keeps the legacy
undeclared-keys rule for that operation -- labelled, never silently bypassed.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from core.semantic.graph_builder import content_tokens
from core.semantic.request_graph import RequestGraph, SlotId

_TYPE_WORDS: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "str": (str,),
    "text": (str,),
    "path": (str,),
    "integer": (int,),
    "int": (int,),
    "number": (int, float),
    "float": (int, float),
    "boolean": (bool,),
    "bool": (bool,),
    "array": (list, tuple),
    "list": (list, tuple),
    "object": (dict,),
    "mapping": (dict,),
    "dict": (dict,),
}

_CEILING_RE = re.compile(r"ceiling\s+(\d+)", re.IGNORECASE)
_DEFAULT_RE = re.compile(r"default\s+(\d+)", re.IGNORECASE)


@dataclass(frozen=True)
class ArgumentRule:
    name: str
    types: tuple[type, ...]
    required: bool
    ceiling: int | None = None
    prose: str = ""


@dataclass(frozen=True)
class ExecutableSchema:
    """What a call to ``operation`` must look like to be executable at all."""

    operation: str
    rules: tuple[ArgumentRule, ...]
    #: False when the contract's prose could not be read into types; admission then keeps the
    #: legacy undeclared-keys rule for this operation and says so.
    typed: bool = True
    detail: str = ""

    @property
    def required(self) -> frozenset[str]:
        return frozenset(r.name for r in self.rules if r.required)

    @property
    def declared(self) -> frozenset[str]:
        return frozenset(r.name for r in self.rules)


def _union_types(text: str) -> tuple[type, ...]:
    """The Python types of a prose hint that OPENS with a type union ("string|list[string]"), or ().

    The same reading the native tool schema uses (core.cloud_tool_call_contract), so admission and
    the provider-response parser accept the same arguments. A value list ("normal|dark-null") is not
    a type union.
    """
    head = text.lower().split(None, 1)[0].split("(", 1)[0] if text.strip() else ""
    if "|" not in head:
        return ()
    found: list[type] = []
    for token in head.split("|"):
        word = re.match(r"[a-z_]+", token.strip())
        types = _TYPE_WORDS.get(word.group(0)) if word else None
        if types is None:
            return ()
        found.extend(kind for kind in types if kind not in found)
    return tuple(found)


def schema_from_contract(operation: str, input_schema: Any) -> ExecutableSchema:
    """Derive the executable schema from a contract's ``input_schema`` mapping of prose."""
    if not isinstance(input_schema, Mapping):
        return ExecutableSchema(operation=str(operation), rules=(), typed=False, detail="no input_schema mapping")
    rules: list[ArgumentRule] = []
    typed = True
    for key, prose in input_schema.items():
        text = str(prose or "").strip()
        words = re.findall(r"[a-zA-Z_]+", text.lower())
        head = words[0] if words else ""
        types = _union_types(text) or _TYPE_WORDS.get(head)
        if types is None:
            typed = False
            types = (object,)
        ceiling_match = _CEILING_RE.search(text)
        rules.append(ArgumentRule(
            name=str(key),
            types=types,
            required="optional" not in words,
            ceiling=int(ceiling_match.group(1)) if ceiling_match else None,
            prose=text,
        ))
    return ExecutableSchema(
        operation=str(operation), rules=tuple(rules), typed=typed,
        detail="" if typed else "a declared type word was not recognised; legacy undeclared-keys rule applies",
    )


# -- domain validators, by argument name -----------------------------------------------------------

def _non_empty_string(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        return "must be a non-empty string"
    if "\x00" in value:
        return "must not contain a NUL byte"
    return ""


def _positive_count(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return "must be a positive integer"
    return ""


def _non_negative_int(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return "must be a non-negative integer"
    return ""


DOMAIN_VALIDATORS: dict[str, Callable[[Any], str]] = {
    "path": _non_empty_string,
    "query": _non_empty_string,
    "url": _non_empty_string,
    "pattern": _non_empty_string,
    "max_hits": _positive_count,
    "max_pages": _positive_count,
    "max_lines": _positive_count,
    "limit": _positive_count,
    "start_line": _non_negative_int,
}

#: Argument names whose string value names an OPERAND the user must have stated (or a prior slot
#: produced): a model may not invent them. Others (free-form queries) are checked by content words.
_OPERAND_ARGUMENTS = frozenset({"path", "url", "asset", "symbol", "location", "place", "city", "file", "directory"})


def validate_arguments(schema: ExecutableSchema, arguments: Mapping[str, Any]) -> tuple[str, ...]:
    """Every problem with ``arguments`` against ``schema`` (empty means executable-shaped)."""
    problems: list[str] = []
    keys = set(arguments)
    undeclared = sorted(keys - schema.declared)
    if undeclared and schema.declared:
        problems.append(f"undeclared argument(s): {undeclared}; declared: {sorted(schema.declared)}")
    if not schema.typed:
        return tuple(problems)
    for name in sorted(schema.required - keys):
        problems.append(f"missing required argument {name!r}")
    for rule in schema.rules:
        if rule.name not in arguments:
            continue
        value = arguments[rule.name]
        if rule.types != (object,):
            if isinstance(value, bool) and bool not in rule.types:
                problems.append(f"argument {rule.name!r} must be {rule.types[0].__name__}, got bool")
                continue
            if not isinstance(value, rule.types):
                problems.append(f"argument {rule.name!r} must be {'/'.join(t.__name__ for t in rule.types)}, got {type(value).__name__}")
                continue
        if rule.ceiling is not None and isinstance(value, int) and not isinstance(value, bool) and value > rule.ceiling:
            problems.append(f"argument {rule.name!r} exceeds its ceiling {rule.ceiling}")
        validator = DOMAIN_VALIDATORS.get(rule.name)
        if validator is not None:
            note = validator(value)
            if note:
                problems.append(f"argument {rule.name!r} {note}")
    return tuple(problems)


def arguments_bind_to_slot(
    graph: RequestGraph, slot_id: SlotId, arguments: Mapping[str, Any], *, produced: Mapping[str, str] | None = None
) -> tuple[str, ...]:
    """Problems with arguments that name something the request never stated.

    For operand-naming arguments the value must equal (case-folded) a mention surface of the slot,
    appear verbatim in the request's source text, or be a value a prerequisite slot produced
    (``produced`` maps slot id -> value). For other string arguments every content word must come
    from the request's text or its mentions -- a query may rephrase, not invent.
    """
    slot = next((s for s in graph.slots if s.id == slot_id), None)
    if slot is None:
        return (f"slot {slot_id!r} is not in the graph",)
    request = next((r for r in graph.requests if r.id == slot.request_id), None)
    source = (request.source_text if request is not None else "") or ""
    mentions = {m.id: m for m in graph.mentions}
    surfaces = {mentions[op.mention_id].surface.casefold() for op in slot.operands if op.mention_id in mentions}
    entity_keys = {mentions[op.mention_id].entity_key.casefold() for op in slot.operands if op.mention_id in mentions and mentions[op.mention_id].entity_key}
    produced_values = {str(v).casefold() for v in (produced or {}).values()}
    allowed_words = {w for _s, _e, w in content_tokens(source)} | {w for surface in surfaces for _s, _e, w in content_tokens(surface)}
    problems: list[str] = []
    for name, value in arguments.items():
        if not isinstance(value, str) or not value.strip():
            continue
        folded = value.strip().casefold()
        if name in _OPERAND_ARGUMENTS:
            if folded in surfaces or folded in entity_keys or folded in produced_values or folded in source.casefold():
                continue
            problems.append(f"argument {name!r}={value!r} names an operand the request did not state")
            continue
        words = {w for _s, _e, w in content_tokens(value)}
        invented = sorted(words - allowed_words - {w for pv in produced_values for _s, _e, w in content_tokens(pv)})
        if invented:
            problems.append(f"argument {name!r} introduces words the request never said: {invented}")
    return tuple(problems)


__all__ = [
    "DOMAIN_VALIDATORS",
    "ArgumentRule",
    "ExecutableSchema",
    "arguments_bind_to_slot",
    "schema_from_contract",
    "validate_arguments",
]
