from __future__ import annotations

import base64
import hmac
import html
import json
import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from core.vool_wallet import decode_solana_pubkey, verify_wallet_signature

DEFAULT_GATE_URL = "http://127.0.0.1:11435/gate/unlock"
GATE_MODE_SERVER_WHITELIST = "server_whitelist"
# Default lifetime of a server-issued gate nonce. Short enough to close the
# replay window, long enough for a human to approve a wallet signature.
GATE_NONCE_TTL_SECONDS = 120.0
_CONTENT_AAD_PREFIX = b"vool-web0-gated-content:v1:"
_KEY_STORE_AAD = b"vool-web0-gate-key-store:v1"
_GATE_CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Max-Age": "86400",
}


def gate_cors_headers() -> dict[str, str]:
    return dict(_GATE_CORS_HEADERS)


def _normalize_gate_mode(mode: str) -> str:
    normalized = str(mode or "").strip().lower().replace("-", "_")
    if normalized in {"", "whitelist", "server_whitelist"}:
        return GATE_MODE_SERVER_WHITELIST
    if normalized == "kvac":
        raise ValueError("Dark Null KVAC gating is not implemented in this local runtime yet")
    raise ValueError(f"Unsupported Web0 gate mode: {mode!r}")


def _content_aad(block_id: str) -> bytes:
    return _CONTENT_AAD_PREFIX + str(block_id).encode("ascii")


def _split_whitelist(entries: list[str] | tuple[str, ...]) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    wallets: list[str] = []
    pending_names: list[str] = []
    invalid: list[str] = []
    seen_wallets: set[str] = set()
    seen_names: set[str] = set()
    for raw in entries:
        entry = str(raw or "").strip()
        if not entry:
            continue
        if entry.endswith(".null"):
            if entry not in seen_names:
                seen_names.add(entry)
                pending_names.append(entry)
            continue
        try:
            decode_solana_pubkey(entry)
        except Exception:
            invalid.append(entry)
            continue
        if entry not in seen_wallets:
            seen_wallets.add(entry)
            wallets.append(entry)
    return tuple(wallets), tuple(pending_names), tuple(invalid)


@dataclass(frozen=True)
class GatedBlock:
    block_id: str
    mode: str
    gate_url: str
    content_nonce_b64: str
    content_ciphertext_b64: str
    allowed_wallets: tuple[str, ...]
    pending_null_names: tuple[str, ...] = ()
    invalid_whitelist_entries: tuple[str, ...] = ()
    label: str = "Private content"
    plaintext_kind: str = "text"

    def public_payload(self) -> dict[str, Any]:
        return {
            "version": 1,
            "block_id": self.block_id,
            "mode": self.mode,
            "gate_url": self.gate_url,
            "content_nonce_b64": self.content_nonce_b64,
            "content_ciphertext_b64": self.content_ciphertext_b64,
            "label": self.label,
            "plaintext_kind": self.plaintext_kind,
            "pending_null_names": list(self.pending_null_names),
            "invalid_whitelist_entries": list(self.invalid_whitelist_entries),
        }


@dataclass(frozen=True)
class GatedBlockSecret:
    block_id: str
    aes_key: bytes
    allowed_wallets: tuple[str, ...]


@dataclass(frozen=True)
class EncryptedGatedBlock:
    block: GatedBlock
    secret: GatedBlockSecret


