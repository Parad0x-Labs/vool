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
import threading

from core.plugin_manifest import host_name, https_url


class NetworkRefused(RuntimeError):
    pass


# One proxied connection may carry at most this many bytes in both directions together.
CONNECTION_BYTE_CAP = 64 * 1024 * 1024
_HELLO_LIMIT = 64 * 1024


def _read_exact(sock, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        block = sock.recv(size - len(data))
        if not block:
            raise NetworkRefused("The plugin connection ended before its TLS greeting.")
        data.extend(block)
    return bytes(data)


def read_client_hello(sock) -> tuple[bytes, str]:
    """Read a complete TLS ClientHello and return its raw records and the requested server name.

    The name is the one the plugin asks the far side for. Checking it against the declared host
    stops a plugin from reaching another site that shares the declared host's address.
    """
    raw, handshake = bytearray(), bytearray()
    while len(handshake) < 4 or len(handshake) < 4 + int.from_bytes(handshake[1:4], "big"):
        header = _read_exact(sock, 5)
        if header[0] != 0x16 or header[1] != 0x03:
            raise NetworkRefused("The plugin connection did not start with a TLS greeting.")
        body = _read_exact(sock, int.from_bytes(header[3:5], "big"))
        raw.extend(header + body)
        handshake.extend(body)
        if len(raw) > _HELLO_LIMIT:
            raise NetworkRefused("The plugin's TLS greeting is too large.")
    if handshake[0] != 0x01:
        raise NetworkRefused("The plugin connection did not start with a TLS greeting.")
    return bytes(raw), _server_name(bytes(handshake[4:4 + int.from_bytes(handshake[1:4], "big")]))


def _server_name(hello: bytes) -> str:
    try:
        at = 2 + 32
        at += 1 + hello[at]
        at += 2 + int.from_bytes(hello[at:at + 2], "big")
        at += 1 + hello[at]
        end = at + 2 + int.from_bytes(hello[at:at + 2], "big")
        at += 2
        names = []
        while at + 4 <= end:
            kind, size = int.from_bytes(hello[at:at + 2], "big"), int.from_bytes(hello[at + 2:at + 4], "big")
            value = hello[at + 4:at + 4 + size]
            if kind == 0x0000:
                inner = 2
                while inner + 3 <= len(value):
                    length = int.from_bytes(value[inner + 1:inner + 3], "big")
                    if value[inner] == 0:
                        names.append(value[inner + 3:inner + 3 + length].decode("ascii"))
                    inner += 3 + length
            at += 4 + size
    except (IndexError, UnicodeDecodeError):
        raise NetworkRefused("The plugin's TLS greeting could not be read.") from None
    if len(names) != 1:
        raise NetworkRefused("The plugin's TLS greeting must name exactly one server.")
    return names[0].rstrip(".").lower()


def destination(url: str, hosts: list[str] | tuple[str, ...]) -> tuple[str, str]:
    try:
        parsed = https_url(url, hosts)
    except (ValueError, TypeError):
        raise NetworkRefused("The plugin tried to reach a host outside its declared HTTPS access.") from None
    return parsed.hostname, (parsed.path or "/") + (("?" + parsed.query) if parsed.query else "")


# IPv6 ranges that carry an IPv4 address inside them. `is_global` can call these public
# while they reach a private or loopback IPv4 host through a translator or tunnel.
_IPV4_CARRYING = tuple(ipaddress.ip_network(net) for net in (
    "64:ff9b::/96", "64:ff9b:1::/48", "2002::/16", "2001::/32", "::ffff:0:0/96", "::/96"))


def is_public_address(address) -> bool:
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return False
        if any(address in network for network in _IPV4_CARRYING):
            return False
    return address.is_global and not (address.is_multicast or address.is_reserved or address.is_unspecified)


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
        return is_public_address(ipaddress.ip_address(row[4][0]))

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


def network_available(*, private_environment: bool = False) -> bool:
    """Whether a plugin process here can reach its declared hosts through this proxy.

    Only the macOS sandbox can confine a child to one loopback port, so only there
    does the proxy limit a process to its declared hosts. Linux process namespaces and
    the isolated container cannot reach a host-side loopback proxy, so plugin process
    network stays off there. VOOL's own provider and scanner requests use their
    declared-host transport on every platform.
    """
    import sys

    if sys.platform != "darwin" or private_environment:
        return False
    from core.plugin_executor import kernel_confinement_available

    return kernel_confinement_available()


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
                    client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                    hello, server_name = read_client_hello(client)
                    if server_name != host:
                        # Refused before any connection to the declared host's address is made.
                        return
                    upstream = connect_public(host, call=owner.call)
                    owner.call.check()
                    with owner.lock:
                        owner.sockets.add(upstream)
                    upstream.sendall(hello)
                    carried = len(hello)
                    while True:
                        owner.call.check()
                        ready, _, _ = select.select((client, upstream), (), (), 0.1)
                        for source in ready:
                            block = source.recv(65536)
                            if not block:
                                return
                            carried += len(block)
                            if carried > CONNECTION_BYTE_CAP:
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
        except OSError:
            raise NetworkRefused("The plugin's private network connection could not start. Network access remains off.") from None
        # The IPv6 loopback listener only serves clients that resolve localhost to ::1; a
        # machine without IPv6 loopback still has the IPv4 proxy the environment points at.
        self.server6, self.thread6 = None, None
        with contextlib.suppress(OSError):
            self.server6 = Server6(("::1", self.server.server_address[1]), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
        self.thread.start()
        if self.server6 is not None:
            self.thread6 = threading.Thread(target=self.server6.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
            self.thread6.start()
        call.own(self.close)

    @property
    def environment(self) -> dict[str, str]:
        address = f"http://{self.token}:@127.0.0.1:{self.port}"
        return {"HTTPS_PROXY": address, "https_proxy": address, "ALL_PROXY": address,
                "all_proxy": address, "NO_PROXY": "", "no_proxy": ""}

    def close(self):
        for server in (self.server, self.server6):
            if server is not None:
                server.shutdown()
                server.server_close()
        with self.lock:
            connections, self.sockets = list(self.sockets), set()
        for connection in connections:
            with contextlib.suppress(OSError):
                connection.shutdown(socket.SHUT_RDWR)
            connection.close()
        if threading.current_thread() is not self.thread:
            self.thread.join(2)
        if self.thread6 is not None and threading.current_thread() is not self.thread6:
            self.thread6.join(2)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()
