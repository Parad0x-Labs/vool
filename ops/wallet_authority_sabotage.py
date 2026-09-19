"""Sabotage every former money bypass and prove a named test bites.

Usage (from the repo root)::

    PYTHONPATH="$PWD" .venv/bin/python -m ops.wallet_authority_sabotage --out <log>

Each mutation re-arms ONE retired surface in memory (a pytest plugin patch applied at
``pytest_configure``, before collection -- no production file is edited), then runs the
suites that guard that surface. The run is a proof only when every mutation reds at least one
test that names the surface AND the same suites are green with no mutation (the control).

The plugin half: ``-p ops.wallet_authority_sabotage`` with ``VOOL_AUTHORITY_SABOTAGE=<name>``.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import types
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CENSUS = "tests/wallet/test_wallet_authority_census.py"
FAKE_SECRET = "5" * 88  # base58-shaped; never a real key


def _key_file_under(home: Path) -> Path:
    from core.vool_wallet import WALLET_FILENAME

    target = home / "data" / "keys" / WALLET_FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{}", encoding="utf-8")
    return target


def _home(runtime_home) -> Path:
    if runtime_home:
        return Path(runtime_home)
    from core.runtime_paths import active_vool_home

    return Path(active_vool_home())


# name -> (what the sabotage re-arms, the suites that must bite)
MUTATIONS: dict[str, tuple[str, tuple[str, ...]]] = {
    "legacy_create": ("core.vool_wallet.get_or_create_wallet mints a key file again", (CENSUS, "tests/test_vool_wallet.py", "tests/test_x402_wallet_signer_contract.py")),
    "legacy_export": ("vool_wallet reveal/export doors hand out a secret again", (CENSUS, "tests/test_wallet_export_cli.py", "tests/test_wallet_reveal_consent.py", "tests/test_vool_wallet.py")),
    "spend_tool": ("wallet_spend_tools.execute_spend and tool.wallet.spend send again", (CENSUS, "tests/test_wallet_spend.py", "tests/gauntlet/test_wallet_safety_gaps.py")),
    "pay_tool": ("tool.pay.x402 pays again", (CENSUS, "tests/test_payment_tools.py", "tests/gauntlet/test_prompt_injection_containment.py", "tests/gauntlet/test_tool_honesty_matrix.py")),
    "x402_live": ("X402Client._live_pay settles again (devnet/mainnet modes)", (CENSUS, "tests/test_x402_canonical.py", "tests/test_compute_rental_x402.py")),
    "dial_pay": ("null_dial._dial_pay_x402 pays a 402 again", (CENSUS, "tests/test_null_dial.py")),
    "anchor_broadcast": ("solana_anchor.submit_memo_anchor / anchor_vault_proof broadcast again", (CENSUS, "tests/test_anchor_off_hot_path.py", "tests/test_launch_readiness.py")),
    "register_tail": ("null_register_execute.sign_and_broadcast_registration signs+sends again", (CENSUS, "tests/test_null_register_execute.py", "tests/test_null_register_policy_enforcement.py")),
    "installer_wallet": ("installer.initialize_agent_wallet writes a key file again", (CENSUS, "tests/test_initialize_agent_wallet.py")),
    "mainnet_allowed": ("core.wallet.config admits a mainnet network and any RPC host", (CENSUS, "tests/wallet/test_wallet_lifecycle.py", "tests/wallet/test_wallet_sabotage.py")),
    "cli_export": ("apps.vool_cli.cmd_wallet_export prints a secret again", ("tests/test_wallet_export_cli.py",)),
    "dna_purchase": ("DNAPaymentBridge.purchase_credits / _from_dex buy again", (CENSUS, "tests/test_dna_payment_bridge_wallet_mode.py", "tests/legacy/test_credit_dex.py")),
    "brake_canonical_first": ("the mirrored freeze flips the canonical store BEFORE the fallible file write (the old order, no rollback)", ("tests/test_vool_agent_brake.py",)),
    "redaction_registry": ("the free-text redactor forgets which key-shaped strings the wallet minted as public (tx signatures masked out of chat again)", ("tests/wallet/test_wallet_lifecycle.py", "tests/test_credential_intelligence_redaction.py")),
}


def _apply(name: str) -> None:
    if name == "legacy_create":
        from core import vool_wallet

        def creating(*, runtime_home=None, derivation_key=None):
            _key_file_under(_home(runtime_home))
            return types.SimpleNamespace(pubkey="11111111111111111111111111111111", exists=lambda: True)

        vool_wallet.get_or_create_wallet = creating
    elif name == "legacy_export":
        from core import vool_wallet

        vool_wallet.reveal_wallet_secret_key_base58 = lambda **_kw: FAKE_SECRET
        vool_wallet.VoolWallet.export_secret_key_base58 = lambda self, *a, **k: FAKE_SECRET
    elif name == "spend_tool":
        from core import runtime_execution_tools as ret
        from core import wallet_spend_tools

        wallet_spend_tools.execute_spend = lambda **_kw: wallet_spend_tools.SpendResult(ok=True, status="executed", message="sent", details={"tx": "SIG"})
        ret._wallet_spend = lambda arguments: ret.RuntimeExecutionResult(handled=True, ok=True, status="executed", response_text="sent", details={})
    elif name == "pay_tool":
        from core.execution import payment_tools
        from core.execution.models import ToolIntentExecution

        def paid(arguments, *, source_context, dna_pay_and_unlock_fn, dna_get_quote_fn, resolve_x402_endpoint_fn):
            return ToolIntentExecution(handled=True, ok=True, status="paid", response_text="Paid 0.02 USDC", mode="tool_executed", tool_name="pay.x402", details={"executed": True, "amount_paid_usdc": 0.02})

        payment_tools._execute_pay_x402 = paid
    elif name == "x402_live":
        from core.x402 import client

        client.X402Client._live_pay = lambda self, a, r, s: self._stub_pay(a, r, s)
    elif name == "dial_pay":
        from core import null_dial

        null_dial._dial_pay_x402 = lambda resource_url, wallet, *, requirements=None, **kw: {"status": "paid", "payment_tx": "SIG", "amount_paid_usdc": 0.02, "resource_response": {}}
    elif name == "anchor_broadcast":
        from core import solana_anchor

        solana_anchor.submit_memo_anchor = lambda *a, **k: "SIG" * 15
        solana_anchor.anchor_vault_proof = lambda *a, **k: "SIG" * 15
    elif name == "register_tail":
        from core import null_register_execute

        null_register_execute.sign_and_broadcast_registration = lambda plan, wallet, *, blockhash_fn=None, rpc=None: "SIG" * 15
    elif name == "installer_wallet":
        from installer import initialize_agent_wallet

        def installing(runtime_home=None, *a, **k):
            _key_file_under(_home(runtime_home))
            return "11111111111111111111111111111111"

        initialize_agent_wallet.initialize_agent_wallet = installing
    elif name == "mainnet_allowed":
        from core.wallet import config

        config.network_allowed = lambda _n: True
        config.rpc_url_allowed = lambda _u: True
    elif name == "cli_export":
        from apps import vool_cli

        def exporting(**_kw):
            print(FAKE_SECRET)
            return 0

        vool_cli.cmd_wallet_export = exporting
    elif name == "dna_purchase":
        from core import dna_payment_bridge

        dna_payment_bridge.DNAPaymentBridge.purchase_credits = lambda self, amount, *a, **k: {"ok": True, "credits": float(amount) * 100}
        dna_payment_bridge.DNAPaymentBridge.purchase_credits_from_dex = lambda self, credits, *a, **k: {"ok": True, "credits": float(credits)}
    elif name == "brake_canonical_first":
        from core import wallet_spend_policy_store as store

        def canonical_first(frozen, *, wallet=None, policy=None, ledger=None, now=None):
            from core.wallet import limits
            from core.wallet_spend_policy import freeze, unfreeze

            limits.set_frozen(bool(frozen))
            if policy is None or ledger is None:
                loaded_policy, loaded_ledger = store.load_policy_and_ledger(wallet, allow_missing_policy=True)
                policy = loaded_policy if policy is None else policy
                ledger = loaded_ledger if ledger is None else ledger
            policy = freeze(policy) if frozen else unfreeze(policy)
            store.save_policy_and_ledger(wallet, policy, ledger, now=now)
            return False

        store.set_frozen_mirrored = canonical_first
    elif name == "redaction_registry":
        from core import secret_redaction

        secret_redaction.register_public_identifier = lambda value: None
    else:
        raise SystemExit(f"unknown sabotage {name!r}")


def pytest_configure(config) -> None:  # the plugin half
    name = os.environ.get("VOOL_AUTHORITY_SABOTAGE", "")
    if name:
        _apply(name)


def _run(targets: tuple[str, ...], sabotage: str) -> tuple[int, list[str], str]:
    env = dict(os.environ, PYTHONPATH=str(REPO), VOOL_AUTHORITY_SABOTAGE=sabotage)
    cmd = [sys.executable, "-m", "pytest", *targets, "-p", "ops.wallet_authority_sabotage", "-p", "no:cacheprovider", "-q", "--tb=no", "-rfE"]
    proc = subprocess.run(cmd, cwd=REPO, env=env, capture_output=True, text=True)
    lines = (proc.stdout + proc.stderr).splitlines()
    failed = [line for line in lines if line.startswith(("FAILED ", "ERROR "))]
    summary = next((line for line in reversed(lines) if " in " in line and ("passed" in line or "failed" in line or "error" in line)), "(no summary)")
    return proc.returncode, failed, summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True)
    parser.add_argument("--only", default="", help="comma-separated mutation names")
    args = parser.parse_args(argv)
    names = [n for n in args.only.split(",") if n] or list(MUTATIONS)
    out: list[str] = [
        "# AUTHORITY RECONCILIATION sabotage — every former bypass re-armed in memory, one at a time",
        f"# {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} · repo {REPO} · python {sys.version.split()[0]}",
        "# method: pytest plugin patch at pytest_configure (no production edit); PROOF = control green AND every mutation reds >=1 named test",
    ]
    # One control per mutation over EXACTLY its target files (a union run mixes fixtures from
    # unrelated suites and proves nothing about the pair being compared).
    controls: dict[tuple[str, ...], tuple[int, list[str], str]] = {}
    verdicts: list[bool] = []
    control_ok = True
    for name in names:
        what, targets = MUTATIONS[name]
        if targets not in controls:
            controls[targets] = _run(targets, "")
        c_rc, c_failed, c_summary = controls[targets]
        this_control_ok = c_rc == 0 and not c_failed
        control_ok = control_ok and this_control_ok
        _rc, failed, summary = _run(targets, name)
        bites = bool(failed) and this_control_ok
        verdicts.append(bites)
        out.append(f"{'BITES ' if bites else 'NO-BITE'} {name}: {what}")
        out.append(f"  files: {' '.join(targets)}")
        out.append(f"  control (no sabotage): {c_summary}{'' if this_control_ok else '  <-- CONTROL RED'}")
        out.extend(f"    {line}" for line in c_failed[:6])
        out.append(f"  sabotaged: {summary}")
        out.extend(f"  {line}" for line in failed[:14])
        if len(failed) > 14:
            out.append(f"  ... {len(failed) - 14} more")
    ok = control_ok and all(verdicts)
    out.append(f"VERDICT: {'PROVEN' if ok else 'NOT PROVEN'} — controls {'all green' if control_ok else 'RED'}; {sum(verdicts)}/{len(verdicts)} mutations bite against a green control")
    Path(args.out).write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n".join(out))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
