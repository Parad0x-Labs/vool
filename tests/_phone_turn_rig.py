"""Rig for person-path phone-turn tests: a served daemon whose chat door a PHONE turn enters.

A real phone is a peer on the LAN, not loopback: `core/web/api/mobile_companion_api.py:982-983`
sends `source_context={"surface": "mobile_companion"}` + `platform="mobile_companion"` to the REAL
`/api/chat` door with the phone's TCP peer as client_host, and the server stamps the turn
`_owner_local: False` (service.py:7887-7896) — which is what makes file/note/reminder asks get the
plain computer-only refusal instead of running. The stock `tests/_reader_served_rig.ServedDaemon`
binds 127.0.0.1 only, so every request would arrive owner-local and never reproduce the phone stamp.
This subclass binds 0.0.0.0 (as `tests/_owner_network_rig.NetworkDaemon` does for the same reason)
and sends the phone's exact body from the machine's LAN address.

The scripted model is the reader rig's `CapturingProvider` (answers the certification probe itself,
then serves canned prose); the daemon runs the full production agent runtime, so whatever the phone
person reads back was produced by the real pipeline, not by the fixture.
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

from tests._reader_served_rig import REPO_ROOT, ServedDaemon, urlopen


def lan_ip() -> str:
    """This machine's LAN address, without psutil: pick a route, read the socket name."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 1))  # no packet is sent; the kernel just picks a route
        return str(probe.getsockname()[0])
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


class PhoneDaemon(ServedDaemon):
    """`ServedDaemon` reachable from the LAN, so a turn can arrive the way a phone's does."""

    def __init__(self, home: Path, **kwargs: Any) -> None:
        super().__init__(home, **kwargs)
        self.lan = lan_ip()

    def start(self, *, timeout: float = 240.0) -> "PhoneDaemon":
        # Same boot as ServedDaemon.start() with one difference: --bind 0.0.0.0, so the test
        # client can connect as a LAN peer. Copied rather than parametrised upstream because the
        # rig is shared by drives that must stay loopback-only.
        self.home.mkdir(parents=True, exist_ok=True)
        self.register_provider()
        handle = self.log_path.open("wb")
        self.process = subprocess.Popen(
            [sys.executable, "-m", "apps.vool_api_server", "--port", str(self.port), "--bind", "0.0.0.0"],
            cwd=str(REPO_ROOT),
            env=self.env(),
            stdout=handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"daemon exited {self.process.returncode}\n{self.log_tail()}")
            try:
                with urlopen(f"{self.base_url}/healthz", timeout=3) as response:
                    if response.status == 200:
                        return self
            except Exception:
                time.sleep(1.0)
        raise TimeoutError(f"daemon never became healthy\n{self.log_tail()}")

    def post_chat(self, body: dict[str, Any], *, remote: bool, timeout: float = 300.0) -> dict[str, Any]:
        """POST /api/chat from the LAN peer (remote=True) or from loopback (the PC's own page)."""
        host = self.lan if remote else "127.0.0.1"
        headers = {"Host": f"127.0.0.1:{self.port}", "Content-Type": "application/json"}
        request = urllib.request.Request(
            f"http://{host}:{self.port}/api/chat",
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def phone_turn(self, text: str, *, session_id: str = "", timeout: float = 300.0) -> dict[str, Any]:
        """One turn with exactly the body `_action_chats_send` builds for a phone chats.send."""
        body: dict[str, Any] = {
            "messages": [{"role": "user", "content": text}],
            "stream": False,
            "model": self.model,
            "mode": "auto",
            "source_context": {"surface": "mobile_companion"},
            "platform": "mobile_companion",
        }
        if session_id:
            body["session_id"] = session_id
        return self.post_chat(body, remote=True, timeout=timeout)

    def pc_turn(self, text: str, *, session_id: str = "", timeout: float = 300.0) -> dict[str, Any]:
        """The same ask the served chat page makes: no surface keys, owner-local loopback peer."""
        body: dict[str, Any] = {
            "messages": [{"role": "user", "content": text}],
            "stream": False,
            "model": self.model,
            "mode": "auto",
        }
        if session_id:
            body["session_id"] = session_id
        return self.post_chat(body, remote=False, timeout=timeout)


def reply_text(payload: dict[str, Any]) -> str:
    """The text the person reads: message.content (fallback response), as the phone door reads it."""
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    return str(message.get("content") or payload.get("response") or "")
