"""A hermetic x402 resource that settles the canonical Solana ``exact`` payment the way a pay-kit server does.

It answers 402 with a v2 ``PAYMENT-REQUIRED`` challenge naming its own fee payer. A request carrying
``PAYMENT-SIGNATURE`` is decoded, the payer's signature is checked over the transaction's own message, the fee payer
cosigns, and the settled bytes are handed to the scripted RPC so ``getTransaction`` returns them: the wallet's chain
proof reads exactly what this resource "landed". Nothing here reaches a network beyond loopback, and the only keys
are throwaway test keys generated per resource.
"""
from __future__ import annotations

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from tests.wallet._rig import OTHER_DESTINATION, ScriptedRpc

DEVNET_CAIP2 = "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"
MAINNET_CAIP2 = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"
PAID_BODY = b'{"report": "quarterly numbers", "paid": true}'


class ScriptedPayKitResource:
    """``mode``: 'ok' (settle and deliver) | 'settle_fails' (the landed transaction failed) | 'no_settlement_header'
    (deliver without naming a transaction) | 'wrong_settlement' (name a transaction that does not exist) |
    'names_earlier_payment' (land this payment but name the FIRST one this resource landed: a real, successful
    transaction carrying the same payer's signature, over another message)."""

    def __init__(self, rpc: ScriptedRpc, *, amount_minor: int = 1500, asset: str = "SOL", pay_to: str = OTHER_DESTINATION,
                 network: str = DEVNET_CAIP2, fee_payer: bool = True, wire_version: int = 2, mode: str = "ok") -> None:
        from solders.keypair import Keypair

        self.rpc = rpc
        self.amount_minor = amount_minor
        self.asset = asset
        self.pay_to = pay_to
        self.network = network
        self.with_fee_payer = fee_payer
        self.wire_version = wire_version
        self.mode = mode
        self.fee_payer = Keypair()
        self.requests: list[dict[str, Any]] = []
        self.paid_requests: list[dict[str, Any]] = []
        self.landed: list[str] = []
        self._lock = threading.Lock()
        resource = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: Any) -> None:
                return

            def _handle(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                payment = self.headers.get("PAYMENT-SIGNATURE") or self.headers.get("X-PAYMENT") or ""
                record = {"method": self.command, "path": self.path, "body": body, "content_type": self.headers.get("Content-Type") or "",
                          "authorization": self.headers.get("Authorization") or "", "cookie": self.headers.get("Cookie") or "", "paid": bool(payment)}
                with resource._lock:
                    resource.requests.append(record)
                if not payment:
                    challenge = resource.challenge()
                    encoded = base64.b64encode(json.dumps(challenge).encode()).decode()
                    self._send(402, json.dumps(challenge).encode(), {"Content-Type": "application/json", "PAYMENT-REQUIRED": encoded})
                    return
                settled = resource.settle(payment)
                with resource._lock:
                    resource.paid_requests.append({**record, "settled": settled})
                headers = {"Content-Type": "application/json"}
                if settled and resource.mode != "no_settlement_header":
                    named = {"wrong_settlement": "1" * 88, "names_earlier_payment": resource.landed[0]}.get(resource.mode, settled)
                    answer = {"success": True, "transaction": named, "network": resource.network, "payer": ""}
                    headers["PAYMENT-RESPONSE"] = base64.b64encode(json.dumps(answer).encode()).decode()
                self._send(200 if settled else 402, PAID_BODY if settled else b'{"error": "payment invalid"}', headers)

            do_GET = _handle  # noqa: N815 - http.server dispatches by these names
            do_POST = _handle  # noqa: N815
            do_PUT = _handle  # noqa: N815

            def _send(self, status: int, body: bytes, headers: dict[str, str]) -> None:
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def challenge(self) -> dict[str, Any]:
        offer: dict[str, Any] = {"scheme": "exact", "network": self.network, "amount": str(self.amount_minor), "asset": self.asset,
                                 "payTo": self.pay_to, "maxTimeoutSeconds": 60, "extra": {}}
        if self.with_fee_payer:
            offer["extra"]["feePayer"] = str(self.fee_payer.pubkey())
        return {"x402Version": self.wire_version, "resource": {"url": self.url}, "accepts": [offer]}

    def settle(self, header: str) -> str:
        """Verify the payer's signature over the transaction's message, cosign as fee payer, land it. Returns the
        transaction id, or '' when the payment does not verify."""
        from solders.message import to_bytes_versioned
        from solders.transaction import VersionedTransaction

        try:
            envelope = json.loads(base64.b64decode(header))
            raw = base64.b64decode(envelope["payload"]["transaction"])
            tx = VersionedTransaction.from_bytes(raw)
        except Exception:
            return ""
        message = tx.message
        signed_bytes = bytes(to_bytes_versioned(message))
        keys = list(message.account_keys)
        if not keys or keys[0] != self.fee_payer.pubkey():
            return ""
        required = int(message.header.num_required_signatures)
        signatures = list(tx.signatures)
        for index in range(1, required):
            if not signatures[index].verify(keys[index], signed_bytes):
                return ""
        signatures[0] = self.fee_payer.sign_message(signed_bytes)
        landed = VersionedTransaction.populate(message, signatures)
        tx_id = str(signatures[0])
        self.rpc.transactions[tx_id] = bytes(landed)
        self.landed.append(tx_id)
        if self.mode == "settle_fails":
            self.rpc.failed_transactions.add(tx_id)
        return tx_id

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}/paid/report"

    def __enter__(self) -> ScriptedPayKitResource:
        self._thread.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self._server.shutdown()
        self._server.server_close()
