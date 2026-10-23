"""The PC dials out; the relay receives sealed envelopes and routing metadata only."""

from __future__ import annotations

import json
import re
import threading
from urllib.parse import urlsplit

from core import remote_fetch_policy


def validate(endpoint, room):
    import ipaddress

    parsed = urlsplit(endpoint)
    try:
        local = ipaddress.ip_address(parsed.hostname or "").is_loopback
    except ValueError:
        local = False
    if (
        (parsed.scheme != "https" and not (parsed.scheme == "http" and local))
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("Use an HTTPS relay address, without a path or password.")
    if not re.fullmatch(r"[a-f0-9]{48,64}", room):
        raise ValueError("Use the room code from your relay.")
    return endpoint.rstrip("/") + "/rooms/" + room


class OutboundRelay:
    def __init__(self, runtime, endpoint, room, token, *, active, model_name="vool"):
        self.runtime = runtime
        self.base = validate(endpoint, room)
        self.token = token
        from core.secret_redaction import register_exact_secret

        register_exact_secret(token)
        self.active = active
        self.model_name = model_name
        self.stop_event = threading.Event()
        self.thread = None
        self.connected = False

    def _call(self, path, value):
        with remote_fetch_policy.open_remote_url(
            self.base + path,
            data=json.dumps(value).encode(),
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.token},
            method="POST",
            timeout=3,
            redirect_policy="refuse",
            no_proxy=True,
            credential_headers=("Authorization",),
        ) as response:
            if response.status != 200:
                raise ValueError("relay unavailable")
            return json.loads(response.read(6 * 1024 * 1024 + 1))

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self.run, name="phone-relay", daemon=True)
        self.thread.start()

    def run(self):
        from core.effect_gateway import named_background_effect_scope
        from core.mobile_companion.sealing import dispatch

        with named_background_effect_scope("mobile.relay", durable=True):
            while not self.stop_event.is_set() and self.active():
                try:
                    value = self._call("/pc/poll", {})
                    self.connected = True
                    job = value.get("job")
                    if not job:
                        continue
                    if self.stop_event.is_set() or not self.active():
                        break
                    response = dispatch(
                        "/api/mobile/companion",
                        job["body"],
                        {},
                        runtime=self.runtime,
                        model_name=self.model_name,
                        client_host="relay",
                        active=lambda: not self.stop_event.is_set() and self.active(),
                    )
                    if self.stop_event.is_set() or not self.active():
                        break
                    body = json.loads(response.body)
                    # Authentication failures have no private body to relay.
                    if body.get("protocol") != "vool.mobile.sealed.v2":
                        continue
                    self._call("/pc/answer", {"id": job["id"], "status": response.status, "body": body})
                except (OSError, ValueError, KeyError, TypeError, remote_fetch_policy.RemoteFetchRefusedError):
                    self.connected = False
                    self.stop_event.wait(1)
            self.connected = False

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=4)
