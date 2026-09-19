from __future__ import annotations

import base64
import contextlib
import errno
import os
import secrets
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from uuid import uuid4

from core import audit_logger, policy_engine
from network.chunk_protocol import decode_frame
from network.stream_transport import (
    StreamClientTlsConfig,
    StreamEndpoint,
    StreamServerTlsConfig,
    StreamTransportServer,
)
from network.transfer_manager import TransferManager

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except Exception:  # pragma: no cover
    AESGCM = None  # type: ignore[assignment]


_FRAG_MAGIC = b"NFR1"
_FRAG_VERSION = 1
_FRAG_HEADER_LEN = 32
_ENC_MAGIC = b"NEN1"
_ENC_AAD = b"vool-mesh-v1"
_EPHEMERAL_PAIR_RETRIES = 16


def _datagram_limit() -> int:
    return int(policy_engine.get("system.max_datagram_bytes", 32768))


def _fragment_datagram_limit() -> int:
    configured = int(policy_engine.get("system.max_fragment_datagram_bytes", 1400))
    return max(_FRAG_HEADER_LEN + 1, min(_datagram_limit(), configured))


def _message_limit() -> int:
    return int(policy_engine.get("system.max_message_bytes", 262144))


def _stream_threshold() -> int:
    return int(policy_engine.get("system.stream_transfer_threshold_bytes", 24576))


def _stream_enabled() -> bool:
    return bool(policy_engine.get("system.enable_stream_data_plane", True))


def _fragment_enabled() -> bool:
    return bool(policy_engine.get("system.enable_udp_fragmentation", True))


def _stream_port_for_udp_port(port: int) -> int:
    return int(port) + 1


def _frag_bucket_limit() -> int:
    return int(policy_engine.get("system.max_fragment_buckets", 2048))


def _frag_timeout_seconds() -> float:
    return float(policy_engine.get("system.fragment_timeout_seconds", 30.0))


def _udp_socket_buffer_bytes() -> int:
    configured = int(policy_engine.get("system.udp_socket_buffer_bytes", 0) or 0)
    if configured > 0:
        return configured
    return max(1_048_576, _message_limit() * 2)


def _fragment_burst_packets() -> int:
    return max(1, int(policy_engine.get("system.fragment_burst_packets", 1) or 1))


def _fragment_pause_seconds() -> float:
    return max(0.0, float(policy_engine.get("system.fragment_pause_seconds", 0.002) or 0.0))


def _fragment_send_passes() -> int:
    return max(1, int(policy_engine.get("system.fragment_send_passes", 2) or 1))


def _mesh_psk_bytes() -> bytes | None:
    raw = os.environ.get("VOOL_MESH_PSK_B64") or str(policy_engine.get("system.mesh_psk_b64", "") or "")
    raw = raw.strip()
    if not raw:
        return None
    try:
        key = base64.b64decode(raw.encode("ascii"))
    except Exception:
        return None
    if len(key) != 32:
        return None
    return key


def _mesh_encryption_required() -> bool:
    return bool(policy_engine.get("system.require_mesh_encryption", False))


def _stream_tls_server_config() -> StreamServerTlsConfig:
    return StreamServerTlsConfig(
        enabled=bool(policy_engine.get("system.stream_tls_enabled", False)),
        certfile=str(policy_engine.get("system.stream_tls_certfile", "") or "").strip() or None,
        keyfile=str(policy_engine.get("system.stream_tls_keyfile", "") or "").strip() or None,
        ca_file=str(policy_engine.get("system.stream_tls_ca_file", "") or "").strip() or None,
        require_client_cert=bool(policy_engine.get("system.stream_tls_require_client_cert", False)),
    )


def _stream_tls_client_config() -> StreamClientTlsConfig:
    return StreamClientTlsConfig(
        enabled=bool(policy_engine.get("system.stream_tls_enabled", False)),
        ca_file=str(policy_engine.get("system.stream_tls_ca_file", "") or "").strip() or None,
        insecure_skip_verify=bool(policy_engine.get("system.stream_tls_insecure_skip_verify", False)),
    )


def _encrypt_for_mesh(payload: bytes) -> bytes:
    key = _mesh_psk_bytes()
    if key is None and _mesh_encryption_required():
        raise ValueError("Mesh encryption is required but mesh PSK is not configured.")
    if key is None or AESGCM is None:
        return payload
    nonce = secrets.token_bytes(12)
    encrypted = AESGCM(key).encrypt(nonce, payload, _ENC_AAD)
    return _ENC_MAGIC + nonce + encrypted


