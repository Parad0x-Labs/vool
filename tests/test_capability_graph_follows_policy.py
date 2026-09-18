"""The capability graph's tool availability follows a policy change without a restart.

Found during email revision 5 (evidence/39a-order-c1c2-then-navigator.log and
evidence/39b-order-c3-then-navigator.log): the graph re-indexed availability only when the tool
REGISTRY epoch moved, while every tool contract's ``supported`` flag is recomputed from policy on
each read (core.runtime_tool_contracts: "re-evaluated on every call, so no ... preference change can
outrun it"). A policy change -- including the operator's own door,
``policy_engine.set_operator_policy_values`` -- moves no registry epoch, so the offer seats and the
navigator kept reporting the availability the graph was first indexed under: a disabled tool could
stay seatable and an enabled one unseatable until something unrelated re-indexed the graph or the
process restarted.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_policy_home(tmp_path, monkeypatch):
    from core import policy_engine, runtime_paths

    for name in ("VOOL_ENABLE_WEB", "VOOL_ALLOW_WEB", "VOOL_DISABLE_WEB", "VOOL_NO_WEB"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    previous = policy_engine._POLICY_CACHE
    policy_engine._POLICY_CACHE = None
    try:
        yield tmp_path
    finally:
        policy_engine._POLICY_CACHE = previous
        runtime_paths.configure_runtime_home(None)


def _home_policy_file(tmp_path):
    from core import runtime_paths

    target = runtime_paths.active_config_home_dir() / "default_policy.yaml"
    assert str(target).startswith(str(tmp_path.resolve())), "refusing to write a policy file outside the test home"
    return target


def _write_email_policy(tmp_path, *, enabled: bool) -> None:
    from core import policy_engine, runtime_paths

    target = _home_policy_file(tmp_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    shipped = (runtime_paths.PROJECT_CONFIG_DIR / "default_policy.yaml").read_text(encoding="utf-8")
    flag = "true" if enabled else "false"
    target.write_text(shipped.rstrip("\n") + f"\nemail:\n  read_enabled: {flag}\n  send_enabled: {flag}\n",
                      encoding="utf-8")
    policy_engine.load(force_reload=True)


def _email_row() -> dict:
    from core.tool_navigator import catalog_family_table

    return next(row for row in catalog_family_table() if row.get("family") == "email")


def _index_graph_under_current_policy() -> None:
    """Pin where a case starts: the graph is process-wide, so whichever test indexed it last would
    otherwise decide the starting availability (evidence/40b showed a case passing only because an
    earlier test had indexed the graph under a different policy)."""
    from core.capability_graph import bootstrap_from_registry

    bootstrap_from_registry()


def _seated(intent: str) -> bool:
    from core.capability_graph import model_visible_specs

    return intent in {str(spec.get("intent") or "") for spec in model_visible_specs(explicit_intents=(intent,))}


def test_a_reloaded_email_policy_reaches_the_offer_and_the_navigator(tmp_path) -> None:
    """ORIGINAL: the served drive's own path -- a policy file in the runtime home, reloaded. The
    graph is first indexed with email disabled; enabling it must seat email.read and count email
    tools available; disabling it again must take both back."""
    from core.runtime_tool_contracts import runtime_tool_contract_map

    _write_email_policy(tmp_path, enabled=False)
    _index_graph_under_current_policy()
    assert not runtime_tool_contract_map()["email.read"].supported
    assert _email_row()["available_count"] == 0 and not _seated("email.read")

    _write_email_policy(tmp_path, enabled=True)
    assert runtime_tool_contract_map()["email.read"].supported  # the contract already follows policy
    assert _email_row()["available_count"] >= 1
    assert _seated("email.read")

    _write_email_policy(tmp_path, enabled=False)
    assert _email_row()["available_count"] == 0
    assert not _seated("email.read")


def test_novel_the_operator_policy_door_reaches_the_offer_without_a_restart(tmp_path) -> None:
    """NOVEL: a different family through the product's operator door. Web lookups turned off, then
    on, then off again: the web.fetch seat follows each change (web.fetch's contract is the one that
    reads the web switch; web.search is always declared supported). The door moves no registry
    epoch -- measured by probe_graph_epoch_after_policy_door.py (evidence
    probe-graph-epoch-after-policy-door.txt). The offer is only built -- nothing is fetched."""
    from core import policy_engine
    from core.runtime_tool_contracts import runtime_tool_contract_map

    _home_policy_file(tmp_path)
    policy_engine.set_operator_policy_values({"system.allow_web_fallback": False, "system.local_only_mode": False})
    _index_graph_under_current_policy()
    assert not runtime_tool_contract_map()["web.fetch"].supported
    assert not _seated("web.fetch")

    policy_engine.set_operator_policy_values({"system.allow_web_fallback": True})
    assert runtime_tool_contract_map()["web.fetch"].supported
    assert _seated("web.fetch")

    policy_engine.set_operator_policy_values({"system.allow_web_fallback": False})
    assert not runtime_tool_contract_map()["web.fetch"].supported
    assert not _seated("web.fetch")
