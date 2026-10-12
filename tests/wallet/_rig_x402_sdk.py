"""A hermetic EIP-3009 token and an x402 resource whose facilitator settles on it, for the x402 SDK lane.

``ScriptedUsdcChain`` is the scripted EVM node (``_rig_evm_native``) plus one EIP-3009 token: ``balanceOf``,
``authorizationState`` at a block, and ``transferWithAuthorization`` executed the way the token executes it (the
signature recovered over the token's own EIP-712 domain, the window checked against the block's timestamp, a used
nonce refused, the balance checked), emitting ``Transfer`` and ``AuthorizationUsed`` logs in a receipt the facilitator
pays the gas for. ``eth_getLogs`` answers those logs. Block timestamps follow the wall clock plus ``time_skip``, so a
window can be made to close (:meth:`ScriptedUsdcChain.advance`).

``ScriptedX402SdkResource`` answers 402 with a v2 ``PAYMENT-REQUIRED`` built with the x402 SDK's own encoder. A request
carrying ``PAYMENT-SIGNATURE`` is decoded with the SDK's decoder and settled on the chain by its facilitator, then
answered with the SDK-encoded ``PAYMENT-RESPONSE``. Nothing here reaches a network beyond loopback; the only keys are
the wallet's own, created per test.
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from tests.wallet._rig_evm_native import ScriptedEvmNativeChain

BASE_MAINNET = "eip155:8453"
BASE_SEPOLIA = "eip155:84532"
#: the registry's EIP-3009 tokens on the two Base rows (address, EIP-712 name, version)
USDC = {
    BASE_MAINNET: ("0x833589fcd6edb6e08f4c7c32d4f71b54bda02913", "USD Coin", "2"),
    BASE_SEPOLIA: ("0x036cbd53842c5426634e7929541ec2318f3dcf7e", "USDC", "2"),
}
PAID_BODY = b'{"report": "base numbers", "paid": true}'
TRANSFER_TOPIC0 = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
AUTHORIZATION_USED_TOPIC0 = "0x98de503528ee59b575ef0c0a2576a82497bfc029a5685b209e9ec333479b10a5"
_SELECTOR_BALANCE_OF = "0x70a08231"
_SELECTOR_AUTHORIZATION_STATE = "0xe94a0102"
_BLOCK_SECONDS = 2
_TYPES = [
    {"name": "from", "type": "address"}, {"name": "to", "type": "address"}, {"name": "value", "type": "uint256"},
    {"name": "validAfter", "type": "uint256"}, {"name": "validBefore", "type": "uint256"}, {"name": "nonce", "type": "bytes32"},
]


def _word(address: str) -> str:
    return "0x" + str(address).lower().removeprefix("0x").rjust(64, "0")


class ScriptedUsdcChain(ScriptedEvmNativeChain):
    """The scripted Base node with its USDC. ``token_balances`` maps lower-case owners to minor units."""

    def __init__(self, *, network: str = BASE_SEPOLIA, **kwargs: Any) -> None:
        chain_id = int(network.split(":")[1])
        super().__init__(chain_id=chain_id, fee_model="op_stack", **kwargs)
        self.network = network
        self.token, self.eip712_name, self.eip712_version = USDC[network]
        self.token_balances: dict[str, int] = {}
        #: (authorizer, nonce hex) -> the block the authorization was used in
        self.used: dict[tuple[str, str], int] = {}
        self.logs: list[dict[str, Any]] = []
        #: transactions the facilitator landed, in order
        self.facilitated: list[str] = []
        self.time_skip = 0
        self._genesis_time = int(time.time()) - self.block_number * _BLOCK_SECONDS
        #: when set, eth_getLogs over more blocks than this is refused, as public endpoints refuse wide ranges
        self.max_log_span: int | None = None

    # -- scripting -------------------------------------------------------------------------------------------
    def fund_token(self, owner: str, minor: int) -> None:
        with self._lock:
            self.token_balances[owner.lower()] = int(minor)

    def token_balance(self, owner: str) -> int:
        with self._lock:
            return int(self.token_balances.get(owner.lower(), 0))

    def advance(self, seconds: int) -> None:
        """The chain moves on ``seconds``: empty blocks are produced, the clock with them."""
        with self._lock:
            self.time_skip += int(seconds)
            self.block_number = max(self.block_number, self._number_at(self._now()))

    def _now(self) -> int:
        return int(time.time()) + self.time_skip

    def _number_at(self, moment: int) -> int:
        return (moment - self._genesis_time) // _BLOCK_SECONDS

    def timestamp_of(self, number: int) -> int:
        """Block ``number``'s timestamp: the latest block is now (wall clock plus the skip), each earlier one 2 s before."""
        with self._lock:
            latest = max(self.block_number, self._number_at(self._now()))
            return self._now() - (latest - int(number)) * _BLOCK_SECONDS

    def _tagged(self, tag: str) -> int:
        if tag == "safe" and self.safe_block is not None:
            return int(self.safe_block)
        if tag == "finalized" and self.finalized_block is not None:
            return int(self.finalized_block)
        if tag.startswith("0x"):
            return int(tag, 16)
        return self.block_number

    # -- the token -------------------------------------------------------------------------------------------
    def execute_authorization(self, authorization: dict[str, Any], signature: str, *, submitter: str = "0x" + "fa" * 20) -> tuple[str, str]:
        """``transferWithAuthorization`` as the token runs it, submitted by the facilitator. Returns (tx hash, '') when it
        landed, ('', reason) when the token would revert it (nothing recorded then: a reverted call uses nothing)."""
        from eth_account import Account
        from eth_account.messages import encode_typed_data
        from eth_utils import keccak

        with self._lock:
            self.block_number = max(self.block_number, self._number_at(self._now()))
            payer, payee = str(authorization["from"]).lower(), str(authorization["to"]).lower()
            value, valid_after, valid_before = int(authorization["value"]), int(authorization["validAfter"]), int(authorization["validBefore"])
            nonce = "0x" + str(authorization["nonce"]).lower().removeprefix("0x").rjust(64, "0")
            typed = {
                "types": {"EIP712Domain": [{"name": "name", "type": "string"}, {"name": "version", "type": "string"},
                                           {"name": "chainId", "type": "uint256"}, {"name": "verifyingContract", "type": "address"}],
                          "TransferWithAuthorization": _TYPES},
                "primaryType": "TransferWithAuthorization",
                "domain": {"name": self.eip712_name, "version": self.eip712_version, "chainId": self.chain_id, "verifyingContract": self.token},
                "message": {"from": payer, "to": payee, "value": value, "validAfter": valid_after, "validBefore": valid_before, "nonce": bytes.fromhex(nonce[2:])},
            }
            try:
                signer = Account.recover_message(encode_typed_data(full_message=typed), signature=bytes.fromhex(str(signature).removeprefix("0x"))).lower()
            except Exception:
                return "", "invalid_signature"
            moment = self.timestamp_of(self.block_number + 1)
            if signer != payer:
                return "", "invalid_signature"
            if not valid_after < moment:
                return "", "authorization_not_yet_valid"
            if not moment < valid_before:
                return "", "authorization_expired"
            if (payer, nonce) in self.used:
                return "", "authorization_used_or_canceled"
            if self.token_balances.get(payer, 0) < value:
                return "", "transfer_amount_exceeds_balance"
            self.block_number += 1
            number = self.block_number
            tx_hash = "0x" + keccak(text=f"{self.chain_id}:{payer}:{nonce}:{number}").hex()
            self.token_balances[payer] -= value
            self.token_balances[payee] = self.token_balances.get(payee, 0) + value
            self.used[(payer, nonce)] = number
            block_hash = "0x" + format(number, "064x")
            logs = [
                {"address": self.token, "topics": [TRANSFER_TOPIC0, _word(payer), _word(payee)], "data": "0x" + format(value, "064x"),
                 "blockNumber": hex(number), "blockHash": block_hash, "transactionHash": tx_hash, "logIndex": "0x0", "removed": False},
                {"address": self.token, "topics": [AUTHORIZATION_USED_TOPIC0, _word(payer), nonce], "data": "0x",
                 "blockNumber": hex(number), "blockHash": block_hash, "transactionHash": tx_hash, "logIndex": "0x1", "removed": False},
            ]
            self.logs.extend(logs)
            tx = {"hash": tx_hash, "from": submitter, "to": self.token, "nonce": len(self.facilitated), "value": 0}
            self.block_txs.setdefault(number, []).append(tx)
            self.receipts[tx_hash] = {
                "transactionHash": tx_hash, "status": "0x1", "blockNumber": hex(number), "blockHash": block_hash, "gasUsed": hex(80_000),
                "effectiveGasPrice": hex(self.base_fee), "from": submitter, "to": self.token, "logs": logs, "type": "0x2", "l1Fee": hex(self.l1_fee), "_tx": tx,
            }
            self.facilitated.append(tx_hash)
            return tx_hash, ""

    def drop_facilitated(self, tx_hash: str) -> None:
        """A reorg takes the block that held a facilitated transaction: its receipt, logs, transfer and the nonce's use
        are gone."""
        with self._lock:
            receipt = self.receipts.pop(tx_hash, None)
            if receipt is None:
                return
            for log in receipt["logs"]:
                if log["topics"][0] == TRANSFER_TOPIC0:
                    payer, payee, value = "0x" + log["topics"][1][-40:], "0x" + log["topics"][2][-40:], int(log["data"], 16)
                    self.token_balances[payer] = self.token_balances.get(payer, 0) + value
                    self.token_balances[payee] = self.token_balances.get(payee, 0) - value
            self.logs = [log for log in self.logs if log["transactionHash"] != tx_hash]
            for key, _number in [item for item in self.used.items() if item[1] == int(receipt["blockNumber"], 16)]:
                self.used.pop(key)

    # -- the node --------------------------------------------------------------------------------------------
    def _answer(self, body: dict[str, Any]) -> tuple[str, Any]:
        method = str(body.get("method") or "")
        params = body.get("params") or []
        self.block_number = max(self.block_number, self._number_at(self._now()))
        if method == "eth_getBlockByNumber":
            kind, payload = super()._answer(body)
            block = payload.get("result") if isinstance(payload, dict) else None
            if isinstance(block, dict):
                block["timestamp"] = hex(self.timestamp_of(int(block["number"], 16)))
            return kind, payload
        if method == "eth_call" and str((params[0] if params else {}).get("to") or "").lower() == self.token:
            data = str(params[0].get("data") or "").lower()
            at = self._tagged(str(params[1]) if len(params) > 1 else "latest")
            if data.startswith(_SELECTOR_BALANCE_OF):
                owner = "0x" + data[10 + 24:10 + 64]
                return self._ok(body, "0x" + format(self.token_balances.get(owner, 0), "064x"))
            if data.startswith(_SELECTOR_AUTHORIZATION_STATE):
                owner, nonce = "0x" + data[10 + 24:10 + 64], "0x" + data[10 + 64:10 + 128]
                used = self.used.get((owner, nonce))
                return self._ok(body, "0x" + format(1 if used is not None and used <= at else 0, "064x"))
            return self._err(body, -32000, "execution reverted")
        if method == "eth_getLogs":
            query = params[0] if params else {}
            start, end = self._tagged(str(query.get("fromBlock") or "0x0")), self._tagged(str(query.get("toBlock") or "latest"))
            if self.max_log_span is not None and end - start + 1 > self.max_log_span:
                return self._err(body, -32005, f"query exceeds max block range {self.max_log_span}")
            topics = [str(topic).lower() if topic is not None else None for topic in (query.get("topics") or [])]
            found = [log for log in self.logs if log["address"] == str(query.get("address") or "").lower() and start <= int(log["blockNumber"], 16) <= end
                     and all(want is None or (i < len(log["topics"]) and log["topics"][i] == want) for i, want in enumerate(topics))]
            return self._ok(body, found)
        return super()._answer(body)


