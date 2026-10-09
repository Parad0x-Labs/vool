"""Watch-only adapter to a pinned zcash-devtool binary.

The binary is identified by its SHA-256 (``VOOL_ZCASH_DEVTOOL_SHA256``). Its bytes are read once, checked against the
pin and copied into a VOOL-owned owner-only directory under their hash; every run re-hashes THAT copy and executes
it, so the bytes that were checked are the bytes that run. Only view-only subcommands can be run at all
(:data:`ALLOWED_SUBCOMMANDS`); anything that could create, restore, send, shield or reveal a spending key is refused
before a process starts, and the network subcommands are refused while Local Only is on.

The viewing key never travels in the process arguments: ``init-fvk --fvk -`` reads it from stdin, which needs the
pinned build carrying ``core/zcash/devtool/init-fvk-stdin.patch`` (an unpatched build fails closed: it cannot decode
``-`` as a key). Every file the tool writes is created owner-only (umask 077).

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
import stat
import subprocess
import tempfile
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
#: The wallet database's output_pool codes. Since NU7 an Orchard receiver is paid into the Ironwood pool (code 4).
_POOLS = {0: "transparent", 2: "sapling", 3: "orchard", 4: "ironwood"}
SHIELDED_POOLS = frozenset({"sapling", "orchard", "ironwood"})
_ADDRESS_PREFIXES = {
    config.NETWORK_MAIN: ("u1", "zs1"),
    config.NETWORK_TEST: ("utest1", "ztestsapling1"),
}
_TXID = re.compile(r"^[0-9a-f]{64}$")
#: Key-shaped text in tool output. No leading word boundary: a key glued to "FVK_" or an escaped "\n" is still a key.
#: The run between prefix and separator is bounded (the longest HRP rest is "-regtest" / "regtestsapling"), so a long
#: hostile error line is scanned in linear time.
_KEY_SHAPED = re.compile(r"(?i)(?:uview|uivk|zxview|zview|zivk|secret-extended-key|secret-spending-key)[0-9a-z-]{0,24}1[0-9a-z]{20,}")


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


def _private_dir(path: Path) -> Path:
    """Create ``path`` owner-only, and refuse one that someone else owns or can write."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            os.chmod(path, 0o700)
        info = os.stat(path)
    except OSError:
        raise ZcashWatchError("data_dir_unavailable", "The Zcash data folder could not be prepared, so nothing was run.") from None
    if os.name == "posix":
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise ZcashWatchError("data_dir_unsafe", "The Zcash data folder is not private to this user, so nothing was run.")
    return path


def _tighten(directory: Path) -> None:
    """Owner-only modes for what is already in the wallet folder (files made before the umask rule)."""
    with contextlib.suppress(OSError):
        for entry in directory.iterdir():
            want = 0o700 if entry.is_dir() and not entry.is_symlink() else 0o600
            if not entry.is_symlink() and stat.S_IMODE(entry.stat().st_mode) & 0o077:
                os.chmod(entry, want)


def wallet_ro_uri(db_path: Path) -> str:
    """A read-only SQLite URI; the path is percent-encoded so '?' or '#' in it cannot cut the URI short."""
    return Path(db_path).resolve().as_uri() + "?mode=ro"


