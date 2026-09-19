"""Cross-process money race and recovery probe.

An instrument, not a gate: it runs money scenarios in INDEPENDENT `python -B`
processes against one shared store and prints what it observed as JSON. It
exits 0 whatever it observes; the test suite owns pass/fail. Not collected by
pytest (no `test_` prefix); the cross-process tests import its race runner.

Mode `legacy` drives the production money calls that exist at the pinned
base, unchanged:

* unit budgets (`core.effect_budget.reserve_effect_units`) and the effect
  gateway's retry law (`EffectLedger.open_effect(..., retry_of=...)`);
* the paid-call USD ledger (`core.model_spend_ledger.reserve_spend`) and its
  production failure path (`core.paid_call_reservation.release_owner_pick_paid_call`);
* wallet ceilings (`core.wallet.limits.reserve_spend`).

Every worker waits at a file barrier after its imports so the reservations
collide; a worker asked to die kills itself with SIGKILL after its call
returns, which is what a crash between a reservation and its settlement is.

Run from the repository root:

    python -B -m tests.effect_budget.money_race_probe --mode legacy --home <empty dir> --out <file.json>
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
PROBE_MODULE = "tests.effect_budget.money_race_probe"
DESTINATION = "11111111111111111111111111111111"

_WORKERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {}
_WARMERS: dict[str, Callable[[], None]] = {}


def worker(name: str, *, warm: Callable[[], None] | None = None):
    def register(function: Callable[[dict[str, Any]], dict[str, Any]]):
        _WORKERS[name] = function
        if warm is not None:
            _WARMERS[name] = warm
        return function

    return register


def configure_home(home: Path) -> Path:
    """Point every store this probe touches at one database under `home`."""
    home = Path(home)
    (home / "data").mkdir(parents=True, exist_ok=True)
    os.environ["VOOL_HOME"] = str(home)
    from core import runtime_paths

    runtime_paths.configure_runtime_home(home)
    from storage.db import configure_default_db_path

    db_name = os.environ.get("MONEY_PROBE_DB_NAME", "probe.db")
    if "/" in db_name or "\\" in db_name or not db_name.endswith(".db"):
        raise ValueError("MONEY_PROBE_DB_NAME must be a bare .db file name")
    db_path = home / "data" / db_name
    configure_default_db_path(db_path)
    from core.runtime_continuity import configure_runtime_continuity_db_path

    configure_runtime_continuity_db_path(db_path)
    return db_path


def child_env(home: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "PYTHONPATH": str(REPO_ROOT),
            "PYTHONDONTWRITEBYTECODE": "1",
            "VOOL_HOME": str(home),
            "VOOL_KEY_STORAGE_MODE": "file",
            "VOOL_KEY_PASSPHRASE": "money-race-probe",
        }
    )
    env.pop("PYTEST_CURRENT_TEST", None)
    return env


def race(home: Path, worker_name: str, arguments: list[dict[str, Any]], *, timeout: float = 180.0) -> list[dict[str, Any]]:
    """Start one process per argument set, open the barrier once every process
    is ready, and collect each process's JSON result and exit code."""
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    barrier = home / f"barrier-{worker_name}-{time.monotonic_ns()}"
    processes = []
    for index, args in enumerate(arguments):
        command = [
            sys.executable,
            "-B",
            "-m",
            PROBE_MODULE,
            "--worker",
            worker_name,
            "--home",
            str(home),
            "--go",
            str(barrier),
            "--args",
            json.dumps({"index": index, **args}),
        ]
        processes.append(
            subprocess.Popen(
                command,
                cwd=str(REPO_ROOT),
                env=child_env(home),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        )
    deadline = time.monotonic() + timeout
    while len(list(home.glob(barrier.name + ".ready-*"))) < len(processes):
        if any(process.poll() is not None for process in processes) or time.monotonic() > deadline:
            break
        time.sleep(0.01)
    barrier.write_text("go", encoding="utf-8")
    results: list[dict[str, Any]] = []
    for process in processes:
        try:
            stdout, stderr = process.communicate(timeout=max(1.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
        parsed: dict[str, Any] = {}
        for line in reversed((stdout or "").splitlines()):
            try:
                candidate = json.loads(line)
            except ValueError:
                continue
            if isinstance(candidate, dict):
                parsed = candidate
                break
        parsed["returncode"] = process.returncode
        if process.returncode not in (0, -signal.SIGKILL):
            parsed["stderr_tail"] = (stderr or "")[-1200:]
        results.append(parsed)
    return results


def _child_main(worker_name: str, home: Path, barrier: Path, args: dict[str, Any]) -> None:
    configure_home(home)
    warm = _WARMERS.get(worker_name)
    if warm is not None:
        warm()
    Path(str(barrier) + f".ready-{os.getpid()}").write_text("ready", encoding="utf-8")
    deadline = time.monotonic() + 120.0
    while not barrier.exists():
        if time.monotonic() > deadline:
            raise TimeoutError("the barrier never opened")
        time.sleep(0.001)
    result = _WORKERS[worker_name](args)
    print(json.dumps({"pid": os.getpid(), **result}, sort_keys=True, default=str), flush=True)


def die_now(result: dict[str, Any]) -> None:
    """Report, then die the way a crash dies: no cleanup, no finally blocks."""
    print(json.dumps({"pid": os.getpid(), "killed_self": True, **result}, sort_keys=True, default=str), flush=True)
    os.kill(os.getpid(), signal.SIGKILL)


# ---------------------------------------------------------------------------
# legacy workers: the pinned base's production money calls
# ---------------------------------------------------------------------------


def _warm_unit() -> None:
    from core import effect_budget


def _warm_paid() -> None:
    from core import model_spend_ledger, paid_call_reservation


def _warm_wallet() -> None:
    from core.wallet import limits


@worker("legacy_unit_reserve", warm=_warm_unit)
def _legacy_unit_reserve(args: dict[str, Any]) -> dict[str, Any]:
    from core import effect_budget as eb

    try:
        receipt = eb.reserve_effect_units(
            "provider_call", session_id=str(args["session_id"]), project_key="probe", units=int(args["units"])
        )
    except eb.EffectBudgetRefusedError as refusal:
        return {"granted": False, "code": refusal.code, "rule": refusal.rule}
    return {"granted": True, "reservation_id": receipt.reservation_id, "units": receipt.units}


@worker("legacy_paid_reserve", warm=_warm_paid)
def _legacy_paid_reserve(args: dict[str, Any]) -> dict[str, Any]:
    from core.model_spend_ledger import SpendLimits, reserve_spend

    limits = SpendLimits(
        per_call_usd=float(args["per_call_usd"]),
        per_task_usd=float(args["per_task_usd"]),
        daily_usd=float(args["daily_usd"]),
        monthly_usd=float(args["monthly_usd"]),
    )
    try:
        reservation = reserve_spend(
            model_call_id=str(args["model_call_id"]),
            task_id=str(args["task_id"]),
            subtask_id="money_race_probe",
            model_id="probe/paid-model",
            maximum_usd=float(args["maximum_usd"]),
            limits=limits,
        )
    except PermissionError as refusal:
        return {"granted": False, "code": str(refusal)}
    result = {"granted": True, "model_call_id": reservation.model_call_id, "reserved_usd": reservation.reserved_usd}
    if args.get("die_after_reserve"):
        die_now(result)
    return result


@worker("legacy_wallet_reserve", warm=_warm_wallet)
def _legacy_wallet_reserve(args: dict[str, Any]) -> dict[str, Any]:
    from core.wallet import limits

    verdict = limits.reserve_spend(
        wallet_id=str(args["wallet_id"]),
        asset=str(args["asset"]),
        amount_minor=int(args["amount_minor"]),
        destination=str(args["destination"]),
        proposal_id=str(args["proposal_id"]),
        fee_minor=int(args["fee_minor"]),
        chain=str(args["chain"]),
    )
    return {"granted": bool(verdict.ok), "limit": verdict.limit, "reason": verdict.reason}


# ---------------------------------------------------------------------------
# legacy scenarios
# ---------------------------------------------------------------------------


def scenario_legacy_unit_budget(home: Path) -> dict[str, Any]:
    configure_home(home)
    from core import effect_budget as eb

    token = eb.grant_operator_budget_authority("money race probe: legacy unit budget")
    eb.apply_operator_adjustment(
        token,
        [eb.BudgetAdjustment(budget_class="provider_call", scope=eb.SCOPE_SESSION, new_limit=4, note="total 4")],
    )
    results = race(home, "legacy_unit_reserve", [{"session_id": "probe-race", "units": 3} for _ in range(4)])
    status = eb.budget_status("provider_call", session_id="probe-race")[0]

    # The gateway's retry law, driven through the production effect ledger in
    # this process (a single-process semantics check, labelled as such): one
    # logical effect may begin several transport attempts under ONE unit.
    eb.apply_operator_adjustment(
        token,
        [eb.BudgetAdjustment(budget_class="provider_call", scope=eb.SCOPE_SESSION, new_limit=1, note="one call")],
    )
    from core.effect_gateway import (
        DECISION_ALLOWED,
        LIFECYCLE_AUTHORIZED,
        EffectReceipt,
        close_effect_receipt_scope,
        open_effect_receipt_scope,
    )

    context = {"session_id": "probe-retry", "turn_id": "turn-retry", "request_id": "req-retry", "workspace_root": str(home)}
    ledger = open_effect_receipt_scope(context)
    attempts_begun = 0
    retry_refusal = ""
    try:
        receipt = EffectReceipt(
            effect_class="provider_call", decision=DECISION_ALLOWED, lifecycle=LIFECYCLE_AUTHORIZED, reason="probe paid attempt"
        )
        first = ledger.open_effect(receipt)
        first.begin_attempt()
        attempts_begun += 1
        first.fail(reason="stream_interrupted_after_request_sent")
        try:
            retry = ledger.open_effect(receipt, retry_of=first.effect_id)
            retry.begin_attempt()
            attempts_begun += 1
            retry.succeed()
        except eb.EffectBudgetRefusedError as refusal:
            retry_refusal = refusal.code
    finally:
        close_effect_receipt_scope()
    retry_rows = [row for row in eb.reservation_rows() if row.get("session_id") == "probe-retry"]
    return {
        "race": {
            "workers": results,
            "granted": sum(1 for item in results if item.get("granted")),
            "units_used_after_race": status.used,
            "limit": status.limit,
        },
        "retry_law_single_process": {
            "session_limit_units": 1,
            "transport_attempts_begun": attempts_begun,
            "retry_refusal_code": retry_refusal,
            "reservations_for_session": len(retry_rows),
            "reservation_states": sorted(str(row.get("state")) for row in retry_rows),
        },
        "api_facts": {
            "gateway_open_effect_units_per_effect": 1,
            "unit_rows_have_asset_or_amount_columns": False,
            "unit_states": [eb.RESERVATION_RESERVED, eb.RESERVATION_CONSUMED, eb.RESERVATION_RELEASED],
        },
    }


def scenario_legacy_paid_ledger(home: Path) -> dict[str, Any]:
    configure_home(home)
    from core import paid_call_reservation
    from core.model_spend_ledger import get_spend_reservation

    limits = {"per_call_usd": 3.0, "per_task_usd": 4.0, "daily_usd": 1000.0, "monthly_usd": 1000.0}
    first = race(
        home,
        "legacy_paid_reserve",
        [{"model_call_id": f"race-a-{i}", "task_id": "task-race", "maximum_usd": 3.0, **limits} for i in range(4)],
    )
    winners = [item for item in first if item.get("granted")]
    released = {}
    if winners:
        winner = str(winners[0]["model_call_id"])
        # The winner's request left and its stream died. This is the production
        # failure path the paid lane calls with reason "call_failed".
        paid_call_reservation.release_owner_pick_paid_call(SimpleNamespace(model_call_id=winner), reason="call_failed")
        row = get_spend_reservation(winner)
        released = {"model_call_id": winner, "status_after_call_failed": row.status if row else None, "reserved_usd_after": row.reserved_usd if row else None}
    second = race(
        home,
        "legacy_paid_reserve",
        [{"model_call_id": f"race-b-{i}", "task_id": "task-race", "maximum_usd": 3.0, **limits} for i in range(3)],
    )
    granted_total = sum(1 for item in first + second if item.get("granted"))
    crash = race(
        home,
        "legacy_paid_reserve",
        [{"model_call_id": "crash-1", "task_id": "task-crash", "maximum_usd": 3.0, "die_after_reserve": True, **limits}],
    )
    crash_row = get_spend_reservation("crash-1")
    after_crash = race(
        home,
        "legacy_paid_reserve",
        [{"model_call_id": "after-crash-1", "task_id": "task-crash", "maximum_usd": 3.0, **limits}],
    )
    return {
        "per_task_cap_usd": 4.0,
        "per_call_maximum_usd": 3.0,
        "race_one": first,
        "call_failed_release": released,
        "race_two_after_release": second,
        "possibly_billed_reservations_granted": granted_total,
        "possibly_billed_exposure_usd": granted_total * 3.0,
        "crash_between_reserve_and_settle": {
            "worker": crash,
            "row_status_after_crash": crash_row.status if crash_row else None,
            "row_reserved_usd_after_crash": crash_row.reserved_usd if crash_row else None,
            "fresh_process_reserve_after_crash": after_crash,
        },
    }


def scenario_legacy_wallet_limits(home: Path) -> dict[str, Any]:
    configure_home(home)
    from core.wallet import limits

    limits.set_limits(
        "probe-wallet",
        "USDC",
        limits.SpendLimits(per_tx_minor=10_000_000, daily_minor=4_000_000, per_destination_daily_minor=4_000_000),
    )
    results = race(
        home,
        "legacy_wallet_reserve",
        [
            {
                "wallet_id": "probe-wallet",
                "asset": "USDC",
                "amount_minor": 3_000_000,
                "destination": DESTINATION,
                "proposal_id": f"probe-p{i}",
                "fee_minor": 5_000,
                "chain": "solana:probe-network",
            }
            for i in range(4)
        ],
    )
    with limits.connection() as conn:
        rows = [
            {"proposal_id": row[0], "asset": row[1], "amount_minor": int(row[2]), "fee_minor": int(row[3]), "state": row[4]}
            for row in conn.execute(
                "SELECT proposal_id, asset, amount_minor, fee_minor, state FROM wallet_spend_ledger ORDER BY id"
            ).fetchall()
        ]
    held = sum(row["amount_minor"] + row["fee_minor"] for row in rows if row["state"] in ("reserved", "settled"))
    import inspect

    return {
        "workers": results,
        "granted": sum(1 for item in results if item.get("granted")),
        "ledger_rows": rows,
        "held_in_the_USDC_bucket_minor": held,
        "fee_minor_passed_in_lamport_units": 5_000,
        "reserve_spend_parameters": sorted(inspect.signature(limits.reserve_spend).parameters),
    }


LEGACY_SCENARIOS: dict[str, Callable[[Path], dict[str, Any]]] = {
    "legacy_unit_budget": scenario_legacy_unit_budget,
    "legacy_paid_ledger": scenario_legacy_paid_ledger,
    "legacy_wallet_limits": scenario_legacy_wallet_limits,
}


# ---------------------------------------------------------------------------
# money: the monetary law (`core.effect_budget_money`), when the tree has it
# ---------------------------------------------------------------------------

try:  # the pinned base has no monetary law; legacy mode must still import
    from core.effect_budget_money import AssetIdentity as _AssetIdentity
except ImportError:  # pragma: no cover - base tree
    _AssetIdentity = None

#: SYNTHETIC identities: exact-compared like real ones, naming no real network,
#: mint, account or endpoint.
MONEY_NETWORK = "solana:synthetic-cluster"
MONEY_PROVIDER = "usepod-synthetic"
MONEY_ACCOUNT = "usepod-synthetic:acct-1"
MONEY_X402_ACCOUNT = "usepod-synthetic:x402-payer-1"
MONEY_PAYER = "wallet-synthetic-payer-1"
MONEY_USDC = (
    _AssetIdentity(network=MONEY_NETWORK, asset="SyntheticUsdcMint11111111111111111111111111", decimals=6, symbol="USDC")
    if _AssetIdentity is not None
    else None
)
MONEY_SOL = _AssetIdentity(network=MONEY_NETWORK, asset="native", decimals=9, symbol="SOL") if _AssetIdentity is not None else None


def prepaid_request(
    operation_id: str,
    grant_id: str,
    maximum: int,
    *,
    task_id: str = "task-1",
    session_id: str = "sess-1",
    model: str = "model-x",
    route: str = "marketplace-only",
    account: str = MONEY_ACCOUNT,
) -> dict[str, Any]:
    return {
        "operation_id": operation_id,
        "operation_kind": "inference_prepaid",
        "grant_id": grant_id,
        "lines": [
            ["inference_expense", MONEY_USDC.key, int(maximum), account],
            ["provider_credit_debit", MONEY_USDC.key, int(maximum), account],
        ],
        "identity": {"task_id": task_id, "session_id": session_id, "provider_id": MONEY_PROVIDER},
        "provider_account": account,
        "model_id": model,
        "route": route,
    }


def x402_request(operation_id: str, grant_id: str, cap: int, *, fee: int = 5_000, task_id: str = "task-1") -> dict[str, Any]:
    lines = [
        ["wallet_outflow", MONEY_USDC.key, int(cap), MONEY_PAYER],
        ["inference_expense", MONEY_USDC.key, int(cap), MONEY_X402_ACCOUNT],
    ]
    if fee:
        lines.append(["network_fee", MONEY_SOL.key, int(fee), MONEY_PAYER])
    return {
        "operation_id": operation_id,
        "operation_kind": "inference_x402",
        "grant_id": grant_id,
        "lines": lines,
        "identity": {"task_id": task_id, "session_id": "sess-1", "provider_id": MONEY_PROVIDER},
        "provider_account": MONEY_X402_ACCOUNT,
        "model_id": "model-x",
        "route": "marketplace-only",
        "network": MONEY_NETWORK,
        "payer_account": MONEY_PAYER,
    }


def request_from_dict(data: dict[str, Any]) -> Any:
    from core import effect_budget_money as ebm

    return ebm.LiabilityRequest(
        operation_id=str(data["operation_id"]),
        operation_kind=str(data["operation_kind"]),
        grant_id=str(data["grant_id"]),
        lines=tuple(
            ebm.MoneyLine(str(flow), ebm.AssetIdentity.from_key(str(asset_key)), int(maximum), str(account))
            for flow, asset_key, maximum, account in data["lines"]
        ),
        identity=ebm.MoneyIdentity(**dict(data.get("identity") or {})),
        provider_account=str(data.get("provider_account") or ""),
        model_id=str(data.get("model_id") or ""),
        route=str(data.get("route") or ""),
        network=str(data.get("network") or ""),
        payer_account=str(data.get("payer_account") or ""),
        expires_epoch=float(data.get("expires_epoch") or 0.0),
        correlation=dict(data.get("correlation") or {}),
    )


def evidence_from_dict(data: dict[str, Any]) -> Any:
    from core import effect_budget_money as ebm

    return ebm.SettlementEvidence(
        evidence_kind=str(data["kind"]),
        evidence_id=str(data["id"]),
        source=str(data.get("source") or "provider"),
        actuals={str(flow): int(amount) for flow, amount in dict(data.get("actuals") or {}).items()},
        credits=tuple(
            ebm.CreditLine(asset=ebm.AssetIdentity.from_key(str(asset_key)), account=str(account), amount_atomic=int(amount), verified=bool(verified))
            for asset_key, account, amount, verified in list(data.get("credits") or [])
        ),
    )


def mint_prepaid_grant(token: Any, **overrides: Any) -> Any:
    from dataclasses import replace

    from core import effect_budget_money as ebm

    spec = ebm.MoneyGrantSpec(
        kind=ebm.GRANT_TASK_ENVELOPE,
        operation_kinds=(ebm.OP_INFERENCE_PREPAID,),
        provider_id=MONEY_PROVIDER,
        provider_account=MONEY_ACCOUNT,
        models=("model-x", "model-y"),
        routes=("marketplace-only",),
        asset=MONEY_USDC,
        max_total_atomic=4_000_000,
        per_operation_max_atomic=3_000_000,
        expires_epoch=time.time() + 3600,
        task_id="task-1",
    )
    return ebm.grant_money_authority(token, replace(spec, **overrides))


def mint_x402_grant(token: Any, **overrides: Any) -> Any:
    from dataclasses import replace

    from core import effect_budget_money as ebm

    spec = ebm.MoneyGrantSpec(
        kind=ebm.GRANT_TASK_ENVELOPE,
        operation_kinds=(ebm.OP_INFERENCE_X402,),
        provider_id=MONEY_PROVIDER,
        provider_account=MONEY_X402_ACCOUNT,
        models=("model-x",),
        routes=("marketplace-only",),
        network=MONEY_NETWORK,
        payer_account=MONEY_PAYER,
        asset=MONEY_USDC,
        max_total_atomic=2_000_000,
        per_operation_max_atomic=500_000,
        fee_asset=MONEY_SOL,
        max_fee_total_atomic=50_000,
        per_operation_max_fee_atomic=10_000,
        expires_epoch=time.time() + 3600,
        task_id="task-1",
    )
    return ebm.grant_money_authority(token, replace(spec, **overrides))


def observe_verified_credit(balance: int, *, account: str = MONEY_ACCOUNT) -> None:
    from core import effect_budget_money as ebm

    ebm.record_liquidity_observation(
        account=account, asset=MONEY_USDC, balance_atomic=int(balance), source="provider:x-balance-remaining", verified=True
    )


def observe_wallet(*, usdc: int, sol: int) -> None:
    from core import effect_budget_money as ebm

    ebm.record_liquidity_observation(account=MONEY_PAYER, asset=MONEY_USDC, balance_atomic=int(usdc), source="rpc:getTokenAccountBalance", verified=True)
    ebm.record_liquidity_observation(account=MONEY_PAYER, asset=MONEY_SOL, balance_atomic=int(sol), source="rpc:getBalance", verified=True)


def prepare_shared_store(home: Path) -> None:
    """Bring the shared store to the state a booted install has before any
    worker runs: storage migrated and the wallet store's tables present.
    Workers then share an initialized store, as processes beside a running
    daemon do."""
    db_path = configure_home(home)
    from storage.migrations import run_migrations

    run_migrations(db_path)
    from core.wallet import limits

    limits.is_frozen()


def _warm_money() -> None:
    from core import effect_budget_money


@worker("money_reserve", warm=_warm_money)
def _money_reserve(args: dict[str, Any]) -> dict[str, Any]:
    from core import effect_budget as eb
    from core import effect_budget_money as ebm

    request = request_from_dict(args["request"])
    try:
        receipt = ebm.reserve_liability(request)
    except eb.EffectBudgetRefusedError as refusal:
        return {"granted": False, "code": refusal.code, "rule": refusal.rule, "operation_id": request.operation_id, "instance_id": eb.current_budget_instance_id(), "pid": os.getpid()}
    result: dict[str, Any] = {
        "granted": True,
        "liability_id": receipt.liability_id,
        "state": receipt.state,
        "idempotent": receipt.idempotent,
        "operation_id": request.operation_id,
        "instance_id": eb.current_budget_instance_id(),
        "pid": os.getpid(),
    }
    # An executor that won claims dispatch at once — that is what makes its
    # liability outlive its process. A reservation left unclaimed by a process
    # that exits is released by the next reconciliation (nothing could have
    # been paid without a claim).
    if args.get("claim"):
        claim = ebm.claim_dispatch(receipt.liability_id, executor=f"probe-process-{os.getpid()}")
        result["claimed_attempt"] = claim.attempt
        if args.get("after_claim") in ("dispatched", "unknown"):
            ebm.record_dispatched(receipt.liability_id, claim.claim_token, evidence_id=f"request-{request.operation_id}")
        if args.get("after_claim") == "unknown":
            ebm.record_unknown(receipt.liability_id, claim.claim_token, reason="stream_interrupted_after_request_sent")
        result["state"] = ebm.liability(receipt.liability_id)["state"]
    return result


@worker("money_claim", warm=_warm_money)
def _money_claim(args: dict[str, Any]) -> dict[str, Any]:
    from core import effect_budget as eb
    from core import effect_budget_money as ebm

    try:
        claim = ebm.claim_dispatch(str(args["liability_id"]), executor=f"probe-process-{os.getpid()}")
    except eb.EffectBudgetRefusedError as refusal:
        return {"claimed": False, "code": refusal.code}
    return {"claimed": True, "attempt": claim.attempt}


@worker("money_settle", warm=_warm_money)
def _money_settle(args: dict[str, Any]) -> dict[str, Any]:
    from core import effect_budget as eb
    from core import effect_budget_money as ebm

    try:
        evidence = evidence_from_dict(args["evidence"])
        result = ebm.settle_liability(str(args["liability_id"]), evidence)
    except eb.EffectBudgetRefusedError as refusal:
        return {"applied": False, "code": refusal.code}
    return {"applied": True, "idempotent": bool(result.get("idempotent")), "state": result.get("state")}


@worker("money_projection", warm=_warm_money)
def _money_projection(args: dict[str, Any]) -> dict[str, Any]:
    from core import effect_budget_money as ebm

    return {"projection": ebm.money_projection(task_id=str(args.get("task_id") or ""))}


@worker("money_sequence", warm=_warm_money)
def _money_sequence(args: dict[str, Any]) -> dict[str, Any]:
    """reserve -> claim -> dispatched -> settle, dying at `die_at` the way a
    crash dies. `inside_settle_commit` dies after the settlement's writes and
    receipt statement executed but BEFORE its transaction commits."""
    from core import effect_budget as eb
    from core import effect_budget_money as ebm

    request = request_from_dict(args["request"])
    die_at = str(args.get("die_at") or "")
    out: dict[str, Any] = {"operation_id": request.operation_id, "pid": os.getpid()}
    receipt = ebm.reserve_liability(request)
    out["liability_id"] = receipt.liability_id
    out["instance_id"] = eb.current_budget_instance_id()
    if die_at == "after_reserve":
        die_now({**out, "died_at": die_at})
    claim = ebm.claim_dispatch(receipt.liability_id, executor=f"probe-process-{os.getpid()}")
    if die_at == "after_claim":
        die_now({**out, "died_at": die_at})
    ebm.record_dispatched(receipt.liability_id, claim.claim_token, evidence_id=f"request-{request.operation_id}")
    if die_at == "after_dispatched":
        die_now({**out, "died_at": die_at})
    evidence = evidence_from_dict(args["evidence"])
    if die_at == "inside_settle_commit":
        journal = ebm._journal

        def dying_journal(conn: Any, kind: str, **kwargs: Any) -> None:
            journal(conn, kind, **kwargs)
            if kind in ("money_settled", "money_evidence_applied"):
                die_now({**out, "died_at": die_at, "inside_open_transaction": True})

        ebm._journal = dying_journal
    result = ebm.settle_liability(receipt.liability_id, evidence)
    out["state"] = result["state"]
    return out


def scenario_money_same_shape_as_legacy(home: Path) -> dict[str, Any]:
    """The legacy paid-ledger scenario's exact shape, against the monetary law:
    four processes needing 3 of 4; the winner's request leaves and its stream
    dies; three more processes race; then a crash between reserve and
    settlement, and a crash before any dispatch claim."""
    prepare_shared_store(home)
    from core import effect_budget as eb
    from core import effect_budget_money as ebm

    token = eb.grant_operator_budget_authority("money race probe: same shape")
    grant = mint_prepaid_grant(token, task_id="task-race")
    observe_verified_credit(100_000_000)
    # every winner claims, sends and loses its stream (the same failure as the
    # legacy scenario's call_failed path), in its own process
    first = race(
        home,
        "money_reserve",
        [
            {"request": prepaid_request(f"race-a-{i}", grant.grant_id, 3_000_000, task_id="task-race"), "claim": True, "after_claim": "unknown"}
            for i in range(4)
        ],
    )
    winners = [item for item in first if item.get("granted")]
    failure_path: dict[str, Any] = {}
    if winners:
        liability_id = str(winners[0]["liability_id"])
        failure_path = {"liability_id": liability_id, "state_after_failure": ebm.liability(liability_id)["state"]}
    second = race(home, "money_reserve", [{"request": prepaid_request(f"race-b-{i}", grant.grant_id, 3_000_000, task_id="task-race")} for i in range(3)])
    granted_total = sum(1 for item in first + second if item.get("granted"))
    crash_grant = mint_prepaid_grant(token, task_id="task-crash")
    evidence = {"kind": "provider_usage_receipt", "id": "never-arrives", "source": "provider", "actuals": {}}
    crash = race(
        home,
        "money_sequence",
        [
            {"request": prepaid_request("crash-after-claim", crash_grant.grant_id, 3_000_000, task_id="task-crash"), "die_at": "after_claim", "evidence": evidence},
        ],
    )
    eb.reset_effect_budget_process_state()
    reconciled = ebm.reconcile_money_liabilities(force=True)
    after_crash = race(home, "money_reserve", [{"request": prepaid_request("after-crash", crash_grant.grant_id, 3_000_000, task_id="task-crash")}])
    never_sent_grant = mint_prepaid_grant(token, task_id="task-never-sent")
    never_sent = race(
        home,
        "money_sequence",
        [{"request": prepaid_request("crash-after-reserve", never_sent_grant.grant_id, 3_000_000, task_id="task-never-sent"), "die_at": "after_reserve", "evidence": evidence}],
    )
    eb.reset_effect_budget_process_state()
    reconciled_never_sent = ebm.reconcile_money_liabilities(force=True)
    after_never_sent = race(home, "money_reserve", [{"request": prepaid_request("after-never-sent", never_sent_grant.grant_id, 3_000_000, task_id="task-never-sent")}])
    return {
        "grant_total_atomic": 4_000_000,
        "per_operation_maximum_atomic": 3_000_000,
        "race_one": first,
        "failure_after_request_sent": failure_path,
        "race_two_after_failure": second,
        "possibly_billed_reservations_granted": granted_total,
        "possibly_billed_exposure_atomic": granted_total * 3_000_000,
        "crash_after_dispatch_claim": {
            "worker": crash,
            "reconciled": reconciled,
            "fresh_process_reserve_after_crash": after_crash,
        },
        "crash_before_any_claim": {
            "worker": never_sent,
            "reconciled": reconciled_never_sent,
            "fresh_process_reserve_after_crash": after_never_sent,
        },
    }


MONEY_SCENARIOS: dict[str, Callable[[Path], dict[str, Any]]] = {
    "money_same_shape_as_legacy": scenario_money_same_shape_as_legacy,
}


def _run_scenarios(scenarios: dict[str, Callable[[Path], dict[str, Any]]], home: Path) -> dict[str, Any]:
    report: dict[str, Any] = {}
    for name, scenario in scenarios.items():
        scenario_home = Path(home) / name
        started = time.monotonic()
        try:
            report[name] = {"observed": scenario(scenario_home)}
        except Exception as exc:  # an instrument reports its own failure
            report[name] = {"error": f"{type(exc).__name__}: {exc}"}
        report[name]["seconds"] = round(time.monotonic() - started, 3)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=("legacy", "money"), default="legacy")
    parser.add_argument("--home", required=True)
    parser.add_argument("--out", default="")
    parser.add_argument("--worker", default="")
    parser.add_argument("--go", default="")
    parser.add_argument("--args", default="{}")
    options = parser.parse_args(argv)
    home = Path(options.home).resolve()
    if options.worker:
        _child_main(options.worker, home, Path(options.go), json.loads(options.args))
        return 0
    home.mkdir(parents=True, exist_ok=True)
    report = {
        "probe": PROBE_MODULE,
        "mode": options.mode,
        "python": sys.version.split()[0],
        "scenarios": _run_scenarios(LEGACY_SCENARIOS if options.mode == "legacy" else MONEY_SCENARIOS, home),
    }
    text = json.dumps(report, indent=2, sort_keys=True, default=str)
    if options.out:
        Path(options.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
