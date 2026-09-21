"""THE MONEY LAW SERVED — the owner-local money routes.

Part 1 drives the route handlers in-process: the guard suite (a non-loopback
TCP peer cannot be manufactured over a real loopback socket, so the peer
refusal is proven here and labelled as handler-level). Part 2 (below) boots
the real `apps.vool_api_server` in its own process and home and runs
independent CLI processes against the same store while the daemon serves
projection, revocation and reconciliation over real HTTP.
"""
from __future__ import annotations

import json
import os

import pytest

from core import effect_budget as eb
from core import effect_budget_money as ebm
from tests.effect_budget import money_race_probe as probe
from tests.effect_budget.conftest import *  # noqa: F403 — fixtures

JSON = {"Content-Type": "application/json"}


def _body(response):
    return json.loads(response.body.decode("utf-8"))


def _money_table_rows(table):
    from storage.db import get_connection

    conn = get_connection()
    try:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is None:
            return []
        return [dict(row) for row in conn.execute(f"SELECT * FROM {table}").fetchall()]
    finally:
        conn.close()


def test_money_routes_are_owner_local_guarded_and_never_widen_authority():
    from core.web.api import money_authority_api as api

    assert api.handle_money_get("/api/money/projection", {}, client_host="192.168.1.20").status == 403
    assert api.handle_money_get("/api/money/projection", {}, client_host="").status == 403, "an unknown peer is not the owner"
    assert api.handle_money_post("/api/money/reconcile", {}, JSON, client_host="10.0.0.2").status == 403
    for path in ("/api/money/grants", "/api/money/rules", "/api/money/conversion-bounds", "/api/money/funding-policy"):
        response = api.handle_money_post(path, {"kind": "task_envelope", "max_total_atomic": 10**12}, JSON, client_host="127.0.0.1")
        assert response.status == 403 and _body(response)["error"] == "widening_not_served", path
    assert _money_table_rows("effect_budget_money_grants") == []
    assert _money_table_rows("effect_budget_money_rules") == []
    assert api.handle_money_post("/api/money/reconcile", {}, {"Content-Type": "text/plain"}, client_host="127.0.0.1").status == 415
    cross = api.handle_money_post("/api/money/reconcile", {}, {**JSON, "Origin": "https://attacker.example"}, client_host="127.0.0.1")
    assert cross.status == 403 and "cross-origin" in _body(cross)["error"]
    assert api.handle_money_post("/api/money/reconcile", ["not", "an", "object"], JSON, client_host="127.0.0.1").status == 400
    assert api.handle_money_post("/api/money/reconcile", {"pad": "x" * 70_000}, JSON, client_host="127.0.0.1").status == 413
    assert api.handle_money_get("/api/money/liabilities/mli:does-not-exist", {}, client_host="127.0.0.1").status == 404
    assert api.handle_money_post("/api/money/unknown", {}, JSON, client_host="127.0.0.1").status == 404


def test_revocation_over_the_owner_route_blocks_new_money_and_keeps_what_may_be_paid():
    from core.web.api import money_authority_api as api

    token = eb.grant_operator_budget_authority("served route test")
    grant = probe.mint_prepaid_grant(token)
    probe.observe_verified_credit(10_000_000)
    held = ebm.reserve_liability(probe.request_from_dict(probe.prepaid_request("route-held", grant.grant_id, 1_000_000)))
    ebm.claim_dispatch(held.liability_id, executor="route-test")
    response = api.handle_money_post("/api/money/grants/revoke", {"grant_id": grant.grant_id, "reason": "owner pressed stop"}, JSON, client_host="127.0.0.1")
    assert response.status == 200 and _body(response)["grant"]["state"] == ebm.GRANT_REVOKED
    refused = None
    try:
        ebm.reserve_liability(probe.request_from_dict(probe.prepaid_request("route-after", grant.grant_id, 1)))
    except eb.EffectBudgetRefusedError as refusal:
        refused = refusal.code
    assert refused == ebm.MONEY_AUTHORITY_REVOKED
    projection = _body(api.handle_money_get("/api/money/projection", {"task_id": ["task-1"]}, client_host="127.0.0.1"))["projection"]
    assert projection["liability_states"] == {ebm.LIABILITY_DISPATCHING: 1}
    assert projection["inference_expense"][f"{probe.MONEY_ACCOUNT}|{probe.MONEY_USDC.key}"]["held"] == "1000000"
    unknown_grant = api.handle_money_post("/api/money/grants/revoke", {"grant_id": "mga:missing"}, JSON, client_host="127.0.0.1")
    assert unknown_grant.status == 403 and _body(unknown_grant)["error"] == ebm.MONEY_AUTHORITY_INVALID
    listed = _body(api.handle_money_get("/api/money/liabilities", {"grant_id": [grant.grant_id]}, client_host="127.0.0.1"))["liabilities"]
    assert [item["liability_id"] for item in listed] == [held.liability_id]
    contract = _body(api.handle_money_get("/api/money/contract", {}, client_host="127.0.0.1"))["contract"]
    assert contract["contract_id"] == "effect-budget-money-contract/v1"


