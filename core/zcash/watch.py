"""Watch-only adapter to a pinned zcash-devtool binary.

The binary is identified by its SHA-256 (``VOOL_ZCASH_DEVTOOL_SHA256``) and re-hashed before EVERY run; an
unpinned or changed binary is refused. Only view-only subcommands can be run at all (:data:`ALLOWED_SUBCOMMANDS`);
anything that could create, restore, send, shield or reveal a spending key is refused before a process starts.

Received notes are read from the view-only wallet's own SQLite views (``v_tx_outputs`` / ``v_transactions``,
opened read-only) because the devtool's JSON listing carries only txid and height. The two are cross-checked: a
received output whose txid the devtool's own ``list-tx --json`` does not list makes the whole read unknown.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.zcash import config
from core.zcash.keys import ViewingKey

#: The ONLY devtool subcommands this lane runs. All read or derive from the viewing key; none can spend.
ALLOWED_SUBCOMMANDS = frozenset({"init-fvk", "sync", "list-addresses", "list-tx", "get-info"})
_NETWORK_SUBCOMMANDS = frozenset({"init-fvk", "sync", "get-info"})
ACCOUNT_NAME = "vool-invoices"
WALLET_DB = "data.sqlite"
STATE_FILE = "watch_state.json"
DEFAULT_TIMEOUT_S = 120.0
SYNC_TIMEOUT_S = 1800.0
_POOLS = {0: "transparent", 2: "sapling", 3: "orchard"}
_SHIELDED = frozenset({"sapling", "orchard"})
_ADDRESS_PREFIXES = {
    config.NETWORK_MAIN: ("u1", "zs1"),
    config.NETWORK_TEST: ("utest1", "ztestsapling1"),
}
_TXID = re.compile(r"^[0-9a-f]{64}$")
_KEY_SHAPED = re.compile(r"(?i)\b(?:uview|uivk|zxview|secret-extended-key)[0-9a-z-]*1[0-9a-z]{20,}")


class ZcashWatchError(Exception):
    """A typed failure of the watch lane. ``code`` is stable; ``message`` is safe to show (no key material)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ReceivedNote:
    txid: str
    pool: str
    output_index: int
    value_zat: int
    memo: str | None
    mined_height: int | None
    block_time: int | None

    def to_dict(self) -> dict[str, Any]:
        return {"txid": self.txid, "pool": self.pool, "output_index": self.output_index, "value_zat": self.value_zat,
                "memo": self.memo, "mined_height": self.mined_height, "block_time": self.block_time}


