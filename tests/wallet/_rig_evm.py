"""Hermetic EVM/x402-v2 rigs: a scripted JSON-RPC chain, an official-shape x402 v2 facilitator, a paid
v2 resource, and an EIP-1193-style signing stand-in. No real chain — the only "funds" are integers here."""
from __future__ import annotations

import base64
import hashlib
import json
import threading
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

TRANSFER_TOPIC0 = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


def pad_address_topic(address: str) -> str:
    """An ERC-20 log topic carrying an address: the 20-byte address left-padded to 32 bytes."""
    return "0x" + str(address or "").removeprefix("0x").lower().rjust(64, "0")


def _send_raw(handler: BaseHTTPRequestHandler, status: int, body: bytes, headers: dict[str, str]) -> None:  # the one wire detail every rig shares
    handler.send_response(status)
    for key, value in headers.items():
        handler.send_header(key, value)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _send_json(handler: BaseHTTPRequestHandler, payload: dict[str, Any], status: int = 200) -> None:
    _send_raw(handler, status, json.dumps(payload).encode(), {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"})


class ScriptedEvmRpc:
    """A loopback EVM-dialect JSON-RPC endpoint that records every call and answers eth_chainId /
    eth_blockNumber / eth_getTransactionReceipt from a seeded receipt table. `lie_chain_id` makes it
    answer the WRONG chain id so mismatch sabotage is testable; unknown methods get result null."""

    def __init__(self, *, chain_id: int = 84532, lie_chain_id: int | None = None) -> None:
        self.chain_id = chain_id
        self.lie_chain_id = lie_chain_id
        self.receipts: dict[str, dict[str, Any]] = {}
        self.calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        rpc = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: Any) -> None:  # silence
                return

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                method = str(body.get("method") or "")
                params = body.get("params") or []
                with rpc._lock:
                    rpc.calls.append({"method": method, "params": params})
                if method == "eth_chainId":
                    result: Any = hex(rpc.lie_chain_id if rpc.lie_chain_id is not None else rpc.chain_id)
                elif method == "eth_getTransactionReceipt":
                    tx_hash = str(params[0]) if params else ""
                    with rpc._lock:
                        result = rpc.receipts.get(tx_hash) or rpc.receipts.get(tx_hash.lower())
                else:
                    result: Any = "0x1" if method == "eth_blockNumber" else None
                _send_json(self, {"jsonrpc": "2.0", "id": body.get("id"), "result": result})

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def add_transfer_receipt(self, tx_hash: str, *, contract_address: str, from_address: str, to_address: str, amount_int: int, log_index: int = 0) -> dict[str, Any]:
        """Seed an ERC-20 Transfer-shaped success receipt so a settlement lookup finds real logs."""
        log = {"address": contract_address, "topics": [TRANSFER_TOPIC0, pad_address_topic(from_address), pad_address_topic(to_address)], "data": "0x" + format(amount_int, "064x"), "logIndex": hex(log_index), "removed": False}
        receipt = {"transactionHash": tx_hash, "status": "0x1", "blockNumber": "0x1", "logs": [log]}
        with self._lock:
            self.receipts[tx_hash] = receipt
            return receipt

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def call_count(self) -> int:
        with self._lock:
            return len(self.calls)

    def methods(self) -> list[str]:
        with self._lock:
            return [str(call["method"]) for call in self.calls]

    def __enter__(self) -> ScriptedEvmRpc:
        self._thread.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self._server.shutdown()
        self._server.server_close()


class FacilitatorSimulator:
    """A loopback x402 v2 facilitator speaking the official shapes: GET /supported, POST /verify,
    POST /settle. Flags make each stage lie (verify/settle failure, wrong tx prefix, malformed
    /supported body) so the client's validation paths run hermetically."""

    def __init__(self, *, networks: tuple[str, ...] = ("eip155:84532",), schemes: tuple[str, ...] = ("exact",), verify_ok: bool = True, settle_ok: bool = True, settlement_tx_prefix: str = "0x", supported_shapes_ok: bool = False) -> None:
        self.networks = networks
        self.schemes = schemes
        self.verify_ok = verify_ok
        self.settle_ok = settle_ok
        self.settlement_tx_prefix = settlement_tx_prefix
        self.supported_shapes_ok = supported_shapes_ok
        self.verify_requests: list[dict[str, Any]] = []
        self.settle_requests: list[dict[str, Any]] = []
        self.settled: list[str] = []
        self._lock = threading.Lock()
        facilitator = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: Any) -> None:
                return

            def do_GET(self) -> None:
                if not self.path.startswith("/supported"):
                    return _send_json(self, {"error": "not_found"}, 404)
                if facilitator.supported_shapes_ok:
                    return _send_json(self, {"kinds": "not-a-list"})
                kinds = [{"x402Version": 2, "scheme": scheme, "network": network} for network in facilitator.networks for scheme in facilitator.schemes]
                return _send_json(self, {"kinds": kinds, "extensions": [], "signers": {}})

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                requirements = body.get("paymentRequirements") or {}
                authorization = ((body.get("paymentPayload") or {}).get("payload") or {}).get("authorization") or {}
                payer = str(authorization.get("from") or "")
                if self.path.startswith("/verify"):
                    with facilitator._lock:
                        facilitator.verify_requests.append(body)
                    valid = bool(facilitator.verify_ok) and body.get("x402Version") in (1, 2)
                    reason = "" if valid else ("insufficient_funds" if body.get("x402Version") in (1, 2) else "unsupported_x402_version")
                    return _send_json(self, {"isValid": valid, "invalidReason": reason, "payer": payer})
                if self.path.startswith("/settle"):
                    tx = facilitator.settlement_tx_prefix + hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
                    with facilitator._lock:
                        facilitator.settle_requests.append(body)
                        if facilitator.settle_ok:
                            facilitator.settled.append(tx)
                    if not facilitator.settle_ok:
                        return _send_json(self, {"success": False, "errorReason": "simulation_failed", "transaction": "", "network": requirements.get("network")})
                    return _send_json(self, {"success": True, "errorReason": "", "payer": payer, "transaction": tx, "network": requirements.get("network")})
                return _send_json(self, {"error": "not_found"}, 404)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def verify_count(self) -> int:
        with self._lock:
            return len(self.verify_requests)

    def settle_count(self) -> int:
        with self._lock:
            return len(self.settle_requests)

    def __enter__(self) -> FacilitatorSimulator:
        self._thread.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self._server.shutdown()
        self._server.server_close()