def encrypt_content_block(
    content: str,
    whitelist: list[str] | tuple[str, ...],
    *,
    mode: str = "whitelist",
    gate_url: str = DEFAULT_GATE_URL,
    label: str = "Private content",
    block_id: str | None = None,
) -> EncryptedGatedBlock:
    gate_mode = _normalize_gate_mode(mode)
    wallets, pending_names, invalid_entries = _split_whitelist(whitelist)
    if not wallets:
        raise ValueError("At least one valid Solana wallet pubkey is required for server whitelist gating")
    normalized_block_id = str(block_id or f"gate_{secrets.token_hex(8)}").strip()
    if not normalized_block_id.replace("_", "").replace("-", "").isalnum():
        raise ValueError("block_id must contain only letters, numbers, hyphen, or underscore")
    aes_key = os.urandom(32)
    nonce = os.urandom(12)
    ciphertext = AESGCM(aes_key).encrypt(nonce, str(content).encode("utf-8"), _content_aad(normalized_block_id))
    block = GatedBlock(
        block_id=normalized_block_id,
        mode=gate_mode,
        gate_url=str(gate_url or DEFAULT_GATE_URL).strip() or DEFAULT_GATE_URL,
        content_nonce_b64=base64.b64encode(nonce).decode("ascii"),
        content_ciphertext_b64=base64.b64encode(ciphertext).decode("ascii"),
        allowed_wallets=wallets,
        pending_null_names=pending_names,
        invalid_whitelist_entries=invalid_entries,
        label=str(label or "Private content").strip() or "Private content",
    )
    return EncryptedGatedBlock(
        block=block,
        secret=GatedBlockSecret(
            block_id=normalized_block_id,
            aes_key=aes_key,
            allowed_wallets=wallets,
        ),
    )


def decrypt_content_block(block: GatedBlock, aes_key: bytes) -> str:
    nonce = base64.b64decode(block.content_nonce_b64, validate=True)
    ciphertext = base64.b64decode(block.content_ciphertext_b64, validate=True)
    plaintext = AESGCM(bytes(aes_key)).decrypt(nonce, ciphertext, _content_aad(block.block_id))
    return plaintext.decode("utf-8")


def make_gate_challenge(block_id: str, wallet_pubkey: str) -> str:
    return f"vool-gate-v1:{block_id}:{wallet_pubkey}"


def make_gate_challenge_v2(block_id: str, wallet_pubkey: str, server_nonce: str) -> str:
    """Replay-resistant challenge bound to a fresh, server-issued nonce.

    Same block/wallet binding as v1 plus a server nonce that the gate hands out
    per attempt and accepts exactly once, within a short TTL, closing the replay
    window left open by the static v1 challenge.
    """
    return f"vool-gate-v2:{block_id}:{wallet_pubkey}:{server_nonce}"


class GateChallengeStore:
    """Issues fresh, single-use, short-TTL server nonces for gate unlocks.

    A challenge is bound to ``(block_id, wallet_pubkey)`` so a nonce issued for
    one wallet/block cannot be replayed against another. Nonces are consumed on
    first valid use and expire after ``ttl_seconds``. In-process only: a random
    nonce per issue, no external dependency, no on-chain call.
    """

    def __init__(self, *, ttl_seconds: float = GATE_NONCE_TTL_SECONDS) -> None:
        self._ttl = float(ttl_seconds)
        self._lock = RLock()
        # nonce -> (block_id, wallet_pubkey, expires_at)
        self._issued: dict[str, tuple[str, str, float]] = {}

    def _now(self) -> float:
        return time.monotonic()

    def _purge_expired(self, now: float) -> None:
        expired = [nonce for nonce, (_, _, exp) in self._issued.items() if exp <= now]
        for nonce in expired:
            self._issued.pop(nonce, None)

    def issue(self, block_id: str, wallet_pubkey: str) -> str:
        """Mint a fresh server nonce for this block/wallet and return the full challenge."""
        block_id = str(block_id)
        wallet_pubkey = str(wallet_pubkey)
        server_nonce = secrets.token_urlsafe(24)
        now = self._now()
        with self._lock:
            self._purge_expired(now)
            self._issued[server_nonce] = (block_id, wallet_pubkey, now + self._ttl)
        return make_gate_challenge_v2(block_id, wallet_pubkey, server_nonce)

    def consume(self, block_id: str, wallet_pubkey: str, challenge: str) -> bool:
        """Validate and burn a v2 challenge. True only for a fresh, matching, unexpired one."""
        block_id = str(block_id)
        wallet_pubkey = str(wallet_pubkey)
        parts = str(challenge or "").split(":", 3)
        if len(parts) != 4 or parts[0] != "vool-gate-v2":
            return False
        c_block, c_wallet, server_nonce = parts[1], parts[2], parts[3]
        if not hmac.compare_digest(c_block, block_id) or not hmac.compare_digest(c_wallet, wallet_pubkey):
            return False
        now = self._now()
        with self._lock:
            self._purge_expired(now)
            record = self._issued.pop(server_nonce, None)  # single use: pop unconditionally
        if record is None:
            return False
        rec_block, rec_wallet, expires_at = record
        if expires_at <= now:
            return False
        return hmac.compare_digest(rec_block, block_id) and hmac.compare_digest(rec_wallet, wallet_pubkey)


