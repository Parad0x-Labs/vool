from __future__ import annotations

import os
import socket
import struct

STUN_SERVERS = [
    ("stun.l.google.com", 19302),
    ("stun1.l.google.com", 19302),
    ("stun2.l.google.com", 19302),
]

_HEADER_STRUCT = struct.Struct("!HHI12s")


def _parse_mapped_address(data: bytes, magic_cookie: int) -> tuple[str, int] | None:
    if len(data) < 20:
        return None
    res_type, res_len, _, _transaction_id = _HEADER_STRUCT.unpack(data[:20])
    if res_type != 0x0101:
        return None
    offset = 20
    boundary = min(len(data), 20 + res_len)
    while offset + 4 <= boundary:
        attr_type, attr_len = struct.unpack("!HH", data[offset : offset + 4])
        offset += 4
        payload = data[offset : offset + attr_len]
        if attr_type == 0x0020 and len(payload) >= 8:
            family = payload[1]
            if family == 0x01:
                x_port = struct.unpack("!H", payload[2:4])[0]
                x_ip = struct.unpack("!I", payload[4:8])[0]
                port = x_port ^ (magic_cookie >> 16)
                ip = socket.inet_ntoa(struct.pack("!I", x_ip ^ magic_cookie))
                return ip, int(port)
        if attr_type == 0x0001 and len(payload) >= 8:
            family = payload[1]
            if family == 0x01:
                port = struct.unpack("!H", payload[2:4])[0]
                ip = socket.inet_ntoa(payload[4:8])
                return ip, int(port)
        offset += attr_len
        if attr_len % 4:
            offset += 4 - (attr_len % 4)
    return None


def discover_public_endpoint(local_sock: socket.socket) -> tuple[str, int] | None:
    if os.environ.get("VOOL_DISABLE_STUN") == "1":
        return None

    magic_cookie = 0x2112A442
    request = _HEADER_STRUCT.pack(0x0001, 0x0000, magic_cookie, os.urandom(12))
    original_timeout = local_sock.gettimeout()
    local_sock.settimeout(0.75)
    try:
        for host, port in STUN_SERVERS:
            try:
                server_ip = socket.gethostbyname(host)
                local_sock.sendto(request, (server_ip, port))
                data, addr = local_sock.recvfrom(2048)
                if addr[0] != server_ip:
                    continue
                parsed = _parse_mapped_address(data, magic_cookie)
                if parsed:
                    return parsed
            except OSError:
                continue
    finally:
        local_sock.settimeout(original_timeout)
    return None
