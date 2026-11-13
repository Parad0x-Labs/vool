"""SIMULATION: a swap venue and a Jupiter-shaped quote/build API for the wallet's swap lane (core.wallet.swaps).

``SwapNode`` is the simulated Solana node (tests/wallet/_simulated_solana.py) that also EXECUTES an instruction of
Jupiter's program id as a stand-in venue: it takes the input coin from the wallet and gives the output coin, in the
amounts the built instruction names. ``FakeJupiter`` answers ``GET /quote`` and ``POST /swap`` in Jupiter's shapes, at a
fixed rate, and builds a real v0 transaction for the node's live blockhash. Knobs make it misbehave the ways a
compromised or buggy provider could (deliver less, take more, add a transfer to someone else, ask for a second signer,
call an unknown program, hand out a token delegate, overpay priority), so the wallet's checks are exercised on real
bytes. Every value is synthetic.
"""
from __future__ import annotations

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

from tests.wallet._simulated_solana import (
    ASSOCIATED_TOKEN_PROGRAM,
    SYSTEM_PROGRAM,
    TOKEN_PROGRAM,
    SimulatedSolanaNode,
    _Failure,
)

JUPITER_PROGRAM = "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4"
COMPUTE_BUDGET_PROGRAM = "ComputeBudget111111111111111111111111111111"
WSOL_MINT = "So11111111111111111111111111111111111111112"
SWAP_TAG = 0x07


class SwapNode(SimulatedSolanaNode):
    """The simulated node, plus a stand-in venue at Jupiter's program id: data = tag, amount in (u64), amount out (u64);
    accounts = wallet (signer), source (the wallet itself for SOL, else its token account), destination (likewise)."""

    def _execute_other(self, index: int, program: str, accounts: list[str], data: bytes, *, signers: set[str], lamports: dict[str, int],
                       tokens: dict[str, dict[str, Any]], payments: list[dict[str, Any]]) -> None:
        if program != JUPITER_PROGRAM or len(data) != 17 or data[0] != SWAP_TAG or len(accounts) < 3:
            return super()._execute_other(index, program, accounts, data, signers=signers, lamports=lamports, tokens=tokens, payments=payments)
        wallet, source, destination = accounts[:3]
        amount_in, amount_out = int.from_bytes(data[1:9], "little"), int.from_bytes(data[9:17], "little")
        if wallet not in signers:
            raise _Failure({"InstructionError": [index, "MissingRequiredSignature"]}, "the wallet did not sign the swap")
        if source == wallet:
            if lamports.get(wallet, 0) < amount_in:
                raise _Failure({"InstructionError": [index, {"Custom": 6001}]}, "not enough SOL for the swap")
            lamports[wallet] -= amount_in
        else:
            account = tokens.get(source)
            if account is None or account["owner"] != wallet or account["amount"] < amount_in:
                raise _Failure({"InstructionError": [index, {"Custom": 6001}]}, "not enough of the input token")
            account["amount"] -= amount_in
        if destination == wallet:
            lamports[wallet] = lamports.get(wallet, 0) + amount_out
        else:
            account = tokens.get(destination)
            if account is None or account["owner"] != wallet:
                raise _Failure({"InstructionError": [index, "InvalidAccountData"]}, "the output token account does not exist")
            account["amount"] += amount_out
            self.mints[account["mint"]]["supply"] += amount_out
        payments.append({"asset": "swap", "amount_atomic": amount_in, "to_owner": JUPITER_PROGRAM, "from_owner": wallet})


