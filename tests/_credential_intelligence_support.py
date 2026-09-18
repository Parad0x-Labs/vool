"""Shared fakes for the credential-intelligence tests (P0, 2026-09-02).

Three things every test here needs, kept out of the test files so the LAW stays readable:

* **FakeProviderServer** — a real local HTTP server on 127.0.0.1:<ephemeral>. The tests drive the
  ACTUAL credential intake boundary: real sockets, real HTTP, the real effect door — never a
  monkeypatched transport. It scripts one response per request, counts requests per path, and
  records only a DIGEST of any Authorization header so the assertion "the key reached this
  provider" never requires holding a second copy of the secret.
* **HangingKeyring** — a keyring stand-in whose ``set_password`` blocks until released, to
  reproduce a pending macOS Keychain authorization dialog (the bounded-call timeout path).
* **IsolatedCredentialHome** — VOOL_HOME + runtime home + grant/store-mode pinning, so no test
  can touch the developer's real Keychain, vault, or escalation policy file.
"""
from __future__ import annotations

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import core.runtime_paths as runtime_paths

#: A key long enough to clear the completeness gate, with a shape no vendor claims (so
#: classification can never mistake it for a real provider's format by accident).
ODD_KEY = "zk9-test-intelligence-key-0123456789abcdef"


#: Headers that can carry a credential; the fake records a DIGEST of each one that arrives.
_CREDENTIAL_HEADERS = ("authorization", "x-subscription-token", "x-api-key")


