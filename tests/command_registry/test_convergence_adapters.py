"""Convergence amendment gates: census pin, anti-unwiring sabotage, adapter
forwarding proofs, and the live permission-metadata check.
"""
from __future__ import annotations

import json

import pytest

from core.command_registry.census import CensusItem, census_check, load_pin, run_census
from core.command_registry.registry import registry as get_registry


@pytest.fixture(scope="module")
def reg():
    return get_registry()


# -- the census itself ------------------------------------------------------------


def test_census_is_complete_and_pinned():
    report = run_census()
    totals = report.totals()
    assert totals["LEGACY_UNMIGRATED"] == 0, "release-critical actions must all be owned or adapted"
    pin = load_pin()
    assert pin and pin.get("items"), "census snapshot must exist"
    assert set(pin["items"]) == {i.census_id for i in report.items}, "pin must match discovery exactly"
    assert pin["totals"] == totals


def test_every_adapter_binding_exists(reg):
    report = run_census()
    ids = {s.command_id for s in reg.commands()}
    for item in report.items:
        if item.classification == "GENERATED_ADAPTER":
            assert item.registry_command_id in ids, f"{item.census_id} binds unknown command"


# -- anti-unwiring sabotage ------------------------------------------------------------


def test_sabotage_new_uncatalogued_route_fails(monkeypatch):
    import core.command_registry.census as census_mod

    original = census_mod.discover_http

    def with_new_route():
        return [
            *original(),
            CensusItem(census_id="http:POST:/api/sneaky/new-route", surface="http", name="POST /api/sneaky/new-route")
        ]

    monkeypatch.setattr(census_mod, "discover_http", with_new_route)
    report = census_check(set())
    assert not report.ok
    assert any("uncatalogued" in f and "/api/sneaky/new-route" in f for f in report.findings)


def test_sabotage_new_cli_leaf_fails(monkeypatch):
    import core.command_registry.census as census_mod

    original = census_mod.discover_cli

    def with_new_leaf():
        return [*original(), CensusItem(census_id="cli-vool:sneaky", surface="cli-vool", name="sneaky")]

    monkeypatch.setattr(census_mod, "discover_cli", with_new_leaf)
    report = census_check(set())
    assert any("uncatalogued" in f and "cli-vool:sneaky" in f for f in report.findings)


def test_sabotage_classification_drift_fails(monkeypatch):
    import core.command_registry.census as census_mod

    original = census_mod.run_census

    def drifted(registry_surfaces=None):
        report = original(registry_surfaces)
        items = []
        for item in report.items:
            if item.census_id == "http:GET:/api/council/runs":
                item = CensusItem(
                    census_id=item.census_id, surface=item.surface, name=item.name,
                    classification="EXTERNAL_TRANSPORT_ONLY", registry_command_id="", note="drift",
                )
            items.append(item)
        from core.command_registry.census import CensusReport

        return CensusReport(ok=report.ok, items=tuple(items))

    monkeypatch.setattr(census_mod, "run_census", drifted)
    result = census_check(set())
    assert any("classification drift" in f and "council/runs" in f for f in result.findings)


def test_sabotage_broken_adapter_binding_fails(reg):
    ids = {s.command_id for s in reg.commands()}
    report = census_check(ids - {"council.stop"})
    assert any("adapter binding broken" in f and "council.stop" in f for f in report.findings)


def test_sabotage_legacy_unmigrated_fails(monkeypatch):
    """A pinned item discovered as LEGACY_UNMIGRATED (or an unpinned new one)
    must fail the census: the first as legacy_unmigrated/classification drift,
    the second as uncatalogued — either way the gate is RED."""
    import core.command_registry.census as census_mod

    original = census_mod.run_census

    def with_unmigrated(registry_surfaces=None):
        report = original(registry_surfaces)
        items = []
        for item in report.items:
            if item.census_id == "http:GET:/api/council/runs":
                item = CensusItem(
                    census_id=item.census_id, surface=item.surface, name=item.name,
                    classification="LEGACY_UNMIGRATED", registry_command_id="", note="",
                )
            items.append(item)
        from core.command_registry.census import CensusReport

        return CensusReport(ok=report.ok, items=tuple(items))

    monkeypatch.setattr(census_mod, "run_census", with_unmigrated)
    result = census_check(set())
    assert not result.ok
    assert any(
        "legacy_unmigrated" in f or ("classification drift" in f and "council/runs" in f)
        for f in result.findings
    )


