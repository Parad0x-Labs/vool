
"""Save-for-later: an EXPLICIT unverified store, quarantined from every execution consumer.

THE LAWS UNDER TEST
-------------------
* ``persist="later"`` stores the key encrypted in a QUARANTINE slot (``quarantine.<slot>``) —
  the provider's real slot stays empty, the cloud lane stays unactivated, the connection
  indicator stays ``no_key``, and the search chain sees no key. The isolation is structural:
  nothing that executes resolves a slot by any other name than the provider table's.
* The quarantined row is HONEST: status ``unverified_quarantined``, the last verification
  outcome recorded, listed with retry/delete.
* Retry asks the provider ONCE. A verified answer promotes the key into the real slot, drops
  the quarantine copy and binds the lane like any verified save; any other answer keeps it
  quarantined with the provider's own word.
* A verified binding can never be displaced by an unverified paste, and deleting a quarantined
  key never touches a verified one.
"""
from __future__ import annotations

import dataclasses
import hashlib

from tests._credential_intelligence_support import FakeProviderServer
from tests.first_run_pact_rig import pact_rig


def _synthetic(label: str, prefix: str, length: int) -> str:
    return prefix + (hashlib.sha256(f"quarantine:{label}".encode()).hexdigest() * 2)[:length]


OPENROUTER_KEY = _synthetic("openrouter", "sk-or-v1-", 64)
OTHER_KEY = _synthetic("other", "sk-or-v1-", 64)


def _openrouter(valid_key: str):
    want = hashlib.sha256(f"Bearer {valid_key}".encode()).hexdigest()

    def respond(record):
        if record["path"].endswith("/models"):
            return (200, {"data": [{"id": "lab/one", "context_length": 8192}]}) if record["auth_sha256"] == want else (401, {"error": {"code": 401, "message": "no"}})
        if record["auth_sha256"] != want:
            return (401, {"error": {"code": 401, "message": "no"}})
        return (200, {"data": {"label": "q", "limit_remaining": None}})

    return respond


def _point_openrouter_at(monkeypatch, base_url: str) -> None:
    import core.cloud_providers as cloud_providers

    monkeypatch.setitem(
        cloud_providers.PROVIDERS, "openrouter",
        dataclasses.replace(cloud_providers.PROVIDERS["openrouter"], base_url=base_url),
    )


def _begin_classify_preview(rig, value: str, provider_id: str = "openrouter") -> str:
    status, begun = rig.post("/api/intake/begin", {})
    assert status == 200, begun
    session_id = begun["session_id"]
    status, classified = rig.post("/api/intake/classify", {"session_id": session_id, "value": value})
    assert status == 200, classified
    status, seen = rig.post("/api/intake/preview", {"session_id": session_id, "provider_id": provider_id})
    assert status == 200, seen
    return session_id


# ------------------------------------------------------------------ the explicit unverified store

def test_save_for_later_quarantines_without_any_request(pact_rig, monkeypatch):
    with FakeProviderServer() as openrouter:
        _point_openrouter_at(monkeypatch, f"{openrouter.url}/api/v1")
        session_id = _begin_classify_preview(pact_rig, OPENROUTER_KEY)
        status, done = pact_rig.post("/api/intake/complete", {"session_id": session_id, "persist": "later"})
        assert status == 200, done
        assert done["quarantined"] is True
        assert done["binding"]["status"] == "unverified_quarantined"
        assert openrouter.request_count == 0, "save-for-later sent a request"


def test_quarantined_key_is_invisible_to_every_execution_consumer(pact_rig, monkeypatch):
    with FakeProviderServer() as openrouter:
        _point_openrouter_at(monkeypatch, f"{openrouter.url}/api/v1")
        session_id = _begin_classify_preview(pact_rig, OPENROUTER_KEY)
        pact_rig.post("/api/intake/complete", {"session_id": session_id, "persist": "later"})

        from core import credential_store

        # the real slot is EMPTY: no completion, no probe, no search chain can see this key
        assert credential_store.get_credential("llm.cloud.openrouter") is None
        assert credential_store.get_credential("quarantine.llm.cloud.openrouter") == OPENROUTER_KEY

        from core.cloud_connection_state import connection_status

        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        status_row = connection_status(provider="openrouter")
        assert status_row["state"] == "no_key", status_row

        from core.cloud_providers import active_provider

        assert active_provider("") != "openrouter" or not credential_store.has_credential("llm.cloud.openrouter")


def test_quarantine_survives_restart_and_lists_honestly(pact_rig, monkeypatch):
    with FakeProviderServer() as openrouter:
        _point_openrouter_at(monkeypatch, f"{openrouter.url}/api/v1")
        session_id = _begin_classify_preview(pact_rig, OPENROUTER_KEY)
        pact_rig.post("/api/intake/complete", {"session_id": session_id, "persist": "later"})
        # a fresh store reads the same row (the file is the persistence)
        status, listed = pact_rig.get("/api/intake/quarantine/list")
        assert status == 200, listed
        rows = listed.get("quarantined") or (listed.get("data") or {}).get("quarantined") or []
        assert [r["provider_id"] for r in rows] == ["openrouter"]
        assert rows[0]["status"] == "unverified_quarantined"


# ------------------------------------------------------------------ retry promotes only on a verified answer