def render_gated_block_html(block: GatedBlock) -> str:
    payload_json = json.dumps(block.public_payload(), separators=(",", ":"), sort_keys=True)
    label = html.escape(block.label, quote=True)
    block_id_attr = html.escape(block.block_id, quote=True)
    payload_id = f"vool-gate-payload-{block.block_id}"
    status_id = f"vool-gate-status-{block.block_id}"
    button_id = f"vool-gate-button-{block.block_id}"
    container_id = f"vool-gate-{block.block_id}"
    js = f"""
(async function () {{
  const payloadEl = document.getElementById("{payload_id}");
  const container = document.getElementById("{container_id}");
  const statusEl = document.getElementById("{status_id}");
  const button = document.getElementById("{button_id}");
  if (!payloadEl || !container || !statusEl || !button) return;
  const payload = JSON.parse(payloadEl.textContent || "{{}}");

  function setStatus(message) {{
    statusEl.textContent = message;
  }}

  function b64ToBytes(value) {{
    const binary = atob(value);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    return bytes;
  }}

  function bytesToB64(bytes) {{
    let binary = "";
    for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
    return btoa(binary);
  }}

  async function decryptContent(aesKeyHex) {{
    const aesBytes = new Uint8Array(aesKeyHex.length / 2);
    for (let i = 0; i < aesKeyHex.length; i += 2) aesBytes[i / 2] = parseInt(aesKeyHex.slice(i, i + 2), 16);
    const key = await crypto.subtle.importKey("raw", aesBytes, {{ name: "AES-GCM" }}, false, ["decrypt"]);
    const plaintext = await crypto.subtle.decrypt(
      {{ name: "AES-GCM", iv: b64ToBytes(payload.content_nonce_b64) }},
      key,
      b64ToBytes(payload.content_ciphertext_b64)
    );
    return new TextDecoder().decode(plaintext);
  }}

  async function unlock() {{
    const provider = window.phantom?.solana || window.solana;
    if (!provider) {{
      setStatus("No Solana wallet found.");
      return;
    }}
    setStatus("Connecting wallet...");
    let walletPubkey;
    try {{
      const connected = await provider.connect();
      walletPubkey = connected.publicKey.toBase58();
    }} catch (error) {{
      setStatus("Wallet connection cancelled.");
      return;
    }}

    const challenge = "vool-gate-v1:" + payload.block_id + ":" + walletPubkey;
    let signature;
    try {{
      const signed = await provider.signMessage(new TextEncoder().encode(challenge), "utf8");
      signature = bytesToB64(signed.signature);
    }} catch (error) {{
      setStatus("Wallet signature rejected.");
      return;
    }}

    setStatus("Verifying access...");
    let response;
    try {{
      response = await fetch(payload.gate_url, {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify({{
          block_id: payload.block_id,
          wallet_pubkey: walletPubkey,
          nonce: challenge,
          signature: signature
        }})
      }});
    }} catch (error) {{
      setStatus("VOOL gate is unreachable.");
      return;
    }}
    const result = await response.json().catch(() => ({{ error: "invalid_gate_response" }}));
    if (!response.ok || !result.aes_key) {{
      setStatus(result.error || "Access denied.");
      return;
    }}
    try {{
      const content = await decryptContent(result.aes_key);
      container.replaceChildren();
      const unlocked = document.createElement("div");
      unlocked.className = "vool-gated-content";
      unlocked.textContent = content;
      container.appendChild(unlocked);
    }} catch (error) {{
      setStatus("Decryption failed.");
    }}
  }}

  button.addEventListener("click", unlock);
}})();
"""
    return f"""<section id="{container_id}" class="vool-gate-block" data-block-id="{block_id_attr}" data-mode="{html.escape(block.mode, quote=True)}">
  <script id="{payload_id}" type="application/json">{payload_json}</script>
  <div class="vool-gate-locked">
    <div class="vool-gate-icon" aria-hidden="true">LOCKED</div>
    <div class="vool-gate-label">{label}</div>
    <div id="{status_id}" class="vool-gate-status">Connect a whitelisted wallet to unlock.</div>
    <button id="{button_id}" class="vool-gate-button" type="button">Connect Wallet</button>
  </div>
</section>
<script>
{js}
</script>"""