class ScriptedX402SdkResource:
    """A paid resource at loopback. ``mode``: 'settle' (the facilitator lands the authorization, the answer names it) |
    'never' (the payment is refused with a 402 and nothing lands) | 'names_other' (nothing lands; the answer names a
    transaction that is not this payment) | 'drop' (it lands, then the connection closes unanswered) | 'hold' (the
    answer is 200 naming nothing; :meth:`settle_held` lands it later). ``offer`` entries override the challenge's."""

    def __init__(self, chain: ScriptedUsdcChain, *, amount_minor: int, pay_to: str, mode: str = "settle", max_timeout_seconds: int = 60,
                 offer: dict[str, Any] | None = None) -> None:
        self.chain = chain
        self.amount_minor = int(amount_minor)
        self.pay_to = pay_to
        self.mode = mode
        self.max_timeout_seconds = int(max_timeout_seconds)
        self.offer_overrides = dict(offer or {})
        self.requests: list[dict[str, Any]] = []
        self.paid_requests: list[dict[str, Any]] = []
        self.landed: list[str] = []
        self.refusals: list[str] = []
        self.held: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        resource = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: Any) -> None:
                return

            def _handle(self) -> None:
                from x402.http.utils import encode_payment_required_header, encode_payment_response_header

                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                payment = self.headers.get("PAYMENT-SIGNATURE") or ""
                record = {"method": self.command, "path": self.path, "body": body, "paid": bool(payment)}
                with resource._lock:
                    resource.requests.append(record)
                if not payment:
                    challenge = resource.challenge()
                    self._send(402, json.dumps({"error": "payment required"}).encode(), {"Content-Type": "application/json",
                                                                                        "PAYMENT-REQUIRED": encode_payment_required_header(challenge)})
                    return
                payload = resource.decode(payment)
                with resource._lock:
                    resource.paid_requests.append({**record, "payload": payload})
                if resource.mode == "never":
                    self._send(402, b'{"error": "settlement refused"}', {"Content-Type": "application/json"})
                    return
                if resource.mode == "hold":
                    with resource._lock:
                        resource.held.append(payload)
                    self._send(200, PAID_BODY, {"Content-Type": "application/json"})
                    return
                if resource.mode == "names_other":
                    named = "0x" + "ab" * 32
                else:
                    named, refused = resource.chain.execute_authorization(payload["payload"]["authorization"], payload["payload"]["signature"])
                    if not named:
                        with resource._lock:
                            resource.refusals.append(refused)
                        self._send(402, json.dumps({"error": refused}).encode(), {"Content-Type": "application/json"})
                        return
                    with resource._lock:
                        resource.landed.append(named)
                if resource.mode == "drop":
                    self.close_connection = True
                    return
                settled = resource.settle_response(named)
                self._send(200, PAID_BODY, {"Content-Type": "application/json", "PAYMENT-RESPONSE": encode_payment_response_header(settled)})

            do_GET = _handle  # noqa: N815 - http.server dispatches by these names
            do_POST = _handle  # noqa: N815

            def _send(self, status: int, body: bytes, headers: dict[str, str]) -> None:
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def requirement(self) -> dict[str, Any]:
        token, name, version = USDC[self.chain.network]
        entry: dict[str, Any] = {"scheme": "exact", "network": self.chain.network, "amount": str(self.amount_minor), "asset": token, "payTo": self.pay_to,
                                 "maxTimeoutSeconds": self.max_timeout_seconds, "extra": {"name": name, "version": version, "assetTransferMethod": "eip3009"}}
        entry.update(self.offer_overrides)
        return entry

    def challenge(self) -> Any:
        from x402.schemas import PaymentRequired

        return PaymentRequired.model_validate({"x402Version": 2, "error": "payment required", "accepts": [self.requirement()],
                                               "resource": {"url": self.url, "description": "Base numbers", "mimeType": "application/json"}})

    @staticmethod
    def decode(header: str) -> dict[str, Any]:
        from x402.http.utils import decode_payment_signature_header

        return decode_payment_signature_header(header).model_dump(by_alias=True, exclude_none=True)

    def settle_response(self, tx_hash: str) -> Any:
        from x402.schemas import SettleResponse

        return SettleResponse.model_validate({"success": True, "transaction": tx_hash, "network": self.chain.network, "payer": ""})

    def settle_held(self) -> list[str]:
        """Land every authorization the resource held back. Returns the transactions."""
        with self._lock:
            held, self.held = self.held, []
        landed = []
        for payload in held:
            tx_hash, _refused = self.chain.execute_authorization(payload["payload"]["authorization"], payload["payload"]["signature"])
            if tx_hash:
                landed.append(tx_hash)
        with self._lock:
            self.landed.extend(landed)
        return landed

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}/paid/report"

    def __enter__(self) -> ScriptedX402SdkResource:
        self._thread.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self._server.shutdown()
        self._server.server_close()


__all__ = ["BASE_MAINNET", "BASE_SEPOLIA", "PAID_BODY", "USDC", "ScriptedUsdcChain", "ScriptedX402SdkResource"]