# ---------------------------------------------------------------------------
# Part 2 — the real daemon, real HTTP, independent processes on its store
# ---------------------------------------------------------------------------


def _http(daemon, method, path, payload=None, *, origin=None):
    import urllib.error
    import urllib.request

    headers = {}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json", "Origin": origin or daemon.base_url}
    request = urllib.request.Request(f"{daemon.base_url}{path}", data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8") or "{}"
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, {"raw": raw[:300]}


def test_the_daemon_serves_money_state_written_by_independent_processes_and_keeps_it_across_restart(tmp_path, monkeypatch):
    import signal

    from tests._blackbox_served_rig import ScriptedProvider, ServedDaemon

    home = tmp_path / "served-money-home"
    home.mkdir()
    # the CLI processes and this operator process open the DAEMON's own store file
    monkeypatch.setenv("MONEY_PROBE_DB_NAME", "vool_web0_v2.db")
    provider = ScriptedProvider({}, default="ok")
    env = {
        # every model endpoint points at the in-test scripted provider, and local models are disabled by
        # policy so boot never tries to pull or contact a local model runner
        "OLLAMA_HOST": provider.base_url,
        "VOOL_OLLAMA_URL": provider.base_url,
        "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat",
        "VOOL_LOCAL_MODELS_ENABLED": "0",
        "VOOL_INSTALL_PROFILE": "hybrid-fallback",
    }
    key = f"{probe.MONEY_ACCOUNT}|{probe.MONEY_USDC.key}"
    with provider:
        daemon = ServedDaemon(home, env_extra=env)
        try:
            daemon.start(timeout=240)
        except Exception as exc:  # pragma: no cover - environment gate, stated in the skip
            pytest.skip(f"served daemon could not boot here: {exc}")
        restarted = None
        # The journey pins the process runtime state to the served home (probe.configure_home).
        # The race children seal their node signer record under THAT home with their own
        # passphrase, so if the pin outlives this test, the next signer reload in this pytest
        # process resolves into the served home and dies with InvalidTag under the suite
        # passphrase (measured 2026-09-21: tests/test_identity_lifecycle.py and tests/legacy/*
        # red in the same shard). The finally hands every pinned piece back.
        runtime_state = probe.runtime_state_snapshot()
        try:
            daemon_store = home / "data" / "vool_web0_v2.db"
            assert daemon_store.exists(), sorted(str(p.relative_to(home)) for p in home.rglob("*.db"))
            probe.configure_home(home)
            eb.reset_effect_budget_process_state()
            token = eb.grant_operator_budget_authority("served money operator setup (outside every effect scope)")
            grant = probe.mint_prepaid_grant(token, task_id="served-task", max_total_atomic=4_000_000, per_operation_max_atomic=3_000_000)
            kill_grant = probe.mint_prepaid_grant(token, task_id="served-kill", max_total_atomic=4_000_000, per_operation_max_atomic=3_000_000)
            probe.observe_verified_credit(50_000_000)

            race = probe.race(
                home,
                "money_reserve",
                [{"request": probe.prepaid_request(f"served-op-{i}", grant.grant_id, 3_000_000, task_id="served-task"), "claim": True} for i in range(4)],
            )
            winners = [item for item in race if item.get("granted")]
            assert len(winners) == 1 and all(item["returncode"] == 0 for item in race), race
            status, body = _http(daemon, "GET", "/api/money/projection?task_id=served-task")
            assert status == 200, body
            # The winner exited holding its dispatch claim. It stays `dispatching` (held) until some
            # process's first use reconciles it to `unknown`, and a refused process that starts after
            # the winner exits can do that before this read. Either way the whole maximum stays
            # counted, nothing is settled, and the refused processes left no liability.
            expense = body["projection"]["inference_expense"][key]
            assert int(expense["held"]) + int(expense["unknown"]) == 3_000_000, body
            assert expense["settled_exact"] == "0" and expense["settled_bounded"] == "0", body
            states = body["projection"]["liability_states"]
            assert sum(states.values()) == 1 and set(states) <= {ebm.LIABILITY_DISPATCHING, ebm.LIABILITY_UNKNOWN}, body

            killed = probe.race(
                home,
                "money_sequence",
                [{"request": probe.prepaid_request("served-killed", kill_grant.grant_id, 1_000_000, task_id="served-kill"), "die_at": "after_claim", "evidence": {"kind": "provider_usage_receipt", "id": "never", "source": "provider"}}],
            )[0]
            assert killed["returncode"] == -signal.SIGKILL, killed
            status, body = _http(daemon, "POST", "/api/money/reconcile", {})
            assert status == 200, body
            # the winner may already have been judged by an earlier process's first use; the killed
            # claimant can only be judged inside the daemon (this test process never reconciles and
            # no other process opens the store between the kill and this call)
            assert {change["to"] for change in body["money_changes"]} <= {ebm.LIABILITY_UNKNOWN}, body
            for liability_id in (killed["liability_id"], winners[0]["liability_id"]):
                status, found = _http(daemon, "GET", f"/api/money/liabilities/{liability_id}")
                assert status == 200 and found["liability"]["state"] == ebm.LIABILITY_UNKNOWN, found
            judged = [row for row in _money_table_rows("effect_budget_events") if row["event_kind"] == "money_reconciled_unknown" and row["reservation_id"] == killed["liability_id"]]
            assert len(judged) == 1, judged
            detail = json.loads(judged[0]["detail_json"])
            assert (detail["judged_instance_id"], detail["proof"]) == (killed["instance_id"], eb.INSTANCE_DEATH_PROCESS_GONE), detail
            judge_rows = [row for row in _money_table_rows("effect_budget_instances") if row["instance_id"] == detail["reconciler_instance_id"]]
            assert judged[0]["instance_id"] == detail["reconciler_instance_id"] and len(judge_rows) == 1, (judged, judge_rows)
            assert int(judge_rows[0]["pid"]) == daemon.process.pid, "the served daemon itself judged the killed claimant"

            status, body = _http(daemon, "POST", "/api/money/grants/revoke", {"grant_id": grant.grant_id, "reason": "owner stopped the task"})
            assert status == 200 and body["grant"]["state"] == ebm.GRANT_REVOKED, body
            after = probe.race(home, "money_reserve", [{"request": probe.prepaid_request("served-after-revoke", grant.grant_id, 500_000, task_id="served-task")}])[0]
            assert after["granted"] is False and after["code"] == ebm.MONEY_AUTHORITY_REVOKED, after
            status, body = _http(daemon, "GET", f"/api/money/liabilities/{winners[0]['liability_id']}")
            assert status == 200 and body["liability"]["state"] == ebm.LIABILITY_UNKNOWN, "revocation kept the payment that may have gone out"

            status, body = _http(daemon, "POST", "/api/money/grants", {"kind": "task_envelope", "max_total_atomic": 10**12})
            assert status == 403 and body["error"] == "widening_not_served", body
            status, body = _http(daemon, "POST", "/api/money/reconcile", {}, origin="https://attacker.example")
            assert status == 403, body

            daemon.stop()
            restarted = ServedDaemon(home, env_extra=env)
            restarted.start(timeout=240)
            status, body = _http(restarted, "GET", "/api/money/projection?task_id=served-task")
            assert status == 200 and body["projection"]["liability_states"] == {ebm.LIABILITY_UNKNOWN: 1}, body
            assert body["projection"]["inference_expense"][key]["unknown"] == "3000000", body
            status, body = _http(restarted, "GET", "/api/money/grants?active=1")
            assert status == 200 and grant.grant_id not in {item["grant_id"] for item in body["grants"]}, body
        finally:
            if restarted is not None:
                restarted.stop()
            daemon.stop()
            probe.restore_runtime_state(runtime_state)


from core import runtime_paths as _runtime_paths

_PROCESS_VOOL_HOME_AT_IMPORT = os.environ.get("VOOL_HOME")
_PROCESS_HOME_OVERRIDE_AT_IMPORT = _runtime_paths._VOOL_HOME_OVERRIDE


def test_the_served_daemon_test_leaves_the_process_runtime_home_behind_it():
    """The served-home journey borrows the process runtime state, so it must give it back.

    This module is collected as one unit, so this case runs immediately after the served-daemon
    journey in every shard and ordering. If that journey leaves VOOL_HOME pointing at the served
    home (or the runtime-home override moved), every later signer-touching test in the same
    pytest process resolves its import-frozen paths into the served home and fails under the
    suite passphrase — the cross-suite poison measured on 2026-09-21.
    """
    assert os.environ.get("VOOL_HOME") == _PROCESS_VOOL_HOME_AT_IMPORT, (
        "VOOL_HOME drifted out of the served-daemon journey: "
        f"{os.environ.get('VOOL_HOME')!r} (session pin {_PROCESS_VOOL_HOME_AT_IMPORT!r})"
    )
    assert _runtime_paths._VOOL_HOME_OVERRIDE == _PROCESS_HOME_OVERRIDE_AT_IMPORT, (
        "runtime-home override drifted out of the served-daemon journey: "
        f"{_runtime_paths._VOOL_HOME_OVERRIDE!r} (at import {_PROCESS_HOME_OVERRIDE_AT_IMPORT!r})"
    )


def test_this_test_runs_on_its_own_isolated_store(request, tmp_path):
    """The package's store isolation and real-home guard hold for every money test whatever order the
    run collected files in (tests/effect_budget/conftest.py exports them for exactly this)."""
    from pathlib import Path

    from storage.db import active_default_db_path

    assert {"_isolated_budget_store", "_real_home_residue_guard"} <= set(request.fixturenames), request.fixturenames
    # Goal 2 stage 2 (2026-09-17): with the store in its own tmp subdir (see conftest), isolation means
    # this test's store lives inside THIS test's tmp tree and nowhere else.
    assert Path(active_default_db_path()).resolve().is_relative_to(Path(tmp_path).resolve())
