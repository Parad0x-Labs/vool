"""Executable argument schemas: derived from the contracts, deterministic, and bound to the graph.
A permitted operation can still be semantically wrong; these are the checks that say so."""
from __future__ import annotations

import pytest

from core.semantic.executable_schema import (
    ExecutableSchema,
    arguments_bind_to_slot,
    schema_from_contract,
    validate_arguments,
)
from core.semantic.graph_builder import RequestGraphBuilder, operand
from core.semantic.request_graph import SemanticRole

READ_FILE_PROSE = {
    "path": "string",
    "start_line": "integer optional",
    "max_lines": "integer optional (default 2000, ceiling 50000)",
    "verbatim": "boolean optional",
}


def test_schema_is_derived_from_the_contract_prose() -> None:
    schema = schema_from_contract("workspace.read_file", READ_FILE_PROSE)
    assert schema.typed and schema.required == {"path"}
    assert schema.declared == {"path", "start_line", "max_lines", "verbatim"}
    max_lines = next(r for r in schema.rules if r.name == "max_lines")
    assert max_lines.ceiling == 50000 and max_lines.types == (int,) and not max_lines.required


def test_required_keys_types_ceilings_and_domains_are_checked() -> None:
    schema = schema_from_contract("workspace.read_file", READ_FILE_PROSE)
    assert validate_arguments(schema, {"path": "notes.txt"}) == ()
    assert any("missing required" in p for p in validate_arguments(schema, {}))
    assert any("must be int" in p for p in validate_arguments(schema, {"path": "n", "max_lines": "many"}))
    assert any("ceiling" in p for p in validate_arguments(schema, {"path": "n", "max_lines": 999999}))
    assert any("bool" in p for p in validate_arguments(schema, {"path": "n", "start_line": True}))
    assert any("non-empty" in p for p in validate_arguments(schema, {"path": "   "}))
    assert any("NUL" in p for p in validate_arguments(schema, {"path": "a\x00b"}))
    assert any("undeclared" in p for p in validate_arguments(schema, {"path": "n", "evil": 1}))


def test_unreadable_prose_keeps_the_legacy_rule_and_says_so() -> None:
    schema = schema_from_contract("x.op", {"thing": "whatever the caller likes"})
    assert schema.typed is False and "legacy" in schema.detail
    assert validate_arguments(schema, {"thing": 3}) == ()          # no type claim is made
    assert any("undeclared" in p for p in validate_arguments(schema, {"other": 1}))
    assert schema_from_contract("x.op", None).typed is False


def _graph():
    b = RequestGraphBuilder("read notes.txt and search the web for the latest Python release", turn_id="t")
    r0 = b.add_request("read notes.txt")
    r1 = b.add_request("search the web for the latest Python release")
    m_file = b.add_mention("notes.txt", within=b.request_span(r0), kind="file", role=SemanticRole.SUBJECT)
    m_py = b.add_mention("Python", within=b.request_span(r1), kind="software", role=SemanticRole.SUBJECT, entity_key="Python")
    s0 = b.add_slot(r0, expected="file contents", operands=(operand(SemanticRole.SUBJECT, mention_id=m_file),))
    s1 = b.add_slot(r1, expected="latest Python release", operands=(operand(SemanticRole.SUBJECT, mention_id=m_py),))
    return b.build(), s0, s1


def test_operand_arguments_must_be_stated_by_the_request() -> None:
    graph, s0, _s1 = _graph()
    assert arguments_bind_to_slot(graph, s0, {"path": "notes.txt"}) == ()
    assert arguments_bind_to_slot(graph, s0, {"path": "NOTES.TXT"}) == ()
    (problem,) = arguments_bind_to_slot(graph, s0, {"path": "/etc/shadow"})
    assert "did not state" in problem


def test_free_text_arguments_may_rephrase_but_not_invent() -> None:
    graph, _s0, s1 = _graph()
    assert arguments_bind_to_slot(graph, s1, {"query": "latest Python release"}) == ()
    assert arguments_bind_to_slot(graph, s1, {"query": "python release latest"}) == ()
    (problem,) = arguments_bind_to_slot(graph, s1, {"query": "latest Ruby release"})
    assert "ruby" in problem


def test_a_prior_result_may_supply_an_operand() -> None:
    graph, s0, _s1 = _graph()
    assert arguments_bind_to_slot(graph, s0, {"path": "build/out.log"}, produced={"slot-9": "build/out.log"}) == ()


def test_unknown_slot_is_a_problem_not_a_pass() -> None:
    graph, _s0, _s1 = _graph()
    assert arguments_bind_to_slot(graph, "slot-404", {"path": "x"})  # type: ignore[arg-type]


@pytest.mark.parametrize("prose,expected", [("string", (str,)), ("integer optional", (int,)), ("boolean", (bool,)), ("number optional", (int, float))])
def test_type_words_are_read(prose: str, expected) -> None:
    schema = schema_from_contract("op", {"k": prose})
    assert schema.rules[0].types == expected
    assert isinstance(schema, ExecutableSchema)
