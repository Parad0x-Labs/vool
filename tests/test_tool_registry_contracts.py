from __future__ import annotations

from tools.registry import TOOLS, list_tool_contracts, load_builtin_tools, tool_contract


def test_builtin_tool_registry_exposes_explicit_contracts() -> None:
    TOOLS.clear()
    load_builtin_tools()

    contracts = list_tool_contracts()
    names = {contract.name for contract in contracts}
    assert {"web.search", "web.ddg_instant", "web.fetch", "web.research", "browser.render"}.issubset(names)

    for contract in contracts:
        assert contract.description
        assert contract.input_schema
        assert contract.output_schema
        assert contract.side_effect_class
        assert contract.approval_requirement
        assert contract.timeout_policy
        assert contract.retry_policy
        assert contract.artifact_emission
        assert contract.error_contract


def test_tool_contract_lookup_matches_registered_tool() -> None:
    TOOLS.clear()
    load_builtin_tools()

    contract = tool_contract("web.search")
    assert contract.name == "web.search"
    assert contract.side_effect_class == "network_read"
    assert contract.approval_requirement == "none"