class FakeJupiter:
    """Jupiter's quote and build endpoints at a fixed rate: ``rate`` output atomic units per input atomic unit, as a
    fraction (numerator, denominator) per (input mint, output mint)."""

    def __init__(self, node: SwapNode, *, rates: dict[tuple[str, str], tuple[int, int]], price_impact_pct: str = "0.0004") -> None:
        self.node = node
        self.rates = dict(rates)
        self.price_impact_pct = price_impact_pct
        self.knobs: dict[str, Any] = {}
        self.calls: list[tuple[str, str]] = []
        self.attacker = ""
        rig = self

        class _Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args: Any) -> None:
                return

            def do_GET(self) -> None:
                parts = urlsplit(self.path)
                rig.calls.append(("GET", parts.path))
                if not parts.path.endswith("/quote"):
                    return self._send(404, {"error": "not found"})
                query = {k: v[0] for k, v in parse_qs(parts.query).items()}
                status, body = rig.quote(query)
                return self._send(status, body)

            def do_POST(self) -> None:
                parts = urlsplit(self.path)
                rig.calls.append(("POST", parts.path))
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                if not parts.path.endswith("/swap"):
                    return self._send(404, {"error": "not found"})
                status, body = rig.build(json.loads(raw.decode()))
                return self._send(status, body)

            def _send(self, status: int, payload: dict[str, Any]) -> None:
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}/swap/v1"

    def __enter__(self) -> FakeJupiter:
        self._thread.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self._server.shutdown()
        self._server.server_close()

    # --- the API ------------------------------------------------------------------------------------------------

    def quote(self, query: dict[str, str]) -> tuple[int, dict[str, Any]]:
        pair = (query.get("inputMint", ""), query.get("outputMint", ""))
        if pair not in self.rates:
            return 400, {"error": "The token is not tradable", "errorCode": "TOKEN_NOT_TRADABLE"}
        num, den = self.rates[pair]
        amount, slippage, mode = int(query["amount"]), int(query.get("slippageBps") or 50), query.get("swapMode") or "ExactIn"
        if mode == "ExactIn":
            in_amount, out_amount = amount, amount * num // den
            threshold = out_amount * (10_000 - slippage) // 10_000
        else:
            out_amount, in_amount = amount, -(-amount * den // num)
            threshold = -(-in_amount * (10_000 + slippage) // 10_000)
        body = {
            "inputMint": pair[0], "inAmount": str(in_amount), "outputMint": pair[1], "outAmount": str(out_amount), "otherAmountThreshold": str(threshold),
            "swapMode": mode, "slippageBps": slippage, "platformFee": None, "priceImpactPct": self.price_impact_pct,
            "routePlan": [{"swapInfo": {"ammKey": "Amm1111111111111111111111111111111111111111", "label": "SimPool", "inputMint": pair[0], "outputMint": pair[1],
                                        "inAmount": str(in_amount), "outAmount": str(out_amount)}, "percent": 100}],
            "contextSlot": self.node.slot, "timeTaken": 0.001,
        }
        if self.knobs.get("quote_lies_about_mint"):
            body["outputMint"] = WSOL_MINT
        return 200, body

    def build(self, request: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        from solders.hash import Hash
        from solders.instruction import AccountMeta, Instruction
        from solders.keypair import Keypair
        from solders.message import MessageV0
        from solders.pubkey import Pubkey
        from solders.signature import Signature
        from solders.system_program import TransferParams, transfer
        from solders.transaction import VersionedTransaction

        if self.knobs.get("build_refused"):
            return 422, {"error": "route expired", "errorCode": "ROUTE_EXPIRED"}
        quote = request["quoteResponse"]
        owner = Pubkey.from_string(str(request["userPublicKey"]))
        in_mint, out_mint = str(quote["inputMint"]), str(quote["outputMint"])
        amount_in, amount_out = int(quote["inAmount"]), int(quote["outAmount"])
        if self.knobs.get("take_more"):
            amount_in += int(self.knobs["take_more"])
        if self.knobs.get("deliver_less"):
            amount_out = int(quote["otherAmountThreshold"]) - 1 if quote["swapMode"] == "ExactIn" else amount_out - int(self.knobs["deliver_less"])
        source = owner if in_mint == WSOL_MINT else Pubkey.from_string(self.node.token_account_address(str(owner), in_mint))
        destination = owner if out_mint == WSOL_MINT else Pubkey.from_string(self.node.token_account_address(str(owner), out_mint))
        budget = Pubkey.from_string(COMPUTE_BUDGET_PROGRAM)
        price = int(self.knobs.get("unit_price") or 100_000)
        instructions = [
            Instruction(budget, bytes([2]) + (200_000).to_bytes(4, "little"), []),
            Instruction(budget, bytes([3]) + price.to_bytes(8, "little"), []),
        ]
        if out_mint != WSOL_MINT:
            instructions.append(Instruction(Pubkey.from_string(ASSOCIATED_TOKEN_PROGRAM), bytes([1]), [
                AccountMeta(owner, True, True), AccountMeta(destination, False, True), AccountMeta(owner, False, False),
                AccountMeta(Pubkey.from_string(out_mint), False, False), AccountMeta(Pubkey.from_string(SYSTEM_PROGRAM), False, False),
                AccountMeta(Pubkey.from_string(TOKEN_PROGRAM), False, False)]))
        instructions.append(Instruction(Pubkey.from_string(JUPITER_PROGRAM), bytes([SWAP_TAG]) + amount_in.to_bytes(8, "little") + amount_out.to_bytes(8, "little"), [
            AccountMeta(owner, True, True), AccountMeta(source, False, True), AccountMeta(destination, False, True)]))
        if self.knobs.get("extra_transfer"):
            self.attacker = self.attacker or str(Keypair().pubkey())
            instructions.append(transfer(TransferParams(from_pubkey=owner, to_pubkey=Pubkey.from_string(self.attacker), lamports=int(self.knobs["extra_transfer"]))))
        if self.knobs.get("unknown_program"):
            instructions.append(Instruction(Keypair().pubkey(), b"\x01", [AccountMeta(owner, True, True)]))
        if self.knobs.get("approve_delegate") and in_mint != WSOL_MINT:
            delegate = Keypair().pubkey()
            instructions.append(Instruction(Pubkey.from_string(TOKEN_PROGRAM), bytes([4]) + (10**12).to_bytes(8, "little"), [
                AccountMeta(source, False, True), AccountMeta(delegate, False, False), AccountMeta(owner, True, False)]))
        if self.knobs.get("second_signer"):
            other = Keypair().pubkey()
            instructions.append(Instruction(Pubkey.from_string(JUPITER_PROGRAM), b"\x00", [AccountMeta(other, True, False)]))
        blockhash = self.node.latest_blockhash
        message = MessageV0.try_compile(owner, instructions, [], Hash.from_string(blockhash))
        signatures = [Signature.default()] * int(message.header.num_required_signatures)
        transaction = VersionedTransaction.populate(message, signatures)
        return 200, {"swapTransaction": base64.b64encode(bytes(transaction)).decode(), "lastValidBlockHeight": self.node._blockhashes[blockhash],
                     "prioritizationFeeLamports": 20_000}


__all__ = ["JUPITER_PROGRAM", "WSOL_MINT", "FakeJupiter", "SwapNode"]
