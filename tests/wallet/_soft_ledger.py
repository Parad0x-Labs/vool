"""A software Ledger running the Solana app's APDUs, for tests: real Ed25519 keys per path, real APDU chunking, and
the person's button press as a switch. It also speaks the 64-byte HID packet framing so the framing is proven too."""
from __future__ import annotations

import hashlib
import struct

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from core.wallet import ledger


class SoftLedger:
    def __init__(self, name: str = "A", *, approve: bool | None = True, app_open: bool = True, locked: bool = False):
        self.name = name
        self.approve = approve  # None: the person never presses (the read times out)
        self.app_open = app_open
        self.locked = locked
        self.shown: list[bytes] = []  # every message the person was shown on the Ledger's screen
        self.closed = 0
        self._pending = b""

    def _key(self, path: bytes) -> Ed25519PrivateKey:
        return Ed25519PrivateKey.from_private_bytes(hashlib.sha256(f"soft-ledger-{self.name}".encode() + path).digest())

    def public_bytes(self, path: list[int]) -> bytes:
        raw = ledger._path_bytes(path)
        return self._key(raw).public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)

    def _answer(self, apdu: bytes) -> tuple[bytes, int]:
        if self.locked:
            return b"", ledger.SW_LOCKED
        if not self.app_open:
            return b"", 0x6E00
        cla, ins, p1, p2, length = apdu[:5]
        data = apdu[5:5 + length]
        if cla != ledger.CLA or len(data) != length:
            return b"", 0x6E00
        if ins == ledger.INS_GET_PUBKEY:
            count = data[0]
            path = data[:1 + 4 * count]
            return self._key(path).public_key().public_bytes(Encoding.Raw, PublicFormat.Raw), ledger.SW_OK
        if ins == ledger.INS_SIGN_MESSAGE:
            if p1 != ledger.P1_CONFIRM:
                return b"", 0x6B00
            self._pending = (self._pending if p2 & ledger.P2_EXTEND else b"") + data
            if p2 & ledger.P2_MORE:
                return b"", ledger.SW_OK
            payload, self._pending = self._pending, b""
            signers, count = payload[0], payload[1]
            if signers != 1:
                return b"", 0x6A80
            path = payload[1:2 + 4 * count]
            message = payload[2 + 4 * count:]
            self.shown.append(message)
            if self.approve is None:
                return b"", -1
            if not self.approve:
                return b"", ledger.SW_USER_REFUSED
            return self._key(path).sign(message), ledger.SW_OK
        return b"", 0x6D00

    def exchange(self, apdu: bytes, *, timeout: float = ledger.ADDRESS_TIMEOUT_SECONDS) -> tuple[bytes, int]:
        # Through the real HID packet framing both ways.
        packets = ledger.frame(apdu)
        received = ledger.unframe(iter(packets).__next__)
        data, status = self._answer(received)
        if status == -1:
            return ledger.unframe(lambda: b""), 0  # no packet before the timeout
        back = ledger.frame(data + struct.pack(">H", status))
        answer = ledger.unframe(iter(back).__next__)
        return answer[:-2], struct.unpack(">H", answer[-2:])[0]

    def close(self) -> None:
        self.closed += 1
