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
import struct
import threading
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

#: How long the person has to read and press on the Ledger. A send's claim on the money is held 30 seconds
#: (``settlement.SIGNER_BUDGET_SECONDS``), so a signature never arrives after its claim lapsed.
SIGN_TIMEOUT_SECONDS = 25.0
ADDRESS_TIMEOUT_SECONDS = 60.0

#: Solana's standard account path, 44'/501'/<account>'/0' (Phantom, Solflare and Ledger Live agree on it).
HARDENED = 0x80000000


class LedgerProblem(Exception):
    """A plain reason the Ledger could not answer: no_ledger, more_than_one_ledger, ledger_locked, open_solana_app,
    refused_on_ledger, ledger_timeout, not_this_ledger, ledger_error."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class Transport(Protocol):
    def exchange(self, apdu: bytes, *, timeout: float = ...) -> tuple[bytes, int]: ...

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

    def exchange(self, apdu: bytes, *, timeout: float = ADDRESS_TIMEOUT_SECONDS) -> tuple[bytes, int]:
        for packet in frame(apdu):
            self._device.write(b"\x00" + packet)
        answer = unframe(lambda: bytes(self._device.read(PACKET, int(timeout * 1000))))
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


def _pubkey(transport: Transport, path: list[int], *, confirm: bool) -> str:
    data, status = transport.exchange(bytes([CLA, INS_GET_PUBKEY, P1_CONFIRM if confirm else P1_NON_CONFIRM, 0x00]) + _with_len(_path_bytes(path)))
    _check(status)
    if len(data) != 32:
        raise LedgerProblem("ledger_error")
    return b58encode(bytes(data))


def _with_len(body: bytes) -> bytes:
    if len(body) > MAX_CHUNK:
        raise LedgerProblem("ledger_error")
    return bytes([len(body)]) + body


def plugged_in() -> bool:
    with _LOCK:
        transports = _transports()
        for transport in transports:
            transport.close()
        return bool(transports)


def public_key(path: list[int], *, confirm: bool = False) -> str:
    """The Solana address at ``path`` on the one Ledger plugged in. ``confirm`` shows it on the Ledger's screen."""
    with _LOCK:
        transport = _one(_transports())
        try:
            return _pubkey(transport, path, confirm=confirm)
        finally:
            transport.close()


def sign_solana_message(path: list[int], message: bytes, *, expected_public_key: str) -> bytes:
    """Ask the Ledger to sign this exact Solana transaction message. The person reads it and presses the buttons on
    the Ledger. The Ledger must hold ``expected_public_key`` at ``path``; the 64-byte signature is checked before it is
    returned."""
    message = bytes(message)
    if not message or len(message) > MAX_MESSAGE_BYTES:
        raise LedgerProblem("ledger_error")
    with _LOCK:
        transport = _one(_transports())
        try:
            if _pubkey(transport, path, confirm=False) != expected_public_key:
                raise LedgerProblem("not_this_ledger")
            head = bytes([1]) + _path_bytes(path)
            payload = head + message
            chunks = [payload[i:i + MAX_CHUNK] for i in range(0, len(payload), MAX_CHUNK)]
            signature = b""
            for index, chunk in enumerate(chunks):
                p2 = (P2_EXTEND if index else 0) | (P2_MORE if index < len(chunks) - 1 else 0)
                data, status = transport.exchange(bytes([CLA, INS_SIGN_MESSAGE, P1_CONFIRM, p2]) + _with_len(chunk), timeout=SIGN_TIMEOUT_SECONDS)
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


__all__ = ["LedgerProblem", "library_available", "parse_path", "path_text", "plugged_in", "public_key", "set_transport_source_for_tests",
           "sign_solana_message", "solana_path"]