class X402V2Resource:
    """A loopback paid resource speaking the OFFICIAL x402 v2 headers: a 402 carrying `PAYMENT-REQUIRED`
    (base64 JSON PaymentRequired, mirrored in the body) and a paid 200 carrying `PAYMENT-RESPONSE`.
    Payment counts as settled only when the payload's signature is in `settled_signatures` (the TEST
    adds it after driving the facilitator settle, standing in for server-side settlement); `refuse_next`
    makes the next settled delivery answer 500."""

    def __init__(self, facilitator: FacilitatorSimulator, *, network: str = "eip155:84532", asset: str = "0x036CbD53842c5426634e7929541eC2318f3dCF7e", pay_to: str = "0x209693Bc6afc0C5328bA36FaF03C514EF312287C", amount_minor: int = 10000, eip712_name: str = "USDC", eip712_version: str = "2", method: str = "GET", rpc: ScriptedEvmRpc | None = None) -> None:
        self.facilitator = facilitator
        self.network = network
        self.asset = asset
        self.pay_to = pay_to
        self.amount_minor = amount_minor
        self.eip712_name = eip712_name
        self.eip712_version = eip712_version
        self.method = method
        self.rpc = rpc
        self.settled_signatures: set[str] = set()
        self.settlement_tx = ""
        self.refuse_next = False
        self.deliveries: list[dict[str, Any]] = []
        self.settlements: list[dict[str, Any]] = []
        self.last_settle_error = ""
        self.challenges = 0
        self._lock = threading.Lock()
        resource = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: Any) -> None:
                return

            def do_GET(self) -> None:
                self._handle()

            do_POST = do_GET  # noqa: N815 - the configured method is enforced client-side

            def _handle(self) -> None:
                header = self.headers.get("PAYMENT-SIGNATURE") or ""
                if not header:
                    return self._challenge()
                try:
                    payload = json.loads(base64.b64decode(header))
                except Exception:
                    payload = {}
                inner = payload.get("payload") or {}
                signature = str(inner.get("signature") or "")
                payer = str((inner.get("authorization") or {}).get("from") or "")
                network = str(payload.get("network") or (payload.get("accepted") or {}).get("network") or resource.network)
                with resource._lock:
                    settled = signature in resource.settled_signatures
                    refuse = resource.refuse_next
                if not settled:
                    # A real server settles an unseen payment through the facilitator
                    # (POST /verify, then POST /settle) before serving the resource.
                    settled = resource._settle_via_facilitator(payload, signature, payer)
                if not settled:
                    return self._challenge()
                if refuse:
                    with resource._lock:
                        resource.refuse_next = False
                    return _send_raw(self, 500, b"delivery failed", {"Content-Type": "text/plain"})
                with resource._lock:
                    resource.deliveries.append({"path": self.path, "network": network, "from": payer, "signature": signature})
                    settlement_tx = next((st["tx"] for st in reversed(resource.settlements) if st["signature"] == signature), resource.settlement_tx)
                response = {"success": True, "errorReason": "", "payer": payer, "transaction": settlement_tx, "network": network}
                headers = {"PAYMENT-RESPONSE": base64.b64encode(json.dumps(response).encode()).decode(), "Content-Type": "text/plain"}
                _send_raw(self, 200, b"PAID V2 REPORT: quarterly numbers", headers)

            def _challenge(self) -> None:
                with resource._lock:
                    resource.challenges += 1
                body = json.dumps(resource.payment_required()).encode()
                _send_raw(self, 402, body, {"PAYMENT-REQUIRED": base64.b64encode(body).decode(), "Content-Type": "application/json"})

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def _settle_via_facilitator(self, payload: dict[str, Any], signature: str, payer: str) -> bool:
        """The server-side settlement a real resource performs: verify, settle, and (when a
        scripted EVM RPC is attached) seed the chain receipt the wallet's independent
        verification will read. Returns whether the facilitator settled."""
        import urllib.request

        facilitator_origin = str(self.facilitator.url or "").rstrip("/")
        requirements = {
            "scheme": "exact", "network": self.network, "amount": str(self.amount_minor),
            "asset": self.asset, "payTo": self.pay_to, "maxTimeoutSeconds": 60,
            "extra": {"name": self.eip712_name, "version": self.eip712_version, "assetTransferMethod": "eip3009"},
        }
        body = json.dumps({"x402Version": 2, "paymentPayload": payload, "paymentRequirements": requirements}).encode()
        try:
            verify = urllib.request.Request(f"{facilitator_origin}/verify", data=body, headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(verify, timeout=10) as response:
                verdict = json.loads(response.read() or b"{}")
            if not verdict.get("isValid"):
                return False
            settle = urllib.request.Request(f"{facilitator_origin}/settle", data=body, headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(settle, timeout=10) as response:
                result = json.loads(response.read() or b"{}")
            tx = str(result.get("transaction") or "")
            if not result.get("success") or not tx:
                return False
        except Exception as exc:
            with self._lock:
                self.last_settle_error = f"{type(exc).__name__}: {exc}"[:200]
            return False
        with self._lock:
            self.settled_signatures.add(signature)
            self.settlements.append({"tx": tx, "signature": signature, "payer": payer})
        if self.rpc is not None:
            self.rpc.add_transfer_receipt(
                tx, contract_address=self.asset, from_address=payer or ("0x" + "0" * 40),
                to_address=self.pay_to, amount_int=self.amount_minor,
            )
        return True

    def payment_required(self) -> dict[str, Any]:
        """The official v2 PaymentRequired shape the 402 carries in its header and its body."""
        accept = {"scheme": "exact", "network": self.network, "amount": str(self.amount_minor), "asset": self.asset, "payTo": self.pay_to, "maxTimeoutSeconds": 60, "extra": {"name": self.eip712_name, "version": self.eip712_version, "assetTransferMethod": "eip3009"}}
        return {"x402Version": 2, "error": "PAYMENT-SIGNATURE header is required", "resource": {"url": self.url, "description": "one paid report", "mimeType": "text/plain"}, "accepts": [accept], "extensions": {}}

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}/paid/v2/report"

    def __enter__(self) -> X402V2Resource:
        self._thread.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self._server.shutdown()
        self._server.server_close()


class EvmExtensionSigner:
    """Stands in for an external EIP-1193 wallet's OWN process: an ephemeral secp256k1 key the page never
    sees, receiving the full EIP-712 typed data over HTTP and answering with a signature from the production
    stand-in helper. Sabotage flags: `tamper` (signs a different message.value), `cancel`, `wrong_chain`."""

    def __init__(self) -> None:
        from eth_account import Account

        account = Account.create()
        self.private_key = account.key  # expose .hex() for tests registering the address
        self._private_key_hex = "0x" + account.key.hex()
        self.address = account.address
        self.tamper = False
        self.cancel = False
        self.wrong_chain: int | None = None
        self.signed: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        signer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: Any) -> None:
                return

            def do_OPTIONS(self) -> None:  # a browser page fetches this cross-origin
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
                self.end_headers()

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                if signer.cancel:
                    self.send_response(400)
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"error": "user rejected"}')
                    return
                typed = dict(body.get("typed_data") or {})
                if signer.tamper:
                    try:
                        value = int((typed.get("message") or {}).get("value") or 0)
                    except (TypeError, ValueError):
                        value = 0
                    typed.setdefault("message", {})["value"] = str(value + 1)
                if signer.wrong_chain is not None:
                    typed.setdefault("domain", {})["chainId"] = signer.wrong_chain
                from core.wallet.evm import sign_authorization  # lazy: production helper, stand-in use only

                signature = sign_authorization(signer._private_key_hex, typed)
                with signer._lock:
                    signer.signed.append(typed)
                return _send_json(self, {"signature": signature, "address": signer.address, "typed_data": typed})

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}/sign"

    def __enter__(self) -> EvmExtensionSigner:
        self._thread.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self._server.shutdown()
        self._server.server_close()


