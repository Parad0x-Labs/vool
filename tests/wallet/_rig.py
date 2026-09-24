"""Hermetic helpers shared by the wallet pack: a scripted testnet RPC and a fixed clock.

Nothing here touches a real chain. The RPC stub speaks the exact JSON-RPC methods the
runtime's testnet broadcaster uses, so a proof against it exercises the real wire shape
while the only "funds" are integers in this process.
"""
from __future__ import annotations

import base64
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

DEVNET = "solana-devnet"
# A valid 32-byte base58 pubkey (the system program id) usable as a destination in tests.
DESTINATION = "11111111111111111111111111111111"
OTHER_DESTINATION = "Vote111111111111111111111111111111111111111"
#: The public cluster genesis hashes (getGenesisHash), verified live on 2026-09-14.
DEVNET_GENESIS = "EtWTRABZaYq6iMfeYKouRu166VU2xqa1wcaWoxPkrZBG"
MAINNET_GENESIS = "5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d"


class ScriptedRpc:
    """A loopback Solana-dialect JSON-RPC endpoint that records every sendTransaction.

    It answers as one cluster: ``genesis_hash`` is what ``getGenesisHash`` returns, so a proof can
    point a devnet row at an endpoint that claims to be mainnet (and the reverse)."""

    def __init__(self, *, simulate_ok: bool = True, send_ok: bool = True, genesis_hash: str = DEVNET_GENESIS) -> None:
        self.simulate_ok = simulate_ok
        self.send_ok = send_ok
        self.genesis_hash = genesis_hash
        #: chain state a quote and a reconciler read; the defaults answer exactly as this rig always did
        self.default_balance = 5_000_000_000
        self.balances: dict[str, int] = {}
        self.accounts: dict[str, dict[str, Any]] = {}
        self.fee_for_message: int | None = 5_000
        self.rent_minimum = 650_240
        self.slot = 1
        self.block_height = 50
        self.finalized_block_height = 50
        self.last_valid_block_height = 100
        self.minimum_ledger_slot = 0
        #: getSignatureStatuses answer: 'confirmed' | 'finalized' | 'processed' | 'none' (not found) | 'err'
        self.status_mode = "confirmed"
        self.status_context_slot = 2
        self.transaction_fee = 5_000
        #: sendTransaction answer after the transaction has been RECORDED (the node executed the call):
        #: 'ok' | 'reject' (validated JSON-RPC preflight error) | 'accept_then_500' | 'accept_then_malformed' |
        #: 'accept_then_wrong_id' | 'accept_then_unlisted_error'. `send_ok=False` is the legacy spelling of 'reject'.
        self.send_mode = "ok"
        #: answer the transaction's real first signature (base58) rather than the legacy synthetic id
        self.real_signature = False
        self.finalized_slot = 1
        #: the next N getSignatureStatuses answers hide an inclusion (delayed indexing, a lagging node)
        self.status_hidden_reads = 0
        #: when True, statuses and transactions exist only for recorded bytes or ``landed`` signatures
        self.status_keyed = False
        self.landed: set[str] = set()
        self.preflight_err: object = "InsufficientFundsForFee"
        self.calls: list[dict[str, Any]] = []
        self.sent: list[bytes] = []
        self.returned: list[str] = []
        self._lock = threading.Lock()
        rpc = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: Any) -> None:  # silence
                return

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                with rpc._lock:
                    rpc.calls.append(body)
                method = body.get("method")
                params = body.get("params") or []
                if method == "getLatestBlockhash":
                    result: Any = {"context": {"slot": rpc.slot}, "value": {"blockhash": "4vJ9JU1bJJE96FWSJKvHsmmFADCg4gpZQff4P3bkLKi", "lastValidBlockHeight": rpc.last_valid_block_height}}
                elif method == "getFeeForMessage":
                    result = {"context": {"slot": rpc.slot}, "value": rpc.fee_for_message}
                elif method == "getMinimumBalanceForRentExemption":
                    result = rpc.rent_minimum
                elif method == "getSlot":
                    commitment = str(((params[0] if params and isinstance(params[0], dict) else {}) or {}).get("commitment") or "")
                    result = rpc.finalized_slot if commitment == "finalized" else rpc.slot
                elif method == "getBlockHeight":
                    commitment = str(((params[0] if params and isinstance(params[0], dict) else {}) or {}).get("commitment") or "")
                    result = rpc.finalized_block_height if commitment == "finalized" else rpc.block_height
                elif method == "minimumLedgerSlot":
                    result = rpc.minimum_ledger_slot
                elif method == "getAccountInfo":
                    key = str(params[0]) if params else ""
                    info = rpc.accounts.get(key)
                    if info is None and rpc.balances.get(key, rpc.default_balance if not rpc.balances else 0) > 0:
                        info = {"executable": False, "owner": "11111111111111111111111111111111"}
                    result = {"context": {"slot": rpc.slot}, "value": None if info is None else {"lamports": rpc.balances.get(key, rpc.default_balance), "executable": bool(info.get("executable")), "owner": str(info.get("owner") or ""), "data": ["", "base64"], "rentEpoch": 0, "space": 0}}
                elif method == "getTransaction":
                    asked_sig = str(params[0]) if params else ""
                    options = params[1] if len(params) > 1 and isinstance(params[1], dict) else {}
                    unknown_sig = rpc.status_keyed and asked_sig not in (rpc.recorded_signatures() | set(rpc.landed))
                    not_final_yet = str(options.get("commitment") or "finalized") == "finalized" and rpc.finalized_slot < rpc.status_context_slot
                    if rpc.status_mode in {"none", "processed"} or unknown_sig or not_final_yet:
                        result = None
                    else:
                        result = {"slot": rpc.status_context_slot, "meta": {"fee": rpc.transaction_fee, "err": {"InstructionError": [0, "Custom"]} if rpc.status_mode == "err" else None}}
                elif method == "simulateTransaction":
                    result = {"context": {"slot": 1}, "value": {"err": None if rpc.simulate_ok else {"InsufficientFundsForFee": {}}, "logs": [], "unitsConsumed": 150}}
                elif method == "sendTransaction":
                    raw = base64.b64decode(params[0])
                    mode = "reject" if not rpc.send_ok else rpc.send_mode
                    if mode in {"blockhash_not_found", "preflight_error"}:
                        # a validated preflight refusal: the node simulated, refused and recorded nothing
                        err = "BlockhashNotFound" if mode == "blockhash_not_found" else rpc.preflight_err
                        self._send({"jsonrpc": "2.0", "id": body.get("id"), "error": {"code": -32002, "message": "Transaction simulation failed", "data": {"err": err, "logs": []}}})
                        return
                    with rpc._lock:
                        rpc.sent.append(raw)
                    if mode == "already_processed":
                        # recorded above: the node executed it, then answered the validated duplicate error
                        self._send({"jsonrpc": "2.0", "id": body.get("id"), "error": {"code": -32002, "message": "Transaction simulation failed: This transaction has already been processed", "data": {"err": "AlreadyProcessed", "logs": []}}})
                        return
                    if mode == "lie_signature":
                        self._send({"jsonrpc": "2.0", "id": body.get("id"), "result": "4" * 87})
                        return
                    if mode == "reject":
                        payload = {"jsonrpc": "2.0", "id": body.get("id"), "error": {"code": -32002, "message": "Transaction simulation failed"}}
                        self._send(payload)
                        return
                    if mode == "accept_then_500":
                        # recorded above; a proxy in front of the node answers 500 with no JSON-RPC body
                        self._send_raw(500, b"upstream error", "text/plain")
                        return
                    if mode == "accept_then_malformed":
                        self._send_raw(200, b'{"jsonrpc": "2.0", "id": ', "application/json")
                        return
                    if mode == "accept_then_wrong_id":
                        self._send({"jsonrpc": "2.0", "id": 999999, "error": {"code": -32002, "message": "Transaction simulation failed"}})
                        return
                    if mode == "accept_then_unlisted_error":
                        self._send({"jsonrpc": "2.0", "id": body.get("id"), "error": {"code": -32005, "message": "Node is unhealthy"}})
                        return
                    if rpc.real_signature:
                        from solders.transaction import Transaction

                        result = str(Transaction.from_bytes(raw).signatures[0])
                    else:
                        result = "5" + base64.b32encode(raw[:40]).decode().rstrip("=").lower().replace("0", "a")[:86]
                    with rpc._lock:
                        rpc.returned.append(result)
                elif method == "getSignatureStatuses":
                    mode = rpc.status_mode
                    with rpc._lock:
                        if rpc.status_hidden_reads > 0:
                            rpc.status_hidden_reads -= 1
                            mode = "none"
                    entry: Any = None
                    if mode != "none":
                        entry = {"slot": 2, "confirmations": None if mode == "finalized" else 5, "err": {"InstructionError": [0, "Custom"]} if mode == "err" else None,
                                 "confirmationStatus": {"err": "confirmed"}.get(mode, mode)}
                    asked = [str(sig) for sig in (params[0] if params else [])]
                    if rpc.status_keyed:
                        known = rpc.recorded_signatures() | set(rpc.landed)
                        result = {"context": {"slot": rpc.status_context_slot}, "value": [entry if sig in known else None for sig in asked]}
                    else:
                        result = {"context": {"slot": rpc.status_context_slot}, "value": [entry for _sig in asked] or [entry]}
                elif method == "getBalance":
                    key = str(params[0]) if params else ""
                    result = {"context": {"slot": rpc.slot}, "value": rpc.balances.get(key, rpc.default_balance)}
                elif method == "getGenesisHash":
                    result = rpc.genesis_hash
                else:
                    result = None
                self._send({"jsonrpc": "2.0", "id": body.get("id"), "result": result})

            def _send(self, payload: dict[str, Any]) -> None:
                self._send_raw(200, json.dumps(payload).encode(), "application/json")

            def _send_raw(self, status: int, data: bytes, content_type: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def recorded_signatures(self) -> set[str]:
        """The real first signatures of every transaction the node recorded (bytes it actually received)."""
        from solders.transaction import Transaction

        with self._lock:
            raws = list(self.sent)
        return {str(Transaction.from_bytes(raw).signatures[0]) for raw in raws}

    def send_count(self) -> int:
        with self._lock:
            return len(self.sent)

    def distinct_transactions(self) -> int:
        """How many DIFFERENT signed transactions were transmitted (retransmissions of identical bytes count once)."""
        with self._lock:
            return len({bytes(raw) for raw in self.sent})

    def signatures(self) -> list[str]:
        with self._lock:
            return list(self.returned)

    def __enter__(self) -> ScriptedRpc:
        self._thread.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def rpc():
    with ScriptedRpc() as stub:
        yield stub


@pytest.fixture
def wallet_env(monkeypatch, rpc, tmp_path):
    """Wallet enabled on Test networks, pointed at the scripted testnet RPC, blackbox isolated, clock fixed.

    Crypto Pilot: a fresh installation operates on Mainnet, so the testnet lanes this fixture serves select
    Test networks explicitly through the operator override (status reports source operator_override)."""
    monkeypatch.setenv("VOOL_WALLET_ENABLED", "1")
    monkeypatch.setenv("VOOL_WALLET_NETWORK_ENVIRONMENT", "testnet")
    monkeypatch.setenv("VOOL_WALLET_TESTNET_RPC_URL", rpc.url)
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    from core.blackbox import store as store_module

    store_module.reset_default_store()
    yield {"rpc": rpc, "blackbox": tmp_path / "blackbox"}
    store_module.reset_default_store()


def x402_body(*, amount_minor: int, asset: str = "SOL", network: str = DEVNET, pay_to: str = DESTINATION) -> dict[str, Any]:
    """The 402 body shape (x402 `accepts[]`) the detector must understand."""
    return {
        "x402Version": 1,
        "error": "X-PAYMENT header is required",
        "accepts": [
            {
                "scheme": "exact",
                "network": network,
                "maxAmountRequired": str(amount_minor),
                "asset": asset,
                "payTo": pay_to,
                "resource": "https://example.test/paid/report",
                "description": "one paid report",
                "maxTimeoutSeconds": 60,
            }
        ],
    }


def fault_codes(limit: int = 50) -> list[str]:
    from core.faults.recorder import list_faults

    return [getattr(f, "code", None) or f["code"] for f in list_faults(limit=limit)]


def sec_codes(limit: int = 50) -> list[str]:
    from core.security_events.store import list_security_events

    return [getattr(e, "sec_code", None) or e["sec_code"] for e in list_security_events(limit=limit)]


def fault_dumps(limit: int = 50) -> str:
    from core.faults.recorder import list_faults

    return json.dumps([f.to_dict() if hasattr(f, "to_dict") else f for f in list_faults(limit=limit)], default=str)


# --- amendment rigs ------------------------------------------------------------------------------

class ScriptedX402Resource:
    """A paid resource: 402 with an x402 offer until an X-PAYMENT header carries a signature
    the scripted RPC actually broadcast. Counts deliveries so a re-paid resource is visible."""

    def __init__(self, rpc: ScriptedRpc, *, amount_minor: int = 1500, asset: str = "SOL", pay_to: str = DESTINATION, port: int = 0) -> None:
        self.rpc = rpc
        self.amount_minor = amount_minor
        self.asset = asset
        self.pay_to = pay_to
        self.deliveries: list[dict[str, Any]] = []
        self.challenges = 0
        self._lock = threading.Lock()
        resource = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: Any) -> None:
                return

            def do_GET(self) -> None:
                header = self.headers.get("X-PAYMENT") or self.headers.get("X-Payment") or ""
                if header:
                    try:
                        payload = json.loads(base64.b64decode(header))
                    except Exception:
                        payload = {}
                    signature = str(((payload.get("payload") or {}).get("signature")) or "")
                    if signature and signature in resource.rpc.signatures():
                        body = b"PAID REPORT: quarterly numbers"
                        with resource._lock:
                            resource.deliveries.append({"signature": signature, "path": self.path, "network": payload.get("network")})
                        self._send(200, body, {"Content-Type": "text/plain"})
                        return
                with resource._lock:
                    resource.challenges += 1
                body = json.dumps(x402_body(amount_minor=resource.amount_minor, asset=resource.asset, pay_to=resource.pay_to)).encode()
                self._send(402, body, {"Content-Type": "application/json"})

            def _send(self, status: int, body: bytes, headers: dict[str, str]) -> None:
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._server = ThreadingHTTPServer(("127.0.0.1", int(port)), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}/paid/report"

    def __enter__(self) -> ScriptedX402Resource:
        self._thread.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self._server.shutdown()
        self._server.server_close()


