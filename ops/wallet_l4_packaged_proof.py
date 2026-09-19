"""L4 proof: the PACKAGED native app pays a v1-wire x402 offer through its own daemon.

Boots the self-contained VOOL.app's embedded runtime (``apps.vool_api_server``) in an
isolated home against the same loopback protocol rig the L1/L3 tests use, then drives the
official v1 ``X-PAYMENT`` journey over the artifact's real HTTP seam: 402 -> capped
proposal -> owner approval by external signature -> ONE submission -> chain-proven
settlement, plus one honest adversarial refusal. Nothing real: the only "funds" are
integers in the loopback rig.

Usage:
  .venv/bin/python ops/wallet_l4_packaged_proof.py /path/to/VOOL.app

Exit 0 with a JSON verdict on stdout; every leg names PASS/FAIL honestly.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

USDC = "0x036cbd53842c5426634e7929541ec2318f3dcf7e"
PAY_TO = "0x209693Bc6afc0C5328bA36FaF03C514EF312287C"
NETWORK = "eip155:84532"


def _post(base: str, path: str, body: dict) -> tuple[int, dict]:
    request = urllib.request.Request(base + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "Origin": "http://127.0.0.1", "Host": "127.0.0.1"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return int(response.status), json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return int(exc.code), json.loads(exc.read() or b"{}")


def _get(base: str, path: str) -> tuple[int, dict]:
    with urllib.request.urlopen(base + path, timeout=30) as response:
        return int(response.status), json.loads(response.read() or b"{}")


def main() -> int:
    app_path = Path(sys.argv[1] if len(sys.argv) > 1 else "").resolve()
    embedded = app_path / "Contents/Resources/python/bin/python3"
    artifact_root = app_path / "Contents/Resources/app"
    if not embedded.exists():
        print(json.dumps({"ok": False, "error": f"no embedded runtime at {embedded}"}))
        return 1
    verdict: dict = {"app": str(app_path), "legs": []}

    def leg(name: str, passed: bool, detail: str = "") -> bool:
        verdict["legs"].append({"leg": name, "status": "PASS" if passed else "FAIL", "detail": detail[:300]})
        return passed

    import socket

    from tests.wallet._rig_evm import EvmExtensionSigner, FacilitatorSimulator, ScriptedEvmRpc
    from tests.wallet._rig_provider import PromptRoutedProvider, seed_daemon
    from tests.wallet.test_wallet_v1_evm_wire import X402V1EvmResource

    def free_port() -> int:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    port = free_port()
    provider = PromptRoutedProvider()
    provider.__enter__()

    with tempfile.TemporaryDirectory(prefix="l4-home-") as home, ScriptedEvmRpc() as rpc, FacilitatorSimulator(networks=(NETWORK,)) as facilitator, X402V1EvmResource(facilitator, rpc, amount_minor=1000) as resource, EvmExtensionSigner() as signer:
        env = dict(os.environ)
        env.update({
            "VOOL_HOME": home,
            "PYTHONPATH": str(artifact_root),
            "VOOL_WALLET_ENABLED": "1",
            "VOOL_WALLET_NETWORK_ENVIRONMENT": "testnet",
            "VOOL_WALLET_X402_CAP_MINOR": "20000",
            "VOOL_WALLET_X402_ALLOW_LOOPBACK": "1",
            "VOOL_WALLET_RPC_URLS": json.dumps({NETWORK: rpc.url}),
            "VOOL_DISABLE_MESH_DAEMON": "1", "VOOL_DISABLE_COMPUTE_MODE": "1", "VOOL_DISABLE_STUN": "1",
            "VOOL_KEY_STORAGE_MODE": "file", "VOOL_KEY_PASSPHRASE": "l4-proof-rig",
            "VOOL_SKIP_PROVIDER_PREWARM": "1",
            # remote-only boot against the scripted loopback provider (no local model)
            "VOOL_INSTALL_PROFILE": "hybrid-fallback",
            "VOOL_ALWAYS_ON_CATALOG": "1",
            "OLLAMA_HOST": provider.base_url,
            "VOOL_OLLAMA_URL": provider.base_url,
            "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat",
            "VOOL_LOCAL_MODELS_ENABLED": "1", "VOOL_DAEMON_BIND_PORT": str(free_port()),
        })
        env.pop("PYTEST_CURRENT_TEST", None)
        log = open(Path(home) / "l4-daemon.log", "wb")  # noqa: SIM115 — handle owned by the child
        process = subprocess.Popen([str(embedded), "-m", "apps.vool_api_server", "--port", str(port), "--bind", "127.0.0.1"], env=env, stdout=log, stderr=log, cwd=str(artifact_root), start_new_session=True)
        try:
            seed_daemon(Path(home), provider.base_url)
            base = None
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    break
                try:
                    status, _health = _get(f"http://127.0.0.1:{port}", "/healthz")
                    if status == 200:
                        base = f"http://127.0.0.1:{port}"
                        break
                except Exception:
                    pass
                time.sleep(1.0)
            if not leg("daemon-boots-from-the-artifact", base is not None, base or "no healthy port found"):
                verdict["ok"] = False
                print(json.dumps(verdict, indent=1))
                return 1
            # the running daemon IS the artifact's runtime: same executable inode
            exe = subprocess.run(["lsof", "-p", str(process.pid), "-a", "-d", "txt", "-Fn"], capture_output=True, text=True).stdout
            leg("daemon-process-belongs-to-the-artifact", "VOOL.app" in exe, exe[:200])

            status, wallets = _post(base, "/api/wallet/external", {"public_key": signer.address, "network": NETWORK, "label": "l4"})
            leg("external-evm-account-registered", status == 200 and wallets.get("ok"), json.dumps(wallets)[:200])

            # register the loopback facilitator's capability in the artifact's own store
            status, discovered = _post(base, "/api/wallet/facilitator/discover", {"origin": facilitator.url, "facilitator_id": "fac-l4"})
            leg("facilitator-capability-discovered-into-the-artifact", status == 200 and discovered.get("ok"), json.dumps(discovered)[:200])

            # the ARTIFACT fetches the paid resource itself: its own outbound confinement
            # meets the 402, parses the v1 offer, parks the capped proposal and binds it
            status, fetched = _post(base, "/api/wallet/x402/fetch", {"url": resource.url, "wallet_id": wallets["wallet"]["wallet_id"]})
            outcome = fetched.get("outcome") or {}
            proposal_id = outcome.get("proposal_id", "")
            leg("artifact-fetch-meets-the-402-and-parks-a-capped-proposal", status == 200 and outcome.get("status") == "payment_required" and bool(proposal_id), json.dumps(fetched)[:300])

            status, approved = _post(base, "/api/wallet/approve", {"proposal_id": proposal_id, "method": "external"})
            signing_request = approved.get("signing_request") or {}
            transports = (signing_request.get("transports") or {}).get("eip1193") or {}
            leg("owner-approval-hands-out-the-typed-data", status == 200 and bool(transports.get("params")), json.dumps(approved)[:300])
            if not transports.get("params"):
                verdict["ok"] = False
                print(json.dumps(verdict, indent=1))
                return 1

            signature = _sign_with(signer, json.loads(transports["params"][1]))
            status, submitted = _post(base, "/api/wallet/external/submit", {"request_id": signing_request["request_id"], "signature_hex": signature})
            receipt = submitted.get("receipt") or {}
            leg("one-submission-settles-chain-proven", status == 200 and receipt.get("state") == "confirmed", json.dumps(receipt)[:300])
            leg("the-server-saw-the-official-v1-wire", bool(resource.payment_headers) and resource.payment_headers[0].get("x402Version") == 1, json.dumps((resource.payment_headers or [{}])[0])[:200])
            leg("settlement-read-from-the-chain", "eth_getTransactionReceipt" in rpc.methods(), str(rpc.methods()[-6:]))

            # the honest adversarial denial: a USD offer on BNB testnet refuses typed
            from core.wallet import x402 as lane_x402  # noqa: F401  (rig import context)
            bnb_offer = {"x402Version": 1, "error": "X-PAYMENT required", "accepts": [{"scheme": "exact", "network": "eip155:97", "maxAmountRequired": "5000", "asset": "0x55d398326f99059ff775485246999027b3197955", "payTo": "0x" + "e" * 40, "resource": "https://example.test/bnb", "maxTimeoutSeconds": 60}]}
            status, refused = _post(base, "/api/wallet/x402/propose", {"wallet_id": wallets["wallet"]["wallet_id"], "status": 402, "headers": {}, "body": json.dumps(bnb_offer)})
            leg("bnb-testnet-usd-offer-refuses-typed", status in (400, 403, 502) and refused.get("error") in {"wallet_network_disabled", "x402_scheme_unavailable"}, json.dumps(refused)[:200])
        finally:
            provider.__exit__(None, None, None)
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            except Exception:
                process.terminate()
            try:
                process.wait(timeout=15)
            except Exception:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            log.close()
        leg("daemon-terminated-by-own-pgid", process.poll() is not None, str(process.returncode))

    verdict["ok"] = all(leg_entry["status"] == "PASS" for leg_entry in verdict["legs"])
    print(json.dumps(verdict, indent=1))
    return 0 if verdict["ok"] else 1


def _sign_with(signer, typed: dict) -> str:
    import http.client
    from urllib.parse import urlsplit

    parts = urlsplit(signer.url)
    conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=10)
    try:
        conn.request("POST", parts.path, body=json.dumps({"typed_data": typed}).encode(), headers={"Content-Type": "application/json"})
        answer = json.loads(conn.getresponse().read())
    finally:
        conn.close()
    if "signature" not in answer:
        raise SystemExit(f"the extension stand-in refused: {answer}")
    return answer["signature"]


if __name__ == "__main__":
    raise SystemExit(main())