class FakeProviderServer:
    """One scripted provider endpoint on loopback. Start it, point a descriptor at ``url``,
    and it will answer ``responses`` in order: each item is ``(status, body)``,
    ``(status, body, headers)`` or ``(status, body, headers, delay_s)``. A dict/list body is sent
    as JSON; ``bytes``/``str`` is sent raw (an HTML page, an empty redirect). ``responses`` may also
    be a callable ``(request_record) -> item`` so one fake can answer by path or by credential."""

    def __init__(self, responses=None):
        self.responses = responses if callable(responses) else list(responses or [(200, {"data": []})])
        self.cursor = 0
        self.requests: list[dict[str, object]] = []
        self._lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _serve(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                raw_request = self.rfile.read(length) if length else b""
                try:
                    request_json = json.loads(raw_request) if raw_request else None
                except ValueError:
                    request_json = None
                with outer._lock:
                    auth = str(self.headers.get("Authorization") or "")
                    record = {
                        "method": self.command,
                        "path": self.path.split("?", 1)[0],
                        "query": self.path.split("?", 1)[1] if "?" in self.path else "",
                        # digest, never the value: the fake must not become a secret copy
                        "auth_sha256": hashlib.sha256(auth.encode()).hexdigest(),
                        "auth_present": bool(auth),
                        "header_names": sorted({k.lower() for k in self.headers}),
                        "header_sha256": {
                            name: hashlib.sha256(str(self.headers.get(name)).encode()).hexdigest()
                            for name in _CREDENTIAL_HEADERS
                            if self.headers.get(name) is not None
                        },
                        "json_keys": sorted(request_json) if isinstance(request_json, dict) else [],
                    }
                    outer.requests.append(record)
                    if callable(outer.responses):
                        item = outer.responses(record)
                    else:
                        outer.cursor = min(outer.cursor, len(outer.responses) - 1)
                        item = outer.responses[outer.cursor]
                        outer.cursor += 1
                status, body = item[0], item[1]
                headers = dict(item[2]) if len(item) > 2 else {}
                delay = float(item[3]) if len(item) > 3 else 0.0
                if isinstance(body, (bytes, str)):
                    data = body.encode("utf-8") if isinstance(body, str) else body
                    headers.setdefault("Content-Type", "text/plain")
                else:
                    data = json.dumps(body).encode("utf-8")
                    headers.setdefault("Content-Type", "application/json")
                if delay:
                    import time

                    time.sleep(delay)
                try:
                    self.send_response(status)
                    for name, value in headers.items():
                        self.send_header(name, value)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            do_GET = _serve  # noqa: N815 (BaseHTTPRequestHandler API)
            do_POST = _serve  # noqa: N815 (BaseHTTPRequestHandler API)

            def log_message(self, *_args) -> None:  # keep pytest output clean
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    @property
    def host(self) -> str:
        return str(self._server.server_address[0])

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    @property
    def request_count(self) -> int:
        with self._lock:
            return len(self.requests)

    def saw_bearer(self, secret: str) -> bool:
        """True if some request carried exactly ``Bearer <secret>`` (compared by digest)."""
        want = hashlib.sha256(f"Bearer {secret}".encode()).hexdigest()
        with self._lock:
            return any(r["auth_sha256"] == want for r in self.requests)

    def __enter__(self) -> FakeProviderServer:
        self._thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        self._server.shutdown()
        self._server.server_close()


class HangingKeyring:
    """A keyring whose ``set_password`` blocks on an event — a pending Keychain dialog.

    The LATE write lands only after the test releases the event, which is exactly the
    bounded-call reality: the daemon thread outlives the caller's timeout. ``get_password``
    works normally (the prompt was answered by then)."""

    class errors:  # noqa: N801 (mirrors keyring.errors)
        class PasswordDeleteError(Exception):
            pass

    def __init__(self):
        self._store: dict[tuple[str, str], str] = {}
        self.release = threading.Event()
        self.blocked = threading.Event()
        self.get_calls = 0

    def set_password(self, service, account, password):
        self.blocked.set()
        self.release.wait(30.0)  # bounded so a broken test cannot hang the worker forever
        self._store[(service, account)] = password

    def get_password(self, service, account):
        self.get_calls += 1
        return self._store.get((service, account))

    def delete_password(self, service, account):
        if (service, account) not in self._store:
            raise self.errors.PasswordDeleteError("not found")
        del self._store[(service, account)]

    def unblock_late_write(self) -> None:
        self.release.set()


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """VOOL_HOME isolation for the credential boundary: no real vault, index, journal, or
    escalation-policy file is ever touched. Yields the home path."""
    home = tmp_path / "vool-home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("VOOL_HOME", str(home))
    runtime_paths.configure_runtime_home(home)
    yield home
    runtime_paths.configure_runtime_home(None)


@pytest.fixture
def vault_home(isolated_home, monkeypatch):
    """Isolated home + the conftest's default vault backend (no Keychain interaction at all)."""
    monkeypatch.setenv("VOOL_CREDENTIAL_STORE", "vault")
    monkeypatch.delenv("VOOL_KEYCHAIN_ALLOWED", raising=False)
    return isolated_home


@pytest.fixture
def keychain_home(isolated_home, monkeypatch):
    """Isolated home + an in-memory fake Keychain + the explicit operator write grant.

    Undoes the root conftest's VOOL_CREDENTIAL_STORE=vault pin for tests that exercise the
    Keychain path; the fake keyring means no test can reach the developer's login Keychain."""
    import core.credential_store as cs

    fake = HangingKeyring()
    monkeypatch.delenv("VOOL_CREDENTIAL_STORE", raising=False)
    monkeypatch.setenv("VOOL_KEYCHAIN_ALLOWED", "1")
    monkeypatch.setattr(cs, "_load_keyring", lambda: fake)
    monkeypatch.setattr(cs, "_active_keyring", lambda *args, **kwargs: fake)
    yield fake
    # A timeout armed inside one test must not keep the breaker tripped for the rest of the
    # session. Plain assignment on purpose: monkeypatch would "restore" the armed value.
    import core.bounded_keyring as bk

    bk._KEYCHAIN_BLOCKED = False


def descriptor_for(server: FakeProviderServer | None, provider_id: str = "provtest", **overrides):
    """A provider descriptor; pointing at a live fake server when one is given, else at a
    pinned example host (verification is never called in those tests)."""
    from core.credential_intelligence.provider_registry import ProviderDescriptor

    endpoint = f"{server.url}/v1/models" if server is not None else "https://prov.example/v1/models"
    fields = {
        "provider_id": provider_id,
        "label": provider_id.title(),
        "kind": "llm_cloud",
        "credential_slot": f"llm.cloud.{provider_id}",
        "verify_endpoint": endpoint,
        "verify_method": "GET",
        "auth_style": "bearer",
        "key_prefixes": (f"{provider_id}-",),
        "capability_family": "cloud_chat",
        "paid": True,
    }
    fields.update(overrides)
    return ProviderDescriptor(**fields)


def registry_of(*descriptors):
    from core.credential_intelligence.provider_registry import ProviderRegistry

    return ProviderRegistry({d.provider_id: d for d in descriptors})


def sweep_home_for_secret(home, secret: str) -> list[str]:
    """Every file under the isolated home whose bytes contain the raw secret. The honest
    end-of-flow proof: an encrypted vault entry will not match; any log, index, journal,
    receipt, or diagnostics file that leaked plaintext will."""
    hits: list[str] = []
    for path in sorted(home.rglob("*")):
        if not path.is_file():
            continue
        try:
            if secret.encode("utf-8") in path.read_bytes():
                hits.append(str(path.relative_to(home)))
        except OSError:
            continue
    return hits


__all__ = [
    "ODD_KEY",
    "FakeProviderServer",
    "HangingKeyring",
    "descriptor_for",
    "isolated_home",
    "keychain_home",
    "registry_of",
    "sweep_home_for_secret",
    "vault_home",
]