class ExtensionSigner:
    """Stands in for the wallet extension's OWN process: it holds an ephemeral key the page never
    sees, receives a base58 message, and answers with a base58 signature (Phantom's
    `request({method:'signTransaction'})` result shape) or a WalletConnect-style signed tx."""

    def __init__(self) -> None:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        from core.vool_wallet import b58decode, b58encode

        self._key = Ed25519PrivateKey.generate()
        self.public_key = b58encode(self._key.public_key().public_bytes_raw())
        self.signed: list[bytes] = []
        self.tamper = False  # sabotage: sign a different message than the one asked for
        signer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: Any) -> None:
                return

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                message = b58decode(str(body.get("message") or ""))
                to_sign = (b"tampered" + message[8:]) if signer.tamper else message
                sig = signer._key.sign(to_sign)
                signer.signed.append(message)
                out = json.dumps({"signature": b58encode(sig), "publicKey": signer.public_key}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def do_OPTIONS(self) -> None:
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
                self.end_headers()

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def sign_message(self, message: bytes) -> bytes:
        self.signed.append(message)
        return self._key.sign(message)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}/sign"

    def __enter__(self) -> ExtensionSigner:
        self._thread.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self._server.shutdown()
        self._server.server_close()


def fake_phantom_init_script(signer_url: str, public_key: str) -> str:
    """An injected provider with Phantom's documented surface: connect(), publicKey, and
    request({method:'signTransaction', params:{message: <base58>}}) -> {signature, publicKey}.
    Signing happens in the extension stand-in over HTTP, never in the page."""
    return (
        "window.__fakePhantomCalls = [];"
        "window.phantom = { solana: { isPhantom: true, publicKey: null,"
        f"  connect: async function(){{ this.publicKey = {{ toString: function(){{ return {public_key!r}; }} }}; return {{ publicKey: this.publicKey }}; }},"
        "  request: async function(args){ window.__fakePhantomCalls.push(args);"
        "    if (!args || args.method !== 'signTransaction') throw new Error('unsupported method ' + (args && args.method));"
        f"    const r = await fetch({signer_url!r}, {{ method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify({{ message: args.params.message }}) }});"
        "    return await r.json(); } } };"
        "window.solana = window.phantom.solana;"
    )

