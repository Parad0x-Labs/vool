"""Zcash private invoices end to end: the watch adapter's guards, the matcher's states, receipts, memory, tools, page.

The devtool is played by a small script (pinned by its own SHA-256 like the real binary) that answers the
view-only subcommands from a JSON file and logs every invocation. The wallet database it serves has the real
column names of zcash_client_sqlite 0.22's ``v_tx_outputs`` / ``v_transactions`` views (read from a wallet the
real zcash-devtool built). The live testnet run is separate evidence, not this file.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
import stat
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from core.zcash import matcher
from core.zcash.invoices import InvoiceError, InvoiceStore, format_zec, parse_amount, payment_uri
from core.zcash.service import ZcashLane
from core.zcash.watch import ALLOWED_SUBCOMMANDS, Devtool, ReceivedNote, ZcashWatchError, decode_memo
from tests.test_zcash_keys import SEED_PHRASE, UFVK_TEST, UFVK_TEST_OTHER, isolate_credential_vault

ADDRESS = "utest1rfz5kscxeqmr73uq0ujm4j60jc49fvcjatcy5p2485t5tpzzh3c5uxqvttqukr6yp0ryvfj7sar4um4skuqsdgt8nkprkr82y5865z5j"
ACCOUNT = bytes(range(16))

FAKE = r"""#!/usr/bin/env python3
import json, sqlite3, sys, time
from pathlib import Path
cfg = json.loads(Path(__CFG__).read_text())
args = sys.argv[1:]
with open(__LOG__, "a") as log:
    log.write(json.dumps(args) + "\n")
wallet = Path(args[args.index("-w") + 1])
sub = args[args.index("-w") + 2]
answer = cfg.get(sub, {"rc": 0, "out": ""})
time.sleep(answer.get("sleep", 0))
if sub == "init-fvk" and answer.get("rc", 0) == 0:
    fvk = args[args.index("--fvk") + 1]
    if fvk == "-":
        fvk = sys.stdin.readline().strip()
    wallet.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(wallet / "data.sqlite")
    db.executescript(cfg["schema"])
    db.execute("INSERT INTO accounts (ufvk, has_spend_key, birthday_height) VALUES (?, 0, ?)", (fvk, cfg["birthday"]))
    db.commit()
if sub == "sync" and answer.get("rc", 0) == 0:
    db = sqlite3.connect(wallet / "data.sqlite")
    db.execute("DELETE FROM scan_queue")
    db.execute("INSERT INTO scan_queue VALUES (?, ?, 10)", (cfg["birthday"], cfg["scanned"] + 1))
    db.commit()
sys.stdout.write(answer.get("out", ""))
sys.stderr.write(answer.get("err", ""))
sys.exit(answer.get("rc", 0))
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (id INTEGER PRIMARY KEY, ufvk TEXT, has_spend_key INTEGER NOT NULL DEFAULT 0, birthday_height INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS scan_queue (block_range_start INTEGER NOT NULL, block_range_end INTEGER NOT NULL, priority INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS v_transactions (account_uuid BLOB, mined_height INTEGER, txid BLOB, block_time INTEGER);
CREATE TABLE IF NOT EXISTS v_tx_outputs (transaction_id INTEGER, txid BLOB, tx_mined_height INTEGER, output_pool INTEGER,
  output_index INTEGER, from_account_uuid BLOB, to_account_uuid BLOB, to_address TEXT, value INTEGER, is_change INTEGER, memo BLOB);
"""


def _memo(text: str) -> bytes:
    return text.encode() + b"\x00" * (512 - len(text.encode()))


class Chain:
    """The fake devtool's world: a tip, how far the wallet scans, transactions, and how each subcommand answers.

    The script reads its answers from an absolute path baked into it, because the lane executes its own pinned
    copy of the script (under the Zcash data folder), not the file at VOOL_ZCASH_DEVTOOL.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.config = root / "fake-devtool.json"
        self.log = root / "fake-devtool.log"
        self.script = root / "zcash-devtool"
        self.script.write_text(FAKE.replace("/usr/bin/env python3", sys.executable, 1)
                               .replace("__CFG__", repr(str(self.config))).replace("__LOG__", repr(str(self.log))))
        self.script.chmod(self.script.stat().st_mode | stat.S_IEXEC)
        self.wallet = root / "data" / "test" / "wallet"  # where the lane's default devtool keeps it (VOOL_ZCASH_DATA_DIR/test)
        self.tip = 1000
        self.scanned: int | None = None  # None: the wallet scans to the tip
        self.txs: list[dict] = []
        self.overrides: dict[str, dict] = {}
        self.write()

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.script.read_bytes()).hexdigest()

    def pay(self, memo: str | None, zat: int, *, height: int | None, pool: int = 3, change: bool = False, internal: bool = False,
            output_index: int = 0) -> str:
        txid_bytes = hashlib.sha256(f"{len(self.txs)}{memo}{zat}".encode()).digest()
        self.txs.append({"txid": txid_bytes, "memo": memo, "zat": zat, "height": height, "pool": pool, "change": change,
                         "internal": internal, "index": output_index})
        self._db()
        self.write()
        return txid_bytes[::-1].hex()

    def _db(self) -> None:
        if not (self.wallet / "data.sqlite").exists():
            return
        db = sqlite3.connect(self.wallet / "data.sqlite")
        db.execute("DELETE FROM v_tx_outputs")
        db.execute("DELETE FROM v_transactions")
        for i, tx in enumerate(self.txs):
            db.execute("INSERT INTO v_transactions VALUES (?, ?, ?, ?)", (ACCOUNT, tx["height"], tx["txid"], 1_760_000_000 + i))
            db.execute("INSERT INTO v_tx_outputs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (
                i, tx["txid"], tx["height"], tx["pool"], tx["index"], ACCOUNT if tx["internal"] else None, ACCOUNT, ADDRESS, tx["zat"],
                1 if tx["change"] else 0, _memo(tx["memo"]) if tx["memo"] is not None and tx["pool"] != 0 else None))
        db.commit()
        db.close()

    def write(self) -> None:
        listed = [{"txid": tx["txid"][::-1].hex(), "mined_height": tx["height"]} for tx in self.txs]
        cfg = {
            "schema": SCHEMA, "birthday": 900, "scanned": self.tip if self.scanned is None else self.scanned,
            "sync": {"rc": 0, "out": ""},
            "get-info": {"rc": 0, "out": json.dumps({"server_uri": "https://testnet.zec.rocks:443", "chain_name": "test", "chain_tip_height": self.tip})},
            "list-tx": {"rc": 0, "out": json.dumps(listed)},
            "list-addresses": {"rc": 0, "out": f"Receiver(orchard): {ADDRESS}\n"},
            **self.overrides,
        }
        self.config.write_text(json.dumps(cfg))

    def calls(self) -> list[list[str]]:
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def subcommands(self) -> list[str]:
        return [call[call.index("-w") + 2] for call in self.calls()]


