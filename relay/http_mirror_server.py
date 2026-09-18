from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

MAX_BODY_BYTES = 256 * 1024  # 256 KB


@dataclass
class MirrorServerConfig:
    host: str = "127.0.0.1"
    port: int = 8787
    data_dir: str = "./relay_mirror"


class FileMirrorStore:
    def __init__(self, base_dir: str) -> None:
        self.base_dir = Path(base_dir).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _topic_path(self, topic_name: str) -> Path:
        safe = "".join(ch for ch in topic_name if ch.isalnum() or ch in {"_", "-"}).strip() or "topic"
        return self.base_dir / f"{safe}.json"

    def put(self, topic_name: str, snapshot: dict[str, Any]) -> None:
        with self._lock:
            path = self._topic_path(topic_name)
            path.write_text(json.dumps(snapshot, sort_keys=True, indent=2), encoding="utf-8")

    def publish_snapshot(self, topic_name: str, snapshot: dict[str, Any]) -> tuple[bool, str]:
        """A8 pass-002 durable publish boundary (T04): eligibility is
        revalidated INSIDE the coordination lock shared with the ERASE
        traversal (core.finalization.A8_TRAVERSAL_LOCK) immediately before
        the durable write — an erase that wins canonically cannot be undone
        by a slow in-flight publisher. Returns (ok, reason)."""
        from core.finalization import A8_TRAVERSAL_LOCK

        records = [r for r in (snapshot.get("records") or []) if isinstance(r, dict)]
        with A8_TRAVERSAL_LOCK:
            if not _publish_records_eligible(records):
                return False, "payload_unavailable_by_policy"
            self.put(topic_name, snapshot)
        return True, ""

    def delete(self, topic_name: str) -> bool:
        """A8 local-snapshot tombstone: remove one local mirror snapshot."""
        with self._lock:
            path = self._topic_path(topic_name)
            if not path.exists():
                return False
            path.unlink(missing_ok=True)
            return True

    def get(self, topic_name: str) -> dict[str, Any] | None:
        with self._lock:
            path = self._topic_path(topic_name)
            if not path.exists():
                return None
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                return None

    def list_topics(self) -> list[str]:
        with self._lock:
            return sorted(p.stem for p in self.base_dir.glob("*.json"))


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _basic_snapshot_shape_ok(snapshot: dict[str, Any]) -> bool:
    required = {
        "topic_name",
        "publisher_peer_id",
        "published_at",
        "expires_at",
        "record_count",
        "records",
        "snapshot_hash",
        "signature",
    }
    if not isinstance(snapshot, dict):
        return False
    if not required.issubset(set(snapshot.keys())):
        return False
    if not isinstance(snapshot.get("records"), list):
        return False
    return isinstance(snapshot.get("record_count"), int)


def _publish_records_eligible(records: list[dict[str, Any]]) -> bool:
    """A8 canonical availability check for snapshot records about to be
    durably written to (or served from) this relay. WITHHELD/ERASED governed
    bytes are refused; store failure FAILS CLOSED (ineligible)."""
    from core.finalization import payload_availability_for_hash, _sha256_hex

    try:
        for record in records:
            content = str((record or {}).get("content") or "")
            if not content.strip():
                continue
            verdict = payload_availability_for_hash(_sha256_hex(content))
            if verdict is not None and verdict != "AVAILABLE":
                return False
    except Exception:
        # Uncertainty never resolves into propagation of governed bytes.
        return False
    return True


