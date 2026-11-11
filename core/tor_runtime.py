"""The one Tor process VOOL runs, shared by every feature that needs Tor.

Two features use it today:

* Private web (`core.private_web`) sends web traffic through its SOCKS port;
* "Use away from home" (the phone app) publishes an onion address through its control port.

Tor starts only when a feature the person switched on asks for it (`start(reason)`), and stops
when the last one lets go (`release(reason)`), so a fresh install never goes online by itself.

What VOOL runs:

* the Tor binary shipped with VOOL (or `VOOL_TOR_BINARY`, or a `tor` already on the PATH);
* its own DataDirectory under the VOOL data dir, owner-only;
* SocksPort and ControlPort on 127.0.0.1 with ports Tor picks itself, control by cookie;
* `__OwningControllerProcess` = this process, so Tor exits if VOOL dies;
* no torrc of the person's is read (`-f` points at an empty file VOOL owns).

Features never touch torrc: they use `socks_port` and `add_onion` / `remove_onion`.
"""

from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BOOTSTRAP_TIMEOUT_SECONDS = 120.0
_CONTROL_FILE_TIMEOUT_SECONDS = 30.0
_CONTROL_IO_TIMEOUT_SECONDS = 10.0


class TorUnavailable(RuntimeError):
    """Tor could not be started or is not ready. The message is plain and may reach the person."""


@dataclass(frozen=True)
class OnionService:
    address: str  # "<56 chars>.onion"
    private_key: str  # "ED25519-V3:<base64>", for the caller to keep in its credential store


