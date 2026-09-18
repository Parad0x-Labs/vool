# VENDORED SNAPSHOT — DO NOT EDIT IN PLACE.
# Source: OX-LIQUEFY (Parad0x Labs, MIT) engines/security_compliance.py
# Pinned upstream commits: 2936ecc (audited lineage) through 822bd0b (HEAD). Snapshot date: 2026-09-02.
# Local modifications: AuditChain/secure_audit_log globals retained but UNUSED by VOOL — VOOL owns all chain state (dossier §36.6).
#!/usr/bin/env python3
"""
NULL_Security_Compliance_Layer - [NULL FORTRESS v1]
===================================================
MISSION: Provide a strong encryption envelope using AES-256-GCM + PBKDF2-HMAC-SHA256
         (primitives common to SOC 2 / HIPAA / FedRAMP regimes; NOT certified to any).
FEAT:    AES-256-GCM Encryption, HMAC-SHA256 Integrity, Multi-Tenant Isolation.
STATUS:  Public Beta. Unaudited — no formal security audit or compliance certification.
"""

import os
import hashlib
import hmac
import time
import json
import struct
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.backends import default_backend

# =========================================================
# 1. SECURITY CONFIGURATION
# =========================================================

PROTOCOL_SEC = b'NSEC'
VER_SEC = 1

class NULL_Security_Layer:
    def __init__(self, master_secret: str = None):
        """
        master_secret: Central key for deriving tenant-specific keys.
        In production, this should come from a KMS (AWS KMS, HashiCorp Vault).
        """
        if not master_secret:
            # NEVER fall back to a constant key: a publicly-known default secret
            # means anyone can derive the tenant keys and decrypt the data. Refuse
            # to operate without an explicit secret instead of encrypting with a
            # key that provides no confidentiality. Empty strings/byte strings are
            # rejected for the same reason.
            raise ValueError(
                "encryption requires an explicit master secret; "
                "refusing to use a default key"
            )
        self.master_secret = master_secret.encode() if isinstance(master_secret, str) else master_secret

    def _derive_tenant_key(self, tenant_id: str, salt: bytes) -> bytes:
        """KDF to ensure multi-tenant cryptographic isolation."""
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
            backend=default_backend()
        )
        return kdf.derive(self.master_secret + tenant_id.encode())

    def seal(self, data: bytes, tenant_id: str, metadata: dict = None) -> bytes:
        """
        Authenticated seal (primitives common to SOC 2 / FedRAMP regimes; NOT certified):
        1. Multi-tenant key derivation (Isolation)
        2. AES-256-GCM authenticated encryption (Privacy + Integrity)
        3. HMAC-SHA256 signature (Authenticity)
        4. Audit logging hook
        """
        if metadata is None: metadata = {}
        
        # A. Audit Trail
        audit_trail = {
            "t": tenant_id,
            "ts": time.time(),
            "meta": metadata,
            "v": VER_SEC
        }
        audit_json = json.dumps(audit_trail).encode()
        
        # B. Cryptographic Prep
        salt = os.urandom(16)
        nonce = os.urandom(12)
        key = self._derive_tenant_key(tenant_id, salt)
        aesgcm = AESGCM(key)
        
        # C. Encryption (AES-GCM provides integrity for data + audit trail)
        ciphertext = aesgcm.encrypt(nonce, data, audit_json)
        
        # D. Assemble Wrapper
        # [MAGIC][VER][SALT][NONCE][AUDIT_LEN][AUDIT][CIPHERTEXT]
        payload = bytearray(PROTOCOL_SEC)
        payload.append(VER_SEC)
        payload.extend(salt)
        payload.extend(nonce)
        payload.extend(struct.pack(">I", len(audit_json)))
        payload.extend(audit_json)
        payload.extend(ciphertext)
        
        # E. Authenticity (Outer HMAC)
        signature = hmac.new(key, payload, hashlib.sha256).digest()
        
        # FINAL: [SIGNATURE][PAYLOAD]
        return bytes(signature + payload)

    def unseal(self, secure_blob: bytes, tenant_id: str) -> tuple[bytes, dict]:
        """
        Verifies and decrypts a secure blob.
        Returns: (decrypted_data, audit_metadata)
        """
        sig = secure_blob[:32]
        payload = secure_blob[32:]
        
        if not payload.startswith(PROTOCOL_SEC):
            raise ValueError("Security violation: Invalid protocol magic.")
            
        # A. Extract Components
        p = 4
        ver = payload[p]; p += 1
        salt = payload[p:p+16]; p += 16
        nonce = payload[p:p+12]; p += 12
        audit_len = struct.unpack(">I", payload[p:p+4])[0]; p += 4
        audit_json = payload[p:p+audit_len]; p += audit_len
        ciphertext = payload[p:]
        
        # B. Verify Tenant Isolation Key
        key = self._derive_tenant_key(tenant_id, salt)
        
        # C. Verify Authenticity (Constant-time comparison)
        expected_sig = hmac.new(key, payload, hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expected_sig):
            raise PermissionError("CRYPTO_INTEGRITY_FAILURE: Blob tampered or wrong tenant access.")
            
        # D. Decrypt
        aesgcm = AESGCM(key)
        try:
            data = aesgcm.decrypt(nonce, ciphertext, audit_json)
        except Exception:
            raise PermissionError("DECRYPTION_FAILURE: Potential tampering or key mismatch.")
            
        audit_metadata = json.loads(audit_json)
        return data, audit_metadata

