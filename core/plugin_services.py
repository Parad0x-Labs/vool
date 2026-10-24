"""Native service declarations use the same enabled package and own credential slots."""
from __future__ import annotations

import contextlib
import http.client
import json
import socket
import ssl
import threading
import time
from pathlib import Path
from urllib.parse import quote, urlsplit

import requests

from core.plugin_network import NetworkRefused, connect_public, destination
from core.plugin_processes import InvocationStopped, invocation

_MAX_RESPONSE = 4 * 1024 * 1024


class ServiceRefused(RuntimeError):
    pass


def credential_values(plugin_id: str, declaration: dict, names) -> dict[str, str]:
    """Resolve only this invocation's declared environment names; never ambient keys."""
    from core.credential_store import get_credential
    from core.secret_redaction import register_exact_secret

    slots = {spec["env"]: slot for slot, spec in declaration.get("credentials", {}).items()}
    result = {}
    for name in names:
        if name not in slots:
            raise ServiceRefused("The plugin requested a key outside its declared slots.")
        value = get_credential(f"plugin.{plugin_id}.{slots[name]}")
        if not value:
            raise ServiceRefused("Connect your service key in Add-ons before using this plugin.")
        register_exact_secret(value)
        register_exact_secret(quote(value, safe=""))
        register_exact_secret(json.dumps(value)[1:-1])
        result[name] = value
    return result


