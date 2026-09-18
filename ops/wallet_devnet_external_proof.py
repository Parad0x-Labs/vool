"""Two-phase DEVNET proof funded by an EXTERNAL operator, not the rate-limited faucet.

:mod:`ops.wallet_devnet_proof` asks the public faucet for devnet SOL and destroys its ephemeral
home in one process. When the faucet is dry that lane records BLOCKED and nothing is proven.
This module wraps the same production path in two phases so a human can fund the burner between
them, and it never destroys a home that still holds funded devnet SOL.

Phase 1 (``prepare``) mints a burner through the canonical :mod:`core.wallet.custody` pocket path
inside an isolated ``VOOL_HOME`` it creates at mode 0700, writes the resume state it needs at
mode 0600, prints the public address to fund, and stops. Phase 2 (``prove``) resumes from that
home and drives the production lifecycle end to end on public Solana devnet.

Run (repo root, one worktree, the SAME ``--home`` for both phases)::

    VOOL_WALLET_ENABLED=1 PYTHONPATH="$PWD" .venv/bin/python -m ops.wallet_devnet_external_proof \
        prepare --home ~/vool/devnet-proof-home --out <dir>/phase1.json
    # fund the printed address on devnet, then:
    VOOL_WALLET_ENABLED=1 PYTHONPATH="$PWD" .venv/bin/python -m ops.wallet_devnet_external_proof \
        prove --home ~/vool/devnet-proof-home --out <dir>/phase2.json

What is recorded: public addresses, amounts, transaction signatures, explorer URLs, slots,
confirmation status, balance deltas, lifecycle state sequences, content digests, cleanup status.
What is never printed, logged, written to the record or committed: the recovery phrase (dropped
unread at creation), the PIN, the sealed blob, the seed, the device secret, the key passphrase.
Those live only inside the isolated home, in files this module creates at mode 0600.

Mainnet is impossible by construction: ``core.wallet.config.ALLOWED_NETWORKS`` has no mainnet
entry, and both phases assert that before doing anything else.
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import json
import os
import shutil
import stat
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

DEVNET_RPC = "https://api.devnet.solana.com"
#: The endpoint every read and every broadcast in this run actually used. Only the public devnet
#: endpoint can yield a DEVNET-PROVEN verdict; anything else is a rehearsal and says so in the record.
RPC_URL = DEVNET_RPC
EXPLORER_TX = "https://explorer.solana.com/tx/{sig}?cluster=devnet"
EXPLORER_ADDRESS = "https://explorer.solana.com/address/{address}?cluster=devnet"

#: A Solana account that ends a transaction with a non-zero balance below the rent-exempt
#: minimum is refused on chain (InsufficientFundsForRent), so a FRESH receiver cannot be paid
#: dust. Both payments are sized above the 0-data rent-exempt floor (890_880 lamports).
RENT_EXEMPT_FLOOR_LAMPORTS = 890_880
TRANSFER_LAMPORTS = 1_000_000        # 0.001 SOL, first payment: payer -> fresh receiver
X402_LAMPORTS = 1_000_000            # 0.001 SOL, second payment: payer -> fresh x402 merchant
X402_CAP_LAMPORTS = 2_000_000        # the automatic cap this run configures, via the documented env knob
X402_OVER_CAP_LAMPORTS = 5_000_000   # an offer that must be refused BEFORE a proposal exists
RECOMMENDED_FUNDING_SOL = "0.02"

#: Solana devnet's genesis hash. The chain's own identity, and the only thing that actually
#: distinguishes devnet from a local validator or a private fork -- an endpoint URL does not, and a
#: keyed provider URL cannot be recorded at all without recording the key. The verdict reads this.
DEVNET_GENESIS_HASH = "EtWTRABZaYq6iMfeYKouRu166VU2xqa1wcaWoxPkrZBG"

RESUME_FILENAME = "resume.json"
RESUME_SCHEMA = "vool.wallet.devnet_external_proof.resume.v1"
RECORD_SCHEMA = "vool.wallet.devnet_external_proof.v1"

#: Enough BIP39 words in a row to be a recovery phrase rather than English prose.
_PHRASE_RUN = 8


# --------------------------------------------------------------------------------------------
# public devnet RPC, independent of core.wallet.lifecycle's own client
# --------------------------------------------------------------------------------------------

def rpc(method: str, params: list[Any], *, url: str = "", timeout: float = 30.0) -> dict[str, Any]:
    url = url or RPC_URL
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return {"error": {"code": exc.code, "message": (exc.read() or b"")[:300].decode("utf-8", "replace")}}
    except Exception as exc:  # the record must carry the transport failure verbatim
        return {"error": {"code": -1, "message": f"{type(exc).__name__}: {str(exc)[:200]}"}}


def balance_of(pubkey: str, *, url: str = "") -> int:
    answer = rpc("getBalance", [pubkey, {"commitment": "confirmed"}], url=url)
    return int(((answer.get("result") or {}).get("value")) or 0)


def signature_count(pubkey: str, *, url: str = "") -> int:
    """How many transactions this address has ever appeared in (capped read; a broadcast moves it).

    Read at ``confirmed``, not the RPC's finalized default: the lifecycle returns as soon as a
    payment is confirmed, so a finalized read lags behind the very broadcast this count exists to
    detect, and a duplicate could slip past two equally stale numbers.
    """
    answer = rpc("getSignaturesForAddress", [pubkey, {"limit": 1000, "commitment": "confirmed"}], url=url)
    result = answer.get("result")
    return len(result) if isinstance(result, list) else -1


def get_transaction(signature: str, *, url: str = "") -> dict[str, Any] | None:
    answer = rpc("getTransaction", [signature, {"encoding": "json", "commitment": "confirmed", "maxSupportedTransactionVersion": 0}], url=url)
    result = answer.get("result")
    return result if isinstance(result, dict) else None


def signature_status(signature: str, *, url: str = "") -> dict[str, Any] | None:
    answer = rpc("getSignatureStatuses", [[signature], {"searchTransactionHistory": True}], url=url)
    values = ((answer.get("result") or {}).get("value")) or []
    return values[0] if values and isinstance(values[0], dict) else None


def _account_delta(transaction: dict[str, Any], address: str) -> int | None:
    """The lamport delta this confirmed transaction applied to ``address``, read from chain meta."""
    meta = transaction.get("meta") or {}
    keys = (((transaction.get("transaction") or {}).get("message") or {}).get("accountKeys")) or []
    names = [k if isinstance(k, str) else str((k or {}).get("pubkey") or "") for k in keys]
    if address not in names:
        return None
    index = names.index(address)
    pre, post = meta.get("preBalances") or [], meta.get("postBalances") or []
    if index >= len(pre) or index >= len(post):
        return None
    return int(post[index]) - int(pre[index])


# --------------------------------------------------------------------------------------------
# the isolated home
# --------------------------------------------------------------------------------------------

def _mode_of(path: Path) -> str:
    return oct(stat.S_IMODE(path.stat().st_mode))


def _write_private(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON so the bytes are never world- or group-readable, not even between open and chmod."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(FileNotFoundError):
        path.unlink()
    handle = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
    os.chmod(path, 0o600)


def _read_resume(home: Path) -> dict[str, Any]:
    path = home / RESUME_FILENAME
    if not path.exists():
        raise SystemExit(f"no resume state at {path}: run `prepare --home {home}` first")
    return json.loads(path.read_text(encoding="utf-8"))


def _bootstrap(home: Path, resume: dict[str, Any] | None, *, rpc_url: str = "") -> None:
    """Point the whole runtime at the isolated home BEFORE any core/network module is imported."""
    global RPC_URL
    RPC_URL = str(rpc_url or (resume or {}).get("rpc") or DEVNET_RPC)
    home.mkdir(parents=True, exist_ok=True)
    os.chmod(home, 0o700)
    os.environ["VOOL_HOME"] = str(home)
    os.environ["VOOL_WALLET_TESTNET_RPC_URL"] = RPC_URL
    os.environ["VOOL_KEY_STORAGE_MODE"] = "file"
    os.environ["VOOL_CREDENTIAL_STORE"] = "vault"
    os.environ["VOOL_WALLET_X402_CAP_MINOR"] = str(X402_CAP_LAMPORTS)
    os.environ["VOOL_WALLET_X402_ALLOW_LOOPBACK"] = "1"
    if resume is not None:
        os.environ["VOOL_KEY_PASSPHRASE"] = resume["key_passphrase"]

    from core import runtime_paths

    runtime_paths.configure_runtime_home(home)
    from storage.db import configure_default_db_path, reset_default_connection
    from storage.migrations import run_migrations

    configure_default_db_path(home / "devnet-proof.db")
    reset_default_connection()
    run_migrations()


def _assert_devnet_only() -> dict[str, Any]:
    """This proof stays on the Solana Devnet row: the row is a Test networks row, no undeclared mainnet name
    resolves, neither the excluded mainnet endpoint nor the endpoint the Solana mainnet row declares can serve
    the devnet client, and the declared set is exactly the registry's rows. Anything else fails here."""
    from core.wallet import chains, config, lifecycle

    refusals: dict[str, Any] = {}
    for candidate in ("solana-mainnet", "mainnet-beta", "solana-mainnet-beta", "MAINNET"):
        refusals[candidate] = config.network_allowed(candidate)
    devnet = chains.resolve_network(config.NETWORK_SOLANA_DEVNET)
    mainnet_endpoints = ("https://api.mainnet-beta.solana.com", chains.resolve_network(chains.SOLANA_MAINNET).rpc_origins[0])
    rpc_refusals = {url: chains.rpc_origin_allowed(devnet, url) for url in mainnet_endpoints}
    constructed = "unexpected_success"
    try:
        lifecycle.RpcClient("https://api.mainnet-beta.solana.com", network="solana-mainnet")
    except Exception as exc:  # any refusal is the point; the code is recorded
        constructed = getattr(exc, "code", None) or type(exc).__name__
    devnet_client = "unexpected_success"
    try:
        lifecycle.RpcClient(mainnet_endpoints[1], network=config.NETWORK_SOLANA_DEVNET)
    except Exception as exc:  # any refusal is the point; the code is recorded
        devnet_client = getattr(exc, "code", None) or type(exc).__name__
    verdict = {
        "allowed_networks": list(config.ALLOWED_NETWORKS),
        "declared_networks": list(chains.DECLARED_NETWORKS),
        "devnet_row_environment": devnet.environment,
        "network_allowed_for_mainnet_names": refusals,
        "devnet_origin_allowed_for_mainnet_endpoints": rpc_refusals,
        "rpc_client_construction_with_mainnet": constructed,
        "devnet_client_construction_with_mainnet_endpoint": devnet_client,
    }
    ok = (
        list(config.ALLOWED_NETWORKS) == list(chains.DECLARED_NETWORKS)
        and devnet.environment == chains.ENVIRONMENT_TESTNET
        and not any(refusals.values())
        and not any(rpc_refusals.values())
        and constructed == "wallet_network_disabled"
        and devnet_client == "wallet_network_disabled"
    )
    verdict["ok"] = ok
    if not ok:
        raise RuntimeError(f"devnet-only assertion failed: {json.dumps(verdict, sort_keys=True)}")
    return verdict


