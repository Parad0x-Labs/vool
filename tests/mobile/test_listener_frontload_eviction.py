"""A sender that front-loads its body and then drips cannot hold every listener slot against a phone."""
from __future__ import annotations

import socket
import threading
import time
from types import SimpleNamespace

from core.mobile_companion import listener as listener_module

_SIZE = 6 * 1024 * 1024


def _serve():
    manager = SimpleNamespace(running=True, runtime=None, model_name="")
    server = listener_module._Server(("127.0.0.1", 0), listener_module._handler(manager))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _phone(port):
    s = socket.create_connection(("127.0.0.1", port), source_address=("127.0.0.9", 0), timeout=5)
    try:
        s.sendall(b"POST /api/mobile/companion HTTP/1.1\r\nHost: x\r\nContent-Length: 2\r\n\r\n[]")
        return s.recv(64)
    except OSError:
        return b""
    finally:
        s.close()


def test_front_loaded_drippers_from_three_addresses_leave_the_phone_served():
    server = _serve()
    port = server.server_address[1]
    held = []
    stop = threading.Event()
    try:
        for source in ("127.0.0.2", "127.0.0.3", "127.0.0.4"):
            for _ in range(4):
                s = socket.create_connection(("127.0.0.1", port), source_address=(source, 0))
                s.sendall(b"POST /api/mobile/companion HTTP/1.1\r\nHost: x\r\nContent-Length: %d\r\n\r\n" % _SIZE)
                s.sendall(b"x" * (_SIZE - 64))  # a fast burst, then a drip that never finishes
                held.append(s)

        def drip():
            while not stop.wait(4):
                for s in held:
                    try:
                        s.send(b"x")
                    except OSError:
                        pass

        threading.Thread(target=drip, daemon=True).start()
        time.sleep(listener_module._RATE_WINDOW + 1)  # past the recent-progress window
        answers = []
        for _ in range(3):
            answers.append(_phone(port))
            time.sleep(3)
        assert all(answer.startswith(b"HTTP/1.") for answer in answers), answers
    finally:
        stop.set()
        for s in held:
            s.close()
        server.shutdown()
        server.server_close()


def test_a_phone_streaming_a_large_body_is_never_evicted_by_a_newcomer():
    progress = {"source": "a", "started": 0.0, "bytes": 0, "body": True, "recent": [(0.0, 0)]}
    for step in range(1, 11):
        progress["bytes"] = step * 64 * 1024
        progress["recent"].append((step * 0.1, progress["bytes"]))
    assert listener_module._recent_rate(progress, 1.05) >= listener_module._MIN_BODY_RATE
    assert listener_module._recent_rate(progress, 1.0 + listener_module._RATE_WINDOW + 0.5) == 0.0
