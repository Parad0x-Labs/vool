"""A Ledger hardware wallet (Nano S Plus, Nano X over USB, Stax, Flex) as the signer of a Solana wallet.

The wallet's key lives only on the Ledger. VOOL asks the Ledger's Solana app for the public key at a path, and later
hands it the exact transaction message to sign; the person reads the transfer on the Ledger's own screen and presses
its buttons. VOOL never sees the key, and a signature the Ledger returns is checked against the public key before it
is used (:mod:`core.wallet.external_signing` checks it again before anything is sent).

This module only talks to the Ledger over USB HID with the Ledger APDU framing. It decides nothing about money: the
quote, caps, approval and receipt path stays where it is, and the Ledger is only the hand that signs.
"""
from __future__ import annotations

import contextlib
import secrets
import struct
import threading
import time
from collections.abc import Callable, Iterable
from typing import Protocol

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from core.vool_wallet import b58encode, decode_solana_pubkey

AUTHORITY = "core.wallet.ledger"
LEDGER_VENDOR_ID = 0x2C97
CHANNEL = 0x0101
TAG_APDU = 0x05
PACKET = 64

CLA = 0xE0
INS_GET_APP_CONFIGURATION = 0x04
INS_GET_PUBKEY = 0x05
INS_SIGN_MESSAGE = 0x06
P1_NON_CONFIRM = 0x00
P1_CONFIRM = 0x01
P2_EXTEND = 0x01
P2_MORE = 0x02
MAX_CHUNK = 255
MAX_MESSAGE_BYTES = 1232  # one Solana packet; a longer message is not a transaction VOOL builds

SW_OK = 0x9000
SW_USER_REFUSED = 0x6985
SW_LOCKED = 0x5515
SW_WRONG_APP = (0x6E00, 0x6D00, 0x6E01, 0x6511, 0x6A82)

#: ONE deadline for everything a send asks of the Ledger: waiting for the Ledger to be free, the address check and the
#: press. A send's claim on the money is held 30 seconds (``settlement.SIGNER_BUDGET_SECONDS``) from just before the
#: signer is asked; this budget ends a few seconds before that, so a press never lands after its claim lapsed.
SIGN_BUDGET_SECONDS = 26.0
#: Kept for callers that show the time: the person has about this long to read and press.
SIGN_TIMEOUT_SECONDS = 25.0
#: How long one approval waits for another one using the same Ledger before it says the Ledger is busy.
LOCK_WAIT_SECONDS = 2.0
#: The address check before a press needs no press, so a Ledger that does not answer within this is not ready.
ADDRESS_CHECK_SECONDS = 5.0
#: Adding a Ledger: time to read the proof transfer on the Ledger and press.
ADDRESS_TIMEOUT_SECONDS = 60.0

#: Solana's standard account path, 44'/501'/<account>'/0' (Phantom, Solflare and Ledger Live agree on it).
HARDENED = 0x80000000