@pytest.fixture
def chain(tmp_path, monkeypatch):
    isolate_credential_vault(monkeypatch, tmp_path / "vault")
    world = Chain(tmp_path)
    monkeypatch.setenv("VOOL_ZCASH_ENABLED", "1")
    monkeypatch.setenv("VOOL_ZCASH_NETWORK", "test")
    monkeypatch.setenv("VOOL_ZCASH_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("VOOL_ZCASH_DEVTOOL", str(world.script))
    monkeypatch.setenv("VOOL_ZCASH_DEVTOOL_SHA256", world.sha256)
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    monkeypatch.delenv("VOOL_ZCASH_CONFIRMATIONS", raising=False)
    return world


def _lane(chain: Chain) -> ZcashLane:
    return ZcashLane(devtool=Devtool(wallet_dir=chain.wallet))


def _ready_lane(chain: Chain) -> ZcashLane:
    lane = _lane(chain)
    lane.set_viewing_key(UFVK_TEST)
    return lane


# --- the adapter's guards -----------------------------------------------------------------------

@pytest.mark.parametrize("sub", ["init", "send", "pay", "shield", "propose", "restore-mnemonic", "display-mnemonic", "derive-path", "reset", "import-ufvk", "fan-out"])
def test_only_view_only_subcommands_ever_start_a_process(chain, sub) -> None:
    assert sub not in ALLOWED_SUBCOMMANDS
    with pytest.raises(ZcashWatchError) as refused:
        _lane(chain).devtool.run(sub, [])
    assert refused.value.code == "subcommand_refused"
    assert chain.calls() == []


def test_an_unpinned_or_changed_binary_is_never_run(chain, monkeypatch) -> None:
    monkeypatch.setenv("VOOL_ZCASH_DEVTOOL_SHA256", "")
    with pytest.raises(ZcashWatchError) as unpinned:
        _lane(chain).devtool.run("get-info")
    assert unpinned.value.code == "devtool_unpinned"
    monkeypatch.setenv("VOOL_ZCASH_DEVTOOL_SHA256", chain.sha256)
    chain.script.write_text(chain.script.read_text() + "\n# tampered\n")
    with pytest.raises(ZcashWatchError) as changed:
        _lane(chain).devtool.run("get-info")
    assert changed.value.code == "devtool_hash_mismatch"
    assert chain.calls() == []


def test_the_checked_bytes_are_the_bytes_that_run(chain, tmp_path) -> None:
    marker = tmp_path / "unpinned-ran"
    evil = f"#!{sys.executable}\nimport pathlib; pathlib.Path({str(marker)!r}).write_text('ran')\n"
    devtool = _lane(chain).devtool
    devtool.run("get-info")
    copy = chain.root / "data" / "bin" / chain.sha256
    assert copy.is_file() and stat.S_IMODE(copy.stat().st_mode) == 0o500
    pinned = chain.sha256
    chain.script.write_text(evil)  # the configured path is swapped after the check
    devtool.run("get-info")
    assert not marker.exists() and chain.subcommands() == ["get-info", "get-info"]
    os.chmod(copy, 0o700)
    copy.write_text(evil)  # VOOL's own copy is swapped: it no longer hashes to the pin, and the source no longer does either
    with pytest.raises(ZcashWatchError) as swapped:
        devtool.run("get-info")
    assert swapped.value.code == "devtool_hash_mismatch" and devtool.sha256 == pinned
    assert not marker.exists() and len(chain.calls()) == 2


@pytest.mark.parametrize("odd", ["dir?x", "dir#x", "dir%3Fx"])
def test_the_wallet_is_opened_read_only_whatever_its_path(tmp_path, odd) -> None:
    wallet = tmp_path / odd / "wallet"
    wallet.mkdir(parents=True)
    with sqlite3.connect(wallet / "data.sqlite") as db:
        db.executescript(SCHEMA)
        db.execute("INSERT INTO accounts (ufvk, has_spend_key, birthday_height) VALUES ('x', 0, 900)")
        db.execute("INSERT INTO scan_queue VALUES (900, 1001, 10)")
    devtool = Devtool(binary="/nonexistent", sha256="0" * 64, wallet_dir=wallet, network="test", bin_dir=tmp_path / "bin")
    assert devtool.scanned_height() == 1000
    conn = devtool._connect_ro()
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("CREATE TABLE written (a)")
    conn.close()
    assert [p.name for p in tmp_path.iterdir()] == [odd]  # no stray database at a truncated path


def test_a_failing_tool_reports_its_error_without_the_viewing_key(chain) -> None:
    chain.overrides["init-fvk"] = {"rc": 1, "err": f"Error: could not decode {UFVK_TEST}\n\nStack backtrace:\n  0: _start\n"}
    chain.write()
    with pytest.raises(ZcashWatchError) as failed:
        _lane(chain).set_viewing_key(UFVK_TEST)
    assert failed.value.code == "devtool_failed"
    assert UFVK_TEST not in failed.value.message and "[viewing key]" in failed.value.message


def test_a_hung_tool_times_out(chain) -> None:
    chain.overrides["get-info"] = {"rc": 0, "sleep": 5, "out": "{}"}
    chain.write()
    with pytest.raises(ZcashWatchError) as hung:
        _lane(chain).devtool.run("get-info", timeout=0.5)
    assert hung.value.code == "devtool_timeout"


def test_a_seed_phrase_creates_nothing_anywhere(chain) -> None:
    from core.zcash.keys import ZcashKeyRefused

    with pytest.raises(ZcashKeyRefused):
        _lane(chain).set_viewing_key(SEED_PHRASE)
    from core.zcash import keys

    assert keys.load_viewing_key("test") is None
    assert chain.calls() == []
    assert not (chain.wallet / "data.sqlite").exists()


def test_setup_keeps_the_key_in_the_credential_store_and_derives_a_shielded_address(chain) -> None:
    from core.zcash import keys

    lane = _lane(chain)
    out = lane.set_viewing_key(UFVK_TEST)
    assert out["address"] == ADDRESS
    assert keys.load_viewing_key("test").encoded == UFVK_TEST
    assert chain.subcommands() == ["init-fvk", "list-addresses"]
    assert stat.S_IMODE(os.stat(chain.wallet).st_mode) == 0o700


def test_the_viewing_key_reaches_the_tool_on_stdin_never_in_its_arguments(chain) -> None:
    _ready_lane(chain)
    [init] = [call for call in chain.calls() if "init-fvk" in call]
    assert init[init.index("--fvk") + 1] == "-"
    assert not any(UFVK_TEST in arg for call in chain.calls() for arg in call)
    with sqlite3.connect(chain.wallet / "data.sqlite") as db:  # the tool got the key: it watches exactly that account
        assert db.execute("SELECT ufvk, has_spend_key FROM accounts").fetchall() == [(UFVK_TEST, 0)]


def test_a_timed_out_setup_keeps_no_trace_of_the_key_in_the_exception_chain(chain) -> None:
    chain.overrides["init-fvk"] = {"rc": 0, "sleep": 3}
    chain.write()
    devtool = Devtool(wallet_dir=chain.wallet)
    original = devtool.run
    devtool.run = lambda sub, args=None, **kw: original(sub, args, **{**kw, "timeout": 0.5})
    with pytest.raises(ZcashWatchError) as hung:
        ZcashLane(devtool=devtool).set_viewing_key(UFVK_TEST)
    assert hung.value.code == "devtool_timeout" and hung.value.__cause__ is None and hung.value.__suppress_context__
    assert UFVK_TEST not in str(hung.value)


def test_every_file_holding_wallet_or_invoice_data_is_owner_only(chain) -> None:
    old = os.umask(0o022)
    try:
        lane = _ready_lane(chain)
        lane.create_invoice("0.05", payer="Ana")
        lane.refresh()
    finally:
        os.umask(old)
    data = chain.root / "data"
    for folder in (data, data / "test", chain.wallet, data / "bin"):
        assert stat.S_IMODE(os.stat(folder).st_mode) == 0o700, folder
    for path in (chain.wallet / "data.sqlite", chain.wallet / "watch_state.json", chain.wallet / "receiving_address.txt",
                 data / "test" / "invoices.sqlite"):
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600, path


@pytest.mark.skipif(os.name != "posix", reason="POSIX owner and mode bits")
def test_a_wallet_folder_others_can_write_is_refused_when_it_cannot_be_tightened(chain, monkeypatch) -> None:
    lane = _ready_lane(chain)
    os.chmod(chain.wallet, 0o777)
    monkeypatch.setattr(os, "chmod", lambda path, mode, **kw: None)  # a folder VOOL cannot tighten (another owner)
    with pytest.raises(ZcashWatchError) as unsafe:
        lane.devtool.run("list-tx", ["--json"])
    assert unsafe.value.code == "data_dir_unsafe"
    assert chain.subcommands() == ["init-fvk", "list-addresses"]


def test_set_key_never_swaps_the_watched_key_silently(chain) -> None:
    from core.zcash import keys

    lane = _ready_lane(chain)
    with pytest.raises(ZcashWatchError) as other:
        lane.set_viewing_key(UFVK_TEST_OTHER)
    assert other.value.code == "already_initialized"
    assert keys.load_viewing_key("test").encoded == UFVK_TEST
    assert lane.set_viewing_key(UFVK_TEST)["address"] == ADDRESS  # the same key again is fine
    assert chain.subcommands().count("init-fvk") == 1


def test_a_key_the_store_cannot_keep_leaves_no_ready_claim(chain, monkeypatch) -> None:
    import core.credential_store as store
    from core.zcash.keys import ZcashKeyRefused

    with monkeypatch.context() as dropping:
        dropping.setattr(store, "store_credential", lambda name, value, label="": None)
        with pytest.raises(ZcashKeyRefused) as refused:
            _lane(chain).set_viewing_key(UFVK_TEST)
    assert refused.value.code == "keychain_unavailable"
    assert _lane(chain).set_viewing_key(UFVK_TEST)["address"] == ADDRESS  # retry saves it; the wallet is not rebuilt
    assert chain.subcommands().count("init-fvk") == 1


def test_a_wallet_watching_another_key_confirms_nothing(chain) -> None:
    lane = _ready_lane(chain)
    invoice = lane.create_invoice("0.05")
    chain.pay(invoice.memo, 5_000_000, height=990)
    with sqlite3.connect(chain.wallet / "data.sqlite") as db:
        db.execute("UPDATE accounts SET ufvk = ?", (UFVK_TEST_OTHER,))
    report = lane.refresh()
    assert (report["fresh"], report["reason"], [s.state for s in report["statuses"]]) == (False, "wallet_key_mismatch", ["unknown"])
    with sqlite3.connect(chain.wallet / "data.sqlite") as db:
        db.execute("UPDATE accounts SET ufvk = ?, has_spend_key = 1", (UFVK_TEST,))
    assert lane.refresh()["reason"] == "wallet_key_mismatch"


def test_received_notes_must_cross_check_with_the_tools_own_listing(chain) -> None:
    lane = _ready_lane(chain)
    chain.pay("VOOL-x", 10, height=990)
    chain.overrides["list-tx"] = {"rc": 0, "out": "[]"}
    chain.write()
    with pytest.raises(ZcashWatchError) as mismatch:
        lane.devtool.received_notes()
    assert mismatch.value.code == "tx_crosscheck_failed"


def test_change_internal_and_transparent_outputs_never_carry_an_invoice_memo(chain) -> None:
    lane = _ready_lane(chain)
    paid = chain.pay("VOOL-a", 5, height=990)
    chain.pay("VOOL-a", 7, height=990, change=True)
    chain.pay("VOOL-a", 9, height=990, internal=True)
    chain.pay("VOOL-a", 11, height=990, pool=0)
    notes = lane.devtool.received_notes()
    assert [(n.txid, n.memo) for n in notes if n.memo] == [(paid, "VOOL-a")]
    assert [(n.pool, n.memo) for n in notes if n.pool == "transparent"] == [("transparent", None)]


def test_memo_decoding_follows_zip_302() -> None:
    assert decode_memo(_memo("VOOL-ab12")) == "VOOL-ab12"
    assert decode_memo(b"\xf6" + b"\x00" * 511) is None
    assert decode_memo(b"\xff" + b"\x01" * 511) is None
    assert decode_memo(b"\xc3\x28" + b"\x00" * 510) is None
    assert decode_memo(None) is None


# --- amounts and the payment request -----------------------------------------------------------

def test_amounts_are_exact_zatoshis_and_nonsense_is_refused() -> None:
    assert parse_amount("0.05") == 5_000_000
    assert parse_amount("1,5 ZEC") == 150_000_000
    assert parse_amount("0.00000001") == 1
    assert parse_amount("1000") == 1000 * 100_000_000 and parse_amount(".5") == 50_000_000 and parse_amount("0,500") == 50_000_000
    assert parse_amount("1.0000") == 100_000_000 and parse_amount("12.50") == 1_250_000_000
    for bad in ("0", "-1", "abc", "0.000000001", "nan", "inf", "21000001", "", "1.", ".", "1e3", "1 000", "1'000", "1.000.000",
                "1,000.5", "+1", "0x10", "١٢"):
        with pytest.raises(InvoiceError):
            parse_amount(bad)
    for thousands in ("1,000", "12,500 ZEC", "1.000", "2,000 TAZ"):  # thousands in half the world: refused, never guessed
        with pytest.raises(InvoiceError) as ambiguous:
            parse_amount(thousands)
        assert ambiguous.value.code == "amount_ambiguous"
    assert format_zec(5_000_000) == "0.05" and format_zec(100_000_000) == "1"


def test_the_payment_request_is_a_zip_321_uri_carrying_the_memo(chain) -> None:
    invoice = _ready_lane(chain).create_invoice("0.05", label="Logo for Ana & co", payer="Ana")
    uri = payment_uri(invoice)
    parts = urlsplit(uri)
    assert parts.scheme == "zcash" and parts.path == ADDRESS
    query = parse_qs(parts.query)
    assert query["amount"] == ["0.05"]
    memo = query["memo"][0]
    assert "=" not in memo and base64.urlsafe_b64decode(memo + "=" * (-len(memo) % 4)).decode() == invoice.memo
    assert query["message"] == ["Logo for Ana & co"]
    assert invoice.memo == "VOOL-" + invoice.invoice_id


# --- the matcher: every state is explicit, doubt is unknown -------------------------------------

def _invoice(store: InvoiceStore, zat: int):
    return store.create(address=ADDRESS, amount_zat=zat, network="test")


def _note(memo: str | None, zat: int, height: int | None, pool: str = "orchard") -> ReceivedNote:
    return ReceivedNote(txid=hashlib.sha256(f"{memo}{zat}{height}".encode()).hexdigest(), pool=pool, output_index=0, value_zat=zat,
                        memo=memo, mined_height=height, block_time=None)


def test_match_states(tmp_path) -> None:
    store = InvoiceStore(tmp_path / "inv.sqlite")
    exact, over, under, waiting, unpaid, near = (_invoice(store, 100) for _ in range(6))
    fresh = matcher.Freshness(True, "", 1000, time.time())
    notes = [
        _note(exact.memo, 100, 998),          # 3 confirmations
        _note(over.memo, 150, 990),
        _note(under.memo, 60, 990),
        _note(waiting.memo, 100, 999),        # 2 confirmations
        _note(near.memo + "0", 100, 990),     # a memo that only starts like the invoice's
        _note(unpaid.memo, 100, 990, pool="transparent"),
    ]
    states = {s.invoice.invoice_id: s.state for s in matcher.match_invoices(store.all(network="test"), notes, fresh, required=3)}
    assert states == {exact.invoice_id: "paid", over.invoice_id: "overpaid", under.invoice_id: "underpaid",
                      waiting.invoice_id: "unconfirmed", unpaid.invoice_id: "unpaid", near.invoice_id: "unpaid"}


@pytest.mark.parametrize(
    ("state", "reason"),
    [({}, "never_synced"),
     ({"last_sync_at": time.time() - 10, "tip": 5, "attempt_seq": 2, "ok_seq": 1, "last_error": "devtool_failed"}, "sync_failed"),
     # the clock stepped back between the good sync and the failed attempt: the sequence still shows the failure
     ({"last_sync_at": time.time() - 10, "tip": 5, "last_attempt_at": time.time() - 20, "attempt_seq": 2, "ok_seq": 1,
       "last_error": "devtool_failed"}, "sync_failed"),
     ({"last_sync_at": time.time() - 10, "tip": 5, "attempt_seq": 2, "ok_seq": 1, "last_error": "in_progress"}, "sync_failed"),
     ({"last_sync_at": time.time() + 3600, "tip": 5, "attempt_seq": 1, "ok_seq": 1, "last_error": ""}, "clock_moved"),
     ({"last_sync_at": time.time() - 3600, "tip": 5, "attempt_seq": 1, "ok_seq": 1, "last_error": ""}, "stale_sync")],
)
def test_without_a_fresh_sync_nothing_is_paid_or_unpaid(tmp_path, state, reason) -> None:
    store = InvoiceStore(tmp_path / "inv.sqlite")
    invoice = _invoice(store, 100)
    fresh = matcher.freshness(state, stale_after=900)
    assert (fresh.fresh, fresh.reason) == (False, reason)
    [status] = matcher.match_invoices(store.all(network="test"), [_note(invoice.memo, 100, 1)], fresh, required=3)
    assert (status.state, status.reason) == ("unknown", reason)


# --- refresh: receipts once, memory with evidence, failure stays unknown ----------------------------

def test_a_payment_becomes_paid_after_confirmations_and_is_receipted_once(chain) -> None:
    from core.blackbox.store import default_store
    from core.context_namespace import ensure_chat_namespace
    from core.memory.entries import list_memory_entries

    lane = _ready_lane(chain)
    invoice = lane.create_invoice("0.05", label="logo", payer="Ana")
    txid = chain.pay(invoice.memo, 5_000_000, height=1000)
    first = lane.refresh()
    assert [s.state for s in first["statuses"]] == ["unconfirmed"] and first["receipted"] == []

    chain.tip = 1002
    chain.write()
    session = "zcash-test-chat"
    ensure_chat_namespace(session)
    second = lane.refresh(source_context={"session_id": session})
    assert [s.state for s in second["statuses"]] == ["paid"]
    [receipt] = second["receipted"]
    assert (receipt["txids"], receipt["received_zat"], receipt["heights"]) == ([txid], 5_000_000, [1000])
    entries = [e for e in default_store().entries() if e.get("kind") == "zec_received"]
    assert [e["invoice_id"] for e in entries] == [invoice.invoice_id]
    facts = [e for e in list_memory_entries(chat_id=session, limit=50) if invoice.invoice_id in str(e.get("text") or "")]
    assert len(facts) == 1 and txid in facts[0]["text"] and invoice.memo in facts[0]["text"]

    third = lane.refresh(source_context={"session_id": session})
    assert third["receipted"] == [] and [s.state for s in third["statuses"]] == ["paid"]
    assert len([e for e in default_store().entries() if e.get("kind") == "zec_received"]) == 1
    assert lane.store.get(invoice.invoice_id).paid_txid == txid


def test_a_failed_sync_leaves_open_invoices_unknown_and_receipts_nothing(chain) -> None:
    lane = _ready_lane(chain)
    invoice = lane.create_invoice("0.05")
    chain.pay(invoice.memo, 5_000_000, height=900)
    chain.overrides["sync"] = {"rc": 1, "err": "Error: connection refused"}
    chain.write()
    report = lane.refresh()
    assert report["fresh"] is False and report["reason"] == "never_synced"
    assert [s.state for s in report["statuses"]] == ["unknown"] and report["receipted"] == []
    assert "connection refused" in report["sync_error"]


def _journal(kind: str = "zec_received") -> list[dict]:
    from core.blackbox.store import default_store

    return [e for e in default_store().entries() if e.get("kind") == kind]


def test_one_output_listed_twice_counts_once_and_conflicting_rows_confirm_nothing(chain) -> None:
    lane = _ready_lane(chain)
    invoice = lane.create_invoice("0.0000010")  # 100 zat
    chain.pay(invoice.memo, 50, height=990)
    with sqlite3.connect(chain.wallet / "data.sqlite") as db:
        db.execute("INSERT INTO v_tx_outputs SELECT * FROM v_tx_outputs")
    assert [(s.state, s.confirmed_zat) for s in lane.refresh()["statuses"]] == [("underpaid", 50)]
    with sqlite3.connect(chain.wallet / "data.sqlite") as db:
        db.execute("UPDATE v_tx_outputs SET value = 50 + 50 WHERE rowid = (SELECT MAX(rowid) FROM v_tx_outputs)")
    report = lane.refresh()
    assert (report["fresh"], report["reason"], [s.state for s in report["statuses"]]) == (False, "wallet_unreadable", ["unknown"])
    assert report["receipted"] == [] and _journal() == []
    half = _note(invoice.memo, 50, 990)
    [status] = matcher.match_invoices([invoice], [half, half], matcher.Freshness(True, "", 1000, time.time()), required=3)
    assert (status.state, status.confirmed_zat) == ("underpaid", 50)


def test_a_payment_reorged_out_takes_the_invoice_out_of_paid_and_back_when_it_returns(chain) -> None:
    from core.runtime_execution_tools import execute_runtime_tool

    lane = _ready_lane(chain)
    invoice = lane.create_invoice("0.05")
    chain.pay(invoice.memo, 5_000_000, height=1000)
    chain.tip = 1002
    chain.write()
    assert [s.state for s in lane.refresh()["statuses"]] == ["paid"]
    paid_txs = list(chain.txs)
    chain.txs.clear()  # the block, and the payment with it, is reorged out
    chain._db()
    chain.tip = 1003
    chain.write()
    for _ in range(2):
        [status] = lane.refresh()["statuses"]
        assert (status.state, status.reason) == ("reorged", "payment_left_the_chain")
    assert len(_journal("zec_payment_reorged")) == 1 and len(_journal()) == 1
    assert lane.store.get(invoice.invoice_id).reorged is True
    assert "no longer on the chain" in execute_runtime_tool("zcash.invoice.unpaid", {}).response_text
    history = execute_runtime_tool("zcash.payment.history", {})
    assert history.response_text.startswith("No paid Zcash invoices") and "no longer on the chain" in history.response_text
    assert lane.export_csv().splitlines()[1].endswith(",reorged")
    lane.devtool.sync = lambda: (_ for _ in ()).throw(ZcashWatchError("devtool_failed", "offline"))
    [blind] = lane.refresh()["statuses"]
    assert blind.state == "reorged"  # without a fresh read the last known chain state stands
    del lane.devtool.sync
    chain.txs[:] = paid_txs  # mined again
    chain._db()
    chain.write()
    [back] = lane.refresh()["statuses"]
    assert back.state == "paid" and lane.store.get(invoice.invoice_id).reorged is False and len(_journal()) == 1


def test_confirmations_count_only_blocks_the_wallet_has_scanned(chain) -> None:
    lane = _ready_lane(chain)
    invoice = lane.create_invoice("0.05")
    chain.pay(invoice.memo, 5_000_000, height=998)  # 3 confirmations by the server's tip of 1000
    chain.scanned = 950
    chain.write()
    report = lane.refresh()
    assert (report["fresh"], report["reason"], [s.state for s in report["statuses"]]) == (False, "wallet_behind", ["unknown"])
    chain.scanned = 999  # within the lag allowance, but block 1000 is not scanned: 2 confirmations
    chain.write()
    assert [s.state for s in lane.refresh()["statuses"]] == ["unconfirmed"]
    chain.scanned = 1000
    chain.write()
    assert [s.state for s in lane.refresh()["statuses"]] == ["paid"]


def test_a_failed_journal_write_leaves_no_paid_state_and_the_next_refresh_receipts_it(chain, monkeypatch) -> None:
    import core.blackbox.store as blackbox

    lane = _ready_lane(chain)
    invoice = lane.create_invoice("0.05")
    chain.pay(invoice.memo, 5_000_000, height=990)
    with monkeypatch.context() as broken:
        broken.setattr(blackbox, "default_store", lambda: (_ for _ in ()).throw(OSError("disk full")))
        first = lane.refresh()
    assert first["receipted"] == [] and [(s.state, s.reason) for s in first["statuses"]] == [("unknown", "receipt_pending")]
    assert lane.store.get(invoice.invoice_id).paid_txid == ""
    second = lane.refresh()
    assert [r["invoice_id"] for r in second["receipted"]] == [invoice.invoice_id]
    assert [s.state for s in second["statuses"]] == ["paid"] and len(_journal()) == 1
    assert lane.refresh()["receipted"] == [] and len(_journal()) == 1


@pytest.mark.parametrize("where", ["before_journal", "after_journal"])
def test_a_crash_mid_receipt_is_repaired_once_on_restart(chain, monkeypatch, where) -> None:
    import core.zcash.service as service

    lane = _ready_lane(chain)
    invoice = lane.create_invoice("0.05")
    txid = chain.pay(invoice.memo, 5_000_000, height=990)

    def killed(*args, **kwargs):
        raise KeyboardInterrupt("process killed here")

    with monkeypatch.context() as dying:
        if where == "before_journal":
            dying.setattr(service, "journal_once", killed)
        else:
            dying.setattr(InvoiceStore, "commit_paid", killed)
        with pytest.raises(KeyboardInterrupt):
            lane.refresh()
    assert lane.store.get(invoice.invoice_id).paid_txid == ""
    assert len(_journal()) == (1 if where == "after_journal" else 0)
    restarted = ZcashLane(devtool=Devtool(wallet_dir=chain.wallet))
    report = restarted.refresh()
    assert [r["txids"] for r in report["receipted"]] == [[txid]]
    assert len(_journal()) == 1 and restarted.store.get(invoice.invoice_id).paid_receipt_id == _journal()[0]["receipt_id"]


def test_a_receipt_made_from_the_console_is_remembered_in_the_next_chat(chain) -> None:
    from core.context_namespace import ensure_chat_namespace
    from core.memory.entries import list_memory_entries

    lane = _ready_lane(chain)
    invoice = lane.create_invoice("0.05", payer="Ana")
    txid = chain.pay(invoice.memo, 5_000_000, height=990)
    assert len(lane.refresh()["receipted"]) == 1  # the owner console passes no chat session
    session = "zcash-console-then-chat"
    ensure_chat_namespace(session)
    for _ in range(2):
        lane.refresh(source_context={"session_id": session})
        facts = [e for e in list_memory_entries(chat_id=session, limit=50) if invoice.invoice_id in str(e.get("text") or "")]
        assert len(facts) == 1 and txid in facts[0]["text"]


def test_export_lists_paid_invoices_and_defuses_spreadsheet_formulas(chain) -> None:
    lane = _ready_lane(chain)
    paid = lane.create_invoice("0.1", label="=HYPERLINK(evil)", payer="Bo")
    lane.create_invoice("0.2", label="open one")
    txid = chain.pay(paid.memo, 10_000_000, height=990)
    lane.refresh()
    rows = lane.export_csv().splitlines()
    assert len(rows) == 2 and paid.invoice_id in rows[1] and txid in rows[1]
    assert ",'=HYPERLINK(evil)," in rows[1] and "open one" not in "".join(rows)


# --- the switch, the tools, the page ---------------------------------------------------------------

def test_tools_are_absent_while_the_lane_is_off_and_present_when_on(chain, monkeypatch) -> None:
    from core.runtime_tool_contracts import runtime_tool_contracts

    names = {"zcash.invoice.create", "zcash.invoice.unpaid", "zcash.payment.history"}
    assert names <= {c.intent for c in runtime_tool_contracts()}
    monkeypatch.setenv("VOOL_ZCASH_ENABLED", "0")
    assert not names & {c.intent for c in runtime_tool_contracts()}


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("Bill Ana 0.05 ZEC for the logo", "zcash.invoice.create"),
        ("Who still owes me ZEC?", "zcash.invoice.unpaid"),
        ("What did Ana pay me in zcash this quarter?", "zcash.payment.history"),
        ("Export my ZEC payments for Q3", "zcash.payment.history"),
    ],
)
def test_the_owners_words_seat_the_tool_only_while_the_lane_is_on(chain, monkeypatch, text, intent) -> None:
    from core.tool_offer_assembly import assemble_tool_offer

    def seated(words: str) -> set[str]:
        return {i for i in assemble_tool_offer(user_text=words).intents if i.startswith("zcash.")}

    assert intent in seated(text)
    assert seated("Send 0.1 SOL to Bob") == set() and seated("Pay the electricity bill") == set()
    monkeypatch.setenv("VOOL_ZCASH_ENABLED", "0")
    assert seated(text) == set()


