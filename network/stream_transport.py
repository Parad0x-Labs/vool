from __future__ import annotations

import contextlib
import socket
import ssl
import struct
import threading
from collections.abc import Callable
from dataclasses import dataclass

from core import policy_engine

_LEN_STRUCT = struct.Struct("!I")


@dataclass(frozen=True)
class StreamEndpoint:
    host: str
    port: int


@dataclass(frozen=True)
class StreamServerTlsConfig:
    enabled: bool = False
    certfile: str | None = None
    keyfile: str | None = None
    ca_file: str | None = None
    require_client_cert: bool = False


@dataclass(frozen=True)
class StreamClientTlsConfig:
    enabled: bool = False
    ca_file: str | None = None
    insecure_skip_verify: bool = False


class StreamTransportServer:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 0,
        on_frame: Callable[[bytes, tuple[str, int]], bytes | None] | None = None,
        tls_config: StreamServerTlsConfig | None = None,
    ):
        self.host = host
        self.port = int(port)
        self.on_frame = on_frame
        self.tls_config = tls_config or StreamServerTlsConfig()
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._tls_context: ssl.SSLContext | None = None
        self._max_frame_bytes = max(1024, int(policy_engine.get("network.stream.max_frame_bytes", 262144)))
        self._client_timeout_seconds = max(1.0, float(policy_engine.get("network.stream.client_timeout_seconds", 10.0)))
        max_clients = max(1, int(policy_engine.get("network.stream.max_concurrent_clients", 128)))
        self._client_sem: threading.BoundedSemaphore = threading.BoundedSemaphore(value=max_clients)

    def start(self, *, prebound_socket: socket.socket | None = None) -> StreamEndpoint:
        if self._sock is not None:
            sockname = self._sock.getsockname()
            return StreamEndpoint(host=str(sockname[0]), port=int(sockname[1]))

        self._tls_context = _build_server_tls_context(self.tls_config)
        sock = prebound_socket or socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if prebound_socket is None:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((self.host, self.port))
            backlog = max(16, int(policy_engine.get("network.stream.listen_backlog", 128)))
            sock.listen(backlog)
        except Exception:
            sock.close()
            raise
        self._sock = sock
        self._thread = threading.Thread(target=self._serve, name="vool-stream-transport", daemon=True)
        self._thread.start()
        sockname = self._sock.getsockname()
        return StreamEndpoint(host=str(sockname[0]), port=int(sockname[1]))

    def stop(self) -> None:
        self._stop.set()
        if self._sock is not None:
            with contextlib.suppress(OSError):
                self._sock.close()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def _serve(self) -> None:
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                client, addr = self._sock.accept()
            except OSError:
                break
            if not self._client_sem.acquire(blocking=False):
                with contextlib.suppress(OSError):
                    client.close()
                continue
            if self._tls_context is not None:
                try:
                    client = self._tls_context.wrap_socket(client, server_side=True)
                except Exception:
                    with contextlib.suppress(OSError):
                        client.close()
                    self._client_sem.release()
                    continue
            t = threading.Thread(target=self._handle_client, args=(client, addr), daemon=True)
            t.start()

    def _handle_client(self, client: socket.socket, addr: tuple[str, int]) -> None:
        try:
            with client:
                client.settimeout(self._client_timeout_seconds)
                while not self._stop.is_set():
                    try:
                        raw_len = _recv_exact(client, _LEN_STRUCT.size)
                        if not raw_len:
                            return
                        frame_len = _LEN_STRUCT.unpack(raw_len)[0]
                        if frame_len <= 0 or frame_len > self._max_frame_bytes:
                            return
                        payload = _recv_exact(client, frame_len)
                        if payload is None:
                            return
                        response = self.on_frame(payload, addr) if self.on_frame else None
                        if response is not None:
                            if len(response) > self._max_frame_bytes:
                                return
                            _send_framed(client, response)
                    except (OSError, TimeoutError):
                        return
        finally:
            self._client_sem.release()


def _recv_exact(sock: socket.socket, expected: int) -> bytes | None:
    buf = bytearray()
    while len(buf) < expected:
        chunk = sock.recv(expected - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


def _send_framed(sock: socket.socket, payload: bytes) -> None:
    sock.sendall(_LEN_STRUCT.pack(len(payload)))
    sock.sendall(payload)


def send_frame(
    endpoint: StreamEndpoint,
    payload: bytes,
    *,
    timeout_seconds: float = 5.0,
    tls_config: StreamClientTlsConfig | None = None,
) -> bytes | None:
    max_frame_bytes = max(1024, int(policy_engine.get("network.stream.max_frame_bytes", 262144)))
    if len(payload) > max_frame_bytes:
        return None
    cfg = tls_config or StreamClientTlsConfig()
    with socket.create_connection((endpoint.host, endpoint.port), timeout=timeout_seconds) as raw_sock:
        sock: socket.socket
        if cfg.enabled:
            context = _build_client_tls_context(cfg)
            server_hostname = None if cfg.insecure_skip_verify else endpoint.host
            sock = context.wrap_socket(raw_sock, server_hostname=server_hostname)
        else:
            sock = raw_sock
        _send_framed(sock, payload)
        sock.settimeout(timeout_seconds)
        raw_len = _recv_exact(sock, _LEN_STRUCT.size)
        if raw_len is None:
            return None
        frame_len = _LEN_STRUCT.unpack(raw_len)[0]
        if frame_len <= 0 or frame_len > max_frame_bytes:
            return None
        return _recv_exact(sock, frame_len)


def _build_server_tls_context(cfg: StreamServerTlsConfig) -> ssl.SSLContext | None:
    if not cfg.enabled:
        return None
    certfile = str(cfg.certfile or "").strip()
    keyfile = str(cfg.keyfile or "").strip()
    if not certfile or not keyfile:
        raise ValueError("Stream TLS is enabled but certfile/keyfile are missing.")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certfile=certfile, keyfile=keyfile)
    cafile = str(cfg.ca_file or "").strip()
    if cafile:
        context.load_verify_locations(cafile=cafile)
        context.verify_mode = ssl.CERT_REQUIRED if cfg.require_client_cert else ssl.CERT_OPTIONAL
    return context


def _build_client_tls_context(cfg: StreamClientTlsConfig) -> ssl.SSLContext:
    if cfg.insecure_skip_verify:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context
    cafile = str(cfg.ca_file or "").strip()
    if cafile:
        return ssl.create_default_context(cafile=cafile)
    return ssl.create_default_context()