def decode_memo(raw: bytes | None) -> str | None:
    """ZIP 302: a first byte <= 0xF4 is UTF-8 text padded with zeros; 0xF6 is 'no memo'; anything else is not text."""
    if not raw:
        return None
    data = bytes(raw)
    if data[0] > 0xF4:
        return None
    try:
        return data.rstrip(b"\x00").decode("utf-8")
    except UnicodeDecodeError:
        return None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class Devtool:
    def __init__(self, *, binary: str | None = None, sha256: str | None = None, wallet_dir: Path | None = None,
                 network: str | None = None, server: str | None = None, connection: str | None = None) -> None:
        self.binary = Path(binary if binary is not None else config.devtool_path()) if (binary or config.devtool_path()) else None
        self.sha256 = str(sha256 if sha256 is not None else config.devtool_sha256()).strip().lower()
        self.network = network or config.network()
        if self.network not in (config.NETWORK_MAIN, config.NETWORK_TEST):
            raise ZcashWatchError("network_unsupported", "VOOL's Zcash lane runs on mainnet or testnet only.")
        self.wallet_dir = Path(wallet_dir) if wallet_dir is not None else config.data_dir() / "wallet"
        self.server = server or config.server()
        self.connection = connection or config.connection()

    # --- the one place a process starts ----------------------------------------------------------

    def _verify_binary(self) -> Path:
        if self.binary is None:
            raise ZcashWatchError("devtool_missing", "The Zcash watch tool is not configured (VOOL_ZCASH_DEVTOOL).")
        if not re.fullmatch(r"[0-9a-f]{64}", self.sha256 or ""):
            raise ZcashWatchError("devtool_unpinned", "The Zcash watch tool has no pinned SHA-256 (VOOL_ZCASH_DEVTOOL_SHA256), so it is not run.")
        if not self.binary.is_file():
            raise ZcashWatchError("devtool_missing", "The Zcash watch tool binary was not found.")
        actual = sha256_file(self.binary)
        if actual != self.sha256:
            raise ZcashWatchError("devtool_hash_mismatch", "The Zcash watch tool binary does not match its pinned SHA-256, so it is not run.")
        return self.binary

    def run(self, subcommand: str, args: list[str] | None = None, *, timeout: float = DEFAULT_TIMEOUT_S, secret: str = "") -> str:
        if subcommand not in ALLOWED_SUBCOMMANDS:
            raise ZcashWatchError("subcommand_refused", f"The Zcash watch lane does not run '{subcommand[:40]}': only view-only commands are allowed.")
        binary = self._verify_binary()
        self.wallet_dir.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):  # the wallet db holds the viewing key: owner-only where the OS allows it
            os.chmod(self.wallet_dir, 0o700)
        argv = [str(binary), "wallet", "-w", str(self.wallet_dir), subcommand, *(args or [])]
        if subcommand in _NETWORK_SUBCOMMANDS:
            argv += ["-s", self.server, "--connection", self.connection]
        env = {key: os.environ[key] for key in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT") if key in os.environ}
        env.update({"RUST_LOG": "error", "RUST_BACKTRACE": "0", "RUST_LIB_BACKTRACE": "0"})
        try:
            done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env, stdin=subprocess.DEVNULL, check=False)
        except subprocess.TimeoutExpired as exc:
            raise ZcashWatchError("devtool_timeout", f"The Zcash watch tool did not finish '{subcommand}' in {int(timeout)}s.") from exc
        except OSError as exc:
            raise ZcashWatchError("devtool_failed", "The Zcash watch tool could not be started.") from exc
        if done.returncode != 0:
            lines = [line.strip() for line in (done.stderr or done.stdout or "").splitlines() if line.strip()]
            text = next((line for line in lines if line.startswith("Error:")), lines[0] if lines else "")
            if secret:
                text = text.replace(secret, "[viewing key]")
            text = _KEY_SHAPED.sub("[viewing key]", text)[:300]  # redact before cutting, so no fragment survives
            raise ZcashWatchError("devtool_failed", f"The Zcash watch tool failed on '{subcommand}': {text or 'no detail'}")
        return done.stdout or ""

    # --- operations ---------------------------------------------------------------------------

    @property
    def db_path(self) -> Path:
        return self.wallet_dir / WALLET_DB

    def initialized(self) -> bool:
        return self.db_path.is_file()

    def initialize(self, key: ViewingKey, *, birthday: int | None = None) -> None:
        """Create the view-only wallet from the viewing key. An existing wallet is never replaced here."""
        if key.network != self.network:
            raise ZcashWatchError("viewing_key_wrong_network", "The viewing key is for another Zcash network.")
        if self.initialized():
            raise ZcashWatchError("already_initialized", "A Zcash watch wallet already exists for this network.")
        args = ["--name", ACCOUNT_NAME, "--fvk", key.encoded]
        if birthday is not None:
            args += ["--birthday", str(int(birthday))]
        self.run("init-fvk", args, secret=key.encoded)

    def receiving_address(self) -> str:
        """The account's shielded receiving address: Orchard-only when the key has Orchard, else Sapling."""
        last_error: ZcashWatchError | None = None
        for receiver in ("orchard", "sapling"):
            try:
                out = self.run("list-addresses", ["--receiver", receiver])
            except ZcashWatchError as exc:
                last_error = exc
                continue
            match = re.search(r"Receiver\(" + receiver + r"\):\s*(\S+)", out)
            if match and match.group(1).startswith(_ADDRESS_PREFIXES[self.network]):
                return match.group(1)
        raise last_error or ZcashWatchError("address_unavailable", "No shielded receiving address could be derived from the viewing key.")

    def chain_tip(self) -> int:
        out = self.run("get-info")
        try:
            info = json.loads(out.strip().splitlines()[-1])
            tip = int(info["chain_tip_height"])
            chain = str(info.get("chain_name") or "")
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ZcashWatchError("chain_info_unreadable", "The Zcash server's chain info could not be read.") from exc
        if chain and chain != self.network:
            raise ZcashWatchError("chain_mismatch", f"The Zcash server reports chain '{chain[:20]}', not {self.network}.")
        if tip <= 0:
            raise ZcashWatchError("chain_info_unreadable", "The Zcash server reported no chain height.")
        return tip

    def sync(self) -> dict[str, Any]:
        """Sync, then read the tip. Records the outcome; only a fully successful round counts as fresh."""
        state = self.read_state()
        state["last_attempt_at"] = time.time()
        try:
            self.run("sync", timeout=SYNC_TIMEOUT_S)
            tip = self.chain_tip()
        except ZcashWatchError as exc:
            state["last_error"] = exc.code
            self._write_state(state)
            raise
        state.update({"last_sync_at": time.time(), "tip": tip, "last_error": ""})
        self._write_state(state)
        return state

    def read_state(self) -> dict[str, Any]:
        try:
            data = json.loads((self.wallet_dir / STATE_FILE).read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write_state(self, state: dict[str, Any]) -> None:
        self.wallet_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.wallet_dir / (STATE_FILE + ".tmp")
        tmp.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
        tmp.replace(self.wallet_dir / STATE_FILE)

    def listed_transactions(self) -> dict[str, int | None]:
        out = self.run("list-tx", ["--json"])
        try:
            rows = json.loads(out.strip().splitlines()[-1]) if out.strip() else []
            listed = {str(row["txid"]).lower(): (None if row.get("mined_height") is None else int(row["mined_height"])) for row in rows}
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            raise ZcashWatchError("tx_list_unreadable", "The Zcash watch tool's transaction list could not be read.") from exc
        if any(not _TXID.match(txid) for txid in listed):
            raise ZcashWatchError("tx_list_unreadable", "The Zcash watch tool listed a malformed transaction id.")
        return listed

    def received_notes(self) -> list[ReceivedNote]:
        """Every output received from outside the wallet, cross-checked against the devtool's own listing."""
        if not self.initialized():
            raise ZcashWatchError("not_initialized", "There is no Zcash watch wallet yet.")
        listed = self.listed_transactions()
        try:
            conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=10)
        except sqlite3.Error as exc:
            raise ZcashWatchError("wallet_unreadable", "The Zcash watch wallet could not be opened.") from exc
        try:
            rows = conn.execute(
                "SELECT o.txid, o.output_pool, o.output_index, o.value, o.memo,"
                " (SELECT MAX(t.block_time) FROM v_transactions t WHERE t.txid = o.txid)"
                " FROM v_tx_outputs o"
                " WHERE o.to_account_uuid IS NOT NULL AND o.from_account_uuid IS NULL AND NOT o.is_change"
            ).fetchall()
        except sqlite3.Error as exc:
            raise ZcashWatchError("wallet_unreadable", "The Zcash watch wallet's outputs could not be read.") from exc
        finally:
            conn.close()
        notes: list[ReceivedNote] = []
        for txid_raw, pool_code, output_index, value, memo, block_time in rows:
            if not isinstance(txid_raw, bytes | bytearray) or len(txid_raw) != 32:
                raise ZcashWatchError("wallet_unreadable", "The Zcash watch wallet holds a malformed transaction id.")
            txid = bytes(txid_raw)[::-1].hex()  # display order, as the devtool prints it
            if txid not in listed:
                raise ZcashWatchError("tx_crosscheck_failed", "The Zcash watch wallet and the watch tool disagree about a transaction, so nothing is confirmed.")
            pool = _POOLS.get(int(pool_code))
            if pool is None:
                continue  # a pool this build does not know cannot pay an invoice
            notes.append(ReceivedNote(
                txid=txid, pool=pool, output_index=int(output_index), value_zat=int(value),
                memo=decode_memo(memo) if pool in _SHIELDED else None,
                mined_height=listed[txid], block_time=None if block_time is None else int(block_time),
            ))
        return notes
