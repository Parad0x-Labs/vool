#!/usr/bin/env python3
"""A stand-in for the tor binary, for tests of core/tor_runtime.py on machines without Tor.

It speaks the parts of Tor's command line and control protocol VOOL uses: auto ports written to
ControlPortWriteToFile, cookie authentication, TAKEOWNERSHIP, GETINFO net/listeners/socks and
status/bootstrap-phase, ADD_ONION / DEL_ONION, SIGNAL SHUTDOWN, and exiting when the owning
process dies. Its SOCKS port only answers the greeting.

FAKE_TOR_BOOTSTRAP: seconds until it reports PROGRESS=100, or "never".
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import socket
import sys
import threading
import time

args = sys.argv[1:]
options: dict[str, str] = {}
i = 0
while i < len(args):
    if args[i] == "-f":
        i += 2
        continue
    if args[i].startswith("--"):
        i += 1
        continue
    options[args[i]] = args[i + 1]
    i += 2

started = time.monotonic()
bootstrap = os.environ.get("FAKE_TOR_BOOTSTRAP", "0")
cookie = secrets.token_bytes(32)
with open(options["CookieAuthFile"], "wb") as fh:
    fh.write(cookie)

socks = socket.create_server(("127.0.0.1", 0))
control = socket.create_server(("127.0.0.1", 0))
onions: set[str] = set()
stop = threading.Event()


def progress() -> int:
    if bootstrap == "never":
        return 14
    return 100 if time.monotonic() - started >= float(bootstrap) else 50


def service_id(key: str) -> str:
    return base64.b32encode(hashlib.sha256(key.encode()).digest() + b"\0\0\0").decode().lower()[:56]


def serve_socks() -> None:
    while not stop.is_set():
        try:
            conn, _ = socks.accept()
        except OSError:
            return
        try:
            conn.recv(16)
            conn.sendall(b"\x05\x00")
        except OSError:
            pass
        conn.close()


def serve_control_client(conn: socket.socket) -> None:
    reader = conn.makefile("rb")
    authed = False
    for raw in reader:
        line = raw.decode().strip()
        verb, _, rest = line.partition(" ")
        if verb == "AUTHENTICATE":
            authed = rest == cookie.hex()
            conn.sendall(b"250 OK\r\n" if authed else b"515 Authentication failed\r\n")
            if not authed:
                break
            continue
        if not authed:
            conn.sendall(b"514 Authentication required\r\n")
            break
        if verb == "TAKEOWNERSHIP":
            conn.sendall(b"250 OK\r\n")
        elif verb == "GETINFO" and rest == "net/listeners/socks":
            conn.sendall(f'250-net/listeners/socks="127.0.0.1:{socks.getsockname()[1]}"\r\n250 OK\r\n'.encode())
        elif verb == "GETINFO" and rest == "status/bootstrap-phase":
            conn.sendall(f"250-status/bootstrap-phase=NOTICE BOOTSTRAP PROGRESS={progress()} TAG=x SUMMARY=\"x\"\r\n250 OK\r\n".encode())
        elif verb == "ADD_ONION":
            key = rest.split(" ")[0]
            new_key = ""
            if key == "NEW:ED25519-V3":
                new_key = "ED25519-V3:" + base64.b64encode(secrets.token_bytes(64)).decode()
                key = new_key
            sid = service_id(key)
            if sid in onions:
                conn.sendall(b"550 Onion address collision\r\n")
                continue
            onions.add(sid)
            reply = f"250-ServiceID={sid}\r\n"
            if new_key:
                reply += f"250-PrivateKey={new_key}\r\n"
            conn.sendall((reply + "250 OK\r\n").encode())
        elif verb == "DEL_ONION":
            onions.discard(rest)
            conn.sendall(b"250 OK\r\n")
        elif verb == "SIGNAL" and rest == "SHUTDOWN":
            conn.sendall(b"250 OK\r\n")
            stop.set()
            break
        else:
            conn.sendall(b"510 Unrecognized command\r\n")
    conn.close()


def serve_control() -> None:
    while not stop.is_set():
        try:
            conn, _ = control.accept()
        except OSError:
            return
        threading.Thread(target=serve_control_client, args=(conn,), daemon=True).start()


threading.Thread(target=serve_socks, daemon=True).start()
threading.Thread(target=serve_control, daemon=True).start()
with open(options["ControlPortWriteToFile"], "w") as fh:
    fh.write(f"PORT=127.0.0.1:{control.getsockname()[1]}\n")

owner = int(options.get("__OwningControllerProcess", "0") or 0)
while not stop.is_set():
    if owner:
        try:
            os.kill(owner, 0)
        except ProcessLookupError:
            break
    time.sleep(0.1)
socks.close()
control.close()