def fake_eip1193_init_script(signer_url: str, address: str, *, chain_id: int = 84532) -> str:
    """An injected EIP-1193 provider with the documented surface: eth_requestAccounts,
    eth_chainId and eth_signTypedData_v4. Signing happens in the 'extension' stand-in over
    HTTP — the page never sees key material — and every call is recorded in
    window.__fakeEvmCalls for the served assertions."""
    import json as _json

    return (
        "window.__fakeEvmCalls = [];"
        "window.ethereum = { isMetaMask: true,"
        f"  chainId: {_json.dumps(hex(chain_id))},"
        f"  selectedAddress: {_json.dumps(address.lower())},"
        "  request: async function(args){"
        "    window.__fakeEvmCalls.push(args);"
        "    if (!args || typeof args.method !== 'string') throw new Error('bad request args');"
        "    if (args.method === 'eth_requestAccounts' || args.method === 'eth_accounts')"
        f"      return [{_json.dumps(address)}];"
        "    if (args.method === 'eth_chainId') return window.ethereum.chainId;"
        "    if (args.method === 'wallet_switchEthereumChain') { window.ethereum.chainId = args.params && args.params[0] && args.params[0].chainId || window.ethereum.chainId; return null; }"
        "    if (args.method === 'eth_signTypedData_v4'){"
        "      var addressParam = args.params && args.params[0];"
        "      var typedParam = args.params && args.params[1];"
        "      var typed = typeof typedParam === 'string' ? JSON.parse(typedParam) : typedParam;"
        f"      const r = await fetch({_json.dumps(signer_url)}, {{ method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify({{ address: addressParam, typed_data: typed }}) }});"
        "      var answer = await r.json();"
        "      if (!answer || !answer.signature) throw new Error(answer && answer.error ? answer.error : 'user rejected');"
        "      return answer.signature;"
        "    }"
        "    throw new Error('unsupported method ' + args.method);"
        "  } };"
    )


@pytest.fixture
def evm_rig():
    """One ScriptedEvmRpc + one FacilitatorSimulator, started together."""
    with ScriptedEvmRpc() as rpc, FacilitatorSimulator() as facilitator:
        yield types.SimpleNamespace(rpc=rpc, facilitator=facilitator)