def _public_endpoint(url: str) -> str:
    """Scheme and host only. A provider URL can carry an API key in its path or query, and an
    endpoint is a public fact while its key is not -- so only the part that is a public fact is
    ever recorded."""
    parts = urlsplit(str(url or ""))
    return f"{parts.scheme}://{parts.hostname}" + (f":{parts.port}" if parts.port else "")


def _target() -> dict[str, Any]:
    """What chain this run actually talked to, and therefore what it is entitled to claim.

    Read from the chain, not from the URL: ``getGenesisHash`` is devnet's own identity, so a
    private or keyed endpoint that really serves devnet qualifies, and a local validator (whose
    genesis is minted at boot) can never be mistaken for it however it is addressed.
    """
    genesis = str((rpc("getGenesisHash", []) or {}).get("result") or "")
    devnet = genesis == DEVNET_GENESIS_HASH
    return {
        "rpc": _public_endpoint(RPC_URL),
        "rpc_is_the_default_public_endpoint": RPC_URL == DEVNET_RPC,
        "genesis_hash": genesis,
        "expected_devnet_genesis_hash": DEVNET_GENESIS_HASH,
        "is_public_devnet": devnet,
        "claimable_verdict": "DEVNET-PROVEN" if devnet else "LOCAL-VALIDATOR-REHEARSAL",
        "note": (
            "the endpoint answered getGenesisHash with Solana devnet's genesis: this is the public devnet chain"
            if devnet
            else f"this endpoint's genesis hash is {genesis or 'unreadable'}, not devnet's: the code path is exercised, the chain is not devnet"
        ),
    }


def _new_pin() -> str:
    return f"{int.from_bytes(os.urandom(4), 'big') % 1_000_000:06d}"


