"""Sabotage every fence the devnet proof relies on, and prove a NAMED test bites.

Usage (from the repo root)::

    PYTHONPATH="$PWD" .venv/bin/python -m ops.wallet_devnet_proof_sabotage --out <log>

Each mutation disarms ONE guard in memory (a pytest plugin patch applied at ``pytest_configure``,
before collection -- no production file is edited), then runs the suites that guard it. The run is
a proof only when every mutation reds at least one test naming that guard AND the same suites are
green with no mutation (the control). A mutation that reds nothing is reported NO-BITE, not
quietly dropped: it means the guard has no independent coverage, which is the finding.

The plugin half: ``-p ops.wallet_devnet_proof_sabotage`` with ``VOOL_DEVNET_PROOF_SABOTAGE=<name>``.

Nothing here touches a real chain. Every suite it runs drives the pack's scripted loopback RPC.
"""
from __future__ import annotations

import argparse
import dataclasses
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

PROOF = "tests/wallet/test_wallet_devnet_external_proof.py"
LIFECYCLE = "tests/wallet/test_wallet_lifecycle.py"
X402 = "tests/wallet/test_wallet_x402_flow.py"

# name -> (the guard the mutation disarms, the suites that must bite)
MUTATIONS: dict[str, tuple[str, tuple[str, ...]]] = {
    "preflight_finalized": ("sendTransaction stops naming the preflight commitment, so preflight falls back to the RPC default (finalized) while the blockhash came from the confirmed bank", (PROOF, LIFECYCLE)),
    "simulate_default_commitment": ("simulateTransaction stops naming its commitment", (PROOF,)),
    "door_state_guard": ("approve_and_execute stops checking that the proposal is awaiting approval", (PROOF, LIFECYCLE)),
    "signature_fence": ("_claim stops refusing a proposal that already carries a broadcast signature", (PROOF, LIFECYCLE)),
    "cas_expected_state": ("the claim proceeds blind to the race: proposals.transition writes as though the row were still in the state this approval expected, which is the bypass the compare-and-set retired", (PROOF, LIFECYCLE)),
    "x402_binding_reuse": ("x402 stops recognising an existing binding, so a repeat fetch mints and pays a second proposal", (X402,)),
    "x402_delivery_receipt": ("the x402 binding never records that it was delivered, so every retry writes another delivered receipt", (X402,)),
    "mainnet_allowed": ("core.wallet.config admits any network, mainnet included", (PROOF, LIFECYCLE)),
    "leak_scan_blind": ("the proof's leak scan reports clean without reading anything", (PROOF,)),
}