def _decrypt_for_mesh(payload: bytes) -> bytes:
    if _mesh_encryption_required() and not payload.startswith(_ENC_MAGIC):
        raise ValueError("Mesh encryption is required but an unencrypted payload was received.")
    if not payload.startswith(_ENC_MAGIC):
        return payload
    key = _mesh_psk_bytes()
    if key is None or AESGCM is None:
        raise ValueError("Encrypted mesh payload received but mesh PSK is not configured.")
    if len(payload) <= len(_ENC_MAGIC) + 12:
        raise ValueError("Encrypted mesh payload malformed.")
    nonce_start = len(_ENC_MAGIC)
    nonce = payload[nonce_start : nonce_start + 12]
    ciphertext = payload[nonce_start + 12 :]
    return AESGCM(key).decrypt(nonce, ciphertext, _ENC_AAD)


def _frag_header(transfer_hex: str, index: int, total: int) -> bytes:
    transfer_raw = bytes.fromhex(transfer_hex)
    if len(transfer_raw) != 16:
        raise ValueError("transfer id must be 16 bytes")
    return (
        _FRAG_MAGIC
        + bytes([_FRAG_VERSION])
        + transfer_raw
        + int(index).to_bytes(2, "big")
        + int(total).to_bytes(2, "big")
        + b"\x00" * 7
    )


def _parse_frag_header(packet: bytes) -> tuple[str, int, int] | None:
    if len(packet) < _FRAG_HEADER_LEN:
        return None
    if packet[:4] != _FRAG_MAGIC:
        return None
    if packet[4] != _FRAG_VERSION:
        return None
    transfer_id = packet[5:21].hex()
    index = int.from_bytes(packet[21:23], "big")
    total = int.from_bytes(packet[23:25], "big")
    if total <= 0 or index >= total:
        return None
    return transfer_id, index, total


def _is_address_in_use_error(exc: OSError) -> bool:
    err_no = getattr(exc, "errno", None)
    winerror = getattr(exc, "winerror", None)
    return err_no == errno.EADDRINUSE or err_no == 10048 or winerror == 10048


def _raise_udp_bind_conflict(host: str, port: int, exc: OSError) -> None:
    raise OSError(
        getattr(exc, "errno", None) or getattr(exc, "winerror", None) or errno.EADDRINUSE,
        f"UDP transport cannot bind {host}:{port}: port already in use. "
        "Stop the conflicting process or choose a different port.",
    ) from exc


def _report_udp_port_conflict(port: int) -> None:
    """Identify (never terminate) the process holding a UDP port we could not bind.

    Correct conflict behavior everywhere — production AND research: report who
    holds the port, fail this subsystem's startup cleanly, never kill an
    unrelated process.
    """
    import subprocess

    try:
        res = subprocess.run(["lsof", "-nP", "-i", f"udp:{int(port)}"], capture_output=True, text=True, timeout=5)
        holders = (res.stdout or "").strip()
    except Exception:
        holders = ""
    audit_logger.log(
        "udp_port_conflict",
        target_id=f"udp:{int(port)}",
        target_type="transport",
        details={"holders": holders[:500] if holders else "unknown"},
    )


def _is_bind_conflict(error: OSError) -> bool:
    codes = {
        int(getattr(errno, "EADDRINUSE", 48)),
        48,
        98,
        10048,
    }
    return int(getattr(error, "errno", -1) or -1) in codes or int(getattr(error, "winerror", -1) or -1) in codes


def _configure_udp_socket_buffers(sock: socket.socket) -> None:
    buffer_bytes = _udp_socket_buffer_bytes()
    with contextlib.suppress(OSError):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, buffer_bytes)
    with contextlib.suppress(OSError):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, buffer_bytes)


@dataclass
class TransportRuntime:
    host: str
    port: int
    public_host: str
    public_port: int
    running: bool
    stream_host: str | None = None
    stream_port: int | None = None


