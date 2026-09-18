"""Model Radar production wiring — HTTP routes, refresh seam, observer, LKG behaviour.

Drives the real dispatch_get/dispatch_post seam with the real service underneath
(an isolated store), the same way test_model_market_feed drives its routes.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from core.model_radar import ModelObservation, PriceComponents


@pytest.fixture()
def radar_store(tmp_path, monkeypatch):
    db_file = tmp_path / "radar.db"

    def _fresh_connection():
        conn = sqlite3.connect(str(db_file))
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr("storage.model_radar.get_connection", _fresh_connection)
    # Never let a route test start the hourly network observer.
    monkeypatch.setenv("VOOL_MODEL_RADAR_POLLER", "0")
    yield db_file


# These tests drive the PRODUCTION seams (routes, force_catalog_refresh, the
# observer), whose freshness math runs on the real clock — so the mock evidence
# is stamped now, exactly like a real fetch would be.
_NOW = datetime.now(timezone.utc).isoformat()


def obs(pin: float | None, pout: float | None, **kwargs) -> ModelObservation:
    base = dict(
        provider_id="openrouter",
        model_id="vendor/alpha",
        display_name="Alpha",
        prices=PriceComponents(pin, pout, None),
        offer_kind="permanent",
        evidence_fetched_at=_NOW,
        source_feed="test-feed",
    )
    base.update(kwargs)
    return ModelObservation(**base)


def _get(path, host, query=None):
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get

    res = dispatch_get(
        path=path, query=query or {}, runtime=RuntimeServices(display_name="N"),
        model_name="vool", client_host=host,
    )
    return res.status, json.loads(res.body.decode("utf-8"))


def _post(path, body, host="127.0.0.1", headers=None):
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_post

    res = dispatch_post(
        path=path, body=body, headers=headers or {"content-type": "application/json"},
        runtime=RuntimeServices(display_name="N"), model_name="vool",
        workspace_root_provider=lambda: "/tmp", client_host=host,
    )
    return res.status, json.loads(res.body.decode("utf-8"))


@pytest.fixture()
def qualified_finding(radar_store):
    from core.model_radar_service import observe_feed, unread_findings

    observe_feed("openrouter", (obs(2.0, 8.0),), now=_NOW)
    observe_feed("openrouter", (obs(1.0, 4.0),), now=_NOW)
    (finding,) = unread_findings()
    return finding


# ---- owner-local gate -------------------------------------------------------------------


def test_radar_routes_are_owner_local(radar_store) -> None:
    for path in ("/api/model-radar/feed", "/api/model-radar/preferences"):
        status, payload = _get(path, "203.0.113.9")
        assert status == 403 and payload.get("error") == "owner_local_required", path
    for path, body in (
        ("/api/model-radar/dismiss", {"fingerprint": "x"}),
        ("/api/model-radar/viewed", {"fingerprints": ["x"]}),
        ("/api/model-radar/preferences", {"lane": "any"}),
        ("/api/model-radar/try-once", {"fingerprint": "x", "session_id": "s"}),
    ):
        status, payload = _post(path, body, host="203.0.113.9")
        assert status == 403 and payload.get("error") == "owner_local_required", path


def test_radar_post_rejects_cross_origin_and_wrong_shape(radar_store, qualified_finding) -> None:
    status, payload = _post(
        "/api/model-radar/dismiss", {"fingerprint": qualified_finding.fingerprint},
        headers={"content-type": "application/json", "origin": "https://evil.example"},
    )
    assert status == 403 and payload["error"] == "cross-origin request not allowed"
    status, payload = _post("/api/model-radar/dismiss", {"fingerprint": "x", "extra": 1})
    assert status == 400 and "unknown fields" in payload["error"]
    status, payload = _post("/api/model-radar/dismiss", {})
    assert status == 400


# ---- the feed read surface ----------------------------------------------------------------


def test_feed_returns_findings_and_prefs(radar_store, qualified_finding) -> None:
    status, payload = _get("/api/model-radar/feed", "127.0.0.1")
    assert status == 200 and payload["ok"] is True
    assert payload["unread"] == 1
    (row,) = payload["findings"]
    assert row["fingerprint"] == qualified_finding.fingerprint
    assert row["kind"] == "price_drop"
    assert row["reductions"]["input_usd_per_m"] == 0.5
    assert row["why"].startswith("The same provider cut")
    assert payload["preferences"]["lane"] == "any"


def test_feed_degrades_to_empty_on_storage_trouble_never_invents(radar_store, qualified_finding, monkeypatch) -> None:
    def _broken(*args, **kwargs):
        raise RuntimeError("store gone")

    monkeypatch.setattr("core.model_radar_service.list_findings", _broken)
    status, payload = _get("/api/model-radar/feed", "127.0.0.1")
    assert status == 200 and payload["ok"] is True and payload["findings"] == [] and payload.get("degraded") is True


def test_viewed_marks_unread_down(radar_store, qualified_finding) -> None:
    status, payload = _post("/api/model-radar/viewed", {"fingerprints": [qualified_finding.fingerprint]})
    assert status == 200 and payload["marked"] == 1
    _status, feed = _get("/api/model-radar/feed", "127.0.0.1")
    assert feed["unread"] == 0
    assert any(f["fingerprint"] == qualified_finding.fingerprint for f in feed["findings"]), "read findings stay listed"


def test_dismiss_via_route_is_permanent(radar_store, qualified_finding) -> None:
    status, payload = _post("/api/model-radar/dismiss", {"fingerprint": qualified_finding.fingerprint})
    assert status == 200 and payload["ok"] is True
    _status, feed = _get("/api/model-radar/feed", "127.0.0.1")
    assert feed["unread"] == 0 and feed["findings"] == [], "dismissed findings leave the surface entirely"
    status, payload = _post("/api/model-radar/dismiss", {"fingerprint": "does-not-exist"})
    assert status == 404


# ---- preferences ----------------------------------------------------------------------------


def test_preferences_round_trip_via_route(radar_store) -> None:
    status, payload = _post(
        "/api/model-radar/preferences",
        {"enabled": True, "providers": ["openrouter"], "lane": "cloud", "required_capabilities": ["tools"], "min_interval_hours": 24},
    )
    assert status == 200 and payload["ok"] is True
    assert payload["preferences"]["providers"] == ["openrouter"]
    assert payload["preferences"]["required_capabilities"] == ["tools"]
    assert payload["preferences"]["min_interval_hours"] == 24
    _status, read = _get("/api/model-radar/preferences", "127.0.0.1")
    assert read["preferences"] == payload["preferences"]
    status, payload = _post("/api/model-radar/preferences", {"bogus": 1})
    assert status == 400


# ---- try-once --------------------------------------------------------------------------------


def test_try_once_validates_and_receipts_without_pinning(radar_store, qualified_finding) -> None:
    from core import cloud_escalation_policy as cep

    policy_before = cep.load_policy()
    status, payload = _post(
        "/api/model-radar/try-once", {"fingerprint": qualified_finding.fingerprint, "session_id": "openclaw:abc"}
    )
    assert status == 200 and payload["ok"] is True
    assert payload["model_id"] == "vendor/alpha" and payload["token"].startswith("mrtry_")
    # No auto-switching: the selection authority is untouched by a trial grant.
    assert cep.load_policy() == policy_before
    status, payload = _post("/api/model-radar/try-once", {"fingerprint": "nope", "session_id": "s"})
    assert status == 409


def test_try_once_refuses_when_the_model_left_the_catalog(radar_store, qualified_finding) -> None:
    from storage import model_radar as radar_storage

    conn = radar_storage.get_connection()
    conn.execute("DELETE FROM model_radar_observations")
    conn.commit()
    conn.close()
    status, payload = _post(
        "/api/model-radar/try-once", {"fingerprint": qualified_finding.fingerprint, "session_id": "s"}
    )
    assert status == 409 and "no longer present" in payload["error"]


# ---- production refresh seam -------------------------------------------------------------------


def _openrouter_stub(prices: tuple[float, float]):
    return SimpleNamespace(
        model_id="vendor/alpha",
        name="Alpha",
        context_length=131072,
        prompt_usd_per_token=prices[0] / 1_000_000,
        completion_usd_per_token=prices[1] / 1_000_000,
        request_usd=None,
        supported_parameters=("tools",),
        input_modalities=("text",),
        output_modalities=("text",),
        fetched_at=_NOW,
        max_output_tokens=0,
    )


def test_force_catalog_refresh_feeds_the_radar(radar_store, monkeypatch) -> None:
    from core import cloud_model_control
    from storage import model_radar as radar_storage

    monkeypatch.setattr(
        "core.openrouter_catalog.refresh_openrouter_catalog",
        lambda *a, **k: (_openrouter_stub((2.0, 8.0)),),
    )
    result = cloud_model_control.force_catalog_refresh()
    assert result["ok"] is True
    # The paid baseline was recorded through the real production seam.
    assert radar_storage.get_observation("openrouter", "vendor/alpha") is not None

    monkeypatch.setattr(
        "core.openrouter_catalog.refresh_openrouter_catalog",
        lambda *a, **k: (_openrouter_stub((1.0, 4.0)),),
    )
    cloud_model_control.force_catalog_refresh()
    from core.model_radar_service import unread_findings

    (finding,) = unread_findings()
    assert finding.kind == "price_drop" and finding.provider_id == "openrouter"


def test_radar_failure_never_breaks_the_refresh(radar_store, monkeypatch) -> None:
    from core import cloud_model_control

    def _explode(*args, **kwargs):
        raise RuntimeError("radar down")

    monkeypatch.setattr("core.model_radar_service.observe_openrouter_catalog", _explode)
    monkeypatch.setattr(
        "core.openrouter_catalog.refresh_openrouter_catalog",
        lambda *a, **k: (_openrouter_stub((2.0, 8.0)),),
    )
    result = cloud_model_control.force_catalog_refresh()
    assert result["ok"] is True and result["total"] == 1, "the refresh must survive the radar failing"


# ---- background observer ----------------------------------------------------------------------


def test_observer_disabled_by_env_and_started_by_first_poll(radar_store, monkeypatch) -> None:
    from core import model_radar_service

    assert model_radar_service.ensure_background_observer() is False  # VOOL_MODEL_RADAR_POLLER=0

    calls = []
    monkeypatch.setattr(model_radar_service, "ensure_background_observer", lambda: calls.append(1) or True)
    status, _payload = _get("/api/model-radar/feed", "127.0.0.1")
    assert status == 200 and len(calls) == 1, "the first owner-local poll starts the observer"
    status, _payload = _get("/api/model-radar/feed", "203.0.113.9")
    assert status == 403 and len(calls) == 1, "a remote peer never starts anything"


def test_observer_poll_observes_through_the_catalog_door(radar_store, monkeypatch) -> None:
    from core import model_radar_service

    seen = {}
    monkeypatch.setattr(
        "core.openrouter_catalog.refresh_openrouter_catalog",
        lambda *a, **k: seen.setdefault("models", (_openrouter_stub((2.0, 8.0)),)) or seen["models"],
    )
    assert model_radar_service._poll_openrouter_once() is True
    from storage import model_radar as radar_storage

    assert radar_storage.get_observation("openrouter", "vendor/alpha") is not None
    monkeypatch.setattr(
        "core.openrouter_catalog.refresh_openrouter_catalog",
        lambda *a, **k: (_ for _ in ()).throw(OSError("provider down")),
    )
    assert model_radar_service._poll_openrouter_once() is False, "an outage costs one cycle, not the loop"