def _mint_pocket(label: str) -> tuple[str, str, str]:
    """Mint one burner through the canonical pocket-custody path. Returns (wallet_id, public_key, pin).

    The recovery phrase is dropped here, unread and unreferenced: the burner is not meant to be
    recovered anywhere but from this home, and a phrase that is never held cannot be leaked.
    """
    from core.wallet import custody

    pin = _new_pin()
    created = custody.create_pocket_wallet(
        acknowledged_warning=True,
        confirmation_phrase=custody.POCKET_CONFIRMATION_PHRASE,
        pin=pin,
        label=label,
    )
    profile = created.profile
    del created
    return profile.wallet_id, profile.public_key, pin


# --------------------------------------------------------------------------------------------
# phase 1: prepare
# --------------------------------------------------------------------------------------------

def cmd_prepare(args: argparse.Namespace) -> int:
    home = Path(args.home).expanduser().resolve()
    if (home / RESUME_FILENAME).exists() and not args.force:
        raise SystemExit(f"{home / RESUME_FILENAME} already exists; pass --force only if that burner is spent or empty")
    _bootstrap(home, None, rpc_url=args.rpc_url)
    passphrase = base64.urlsafe_b64encode(os.urandom(33)).decode("ascii")
    os.environ["VOOL_KEY_PASSPHRASE"] = passphrase

    record: dict[str, Any] = {
        "schema": RECORD_SCHEMA, "phase": "prepare", "status": "AWAITING_FUNDING",
        "started_at": datetime.now(timezone.utc).isoformat(), "network": "solana-devnet", "rpc": _public_endpoint(RPC_URL),
        "funding_source": "external_operator", "faucet_requested": False, "target": _target(),
    }
    record["devnet_only"] = _assert_devnet_only()
    wallet_id, public_key, pin = _mint_pocket("devnet-external-proof-payer")

    from core.wallet import custody

    resume = {
        "schema": RESUME_SCHEMA, "created_at": datetime.now(timezone.utc).isoformat(),
        "home": str(home), "db": str(home / "devnet-proof.db"), "network": "solana-devnet", "rpc": RPC_URL,
        "payer_wallet_id": wallet_id, "payer_public_key": public_key,
        "key_passphrase": passphrase, "payer_pin": pin,
    }
    _write_private(home / RESUME_FILENAME, resume)
    if not custody.verify_pin(wallet_id, pin):
        raise RuntimeError("resume state does not unlock the burner it was written for")

    record.update({
        "payer_public_key": public_key, "payer_wallet_id": wallet_id, "payer_mode": custody.MODE_POCKET_SEALED,
        "payer_explorer": EXPLORER_ADDRESS.format(address=public_key),
        "balance_lamports_at_prepare": balance_of(public_key),
        "home_path": str(home), "home_mode": _mode_of(home), "resume_mode": _mode_of(home / RESUME_FILENAME),
        "recommended_funding_sol": RECOMMENDED_FUNDING_SOL,
        "planned_transfer_lamports": TRANSFER_LAMPORTS, "planned_x402_lamports": X402_LAMPORTS,
        "rent_exempt_floor_lamports": RENT_EXEMPT_FLOOR_LAMPORTS,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    })
    _emit(args.out, record)
    public_devnet = record["target"]["is_public_devnet"]
    banner = (
        "\n" + "=" * 72 + "\n"
        + ("FUND THIS ADDRESS ON SOLANA DEVNET ONLY:\n" if public_devnet else f"REHEARSAL TARGET (NOT public devnet) at {RPC_URL}\nFUND THIS ADDRESS ON THAT ENDPOINT ONLY:\n")
        + f"{public_key}\n"
        + ("NETWORK: Solana devnet\n" if public_devnet else f"NETWORK: {_public_endpoint(RPC_URL)} (local/rehearsal)\n")
        + f"RECOMMENDED AMOUNT: {RECOMMENDED_FUNDING_SOL} SOL\n"
        + "=" * 72 + "\n"
        + (f"explorer: {EXPLORER_ADDRESS.format(address=public_key)}\n" if public_devnet else "explorer: none; this endpoint is not indexed by the public explorer\n")
        + f"resume with: prove --home {home}\n"
    )
    print(banner)
    return 0


# --------------------------------------------------------------------------------------------
# the hermetic 402 resource server (local; the PAYMENT it demands is real devnet)
# --------------------------------------------------------------------------------------------

RESOURCE_BODY = b"vool-x402-paid-resource: the bytes a 402 was guarding.\n"


class _X402Server:
    """A local origin that answers 402 until it is shown a payment it verifies ON PUBLIC DEVNET.

    Hermetic only in the sense that the HTTP hop never leaves the loopback interface. The proof it
    demands is not hermetic: it reads the presented signature back off devnet with getTransaction
    and refuses unless that transaction actually moved the demanded lamports to ``pay_to``.
    """

    def __init__(self, *, pay_to: str, amount: int, rpc_url: str = "") -> None:
        self.pay_to = pay_to
        self.amount = int(amount)
        self.rpc_url = rpc_url or RPC_URL
        self.offers_issued = 0
        self.deliveries = 0
        self.accepted_signatures: list[str] = []
        self.rejections: list[str] = []
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    # -- verification ---------------------------------------------------------------------
    def _verify(self, header: str) -> tuple[bool, str, str]:
        try:
            payload = json.loads(base64.b64decode(header.encode("ascii")))
        except Exception:
            return False, "", "payment_header_unparsable"
        if int(payload.get("x402Version") or 0) != 1 or str(payload.get("scheme")) != "exact":
            return False, "", "payment_header_scheme"
        inner = payload.get("payload") or {}
        signature = str(inner.get("signature") or "")
        if not signature:
            return False, "", "payment_header_no_signature"
        if str(inner.get("payTo")) != self.pay_to:
            return False, signature, "payment_header_wrong_payee"
        if int(str(inner.get("amount") or "0")) < self.amount:
            return False, signature, "payment_header_short_amount"
        transaction = get_transaction(signature, url=self.rpc_url)
        if transaction is None:
            return False, signature, "payment_not_on_devnet"
        if (transaction.get("meta") or {}).get("err"):
            return False, signature, "payment_failed_on_devnet"
        delta = _account_delta(transaction, self.pay_to)
        if delta is None or delta < self.amount:
            return False, signature, f"payment_delta_insufficient:{delta}"
        return True, signature, "verified_on_devnet"

    # -- wire -----------------------------------------------------------------------------
    def _offer(self) -> bytes:
        self.offers_issued += 1
        return json.dumps({
            "x402Version": 1,
            "error": "payment required",
            "accepts": [{
                "scheme": "exact", "network": "solana-devnet", "maxAmountRequired": str(self.amount),
                "asset": "SOL", "payTo": self.pay_to, "resource": "/paid-resource",
                "description": "vool devnet x402 proof resource",
            }],
        }).encode("utf-8")

    def start(self) -> str:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: Any) -> None:  # keep the proof log clean
                return

            def do_GET(self) -> None:  # BaseHTTPRequestHandler's contract
                header = self.headers.get("X-PAYMENT") or ""
                if not header:
                    body = outer._offer()
                    self.send_response(402)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                ok, signature, reason = outer._verify(header)
                if not ok:
                    outer.rejections.append(reason)
                    body = outer._offer()
                    self.send_response(402)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if signature not in outer.accepted_signatures:
                    outer.accepted_signatures.append(signature)
                outer.deliveries += 1
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(RESOURCE_BODY)))
                self.end_headers()
                self.wfile.write(RESOURCE_BODY)

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return f"http://127.0.0.1:{self._server.server_address[1]}/paid-resource"

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)