class UDPTransportServer:
    """
    Minimal UDP transport for v1.
    Safe defaults:
    - bounded packet size
    - no connection state
    - callback gets raw bytes + source addr
    """

    def __init__(
        self,
        *,
        host: str = "0.0.0.0",
        port: int = 49152,
        on_message: Callable[[bytes, tuple[str, int]], None] | None = None,
    ) -> None:
        self.host = host
        self.port = int(port)
        # What the CALLER asked for, kept separate from what a bind happened to yield. `start`
        # writes the assigned port back to self.port, which is right for anyone reading the running
        # server's address and wrong to carry into the next start: a server asked for an ephemeral
        # port that is restarted must ask for an ephemeral port again, not pin itself to last time's.
        self._requested_port = int(port)
        self.on_message = on_message
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stream_server: StreamTransportServer | None = None
        self._stream_endpoint: StreamEndpoint | None = None
        self._transfer = TransferManager()
        self._frag_lock = threading.Lock()
        self._frag_buckets: dict[str, dict[str, object]] = {}
        self._frag_completed: dict[str, float] = {}
        self._stop = threading.Event()

    def start(self) -> TransportRuntime:
        if self._thread and self._thread.is_alive():
            return TransportRuntime(
                self.host,
                self.port,
                getattr(self, "public_host", self.host),
                getattr(self, "public_port", self.port),
                True,
                self._stream_endpoint.host if self._stream_endpoint else None,
                self._stream_endpoint.port if self._stream_endpoint else None,
            )

        # The CALLER's port, not last run's result. Reading self.port here meant a stop/start cycle
        # silently rewrote the server's own configuration: a server constructed with port=0 came
        # back up on the previous run's port, so it took the fixed-port branch, which rebinds the
        # adjacent stream port rather than allocating a fresh pair. That rebind lands on a socket
        # still in TIME_WAIT -- tolerated on macOS, refused on Linux, where the stream server then
        # fail-softs and the restarted transport silently has no stream endpoint at all. Green on
        # every developer machine, red on every CI run.
        requested_port = int(self._requested_port)
        stream_enabled = _stream_enabled()
        if requested_port == 0 and stream_enabled:
            sock, self._stream_server, self._stream_endpoint = self._bind_ephemeral_pair()
        else:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            _configure_udp_socket_buffers(sock)
            try:
                sock.bind((self.host, requested_port))
            except OSError as bind_err:
                sock = self._recover_udp_bind_conflict(sock, requested_port, bind_err)

        bound_port = int(sock.getsockname()[1])

        # Phase 23: STUN Public Endpoint Discovery — research networking only.
        # A production build never probes an external STUN service for a public
        # endpoint; the mesh transport that consumed it does not run in production.
        from core.runtime_mode import research_networking_enabled

        public_endpoint = None
        if research_networking_enabled():
            from network.stun_client import discover_public_endpoint

            public_endpoint = discover_public_endpoint(sock)

        if public_endpoint:
            self.public_host, self.public_port = public_endpoint
            audit_logger.log(
                "stun_discovery_success",
                target_id=f"{self.public_host}:{self.public_port}",
                target_type="transport",
                details={},
            )
        else:
            self.public_host = self.host
            self.public_port = bound_port
            audit_logger.log("stun_discovery_failed", target_id=self.host, target_type="transport", details={})

        self.port = bound_port
        sock.settimeout(1.0)
        self._sock = sock
        self._stop.clear()

        self._thread = threading.Thread(
            target=self._loop,
            name="vool-udp-transport",
            daemon=True,
        )
        self._thread.start()

        if stream_enabled and self._stream_server is None:
            try:
                self._stream_server = StreamTransportServer(
                    host=self.host,
                    port=_stream_port_for_udp_port(self.port),
                    on_frame=self._on_stream_frame,
                    tls_config=_stream_tls_server_config(),
                )
                self._stream_endpoint = self._stream_server.start()
            except Exception as exc:
                self._stream_server = None
                self._stream_endpoint = None
                audit_logger.log(
                    "stream_transport_start_failed",
                    target_id=f"{self.host}:{_stream_port_for_udp_port(self.port)}",
                    target_type="transport",
                    details={"error": str(exc), "mode": "explicit_port_fail_soft"},
                )

        audit_logger.log(
            "transport_started",
            target_id=(
                f"{self.host}:{self.port} (public {self.public_host}:{self.public_port})"
                + (
                    f" stream={self._stream_endpoint.host}:{self._stream_endpoint.port}"
                    if self._stream_endpoint
                    else " stream=disabled"
                )
            ),
            target_type="transport",
            details={},
        )

        return TransportRuntime(
            self.host,
            self.port,
            self.public_host,
            self.public_port,
            True,
            self._stream_endpoint.host if self._stream_endpoint else None,
            self._stream_endpoint.port if self._stream_endpoint else None,
        )

    def _recover_udp_bind_conflict(
        self,
        sock: socket.socket,
        requested_port: int,
        bind_err: OSError,
    ) -> socket.socket:
        if _is_bind_conflict(bind_err) and requested_port > 0:
            sock.close()
            _report_udp_port_conflict(requested_port)
            raise OSError(
                errno.EADDRINUSE,
                f"UDP port {requested_port} is already in use (by another process; "
                "VOOL never terminates the holder). Start the transport on another "
                "port or stop the conflicting service.",
            ) from bind_err
        sock.close()
        raise bind_err

    def _bind_ephemeral_pair(self) -> tuple[socket.socket, StreamTransportServer, StreamEndpoint]:
        last_error: Exception | None = None
        for _attempt in range(_EPHEMERAL_PAIR_RETRIES):
            stream_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock: socket.socket | None = None
            try:
                stream_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                stream_sock.bind((self.host, 0))
                stream_port = int(stream_sock.getsockname()[1])
                if stream_port <= 1:
                    raise OverflowError("ephemeral stream port cannot have an adjacent UDP port")
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                _configure_udp_socket_buffers(sock)
                sock.bind((self.host, stream_port - 1))
                stream_server = StreamTransportServer(
                    host=self.host,
                    port=stream_port,
                    on_frame=self._on_stream_frame,
                    tls_config=_stream_tls_server_config(),
                )
                stream_endpoint = stream_server.start(prebound_socket=stream_sock)
                return sock, stream_server, stream_endpoint
            except (OSError, OverflowError) as exc:
                if sock is not None:
                    sock.close()
                stream_sock.close()
                last_error = exc
                if isinstance(exc, OSError) and not (
                    _is_bind_conflict(exc) or isinstance(exc, PermissionError)
                ):
                    raise
        audit_logger.log(
            "stream_transport_start_failed",
            target_id=f"{self.host}:ephemeral",
            target_type="transport",
            details={"error": str(last_error or "adjacent port pair unavailable"), "mode": "ephemeral_required"},
        )
        raise OSError(f"Could not allocate an adjacent UDP/stream port pair after {_EPHEMERAL_PAIR_RETRIES} attempts")

    def stop(self) -> None:
        self._stop.set()
        if self._sock:
            with contextlib.suppress(Exception):
                self._sock.close()
        if self._stream_server:
            with contextlib.suppress(Exception):
                self._stream_server.stop()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._sock = None
        self._thread = None
        self._stream_server = None
        self._stream_endpoint = None

        audit_logger.log(
            "transport_stopped",
            target_id=f"{self.host}:{self.port}",
            target_type="transport",
            details={},
        )
        # self.port is deliberately LEFT as the port that was actually served -- callers read it
        # after stop to learn what ran (test_bind_conflict_falls_back_to_ephemeral_udp_port does
        # exactly that). What must not leak across a restart is the BINDING decision, and that
        # reads self._requested_port in start() instead.

    def _loop(self) -> None:
        assert self._sock is not None
        max_bytes = _datagram_limit()

        while not self._stop.is_set():
            try:
                data, addr = self._sock.recvfrom(max_bytes)
            except TimeoutError:
                continue
            except OSError:
                break
            except Exception as e:
                audit_logger.log(
                    "transport_receive_error",
                    target_id=f"{self.host}:{self.port}",
                    target_type="transport",
                    details={"error": str(e)},
                )
                continue

            if not data:
                continue

            try:
                payload = self._handle_fragment_packet(data)
                if payload is None:
                    continue
                decoded = _decrypt_for_mesh(payload)
                if self.on_message:
                    self.on_message(decoded, addr)
            except Exception as e:
                audit_logger.log(
                    "transport_callback_error",
                    target_id=f"{self.host}:{self.port}",
                    target_type="transport",
                    details={"error": str(e)},
                )

    def _handle_fragment_packet(self, packet: bytes) -> bytes | None:
        parsed = _parse_frag_header(packet)
        if parsed is None:
            return packet
        transfer_id, index, total = parsed
        chunk = packet[_FRAG_HEADER_LEN:]
        now = time.time()
        timeout_seconds = max(1.0, _frag_timeout_seconds())
        bucket_limit = max(32, _frag_bucket_limit())
        with self._frag_lock:
            stale_completed = [
                tid for tid, completed_at in self._frag_completed.items() if now - completed_at > timeout_seconds
            ]
            for tid in stale_completed:
                self._frag_completed.pop(tid, None)
            stale = [
                tid
                for tid, bucket in self._frag_buckets.items()
                if now - float(bucket.get("created_at", now)) > timeout_seconds
            ]
            for tid in stale:
                self._frag_buckets.pop(tid, None)
            if transfer_id in self._frag_completed:
                return None
            bucket = self._frag_buckets.get(transfer_id)
            if bucket is None:
                if len(self._frag_buckets) >= bucket_limit:
                    return None
                bucket = {"total": total, "chunks": {}, "created_at": now}
                self._frag_buckets[transfer_id] = bucket
            if int(bucket.get("total", total)) != total:
                self._frag_buckets.pop(transfer_id, None)
                return None
            chunks = bucket["chunks"]  # type: ignore[index]
            if isinstance(chunks, dict):
                chunks[index] = chunk
                if len(chunks) == total:
                    out = b"".join(chunks[i] for i in range(total))
                    self._frag_buckets.pop(transfer_id, None)
                    self._frag_completed[transfer_id] = now
                    if len(out) > _message_limit():
                        return None
                    return out
        return None

    def _on_stream_frame(self, payload: bytes, addr: tuple[str, int]) -> bytes | None:
        response = self._transfer.receive_frame(payload)
        try:
            _msg_type, body = decode_frame(payload)
            transfer_id = str(body.get("transfer_id") or "")
            if transfer_id:
                completed = self._transfer.completed_payload(transfer_id)
                if completed is not None and self.on_message:
                    self.on_message(_decrypt_for_mesh(completed), addr)
        except Exception as exc:
            audit_logger.log(
                "stream_frame_processing_error",
                target_id=f"{self.host}:{self.port}",
                target_type="transport",
                details={"error": str(exc)},
            )
        return response