def _decide(intent: str, mode: str) -> tuple[str, tuple[str, ...]]:
    from core.mode_permission_policy import _decide_tool_call

    decision, _ = _decide_tool_call(intent=intent, arguments={}, task_id="zcash-test", source_context={"operating_mode": mode}, consume=False)
    return decision.effect.value, tuple(a.value for a in decision.actions)


def test_the_syncing_tools_obey_the_operating_mode_like_every_network_tool(chain) -> None:
    for intent in ("zcash.invoice.unpaid", "zcash.payment.history"):
        assert _decide(intent, "plan") == ("deny", ("read_files", "use_network_access"))
        assert _decide(intent, "manual")[0] == "require_approval" and "use_network_access" in _decide(intent, "manual")[1]
    assert _decide("zcash.invoice.create", "plan") == ("allow", ("read_files",))
    assert _decide("zcash.invoice.create", "manual") == ("allow", ("read_files",))


def test_local_only_contacts_no_zcash_server_and_answers_unknown(chain, monkeypatch) -> None:
    import core.policy_engine as policy
    from core.runtime_execution_tools import execute_runtime_tool

    lane = _ready_lane(chain)
    invoice = lane.create_invoice("0.05")
    chain.pay(invoice.memo, 5_000_000, height=990)
    before = len(chain.calls())
    monkeypatch.setattr(policy, "local_only_mode", lambda: True)
    for intent in ("zcash.invoice.unpaid", "zcash.payment.history"):
        answer = execute_runtime_tool(intent, {})
        assert "Local Only is on" in answer.response_text and "paid" not in answer.response_text.replace("No paid", "").replace("unpaid", "")
    unpaid = execute_runtime_tool("zcash.invoice.unpaid", {})
    assert unpaid.status == "unknown" and ": unknown" in unpaid.response_text
    with pytest.raises(ZcashWatchError) as refused:
        lane.devtool.run("sync")
    assert refused.value.code == "local_only"
    assert chain.calls()[before:] == []
    assert execute_runtime_tool("zcash.invoice.create", {"amount": "0.01"}).ok  # billing reads the cached address only
    fresh_lane = ZcashLane(devtool=Devtool(wallet_dir=chain.root / "other-wallet"))
    with pytest.raises(ZcashWatchError) as setup:
        fresh_lane.set_viewing_key(UFVK_TEST, source_context={})
    assert setup.value.code == "local_only" and chain.calls()[before:] == []


