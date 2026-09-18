from __future__ import annotations

import time

from core.meet_and_greet_service import MeetAndGreetService
from core.web.meet.routes import dispatch_request, resolve_static_route
from core.web0_mesh_registry import announce_worker, evict_expired, get_worker, list_workers

# ---------------------------------------------------------------------------
# core/web0_mesh_registry  unit tests
# ---------------------------------------------------------------------------

def test_announce_returns_ok_and_worker_id() -> None:
    result = announce_worker({"worker_id": "unit-1", "top_tps": 10.0})
    assert result["ok"] is True
    assert result["worker_id"] == "unit-1"
    assert result["ttl_seconds"] > 0


def test_announce_missing_worker_id_returns_error() -> None:
    result = announce_worker({"top_tps": 10.0})
    assert result["ok"] is False
    assert "worker_id" in result.get("error", "")


def test_list_workers_returns_announced_worker() -> None:
    announce_worker({"worker_id": "unit-list-1", "top_tps": 5.0})
    workers = list_workers()
    ids = [w["worker_id"] for w in workers]
    assert "unit-list-1" in ids


def test_list_workers_sorted_by_tps_descending() -> None:
    announce_worker({"worker_id": "slow-worker", "top_tps": 1.0})
    announce_worker({"worker_id": "fast-worker", "top_tps": 999.0})
    workers = list_workers()
    tps_values = [w["top_tps"] for w in workers]
    assert tps_values == sorted(tps_values, reverse=True)


def test_get_worker_returns_entry() -> None:
    announce_worker({"worker_id": "unit-get-1", "top_tier": "queen", "top_tps": 20.0})
    entry = get_worker("unit-get-1")
    assert entry is not None
    assert entry["top_tier"] == "queen"
    assert entry["active"] is True


def test_get_worker_missing_returns_none() -> None:
    assert get_worker("does-not-exist-xyz") is None


def test_evict_expired_removes_stale_entries() -> None:

    from storage.db import get_connection

    worker_id = "stale-unit-worker"
    announce_worker({"worker_id": worker_id})
    # Backdate expiry directly in SQLite
    conn = get_connection()
    conn.execute(
        "UPDATE web0_workers SET expires_at = ? WHERE worker_id = ?",
        (time.time() - 1, worker_id),
    )
    conn.commit()

    count = evict_expired()
    assert count >= 1
    assert get_worker(worker_id) is None


def test_active_flag_false_after_expiry() -> None:

    from storage.db import get_connection

    announce_worker({"worker_id": "flag-test"})
    conn = get_connection()
    conn.execute(
        "UPDATE web0_workers SET expires_at = ? WHERE worker_id = ?",
        (time.time() - 1, "flag-test"),
    )
    conn.commit()

    entry = get_worker("flag-test")
    assert entry is not None
    assert entry["active"] is False


# ---------------------------------------------------------------------------
# meet/routes.py  integration tests
# ---------------------------------------------------------------------------

def _svc() -> MeetAndGreetService:
    return MeetAndGreetService()


def test_null_browser_static_route_returns_html() -> None:
    result = resolve_static_route("/null-browser")
    assert result is not None
    status, content_type, body = result
    assert status == 200
    assert "text/html" in content_type
    assert b"null://" in body
    assert b".null browser" in body


def test_null_browser_trailing_slash() -> None:
    result = resolve_static_route("/null-browser/")
    assert result is not None
    assert result[0] == 200


def test_get_workers_returns_empty_list_initially() -> None:
    # Use a separate evict to not interfere with other tests
    status, data = dispatch_request("GET", "/v1/workers", {}, {}, _svc())
    assert status == 200
    assert "workers" in data["result"]


def test_post_workers_announce_registers_worker() -> None:
    status, data = dispatch_request(
        "POST",
        "/v1/workers/announce",
        {},
        {"worker_id": "route-test-1", "top_tps": 50.0, "top_tier": "queen"},
        _svc(),
    )
    assert status == 200
    assert data["result"]["ok"] is True
    assert data["result"]["worker_id"] == "route-test-1"


def test_post_workers_announce_missing_id_returns_422() -> None:
    status, _ = dispatch_request("POST", "/v1/workers/announce", {}, {"top_tps": 10.0}, _svc())
    assert status == 422


def test_get_workers_lists_announced_worker() -> None:
    announce_worker({"worker_id": "routes-list-test", "top_tps": 7.0})
    status, data = dispatch_request("GET", "/v1/workers", {}, {}, _svc())
    assert status == 200
    ids = [w["worker_id"] for w in data["result"]["workers"]]
    assert "routes-list-test" in ids


def test_get_worker_by_id_route() -> None:
    announce_worker({"worker_id": "routes-get-test", "top_tps": 15.0})
    status, data = dispatch_request("GET", "/v1/workers/routes-get-test", {}, {}, _svc())
    assert status == 200
    assert data["result"]["worker_id"] == "routes-get-test"


def test_get_worker_by_id_not_found() -> None:
    status, _ = dispatch_request("GET", "/v1/workers/xyz-no-such", {}, {}, _svc())
    assert status == 404


def test_get_workers_active_only_param() -> None:
    announce_worker({"worker_id": "active-param-test"})
    status, _data = dispatch_request(
        "GET", "/v1/workers", {"active_only": ["false"]}, {}, _svc()
    )
    assert status == 200