class Devtool:
    def __init__(self, *, binary: str | None = None, sha256: str | None = None, wallet_dir: Path | None = None,
                 network: str | None = None, server: str | None = None, connection: str | None = None,
                 bin_dir: Path | None = None) -> None:
        self.binary = Path(binary if binary is not None else config.devtool_path()) if (binary or config.devtool_path()) else None
        self.sha256 = str(sha256 if sha256 is not None else config.devtool_sha256()).strip().lower()
        self.network = network or config.network()
        if self.network not in (config.NETWORK_MAIN, config.NETWORK_TEST):
            raise ZcashWatchError("network_unsupported", "VOOL's Zcash lane runs on mainnet or testnet only.")
        self.wallet_dir = Path(wallet_dir) if wallet_dir is not None else config.data_dir() / "wallet"
        self.server = server or config.server()
        self.connection = connection or config.connection()
        self.bin_dir = Path(bin_dir) if bin_dir is not None else config.data_root() / "bin"

    # --- the one place a process starts ----------------------------------------------------------

    def _pinned_executable(self) -> Path:
        """The pinned bytes, as a VOOL-owned copy named by their hash. The copy is re-hashed on every run."""
        if self.binary is None:
            raise ZcashWatchError("devtool_missing", "The Zcash watch tool is not configured (VOOL_ZCASH_DEVTOOL).")
        if not re.fullmatch(r"[0-9a-f]{64}", self.sha256 or ""):
            raise ZcashWatchError("devtool_unpinned", "The Zcash watch tool has no pinned SHA-256 (VOOL_ZCASH_DEVTOOL_SHA256), so it is not run.")
        mismatch = ZcashWatchError("devtool_hash_mismatch", "The Zcash watch tool binary does not match its pinned SHA-256, so it is not run.")
        target = _private_dir(self.bin_dir) / self.sha256
        with contextlib.suppress(OSError):
            if hashlib.sha256(target.read_bytes()).hexdigest() == self.sha256:
                return target
        try:
            blob = self.binary.read_bytes()
        except OSError:
            raise ZcashWatchError("devtool_missing", "The Zcash watch tool binary was not found.") from None
        if hashlib.sha256(blob).hexdigest() != self.sha256:
            raise mismatch
        try:  # a temp name unique to this call: two threads preparing the copy never touch each other's file
            fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=f".{self.sha256}.", suffix=".tmp")
        except OSError:
            raise ZcashWatchError("devtool_failed", "The Zcash watch tool could not be prepared.") from None
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(blob)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp, 0o500)
            os.replace(tmp, target)
        except OSError:
            with contextlib.suppress(OSError):
                tmp.unlink()
            raise ZcashWatchError("devtool_failed", "The Zcash watch tool could not be prepared.") from None
        try:
            prepared = hashlib.sha256(target.read_bytes()).hexdigest()
        except OSError:
            raise ZcashWatchError("devtool_failed", "The Zcash watch tool could not be prepared.") from None
        if prepared != self.sha256:
            raise mismatch
        return target

    def run(self, subcommand: str, args: list[str] | None = None, *, timeout: float = DEFAULT_TIMEOUT_S, secret: str = "",
            stdin_data: str | None = None) -> str:
        if subcommand not in ALLOWED_SUBCOMMANDS:
            raise ZcashWatchError("subcommand_refused", f"The Zcash watch lane does not run '{subcommand[:40]}': only view-only commands are allowed.")
        if subcommand in _NETWORK_SUBCOMMANDS and _local_only():
            raise ZcashWatchError("local_only", "Local Only is on, so VOOL did not contact the Zcash network.")
        binary = self._pinned_executable()
        _private_dir(self.wallet_dir)
        _tighten(self.wallet_dir)
        argv = [str(binary), "wallet", "-w", str(self.wallet_dir), subcommand, *(args or [])]
        if subcommand in _NETWORK_SUBCOMMANDS:
            argv += ["-s", self.server, "--connection", self.connection]
        env = {key: os.environ[key] for key in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT") if key in os.environ}
        env.update({"RUST_LOG": "error", "RUST_BACKTRACE": "0", "RUST_LIB_BACKTRACE": "0"})
        private = {"umask": 0o077} if os.name == "posix" else {}  # the wallet files hold the viewing key
        feed = {"input": stdin_data} if stdin_data is not None else {"stdin": subprocess.DEVNULL}
        try:
            done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env, check=False, **feed, **private)
        except subprocess.TimeoutExpired:
            raise ZcashWatchError("devtool_timeout", f"The Zcash watch tool did not finish '{subcommand}' in {int(timeout)}s.") from None
        except OSError:
            raise ZcashWatchError("devtool_failed", "The Zcash watch tool could not be started.") from None
        finally:
            _tighten(self.wallet_dir)
        if done.returncode != 0:
            lines = [line.strip() for line in (done.stderr or done.stdout or "").splitlines() if line.strip()]
            text = next((line for line in lines if line.startswith("Error:")), lines[0] if lines else "")
            if secret:
                text = re.sub(re.escape(secret), "[viewing key]", text, flags=re.IGNORECASE)
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
        """Create the view-only wallet from the viewing key (fed on stdin). An existing wallet is never replaced here."""
        if key.network != self.network:
            raise ZcashWatchError("viewing_key_wrong_network", "The viewing key is for another Zcash network.")
        if self.initialized():
            raise ZcashWatchError("already_initialized", "A Zcash watch wallet already exists for this network.")
        args = ["--name", ACCOUNT_NAME, "--fvk", "-"]
        if birthday is not None:
            args += ["--birthday", str(int(birthday))]
        self.run("init-fvk", args, secret=key.encoded, stdin_data=key.encoded + "\n")
        self.check_account(key)

    def _connect_ro(self) -> sqlite3.Connection:
        try:
            return sqlite3.connect(wallet_ro_uri(self.db_path), uri=True, timeout=10)
        except sqlite3.Error:
            raise ZcashWatchError("wallet_unreadable", "The Zcash watch wallet could not be opened.") from None

    def check_account(self, key: ViewingKey) -> None:
        """The wallet must watch exactly this key, view-only. Anything else (another key, a spending account) refuses."""
        if not self.initialized():
            raise ZcashWatchError("not_initialized", "There is no Zcash watch wallet yet.")
        conn = self._connect_ro()
        try:
            rows = conn.execute("SELECT ufvk, has_spend_key FROM accounts").fetchall()
        except sqlite3.Error:
            raise ZcashWatchError("wallet_unreadable", "The Zcash watch wallet's account could not be read.") from None
        finally:
            conn.close()
        if len(rows) != 1 or str(rows[0][0] or "") != key.encoded or rows[0][1]:
            raise ZcashWatchError("wallet_key_mismatch", "The Zcash watch wallet does not watch the saved viewing key (or is not view-only), so nothing is confirmed.")

    def scanned_height(self) -> int | None:
        """How far the wallet has fully scanned (zcash_client_sqlite's rule: the end of the first Scanned range that
        starts at or before the birthday). None when nothing is fully scanned yet."""
        conn = self._connect_ro()
        try:
            birthday = conn.execute("SELECT MIN(birthday_height) FROM accounts").fetchone()[0]
            first = conn.execute("SELECT block_range_start, block_range_end FROM scan_queue WHERE priority = 10"
                                 " ORDER BY block_range_start ASC LIMIT 1").fetchone()
        except sqlite3.Error:
            raise ZcashWatchError("wallet_unreadable", "The Zcash watch wallet's scan progress could not be read.") from None
        finally:
            conn.close()
        if birthday is None or first is None or int(first[0]) > int(birthday):
            return None
        return int(first[1]) - 1

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
        """Sync, then read the tip. Records the outcome; only a fully successful round counts as fresh.

        Each attempt takes the next ``attempt_seq`` BEFORE it starts and only a success copies it to ``ok_seq``, so
        a failed attempt is visible by sequence, whatever the wall clock did in between.
        """
        state = self.read_state()
        seq = int(state.get("attempt_seq") or 0) + 1
        state.update({"attempt_seq": seq, "last_attempt_at": time.time(), "last_error": "in_progress"})
        self._write_state(state)
        try:
            self.run("sync", timeout=SYNC_TIMEOUT_S)
            tip = self.chain_tip()
        except ZcashWatchError as exc:
            state["last_error"] = exc.code
            self._write_state(state)
            raise
        state.update({"last_sync_at": time.time(), "tip": tip, "last_error": "", "ok_seq": seq})
        self._write_state(state)
        return state

    def read_state(self) -> dict[str, Any]:
        try:
            data = json.loads((self.wallet_dir / STATE_FILE).read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write_state(self, state: dict[str, Any]) -> None:
        _private_dir(self.wallet_dir)
        try:
            write_private(self.wallet_dir / STATE_FILE, json.dumps(state, sort_keys=True))
        except OSError:
            raise ZcashWatchError("state_unwritable", "The Zcash watch state could not be saved, so nothing is confirmed.") from None

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
        conn = self._connect_ro()
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
        notes: dict[tuple[str, str, int], ReceivedNote] = {}
        for txid_raw, pool_code, output_index, value, memo, block_time in rows:
            if not isinstance(txid_raw, bytes | bytearray) or len(txid_raw) != 32:
                raise ZcashWatchError("wallet_unreadable", "The Zcash watch wallet holds a malformed transaction id.")
            txid = bytes(txid_raw)[::-1].hex()  # display order, as the devtool prints it
            if txid not in listed:
                raise ZcashWatchError("tx_crosscheck_failed", "The Zcash watch wallet and the watch tool disagree about a transaction, so nothing is confirmed.")
            pool = _POOLS.get(int(pool_code))
            if pool is None:
                continue  # a pool this build does not know cannot pay an invoice
            note = ReceivedNote(
                txid=txid, pool=pool, output_index=int(output_index), value_zat=int(value),
                memo=decode_memo(memo) if pool in SHIELDED_POOLS else None,
                mined_height=listed[txid], block_time=None if block_time is None else int(block_time),
            )
            key = (note.txid, note.pool, note.output_index)
            seen = notes.get(key)
            if seen is not None and (seen.value_zat, seen.memo) != (note.value_zat, note.memo):
                raise ZcashWatchError("wallet_unreadable", "The Zcash watch wallet lists one output twice with different contents, so nothing is confirmed.")
            notes[key] = note  # one output counts once, however many rows the view returns for it
        return list(notes.values())


def write_private(path: Path, text: str) -> None:
    """Write a file owner-only (0600) atomically. The temp name is unique to this call (mkstemp), so concurrent
    writers in one process never unlink or replace each other's temp file; the last complete write wins."""
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def _local_only() -> bool:
    try:
        from core.remote_fetch_policy import local_only_active

        return bool(local_only_active(None))
    except Exception:
        return False