def send_message(host: str, port: int, payload: bytes, timeout_seconds: float = 1.0) -> bool:
    msg_limit = _message_limit()
    if len(payload) > msg_limit:
        return False

    try:
        prepared = _encrypt_for_mesh(payload)
    except Exception:
        return False
    if len(prepared) > _stream_threshold() and _stream_enabled():
        stream_port = _stream_port_for_udp_port(int(port))
        retries = max(0, int(policy_engine.get("network.stream_send_retries", 1)))
        attempts = 1 + retries
        for attempt in range(1, attempts + 1):
            try:
                TransferManager(tls_client_config=_stream_tls_client_config()).send_payload(
                    StreamEndpoint(host=host, port=stream_port),
                    prepared,
                )
                return True
            except Exception:
                if attempt < attempts:
                    time.sleep(min(0.25, 0.05 * attempt))
                continue

    datagram_limit = _datagram_limit()
    if len(prepared) > datagram_limit:
        if not _fragment_enabled():
            return False
        return _send_fragmented(host, int(port), prepared, timeout_seconds=timeout_seconds)

    try:
        with _get_send_socket(timeout_seconds) as sock:
            sent = sock.sendto(prepared, (host, int(port)))
            return sent == len(prepared)
    except Exception:
        return False


def _send_fragmented(host: str, port: int, payload: bytes, *, timeout_seconds: float) -> bool:
    datagram_limit = _fragment_datagram_limit()
    chunk_space = datagram_limit - _FRAG_HEADER_LEN
    if chunk_space <= 0:
        return False
    total = (len(payload) + chunk_space - 1) // chunk_space
    if total > 65535:
        return False
    transfer_id = uuid4().hex
    burst_packets = _fragment_burst_packets()
    pause_seconds = _fragment_pause_seconds()
    send_passes = _fragment_send_passes()
    try:
        with _get_send_socket(timeout_seconds) as sock:
            packet_index = 0
            for index in range(total):
                start = index * chunk_space
                end = min(len(payload), start + chunk_space)
                chunk = payload[start:end]
                packet = _frag_header(transfer_id, index, total) + chunk
                for _copy_index in range(send_passes):
                    packet_index += 1
                    sent = sock.sendto(packet, (host, port))
                    if sent != len(packet):
                        return False
                    if pause_seconds > 0 and packet_index < total * send_passes and packet_index % burst_packets == 0:
                        time.sleep(pause_seconds)
                if send_passes > 1 and pause_seconds > 0 and index + 1 < total:
                    time.sleep(max(pause_seconds, 0.001))
        return True
    except Exception:
        return False


def _get_send_socket(timeout_seconds: float) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    _configure_udp_socket_buffers(sock)
    sock.settimeout(timeout_seconds)
    return sock
