from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from core import policy_engine
from core.my_things.manuals import preview
from core.my_things.store import ThingsError
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