def test_chat_tools_run_through_the_runtime_door(chain) -> None:
    from core.runtime_execution_tools import execute_runtime_tool

    _ready_lane(chain)
    created = execute_runtime_tool("zcash.invoice.create", {"amount": "0.05", "label": "logo", "payer": "Ana"})
    assert created is not None and created.ok, created.response_text if created else None
    invoice_id = created.details["invoice"]["invoice_id"]
    assert f"memo VOOL-{invoice_id}" in created.response_text and "zcash:" + ADDRESS in created.response_text

    chain.pay("VOOL-" + invoice_id, 5_000_000, height=990)
    history = execute_runtime_tool("zcash.payment.history", {"payer": "ana"})
    assert history.ok and invoice_id in history.response_text and "0.05 TAZ received" in history.response_text
    unpaid = execute_runtime_tool("zcash.invoice.unpaid", {})
    assert unpaid.ok and unpaid.response_text.startswith("No open Zcash invoices")


def test_chat_tools_refuse_when_off_and_never_claim_payment_when_blind(chain, monkeypatch) -> None:
    from core.runtime_execution_tools import execute_runtime_tool

    _ready_lane(chain)
    execute_runtime_tool("zcash.invoice.create", {"amount": "0.05"})
    chain.overrides["sync"] = {"rc": 1, "err": "Error: offline"}
    chain.write()
    blind = execute_runtime_tool("zcash.invoice.unpaid", {})
    assert blind.status == "unknown" and "can't confirm" in blind.response_text and ": unknown" in blind.response_text
    bad = execute_runtime_tool("zcash.invoice.create", {"amount": "zero"})
    assert bad.ok is False and bad.status == "amount_invalid"
    monkeypatch.setenv("VOOL_ZCASH_ENABLED", "0")
    from core.zcash.tools import run_tool

    off = run_tool("zcash.invoice.create", {"amount": "1"}, None)
    assert off.ok is False and off.status == "zcash_disabled"


def test_the_qr_page_is_owner_local_and_escapes_owner_text(chain, monkeypatch) -> None:
    from core.zcash.page import handle_zcash_get

    invoice = _ready_lane(chain).create_invoice("0.05", label="<script>alert(1)</script>")
    query = {"invoice_id": [invoice.invoice_id]}
    page = handle_zcash_get("/api/zcash/invoice/page", query, client_host="127.0.0.1")
    body = page.body.decode()
    assert page.status == 200 and "qrcode = function" in body.replace("var qrcode = function", "qrcode = function")
    assert "<script>alert(1)" not in body and "&lt;script&gt;alert(1)" in body
    assert handle_zcash_get("/api/zcash/invoice/page", query, client_host="192.168.1.20").status == 403
    assert handle_zcash_get("/api/zcash/invoice/page", {"invoice_id": ["nope"]}, client_host="127.0.0.1").status == 404
    assert handle_zcash_get("/api/zcash/export.csv", {"since": ["2026-13"]}, client_host="127.0.0.1").status == 400
    monkeypatch.setenv("VOOL_ZCASH_ENABLED", "0")
    assert handle_zcash_get("/api/zcash/invoice/page", query, client_host="127.0.0.1").status == 404