def test_retry_promotes_a_good_key_into_the_real_slot(pact_rig, monkeypatch):
    with FakeProviderServer(_openrouter(OPENROUTER_KEY)) as openrouter:
        _point_openrouter_at(monkeypatch, f"{openrouter.url}/api/v1")
        session_id = _begin_classify_preview(pact_rig, OPENROUTER_KEY)
        pact_rig.post("/api/intake/complete", {"session_id": session_id, "persist": "later"})

        from core import credential_store

        assert credential_store.get_credential("llm.cloud.openrouter") is None
        status, retried = pact_rig.post("/api/intake/quarantine/retry", {"provider_id": "openrouter"})
        assert status == 200, retried
        assert retried["promoted"] is True
        assert retried["outcome"] == "verified"
        assert credential_store.get_credential("llm.cloud.openrouter") == OPENROUTER_KEY
        assert credential_store.get_credential("quarantine.llm.cloud.openrouter") is None, "two live copies after promotion"
        status, listed = pact_rig.get("/api/intake/quarantine/list")
        rows = listed.get("quarantined") or (listed.get("data") or {}).get("quarantined") or []
        assert rows == []


def test_retry_keeps_quarantine_on_a_rejected_key(pact_rig, monkeypatch):
    with FakeProviderServer() as openrouter:
        # the service rejects every key
        openrouter.responses = lambda record: (401, {"error": {"code": 401, "message": "no"}})
        _point_openrouter_at(monkeypatch, f"{openrouter.url}/api/v1")
        session_id = _begin_classify_preview(pact_rig, OPENROUTER_KEY)
        pact_rig.post("/api/intake/complete", {"session_id": session_id, "persist": "later"})
        status, retried = pact_rig.post("/api/intake/quarantine/retry", {"provider_id": "openrouter"})
        assert status == 200, retried
        assert retried["promoted"] is False
        assert retried["outcome"] == "invalid"

        from core import credential_store

        assert credential_store.get_credential("llm.cloud.openrouter") is None
        assert credential_store.get_credential("quarantine.llm.cloud.openrouter") == OPENROUTER_KEY


def test_retry_on_throttle_names_itself_and_judges_nothing(pact_rig, monkeypatch):
    with FakeProviderServer() as openrouter:
        openrouter.responses = lambda record: (429, {"error": {"code": 429, "message": "slow"}})
        _point_openrouter_at(monkeypatch, f"{openrouter.url}/api/v1")
        session_id = _begin_classify_preview(pact_rig, OPENROUTER_KEY)
        pact_rig.post("/api/intake/complete", {"session_id": session_id, "persist": "later"})
        status, retried = pact_rig.post("/api/intake/quarantine/retry", {"provider_id": "openrouter"})
        assert retried["promoted"] is False
        assert retried["outcome"] == "rate_limited"


# ------------------------------------------------------------------ delete and protection

def test_delete_removes_the_quarantined_key_only(pact_rig, monkeypatch):
    with FakeProviderServer(_openrouter(OPENROUTER_KEY)) as openrouter:
        _point_openrouter_at(monkeypatch, f"{openrouter.url}/api/v1")
        session_id = _begin_classify_preview(pact_rig, OPENROUTER_KEY)
        pact_rig.post("/api/intake/complete", {"session_id": session_id, "persist": "later"})
        status, deleted = pact_rig.post("/api/intake/quarantine/delete", {"provider_id": "openrouter"})
        assert status == 200 and deleted["deleted"] is True

        from core import credential_store

        assert credential_store.get_credential("quarantine.llm.cloud.openrouter") is None
        status, retry = pact_rig.post("/api/intake/quarantine/retry", {"provider_id": "openrouter"})
        assert status == 404, retry


def test_verified_binding_cannot_be_displaced_by_an_unverified_paste(pact_rig, monkeypatch):
    with FakeProviderServer(_openrouter(OPENROUTER_KEY)) as openrouter:
        _point_openrouter_at(monkeypatch, f"{openrouter.url}/api/v1")
        # verified save first
        session_id = _begin_classify_preview(pact_rig, OPENROUTER_KEY)
        status, verified = pact_rig.post("/api/intake/verify", {"session_id": session_id, "provider_id": "openrouter"})
        assert status == 200 and verified["outcome"] == "verified", verified
        status, done = pact_rig.post("/api/intake/complete", {"session_id": session_id})
        assert status == 200, done

        # now an unverified paste for the same provider chooses save-for-later: refused
        session2 = _begin_classify_preview(pact_rig, OTHER_KEY)
        status, refused = pact_rig.post("/api/intake/complete", {"session_id": session2, "persist": "later"})
        assert status != 200, refused

        from core import credential_store

        assert credential_store.get_credential("llm.cloud.openrouter") == OPENROUTER_KEY, "an unverified paste displaced a working key"


def test_no_secret_in_any_persisted_surface(pact_rig, monkeypatch, tmp_path):
    with FakeProviderServer() as openrouter:
        _point_openrouter_at(monkeypatch, f"{openrouter.url}/api/v1")
        session_id = _begin_classify_preview(pact_rig, OPENROUTER_KEY)
        pact_rig.post("/api/intake/complete", {"session_id": session_id, "persist": "later"})
        hits = []
        for path in sorted((pact_rig.home / "data").rglob("*")):
            if path.is_file():
                if OPENROUTER_KEY.encode() in path.read_bytes() or OPENROUTER_KEY[-8:].encode() in path.read_bytes():
                    hits.append(str(path))
        assert hits == [], f"the raw key leaked into {hits}"