# =========================================================
# 2. TAMPER-EVIDENT AUDIT CHAIN
# =========================================================

class AuditChain:
    """Append-only, tamper-evident hash chain (WORM-style audit log).

    Each entry commits to the previous entry's hash, so altering any past entry
    breaks every subsequent link. The head is a single 32-byte digest that can
    be bound into a PCC root, an AES-GCM AAD, or a Solana anchor.

        entry_hash = SHA256( canon({seq, ts, event, meta, prev}) )
    """

    GENESIS = b"\x00" * 32

    def __init__(self):
        self._entries: list = []

    @property
    def head(self) -> bytes:
        return bytes.fromhex(self._entries[-1]["hash"]) if self._entries else self.GENESIS

    @property
    def entries(self) -> list:
        return list(self._entries)

    @staticmethod
    def _body(seq: int, ts: float, event: str, meta: dict, prev_hex: str) -> bytes:
        return json.dumps(
            {"seq": seq, "ts": ts, "event": event, "meta": meta, "prev": prev_hex},
            sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")

    def append(self, event: str, metadata: dict = None) -> str:
        """Append an event; returns the new chain head (hex)."""
        prev_hex = self.head.hex()
        seq = len(self._entries)
        ts = time.time()
        meta = metadata or {}
        h = hashlib.sha256(self._body(seq, ts, event, meta, prev_hex)).hexdigest()
        self._entries.append(
            {"seq": seq, "ts": ts, "event": event, "meta": meta, "prev": prev_hex, "hash": h}
        )
        return h

    def verify(self) -> bool:
        """True iff every link is intact and no entry has been altered."""
        prev_hex = self.GENESIS.hex()
        for rec in self._entries:
            if rec["prev"] != prev_hex:
                return False
            body = self._body(rec["seq"], rec["ts"], rec["event"], rec["meta"], rec["prev"])
            if hashlib.sha256(body).hexdigest() != rec["hash"]:
                return False
            prev_hex = rec["hash"]
        return True


# Process-wide default chain backing secure_audit_log().
_DEFAULT_AUDIT_CHAIN = AuditChain()


def secure_audit_log(event: str, metadata: dict = None) -> str:
    """SOC 2-style central audit hook — now a real tamper-evident hash-chain
    append (was a print-only placeholder). Returns the new chain head (hex)."""
    return _DEFAULT_AUDIT_CHAIN.append(event, metadata or {})


def audit_chain_head() -> bytes:
    """Current head digest of the process default audit chain."""
    return _DEFAULT_AUDIT_CHAIN.head

if __name__ == "__main__":
    # Quick Security Demo
    sec = NULL_Security_Layer(master_secret="KMS_KEY_123")
    
    tenant_a = "acme_corp"
    tenant_b = "globex_corp"
    
    original_data = b"Sensitive HIPAA Patient Record: John Doe, DOB 1980-01-01"
    print(f"Original: {original_data}")
    
    # Seal for Tenant A
    secure_blob = sec.seal(original_data, tenant_a, {"resource": "patient_records", "user": "admin_1"})
    print(f"Secure Blob Size: {len(secure_blob)} bytes")
    
    # Attempt unauthorized access (Tenant B trying to read Tenant A's data)
    try:
        print("\n[Test] Tenant B attempting to read Tenant A data...")
        sec.unseal(secure_blob, tenant_b)
    except PermissionError as e:
        print(f"Refused: {e}")
        
    # Authorized access
    data, meta = sec.unseal(secure_blob, tenant_a)
    print(f"\n[Test] Authorized Access:")
    print(f"Restored Data: {data}")
    print(f"Audit Metadata: {meta}")

