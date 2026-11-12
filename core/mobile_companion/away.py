"""Away from home: the phone listener gets a hidden address on the private route (Tor).

Off by default. When the owner switches it on, a second copy of the phone listener is bound to
loopback only and published as a hidden service through the shared Tor runtime. The hidden address
comes with a key kept in the credential store, so it stays the same across restarts and the
phone keeps reaching the computer it paired with. Requests arriving this way are the same sealed
envelopes as on the home network; the hidden address is only a route.
"""

from __future__ import annotations

import json
import re

CREDENTIAL = "mobile.away.onion_key"
HIDDEN_HOST = re.compile(r"[a-z2-7]{56}\.onion")
_KEY = re.compile(r"ED25519-V3:[A-Za-z0-9+/=]{20,200}")


def _load_key() -> tuple[str, str]:
    """(key, address) kept from the first time the route was switched on, or ("", "")."""
    from core.credential_store import get_credential, strict_reads

    with strict_reads():
        raw = get_credential(CREDENTIAL) or ""
    try:
        value = json.loads(raw) if raw else {}
    except ValueError:
        value = {}
    key, address = str(value.get("key") or ""), str(value.get("address") or "")
    if _KEY.fullmatch(key) and HIDDEN_HOST.fullmatch(address):
        return key, address
    return "", ""


def _save_key(key: str, address: str) -> None:
    from core.credential_store import store_credential

    store_credential(CREDENTIAL, json.dumps({"key": key, "address": address}), label="Phone away-from-home address")


def forget_key() -> None:
    """A fresh hidden address next time. Phones paired with the old one must rescan the code."""
    from core.credential_store import delete_credential

    delete_credential(CREDENTIAL)


class AwayLink:
    """Owns the loopback listener copy and its hidden address for one CompanionListener."""

    USER = "phone-away"

    def __init__(self, make_server, *, tor=None, load_key=None, save_key=None):
        self._make_server = make_server
        self._tor = tor
        self._load_key = load_key
        self._save_key = save_key
        self.server = None
        self.thread = None
        self.host = ""

    def _runtime(self):
        if self._tor is None:
            from core import tor_runtime

            self._tor = tor_runtime.shared()
        return self._tor

    def start(self) -> str:
        import threading

        if self.server is not None:
            return self.host
        key, expected = (self._load_key or _load_key)()
        server = self._make_server(("127.0.0.1", 0))
        tor = self._runtime()
        started = False
        published = ""
        try:
            # The address can be published before Tor has finished connecting; no need to wait.
            tor.start(self.USER, wait_for_network=False)
            started = True
            service = tor.add_onion(key, server.server_address[1])
            published = str(service.address or "")
            if not HIDDEN_HOST.fullmatch(published) or (expected and published != expected):
                raise ValueError("The private route gave an unexpected address.")
            if not expected:
                if not _KEY.fullmatch(str(service.private_key or "")):
                    raise ValueError("The private route gave no key to keep.")
                (self._save_key or _save_key)(service.private_key, published)
        except BaseException:
            server.server_close()
            if started:
                with _Quiet():
                    tor.remove_onion(published)
                with _Quiet():
                    tor.release(self.USER)
            raise
        self.server = server
        self.host = published
        self.thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()
        return self.host

    def close(self) -> None:
        server, thread, host = self.server, self.thread, self.host
        self.server = None
        self.thread = None
        self.host = ""
        if server is None:
            return
        tor = self._runtime()
        with _Quiet():
            tor.remove_onion(host)
        with _Quiet():
            tor.release(self.USER)
        server.shutdown()
        server.server_close()
        if thread is not None:
            thread.join(timeout=2)


class _Quiet:
    """Shutting the route down must never keep the listener from closing."""

    def __enter__(self):
        return self

    def __exit__(self, kind, _value, _trace):
        return kind is not None and issubclass(kind, Exception)