class LedgerProblem(Exception):
    """A plain reason the Ledger could not answer: no_ledger, more_than_one_ledger, ledger_locked, open_solana_app,
    refused_on_ledger, ledger_timeout, ledger_busy, not_this_ledger, ledger_error."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class Transport(Protocol):
    def exchange(self, apdu: bytes, *, timeout: float = ...) -> tuple[bytes, int]: ...

    def drain(self) -> None: ...

    def close(self) -> None: ...


#: Where Ledgers come from: real USB devices by default; a test puts a software Ledger here.
_transport_source: Callable[[], Iterable[Transport]] | None = None
_LOCK = threading.Lock()


def set_transport_source_for_tests(source: Callable[[], Iterable[Transport]] | None) -> None:
    global _transport_source
    _transport_source = source


def library_available() -> bool:
    try:
        import hid  # noqa: F401
    except Exception:
        return False
    return True


def solana_path(account: int = 0) -> list[int]:
    if not 0 <= int(account) < 1000:
        raise LedgerProblem("ledger_error")
    return [44 | HARDENED, 501 | HARDENED, int(account) | HARDENED, 0 | HARDENED]


def path_text(path: list[int]) -> str:
    return "m/" + "/".join(f"{part & ~HARDENED}'" if part & HARDENED else str(part) for part in path)


def parse_path(text: str) -> list[int]:
    """``m/44'/501'/0'/0'`` back to its numbers (only the shape :func:`path_text` writes)."""
    parts = str(text or "").split("/")
    if not parts or parts[0] != "m" or not 2 <= len(parts) <= 6:
        raise LedgerProblem("ledger_error")
    path = []
    for part in parts[1:]:
        hardened = part.endswith("'")
        number = part[:-1] if hardened else part
        if not number.isdigit() or int(number) >= HARDENED:
            raise LedgerProblem("ledger_error")
        path.append(int(number) | (HARDENED if hardened else 0))
    return path


def _path_bytes(path: list[int]) -> bytes:
    if not 1 <= len(path) <= 5:
        raise LedgerProblem("ledger_error")
    return bytes([len(path)]) + b"".join(struct.pack(">I", part) for part in path)


# --- USB HID framing ------------------------------------------------------------------------------------

def frame(apdu: bytes) -> list[bytes]:
    """Split an APDU into 64-byte HID packets: channel, tag, sequence, and the length in the first packet."""
    data = struct.pack(">H", len(apdu)) + bytes(apdu)
    packets, sequence = [], 0
    while True:
        header = struct.pack(">HBH", CHANNEL, TAG_APDU, sequence)
        room = PACKET - len(header)
        chunk, data = data[:room], data[room:]
        packets.append((header + chunk).ljust(PACKET, b"\x00"))
        sequence += 1
        if not data:
            return packets


def unframe(read: Callable[[], bytes]) -> bytes:
    """Read packets until the whole answer is in; returns the answer with its two status bytes."""
    expected, body, sequence = None, b"", 0
    while expected is None or len(body) < expected:
        packet = bytes(read() or b"")
        if not packet:
            raise LedgerProblem("ledger_timeout")
        if len(packet) < 5:
            raise LedgerProblem("ledger_error")
        channel, tag, seq = struct.unpack(">HBH", packet[:5])
        if channel != CHANNEL or tag != TAG_APDU or seq != sequence:
            raise LedgerProblem("ledger_error")
        payload = packet[5:]
        if sequence == 0:
            expected = struct.unpack(">H", payload[:2])[0]
            payload = payload[2:]
        body += payload
        sequence += 1
    return body[:expected]


class HidTransport:
    def __init__(self, path: bytes):
        import hid

        self._device = hid.device()
        self._device.open_path(path)
        self._device.set_nonblocking(False)

    def drain(self) -> None:
        """Throw away any answer still waiting from an earlier exchange (a press that came after VOOL stopped
        waiting), so it is never read as the answer to the next question."""
        # hidapi's read() takes timeout 0 as "wait forever" on a blocking device, so the shortest real wait is 1 ms
        for _ in range(64):
            if not bytes(self._device.read(PACKET, 1) or b""):
                return

    def exchange(self, apdu: bytes, *, timeout: float = ADDRESS_TIMEOUT_SECONDS) -> tuple[bytes, int]:
        for packet in frame(apdu):
            self._device.write(b"\x00" + packet)
        # never 0 ms: hidapi reads that as no time limit at all
        wait_ms = max(1, int(timeout * 1000))
        answer = unframe(lambda: bytes(self._device.read(PACKET, wait_ms)))
        if len(answer) < 2:
            raise LedgerProblem("ledger_error")
        return answer[:-2], struct.unpack(">H", answer[-2:])[0]

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._device.close()


def _hid_paths() -> list[bytes]:
    try:
        import hid
    except Exception:
        return []
    seen, paths = set(), []
    try:
        listed = hid.enumerate(LEDGER_VENDOR_ID, 0)
    except Exception:
        return []
    for info in listed:
        # The Ledger's APDU interface is interface 0 (usage page 0xFFA0 on macOS and Windows).
        if info.get("interface_number", 0) not in (0, -1) and info.get("usage_page") != 0xFFA0:
            continue
        serial = info.get("serial_number") or info.get("path")
        if serial in seen:
            continue
        seen.add(serial)
        paths.append(info["path"])
    return paths


def _transports() -> list[Transport]:
    if _transport_source is not None:
        return list(_transport_source())
    opened: list[Transport] = []
    for path in _hid_paths():
        with contextlib.suppress(Exception):
            opened.append(HidTransport(path))
    return opened


def _check(status: int) -> None:
    if status == SW_OK:
        return
    if status == SW_USER_REFUSED:
        raise LedgerProblem("refused_on_ledger")
    if status == SW_LOCKED:
        raise LedgerProblem("ledger_locked")
    if status in SW_WRONG_APP:
        raise LedgerProblem("open_solana_app")
    raise LedgerProblem("ledger_error")


def _one(transports: list[Transport]) -> Transport:
    if not transports:
        raise LedgerProblem("no_ledger")
    if len(transports) > 1:
        for transport in transports:
            transport.close()
        raise LedgerProblem("more_than_one_ledger")
    return transports[0]


def _exchange(transport: Transport, apdu: bytes, *, timeout: float) -> tuple[bytes, int]:
    if timeout <= 0:
        raise LedgerProblem("ledger_timeout")
    drain = getattr(transport, "drain", None)
    if drain is not None:
        drain()
    return transport.exchange(apdu, timeout=timeout)


@contextlib.contextmanager
def _held(wait: float):
    """The Ledger answers one question at a time. Another approval using it gets "busy" after ``wait`` seconds instead
    of eating its own deadline in a queue."""
    if not _LOCK.acquire(timeout=max(0.0, float(wait))):
        raise LedgerProblem("ledger_busy")
    try:
        yield
    finally:
        _LOCK.release()


def _pubkey(transport: Transport, path: list[int], *, confirm: bool, timeout: float = ADDRESS_TIMEOUT_SECONDS) -> str:
    data, status = _exchange(transport, bytes([CLA, INS_GET_PUBKEY, P1_CONFIRM if confirm else P1_NON_CONFIRM, 0x00]) + _with_len(_path_bytes(path)),
                             timeout=timeout)
    _check(status)
    if len(data) != 32:
        raise LedgerProblem("ledger_error")
    return b58encode(bytes(data))


def _with_len(body: bytes) -> bytes:
    if len(body) > MAX_CHUNK:
        raise LedgerProblem("ledger_error")
    return bytes([len(body)]) + body


def plugged_in() -> bool:
    """Whether a Ledger is plugged in. Never waits behind a press: while an approval holds the Ledger, it is plugged in."""
    if not _LOCK.acquire(blocking=False):
        return True
    try:
        transports = _transports()
        for transport in transports:
            transport.close()
        return bool(transports)
    finally:
        _LOCK.release()


def public_key(path: list[int], *, confirm: bool = False) -> str:
    """The Solana address at ``path`` on the one Ledger plugged in. ``confirm`` shows it on the Ledger's screen.
    An address alone proves nothing (it is public): see :func:`prove_key`."""
    with _held(LOCK_WAIT_SECONDS):
        transport = _one(_transports())
        try:
            return _pubkey(transport, path, confirm=confirm, timeout=ADDRESS_TIMEOUT_SECONDS if confirm else ADDRESS_CHECK_SECONDS)
        finally:
            transport.close()


def proof_message(address: str) -> bytes:
    """A transaction that can never land: a 0 SOL transfer from the address to itself, with 32 fresh random bytes where
    the recent blockhash goes (no cluster has that blockhash, so no node accepts it). Every Solana app version reads
    and signs a plain transfer, so the person sees "0 SOL" to their own address and presses."""
    from solders.hash import Hash
    from solders.message import Message
    from solders.pubkey import Pubkey
    from solders.system_program import TransferParams, transfer

    owner = Pubkey.from_string(str(address))
    instruction = transfer(TransferParams(from_pubkey=owner, to_pubkey=owner, lamports=0))
    return bytes(Message.new_with_blockhash([instruction], owner, Hash(secrets.token_bytes(32))))


def prove_key(path: list[int], address: str) -> None:
    """Prove the Ledger plugged in HOLDS the key of ``address`` at ``path``, not merely that something answers with the
    address (anything can echo a public address). The Ledger signs :func:`proof_message`, a transfer that can never
    land, and the signature must check against the address. Raises :class:`LedgerProblem` otherwise."""
    sign_solana_message(path, proof_message(address), expected_public_key=address, deadline=time.monotonic() + ADDRESS_TIMEOUT_SECONDS)


def sign_solana_message(path: list[int], message: bytes, *, expected_public_key: str, deadline: float | None = None) -> bytes:
    """Ask the Ledger to sign this exact Solana transaction message. The person reads it and presses the buttons on
    the Ledger. The Ledger must hold ``expected_public_key`` at ``path``; the 64-byte signature is checked before it is
    returned. ``deadline`` (``time.monotonic()``) bounds the WHOLE call: the wait for the Ledger, the address check and
    the press; by default :data:`SIGN_BUDGET_SECONDS` from now."""
    message = bytes(message)
    if not message or len(message) > MAX_MESSAGE_BYTES:
        raise LedgerProblem("ledger_error")
    end = float(deadline) if deadline is not None else time.monotonic() + SIGN_BUDGET_SECONDS

    def left() -> float:
        return end - time.monotonic()

    with _held(min(LOCK_WAIT_SECONDS, left())):
        transport = _one(_transports())
        try:
            if _pubkey(transport, path, confirm=False, timeout=min(ADDRESS_CHECK_SECONDS, left())) != expected_public_key:
                raise LedgerProblem("not_this_ledger")
            head = bytes([1]) + _path_bytes(path)
            payload = head + message
            chunks = [payload[i:i + MAX_CHUNK] for i in range(0, len(payload), MAX_CHUNK)]
            signature = b""
            for index, chunk in enumerate(chunks):
                p2 = (P2_EXTEND if index else 0) | (P2_MORE if index < len(chunks) - 1 else 0)
                data, status = _exchange(transport, bytes([CLA, INS_SIGN_MESSAGE, P1_CONFIRM, p2]) + _with_len(chunk), timeout=left())
                _check(status)
                signature = bytes(data)
        finally:
            transport.close()
    if len(signature) != 64:
        raise LedgerProblem("ledger_error")
    try:
        Ed25519PublicKey.from_public_bytes(decode_solana_pubkey(expected_public_key)).verify(signature, message)
    except Exception:
        raise LedgerProblem("not_this_ledger") from None
    return signature


__all__ = ["LedgerProblem", "library_available", "parse_path", "path_text", "plugged_in", "proof_message", "prove_key", "public_key",
           "set_transport_source_for_tests", "sign_solana_message", "solana_path"]