def platform_dir() -> str:
    machine = platform.machine().lower()
    arch = {"x86_64": "x64", "amd64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(machine, machine)
    system = {"darwin": "macos", "linux": "linux", "win32": "windows"}.get(sys.platform, sys.platform)
    return f"{system}-{arch}"


def find_tor_binary(env: dict[str, str] | None = None) -> str | None:
    environ = os.environ if env is None else env
    pinned = str(environ.get("VOOL_TOR_BINARY") or "").strip()
    if pinned:
        return pinned if os.access(pinned, os.X_OK) else None
    from core.runtime_paths import project_path

    name = "tor.exe" if sys.platform == "win32" else "tor"
    bundled = project_path("third_party", "tor", platform_dir(), name)
    if bundled.is_file() and os.access(bundled, os.X_OK):
        return str(bundled)
    return shutil.which("tor")


def default_data_dir() -> Path:
    from core.runtime_paths import data_path

    return data_path("tor")


class _Control:
    """Tor's control protocol: one command, one reply, lines ending CRLF."""

    def __init__(self, port: int, cookie: bytes) -> None:
        self._sock = socket.create_connection(("127.0.0.1", port), timeout=_CONTROL_IO_TIMEOUT_SECONDS)
        self._file = self._sock.makefile("rb")
        self._lock = threading.Lock()
        code, lines = self.command(f"AUTHENTICATE {cookie.hex()}")
        if code != "250":
            self.close()
            raise TorUnavailable("Tor refused VOOL's control sign-in.")

    def command(self, line: str) -> tuple[str, list[str]]:
        with self._lock:
            self._sock.sendall(line.encode("utf-8") + b"\r\n")
            lines: list[str] = []
            while True:
                raw = self._file.readline()
                if not raw:
                    raise TorUnavailable("Tor closed its control connection.")
                text = raw.decode("utf-8", "replace").rstrip("\r\n")
                if len(text) < 4:
                    continue
                code, sep, rest = text[:3], text[3], text[4:]
                if sep == "+":  # data block, ends with a lone "."
                    block = [rest]
                    while True:
                        data = self._file.readline().decode("utf-8", "replace").rstrip("\r\n")
                        if data == ".":
                            break
                        block.append(data)
                    lines.append("\n".join(block))
                    continue
                lines.append(rest)
                if sep == " ":
                    return code, lines

    def getinfo(self, key: str) -> str:
        code, lines = self.command(f"GETINFO {key}")
        if code != "250":
            raise TorUnavailable(f"Tor did not answer {key}.")
        for line in lines:
            if line.startswith(key + "="):
                return line[len(key) + 1 :]
        return ""

    def close(self) -> None:
        for closer in (self._file.close, self._sock.close):
            try:
                closer()
            except OSError:
                pass


class TorRuntime:
    def __init__(self, *, data_dir: Path | None = None, binary: str | None = None) -> None:
        self._data_dir = Path(data_dir) if data_dir is not None else None
        self._binary = binary
        self._lock = threading.RLock()
        self._users: set[str] = set()
        self._process: subprocess.Popen[bytes] | None = None
        self._control: _Control | None = None
        self._socks_port = 0
        self._onions: set[str] = set()

    # -- state ---------------------------------------------------------------------------

    @property
    def running(self) -> bool:
        with self._lock:
            return self._process is not None and self._process.poll() is None and self._socks_port > 0

    @property
    def socks_port(self) -> int:
        with self._lock:
            return self._socks_port if self.running else 0

    def users(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._users)

    # -- lifecycle -----------------------------------------------------------------------

    def start(self, reason: str, *, timeout: float = BOOTSTRAP_TIMEOUT_SECONDS, wait_for_network: bool = True) -> int:
        """Register a user and make sure Tor is running. Returns the SOCKS port.

        `wait_for_network=True` (Private web) returns only once Tor reached the Tor network.
        An onion service can be added before that; Tor publishes it once connected.
        """
        reason = str(reason or "").strip()
        if not reason:
            raise ValueError("a Tor user needs a name")
        with self._lock:
            self._users.add(reason)
            if not self.running:
                try:
                    self._launch(timeout=timeout)
                except BaseException:
                    self._users.discard(reason)
                    self._shutdown()
                    raise
            port = self._socks_port
        if wait_for_network:
            # Outside the lock: connecting can take a minute, and the port, onion calls and other
            # users must not wait on it.
            try:
                self._wait_for_bootstrap(timeout)
            except BaseException:
                # This user gives up; Tor keeps running for anyone else still using it.
                self.release(reason)
                raise
        return port

    def release(self, reason: str) -> None:
        """Drop a user. Tor stops when nobody is left."""
        with self._lock:
            self._users.discard(str(reason or "").strip())
            if not self._users:
                self._shutdown()

    def stop(self) -> None:
        with self._lock:
            self._users.clear()
            self._shutdown()

    def _launch(self, *, timeout: float) -> None:
        binary = self._binary or find_tor_binary()
        if not binary:
            raise TorUnavailable("Tor is not installed with this copy of VOOL.")
        data_dir = self._data_dir or default_data_dir()
        data_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(data_dir, 0o700)
        control_file = data_dir / "control-port"
        control_file.unlink(missing_ok=True)
        empty_torrc = data_dir / "vool-torrc"
        empty_torrc.write_text("# VOOL starts Tor with everything on the command line.\n", encoding="utf-8")
        argv = [
            binary,
            "-f", str(empty_torrc),
            "--ignore-missing-torrc",
            "DataDirectory", str(data_dir),
            "SocksPort", "127.0.0.1:auto IsolateSOCKSAuth",
            "ControlPort", "127.0.0.1:auto",
            "ControlPortWriteToFile", str(control_file),
            "CookieAuthentication", "1",
            "CookieAuthFile", str(data_dir / "control-cookie"),
            "__OwningControllerProcess", str(os.getpid()),
            "ClientOnly", "1",
            "AvoidDiskWrites", "1",
            "Log", f"notice file {data_dir / 'tor.log'}",
        ]
        self._process = subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        deadline = time.monotonic() + _CONTROL_FILE_TIMEOUT_SECONDS
        control_port = 0
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                raise TorUnavailable("Tor stopped while starting.")
            control_port = _read_control_port(control_file)
            if control_port:
                break
            time.sleep(0.1)
        if not control_port:
            raise TorUnavailable("Tor did not open its control port.")
        cookie = (data_dir / "control-cookie").read_bytes()
        self._control = _Control(control_port, cookie)
        self._control.command("TAKEOWNERSHIP")
        listeners = self._control.getinfo("net/listeners/socks")
        self._socks_port = _first_loopback_port(listeners)
        if not self._socks_port:
            raise TorUnavailable("Tor did not open its SOCKS port.")

    def bootstrapped(self) -> bool:
        with self._lock:
            if not self.running or self._control is None:
                return False
            return "PROGRESS=100" in self._control.getinfo("status/bootstrap-phase")

    def _wait_for_bootstrap(self, timeout: float) -> None:
        bootstrap_deadline = time.monotonic() + timeout
        while True:
            control, process = self._control, self._process
            if control is None or process is None or process.poll() is not None:
                raise TorUnavailable("Tor stopped while connecting.")
            phase = control.getinfo("status/bootstrap-phase")
            if "PROGRESS=100" in phase:
                return
            if time.monotonic() > bootstrap_deadline:
                raise TorUnavailable("Tor could not connect to the Tor network in time.")
            time.sleep(0.5)

    def _shutdown(self) -> None:
        control, process = self._control, self._process
        self._control, self._process, self._socks_port = None, None, 0
        self._onions.clear()
        if control is not None:
            try:
                control.command("SIGNAL SHUTDOWN")
            except (OSError, TorUnavailable):
                pass
            control.close()
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    # -- onion services -------------------------------------------------------------------

    def add_onion(self, private_key: str, target_port: int, *, virtual_port: int = 80) -> OnionService:
        """Publish an onion address that forwards to 127.0.0.1:target_port.

        Pass "" to make a new key; the returned private_key is then the new one, for the caller
        to store. The service lives while Tor runs; the caller calls `remove_onion` to end it.
        """
        with self._lock:
            if not self.running or self._control is None:
                raise TorUnavailable("Tor is not running.")
            key = str(private_key or "").strip() or "NEW:ED25519-V3"
            if "\r" in key or "\n" in key or " " in key:
                raise ValueError("onion key must be one token")
            code, lines = self._control.command(
                f"ADD_ONION {key} Port={int(virtual_port)},127.0.0.1:{int(target_port)}"
            )
            if code != "250":
                raise TorUnavailable("Tor would not publish the away-from-home address.")
            service_id = ""
            new_key = ""
            for line in lines:
                if line.startswith("ServiceID="):
                    service_id = line.split("=", 1)[1]
                elif line.startswith("PrivateKey="):
                    new_key = line.split("=", 1)[1]
            if not service_id:
                raise TorUnavailable("Tor did not return the away-from-home address.")
            self._onions.add(service_id)
            return OnionService(address=f"{service_id}.onion", private_key=new_key or key)

    def remove_onion(self, address: str) -> None:
        service_id = str(address or "").strip().removesuffix(".onion")
        with self._lock:
            if self._control is None or service_id not in self._onions:
                return
            self._control.command(f"DEL_ONION {service_id}")
            self._onions.discard(service_id)


def _read_control_port(path: Path) -> int:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return 0
    # "PORT=127.0.0.1:9051"
    for line in text.splitlines():
        if line.startswith("PORT="):
            return _first_loopback_port(line[5:])
    return 0


def _first_loopback_port(listeners: str) -> int:
    for item in str(listeners or "").replace('"', " ").split():
        host, _, port = item.rpartition(":")
        if host in {"127.0.0.1", "[::1]"} and port.isdigit():
            return int(port)
    return 0


_SHARED: TorRuntime | None = None
_SHARED_LOCK = threading.Lock()


def shared() -> TorRuntime:
    """The process's one Tor."""
    global _SHARED
    with _SHARED_LOCK:
        if _SHARED is None:
            _SHARED = TorRuntime()
        return _SHARED


__all__ = [
    "BOOTSTRAP_TIMEOUT_SECONDS",
    "OnionService",
    "TorRuntime",
    "TorUnavailable",
    "find_tor_binary",
    "platform_dir",
    "shared",
]


def _debug_state(runtime: TorRuntime) -> dict[str, Any]:  # pragma: no cover - for diagnostics
    return {"running": runtime.running, "socks_port": runtime.socks_port, "users": sorted(runtime.users())}