# --- private-key leak detector ----------------------------------------------------------------------

def key_leaked(secret: str, text: str, *, window: int = 20) -> str | None:
    """Say why `text` carries the private-key backup `secret` (a base58 keypair or 0x-hex key), or None.

    A leak is the value in any common spelling (exact; hex with or without 0x, either case; base64 of the raw
    bytes) or any `window`-character run of it. The run check catches truncation, wrapping and chunked copies;
    20 characters of a 256-bit value do not occur by chance in ordinary text.
    """
    value = str(secret or "").strip()
    haystack = str(text or "")
    if not value:
        return None
    variants = {value}
    raw: bytes | None = None
    body = value[2:] if value.lower().startswith("0x") else value
    if len(body) in (64, 128) and all(ch in "0123456789abcdefABCDEF" for ch in body):
        variants |= {body.lower(), body.upper(), "0x" + body.lower(), "0x" + body.upper()}
        raw = bytes.fromhex(body)
    else:
        try:
            from core.vool_wallet import b58decode

            raw = b58decode(value)
        except Exception:
            raw = None
    if raw:
        variants.add(base64.b64encode(raw).decode("ascii"))
        variants.add(raw.hex())
    lowered = haystack.lower()
    for variant in variants:
        if variant and (variant in haystack or (variant.lower() in lowered and all(c in "0123456789abcdefx" for c in variant.lower()))):
            return f"secret present ({len(variant)} chars)"
    for spelling in variants:
        for start in range(0, max(0, len(spelling) - window) + 1, max(1, window // 2)):
            run = spelling[start:start + window]
            if len(run) == window and (run in haystack or (all(c in "0123456789abcdef" for c in run.lower()) and run.lower() in lowered)):
                return f"{window}-character run of the secret present"
    return None


# --- recovery-phrase leak detector -----------------------------------------------------------------

_LEAK_TOKEN_RE = re.compile(r"[a-z]+")


def phrase_leaked(phrase: str, text: str, *, run: int = 3, distinct: int = 6) -> str | None:
    """Say why `text` carries the recovery phrase, or None.

    A leak is the phrase itself (whitespace / punctuation / quoting normalized away), a run of `run` consecutive
    phrase words in phrase order, or `distinct` or more distinct phrase words present as whole tokens. One BIP-39
    word on its own is not a leak: most of the 2048 are ordinary English ("page", "ask", "state", "once"), so the
    earlier per-word membership checks turned red at a rate set by the random phrase draw, never by the runtime.
    Used for model prompts, rendered pages, JSON surfaces and database dumps alike.
    """
    words = [w for w in str(phrase or "").lower().split() if w]
    if not words:
        return None
    norm = " " + " ".join(_LEAK_TOKEN_RE.findall(str(text or "").lower())) + " "
    if " " + " ".join(words) + " " in norm:
        return "phrase verbatim"
    for i in range(0, max(0, len(words) - run + 1)):
        if " " + " ".join(words[i:i + run]) + " " in norm:
            return f"consecutive phrase words {i + 1}-{i + run}"
    tokens = set(norm.split())
    present = sorted(w for w in set(words) if w in tokens)
    if len(present) >= distinct:
        return f"{len(present)} distinct phrase words present: {present}"
    return None
