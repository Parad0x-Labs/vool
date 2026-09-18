"""SIMULATION: a loopback Solana-dialect JSON-RPC node that EXECUTES the signed transactions it is sent.

Not a validator and not a public cluster. It holds lamports, SPL mints and token accounts in memory, answers as
one cluster (the genesis hash it is built with), verifies every ed25519 signature of a submitted transaction,
checks the blockhash, simulates before accepting (the preflight a real node runs), and applies exactly two
instruction kinds atomically: a System Program transfer and an SPL Token ``TransferChecked``. Everything else
fails the transaction. The fee is ``lamports_per_signature`` per required signature, charged to the fee payer.

What it exists for: composition proofs where the wallet's real custody signs real bytes, the chain role is played
locally, and the provider stand-in verifies a payment by reading what this node actually executed. Fault knobs
cover a lost send answer, hidden status reads and a refused preflight. Every value it serves is synthetic.
"""
from __future__ import annotations

import base64
import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

SYSTEM_PROGRAM = "11111111111111111111111111111111"
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
MAINNET_GENESIS = "5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d"
#: the documented USDC mint on Solana mainnet; in this node it is just a synthetic mint with the same address
USDC_MAINNET_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
RENT_PER_BYTE_YEAR = 3480
RENT_EXEMPTION_YEARS = 2
ACCOUNT_STORAGE_OVERHEAD = 128
TOKEN_ACCOUNT_SIZE = 165
MINT_SIZE = 82


def rent_minimum(size: int) -> int:
    return (int(size) + ACCOUNT_STORAGE_OVERHEAD) * RENT_PER_BYTE_YEAR * RENT_EXEMPTION_YEARS


class _Failure(Exception):
    def __init__(self, err: Any, log: str) -> None:
        super().__init__(log)
        self.err = err
        self.log = log