def safe_data(value):
    from core.secret_redaction import redacted_copy

    if isinstance(value, dict):
        return {redacted_copy(key): safe_data(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [safe_data(item) for item in value]
    return redacted_copy(value)


def declaration(plugin_id: str, kind: str, *, expected_approval: str = ""):
    from core import plugin_lifecycle
    from core.plugin_tools import load_manifest

    record = plugin_lifecycle.record_for(plugin_id)
    if (record is None or not plugin_lifecycle.is_available(plugin_id)
            or (expected_approval and expected_approval != record.approval_digest)):
        raise ServiceRefused("This plugin is disabled, changed, or no longer approved. Review it in Add-ons.")
    plugin = load_manifest(Path(record.root) / ".codex-plugin/plugin.json")
    if kind not in plugin.vool.get("kind", []):
        raise ServiceRefused("This plugin does not provide the requested service.")
    return record, plugin


class _Transfer:
    def __init__(self, plugin_id, current, timeout, cancel_check=None):
        self.scope = invocation(plugin_id, current)
        self.call = self.scope.__enter__()
        self.closed = threading.Event()
        self.expired = False
        self.cancel_check = cancel_check
        self.cancelled = False
        self.socket = None
        self.connection = None
        self.response = None
        self.deadline = time.monotonic() + timeout
        self.lock = threading.RLock()
        self.call.own(self.close)
        self.watchdog = threading.Thread(target=self._watch, args=(timeout,), daemon=True)
        self.watchdog.start()

    def _stopped(self):
        if not self.cancelled and self.cancel_check is not None:
            try:
                self.cancelled = bool(self.cancel_check())
            except Exception:
                self.cancelled = True
        return self.cancelled

    def _raise_if_cancelled(self):
        if self._stopped():
            from core.provider_http import ProviderCallCancelled

            self.close()
            raise ProviderCallCancelled()

    def _watch(self, timeout):
        while not self.closed.wait(min(0.05, max(0.001, self.deadline - time.monotonic()))):
            if self._stopped():
                self.close()
                return
            if time.monotonic() >= self.deadline:
                self.expired = True
                self.close()
                return

    def check(self):
        self._raise_if_cancelled()
        if self.expired:
            raise requests.exceptions.Timeout("The plugin service timed out. No automatic retry was made.")
        if self.closed.is_set():
            raise ServiceRefused("The plugin connection was stopped. Its result was discarded.")
        self.call.check()

    def close(self):
        with self.lock:
            if self.closed.is_set():
                return
            self.closed.set()
            connection = self.socket
        if connection is not None:
            with contextlib.suppress(OSError):
                connection.shutdown(socket.SHUT_RDWR)
            connection.close()
        if self.response:
            self.response.close()
        if self.connection:
            self.connection.close()
        self.call.release(self.close)
        # Exit without checking a deliberately cancelled/closed invocation again.
        with contextlib.suppress(InvocationStopped):
            self.scope.__exit__(None, None, None)


class _Response(requests.Response):
    def __init__(self, transfer, url):
        super().__init__()
        self.owner = transfer
        self.status_code = transfer.response.status
        self.headers.update(safe_data(dict(transfer.response.getheaders())))
        self.url = url  # never the credential-bearing wire path
        self.encoding = "utf-8"

    def iter_content(self, chunk_size=8192, decode_unicode=False):
        total = 0
        try:
            while True:
                self.owner.check()
                chunk = self.owner.response.read1(min(max(1, chunk_size), 65536))
                self.owner.check()
                if not chunk:
                    self._content_consumed = True
                    return
                total += len(chunk)
                if total > _MAX_RESPONSE:
                    raise ServiceRefused("The plugin service response exceeds the size limit.")
                yield chunk.decode("utf-8") if decode_unicode else chunk
        except TimeoutError:
            raise requests.exceptions.Timeout("The plugin service timed out. No automatic retry was made.") from None
        except (OSError, http.client.HTTPException, ValueError, AttributeError):
            self.owner.check()
            raise ServiceRefused("The plugin service connection ended before the response completed.") from None
        finally:
            self.close()

    def iter_lines(self, chunk_size=8192, decode_unicode=False, delimiter=None):
        pending = b""
        for chunk in self.iter_content(chunk_size=chunk_size):
            pending += chunk
            lines = pending.split(delimiter or b"\n")
            pending = lines.pop()
            for line in lines:
                text = line.decode("utf-8")
                # SSE payloads are parsed and redacted as data before reaching the adapter.
                prefix = "data: " if text.startswith("data: ") else ""
                body = text[len(prefix):]
                try:
                    text = prefix + json.dumps(safe_data(json.loads(body)))
                except (ValueError, TypeError):
                    text = str(safe_data(text))
                yield text if decode_unicode else text.encode()
        if pending:
            text = str(safe_data(pending.decode("utf-8")))
            yield text if decode_unicode else text.encode()

    def close(self):
        self.owner.close()


def native_request(plugin_id: str, kind: str, method: str, url: str, *, expected_approval="", timeout=20,
                   payload=None, headers=None, stream=False, _connector=connect_public, _tls_context=None,
                   _public_pricing=False, cancel_check=None):
    """Native HTTPS only: approved hosts, pinned public DNS, no redirects or ambient proxies."""
    from core import plugin_lifecycle

    record, plugin = declaration(plugin_id, kind, expected_approval=expected_approval)
    spec = plugin.vool[kind]
    host, path = destination(url, plugin.vool["network"])
    base = urlsplit(spec["base_url"])
    public_pricing = (_public_pricing and kind == "model_provider" and method == "GET" and url == spec.get("pricing_source"))
    if not public_pricing and (host != base.hostname or not path.startswith((base.path or "").rstrip("/") + "/")):
        raise ServiceRefused("The service call differs from this plugin's declared endpoint.")
    if method not in {"GET", "POST"}:
        raise ServiceRefused("This plugin service operation is not supported.")
    duration = timeout[-1] if isinstance(timeout, (tuple, list)) else timeout
    duration = min(120.0, max(0.1, float(duration)))
    transfer = _Transfer(plugin_id, lambda: bool(plugin_lifecycle.is_available(plugin_id)
                         and plugin_lifecycle.record_for(plugin_id).approval_digest == record.approval_digest), duration, cancel_check)
    try:
        transfer.check()
        slot = spec["credential_slot"]
        key = "" if public_pricing else credential_values(plugin_id, plugin.vool, [plugin.vool["credentials"][slot]["env"]])[plugin.vool["credentials"][slot]["env"]]
        wire_headers = {"Accept": "application/json", "User-Agent": "VOOL", "Content-Type": "application/json"}
        # Adapter-authored harmless headers only; credentials always come from this slot.
        for name, value in (headers or {}).items():
            if name.lower() not in {"authorization", "host", "cookie", "proxy-authorization", "connection"}:
                wire_headers[name] = value
        if key and spec.get("auth_placement", "header") == "path":
            path = spec["auth_path"].replace("{credential}", quote(key, safe="")) + path
        elif key:
            wire_headers["Authorization"] = "Bearer " + key
        body = None if payload is None else json.dumps(payload).encode()
        if body and len(body) > _MAX_RESPONSE:
            raise ServiceRefused("The plugin service request exceeds the size limit.")
        transfer.check()
        raw = _connector(host, timeout=min(duration, 10), call=transfer.call)
        transfer.socket = raw
        context = _tls_context or ssl.create_default_context()
        secure = context.wrap_socket(raw, server_hostname=host, do_handshake_on_connect=False)
        transfer.socket = secure
        transfer.check()
        secure.do_handshake()
        transfer.check()
        connection = http.client.HTTPSConnection(host, timeout=min(duration, 10), context=context)
        transfer.connection = connection
        connection.sock = secure
        connection.request(method, path, body=body, headers=wire_headers)
        transfer.response = connection.getresponse()
        transfer.check()
        response = _Response(transfer, url)
        if 300 <= response.status_code < 400:
            raise ServiceRefused("The plugin service tried to redirect the request. The connection was refused.")
        if response.status_code == 402:
            raise ServiceRefused("The service requested payment. A plugin cannot authorize it; use VOOL's wallet review.")
        if response.status_code in {401, 403}:
            raise ServiceRefused("The service rejected this key or access. Check your key and account in Add-ons.")
        if response.status_code >= 500:
            raise ServiceRefused("The plugin service could not complete the request. No automatic retry was made.")
        if response.status_code >= 400:
            raise ServiceRefused("The plugin service refused this request. Nothing was installed or enabled.")
        if not stream:
            content = b"".join(response.iter_content())
            try:
                content = json.dumps(safe_data(json.loads(content))).encode()
            except (ValueError, UnicodeError):
                content = str(safe_data(content.decode("utf-8", errors="replace"))).encode()
            response._content = content
            response._content_consumed = True
        return response
    except NetworkRefused as exc:
        transfer.close()
        raise ServiceRefused(str(exc)) from None
    except InvocationStopped:
        transfer.close()
        raise ServiceRefused("The plugin was disabled or changed. Its service result was discarded.") from None
    except TimeoutError:
        transfer.close()
        transfer._raise_if_cancelled()
        raise requests.exceptions.Timeout("The plugin service timed out. No automatic retry was made.") from None
    except (OSError, http.client.HTTPException):
        transfer.close()
        transfer._raise_if_cancelled()
        if transfer.expired:
            raise requests.exceptions.Timeout("The plugin service timed out. No automatic retry was made.") from None
        raise ServiceRefused("The plugin service could not be reached securely. No automatic retry was made.") from None
    except Exception:
        transfer.close()
        raise


class ProviderTransport:
    def __init__(self, plugin_id: str, approval: str):
        self.plugin_id, self.approval = plugin_id, approval

    def get(self, url, **options):
        return self.request("GET", url, **options)

    def post(self, url, **options):
        return self.request("POST", url, **options)

    def request(self, method, url, **options):
        return native_request(self.plugin_id, "model_provider", method, url, expected_approval=self.approval,
                              timeout=options.get("timeout", 20), payload=options.get("json"),
                              headers=options.get("headers"), stream=options.get("stream", False),
                              cancel_check=options.get("cancel_check"))


def provider_binding(manifest) -> tuple[str, str]:
    config = manifest.runtime_config
    _record, plugin = declaration(config["plugin_id"], "model_provider", expected_approval=config.get("plugin_approval", ""))
    if config.get("base_url") != plugin.vool["model_provider"]["base_url"]:
        raise ServiceRefused("This model belongs to an older plugin endpoint. Refresh its model list in Add-ons.")
    return plugin.vool["model_provider"]["base_url"].rstrip("/"), ""


def enabled(kind: str) -> list:
    from core import plugin_lifecycle
    from core.plugin_tools import load_manifest

    result = []
    for record in plugin_lifecycle.records():
        if plugin_lifecycle.is_available(record.plugin_id):
            plugin = load_manifest(Path(record.root) / ".codex-plugin/plugin.json")
            if kind in plugin.vool.get("kind", []):
                result.append(plugin)
    return result