def render_gated_block_css() -> str:
    return """
.vool-gate-block { border: 1px solid rgba(0,194,168,0.32); border-radius: 16px; padding: 1.5rem; margin: 1rem 0; background: rgba(2,10,18,0.72); color: #f3fffb; }
.vool-gate-locked { display: grid; gap: 0.65rem; justify-items: start; }
.vool-gate-icon { font-size: 0.72rem; letter-spacing: 0.16em; color: #00c2a8; font-weight: 800; }
.vool-gate-label { font-size: 1.05rem; font-weight: 700; }
.vool-gate-status { font-size: 0.9rem; color: rgba(243,255,251,0.74); }
.vool-gate-button { border: 0; border-radius: 999px; padding: 0.58rem 1rem; background: #00c2a8; color: #021014; font-weight: 800; cursor: pointer; }
.vool-gate-button:hover { filter: brightness(1.08); }
.vool-gated-content { white-space: pre-wrap; line-height: 1.55; }
"""


@dataclass
class WalletKeyStore:
    _entries: dict[str, dict[str, bytes]] = field(default_factory=dict)
    storage_path: Path | None = None
    storage_key: bytes | None = None

    def add(self, block_id: str, wallet_pubkey: str, aes_key: bytes) -> None:
        decode_solana_pubkey(wallet_pubkey)
        key_bytes = bytes(aes_key)
        if len(key_bytes) != 32:
            raise ValueError("AES gate key must be exactly 32 bytes")
        self._entries.setdefault(str(block_id), {})[str(wallet_pubkey)] = key_bytes

    def register_secret(self, secret: GatedBlockSecret) -> None:
        for wallet_pubkey in secret.allowed_wallets:
            self.add(secret.block_id, wallet_pubkey, secret.aes_key)

    def register_encrypted_block(self, encrypted: EncryptedGatedBlock) -> None:
        self.register_secret(encrypted.secret)

    def get_aes_key(self, block_id: str, wallet_pubkey: str) -> bytes | None:
        return self._entries.get(str(block_id), {}).get(str(wallet_pubkey))

    def all_block_ids(self) -> list[str]:
        return sorted(self._entries)

    def public_summary(self) -> dict[str, Any]:
        return {
            "blocks": [
                {"block_id": block_id, "wallet_count": len(wallets)}
                for block_id, wallets in sorted(self._entries.items())
            ]
        }

    def save(self, *, path: str | Path | None = None, storage_key: bytes | None = None) -> Path:
        target = Path(path).expanduser().resolve() if path is not None else self.storage_path
        key = bytes(storage_key) if storage_key is not None else self.storage_key
        if target is None or key is None:
            raise RuntimeError("Encrypted gate key store save requires a path and storage_key")
        if len(key) != 32:
            raise ValueError("Gate key store encryption key must be exactly 32 bytes")
        plaintext = json.dumps(
            {
                "version": 1,
                "entries": {
                    block_id: {wallet: aes_key.hex() for wallet, aes_key in wallets.items()}
                    for block_id, wallets in sorted(self._entries.items())
                },
            },
            sort_keys=True,
        ).encode("utf-8")
        nonce = os.urandom(12)
        ciphertext = AESGCM(key).encrypt(nonce, plaintext, _KEY_STORE_AAD)
        envelope = {
            "version": 1,
            "cipher": "AES-256-GCM",
            "nonce_b64": base64.b64encode(nonce).decode("ascii"),
            "ciphertext_b64": base64.b64encode(ciphertext).decode("ascii"),
        }
        target.parent.mkdir(parents=True, exist_ok=True)
        if os.name == "posix":
            target.parent.chmod(0o700)
        target.write_text(json.dumps(envelope, sort_keys=True) + "\n", encoding="utf-8")
        if os.name == "posix":
            target.chmod(0o600)
        self.storage_path = target
        self.storage_key = key
        return target

    @classmethod
    def load(cls, *, path: str | Path, storage_key: bytes) -> WalletKeyStore:
        key = bytes(storage_key)
        if len(key) != 32:
            raise ValueError("Gate key store encryption key must be exactly 32 bytes")
        source = Path(path).expanduser().resolve()
        envelope = json.loads(source.read_text(encoding="utf-8"))
        nonce = base64.b64decode(str(envelope.get("nonce_b64") or ""), validate=True)
        ciphertext = base64.b64decode(str(envelope.get("ciphertext_b64") or ""), validate=True)
        payload = json.loads(AESGCM(key).decrypt(nonce, ciphertext, _KEY_STORE_AAD).decode("utf-8"))
        store = cls(storage_path=source, storage_key=key)
        for block_id, wallets in dict(payload.get("entries") or {}).items():
            for wallet_pubkey, aes_key_hex in dict(wallets or {}).items():
                store.add(str(block_id), str(wallet_pubkey), bytes.fromhex(str(aes_key_hex)))
        return store


