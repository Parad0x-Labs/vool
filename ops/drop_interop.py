"""Live cross-implementation check: Python core.drop against the reference JS package.

Both directions: a JS invite accepted by Python and a Python invite accepted by JS,
then several messages each way with DH ratchets and out-of-order delivery. Needs
Node >= 24.7 (``--node``); it is evidence for review, not a CI test.
Usage: python ops/drop_interop.py --node /path/to/node
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.drop.client import DropClient
from core.drop.session import b64, unb64

NOW = 1791000000000


class JsPeer:
    def __init__(self, node, directory):
        self.process = subprocess.Popen([node, str(Path(__file__).with_name("drop_interop_peer.mjs")), directory],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)

    def call(self, op, **args):
        self.process.stdin.write(json.dumps({"op": op, **args}) + "\n")
        self.process.stdin.flush()
        reply = json.loads(self.process.stdout.readline())
        if not reply["ok"]:
            raise RuntimeError(f"js refused {op}: {reply['code']}")
        return reply["out"]

    def close(self):
        self.process.stdin.close()
        self.process.wait(timeout=20)


def text(value: str) -> str:
    return b64(value.encode())


def conversation(py, js, py_starts):
    """Returns the list of checked plaintexts; raises on any mismatch."""
    checked = []
    js_pin, py_pin = js.call("fingerprint")["fingerprint"], py.fingerprint
    if py_starts:
        invite = unb64(js.call("invite", now=NOW, ttl=86400000)["wire"], 1, 110000)
        started = py.start(invite=invite, peer_fingerprint=js_pin, plaintext=b"py hello", now=NOW)
        got = js.call("accept", wire=b64(started["wire"]), peer=py_pin, now=NOW + 1)
        sid = started["session"]
    else:
        invite = py.issue_invite(now=NOW, ttl=86400000)
        started = js.call("start", invite=b64(invite), peer=py_pin, plaintext=text("js hello"), now=NOW)
        got = py.accept(wire=unb64(started["wire"], 1, 110000), peer_fingerprint=js_pin, now=NOW + 1)
        got = {"plaintext": b64(got["plaintext"])}
        sid = started["session"]
    checked.append(unb64(got["plaintext"], 0, 65536).decode())
    t = NOW + 10
    for round_ in range(3):
        # JS sends two, Python receives them out of order.
        a = js.call("send", session=sid, plaintext=text(f"js r{round_} m0"), now=t)
        b = js.call("send", session=sid, plaintext=text(f"js r{round_} m1"), now=t)
        for wire, want in ((b, f"js r{round_} m1"), (a, f"js r{round_} m0")):
            plain = py.receive(wire=unb64(wire["wire"], 1, 110000), now=t)["plaintext"].decode()
            assert plain == want, (plain, want)
            checked.append(plain)
        # Python sends two, JS receives them out of order.
        c = py.send(session=sid, plaintext=f"py r{round_} m0".encode(), now=t)
        d = py.send(session=sid, plaintext=f"py r{round_} m1".encode(), now=t)
        for wire, want in ((d, f"py r{round_} m1"), (c, f"py r{round_} m0")):
            plain = unb64(js.call("receive", wire=b64(wire["wire"]), now=t)["plaintext"], 0, 65536).decode()
            assert plain == want, (plain, want)
            checked.append(plain)
        t += 10
    return checked


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--node", required=True)
    args = parser.parse_args()
    results = {}
    for py_starts in (True, False):
        with tempfile.TemporaryDirectory() as root:
            os.chmod(root, 0o700)
            js = JsPeer(args.node, str(Path(root) / "js"))
            py = DropClient(Path(root) / "py", secrets.token_bytes(32))
            try:
                results["python_initiates" if py_starts else "js_initiates"] = conversation(py, js, py_starts)
            finally:
                py.close()
                js.close()
    print(json.dumps({"ok": True, "checked": {k: len(v) for k, v in results.items()}, "messages": results}, indent=1))


if __name__ == "__main__":
    main()
