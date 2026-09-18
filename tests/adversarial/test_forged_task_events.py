from __future__ import annotations

import http.client
import importlib
import json
import uuid
from threading import Thread

import pytest

import apps.meet_and_greet_server as _server_mod
import core.api_write_auth as _api_write_auth_mod
from core.meet_and_greet_service import MeetAndGreetConfig, MeetAndGreetService
from storage.db import get_connection, reset_default_connection
from storage.migrations import run_migrations


def _clear_tables() -> None:
    reset_default_connection()
    conn = get_connection()
    try:
        for table in ("hive_topics", "hive_posts", "nonce_cache", "agent_names", "peers"):
            try:
                conn.execute(f"DELETE FROM {table}")
            except Exception:
                continue
        conn.commit()
    finally:
        conn.close()
        reset_default_connection()


def test_forged_signed_topic_update_actor_is_rejected() -> None:
    importlib.reload(_api_write_auth_mod)
    importlib.reload(_server_mod)
    run_migrations()
    _clear_tables()
    service = MeetAndGreetService(MeetAndGreetConfig(local_region="eu"))
    create_code, created = _server_mod.dispatch_request(
        "POST",
        "/v1/hive/topics",
        {},
        {
            "created_by_agent_id": "peer-owner-000000000000000000000000000000000000000000000000000000000000",
            "title": "Spoof guard topic",
            "summary": "Seed topic for spoofed update rejection.",
            "topic_tags": ["security"],
            "status": "open",
            "visibility": "agent_public",
            "evidence_mode": "candidate_only",
        },
        service,
    )
    assert create_code == 200
    topic_id = created["result"]["topic_id"]
    try:
        server = _server_mod.build_server(
            _server_mod.MeetAndGreetServerConfig(host="127.0.0.1", port=0, require_signed_writes=True),
            service=service,
        )
    except PermissionError:
        pytest.skip("Local socket binds are not permitted in this sandbox.")
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        spoofed_actor_id = f"peer-{uuid.uuid4().hex}{uuid.uuid4().hex}"
        signed_payload = _api_write_auth_mod.build_signed_write_envelope(
            target_path="/v1/hive/topic-update",
            payload={
                "topic_id": topic_id,
                "updated_by_agent_id": spoofed_actor_id,
                "summary": "Spoofed update attempt.",
            },
        )
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request(
            "POST",
            "/v1/hive/topic-update",
            body=json.dumps(signed_payload),
            headers={"Content-Type": "application/json"},
        )
        response = conn.getresponse()
        body = json.loads(response.read().decode("utf-8"))
        assert response.status in {400, 403}
        assert body.get("ok") is False
        assert str(body.get("error") or "").strip()
        topic_code, topic_body = _server_mod.dispatch_request(
            "GET",
            f"/v1/hive/topics/{topic_id}",
            {},
            None,
            service,
        )
        assert topic_code == 200
        assert topic_body["result"]["summary"] == "Seed topic for spoofed update rejection."
        conn.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
