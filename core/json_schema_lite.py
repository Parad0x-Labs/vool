"""A small JSON Schema validator for tool arguments.

`jsonschema` is not installed and package installs are prohibited on a machine holding live
keys, so the tool path needs its own validator. This covers the subset a tool `input_schema`
actually uses — the same subset that survives OpenAI's `strict` function schemas — and refuses
loudly on a keyword it does not implement rather than passing a value it never checked.

Errors are returned, not raised, and are phrased so they can be handed straight back to a model
as an observation: a weak local model that emits `{"limit": "25"}` should be told which field
was wrong and why, not handed a stack trace or a silent coercion.

Two JSON-vs-Python details this gets right, because both produce wrong answers quietly:
`True` is an instance of `int` in Python but is not an integer in JSON Schema, and `integer`
accepts a float with no fractional part (`25.0`) because JSON has one number type.
"""
from __future__ import annotations

import re
from typing import Any

_SUPPORTED_KEYWORDS = frozenset(
    {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "enum",
        "const",
        "pattern",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "default",
        "description",
        "title",
        "examples",
    }
)

# Declared on a property to say what a value MEANS, not what shape it has. A path and a bare
# name are both `"type": "string"`; only this separates them. Ignored during validation and
# read by the pre-dispatch shape guard.
_ANNOTATION_PREFIX = "x-vool-"