def _apply(name: str) -> None:
    if name == "preflight_finalized":
        import base64

        from core.wallet import lifecycle

        def broadcast(self, raw_transaction: bytes) -> str:
            return str(self._call("sendTransaction", [base64.b64encode(raw_transaction).decode("ascii"), {"encoding": "base64", "skipPreflight": False}]) or "")

        lifecycle.RpcClient.broadcast = broadcast
    elif name == "simulate_default_commitment":
        import base64

        from core.wallet import lifecycle

        def simulate(self, raw_transaction: bytes) -> lifecycle.SimulationResult:
            result = self._call("simulateTransaction", [base64.b64encode(raw_transaction).decode("ascii"), {"encoding": "base64", "sigVerify": False, "replaceRecentBlockhash": True}])
            value = (result or {}).get("value") or {}
            if value.get("err"):
                return lifecycle.SimulationResult(False, 0, "simulation_error:unknown", 0)
            return lifecycle.SimulationResult(True, 5_000, "ok", int(value.get("unitsConsumed") or 0))

        lifecycle.RpcClient.simulate = simulate
    elif name == "door_state_guard":
        from core.wallet import lifecycle, proposals

        original = lifecycle.PaymentLifecycle._load

        def load_always_pending(self, proposal_id: str):
            proposal = original(self, proposal_id)
            return dataclasses.replace(proposal, state=proposals.STATE_PENDING_APPROVAL)

        lifecycle.PaymentLifecycle._load = load_always_pending
    elif name == "signature_fence":
        from core.wallet import lifecycle

        original_claim = lifecycle.PaymentLifecycle._claim

        def claim_without_signature(self, proposal, profile, *, method: str, payer_message):
            return original_claim(self, dataclasses.replace(proposal, tx_signature=""), profile, method=method, payer_message=payer_message)

        lifecycle.PaymentLifecycle._claim = claim_without_signature
    elif name == "cas_expected_state":
        # Simply dropping expected_state is a no-op at this anchor: the state table already forbids
        # approved -> approved, so a second claim still loses and nothing reds. The bypass the CAS
        # actually retired is "the row moved under me and I did not notice", so that is what this
        # re-arms -- the claim proceeds as though the proposal were still awaiting approval.
        from core.wallet import proposals
        from core.wallet.store import connection

        original_transition = proposals.transition

        def transition_blind_to_the_race(proposal_id, new_state, *, detail=None, expected_state=None, **columns):
            if expected_state is not None:
                with connection() as conn:
                    conn.execute("UPDATE wallet_proposals SET state = ? WHERE proposal_id = ?", (expected_state, str(proposal_id)))
            return original_transition(proposal_id, new_state, detail=detail, expected_state=expected_state, **columns)

        proposals.transition = transition_blind_to_the_race
    elif name == "x402_binding_reuse":
        from core.wallet import x402

        x402.binding_for_digest = lambda _digest: None
    elif name == "x402_delivery_receipt":
        from core.wallet import x402

        original_update = x402._update_binding

        def update_forgetting_state(request_digest: str, **fields):
            fields.pop("state", None)
            return original_update(request_digest, **fields)

        x402._update_binding = update_forgetting_state
    elif name == "mainnet_allowed":
        from core.wallet import config

        config.ALLOWED_NETWORKS = (config.NETWORK_SOLANA_DEVNET, "solana-mainnet")
        config.network_allowed = lambda _network: True
        config.rpc_url_allowed = lambda _url: True
    elif name == "leak_scan_blind":
        from ops import wallet_devnet_external_proof

        wallet_devnet_external_proof._scan_for_secrets = lambda **_kw: {"ok": True, "findings": [], "surfaces_scanned": 0, "bip39_run_threshold": 0}
    else:
        raise SystemExit(f"unknown sabotage {name!r}")


def pytest_configure(config) -> None:  # the plugin half
    name = os.environ.get("VOOL_DEVNET_PROOF_SABOTAGE", "")
    if name:
        _apply(name)


def _run(targets: tuple[str, ...], sabotage: str) -> tuple[int, list[str], str]:
    env = dict(os.environ, PYTHONPATH=str(REPO), VOOL_DEVNET_PROOF_SABOTAGE=sabotage, VOOL_WALLET_ENABLED="1", VOOL_WALLET_NETWORK_ENVIRONMENT="testnet")
    cmd = [sys.executable, "-m", "pytest", *targets, "-p", "ops.wallet_devnet_proof_sabotage", "-p", "no:cacheprovider", "-p", "no:randomly", "-q", "--tb=no", "-rfE"]
    proc = subprocess.run(cmd, cwd=REPO, env=env, capture_output=True, text=True, check=False)
    lines = (proc.stdout + proc.stderr).splitlines()
    failed = [line for line in lines if line.startswith(("FAILED ", "ERROR "))]
    summary = next((line for line in reversed(lines) if " in " in line and ("passed" in line or "failed" in line or "error" in line)), "(no summary)")
    return proc.returncode, failed, summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--out", required=True)
    parser.add_argument("--only", default="", help="comma-separated mutation names")
    args = parser.parse_args(argv)
    names = [n for n in args.only.split(",") if n] or list(MUTATIONS)
    out: list[str] = [
        "# DEVNET PROOF sabotage — every fence the proof leans on, disarmed one at a time",
        f"# {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} · repo {REPO} · python {sys.version.split()[0]}",
        "# method: pytest plugin patch at pytest_configure (no production edit); PROOF = control green AND every mutation reds >=1 named test",
    ]
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