class SimulatedSolanaNode:
    def __init__(self, *, genesis_hash: str = MAINNET_GENESIS, lamports_per_signature: int = 5000) -> None:
        self.genesis_hash = genesis_hash
        self.lamports_per_signature = int(lamports_per_signature)
        self.lock = threading.RLock()
        self.lamports: dict[str, int] = {}
        self.mints: dict[str, dict[str, int]] = {}
        self.token_accounts: dict[str, dict[str, Any]] = {}
        self.transactions: dict[str, dict[str, Any]] = {}
        self.sent: list[bytes] = []
        self.slot = 400_000_000
        self.block_height = 380_000_000
        self._blockhashes: dict[str, int] = {}
        self.latest_blockhash = ""
        self.advance_blocks(0)
        #: fault knobs -- "send_mode": "ok" | "record_then_drop_answer" | "refuse_preflight"; "hide_status_reads": int
        self.faults: dict[str, Any] = {}
        node = self

        class _Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args: Any) -> None:
                return

            def do_POST(self) -> None:
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                try:
                    body = json.loads(raw.decode("utf-8"))
                except Exception:
                    return self._send(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
                outcome = node._dispatch(str(body.get("method") or ""), list(body.get("params") or []))
                if outcome is _DROP:
                    self.close_connection = True
                    return None
                payload = {"jsonrpc": "2.0", "id": body.get("id")}
                payload.update(outcome)
                return self._send(200, payload)

            def _send(self, status: int, payload: dict[str, Any]) -> None:
                data = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    # --- lifecycle ------------------------------------------------------------------------------------------

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def start(self) -> SimulatedSolanaNode:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def __enter__(self) -> SimulatedSolanaNode:
        return self.start()

    def __exit__(self, *_exc: Any) -> None:
        self.stop()

    # --- test controls --------------------------------------------------------------------------------------

    def advance_blocks(self, count: int) -> str:
        with self.lock:
            self.block_height += int(count)
            self.slot += int(count)
            seed = f"{self.genesis_hash}:{self.block_height}".encode()
            from solders.hash import Hash

            self.latest_blockhash = str(Hash(hashlib.sha256(seed).digest()))
            self._blockhashes[self.latest_blockhash] = self.block_height + 150
            return self.latest_blockhash

    def fund_sol(self, address: str, lamports: int) -> None:
        with self.lock:
            self.lamports[str(address)] = self.lamports.get(str(address), 0) + int(lamports)

    def create_mint(self, mint: str, *, decimals: int) -> None:
        with self.lock:
            self.mints[str(mint)] = {"decimals": int(decimals), "supply": 0}
            self.lamports[str(mint)] = rent_minimum(MINT_SIZE)

    def token_account_address(self, owner: str, mint: str) -> str:
        from solders.pubkey import Pubkey
        from solders.token.associated import get_associated_token_address

        return str(get_associated_token_address(Pubkey.from_string(str(owner)), Pubkey.from_string(str(mint))))

    def open_token_account(self, owner: str, mint: str) -> str:
        address = self.token_account_address(owner, mint)
        with self.lock:
            self.token_accounts.setdefault(address, {"mint": str(mint), "owner": str(owner), "amount": 0, "frozen": False})
            self.lamports[address] = rent_minimum(TOKEN_ACCOUNT_SIZE)
        return address

    def fund_token(self, owner: str, mint: str, amount: int) -> str:
        address = self.open_token_account(owner, mint)
        with self.lock:
            self.token_accounts[address]["amount"] += int(amount)
            self.mints[str(mint)]["supply"] += int(amount)
        return address

    def sol_balance(self, address: str) -> int:
        with self.lock:
            return int(self.lamports.get(str(address), 0))

    def token_balance(self, owner: str, mint: str) -> int:
        with self.lock:
            account = self.token_accounts.get(self.token_account_address(owner, mint))
            return int(account["amount"]) if account else 0

    def payment_to(self, signature: str) -> dict[str, Any] | None:
        """What an executed, successful transaction paid, per recipient owner: the provider's verification read."""
        with self.lock:
            record = self.transactions.get(str(signature))
            if record is None or record["err"] is not None:
                return None
            return {"payments": [dict(item) for item in record["payments"]], "fee": record["fee"], "payer": record["fee_payer"]}

    def distinct_sends(self) -> int:
        with self.lock:
            return len({bytes(raw) for raw in self.sent})

    # --- JSON-RPC -------------------------------------------------------------------------------------------

    def _context(self) -> dict[str, Any]:
        return {"slot": self.slot}

    def _dispatch(self, method: str, params: list[Any]) -> Any:
        with self.lock:
            handler = getattr(self, "_rpc_" + method, None)
            if handler is None:
                return {"error": {"code": -32601, "message": f"Method not found: {method}"}}
            return handler(params)

    def _rpc_getGenesisHash(self, _params: list[Any]) -> dict[str, Any]:
        return {"result": self.genesis_hash}

    def _rpc_getLatestBlockhash(self, _params: list[Any]) -> dict[str, Any]:
        return {"result": {"context": self._context(), "value": {"blockhash": self.latest_blockhash, "lastValidBlockHeight": self._blockhashes[self.latest_blockhash]}}}

    def _rpc_getBlockHeight(self, _params: list[Any]) -> dict[str, Any]:
        return {"result": self.block_height}

    def _rpc_getSlot(self, _params: list[Any]) -> dict[str, Any]:
        return {"result": self.slot}

    def _rpc_minimumLedgerSlot(self, _params: list[Any]) -> dict[str, Any]:
        return {"result": 0}

    def _rpc_getBalance(self, params: list[Any]) -> dict[str, Any]:
        return {"result": {"context": self._context(), "value": self.lamports.get(str(params[0]), 0)}}

    def _rpc_getMinimumBalanceForRentExemption(self, params: list[Any]) -> dict[str, Any]:
        return {"result": rent_minimum(int(params[0]) if params else 0)}

    def _account_value(self, address: str) -> dict[str, Any] | None:
        from solders.pubkey import Pubkey
        from solders.token.state import Mint, TokenAccount, TokenAccountState

        if address in self.token_accounts:
            account = self.token_accounts[address]
            data = bytes(TokenAccount(
                mint=Pubkey.from_string(account["mint"]), owner=Pubkey.from_string(account["owner"]), amount=int(account["amount"]), delegate=None,
                state=TokenAccountState.Frozen if account["frozen"] else TokenAccountState.Initialized, is_native=None, delegated_amount=0, close_authority=None,
            ))
            return {"lamports": self.lamports.get(address, 0), "owner": TOKEN_PROGRAM, "executable": False, "rentEpoch": 0, "space": len(data), "data": [base64.b64encode(data).decode(), "base64"]}
        if address in self.mints:
            mint = self.mints[address]
            data = bytes(Mint(mint_authority=None, supply=int(mint["supply"]), decimals=int(mint["decimals"]), is_initialized=True, freeze_authority=None))
            return {"lamports": self.lamports.get(address, 0), "owner": TOKEN_PROGRAM, "executable": False, "rentEpoch": 0, "space": len(data), "data": [base64.b64encode(data).decode(), "base64"]}
        if self.lamports.get(address, 0) > 0:
            return {"lamports": self.lamports[address], "owner": SYSTEM_PROGRAM, "executable": False, "rentEpoch": 0, "space": 0, "data": ["", "base64"]}
        return None

    def _rpc_getAccountInfo(self, params: list[Any]) -> dict[str, Any]:
        return {"result": {"context": self._context(), "value": self._account_value(str(params[0]))}}

    def _rpc_getTokenAccountBalance(self, params: list[Any]) -> dict[str, Any]:
        account = self.token_accounts.get(str(params[0]))
        if account is None:
            return {"error": {"code": -32602, "message": "Invalid param: could not find account"}}
        decimals = int(self.mints[account["mint"]]["decimals"])
        amount = int(account["amount"])
        return {"result": {"context": self._context(), "value": {"amount": str(amount), "decimals": decimals, "uiAmountString": _ui(amount, decimals)}}}

    def _rpc_getFeeForMessage(self, params: list[Any]) -> dict[str, Any]:
        from solders.message import Message

        message = Message.from_bytes(base64.b64decode(params[0]))
        return {"result": {"context": self._context(), "value": self.lamports_per_signature * int(message.header.num_required_signatures)}}

    def _rpc_simulateTransaction(self, params: list[Any]) -> dict[str, Any]:
        from solders.transaction import Transaction

        transaction = Transaction.from_bytes(base64.b64decode(params[0]))
        try:
            self._execute(transaction, apply=False, verify_signatures=False)
            err: Any = None
            logs: list[str] = ["SIMULATION: ok"]
        except _Failure as failure:
            err, logs = failure.err, [f"SIMULATION: {failure.log}"]
        return {"result": {"context": self._context(), "value": {"err": err, "logs": logs, "unitsConsumed": 300}}}

    def _rpc_sendTransaction(self, params: list[Any]) -> Any:
        from solders.transaction import Transaction

        raw = base64.b64decode(params[0])
        transaction = Transaction.from_bytes(raw)
        signature = str(transaction.signatures[0])
        mode = str(self.faults.get("send_mode") or "ok")
        if mode == "refuse_preflight":
            return {"error": {"code": -32002, "message": "Transaction simulation failed: refused by fault knob", "data": {"err": "AccountInUse", "logs": []}}}
        # signatures first, as a node does: bytes that do not verify are refused before any status-cache answer
        if not all(transaction.verify_with_results()):
            return {"error": {"code": -32003, "message": "Transaction signature verification failure"}}
        if signature in self.transactions:
            return {"error": {"code": -32002, "message": "Transaction simulation failed: This transaction has already been processed", "data": {"err": "AlreadyProcessed", "logs": []}}}
        try:
            self._execute(transaction, apply=False, verify_signatures=True)
        except _Failure as failure:
            return {"error": {"code": -32002, "message": f"Transaction simulation failed: {failure.log}", "data": {"err": failure.err, "logs": [failure.log]}}}
        self.sent.append(raw)
        self._execute(transaction, apply=True, verify_signatures=True)
        if mode == "record_then_drop_answer":
            return _DROP
        return {"result": signature}

    def _rpc_getSignatureStatuses(self, params: list[Any]) -> dict[str, Any]:
        hidden = int(self.faults.get("hide_status_reads") or 0)
        values: list[Any] = []
        for signature in params[0] if params else []:
            record = self.transactions.get(str(signature))
            if record is None or hidden > 0:
                values.append(None)
                continue
            values.append({"slot": record["slot"], "confirmations": None, "err": record["err"], "confirmationStatus": "confirmed"})
        if hidden > 0:
            self.faults["hide_status_reads"] = hidden - 1
        return {"result": {"context": self._context(), "value": values}}

    def _rpc_getTransaction(self, params: list[Any]) -> dict[str, Any]:
        record = self.transactions.get(str(params[0]))
        if record is None:
            return {"result": None}
        return {"result": {"slot": record["slot"], "blockTime": None, "meta": dict(record["meta"]), "transaction": {"signatures": [str(params[0])], "message": {"accountKeys": list(record["account_keys"])}}}}

    # --- execution ------------------------------------------------------------------------------------------

    def _execute(self, transaction: Any, *, apply: bool, verify_signatures: bool) -> None:
        message = transaction.message
        keys = [str(key) for key in message.account_keys]
        header = message.header
        signers = set(keys[: int(header.num_required_signatures)])
        if verify_signatures and not all(transaction.verify_with_results()):
            raise _Failure("SignatureFailure", "a signature does not verify")
        blockhash = str(message.recent_blockhash)
        if self._blockhashes.get(blockhash, -1) < self.block_height:
            raise _Failure("BlockhashNotFound", "blockhash not found or expired")
        lamports = dict(self.lamports)
        tokens = {address: dict(account) for address, account in self.token_accounts.items()}
        fee_payer = keys[0]
        fee = self.lamports_per_signature * int(header.num_required_signatures)
        if lamports.get(fee_payer, 0) < fee:
            raise _Failure("InsufficientFundsForFee", "fee payer cannot pay the fee")
        pre_balances = [lamports.get(key, 0) for key in keys]
        pre_tokens = _token_balances(keys, tokens, self.mints)
        lamports[fee_payer] -= fee
        payments: list[dict[str, Any]] = []
        for index, instruction in enumerate(message.instructions):
            program = keys[int(instruction.program_id_index)]
            accounts = [keys[int(position)] for position in bytes(instruction.accounts)]
            data = bytes(instruction.data)
            if program == SYSTEM_PROGRAM:
                if len(data) != 12 or int.from_bytes(data[:4], "little") != 2 or len(accounts) != 2:
                    raise _Failure({"InstructionError": [index, "InvalidInstructionData"]}, "only the System transfer is executed")
                source, destination = accounts
                amount = int.from_bytes(data[4:12], "little")
                if source not in signers:
                    raise _Failure({"InstructionError": [index, "MissingRequiredSignature"]}, "transfer source did not sign")
                if lamports.get(source, 0) < amount:
                    raise _Failure({"InstructionError": [index, {"Custom": 1}]}, "insufficient lamports")
                lamports[source] -= amount
                lamports[destination] = lamports.get(destination, 0) + amount
                for address in (source, destination):
                    if address not in tokens and address not in self.mints and 0 < lamports[address] < rent_minimum(0):
                        raise _Failure({"InsufficientFundsForRent": {"account_index": keys.index(address)}}, "an account would be left below rent exemption")
                payments.append({"asset": "SOL", "amount_atomic": amount, "to_owner": destination, "from_owner": source})
            elif program == TOKEN_PROGRAM:
                if len(data) != 10 or data[0] != 12 or len(accounts) != 4:
                    raise _Failure({"InstructionError": [index, "InvalidInstructionData"]}, "only SPL TransferChecked is executed")
                source, mint, destination, owner = accounts
                amount = int.from_bytes(data[1:9], "little")
                decimals = data[9]
                source_account, destination_account = tokens.get(source), tokens.get(destination)
                if source_account is None or destination_account is None:
                    raise _Failure({"InstructionError": [index, "InvalidAccountData"]}, "a token account does not exist")
                if mint not in self.mints or source_account["mint"] != mint or destination_account["mint"] != mint:
                    raise _Failure({"InstructionError": [index, {"Custom": 3}]}, "mint mismatch")
                if int(self.mints[mint]["decimals"]) != int(decimals):
                    raise _Failure({"InstructionError": [index, {"Custom": 18}]}, "decimals mismatch")
                if owner not in signers or source_account["owner"] != owner:
                    raise _Failure({"InstructionError": [index, {"Custom": 4}]}, "owner mismatch or missing signature")
                if source_account["frozen"] or destination_account["frozen"]:
                    raise _Failure({"InstructionError": [index, {"Custom": 17}]}, "account frozen")
                if source_account["amount"] < amount:
                    raise _Failure({"InstructionError": [index, {"Custom": 1}]}, "insufficient funds")
                source_account["amount"] -= amount
                destination_account["amount"] += amount
                payments.append({"asset": "SPL:" + mint, "amount_atomic": amount, "to_owner": destination_account["owner"], "from_owner": owner, "mint": mint})
            else:
                raise _Failure({"InstructionError": [index, "UnsupportedProgramId"]}, f"program {program} is not executed by this simulation")
        if lamports.get(fee_payer, 0) and lamports[fee_payer] < rent_minimum(0):
            raise _Failure({"InsufficientFundsForRent": {"account_index": 0}}, "fee payer would be left below rent exemption")
        if not apply:
            return
        signature = str(transaction.signatures[0])
        self.lamports = lamports
        self.token_accounts = tokens
        self.transactions[signature] = {
            "slot": self.slot, "err": None, "fee": fee, "fee_payer": fee_payer, "account_keys": keys, "payments": payments,
            "meta": {
                "err": None, "fee": fee, "preBalances": pre_balances, "postBalances": [lamports.get(key, 0) for key in keys],
                "preTokenBalances": pre_tokens, "postTokenBalances": _token_balances(keys, tokens, self.mints), "logMessages": ["SIMULATION: executed"],
            },
        }


_DROP = object()


def _ui(amount: int, decimals: int) -> str:
    text = str(int(amount)).rjust(decimals + 1, "0")
    whole, fraction = text[:-decimals] if decimals else text, text[-decimals:] if decimals else ""
    fraction = fraction.rstrip("0")
    return f"{whole}.{fraction}" if fraction else whole


def _token_balances(keys: list[str], tokens: dict[str, dict[str, Any]], mints: dict[str, dict[str, int]]) -> list[dict[str, Any]]:
    rows = []
    for index, key in enumerate(keys):
        account = tokens.get(key)
        if account is None:
            continue
        decimals = int(mints[account["mint"]]["decimals"])
        rows.append({"accountIndex": index, "mint": account["mint"], "owner": account["owner"], "programId": TOKEN_PROGRAM,
                     "uiTokenAmount": {"amount": str(int(account["amount"])), "decimals": decimals, "uiAmountString": _ui(int(account["amount"]), decimals)}})
    return rows


__all__ = ["MAINNET_GENESIS", "SYSTEM_PROGRAM", "TOKEN_PROGRAM", "USDC_MAINNET_MINT", "SimulatedSolanaNode", "rent_minimum"]
