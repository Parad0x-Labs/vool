"""The tool-argument validator, including the cases that silently pass in naive versions.

`jsonschema` is not installed and cannot be, so this validator is the only thing standing
between a model's arguments and a tool handler. The JSON-vs-Python number cases below are the
ones worth having tests for: both are quiet wrong answers rather than crashes.
"""
from __future__ import annotations

import pytest

from core.json_schema_lite import (
    UnsupportedSchemaError,
    annotations,
    apply_defaults,
    assert_supported,
    validate,
)

JIRA_SEARCH = {
    "type": "object",
    "additionalProperties": False,
    "required": ["jql"],
    "properties": {
        "jql": {"type": "string", "x-vool-kind": "query"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25},
    },
}


def test_valid_arguments_produce_no_errors() -> None:
    assert validate({"jql": "project = ENG", "limit": 25}, JIRA_SEARCH) == []


def test_missing_required_field_is_reported_by_name() -> None:
    errors = validate({"limit": 25}, JIRA_SEARCH)
    assert len(errors) == 1
    assert "jql" in errors[0]


def test_boolean_is_not_an_integer() -> None:
    """`isinstance(True, int)` is True in Python and wrong in JSON Schema."""

    errors = validate({"jql": "x", "limit": True}, JIRA_SEARCH)
    assert errors, "True was accepted where an integer was declared"
    assert "boolean" in errors[0]


def test_whole_float_is_an_integer() -> None:
    """JSON has one number type, so a provider sending 25.0 for an integer field is correct."""

    assert validate({"jql": "x", "limit": 25.0}, JIRA_SEARCH) == []


def test_stringified_number_is_rejected_and_names_the_field() -> None:
    errors = validate({"jql": "x", "limit": "25"}, JIRA_SEARCH)
    assert errors and errors[0].startswith("limit:")
    assert "integer" in errors[0] and "string" in errors[0]


def test_unknown_field_lists_the_accepted_ones() -> None:
    errors = validate({"jql": "x", "q": "y"}, JIRA_SEARCH)
    assert errors and "'q'" in errors[0]
    # The message has to be actionable: a model correcting itself needs the real field names.
    assert "jql" in errors[0] and "limit" in errors[0]


def test_all_errors_are_collected_not_just_the_first() -> None:
    errors = validate({"limit": 500, "q": "y"}, JIRA_SEARCH)
    assert len(errors) >= 3, f"expected missing-required + range + unknown, got {errors}"


def test_type_error_suppresses_downstream_keyword_noise() -> None:
    errors = validate({"jql": "x", "limit": "many"}, JIRA_SEARCH)
    assert len(errors) == 1, f"a wrong type should report once, got {errors}"


@pytest.mark.parametrize(
    ("value", "valid"),
    [("ENG", True), ("ENG2", True), ("eng", False), ("1ENG", False), ("", False)],
)
def test_pattern_is_enforced(value: str, valid: bool) -> None:
    schema = {"type": "object", "properties": {"project": {"type": "string", "pattern": "^[A-Z][A-Z0-9]+$"}}}
    assert (validate({"project": value}, schema) == []) is valid


def test_invalid_regex_in_schema_refuses_the_value() -> None:
    """A constraint that was declared and cannot be checked must not read as satisfied."""

    schema = {"type": "object", "properties": {"a": {"type": "string", "pattern": "([unclosed"}}}
    assert validate({"a": "anything"}, schema)


def test_nested_object_errors_carry_a_path() -> None:
    schema = {
        "type": "object",
        "properties": {"outer": {"type": "object", "properties": {"inner": {"type": "integer"}}}},
    }
    errors = validate({"outer": {"inner": "no"}}, schema)
    assert errors and errors[0].startswith("outer.inner:")


def test_array_item_errors_carry_an_index() -> None:
    schema = {"type": "object", "properties": {"ids": {"type": "array", "items": {"type": "integer"}}}}
    errors = validate({"ids": [1, "two", 3]}, schema)
    assert errors and "ids[1]" in errors[0]


def test_union_type_accepts_either() -> None:
    schema = {"type": "object", "properties": {"v": {"type": ["string", "null"]}}}
    assert validate({"v": "x"}, schema) == []
    assert validate({"v": None}, schema) == []
    assert validate({"v": 1}, schema)


def test_enum_reports_the_choices() -> None:
    schema = {"type": "object", "properties": {"mode": {"enum": ["read", "write"]}}}
    errors = validate({"mode": "delete"}, schema)
    assert errors and "read" in errors[0] and "write" in errors[0]


def test_defaults_fill_absent_keys_only() -> None:
    assert apply_defaults({"jql": "x"}, JIRA_SEARCH) == {"jql": "x", "limit": 25}
    # A present None is a value the model chose; validation judges it, defaults do not mask it.
    assert apply_defaults({"jql": "x", "limit": None}, JIRA_SEARCH)["limit"] is None


def test_annotations_are_readable_and_ignored_by_validation() -> None:
    assert annotations(JIRA_SEARCH, "jql") == {"kind": "query"}
    assert validate({"jql": "anything at all"}, JIRA_SEARCH) == []


def test_unsupported_keyword_is_rejected_at_registration() -> None:
    """A schema we cannot fully check must fail to load, not advertise an unenforced rule."""

    with pytest.raises(UnsupportedSchemaError, match="allOf"):
        assert_supported({"type": "object", "properties": {"a": {"allOf": []}}})


def test_unknown_type_name_is_rejected_at_registration() -> None:
    with pytest.raises(UnsupportedSchemaError, match="strig"):
        assert_supported({"type": "strig"})


def test_the_shipped_example_schema_is_supported() -> None:
    assert_supported(JIRA_SEARCH)