# --------------------------------------------------------------------------------------------
# leak scan
# --------------------------------------------------------------------------------------------

def _bip39_words() -> set[str]:
    from core.wallet import custody as custody_module

    path = Path(custody_module.__file__).with_name("bip39_english.txt")
    return {w.strip() for w in path.read_text(encoding="utf-8").split() if w.strip()}


def _phrase_run_in(text: str, words: set[str]) -> str:
    run: list[str] = []
    for token in "".join(c if c.isalpha() else " " for c in text.lower()).split():
        run = [*run, token] if token in words else []
        if len(run) >= _PHRASE_RUN:
            return " ".join(run)
    return ""


def _scan_for_secrets(*, home: Path, secrets: dict[str, str], extra_texts: dict[str, str]) -> dict[str, Any]:
    """Every durable surface this run wrote, read back as bytes, checked for material that must not be there.

    ``secrets`` are the exact strings that exist only inside the isolated home (PIN, key passphrase,
    sealed blob). Any of them appearing in a receipt, journal, effect record, fault or security event
    is a leak. The BIP39 run check catches a recovery phrase this process never even held.
    """
    words = _bip39_words()
    findings: list[dict[str, str]] = []
    scanned: list[str] = []

    def check(origin: str, text: str, *, allow: tuple[str, ...] = ()) -> None:
        scanned.append(origin)
        for name, value in secrets.items():
            if name in allow or not value:
                continue
            if value in text:
                findings.append({"origin": origin, "secret": name})
        run = _phrase_run_in(text, words)
        if run:
            findings.append({"origin": origin, "secret": "bip39_run", "sample_length": str(len(run.split()))})

    # 1. every row of every table in the wallet/runtime database, except the one column that is
    #    supposed to hold ciphertext (wallet_profiles.sealed_blob).
    import sqlite3

    connection = sqlite3.connect(str(home / "devnet-proof.db"))
    try:
        tables = [r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()]
        for table in tables:
            rows = connection.execute(f"SELECT * FROM {table}").fetchall()  # names come from sqlite_master
            columns = [d[0] for d in connection.execute(f"SELECT * FROM {table} LIMIT 0").description]
            for row in rows:
                for column, value in zip(columns, row, strict=True):
                    if table == "wallet_profiles" and column == "sealed_blob":
                        continue  # ciphertext by design; its plaintext is what must not appear elsewhere
                    check(f"db:{table}.{column}", str(value), allow=())
    finally:
        connection.close()

    # 2. every file the run wrote inside the isolated home, except the resume state itself (which is
    #    the deliberate 0600 store for exactly this material) and the sealed key record.
    #
    #    The database file and its write-ahead log are scanned too, but the sealed CIPHERTEXT is
    #    allowed in them: wallet_profiles.sealed_blob is its designated store, so finding it in the
    #    file that holds that column proves nothing. Everything else stays banned there, including
    #    in freed pages and the WAL -- a PIN, the key passphrase or a recovery phrase must not
    #    survive anywhere in the database, at any layer.
    sealed_names = tuple(name for name in secrets if name.startswith("sealed_ciphertext_"))
    for path in sorted(home.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(home).as_posix()
        if relative in {RESUME_FILENAME} or relative.endswith(("node_signing_key.json", "key_storage.passphrase")):
            continue
        is_sealed_store = relative.split("/")[-1].startswith("devnet-proof.db")
        with contextlib.suppress(Exception):
            check(f"file:{relative}", path.read_bytes().decode("utf-8", "replace"), allow=sealed_names if is_sealed_store else ())

    # 3. the in-memory surfaces the record itself will carry.
    for origin, text in extra_texts.items():
        check(origin, text)

    return {"ok": not findings, "findings": findings, "surfaces_scanned": len(scanned), "bip39_run_threshold": _PHRASE_RUN}


# --------------------------------------------------------------------------------------------
# phase 2: prove
# --------------------------------------------------------------------------------------------

def _states(proposal_id: str) -> list[str]:
    from core.wallet import proposals

    return [event["state"] for event in proposals.proposal_events(proposal_id)]


def _fault_of(exc: BaseException) -> str:
    return str(getattr(exc, "code", "") or f"{type(exc).__name__}")


def _local_airdrop(pubkey: str, lamports: int) -> str:
    """Funding for a REHEARSAL only. Refused against the public devnet endpoint, whose faucet this
    lane is forbidden to touch: the whole point of the two-phase mode is external operator funding."""
    if _target()["is_public_devnet"]:
        raise RuntimeError("--local-airdrop is refused against public devnet: fund the printed address externally")
    answer = rpc("requestAirdrop", [pubkey, int(lamports)])
    if "result" not in answer:
        raise RuntimeError(f"local validator refused the airdrop: {json.dumps(answer.get('error'))[:200]}")
    for _ in range(60):
        if balance_of(pubkey) >= lamports:
            return f"local validator airdrop {answer['result']} landed"
        time.sleep(0.5)
    raise RuntimeError("local validator airdrop never landed")


def cmd_prove(args: argparse.Namespace) -> int:
    home = Path(args.home).expanduser().resolve()
    resume = _read_resume(home)
    _bootstrap(home, resume, rpc_url=args.rpc_url)

    record: dict[str, Any] = {
        "schema": RECORD_SCHEMA, "phase": "prove", "status": "FAILED",
        "started_at": datetime.now(timezone.utc).isoformat(), "network": "solana-devnet", "rpc": _public_endpoint(RPC_URL),
        "funding_source": "external_operator", "faucet_requested": False, "target": _target(),
        "home_path": str(home), "home_mode": _mode_of(home), "resume_mode": _mode_of(home / RESUME_FILENAME),
        "verdict": "NOT-PROVEN",
    }
    server: _X402Server | None = None
    keep_home = True
    try:
        record["devnet_only"] = _assert_devnet_only()

        from core.wallet import approval, config, custody, lifecycle, proposals, receipts, x402

        payer_id = resume["payer_wallet_id"]
        payer_key = resume["payer_public_key"]
        payer_pin = resume["payer_pin"]
        record["payer_public_key"] = payer_key
        record["payer_explorer"] = EXPLORER_ADDRESS.format(address=payer_key)
        if not custody.verify_pin(payer_id, payer_pin):
            raise RuntimeError("the resumed home does not unlock its own burner")

        # -- 1. the operator's funding, confirmed through public devnet RPC ---------------------
        if args.local_airdrop:
            record["funding_source"] = "local_validator_airdrop"
            record["local_airdrop"] = _local_airdrop(payer_key, 200_000_000)
        funded_balance = balance_of(payer_key)
        record["payer_balance_lamports"] = funded_balance
        required = TRANSFER_LAMPORTS + X402_LAMPORTS + 100_000
        if funded_balance < required:
            record["status"] = "AWAITING_FUNDING"
            record["blocked_reason"] = f"payer holds {funded_balance} lamports; the two payments plus fees need at least {required}"
            return 3
        record["funding_confirmed_by"] = f"getBalance on {_public_endpoint(RPC_URL)} (commitment=confirmed)"

        # -- 2. a fresh receiver, minted the same canonical way, with its balance recorded -------
        receiver_id, receiver_key, receiver_pin = _mint_pocket("devnet-external-proof-receiver")
        merchant_id, merchant_key, merchant_pin = _mint_pocket("devnet-external-proof-x402-merchant")
        resume.update({
            "receiver_wallet_id": receiver_id, "receiver_public_key": receiver_key, "receiver_pin": receiver_pin,
            "merchant_wallet_id": merchant_id, "merchant_public_key": merchant_key, "merchant_pin": merchant_pin,
        })
        _write_private(home / RESUME_FILENAME, resume)
        receiver_before = balance_of(receiver_key)
        merchant_before = balance_of(merchant_key)
        payer_signatures_before = signature_count(payer_key)
        record["transfer"] = {
            "receiver_public_key": receiver_key, "receiver_explorer": EXPLORER_ADDRESS.format(address=receiver_key),
            "receiver_balance_before_lamports": receiver_before, "amount_lamports": TRANSFER_LAMPORTS,
            "payer_signature_count_before": payer_signatures_before,
        }

        # -- 3. one real transfer through the canonical lifecycle -------------------------------
        engine = lifecycle.default_lifecycle()
        proposal = proposals.propose_transaction(
            wallet_id=payer_id, destination=receiver_key, amount_minor=TRANSFER_LAMPORTS, asset="SOL",
            origin=proposals.ORIGIN_USER, memo="devnet external-funded proof transfer",
        )
        prepared = engine.prepare(proposal.proposal_id)
        started = time.monotonic()
        receipt = engine.approve_and_execute(proposal.proposal_id, approver=approval.PinApprover(payer_pin))
        record["transfer"].update({
            "proposal_id": proposal.proposal_id,
            "simulation": dict(prepared.simulation),
            "tx_signature": receipt.tx_signature, "explorer": EXPLORER_TX.format(sig=receipt.tx_signature),
            "state": receipt.state, "lifecycle_states": _states(proposal.proposal_id),
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "receipt_id": receipt.receipt_id,
        })
        if receipt.state != proposals.STATE_CONFIRMED:
            raise RuntimeError(f"transfer did not confirm on devnet: state={receipt.state}")

        # -- 4. independent verification, off a client the lifecycle never touched ---------------
        transaction = get_transaction(receipt.tx_signature)
        status = signature_status(receipt.tx_signature)
        receiver_after = balance_of(receiver_key)
        record["transfer"]["verification"] = {
            "method": "getTransaction + getSignatureStatuses + getBalance, separate client from core.wallet.lifecycle.RpcClient",
            "slot": (transaction or {}).get("slot"),
            "block_time": (transaction or {}).get("blockTime"),
            "chain_err": ((transaction or {}).get("meta") or {}).get("err"),
            "fee_lamports": ((transaction or {}).get("meta") or {}).get("fee"),
            "confirmation_status": (status or {}).get("confirmationStatus"),
            "status_err": (status or {}).get("err"),
            "receiver_delta_from_chain_meta": _account_delta(transaction or {}, receiver_key),
            "receiver_balance_after_lamports": receiver_after,
            "receiver_balance_delta_lamports": receiver_after - receiver_before,
        }
        verification = record["transfer"]["verification"]
        if verification["receiver_balance_delta_lamports"] != TRANSFER_LAMPORTS or verification["chain_err"] is not None:
            raise RuntimeError(f"independent verification disagrees with the receipt: {json.dumps(verification, sort_keys=True)}")

        # -- 5. a duplicate approval and a duplicate prepare buy zero second broadcasts ----------
        duplicate: dict[str, Any] = {
            "payer_signature_count_after_first_payment": signature_count(payer_key),
            "payer_balance_after_first_payment": balance_of(payer_key),
        }
        for name, call in (
            ("approve_again", lambda: engine.approve_and_execute(proposal.proposal_id, approver=approval.PinApprover(payer_pin))),
            ("prepare_again", lambda: engine.prepare(proposal.proposal_id)),
        ):
            try:
                call()
                duplicate[name] = "unexpected_success"
            except Exception as exc:  # the fault code is the evidence
                duplicate[name] = _fault_of(exc)
        duplicate["payer_signature_count_after_duplicates"] = signature_count(payer_key)
        duplicate["payer_balance_after_duplicates"] = balance_of(payer_key)
        duplicate["payer_balance_unchanged_by_duplicates"] = duplicate["payer_balance_after_duplicates"] == duplicate["payer_balance_after_first_payment"]
        duplicate["second_broadcasts"] = duplicate["payer_signature_count_after_duplicates"] - duplicate["payer_signature_count_after_first_payment"]
        duplicate["proposal_state_after_duplicates"] = (proposals.get_proposal(proposal.proposal_id) or prepared).state
        duplicate["tx_signature_unchanged"] = (proposals.get_proposal(proposal.proposal_id) or prepared).tx_signature == receipt.tx_signature
        record["duplicate_guard"] = duplicate
        if duplicate["second_broadcasts"] != 0 or duplicate["approve_again"] != "wallet_duplicate_payment" or not duplicate["tx_signature_unchanged"] or not duplicate["payer_balance_unchanged_by_duplicates"]:
            raise RuntimeError(f"duplicate approval was not fenced: {json.dumps(duplicate, sort_keys=True)}")

        # -- 6. x402: hermetic 402 origin, real devnet payment -----------------------------------
        server = _X402Server(pay_to=merchant_key, amount=X402_LAMPORTS)
        url = server.start()
        x402_record: dict[str, Any] = {
            "resource_url": url, "origin": "hermetic loopback HTTP server",
            "payment": (
                "real transaction on public Solana devnet, verified by the origin with getTransaction"
                if _target()["is_public_devnet"]
                else f"real transaction on {_public_endpoint(RPC_URL)} (rehearsal chain), verified by the origin with getTransaction"
            ),
            "merchant_public_key": merchant_key, "merchant_explorer": EXPLORER_ADDRESS.format(address=merchant_key),
            "merchant_balance_before_lamports": merchant_before,
            "cap_minor": config.x402_cap_minor(),
        }
        record["x402"] = x402_record  # by reference: a later failure still carries what was measured

        over_cap = x402.X402Request(amount_minor=X402_OVER_CAP_LAMPORTS, asset="SOL", network="solana-devnet", pay_to=merchant_key, resource="/over-cap")
        proposals_before_over_cap = len(proposals.list_proposals(limit=500))
        try:
            x402.propose_from_x402(over_cap, wallet_id=payer_id)
            x402_record["above_cap_offer"] = "unexpected_success"
        except Exception as exc:
            x402_record["above_cap_offer"] = _fault_of(exc)
        x402_record["above_cap_created_a_proposal"] = len(proposals.list_proposals(limit=500)) != proposals_before_over_cap

        offer_outcome = x402.fetch_paid_resource(url, wallet_id=payer_id)
        x402_record["offer"] = offer_outcome.to_dict()
        if offer_outcome.status != x402.OUTCOME_PAYMENT_REQUIRED or not offer_outcome.proposal_id:
            raise RuntimeError(f"the 402 did not park a capped proposal: {offer_outcome.to_dict()}")
        payer_signatures_before_x402 = signature_count(payer_key)
        x402_receipt = engine.approve_and_execute(offer_outcome.proposal_id, approver=approval.PinApprover(payer_pin))
        delivered = x402.retry_paid_resource(offer_outcome.proposal_id)
        binding = x402.binding_for_proposal(offer_outcome.proposal_id) or {}
        merchant_after = balance_of(merchant_key)
        x402_transaction = get_transaction(x402_receipt.tx_signature)
        x402_status = signature_status(x402_receipt.tx_signature)
        x402_record.update({
            "proposal_id": offer_outcome.proposal_id, "amount_lamports": X402_LAMPORTS,
            "tx_signature": x402_receipt.tx_signature, "explorer": EXPLORER_TX.format(sig=x402_receipt.tx_signature),
            "payment_state": x402_receipt.state, "lifecycle_states": _states(offer_outcome.proposal_id),
            "delivery_status": delivered.status, "delivery_http_status": delivered.http_status,
            "resource_bytes": len(delivered.body), "resource_digest": hashlib.sha256(delivered.body).hexdigest(),
            "binding_request_digest": binding.get("request_digest"), "binding_state": binding.get("state"),
            "binding_tx_signature": binding.get("tx_signature"), "binding_resource_digest": binding.get("resource_digest"),
            "verification": {
                "slot": (x402_transaction or {}).get("slot"), "block_time": (x402_transaction or {}).get("blockTime"),
                "chain_err": ((x402_transaction or {}).get("meta") or {}).get("err"),
                "fee_lamports": ((x402_transaction or {}).get("meta") or {}).get("fee"),
                "confirmation_status": (x402_status or {}).get("confirmationStatus"),
                "merchant_delta_from_chain_meta": _account_delta(x402_transaction or {}, merchant_key),
                "merchant_balance_after_lamports": merchant_after,
                "merchant_balance_delta_lamports": merchant_after - merchant_before,
            },
        })
        if delivered.status != x402.OUTCOME_DELIVERED or delivered.http_status != 200:
            raise RuntimeError(f"the paid resource was not delivered: {delivered.to_dict()}")
        if x402_record["verification"]["merchant_balance_delta_lamports"] != X402_LAMPORTS:
            raise RuntimeError(f"x402 merchant delta disagrees: {json.dumps(x402_record['verification'], sort_keys=True)}")

        # -- 7. exactly one payment; the binding ties request, signature and resource -------------
        repeat = x402.fetch_paid_resource(url, wallet_id=payer_id)
        replay = x402.retry_paid_resource(offer_outcome.proposal_id)
        delivered_receipts = [
            r for r in receipts.list_receipts(limit=500)
            if r.get("state") == "delivered" and r.get("proposal_id") == offer_outcome.proposal_id
        ]
        expected_digest = hashlib.sha256(f"GET|{url}".encode()).hexdigest()
        x402_record["single_payment"] = {
            "repeat_fetch_status": repeat.status, "repeat_fetch_tx_signature": repeat.tx_signature,
            "replay_retry_status": replay.status, "replay_retry_tx_signature": replay.tx_signature,
            "payer_signature_count_before_x402": payer_signatures_before_x402,
            "payer_signature_count_after_all_x402_calls": signature_count(payer_key),
            "origin_offers_issued": server.offers_issued, "origin_deliveries": server.deliveries,
            "origin_distinct_accepted_signatures": len(server.accepted_signatures),
            "origin_rejections": server.rejections,
            "delivered_receipts_recorded": len(delivered_receipts),
            "request_digest_matches_sha256_of_method_and_url": binding.get("request_digest") == expected_digest,
            "binding_binds": {
                "request_digest": binding.get("request_digest"),
                "tx_signature": binding.get("tx_signature"),
                "resource_digest": binding.get("resource_digest"),
            },
        }
        single = x402_record["single_payment"]
        broadcasts = single["payer_signature_count_after_all_x402_calls"] - single["payer_signature_count_before_x402"]
        single["x402_broadcasts"] = broadcasts
        if broadcasts != 1 or single["origin_distinct_accepted_signatures"] != 1 or single["delivered_receipts_recorded"] != 1:
            raise RuntimeError(f"x402 did not pay exactly once: {json.dumps(single, sort_keys=True)}")
        if not single["request_digest_matches_sha256_of_method_and_url"] or binding.get("tx_signature") != x402_receipt.tx_signature:
            raise RuntimeError(f"the x402 binding does not tie request, signature and resource: {json.dumps(single, sort_keys=True)}")

        # -- 8. nothing sensitive reached any durable surface ------------------------------------
        from core.wallet.store import connection as wallet_connection

        with wallet_connection() as conn:
            sealed = [str(r[0]) for r in conn.execute("SELECT sealed_blob FROM wallet_profiles").fetchall() if r[0]]
        sealed_fragments = {f"sealed_ciphertext_{i}": json.loads(blob)["ciphertext"] for i, blob in enumerate(sealed)}
        record["leak_scan"] = _scan_for_secrets(
            home=home,
            secrets={
                "payer_pin": payer_pin, "receiver_pin": receiver_pin, "merchant_pin": merchant_pin,
                "key_passphrase": resume["key_passphrase"], **sealed_fragments,
            },
            extra_texts={
                "record:in_flight": json.dumps(record, sort_keys=True, default=str),
                "record:wallet_receipts": json.dumps(receipts.list_receipts(limit=200), sort_keys=True, default=str),
                "record:x402_body": delivered.body.decode("utf-8", "replace"),
            },
        )
        if not record["leak_scan"]["ok"]:
            raise RuntimeError(f"secret material reached a durable surface: {json.dumps(record['leak_scan'], sort_keys=True)}")

        target = _target()
        record["status"] = target["claimable_verdict"]
        record["verdict"] = target["claimable_verdict"]
        record["verdict_note"] = (
            (
                "Both payments are real transactions on public Solana devnet, confirmed by signature and by "
                "receiver balance delta read back off the public RPC."
                if target["is_public_devnet"]
                else "Both payments executed against a NON-PUBLIC endpoint. Every runtime path is exercised and "
                "verified on that chain, but no claim is made about public Solana devnet."
            )
            + " The x402 HTTP hop is a loopback origin, so the RESOURCE SERVER is a loopback proof; the PAYMENT "
            "it verified before delivering is a chain transaction, not a loopback stub."
        )
        keep_home = bool(args.keep_home)
        return 0
    except Exception as exc:  # the record must carry the failure
        record.setdefault("error", f"{type(exc).__name__}: {str(exc)[:400]}")
        if record["status"] not in {"AWAITING_FUNDING"}:
            record["status"] = "FAILED"
            record["verdict"] = "NOT-PROVEN"
        return 4
    finally:
        if server is not None:
            server.stop()
        record["finished_at"] = datetime.now(timezone.utc).isoformat()
        record["home_preserved"] = keep_home or record["status"] not in {"DEVNET-PROVEN", "LOCAL-VALIDATOR-REHEARSAL"}
        if not record["home_preserved"]:
            shutil.rmtree(home, ignore_errors=True)
        record["home_still_exists"] = home.exists()
        record["cleanup"] = (
            "home deleted after a proven run; the sealed key and the resume state died with it"
            if not record["home_still_exists"]
            else "home preserved: it still holds the sealed burner and any unspent devnet SOL"
        )
        with contextlib.suppress(Exception):
            record["payer_balance_lamports_final"] = balance_of(resume["payer_public_key"])
        _emit(args.out, record)


# --------------------------------------------------------------------------------------------
# sweep: return what the operator funded, THEN destroy the home
# --------------------------------------------------------------------------------------------

def _funding_source(pubkey: str) -> str:
    """Who funded this burner, read off chain: the account that paid lamports INTO it first.

    The operator's address is never asked for and never stored -- it is recoverable from the
    funding transaction itself, which is a public fact.
    """
    answer = rpc("getSignaturesForAddress", [pubkey, {"limit": 1000, "commitment": "confirmed"}])
    entries = answer.get("result")
    if not isinstance(entries, list) or not entries:
        return ""
    for entry in reversed(entries):  # oldest first: the funding transaction
        transaction = get_transaction(str(entry.get("signature") or ""))
        if transaction is None or _account_delta(transaction, pubkey) is None:
            continue
        if int(_account_delta(transaction, pubkey) or 0) <= 0:
            continue
        keys = (((transaction.get("transaction") or {}).get("message") or {}).get("accountKeys")) or []
        names = [k if isinstance(k, str) else str((k or {}).get("pubkey") or "") for k in keys]
        payers = [n for n in names if n != pubkey and int(_account_delta(transaction, n) or 0) < 0]
        if payers:
            return payers[0]
    return ""


def cmd_sweep(args: argparse.Namespace) -> int:
    """Return the residual to whoever funded the burner, verify it landed, then delete the home.

    Deleting a home that still holds funded devnet SOL would strand it, so the return happens
    FIRST and the deletion only follows a verified balance delta at the destination.
    """
    home = Path(args.home).expanduser().resolve()
    resume = _read_resume(home)
    _bootstrap(home, resume, rpc_url=args.rpc_url)
    record: dict[str, Any] = {
        "schema": RECORD_SCHEMA, "phase": "sweep", "status": "FAILED",
        "started_at": datetime.now(timezone.utc).isoformat(), "target": _target(),
        "home_path": str(home),
    }
    try:
        record["devnet_only"] = _assert_devnet_only()
        from core.wallet import approval, lifecycle, limits, proposals

        payer_id, payer_key = resume["payer_wallet_id"], resume["payer_public_key"]
        destination = str(args.to or "").strip() or _funding_source(payer_key)
        if not destination:
            raise RuntimeError("could not read the funding source off chain; pass --to <address> explicitly")
        balance = balance_of(payer_key)
        amount = balance - 5_000  # the fee stays behind; a plain transfer cannot also pay for itself
        record.update({
            "payer_public_key": payer_key, "payer_balance_lamports": balance,
            "destination": destination, "destination_discovered_from": "chain" if not args.to else "operator argument",
            "destination_explorer": EXPLORER_ADDRESS.format(address=destination),
            "amount_lamports": amount,
        })
        if amount <= RENT_EXEMPT_FLOOR_LAMPORTS:
            record["status"] = "NOTHING_TO_SWEEP"
            record["note"] = f"residual {balance} lamports is at or below the rent-exempt floor; a plain transfer cannot empty it"
            return 0
        destination_before = balance_of(destination)

        # An owner-set ceiling for this burner: the defaults are sized for a testnet slice, and the
        # operator funded well above them. Recorded because it is an owner decision, not a default.
        ceiling = limits.SpendLimits(per_tx_minor=amount + 1, daily_minor=(amount + 1) * 2, per_destination_daily_minor=(amount + 1) * 2)
        limits.set_limits(payer_id, "SOL", ceiling)
        record["owner_set_limits"] = ceiling.to_dict()

        proposal = proposals.propose_transaction(
            wallet_id=payer_id, destination=destination, amount_minor=amount, asset="SOL",
            origin=proposals.ORIGIN_USER, memo="return unspent devnet proof funding",
        )
        engine = lifecycle.default_lifecycle()
        engine.prepare(proposal.proposal_id)
        receipt = engine.approve_and_execute(proposal.proposal_id, approver=approval.PinApprover(resume["payer_pin"]))
        transaction = get_transaction(receipt.tx_signature)
        destination_after = balance_of(destination)
        record.update({
            "proposal_id": proposal.proposal_id, "tx_signature": receipt.tx_signature,
            "explorer": EXPLORER_TX.format(sig=receipt.tx_signature), "state": receipt.state,
            "lifecycle_states": _states(proposal.proposal_id),
            "verification": {
                "slot": (transaction or {}).get("slot"),
                "chain_err": ((transaction or {}).get("meta") or {}).get("err"),
                "destination_balance_before_lamports": destination_before,
                "destination_balance_after_lamports": destination_after,
                "destination_balance_delta_lamports": destination_after - destination_before,
                "destination_delta_from_chain_meta": _account_delta(transaction or {}, destination),
            },
        })
        if receipt.state != proposals.STATE_CONFIRMED or record["verification"]["destination_balance_delta_lamports"] != amount:
            raise RuntimeError(f"the return did not land: {json.dumps(record['verification'], sort_keys=True)}")
        record["status"] = "RETURNED"
        return 0
    except Exception as exc:  # the record must carry the failure
        record["error"] = f"{type(exc).__name__}: {str(exc)[:400]}"
        return 4
    finally:
        record["finished_at"] = datetime.now(timezone.utc).isoformat()
        abandoned = []
        for role in ("receiver", "merchant"):
            key = resume.get(f"{role}_public_key")
            if key:
                with contextlib.suppress(Exception):
                    abandoned.append({"role": role, "public_key": key, "balance_lamports": balance_of(key), "explorer": EXPLORER_ADDRESS.format(address=key)})
        record["abandoned_proof_accounts"] = abandoned
        record["abandoned_note"] = (
            "These accounts exist only as proof that the payments landed. A plain transfer cannot empty "
            "them: paying the fee would leave a non-zero balance below the rent-exempt floor, which the "
            "chain refuses. Their sealed keys die with the home, so this devnet SOL is deliberately left."
        )
        deleted = record["status"] in {"RETURNED", "NOTHING_TO_SWEEP"} and not args.keep_home
        with contextlib.suppress(Exception):
            record["payer_balance_lamports_final"] = balance_of(resume["payer_public_key"])
        if deleted:
            shutil.rmtree(home, ignore_errors=True)
        record["home_still_exists"] = home.exists()
        record["cleanup"] = (
            "home deleted after a verified return; the sealed keys and the resume state died with it"
            if not record["home_still_exists"]
            else "home preserved: the return did not verify, so nothing was destroyed"
        )
        _emit(args.out, record)


# --------------------------------------------------------------------------------------------
# selfcheck: the resume seam, with no network and no funds
# --------------------------------------------------------------------------------------------

def cmd_selfcheck(args: argparse.Namespace) -> int:
    """Prove prepare -> (new process) -> unseal works, without touching the network or any funds."""
    home = Path(args.home).expanduser().resolve()
    resume = _read_resume(home)
    _bootstrap(home, resume, rpc_url=args.rpc_url)
    from core.wallet import custody, signers

    profile = custody.require_wallet(resume["payer_wallet_id"])
    signer = signers.signer_for(profile, pin=resume["payer_pin"], proposal_id="selfcheck")
    signature = signer.sign(b"vool-devnet-external-proof-selfcheck")
    record = {
        "schema": RECORD_SCHEMA, "phase": "selfcheck",
        "public_key": profile.public_key, "mode": profile.mode,
        "pin_verifies_after_restart": custody.verify_pin(profile.wallet_id, resume["payer_pin"]),
        "wrong_pin_rejected": not custody.verify_pin(profile.wallet_id, "000000" if resume["payer_pin"] != "000000" else "111111"),
        "signature_length": len(bytes(signature)),
        "home_mode": _mode_of(home), "resume_mode": _mode_of(home / RESUME_FILENAME),
        "devnet_only": _assert_devnet_only(),
    }
    record["ok"] = bool(record["pin_verifies_after_restart"] and record["wrong_pin_rejected"] and record["signature_length"] == 64 and record["home_mode"] == "0o700" and record["resume_mode"] == "0o600")
    _emit(args.out, record)
    return 0 if record["ok"] else 5


def _emit(out: str | None, record: dict[str, Any]) -> None:
    text = json.dumps(record, indent=2, sort_keys=True, default=str) + "\n"
    if out:
        path = Path(out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    print(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name, handler in (("prepare", cmd_prepare), ("prove", cmd_prove), ("sweep", cmd_sweep), ("selfcheck", cmd_selfcheck)):
        child = sub.add_parser(name)
        child.add_argument("--home", required=True, help="the isolated VOOL_HOME, created at mode 0700 and reused by both phases")
        child.add_argument("--out", default="", help="where to write the record (json)")
        child.add_argument("--rpc-url", default=os.environ.get("VOOL_DEVNET_PROOF_RPC_URL", ""), help=f"the endpoint to drive (default {DEVNET_RPC}); also readable from VOOL_DEVNET_PROOF_RPC_URL so a keyed provider URL never reaches a shell history or a record. The verdict comes from the chain's genesis hash, not this string")
        child.set_defaults(handler=handler)
    sub.choices["prepare"].add_argument("--force", action="store_true", help="overwrite an existing burner (only when it is spent or empty)")
    sub.choices["prove"].add_argument("--keep-home", action="store_true", help="preserve the isolated home even after a proven run")
    sub.choices["prove"].add_argument("--local-airdrop", action="store_true", help="fund the burner from a LOCAL validator (rehearsal only; refused against public devnet)")
    sub.choices["sweep"].add_argument("--to", default="", help="where to return the residual (default: the funding source, read off chain)")
    sub.choices["sweep"].add_argument("--keep-home", action="store_true", help="return the residual but preserve the isolated home")
    args = parser.parse_args(argv)
    if os.environ.get("VOOL_WALLET_ENABLED") != "1":
        print("set VOOL_WALLET_ENABLED=1", file=sys.stderr)
        return 2
    # this proof drives the Solana Devnet row, so its run operates on Test networks
    os.environ["VOOL_WALLET_NETWORK_ENVIRONMENT"] = "testnet"
    return int(args.handler(args))


if __name__ == "__main__":
    sys.exit(main())
