"""Rig for person-path phone-turn tests: a served daemon whose SEALED phone door a phone turn enters.

A real phone is a peer on the LAN, never loopback, and it never posts to /api/chat itself: it pairs
through the desktop QR (core/web/api/mobile_companion_api.py start_pairing/claim_pairing) and then
speaks the sealed transport (core/mobile_companion/sealing.py) to the phone listener
(core/mobile_companion/listener.py), which verifies the Ed25519 device proof, the grant HMAC and the
per-request device signature before it dispatches ``chats.send`` into the real /api/chat pipeline
with the phone's TCP peer stamped remote (sealing.py: "mobile:<device_id>" — deny-only, never
owner-local). This rig does exactly that: the daemon boots with phone access enabled on its LAN
address (a pre-written listener.json the server restores at boot, apps/vool_api_server.py), the rig
starts a pairing from loopback, claims it with a generated phone key as the app does, and every
``phone_turn`` is a sealed, signed ``chats.send`` — so what the phone person reads back was produced
by the real production path, pairing seals included.

The scripted model is the reader rig's `CapturingProvider` (answers the certification probe itself,
then serves canned prose); the daemon runs the full production agent runtime.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tests._reader_served_rig import REPO_ROOT, ServedDaemon, urlopen

#: The sealed transport version the phone listener requires (core/mobile_companion/sealing.py).
SEALED_VERSION = "vool.mobile.sealed.v2"


def _canonical_bytes(obj: Any) -> bytes:
    """The canonical JSON every signature in the companion protocol signs over."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def lan_ip() -> str:
    """This machine's LAN address, without psutil: pick a route, read the socket name."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 1))  # no packet is sent; the kernel just picks a route
        return str(probe.getsockname()[0])
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


class PhoneDaemon(ServedDaemon):
    """`ServedDaemon` with the phone listener up, so a paired phone can speak the sealed door."""

    def __init__(self, home: Path, **kwargs: Any) -> None:
        super().__init__(home, **kwargs)
        self.lan = lan_ip()
        self.listener_port = 0
        # The phone side: one Ed25519 identity whose possession every sealed turn proves.
        from nacl.public import Box, PublicKey
        from nacl.signing import SigningKey

        self._signing_key = SigningKey.generate()
        self._phone_public_key = self._signing_key.verify_key.encode().hex()
        self._phone_encryption_public_key = (
            self._signing_key.verify_key.to_curve25519_public_key().encode().hex()
        )
        self._shared_key: bytes | None = None
        self.device_id = ""
        self.grant_id = ""
        self._grant_secret = ""

    # -- daemon boot ---------------------------------------------------------

    def start(self, *, timeout: float = 240.0) -> PhoneDaemon:
        # Same boot as ServedDaemon.start() with two differences: the API server binds 0.0.0.0 so
        # the test client can connect as a LAN peer, and phone access is pre-enabled so the boot's
        # listener restore() (apps/vool_api_server.py) brings the sealed phone listener up on the
        # LAN address. Copied rather than parametrised upstream because the rig is shared by drives
        # that must stay loopback-only.
        self.home.mkdir(parents=True, exist_ok=True)
        listener_settings = self.home / "data" / "mobile_companion"
        listener_settings.mkdir(parents=True, exist_ok=True)
        (listener_settings / "listener.json").write_text(json.dumps({
            "version": 1,
            "enabled": True,
            "address": self.lan,
            "port": 0,
            "last_port": 0,
        }))
        self.register_provider()
        handle = self.log_path.open("wb")
        self.process = subprocess.Popen(
            [sys.executable, "-m", "apps.vool_api_server", "--port", str(self.port), "--bind", "0.0.0.0"],
            cwd=str(REPO_ROOT),
            env=self.env(),
            stdout=handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"daemon exited {self.process.returncode}\n{self.log_tail()}")
            try:
                with urlopen(f"{self.base_url}/healthz", timeout=3) as response:
                    if response.status == 200:
                        break
            except Exception:
                time.sleep(1.0)
        else:
            raise TimeoutError(f"daemon never became healthy\n{self.log_tail()}")
        self._discover_listener()
        return self

    def _post_api(self, path: str, body: dict[str, Any], *, remote: bool, timeout: float = 60.0) -> dict[str, Any]:
        host = self.lan if remote else "127.0.0.1"
        request = urllib.request.Request(
            f"http://{host}:{self.port}{path}",
            data=json.dumps(body).encode("utf-8"),
            headers={"Host": f"127.0.0.1:{self.port}", "Content-Type": "application/json"},
            method="POST",
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def _listener_port_from_settings(self) -> int:
        request = urllib.request.Request(f"{self.base_url}/api/mobile/settings")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not payload.get("running"):
            return 0
        return int(payload.get("port") or 0)

    def _discover_listener(self) -> None:
        deadline = time.monotonic() + 60.0
        while time.monotonic() < deadline:
            try:
                port = self._listener_port_from_settings()
            except Exception:
                port = 0
            if port:
                self.listener_port = port
                return
            time.sleep(1.0)
        raise RuntimeError(f"phone listener never came up on {self.lan}\n{self.log_tail()}")

    # -- sealed transport ----------------------------------------------------

    def _directional_key(self, direction: str) -> bytes:
        return hashlib.sha256(
            (self._shared_key or b"") + (SEALED_VERSION + ":" + direction).encode()
        ).digest()

    def _seal(self, value: dict[str, Any], direction: str) -> dict[str, Any]:
        import base64

        from nacl.secret import SecretBox

        nonce = secrets.token_bytes(24)
        encrypted = SecretBox(self._directional_key(direction)).encrypt(_canonical_bytes(value), nonce)
        return {"protocol": SEALED_VERSION, "nonce": nonce.hex(),
                "ciphertext": base64.b64encode(encrypted.ciphertext).decode()}

    def _open(self, envelope: dict[str, Any], direction: str) -> dict[str, Any]:
        import base64

        from nacl.secret import SecretBox

        raw = SecretBox(self._directional_key(direction)).decrypt(
            base64.b64decode(envelope["ciphertext"], validate=True), bytes.fromhex(envelope["nonce"]))
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("invalid envelope body")
        return value

    def _post_listener(self, path: str, envelope: dict[str, Any], *, timeout: float = 300.0) -> dict[str, Any]:
        """One sealed request to the phone listener, dialed from the LAN the way the phone does."""
        request = urllib.request.Request(
            f"http://{self.lan}:{self.listener_port}{path}",
            data=json.dumps(envelope).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:  # refusals arrive as plain JSON errors
            return {"error": f"http_{error.code}", "detail": error.read().decode("utf-8", "replace")}
        if not isinstance(payload, dict) or "ciphertext" not in payload:
            return payload  # an unsealed typed refusal (never the owner's data)
        opened = self._open(payload, "response")
        return opened.get("body") if isinstance(opened.get("body"), dict) else opened

    def pair(self) -> None:
        """Pair the way the app does: desktop starts the QR pairing, the phone claims it sealed."""
        from nacl.public import Box, PublicKey

        started = self._post_api("/api/mobile/pairing/start", {}, remote=False)
        pairing_id = str(started.get("pairing_id") or "")
        code = str(started.get("code") or "")
        challenge = str(started.get("challenge") or "")
        expires_at_epoch = int(started.get("expires_at_epoch") or 0)
        if not pairing_id or not code or not challenge:
            raise RuntimeError(f"pairing start refused\n{started}\n{self.log_tail()}")

        # The phone reads the desktop's sealing binding (its Curve25519 public key) from the
        # listener's challenge endpoint, exactly as the app does before it seals anything.
        request = urllib.request.Request(
            f"http://{self.lan}:{self.listener_port}/api/mobile/pairing/challenge?pairing_id={pairing_id}")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=30) as response:
            challenge_payload = json.loads(response.read().decode("utf-8"))
        binding = (challenge_payload.get("sealing") or {}).get("binding") or {}
        desktop_public_key = str(binding.get("public_key") or "")
        if not desktop_public_key:
            raise RuntimeError(f"no sealing binding on the challenge\n{challenge_payload}\n{self.log_tail()}")

        self._shared_key = Box(
            self._signing_key.to_curve25519_private_key(), PublicKey(bytes.fromhex(desktop_public_key))
        ).shared_key()

        # Proof of possession: the phone's Ed25519 signature over server-held pairing values.
        proof_payload = {
            "kind": "vool.mobile.pairing.proof.v1",
            "pairing_id": pairing_id,
            "code": code,
            "challenge": challenge,
            "device_public_key": self._phone_public_key,
            "expires_at": expires_at_epoch,
        }
        claim_body = {
            "pairing_id": pairing_id,
            "code": code,
            "device_name": "person-path test phone",
            "platform": "ios",
            "device_public_key": self._phone_public_key,
            "device_proof": {
                "signature": self._signing_key.sign(_canonical_bytes(proof_payload)).signature.hex()
            },
        }
        envelope = {
            "device_id": "",
            "public_key": self._phone_encryption_public_key,
            **self._seal({"kind": SEALED_VERSION + ":claim", "body": claim_body}, "request"),
        }
        claimed = self._post_listener("/api/mobile/pairing/claim", envelope)
        self.device_id = str(claimed.get("device_id") or "")
        self.grant_id = str(claimed.get("grant_id") or "")
        self._grant_secret = str(claimed.get("grant_secret") or "")
        if not self.device_id or not self.grant_id or not self._grant_secret:
            raise RuntimeError(f"sealed pairing claim refused\n{claimed}\n{self.log_tail()}")

        # The owner's step after pairing: let this phone chat (Settings, device rules). A fresh
        # pairing carries every right off (core/mobile_companion/policy.py defaults), so without
        # this the first chats.send is refused with phone_right_off_or_expired.
        rules = self._post_api("/api/mobile/settings", {
            "operation": "device_rules",
            "device_id": self.device_id,
            "rules": {"chat": True},
        }, remote=False)
        if not rules.get("ok"):
            raise RuntimeError(f"owner could not enable the phone's chat right\n{rules}\n{self.log_tail()}")

    def phone_turn(self, text: str, *, session_id: str = "", timeout: float = 300.0) -> dict[str, Any]:
        """One sealed, signed chats.send — the transport every real phone turn uses."""
        if not self.device_id:
            self.pair()
        params: dict[str, Any] = {"message": text}
        if session_id:
            params["chat_id"] = session_id
        nonce = secrets.token_hex(16)
        timestamp = datetime.now(timezone.utc).isoformat()
        request_signature_payload = {
            "grant_id": self.grant_id,
            "verb": "vool.action",
            "params": {"action": "chats.send", **params},
            "nonce": nonce,
            "timestamp": timestamp,
        }
        signature = hmac.new(
            self._grant_secret.encode("utf-8"),
            _canonical_bytes(request_signature_payload),
            hashlib.sha256,
        ).hexdigest()
        request_proof = {
            "kind": "vool.mobile.request.v1",
            "device_id": self.device_id,
            "action": "chats.send",
            "params_sha256": hashlib.sha256(_canonical_bytes(params)).hexdigest(),
            "nonce": nonce,
            "timestamp": timestamp,
            "grant_id": self.grant_id,
        }
        body = {
            "device_id": self.device_id,
            "grant_id": self.grant_id,
            "action": "chats.send",
            "params": params,
            "nonce": nonce,
            "timestamp": timestamp,
            "signature": signature,
            "device_signature": self._signing_key.sign(_canonical_bytes(request_proof)).signature.hex(),
        }
        envelope = {
            "device_id": self.device_id,
            **self._seal({"kind": SEALED_VERSION + ":request", "body": body}, "request"),
        }
        result = self._post_listener("/api/mobile/companion", envelope, timeout=timeout)
        turn = result.get("result") if isinstance(result.get("result"), dict) else result
        # The rig's wire view mirrors the app's: the phone keeps ITS chat handle, so the next
        # turn resumes by it; the canonical desktop id rides alongside as reporting only.
        chat_handle = str(turn.get("chat_id") or "")
        if chat_handle and "vool_session_id" not in turn:
            turn["vool_session_id"] = chat_handle
        return turn

    # -- the PC's own page ---------------------------------------------------

    def post_chat(self, body: dict[str, Any], *, remote: bool, timeout: float = 300.0) -> dict[str, Any]:
        """POST /api/chat from the LAN peer (remote=True) or from loopback (the PC's own page)."""
        host = self.lan if remote else "127.0.0.1"
        headers = {"Host": f"127.0.0.1:{self.port}", "Content-Type": "application/json"}
        request = urllib.request.Request(
            f"http://{host}:{self.port}/api/chat",
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def pc_turn(self, text: str, *, session_id: str = "", timeout: float = 300.0) -> dict[str, Any]:
        """The same ask the served chat page makes: no surface keys, owner-local loopback peer."""
        body: dict[str, Any] = {
            "messages": [{"role": "user", "content": text}],
            "stream": False,
            "model": self.model,
            "mode": "auto",
        }
        if session_id:
            body["session_id"] = session_id
        return self.post_chat(body, remote=False, timeout=timeout)


def reply_text(payload: dict[str, Any]) -> str:
    """The text the person reads: the sealed chats.send reply (fallback: /api/chat message)."""
    if isinstance(payload.get("reply"), str):
        return payload["reply"]
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    return str(message.get("content") or payload.get("response") or "")