class VoolGateHandler:
    def __init__(
        self,
        wallet_store: WalletKeyStore,
        *,
        challenge_store: GateChallengeStore | None = None,
    ) -> None:
        self._store = wallet_store
        # When a challenge store is supplied the gate requires a fresh,
        # single-use, short-TTL server nonce (v2), closing the replay window of
        # the static v1 challenge. When omitted, the static v1 path is kept for
        # backward compatibility.
        self._challenge_store = challenge_store

    def issue_challenge(self, block_id: str, wallet_pubkey: str) -> str | None:
        """Mint a fresh server-bound challenge, or None if no challenge store is configured."""
        if self._challenge_store is None:
            return None
        return self._challenge_store.issue(block_id, wallet_pubkey)

    def handle(self, body: dict[str, Any]) -> dict[str, Any]:
        block_id = str(body.get("block_id") or "").strip()
        wallet_pubkey = str(body.get("wallet_pubkey") or "").strip()
        signature_b64 = str(body.get("signature") or "").strip()
        nonce = str(body.get("nonce") or "").strip()
        if not block_id or not wallet_pubkey or not signature_b64 or not nonce:
            return {"error": "missing_fields"}
        try:
            decode_solana_pubkey(wallet_pubkey)
        except Exception:
            return {"error": "invalid_wallet_pubkey"}
        if self._challenge_store is not None:
            if not self._challenge_store.consume(block_id, wallet_pubkey, nonce):
                return {"error": "invalid_nonce"}
        elif nonce != make_gate_challenge(block_id, wallet_pubkey):
            return {"error": "invalid_nonce"}
        try:
            signature = base64.b64decode(signature_b64, validate=True)
        except Exception:
            return {"error": "invalid_signature"}
        if not verify_wallet_signature(wallet_pubkey=wallet_pubkey, message=nonce, signature=signature):
            return {"error": "invalid_signature"}
        aes_key = self._store.get_aes_key(block_id, wallet_pubkey)
        if aes_key is None:
            return {"error": "not_whitelisted"}
        return {"aes_key": aes_key.hex()}


__all__ = [
    "DEFAULT_GATE_URL",
    "GATE_NONCE_TTL_SECONDS",
    "EncryptedGatedBlock",
    "GateChallengeStore",
    "GatedBlock",
    "GatedBlockSecret",
    "VoolGateHandler",
    "WalletKeyStore",
    "decrypt_content_block",
    "encrypt_content_block",
    "gate_cors_headers",
    "make_gate_challenge",
    "make_gate_challenge_v2",
    "render_gated_block_css",
    "render_gated_block_html",
]
