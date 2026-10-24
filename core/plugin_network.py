"""Declared HTTPS destinations, DNS pinning and an invocation-owned CONNECT proxy."""
from __future__ import annotations

import base64
import contextlib
import hmac
import ipaddress
import queue
import secrets
import select
import socket
import socketserver
import sys
import threading

from core.plugin_manifest import host_name, https_url


class NetworkRefused(RuntimeError):
    pass


def destination(url: str, hosts: list[str] | tuple[str, ...]) -> tuple[str, str]:
    try:
        parsed = https_url(url, hosts)
    except (ValueError, TypeError):
        raise NetworkRefused("The plugin tried to reach a host outside its declared HTTPS access.") from None
    return parsed.hostname, (parsed.path or "/") + (("?" + parsed.query) if parsed.query else "")


def public_addresses(host: str, *, timeout: float = 5) -> tuple[tuple, ...]:
    try:
        host_name(host)
    except (ValueError, TypeError):
        raise NetworkRefused("The plugin destination is not a public HTTPS host.") from None
    answer = queue.Queue(maxsize=1)

    def resolve():
        try:
            answer.put(socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM))
        except OSError:
            answer.put(None)

    threading.Thread(target=resolve, daemon=True).start()
    try:
        addresses = answer.get(timeout=min(10, max(0.1, timeout)))
    except queue.Empty:
        raise NetworkRefused("The plugin service address took too long to resolve. No connection was made.") from None
    if addresses is None:
        raise NetworkRefused("The plugin service address could not be resolved.") from None

    def public(row):
        address = ipaddress.ip_address(row[4][0])
        return address.is_global and not (address.is_multicast or address.is_reserved or address.is_unspecified)

    if not addresses or any(not public(row) for row in addresses):
        raise NetworkRefused("The plugin service resolved to a private or local address. The connection was refused.")
    return tuple(addresses)


def connect_public(host: str, *, timeout: float = 10, call=None) -> socket.socket:
    if call:
        call.check()
    addresses = public_addresses(host, timeout=timeout)
    for family, socktype, proto, _name, address in addresses:
        if call:
            call.check()
        connection = socket.socket(family, socktype, proto)
        connection.settimeout(timeout)

        def close(owned=connection):
            with contextlib.suppress(OSError):
                owned.shutdown(socket.SHUT_RDWR)
            owned.close()

        if call:
            call.own(close)
        try:
            connection.connect(address)  # the approved numeric address; no second DNS lookup
            if call:
                call.check()
            return connection
        except OSError:
            connection.close()
            if call:
                call.release(close)
    raise NetworkRefused("The plugin service could not be reached.")


def network_available() -> bool:
    # Linux's current network namespace cannot reach a host-side proxy. Keep it off.
    return sys.platform == "darwin"


class EgressProxy:
    def __init__(self, hosts, call):
        if not network_available():
            raise NetworkRefused("Plugin process network access is unavailable on this device. It remains off.")
        self.hosts = tuple(hosts)
        self.call = call
        self.token = secrets.token_urlsafe(32)
        from core.secret_redaction import register_exact_secret

        register_exact_secret(self.token)
        self.authorization = "Basic " + base64.b64encode((self.token + ":").encode()).decode()
        self.sockets: set[socket.socket] = set()
        self.connections = 0
        self.lock = threading.Lock()
        owner = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                client = self.request
                client.settimeout(5)
                with owner.lock:
                    owner.connections += 1
                    if owner.connections > 64:
                        client.close()
                        return
                    owner.sockets.add(client)
                upstream = None
                try:
                    owner.call.check()
                    data = bytearray()
                    while b"\r\n\r\n" not in data and len(data) <= 8192:
                        block = client.recv(1)
                        if not block:
                            return
                        data.extend(block)
                    lines = data.decode("ascii").split("\r\n")
                    headers = dict(line.split(":", 1) for line in lines[1:] if ":" in line)
                    authorization = next((v.strip() for k, v in headers.items() if k.lower() == "proxy-authorization"), "")
                    method, target, version = lines[0].split(" ")
                    host = target.removesuffix(":443")
                    if (len(data) > 8192 or method != "CONNECT" or version != "HTTP/1.1"
                            or target != host + ":443" or host not in owner.hosts or not host_name(host)
                            or not hmac.compare_digest(authorization, owner.authorization)):
                        raise NetworkRefused("The plugin connection is outside its declared access.")
                    upstream = connect_public(host, call=owner.call)
                    owner.call.check()
                    with owner.lock:
                        owner.sockets.add(upstream)
                    client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                    while True:
                        owner.call.check()
                        ready, _, _ = select.select((client, upstream), (), (), 0.1)
                        for source in ready:
                            block = source.recv(65536)
                            if not block:
                                return
                            (upstream if source is client else client).sendall(block)
                except Exception:
                    with contextlib.suppress(OSError):
                        client.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                finally:
                    for connection in (client, upstream):
                        if connection is not None:
                            with owner.lock:
                                owner.sockets.discard(connection)
                            connection.close()

        class Server(socketserver.ThreadingTCPServer):
            daemon_threads = True
            block_on_close = False
            allow_reuse_address = False

        class Server6(Server):
            address_family = socket.AF_INET6

            def server_bind(self):
                self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                super().server_bind()

        try:
            self.server = Server(("127.0.0.1", 0), Handler)
            self.server6 = Server6(("::1", self.server.server_address[1]), Handler)
        except OSError:
            if hasattr(self, "server"):
                self.server.server_close()
            raise NetworkRefused("The plugin's private network connection could not start. Network access remains off.") from None
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
        self.thread.start()
        self.thread6 = threading.Thread(target=self.server6.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
        self.thread6.start()
        call.own(self.close)

    @property
    def environment(self) -> dict[str, str]:
        address = f"http://{self.token}:@127.0.0.1:{self.port}"
        return {"HTTPS_PROXY": address, "https_proxy": address, "ALL_PROXY": address,
                "all_proxy": address, "NO_PROXY": "", "no_proxy": ""}

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.server6.shutdown()
        self.server6.server_close()
        with self.lock:
            connections, self.sockets = list(self.sockets), set()
        for connection in connections:
            with contextlib.suppress(OSError):
                connection.shutdown(socket.SHUT_RDWR)
            connection.close()
        if threading.current_thread() is not self.thread:
            self.thread.join(2)
        if threading.current_thread() is not self.thread6:
            self.thread6.join(2)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()
