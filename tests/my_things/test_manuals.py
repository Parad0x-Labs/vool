from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from core import policy_engine
from core.my_things.manuals import preview
from core.my_things.store import ThingsError
from tests.my_things.test_served import online_daemon
from tests.my_things.test_store import store


def test_manual_preview_uses_real_web_tool_and_save_is_a_separate_confirmation(store):
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass
        def do_GET(self):
            calls.append(self.path)
            data = b"Synthetic manual: replace filter every six months."
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        item = store.save({"name": "Synthetic filter", "model": "SF-3411"})
        url = f"http://127.0.0.1:{server.server_port}/manual.txt"
        with pytest.raises(ThingsError, match="off"):
            preview(item["id"], url, confirmed=True)
        assert calls == []
        policy_engine.set_operator_policy_values({"network.outbound_enabled": True, "system.local_only_mode": False, "system.allow_web_fallback": True})
        with pytest.raises(ThingsError, match="confirm"):
            preview(item["id"], url)
        assert calls == []
        from core.mode_permission_policy import set_active_mode

        session = "my-things-manual:" + item["id"]
        set_active_mode(session, "plan")
        with pytest.raises(ThingsError, match="did not open"):
            preview(item["id"], url, confirmed=True)
        assert calls == []
        set_active_mode(session, "manual")
        result = preview(item["id"], url, confirmed=True)
        assert result["saved"] is False and result["requires_confirmation"] is True
        assert "replace filter" in result["text"] and calls == ["/manual.txt"]
        assert store.get(item["id"])["files"] == []
        from core.my_things.store import file_payload

        saved = store.attach(item["id"], 1, file_payload("manual.txt", result["text"].encode(), "manual"))
        assert saved["files"][0]["kind"] == "manual"
        policy_engine.set_operator_policy_values({"network.outbound_enabled": False, "system.local_only_mode": True, "system.allow_web_fallback": False})
        with pytest.raises(ThingsError, match="off"):
            preview(item["id"], url, confirmed=True)
        assert calls == ["/manual.txt"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_manual_preview_and_explicit_save_on_the_real_served_door(online_daemon):
    from tests.my_things.test_served import call, file
    base, _ = online_daemon
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass
        def do_GET(self):
            requests.append(self.path)
            data = b"Synthetic manual CEDAR-44120: clean the filter."
            content_type = "text/plain"
            if self.path == "/binary":
                data, content_type = b"%PDF-1.7 binary", "application/pdf"
            if self.path == "/partial":
                data = b"x" * 2000001
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        item = call(base, "save", {"fields": {"name": "Synthetic air filter"}, "confirm": True})[1]["thing"]
        url = f"http://127.0.0.1:{server.server_port}"
        assert call(base, "manual", {"id": item["id"], "url": url + "/manual", "confirm": False})[0] == 409
        assert requests == []
        status, result, _ = call(base, "manual", {"id": item["id"], "url": url + "/manual", "confirm": True})
        assert status == 200 and result["saved"] is False and "CEDAR-44120" in result["text"], result
        assert call(base)[1]["things"][0]["files"] == []
        for route in ("/binary", "/partial"):
            status, refused, _ = call(base, "manual", {"id": item["id"], "url": url + route, "confirm": True})
            assert status == 409 and refused["error"] == "manual_format", refused
        saved = call(base, "attach", {"id": item["id"], "revision": 1, "file": file("manual.txt", result["text"], "manual")})[1]["thing"]
        assert len(saved["files"]) == 1 and saved["files"][0]["kind"] == "manual"
        assert call(base, "delete", {"id": item["id"], "revision": saved["revision"], "confirm": True})[0] == 200
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
