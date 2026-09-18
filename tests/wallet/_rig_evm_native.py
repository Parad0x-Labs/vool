"""A scripted EVM chain for native-coin transfers (ETH on Ethereum/Base/Robinhood Chain, BNB on BSC).

It models the documented wire behaviour the wallet must survive, per fee model:

* ``eip1559`` (Ethereum): charge = gasUsed * min(maxFee, baseFee + priority);
* ``bsc``: base fee 0, the whole gas price is the priority fee;
* ``op_stack`` (Base): the L2 charge plus a separately charged L1 data fee (receipt ``l1Fee``) that
  ``eth_estimateGas`` does NOT include; the GasPriceOracle predeploy answers ``getL1Fee(bytes)`` and
  ``getOperatorFee(uint256)`` through ``eth_call``;
* ``arbitrum`` (Robinhood Chain): L1 cost folded into gas units (receipt ``gasUsedForL1``), tips ignored.

Signed raw transactions are decoded and their sender recovered with eth-account, so the chain enforces
chain id, nonce, balance and fee cap exactly like a node would. Nothing here touches a real chain.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

GAS_PRICE_ORACLE = "0x420000000000000000000000000000000000000f"


def _hex(value: int) -> str:
    return hex(int(value))


def _selector(signature: str) -> str:
    from eth_utils import keccak

    return "0x" + keccak(text=signature)[:4].hex()


class ScriptedEvmNativeChain:
    """One EVM chain at loopback. Mutate the public attributes between calls to script a scenario.

    ``send_mode``: 'ok' | 'reject_insufficient' (validated rejection before acceptance) |
    'accept_then_500' (recorded, then an upstream 500) | 'accept_then_malformed' | 'already_known'.
    ``mine_mode``: 'auto' (included on the next receipt query) | 'never' (pending forever) |
    'revert' (included with status 0: the fee is charged, the value is not moved).
    """

    def __init__(self, *, chain_id: int, fee_model: str = "eip1559", base_fee: int = 1_000_000_000, priority_fee: int = 1_000_000,
                 gas_price: int = 100_000_000, estimate_gas: int = 21_000, l1_fee: int = 0, operator_fee: int = 0,
                 gas_used_for_l1: int = 0, lie_chain_id: int | None = None, fork: str = "isthmus", operator_fee_scalar: int = 0,
                 da_footprint_gas_scalar: int = 400, extra_data_override: str | None = None) -> None:
        self.chain_id = int(chain_id)
        self.fee_model = fee_model
        #: the OP-stack fork the node answers as: "isthmus" (operator term gas*scalar/1e6; header extraData version 0),
        #: "jovian" (gas*scalar*100; version 1; receipts carry daFootprintGasScalar/blobGasUsed) or "unversioned" (no
        #: extraData at all). `extra_data_override` serves a header of the caller's choosing for inconsistent-evidence cases.
        self.fork = fork
        self.operator_fee_scalar = int(operator_fee_scalar)
        self.da_footprint_gas_scalar = int(da_footprint_gas_scalar)
        self.extra_data_override = extra_data_override
        self.base_fee = int(base_fee)
        self.priority_fee = int(priority_fee)
        self.gas_price = int(gas_price)
        self.estimate_gas = int(estimate_gas)
        self.l1_fee = int(l1_fee)
        self.operator_fee = int(operator_fee)
        self.gas_used_for_l1 = int(gas_used_for_l1)
        self.lie_chain_id = lie_chain_id
        self.balances: dict[str, int] = {}
        self.nonces: dict[str, int] = {}
        self.code: dict[str, str] = {}
        self.block_number = 100
        #: when set, the "safe" / "finalized" tags answer these block numbers (None: the latest block)
        self.safe_block: int | None = None
        self.finalized_block: int | None = None
        #: block number -> the transactions mined in it (for full-transaction block reads and historical nonces)
        self.block_txs: dict[int, list[dict[str, Any]]] = {}
        #: tx hash -> the number of further receipt reads that answer null although the transaction is mined
        self.receipt_lag: dict[str, int] = {}
        #: receipts of blocks below this number answer null (a pruned node); None keeps every receipt
        self.prune_receipts_below: int | None = None
        #: block numbers whose eth_getBlockByNumber hash is non-canonical (differs from receipts' blockHash)
        self.forked_blocks: set[int] = set()
        #: when set, eth_sendRawTransaction answers this txpool error and records nothing
        self.txpool_refusal: str | None = None
        self.send_mode = "ok"
        self.mine_mode = "auto"
        self.oracle_available = True
        self.calls: list[dict[str, Any]] = []
        self.sent: list[bytes] = []
        self.pending: dict[str, dict[str, Any]] = {}
        self.receipts: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()
        chain = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: Any) -> None:
                return

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                with chain._lock:
                    chain.calls.append({"method": body.get("method"), "params": body.get("params") or []})
                    outcome = chain._answer(body)
                kind, payload = outcome
                if kind == "raw":
                    status, data = payload
                    self.send_response(status)
                    self.send_header("Content-Type", "text/plain")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                data = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    # -- scripting helpers -----------------------------------------------------------------------------
    def fund(self, address: str, wei: int) -> None:
        with self._lock:
            self.balances[address.lower()] = int(wei)

    def balance_of(self, address: str) -> int:
        with self._lock:
            return int(self.balances.get(address.lower(), 0))

    def send_count(self) -> int:
        with self._lock:
            return len(self.sent)

    def methods(self) -> list[str]:
        with self._lock:
            return [str(call["method"]) for call in self.calls]

    def mine_pending(self) -> None:
        with self._lock:
            for tx_hash in list(self.pending):
                self._include(tx_hash)

    def inject_replacement(self, sender: str, nonce: int) -> str:
        """Another transaction of ``sender`` with ``nonce`` is mined in a new block; any pending transaction with that
        sender and nonce can no longer be included and is dropped. Returns the replacement's hash."""
        with self._lock:
            sender = sender.lower()
            for tx_hash in [h for h, tx in self.pending.items() if tx["from"] == sender and tx["nonce"] == int(nonce)]:
                self.pending.pop(tx_hash)
            self.block_number += 1
            replacement_hash = "0x" + format((int(nonce) << 64) + self.block_number, "064x")
            tx = {"hash": replacement_hash, "from": sender, "to": "0x" + "9" * 40, "nonce": int(nonce), "value": 0}
            self.block_txs.setdefault(self.block_number, []).append(tx)
            self.nonces[sender] = max(self.nonces.get(sender, 0), int(nonce) + 1)
            return replacement_hash

    def drop_receipt(self, tx_hash: str) -> None:
        """A reorg: the block that held the transaction is gone; it is pending again."""
        with self._lock:
            receipt = self.receipts.pop(tx_hash.lower(), None)
            if receipt is not None:
                self.pending[tx_hash.lower()] = receipt["_tx"]

    # -- the node ----------------------------------------------------------------------------------------
    def _ok(self, body: dict[str, Any], result: Any) -> tuple[str, Any]:
        return "json", {"jsonrpc": "2.0", "id": body.get("id"), "result": result}

    def _err(self, body: dict[str, Any], code: int, message: str) -> tuple[str, Any]:
        return "json", {"jsonrpc": "2.0", "id": body.get("id"), "error": {"code": code, "message": message}}

    def _effective_price(self, max_fee: int, max_priority: int) -> int:
        if self.fee_model == "arbitrum":
            return self.base_fee
        if self.fee_model == "bsc":
            return min(max_fee, max_priority)
        return min(max_fee, self.base_fee + max_priority)

    def _include(self, tx_hash: str) -> None:
        tx = self.pending.pop(tx_hash)
        self.block_number += 1
        self.block_txs.setdefault(self.block_number, []).append({"hash": tx_hash, "from": tx["from"], "to": tx["to"], "nonce": tx["nonce"], "value": tx["value"]})
        gas_used = self.estimate_gas
        price = self._effective_price(tx["maxFeePerGas"], tx["maxPriorityFeePerGas"])
        fee = gas_used * price + (self.l1_fee if self.fee_model == "op_stack" else 0) + self.operator_charge(gas_used)
        sender = tx["from"]
        reverted = self.mine_mode == "revert"
        self.balances[sender] = self.balances.get(sender, 0) - fee - (0 if reverted else tx["value"])
        if not reverted:
            self.balances[tx["to"]] = self.balances.get(tx["to"], 0) + tx["value"]
        self.nonces[sender] = max(self.nonces.get(sender, 0), tx["nonce"] + 1)
        receipt: dict[str, Any] = {
            "transactionHash": tx_hash, "status": "0x0" if reverted else "0x1", "blockNumber": _hex(self.block_number),
            "blockHash": "0x" + format(self.block_number, "064x"), "gasUsed": _hex(gas_used), "effectiveGasPrice": _hex(price),
            "from": sender, "to": tx["to"], "logs": [], "type": "0x2", "_tx": tx,
        }
        if self.fee_model == "op_stack":
            receipt["l1Fee"] = _hex(self.l1_fee)
            if self.operator_fee or self.operator_fee_scalar:
                # the Isthmus rule: the two operator fields exist iff at least one of them is non-zero
                receipt["operatorFeeScalar"], receipt["operatorFeeConstant"] = _hex(self.operator_fee_scalar), _hex(self.operator_fee)
            if self.fork == "jovian":
                receipt["daFootprintGasScalar"], receipt["blobGasUsed"] = _hex(self.da_footprint_gas_scalar), _hex(gas_used * 2)
        if self.fee_model == "arbitrum":
            receipt["gasUsedForL1"] = _hex(self.gas_used_for_l1)
        self.receipts[tx_hash] = receipt

    def operator_charge(self, gas_used: int) -> int:
        """The operator fee the node charges for ``gas_used`` under its fork (zero off OP-stack rows)."""
        if self.fee_model != "op_stack":
            return 0
        scalar_term = gas_used * self.operator_fee_scalar * 100 if self.fork == "jovian" else gas_used * self.operator_fee_scalar // 10**6
        return scalar_term + self.operator_fee

    def header_extra_data(self) -> str | None:
        """The block header's extraData under the node's fork: Holocene/Isthmus version byte 0 plus the 8 EIP-1559
        parameter bytes; Jovian version byte 1 plus those 8 bytes and the 8-byte minBaseFee; "unversioned" none."""
        if self.extra_data_override is not None:
            return self.extra_data_override
        if self.fee_model != "op_stack" or self.fork == "unversioned":
            return None
        params = format(250, "08x") + format(6, "08x")
        if self.fork == "jovian":
            return "0x01" + params + format(0, "016x")
        return "0x00" + params

    def _decode(self, raw_hex: str) -> dict[str, Any]:
        from eth_account import Account
        from eth_account.typed_transactions import TypedTransaction
        from eth_utils import keccak
        from hexbytes import HexBytes

        raw = bytes.fromhex(raw_hex.removeprefix("0x"))
        typed = TypedTransaction.from_bytes(HexBytes(raw)).as_dict()
        to = typed.get("to") or b""
        to_text = ("0x" + bytes(to).hex()) if isinstance(to, (bytes, bytearray)) else str(to)
        return {
            "raw": raw, "hash": "0x" + keccak(raw).hex(), "from": Account.recover_transaction(raw).lower(),
            "access_list": tuple(typed.get("accessList") or ()), "type": int(typed.get("type") or 0),
            "chainId": int(typed["chainId"]), "nonce": int(typed["nonce"]), "to": to_text.lower(),
            "value": int(typed["value"]), "gas": int(typed["gas"]), "maxFeePerGas": int(typed["maxFeePerGas"]),
            "maxPriorityFeePerGas": int(typed["maxPriorityFeePerGas"]), "data": bytes(typed.get("data") or b""),
        }

    def _answer(self, body: dict[str, Any]) -> tuple[str, Any]:
        method = str(body.get("method") or "")
        params = body.get("params") or []
        if method == "eth_chainId":
            return self._ok(body, _hex(self.lie_chain_id if self.lie_chain_id is not None else self.chain_id))
        if method == "eth_blockNumber":
            return self._ok(body, _hex(self.block_number))
        if method == "eth_getBlockByNumber":
            tag = str(params[0]) if params else "latest"
            number = self.block_number
            if tag == "safe" and self.safe_block is not None:
                number = int(self.safe_block)
            elif tag == "finalized" and self.finalized_block is not None:
                number = int(self.finalized_block)
            elif tag.startswith("0x"):
                number = int(tag, 16)
                if number > self.block_number:
                    return self._ok(body, None)
            canonical = "0x" + format(number, "064x")
            block = {"number": _hex(number), "hash": ("0x" + "de" * 32) if number in self.forked_blocks else canonical, "timestamp": _hex(1_789_396_000 + number), "baseFeePerGas": _hex(0 if self.fee_model == "bsc" else self.base_fee)}
            extra = self.header_extra_data()
            if extra is not None:
                block["extraData"] = extra
            if len(params) > 1 and params[1] is True:
                block["transactions"] = [
                    {"hash": tx["hash"], "from": tx["from"], "to": tx["to"], "nonce": _hex(tx["nonce"]), "value": _hex(tx["value"]), "blockNumber": _hex(number)}
                    for tx in self.block_txs.get(number, [])
                ]
            return self._ok(body, block)
        if method == "eth_getBalance":
            return self._ok(body, _hex(self.balances.get(str(params[0]).lower(), 0)))
        if method == "eth_getCode":
            return self._ok(body, self.code.get(str(params[0]).lower(), "0x"))
        if method == "eth_getTransactionCount":
            address = str(params[0]).lower()
            if len(params) > 1 and str(params[1]).startswith("0x"):
                at_block = int(str(params[1]), 16)
                mined = [tx["nonce"] for number, txs in self.block_txs.items() if number <= at_block for tx in txs if tx["from"] == address]
                return self._ok(body, _hex(max(mined) + 1 if mined else 0))
            latest = self.nonces.get(address, 0)
            if len(params) > 1 and params[1] == "pending":
                latest += sum(1 for tx in self.pending.values() if tx["from"] == address)
            return self._ok(body, _hex(latest))
        if method == "eth_estimateGas":
            return self._ok(body, _hex(self.estimate_gas))
        if method == "eth_maxPriorityFeePerGas":
            return self._ok(body, _hex(self.priority_fee))
        if method == "eth_gasPrice":
            return self._ok(body, _hex(self.gas_price if self.fee_model == "bsc" else self.base_fee + self.priority_fee))
        if method == "eth_call":
            call = params[0] if params else {}
            if str(call.get("to") or "").lower() != GAS_PRICE_ORACLE or self.fee_model != "op_stack" or not self.oracle_available:
                return self._err(body, -32000, "execution reverted")
            data = str(call.get("data") or "")
            if data.startswith(_selector("getL1Fee(bytes)")):
                if len(data) <= 10 + 128:
                    return self._err(body, -32000, "execution reverted: empty transaction bytes")
                return self._ok(body, "0x" + format(self.l1_fee, "064x"))
            if data.startswith(_selector("getOperatorFee(uint256)")):
                # the chain's own oracle applies the active fork's formula
                gas = int(data[10:10 + 64], 16) if len(data) >= 10 + 64 else self.estimate_gas
                return self._ok(body, "0x" + format(self.operator_charge(gas), "064x"))
            return self._err(body, -32000, "execution reverted")
        if method == "eth_sendRawTransaction":
            return self._send(body, str(params[0]))
        if method == "eth_getTransactionReceipt":
            tx_hash = str(params[0]).lower()
            if tx_hash in self.pending and self.mine_mode in {"auto", "revert"}:
                self._include(tx_hash)
            receipt = self.receipts.get(tx_hash)
            if receipt is not None and self.receipt_lag.get(tx_hash, 0) > 0:
                self.receipt_lag[tx_hash] -= 1
                receipt = None
            if receipt is not None and self.prune_receipts_below is not None and int(receipt["blockNumber"], 16) < int(self.prune_receipts_below):
                receipt = None
            return self._ok(body, None if receipt is None else {k: v for k, v in receipt.items() if not k.startswith("_")})
        if method == "eth_getTransactionByHash":
            tx_hash = str(params[0]).lower()
            tx = self.pending.get(tx_hash) or (self.receipts.get(tx_hash) or {}).get("_tx")
            if tx is None:
                return self._ok(body, None)
            mined = self.receipts.get(tx_hash)
            return self._ok(body, {"hash": tx_hash, "from": tx["from"], "to": tx["to"], "value": _hex(tx["value"]), "nonce": _hex(tx["nonce"]), "blockNumber": mined["blockNumber"] if mined else None})
        return self._ok(body, None)

    def _send(self, body: dict[str, Any], raw_hex: str) -> tuple[str, Any]:
        tx = self._decode(raw_hex)
        if self.txpool_refusal:
            return self._err(body, -32000, self.txpool_refusal)
        if self.send_mode == "already_known" and tx["hash"] in self.pending:
            return self._err(body, -32000, "already known")
        if tx["chainId"] != self.chain_id:
            return self._err(body, -32000, "invalid chain id for signer")
        expected = self.nonces.get(tx["from"], 0) + sum(1 for p in self.pending.values() if p["from"] == tx["from"])
        if tx["hash"] in self.pending or tx["hash"] in self.receipts:
            return self._err(body, -32000, "already known")
        if tx["nonce"] < expected:
            return self._err(body, -32000, "nonce too low")
        if tx["nonce"] > expected:
            return self._err(body, -32000, "nonce too high")
        worst = tx["gas"] * tx["maxFeePerGas"] + tx["value"] + (self.l1_fee if self.fee_model == "op_stack" else 0)
        if self.send_mode == "reject_insufficient" or self.balances.get(tx["from"], 0) < worst:
            return self._err(body, -32000, "insufficient funds for gas * price + value")
        if self.fee_model != "bsc" and tx["maxFeePerGas"] < self.base_fee:
            return self._err(body, -32000, "max fee per gas less than block base fee")
        self.sent.append(tx["raw"])
        self.pending[tx["hash"]] = tx
        if self.send_mode == "accept_then_500":
            return "raw", (500, b"upstream error")
        if self.send_mode == "accept_then_malformed":
            return "raw", (200, b'{"jsonrpc": "2.0", "id": ')
        return self._ok(body, tx["hash"])

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def __enter__(self) -> ScriptedEvmNativeChain:
        self._thread.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self._server.shutdown()
        self._server.server_close()


__all__ = ["GAS_PRICE_ORACLE", "ScriptedEvmNativeChain"]
