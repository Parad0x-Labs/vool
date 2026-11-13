"""A software FIDO2 authenticator that speaks real CTAP2 over the fido2 library's device interface.

It stands in for a YubiKey in the cloud, where no USB key exists. It answers GetInfo, MakeCredential, GetAssertion
(including the silent up=false pre-check the client makes) and ClientPin GetKeyAgreement, and it implements the
hmac-secret extension the way the CTAP 2.1 spec says: an ECDH shared secret with the platform (PIN/UV protocol 1 or
2), the salt decrypted and authenticated, HMAC-SHA256 with a per-credential random kept inside the "key", the output
encrypted back. So VOOL's real fido2 client code runs end to end against it; only the USB wire is skipped.

The credential secret lives only in this object, like on a real key. A second SoftKey (same model) holds different
secrets and cannot answer for another key's credential.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import struct
from dataclasses import dataclass, field
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fido2 import cbor
from fido2.cose import ES256
from fido2.ctap import CtapDevice, CtapError
from fido2.ctap2.pin import PinProtocolV1, PinProtocolV2
from fido2.hid import CAPABILITY, CTAPHID
from fido2.utils import bytes2int, int2bytes

AAGUID = bytes.fromhex("5ec0f1d0" + "00" * 12)


@dataclass
class _Descriptor:
    product_name: str = "Soft Security Key"


@dataclass
class SoftKey(CtapDevice):
    """One software key. ``touch`` False means nobody touches it (the request waits, then times out)."""

    name: str = "Soft Security Key"
    touch: bool = True
    supports_hmac_secret: bool = True
    client_pin_set: bool = False
    make_cred_uv_not_required: bool = True
    credentials: dict[bytes, dict[str, Any]] = field(default_factory=dict)
    touches: int = 0
    _agreement: ec.EllipticCurvePrivateKey = field(default_factory=lambda: ec.generate_private_key(ec.SECP256R1()))
    _wrap: bytes = field(default_factory=lambda: os.urandom(32))

    @property
    def descriptor(self) -> _Descriptor:
        return _Descriptor(self.name)

    @property
    def capabilities(self) -> int:
        return CAPABILITY.CBOR | CAPABILITY.WINK

    def close(self) -> None:
        pass

    @classmethod
    def list_devices(cls):  # pragma: no cover - VOOL's test source lists keys itself
        return iter(())

    # --- CTAP2 -------------------------------------------------------------------------------------------------

    def call(self, cmd, data=b"", event=None, on_keepalive=None):
        if cmd != CTAPHID.CBOR:
            raise CtapError(CtapError.ERR.INVALID_COMMAND)
        command, payload = data[0], (cbor.decode(data[1:]) if len(data) > 1 else {})
        try:
            if command == 0x04:
                answer = self._get_info()
            elif command == 0x01:
                answer = self._make_credential(payload, event)
            elif command == 0x02:
                answer = self._get_assertion(payload, event)
            elif command == 0x06:
                answer = self._client_pin(payload)
            else:
                return bytes([CtapError.ERR.INVALID_COMMAND])
        except CtapError as exc:
            return bytes([exc.code])
        return b"\x00" + cbor.encode(answer)

    def _get_info(self) -> dict[int, Any]:
        options = {"rk": True, "up": True, "plat": False, "clientPin": self.client_pin_set}
        if self.make_cred_uv_not_required:
            options["makeCredUvNotRqd"] = True
        return {1: ["FIDO_2_0", "FIDO_2_1"], 2: ["hmac-secret"] if self.supports_hmac_secret else [], 3: AAGUID,
                4: options, 5: 1200, 6: [2, 1]}

    def _wait_for_touch(self, event) -> None:
        if self.touch:
            self.touches += 1
            return
        if event is not None:
            event.wait(5)
        raise CtapError(CtapError.ERR.KEEPALIVE_CANCEL if event is not None and event.is_set() else CtapError.ERR.USER_ACTION_TIMEOUT)

    def _make_credential(self, req: dict[int, Any], event) -> dict[int, Any]:
        client_data_hash, rp, _user, params = req[1], req[2], req[3], req[4]
        if not any(p.get("alg") == -7 for p in params):
            raise CtapError(CtapError.ERR.UNSUPPORTED_ALGORITHM)
        if self.client_pin_set and not self.make_cred_uv_not_required and 8 not in req:
            raise CtapError(CtapError.ERR.PUAT_REQUIRED)
        self._wait_for_touch(event)
        private = ec.generate_private_key(ec.SECP256R1())
        credential_id = os.urandom(48)
        wanted = bool((req.get(6) or {}).get("hmac-secret")) and self.supports_hmac_secret
        self.credentials[credential_id] = {"rp_id": rp["id"], "private": private, "cred_random": os.urandom(32), "count": 0}
        cose = dict(ES256.from_cryptography_key(private.public_key()))
        attested = AAGUID + struct.pack(">H", len(credential_id)) + credential_id + cbor.encode(cose)
        flags = 0x01 | 0x40 | (0x80 if wanted else 0)
        auth_data = hashlib.sha256(rp["id"].encode()).digest() + bytes([flags]) + struct.pack(">I", 0) + attested
        if wanted:
            auth_data += cbor.encode({"hmac-secret": True})
        del client_data_hash
        return {1: "none", 2: auth_data, 3: {}}

    def _find(self, rp_id: str, allow: list[dict[str, Any]]) -> tuple[bytes, dict[str, Any]] | None:
        for descriptor in allow or []:
            found = self.credentials.get(bytes(descriptor.get("id") or b""))
            if found is not None and found["rp_id"] == rp_id:
                return bytes(descriptor["id"]), found
        return None

    def _get_assertion(self, req: dict[int, Any], event) -> dict[int, Any]:
        rp_id, client_data_hash, allow = req[1], req[2], req.get(3) or []
        options = req.get(5) or {}
        hit = self._find(rp_id, allow)
        if hit is None:
            raise CtapError(CtapError.ERR.NO_CREDENTIALS)
        credential_id, credential = hit
        user_present = options.get("up", True)
        if user_present:
            self._wait_for_touch(event)
        extensions_out: dict[str, Any] = {}
        hmac_in = (req.get(4) or {}).get("hmac-secret")
        if hmac_in is not None:
            if not self.supports_hmac_secret:
                raise CtapError(CtapError.ERR.UNSUPPORTED_EXTENSION)
            protocol = PinProtocolV2() if int(hmac_in.get(4, 1)) == 2 else PinProtocolV1()
            peer = hmac_in[1]
            point = ec.EllipticCurvePublicNumbers(bytes2int(peer[-2]), bytes2int(peer[-3]), ec.SECP256R1()).public_key()
            shared = protocol.kdf(self._agreement.exchange(ec.ECDH(), point))
            if not hmac.compare_digest(protocol.authenticate(shared, hmac_in[2]), hmac_in[3]):
                raise CtapError(CtapError.ERR.INVALID_PARAMETER)
            salts = protocol.decrypt(shared, hmac_in[2])
            outputs = b"".join(hmac.new(credential["cred_random"], salts[i:i + 32], hashlib.sha256).digest() for i in range(0, len(salts), 32))
            extensions_out["hmac-secret"] = protocol.encrypt(shared, outputs)
        credential["count"] += 1
        flags = (0x01 if user_present else 0) | (0x80 if extensions_out else 0)
        auth_data = hashlib.sha256(rp_id.encode()).digest() + bytes([flags]) + struct.pack(">I", credential["count"])
        if extensions_out:
            auth_data += cbor.encode(extensions_out)
        signature = credential["private"].sign(auth_data + client_data_hash, ec.ECDSA(hashes.SHA256()))
        return {1: {"type": "public-key", "id": credential_id}, 2: auth_data, 3: signature}

    def _client_pin(self, req: dict[int, Any]) -> dict[int, Any]:
        if req.get(2) != 0x02:  # only getKeyAgreement
            raise CtapError(CtapError.ERR.INVALID_COMMAND)
        numbers = self._agreement.public_key().public_numbers()
        return {1: {1: 2, 3: -25, -1: 1, -2: int2bytes(numbers.x, 32), -3: int2bytes(numbers.y, 32)}}