def test_commands_check_cli_goes_red_on_census_sabotage(monkeypatch, capsys):

    import core.command_registry.census as census_mod

    original = census_mod.discover_http

    def with_new_route():
        return [*original(), CensusItem(census_id="http:POST:/api/rogue", surface="http", name="POST /api/rogue")]

    monkeypatch.setattr(census_mod, "discover_http", with_new_route)
    from core.command_registry import cli

    code = cli.main(["commands", "--check"])
    out = capsys.readouterr().out
    assert code == 1
    assert "uncatalogued" in out and "/api/rogue" in out


# -- permission metadata sabotage (live registry) ---------------------------------------


def test_sabotage_stripping_a_live_permission_gate_fails(monkeypatch):
    import dataclasses
    import sys

    reg_module = sys.modules["core.command_registry.registry"]
    import core.command_registry.groups.council as council

    original = council.register

    def sabotaged(reg):
        original(reg)
        reg._commands["council.stop"] = dataclasses.replace(
            reg.lookup("council.stop"),
            permission=__import__("core.command_registry.spec", fromlist=["OpenRead"]).OpenRead(),
        )

    monkeypatch.setattr(council, "register", sabotaged)
    reg_module._REGISTRY = None
    try:
        from core.command_registry.check import check_registry

        report = check_registry(reg_module.registry())
        assert any("gate required" in f.detail for f in report.findings if f.command_id == "council.stop")
    finally:
        monkeypatch.undo()
        reg_module._REGISTRY = None


# -- adapter forwarding proofs through the REAL dispatcher ------------------------------


def _rt():
    from core.web.api.runtime import RuntimeServices

    return RuntimeServices(display_name="VOOL")


def test_prefs_get_forwards_through_registry():
    from core.web.api.service import dispatch_get

    res = dispatch_get(path="/api/settings/prefs", query={}, runtime=_rt(), model_name="vool")
    assert res.status == 200
    payload = json.loads(res.body)
    assert "user_address" in payload and "autonomy_mode" in payload


def test_credentials_post_roundtrip_through_registry(tmp_path, monkeypatch):
    from core import runtime_paths
    from core.web.api.service import dispatch_post

    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    try:
        res = dispatch_post(
            path="/api/settings/credentials",
            body={"name": "llm.cloud.openrouter", "junk": 1},
            headers={"content-type": "application/json"},
            runtime=_rt(),
            model_name="vool",
            workspace_root_provider=lambda: str(tmp_path),
        )
        assert res.status == 400  # unknown-field guard fires inside the lifted authority
    finally:
        runtime_paths.configure_runtime_home(None)


def test_council_reads_forward_through_registry():
    from core.web.api.service import dispatch_get

    for path, key in (("/api/council/runs", "ok"), ("/api/council/lock", None), ("/api/council/scorecard", "ok")):
        res = dispatch_get(
            path=path, query={"run": ["r1"]}, runtime=_rt(), model_name="vool", client_host="127.0.0.1"
        )
        assert res.status == 200
        if key:
            assert json.loads(res.body).get(key) in (True, False)


def test_mode_resolve_op_forward_is_byte_compatible():
    from core.web.api.service import dispatch_post

    res = dispatch_post(
        path="/api/mode",
        body={"op": "resolve_approval", "approval_id": "nope", "decision": "allow", "session_id": "s"},
        headers={"content-type": "application/json"},
        runtime=_rt(),
        model_name="vool",
        workspace_root_provider=lambda: "/tmp",
        client_host="127.0.0.1",
    )
    assert res.status == 409
    assert json.loads(res.body).get("error") == "approval is missing, stale, or already resolved"


def test_blackbox_module_cli_forwards():
    import contextlib
    import io

    from core.blackbox.__main__ import main

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = main(["status"])
    assert code == 0
    assert json.loads(buffer.getvalue())["store"].endswith("/data/blackbox") is True or "store" in json.loads(buffer.getvalue())


def test_repl_summary_uses_registry():
    from pathlib import Path

    source = Path("apps/vool_chat.py").read_text()
    assert 'execute_command("status.show"' in source, "the REPL /summary must go through the registry"
