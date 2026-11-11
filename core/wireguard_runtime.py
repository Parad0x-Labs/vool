"""Private web through the person's own VPN: a WireGuard file, run in user space for VOOL only.

Almost every VPN provider (Mullvad, Proton VPN, IVPN, NordVPN, Surfshark, Windscribe, ...) lets
the person download a WireGuard configuration file. VOOL keeps that file, owner-only, and runs a
user-space WireGuard client (wireproxy) that exposes the tunnel as a SOCKS port on 127.0.0.1.

* Only VOOL's traffic uses the tunnel. The computer's own network is not changed, and no admin
  password is needed.
* Site names are looked up inside the tunnel, by the DNS server the file names.
* The tunnel counts as up only after a real connection through it (to that DNS server) works;
  `core.private_web` refuses to send anything while it is not.
"""

from __future__ import annotations

import base64
import binascii
import configparser
import ipaddress
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

START_TIMEOUT_SECONDS = 25.0
_HEALTH_TTL_SECONDS = 5.0
_HEALTH_TIMEOUT_SECONDS = 4.0


class WireGuardUnavailable(RuntimeError):
    """The tunnel could not be set up or is not working. The message is plain."""


class WireGuardFileProblem(ValueError):
    """The WireGuard file cannot be used. The message says what to fix, in plain words."""


@dataclass(frozen=True)
class WireGuardProfile:
    dns: tuple[str, ...]
    endpoint: str
    addresses: tuple[str, ...]


def _key_ok(value: str) -> bool:
    try:
        return len(base64.b64decode(value.strip(), validate=True)) == 32
    except (binascii.Error, ValueError):
        return False


def parse_wireguard_file(text: str) -> WireGuardProfile:
    """Check a provider's WireGuard file before VOOL keeps it."""
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str  # keep key case
    try:
        parser.read_string(str(text or ""))
    except configparser.Error:
        raise WireGuardFileProblem("This does not look like a WireGuard file.") from None
    if not parser.has_section("Interface") or not parser.has_section("Peer"):
        raise WireGuardFileProblem("This does not look like a WireGuard file: it needs [Interface] and [Peer].")
    interface, peer = parser["Interface"], parser["Peer"]
    if not _key_ok(interface.get("PrivateKey", "")):
        raise WireGuardFileProblem("The file has no valid PrivateKey line.")
    if not _key_ok(peer.get("PublicKey", "")):
        raise WireGuardFileProblem("The file has no valid PublicKey line for the VPN server.")
    endpoint = peer.get("Endpoint", "").strip()
    if not endpoint or ":" not in endpoint:
        raise WireGuardFileProblem("The file has no Endpoint line (the VPN server's address).")
    addresses = tuple(a.strip() for a in interface.get("Address", "").split(",") if a.strip())
    if not addresses:
        raise WireGuardFileProblem("The file has no Address line.")
    dns: list[str] = []
    for item in interface.get("DNS", "").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            dns.append(str(ipaddress.ip_address(item)))
        except ValueError:
            continue  # search domains are allowed in the DNS line; they are not servers
    if not dns:
        raise WireGuardFileProblem(
            "The file has no DNS line. Download it again from your VPN with DNS included, so site "
            "names are looked up inside the VPN and not on your own network."
        )
    return WireGuardProfile(dns=tuple(dns), endpoint=endpoint, addresses=addresses)


def _private_dir() -> Path:
    from core.runtime_paths import data_path

    folder = data_path("private-web")
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    return folder


def stored_file_path() -> Path:
    return _private_dir() / "wireguard.conf"


def save_wireguard_file(text: str) -> WireGuardProfile:
    """Check and keep the person's WireGuard file, readable by this user only."""
    profile = parse_wireguard_file(text)
    target = stored_file_path()
    tmp = target.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(str(text))
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, target)
    os.chmod(target, 0o600)
    return profile


def forget_wireguard_file() -> None:
    stored_file_path().unlink(missing_ok=True)