def build_handler(store: FileMirrorStore):
    class MirrorHandler(BaseHTTPRequestHandler):
        server_version = "VoolMirrorHTTP/0.1"

        def _write_json(self, code: int, payload: dict[str, Any]) -> None:
            data = json.dumps(payload, sort_keys=True).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _read_json_body(self) -> dict[str, Any] | None:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return None

            if length <= 0 or length > MAX_BODY_BYTES:
                return None

            raw = self.rfile.read(length)
            try:
                return json.loads(raw.decode("utf-8"))
            except Exception:
                return None

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/")

            if path == "/healthz":
                self._write_json(
                    200,
                    {
                        "ok": True,
                        "time": _utcnow(),
                        "topics": store.list_topics(),
                    },
                )
                return

            if path == "/topics":
                self._write_json(
                    200,
                    {
                        "ok": True,
                        "topics": store.list_topics(),
                    },
                )
                return

            if path.startswith("/topics/"):
                topic_name = unquote(path[len("/topics/"):]).strip()
                if not topic_name:
                    self._write_json(400, {"ok": False, "error": "missing_topic"})
                    return

                snapshot = store.get(topic_name)
                if not snapshot:
                    self._write_json(404, {"ok": False, "error": "not_found"})
                    return
                # A8 pass-002 serve gate (T02/T03): eligibility is checked at
                # SERVE time, not only at write time; a WITHHELD/ERASED
                # record (or unreadable store) is never disclosed. Removal of
                # every record reduces to an honest 404.
                records = [r for r in (snapshot.get("records") or []) if isinstance(r, dict)]
                eligible = [
                    r
                    for r in records
                    if _publish_records_eligible([r])
                ]
                if len(eligible) != len(records):
                    if not eligible:
                        self._write_json(404, {"ok": False, "error": "not_found"})
                        return
                    snapshot = dict(snapshot)
                    snapshot["records"] = eligible
                    snapshot["record_count"] = len(eligible)
                self._write_json(200, snapshot)
                return

            self._write_json(404, {"ok": False, "error": "not_found"})

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/")

            if path.startswith("/publish/"):
                topic_name = unquote(path[len("/publish/"):]).strip()
                if not topic_name:
                    self._write_json(400, {"ok": False, "error": "missing_topic"})
                    return

                snapshot = self._read_json_body()
                if not snapshot:
                    self._write_json(400, {"ok": False, "error": "invalid_json_or_body"})
                    return

                if not _basic_snapshot_shape_ok(snapshot):
                    self._write_json(400, {"ok": False, "error": "invalid_snapshot_shape"})
                    return

                # Relay does not verify publisher signatures; nodes do that themselves.
                # Relay is intentionally dumb transport — EXCEPT for the A8
                # privacy fence: the durable publish boundary revalidates
                # eligibility under the traversal lock.
                ok, reason = store.publish_snapshot(topic_name, snapshot)
                if not ok:
                    self._write_json(409, {"ok": False, "error": reason})
                    return
                self._write_json(
                    200,
                    {
                        "ok": True,
                        "topic": topic_name,
                        "stored_at": _utcnow(),
                    },
                )
                return

            self._write_json(404, {"ok": False, "error": "not_found"})

        def do_DELETE(self) -> None:
            # A8 erasure traversal support: DELETE removes one LOCAL snapshot
            # (a governed local derivative). Off-host mirrors are not touched —
            # third-party retention stays honestly UNKNOWN.
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/")

            if path.startswith("/topics/"):
                topic_name = unquote(path[len("/topics/"):]).strip()
                if not topic_name:
                    self._write_json(400, {"ok": False, "error": "missing_topic"})
                    return
                removed = store.delete(topic_name)
                self._write_json(
                    200 if removed else 404,
                    {"ok": removed, "topic": topic_name, "deleted": removed},
                )
                return

            self._write_json(404, {"ok": False, "error": "not_found"})

        def log_message(self, format: str, *args: Any) -> None:
            # Keep server quiet by default
            return

    return MirrorHandler


class HttpMirrorServer:
    def __init__(self, config: MirrorServerConfig | None = None) -> None:
        self.config = config or MirrorServerConfig()
        self.store = FileMirrorStore(self.config.data_dir)
        self.httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        handler = build_handler(self.store)
        self.httpd = ThreadingHTTPServer((self.config.host, self.config.port), handler)

        self._thread = threading.Thread(
            target=self.httpd.serve_forever,
            name="vool-http-mirror",
            daemon=False,
        )
        self._thread.start()

    def stop(self) -> None:
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def url(self) -> str:
        return f"http://{self.config.host}:{self.config.port}"


def main() -> None:
    server = HttpMirrorServer()
    server.start()
    print(f"Vool HTTP mirror running at {server.url()}")
    try:
        while True:
            threading.Event().wait(3600)
    except KeyboardInterrupt:
        server.stop()


if __name__ == "__main__":
    main()
