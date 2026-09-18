"""The single registration seam, and the collisions it must refuse.

The duplicate rule is not hygiene. Measured 2026-07-28: `web.fetch` was declared by two sources
with different argument names, `build_cloud_tool_definitions` merged them by unioning properties,
a strict native schema then listed every property in `required`, and every cloud model was forced
to send an argument `core/execution/web_tools.py:140` discards. Nothing refused the collision, so
nothing reported it.

The namespace rule matters once users install their own packs: without it a plugin can declare
`machine.list_directory` and silently receive calls meant for the built-in tool.
"""
from __future__ import annotations

import pytest

from core import tool_registry
from core.runtime_tool_contracts import RuntimeToolContract, ToolClaim


@pytest.fixture(autouse=True)
def _clean_registry():
    tool_registry.reset()
    yield
    tool_registry.reset()


def _contract(intent: str, *, source: str = "plugin", claim: ToolClaim | None = None):
    return RuntimeToolContract(
        intent=intent,
        description="d",
        tool_surface="plugin",
        capability_id="c",
        capability_claim="c",
        supported=True,
        unsupported_reason="",
        input_schema={},
        output_schema={},
        side_effect_class="read_only",
        approval_requirement="none",
        timeout_policy="t",
        retry_policy="r",
        artifact_emission="none",
        error_contract="e",
        source=source,
        claim=claim or ToolClaim(),
    )


def test_builtin_contracts_are_visible_without_registering_anything() -> None:
    assert len(tool_registry.registered_tools()) >= 52


def test_a_registered_tool_joins_the_registry() -> None:
    tool_registry.register(_contract("vool-jira.search_issues"))
    assert "vool-jira.search_issues" in tool_registry.registry_map()


def test_a_duplicate_intent_is_refused_with_the_reason() -> None:
    tool_registry.register(_contract("vool-jira.search_issues"))
    with pytest.raises(tool_registry.ToolRegistrationError, match="already registered"):
        tool_registry.register(_contract("vool-jira.search_issues"))


def test_a_plugin_cannot_shadow_a_builtin_intent() -> None:
    with pytest.raises(tool_registry.ToolRegistrationError):
        tool_registry.register(_contract("machine.list_directory"))


@pytest.mark.parametrize(
    "intent", ["machine.anything", "workspace.anything", "web.anything", "wallet.anything"]
)
def test_a_plugin_cannot_claim_a_runtime_namespace(intent: str) -> None:
    with pytest.raises(tool_registry.ToolRegistrationError, match="runtime namespace"):
        tool_registry.register(_contract(intent))


def test_a_builtin_may_use_a_runtime_namespace() -> None:
    """The reservation is about third-party packs, not about the runtime's own tools."""

    tool_registry.register(_contract("machine.brand_new_builtin", source="builtin"))
    assert "machine.brand_new_builtin" in tool_registry.registry_map()


def test_an_unnamespaced_intent_is_refused() -> None:
    with pytest.raises(tool_registry.ToolRegistrationError, match="namespaced"):
        tool_registry.register(_contract("search"))


def test_an_empty_intent_is_refused() -> None:
    with pytest.raises(tool_registry.ToolRegistrationError):
        tool_registry.register(_contract(""))


def test_register_all_is_atomic() -> None:
    """A half-installed pack is worse than one that failed to install.

    The model would be offered part of a plugin and told the rest does not exist.
    """

    with pytest.raises(tool_registry.ToolRegistrationError):
        tool_registry.register_all(
            [_contract("vool-pack.one"), _contract("machine.stolen"), _contract("vool-pack.two")]
        )
    registry = tool_registry.registry_map()
    assert "vool-pack.one" not in registry
    assert "vool-pack.two" not in registry


def test_register_all_adds_every_tool_when_all_are_valid() -> None:
    tool_registry.register_all([_contract("vool-pack.one"), _contract("vool-pack.two")])
    registry = tool_registry.registry_map()
    assert {"vool-pack.one", "vool-pack.two"} <= set(registry)


def test_unregister_removes_a_plugin_tool() -> None:
    tool_registry.register(_contract("vool-pack.one"))
    assert tool_registry.unregister("vool-pack.one") is True
    assert "vool-pack.one" not in tool_registry.registry_map()


def test_unregister_cannot_remove_a_builtin() -> None:
    assert tool_registry.unregister("machine.list_directory") is False
    assert "machine.list_directory" in tool_registry.registry_map()


def test_claim_lookup_returns_the_declared_binding() -> None:
    """What the binder reads. A third-party tool is covered with no hand-written regex."""

    claim = ToolClaim(
        target_argument="jql", resolved_target_key="jql", result_items_key="issues", cites=("issues[].key",)
    )
    tool_registry.register(_contract("vool-jira.search_issues", claim=claim))
    got = tool_registry.claim_for_intent("vool-jira.search_issues")
    assert got is not None and got.result_items_key == "issues" and got.is_declared


def test_claim_lookup_for_an_unknown_intent_is_none() -> None:
    assert tool_registry.claim_for_intent("nope.nothing") is None


def test_no_builtin_intent_is_declared_twice() -> None:
    """The live check the web.fetch collision would have failed."""

    intents = [str(item.intent) for item in tool_registry.registered_tools()]
    duplicates = sorted({name for name in intents if intents.count(name) > 1})
    assert not duplicates, f"declared more than once: {duplicates}"


def test_validate_does_not_mutate_the_registry() -> None:
    """A loader must be able to check a manifest without installing it."""

    before = set(tool_registry.registry_map())
    tool_registry.validate_contract(_contract("vool-pack.one"))
    assert set(tool_registry.registry_map()) == before