_TYPE_CHECKS = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def _describe(value: Any) -> str:
    """Name a value's JSON type for an error a model has to act on.

    Reports the JSON type plus the value itself, because "expected integer, got string" leaves
    a model guessing which of its arguments was wrong when several are strings.
    """

    if value is None:
        return "null"
    if isinstance(value, bool):
        return f"boolean ({str(value).lower()})"
    if isinstance(value, str):
        shown = value if len(value) <= 40 else f"{value[:37]}..."
        return f"string ({shown!r})"
    if isinstance(value, int):
        return f"integer ({value})"
    if isinstance(value, float):
        return f"number ({value})"
    if isinstance(value, list):
        return f"array of {len(value)}"
    if isinstance(value, dict):
        return f"object with {len(value)} field(s)"
    return type(value).__name__


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_integer(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    # JSON has one number type, so 25.0 arriving where an integer is declared is an integer.
    return isinstance(value, float) and value.is_integer()


_TYPE_CHECKS["number"] = _is_number
_TYPE_CHECKS["integer"] = _is_integer


class UnsupportedSchemaError(ValueError):
    """A schema used a keyword this validator does not implement.

    Raised at registration time, never at call time. A tool whose schema cannot be fully
    checked must fail to load — accepting it would advertise a constraint that is not enforced.
    """


# Keep the historical import name for callers while exposing a conventional
# exception class name to new code.
UnsupportedSchema = UnsupportedSchemaError


def assert_supported(schema: Any, *, path: str = "") -> None:
    """Reject a schema containing a keyword we would silently ignore.

    Call this when a tool is registered, so an unsupported keyword is an authoring error the
    plugin author sees immediately rather than a constraint that quietly never applies.
    """

    where = path or "<root>"
    if not isinstance(schema, dict):
        raise UnsupportedSchemaError(f"{where}: schema must be an object")
    for key in schema:
        if key.startswith(_ANNOTATION_PREFIX) or key in _SUPPORTED_KEYWORDS:
            continue
        raise UnsupportedSchemaError(
            f"{where}: unsupported schema keyword {key!r}; supported: "
            f"{', '.join(sorted(_SUPPORTED_KEYWORDS))}"
        )
    declared = schema.get("type")
    for name in (declared,) if isinstance(declared, str) else tuple(declared or ()):
        if name is not None and name not in _TYPE_CHECKS:
            raise UnsupportedSchemaError(f"{where}: unknown type {name!r}")
    properties = schema.get("properties")
    if isinstance(properties, dict):
        for name, sub in properties.items():
            assert_supported(sub, path=f"{where}.{name}" if path else str(name))
    items = schema.get("items")
    if isinstance(items, dict):
        assert_supported(items, path=f"{where}[]")


def validate(instance: Any, schema: Any, *, path: str = "") -> list[str]:
    """Return every reason ``instance`` does not satisfy ``schema``; empty means valid.

    Collects all errors rather than stopping at the first, so a model correcting its arguments
    gets one complete observation instead of needing a round trip per mistake.
    """

    errors: list[str] = []
    if not isinstance(schema, dict):
        return [f"{path or 'value'}: schema must be an object"]
    where = path or "value"

    declared = schema.get("type")
    types = (declared,) if isinstance(declared, str) else tuple(declared or ())
    if types:
        if not any(_TYPE_CHECKS.get(name, lambda _v: False)(instance) for name in types):
            errors.append(f"{where}: expected {' or '.join(types)}, got {_describe(instance)}")
            # Every remaining keyword assumes the type held; reporting them now would bury the
            # one error that matters under noise.
            return errors

    if "const" in schema and instance != schema["const"]:
        errors.append(f"{where}: must be {schema['const']!r}")

    choices = schema.get("enum")
    if isinstance(choices, list) and instance not in choices:
        shown = ", ".join(repr(item) for item in choices[:12])
        suffix = ", …" if len(choices) > 12 else ""
        errors.append(f"{where}: must be one of [{shown}{suffix}], got {instance!r}")

    if isinstance(instance, str):
        errors.extend(_validate_string(instance, schema, where))
    elif _is_number(instance):
        errors.extend(_validate_number(instance, schema, where))
    elif isinstance(instance, list):
        errors.extend(_validate_array(instance, schema, where, path))
    elif isinstance(instance, dict):
        errors.extend(_validate_object(instance, schema, where, path))
    return errors


def _validate_string(instance: str, schema: dict[str, Any], where: str) -> list[str]:
    errors: list[str] = []
    minimum = schema.get("minLength")
    maximum = schema.get("maxLength")
    if isinstance(minimum, int) and len(instance) < minimum:
        errors.append(f"{where}: shorter than minLength {minimum}")
    if isinstance(maximum, int) and len(instance) > maximum:
        errors.append(f"{where}: longer than maxLength {maximum}")
    pattern = schema.get("pattern")
    if isinstance(pattern, str) and pattern:
        try:
            matched = re.search(pattern, instance) is not None
        except re.error as exc:
            # A bad pattern is the tool author's defect. Refusing the value is the safe read:
            # the constraint was declared and could not be checked.
            return [*errors, f"{where}: schema pattern is not valid regex ({exc})"]
        if not matched:
            errors.append(f"{where}: does not match pattern {pattern!r}")
    return errors


def _validate_number(instance: Any, schema: dict[str, Any], where: str) -> list[str]:
    errors: list[str] = []
    checks = (
        ("minimum", lambda bound: instance < bound, "below minimum"),
        ("maximum", lambda bound: instance > bound, "above maximum"),
        ("exclusiveMinimum", lambda bound: instance <= bound, "not above exclusiveMinimum"),
        ("exclusiveMaximum", lambda bound: instance >= bound, "not below exclusiveMaximum"),
    )
    for keyword, failed, reason in checks:
        bound = schema.get(keyword)
        if _is_number(bound) and failed(bound):
            errors.append(f"{where}: {instance} is {reason} {bound}")
    return errors


def _validate_array(
    instance: list[Any], schema: dict[str, Any], where: str, path: str
) -> list[str]:
    errors: list[str] = []
    minimum = schema.get("minItems")
    maximum = schema.get("maxItems")
    if isinstance(minimum, int) and len(instance) < minimum:
        errors.append(f"{where}: needs at least {minimum} items, got {len(instance)}")
    if isinstance(maximum, int) and len(instance) > maximum:
        errors.append(f"{where}: allows at most {maximum} items, got {len(instance)}")
    items = schema.get("items")
    if isinstance(items, dict):
        for index, item in enumerate(instance):
            errors.extend(validate(item, items, path=f"{path or 'value'}[{index}]"))
    return errors


def _validate_object(
    instance: dict[str, Any], schema: dict[str, Any], where: str, path: str
) -> list[str]:
    errors: list[str] = []
    properties = schema.get("properties")
    properties = properties if isinstance(properties, dict) else {}

    for name in schema.get("required") or ():
        if name not in instance:
            errors.append(f"{where}: missing required field {str(name)!r}")

    if schema.get("additionalProperties") is False:
        for name in instance:
            if name not in properties:
                known = ", ".join(sorted(properties)) or "none"
                errors.append(f"{where}: unknown field {str(name)!r}; accepted: {known}")

    for name, sub in properties.items():
        if name in instance:
            errors.extend(validate(instance[name], sub, path=f"{path}.{name}" if path else str(name)))
    return errors


def apply_defaults(arguments: Any, schema: Any) -> dict[str, Any]:
    """Fill declared top-level defaults into a copy of ``arguments``.

    Only top level, and only where the key is absent — a present ``None`` is a value the model
    chose and is left alone for validation to judge.
    """

    result = dict(arguments) if isinstance(arguments, dict) else {}
    if not isinstance(schema, dict):
        return result
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return result
    for name, sub in properties.items():
        if isinstance(sub, dict) and "default" in sub and name not in result:
            result[name] = sub["default"]
    return result


def annotations(schema: Any, field: str) -> dict[str, Any]:
    """The ``x-vool-*`` annotations declared on one property, without the prefix."""

    if not isinstance(schema, dict):
        return {}
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return {}
    sub = properties.get(field)
    if not isinstance(sub, dict):
        return {}
    return {
        key[len(_ANNOTATION_PREFIX):]: value
        for key, value in sub.items()
        if key.startswith(_ANNOTATION_PREFIX)
    }


__all__ = [
    "UnsupportedSchemaError",
    "annotations",
    "apply_defaults",
    "assert_supported",
    "validate",
]