def find_wireproxy_binary(env: dict[str, str] | None = None) -> str | None:
    environ = os.environ if env is None else env
    pinned = str(environ.get("VOOL_WIREPROXY_BINARY") or "").strip()
    if pinned:
        return pinned if os.access(pinned, os.X_OK) else None
    from core.runtime_paths import project_path
    from core.tor_runtime import platform_dir

    name = "wireproxy.exe" if sys.platform == "win32" else "wireproxy"
    bundled = project_path("third_party", "wireproxy", platform_dir(), name)
    if bundled.is_file() and os.access(bundled, os.X_OK):
        return str(bundled)
    return shutil.which("wireproxy")


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class WireGuardRuntime:
    def __init__(self, *, config_path: Path | None = None, binary: str | None = None) -> None:
        self._config_path = Path(config_path) if config_path is not None else None
        self._binary = binary
        self._lock = threading.RLock()
        self._process: subprocess.Popen[bytes] | None = None
        self._socks_port = 0
        self._profile: WireGuardProfile | None = None
        self._healthy_until = 0.0

    @property
    def running(self) -> bool:
        with self._lock:
            return self._process is not None and self._process.poll() is None and self._socks_port > 0

    @property
    def socks_port(self) -> int:
        with self._lock:
            return self._socks_port if self.running else 0

    def start(self, *, timeout: float = START_TIMEOUT_SECONDS) -> int:
        with self._lock:
            if self.running:
                return self._socks_port
            config_path = self._config_path or stored_file_path()
            try:
                text = config_path.read_text(encoding="utf-8")
            except OSError:
                raise WireGuardUnavailable("No VPN file has been added yet.") from None
            self._profile = parse_wireguard_file(text)
            binary = self._binary or find_wireproxy_binary()
            if not binary:
                raise WireGuardUnavailable("The VPN helper is not installed with this copy of VOOL.")
            port = _free_loopback_port()
            run_config = _private_dir() / "wireproxy.conf" if self._config_path is None else config_path.with_name(config_path.name + ".run")
            fd = os.open(run_config, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(f"WGConfig = {config_path}\n\n[Socks5]\nBindAddress = 127.0.0.1:{port}\n")
            self._process = subprocess.Popen(
                [binary, "-s", "-c", str(run_config)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            self._socks_port = port
        deadline = time.monotonic() + timeout
        from core import private_web

        while time.monotonic() < deadline:
            if not self.running:
                self.stop()
                raise WireGuardUnavailable("The VPN helper stopped while starting. Check the VPN file.")
            if private_web.probe_socks(("127.0.0.1", port), timeout=1.0) and self.tunnel_alive(force=True):
                return port
            time.sleep(0.3)
        self.stop()
        raise WireGuardUnavailable("The VPN did not answer. Check that the file is current and your VPN account is active.")

    def tunnel_alive(self, *, force: bool = False) -> bool:
        """True when a connection through the tunnel to the file's DNS server works."""
        with self._lock:
            if not self.running or self._profile is None:
                return False
            if not force and time.monotonic() < self._healthy_until:
                return True
            port, servers = self._socks_port, self._profile.dns
        from core import private_web

        for server in servers:
            try:
                sock = private_web.socks5_connect(("127.0.0.1", port), server, 53, timeout=_HEALTH_TIMEOUT_SECONDS)
            except OSError:
                continue
            sock.close()
            with self._lock:
                self._healthy_until = time.monotonic() + _HEALTH_TTL_SECONDS
            return True
        with self._lock:
            self._healthy_until = 0.0
        return False

    def stop(self) -> None:
        with self._lock:
            process, self._process, self._socks_port, self._healthy_until = self._process, None, 0, 0.0
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


_SHARED: WireGuardRuntime | None = None
_SHARED_LOCK = threading.Lock()


def shared() -> WireGuardRuntime:
    global _SHARED
    with _SHARED_LOCK:
        if _SHARED is None:
            _SHARED = WireGuardRuntime()
        return _SHARED


__all__ = [
    "WireGuardFileProblem",
    "WireGuardProfile",
    "WireGuardRuntime",
    "WireGuardUnavailable",
    "find_wireproxy_binary",
    "forget_wireguard_file",
    "parse_wireguard_file",
    "save_wireguard_file",
    "shared",
    "stored_file_path",
]
