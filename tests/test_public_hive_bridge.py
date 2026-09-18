from __future__ import annotations

import io
import json
import os
import tempfile
import urllib.error
from pathlib import Path
from unittest import mock

from apps.vool_agent import VoolAgent
from core.credit_ledger import award_credits, escrow_credits_for_task, get_credit_balance, get_escrow_for_task
from core.hive_write_grants import build_hive_write_grant
from core.public_hive import PublicHiveBridgeConfig as ExtractedPublicHiveBridgeConfig
from core.public_hive.bridge import PublicHiveBridge as ExtractedPublicHiveBridge
from core.public_hive_bridge import (
    PublicHiveBridge,
    PublicHiveBridgeConfig,
    ensure_public_hive_auth,
    load_public_hive_bridge_config,
    public_hive_write_enabled,
    sync_public_hive_auth_from_ssh,
    write_public_hive_agent_bootstrap,
)
from network.signer import get_local_peer_id
from storage.migrations import run_migrations


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def test_public_hive_bridge_facade_reuses_extracted_config_contract() -> None:
    assert PublicHiveBridgeConfig is ExtractedPublicHiveBridgeConfig


def test_public_hive_bridge_facade_reuses_extracted_bridge_contract() -> None:
    assert PublicHiveBridge is ExtractedPublicHiveBridge


def test_public_hive_bridge_syncs_presence_with_signed_envelope_and_token() -> None:
    seen: dict[str, object] = {}

    def fake_urlopen(req, timeout=0, context=None):
        seen["url"] = req.full_url
        seen["timeout"] = timeout
        seen["token"] = req.get_header("X-vool-meet-token")
        seen["body"] = json.loads(req.data.decode("utf-8"))
        return _FakeResponse({"ok": True, "result": {"agent_id": "peer-123"}, "error": None})

    bridge = PublicHiveBridge(
        PublicHiveBridgeConfig(
            enabled=True,
            meet_seed_urls=("http://seed-eu.example.test:8766",),
            topic_target_url="http://seed-eu.example.test:8766",
            home_region="eu",
            auth_token="cluster-token",
            request_timeout_seconds=7,
        ),
        urlopen=fake_urlopen,
    )

    result = bridge.sync_presence(
        agent_name="VOOL",
        capabilities=["persistent_memory", "web_research"],
        status="busy",
        transport_mode="openclaw_api",
    )

    assert result["ok"] is True
    assert seen["url"] == "http://seed-eu.example.test:8766/v1/presence/register"
    assert seen["timeout"] == 7
    assert seen["token"] == "cluster-token"
    envelope = dict(seen["body"])
    assert envelope["target_path"] == "/v1/presence/register"
    assert envelope["payload"]["status"] == "busy"
    assert envelope["payload"]["home_region"] == "eu"
    assert "endpoint" not in envelope["payload"]
    assert "endpoints" not in envelope["payload"]


def test_public_hive_bridge_private_post_json_facade_stays_available_for_callers() -> None:
    seen: dict[str, object] = {}

    def fake_urlopen(req, timeout=0, context=None):
        seen["url"] = req.full_url
        seen["timeout"] = timeout
        seen["body"] = json.loads(req.data.decode("utf-8"))
        return _FakeResponse({"ok": True, "result": {"post_id": "post-123"}, "error": None})

    bridge = PublicHiveBridge(
        PublicHiveBridgeConfig(
            enabled=True,
            meet_seed_urls=("http://seed-eu.example.test:8766",),
            topic_target_url="http://seed-eu.example.test:8766",
            home_region="eu",
            auth_token="cluster-token",
            request_timeout_seconds=7,
        ),
        urlopen=fake_urlopen,
    )

    result = bridge._post_json(
        "http://seed-eu.example.test:8766",
        "/v1/hive/posts",
        {"topic_id": "topic-123", "body": "direct bridge transport call"},
    )

    assert result["post_id"] == "post-123"
    assert seen["url"] == "http://seed-eu.example.test:8766/v1/hive/posts"
    assert seen["timeout"] == 7
    envelope = dict(seen["body"])
    assert envelope["target_path"] == "/v1/hive/posts"
    assert envelope["payload"]["body"] == "direct bridge transport call"


def test_public_hive_bridge_private_get_json_falls_back_to_secondary_seed_and_remembers_it() -> None:
    seen_urls: list[str] = []

    def fake_urlopen(req, timeout=0, context=None):
        seen_urls.append(req.full_url)
        if req.full_url.startswith("http://seed-eu.example.test:8766/"):
            raise urllib.error.URLError(TimeoutError("timed out"))
        return _FakeResponse({"ok": True, "result": [{"topic_id": "topic-123"}], "error": None})

    bridge = PublicHiveBridge(
        PublicHiveBridgeConfig(
            enabled=True,
            meet_seed_urls=("http://seed-eu.example.test:8766", "http://seed-us.example.test:8766"),
            topic_target_url="http://seed-eu.example.test:8766",
            home_region="eu",
            auth_token="cluster-token",
            request_timeout_seconds=7,
        ),
        urlopen=fake_urlopen,
    )

    result = bridge._get_json(
        "http://seed-eu.example.test:8766",
        "/v1/hive/research-queue?limit=1",
    )

    assert result == [{"topic_id": "topic-123"}]
    assert seen_urls == [
        "http://seed-eu.example.test:8766/v1/hive/research-queue?limit=1",
        "http://seed-us.example.test:8766/v1/hive/research-queue?limit=1",
    ]

    seen_urls.clear()
    second = bridge._get_json(
        "http://seed-eu.example.test:8766",
        "/v1/hive/research-queue?limit=1",
    )

    assert second == [{"topic_id": "topic-123"}]
    assert seen_urls == ["http://seed-us.example.test:8766/v1/hive/research-queue?limit=1"]


def test_public_hive_bridge_private_post_json_falls_back_to_secondary_seed_for_topic_writes() -> None:
    seen_urls: list[str] = []

    def fake_urlopen(req, timeout=0, context=None):
        seen_urls.append(req.full_url)
        if req.full_url.startswith("http://seed-eu.example.test:8766/"):
            raise urllib.error.URLError(TimeoutError("timed out"))
        return _FakeResponse({"ok": True, "result": {"topic_id": "topic-123"}, "error": None})

    bridge = PublicHiveBridge(
        PublicHiveBridgeConfig(
            enabled=True,
            meet_seed_urls=("http://seed-eu.example.test:8766", "http://seed-us.example.test:8766"),
            topic_target_url="http://seed-eu.example.test:8766",
            home_region="eu",
            auth_token="cluster-token",
            request_timeout_seconds=7,
        ),
        urlopen=fake_urlopen,
    )

    result = bridge._post_json(
        "http://seed-eu.example.test:8766",
        "/v1/hive/topics",
        {"title": "Fallback proof", "summary": "prove second seed write fallback"},
    )

    assert result["topic_id"] == "topic-123"
    assert seen_urls == [
        "http://seed-eu.example.test:8766/v1/hive/topics",
        "http://seed-us.example.test:8766/v1/hive/topics",
    ]


def test_public_hive_bridge_blocks_live_network_under_pytest() -> None:
    bridge = PublicHiveBridge(
        PublicHiveBridgeConfig(
            enabled=True,
            meet_seed_urls=("https://meet-eu.parad0xlabs.com",),
            topic_target_url="https://meet-eu.parad0xlabs.com",
            home_region="eu",
            auth_token="cluster-token",
            request_timeout_seconds=7,
        )
    )

    result = bridge.sync_presence(
        agent_name="VOOL",
        capabilities=["persistent_memory"],
        status="idle",
        transport_mode="vool_agent",
    )

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert result["errors"]
    assert "blocked under pytest" in result["errors"][0]


def test_public_hive_bridge_surfaces_http_error_body_for_write_failures() -> None:
    def fake_urlopen(req, timeout=0, context=None):
        raise urllib.error.HTTPError(
            req.full_url,
            429,
            "Too Many Requests",
            hdrs=None,
            fp=io.BytesIO(
                json.dumps(
                    {
                        "ok": False,
                        "error": "Public Hive write quota exhausted for today. Used 48.0/48.0 points at tier established.",
                    }
                ).encode("utf-8")
            ),
        )

    bridge = PublicHiveBridge(
        PublicHiveBridgeConfig(
            enabled=True,
            meet_seed_urls=("http://seed-eu.example.test:8766",),
            topic_target_url="http://seed-eu.example.test:8766",
            home_region="eu",
            auth_token="cluster-token",
            request_timeout_seconds=7,
        ),
        urlopen=fake_urlopen,
    )

    try:
        bridge.post_public_topic_progress(
            topic_id="topic-1234567890abcdef",
            body="probe",
            progress_state="working",
            claim_id="claim-123",
            idempotency_key="probe-429",
        )
    except ValueError as exc:
        assert "quota exhausted" in str(exc).lower()
    else:
        raise AssertionError("expected ValueError for HTTP 429 write failure")


def test_public_hive_bridge_allows_seed_urls_without_token() -> None:
    # Public hive is opt-in since #75, so enable it explicitly here; this test's subject is that
    # discovered seed URLs are accepted without an auth token, not the enable default.
    with mock.patch.dict(os.environ, {"VOOL_PUBLIC_HIVE_ENABLED": "1"}), mock.patch(
        "core.public_hive_bridge.ensure_public_hive_agent_bootstrap", return_value=None
    ), mock.patch(
        "core.public_hive_bridge._load_agent_bootstrap",
        return_value={
            "home_region": "eu",
            "meet_seed_urls": ["https://seed-eu.example.test:8766"],
            "tls_insecure_skip_verify": True,
        },
    ):
        cfg = load_public_hive_bridge_config()

    assert cfg.enabled is True
    assert cfg.meet_seed_urls == ("https://seed-eu.example.test:8766",)
    assert cfg.auth_token is None
    assert cfg.tls_insecure_skip_verify is False


def test_public_hive_bridge_loads_route_scoped_write_grants() -> None:
    local_peer_id = get_local_peer_id()
    grant = build_hive_write_grant(
        granted_to=local_peer_id,
        allowed_paths=["/v1/hive/posts"],
        max_uses=3,
    )
    with mock.patch("core.public_hive_bridge.ensure_public_hive_agent_bootstrap", return_value=None), mock.patch(
        "core.public_hive_bridge._load_agent_bootstrap",
        return_value={
            "home_region": "eu",
            "meet_seed_urls": ["https://seed-eu.example.test:8766"],
            "write_grants_by_base_url": {
                "https://seed-eu.example.test:8766": {
                    "/v1/hive/posts": grant,
                }
            },
        },
    ):
        cfg = load_public_hive_bridge_config()

    assert cfg.write_grants_by_base_url["https://seed-eu.example.test:8766"]["/v1/hive/posts"]["grant_id"] == grant["grant_id"]


def test_public_hive_write_enabled_requires_auth_for_public_seed_urls() -> None:
    cfg = PublicHiveBridgeConfig(
        enabled=True,
        meet_seed_urls=("https://seed-eu.example.test:8766",),
        topic_target_url="https://seed-eu.example.test:8766",
        auth_token=None,
    )

    assert public_hive_write_enabled(cfg) is False


def test_public_hive_bridge_update_reports_not_owner_for_semantic_http_error() -> None:
    def fake_urlopen(req, timeout=0, context=None):
        raise urllib.error.HTTPError(
            req.full_url,
            400,
            "Bad Request",
            hdrs=None,
            fp=io.BytesIO(json.dumps({"ok": False, "error": "Only the creating agent can edit this Hive topic."}).encode("utf-8")),
        )

    bridge = PublicHiveBridge(
        PublicHiveBridgeConfig(
            enabled=True,
            meet_seed_urls=("https://seed-eu.example.test:8766",),
            topic_target_url="https://seed-eu.example.test:8766",
            home_region="eu",
            auth_token="cluster-token",
            tls_insecure_skip_verify=True,
        ),
        urlopen=fake_urlopen,
    )

    result = bridge.update_public_topic(
        topic_id="topic-1234567890abcdef",
        summary="updated summary",
        topic_tags=["validation"],
    )

    assert result["ok"] is False
    assert result["status"] == "not_owner"


def test_public_hive_bridge_delete_reports_claimed_for_semantic_http_error() -> None:
    def fake_urlopen(req, timeout=0, context=None):
        raise urllib.error.HTTPError(
            req.full_url,
            400,
            "Bad Request",
            hdrs=None,
            fp=io.BytesIO(json.dumps({"ok": False, "error": "This Hive topic is already claimed, so it can't be deleted now."}).encode("utf-8")),
        )

    bridge = PublicHiveBridge(
        PublicHiveBridgeConfig(
            enabled=True,
            meet_seed_urls=("https://seed-eu.example.test:8766",),
            topic_target_url="https://seed-eu.example.test:8766",
            home_region="eu",
            auth_token="cluster-token",
            tls_insecure_skip_verify=True,
        ),
        urlopen=fake_urlopen,
    )

    result = bridge.delete_public_topic(topic_id="topic-1234567890abcdef")

    assert result["ok"] is False
    assert result["status"] == "already_claimed"


def test_public_hive_bridge_update_status_reports_not_owner_for_semantic_http_error() -> None:
    def fake_urlopen(req, timeout=0, context=None):
        raise urllib.error.HTTPError(
            req.full_url,
            400,
            "Bad Request",
            hdrs=None,
            fp=io.BytesIO(json.dumps({"ok": False, "error": "Only the creating agent can update this Hive topic."}).encode("utf-8")),
        )

    bridge = PublicHiveBridge(
        PublicHiveBridgeConfig(
            enabled=True,
            meet_seed_urls=("https://seed-eu.example.test:8766",),
            topic_target_url="https://seed-eu.example.test:8766",
            home_region="eu",
            auth_token="cluster-token",
            tls_insecure_skip_verify=True,
        ),
        urlopen=fake_urlopen,
    )

    result = bridge.update_public_topic_status(topic_id="topic-1234567890abcdef", status="closed")

    assert result["ok"] is False
    assert result["status"] == "not_owner"


def test_public_hive_bridge_update_status_reports_invalid_claim_for_semantic_http_error() -> None:
    def fake_urlopen(req, timeout=0, context=None):
        raise urllib.error.HTTPError(
            req.full_url,
            400,
            "Bad Request",
            hdrs=None,
            fp=io.BytesIO(json.dumps({"ok": False, "error": "Unknown topic claim: claim-does-not-exist"}).encode("utf-8")),
        )

    bridge = PublicHiveBridge(
        PublicHiveBridgeConfig(
            enabled=True,
            meet_seed_urls=("https://seed-eu.example.test:8766",),
            topic_target_url="https://seed-eu.example.test:8766",
            home_region="eu",
            auth_token="cluster-token",
            tls_insecure_skip_verify=True,
        ),
        urlopen=fake_urlopen,
    )

    result = bridge.update_public_topic_status(
        topic_id="topic-1234567890abcdef",
        status="closed",
        claim_id="claim-does-not-exist",
    )

    assert result["ok"] is False
    assert result["status"] == "invalid_claim"


def test_public_hive_bridge_ignores_discovered_auth_token_from_local_watch_config() -> None:
    watch_config = {
        "auth_token": "real-cluster-token",
        "upstream_base_urls": [
            "http://seed-eu.example.test:8766",
            "http://seed-us.example.test:8766",
        ],
    }
    # Public hive is opt-in since #75, so enable it explicitly; this test's subject is that a
    # discovered auth token from the local watch config is ignored, not the enable default.
    with mock.patch.dict(os.environ, {"VOOL_PUBLIC_HIVE_ENABLED": "1"}), mock.patch(
        "core.public_hive_bridge.ensure_public_hive_agent_bootstrap", return_value=None
    ), mock.patch(
        "core.public_hive_bridge._load_agent_bootstrap",
        return_value={},
    ), mock.patch(
        "core.public_hive_bridge._discover_local_cluster_bootstrap",
        return_value={
            "auth_token": watch_config["auth_token"],
            "meet_seed_urls": watch_config["upstream_base_urls"],
            "tls_ca_file": "/tmp/cluster-ca.pem",
        },
    ):
        cfg = load_public_hive_bridge_config()

    assert cfg.enabled is True
    assert cfg.auth_token is None
    assert cfg.meet_seed_urls == tuple(watch_config["upstream_base_urls"])
    assert cfg.tls_ca_file == "/tmp/cluster-ca.pem"
    assert cfg.tls_insecure_skip_verify is False


def test_public_hive_bridge_ignores_discovered_region_tokens_for_selected_watch_urls() -> None:
    do_ip_watch_urls = [
        "https://203.0.113.11:8766",
        "https://203.0.113.12:8766",
        "https://203.0.113.13:8766",
    ]
    separated_watch_urls = [
        "https://meet-eu.parad0xlabs.com",
        "https://meet-us.parad0xlabs.com",
        "https://meet-apac.parad0xlabs.com",
    ]
    real_tokens = {
        "eu": "5c3f6e6c912d758eb8f74a3a3d6fca8878a95195473b2cb3abdbabb644f12c51",
        "us": "2e4ac333c8be3c94dfeffec45df65fc30f879ad223ab222ee557645335dc5d47",
        "apac": "03f41fa8f1dc4612187c95d14dacfd3ebeb99ea612992e7647f7b79f2c92b0e4",
    }

    def fake_load_json(path: Path) -> dict[str, object]:
        target = path.as_posix()
        if target.endswith("config/agent-bootstrap.json"):
            return {}
        if target.endswith("do_ip_first_4node/watch-edge-1.json"):
            return {
                "auth_token": "set-strong-meet-token-before-startup",
                "upstream_base_urls": do_ip_watch_urls,
                "tls_ca_file": "/tmp/cluster-ca.pem",
            }
        if target.endswith("separated_watch_4node/watch-edge-1.json"):
            return {
                "auth_token": "set-strong-meet-token-before-startup",
                "upstream_base_urls": separated_watch_urls,
            }
        if target.endswith("do_ip_first_4node/seed-eu-1.json"):
            return {
                "public_base_url": do_ip_watch_urls[0],
                "auth_token": "replace-with-strong-meet-token-eu",
            }
        if target.endswith("do_ip_first_4node/seed-us-1.json"):
            return {
                "public_base_url": do_ip_watch_urls[1],
                "auth_token": "replace-with-strong-meet-token-us",
            }
        if target.endswith("do_ip_first_4node/seed-apac-1.json"):
            return {
                "public_base_url": do_ip_watch_urls[2],
                "auth_token": "replace-with-strong-meet-token-apac",
            }
        if target.endswith("separated_watch_4node/seed-eu-1.json"):
            return {
                "public_base_url": separated_watch_urls[0],
                "auth_token": real_tokens["eu"],
            }
        if target.endswith("separated_watch_4node/seed-us-1.json"):
            return {
                "public_base_url": separated_watch_urls[1],
                "auth_token": real_tokens["us"],
            }
        if target.endswith("separated_watch_4node/seed-apac-1.json"):
            return {
                "public_base_url": separated_watch_urls[2],
                "auth_token": real_tokens["apac"],
            }
        raise AssertionError(f"unexpected config path: {target}")

    with mock.patch("core.public_hive_bridge.ensure_public_hive_agent_bootstrap", return_value=None), mock.patch(
        "core.public_hive_bridge._load_agent_bootstrap",
        return_value={},
    ), mock.patch(
        "core.public_hive_bridge._load_json_file",
        side_effect=fake_load_json,
    ):
        cfg = load_public_hive_bridge_config()

    assert cfg.meet_seed_urls == tuple(do_ip_watch_urls)
    assert cfg.auth_token is None
    assert cfg.auth_tokens_by_base_url == {}
    assert cfg.tls_ca_file == "/tmp/cluster-ca.pem"


def test_sync_public_hive_auth_from_ssh_writes_runtime_bootstrap() -> None:
    remote_payload = {
        "auth_token": "real-cluster-token",
        "upstream_base_urls": [
            "https://seed-eu.example.test:8766",
            "https://seed-us.example.test:8766",
        ],
        "tls_ca_file": "/opt/vool-hive-mind/config/tls/cluster-ca.pem",
        "tls_insecure_skip_verify": False,
    }

    def fake_runner(cmd, capture_output=False, check=False, text=False, timeout=0):
        assert cmd[0] == "ssh"
        assert "-i" in cmd
        assert capture_output is True
        assert check is True
        assert text is True
        assert timeout == 12
        return type("Completed", (), {"stdout": json.dumps(remote_payload)})()

    with tempfile.TemporaryDirectory() as tmp_dir:
        key_path = Path(tmp_dir) / "cluster_key"
        key_path.write_text("dummy", encoding="utf-8")
        target_path = Path(tmp_dir) / "agent-bootstrap.json"
        result = sync_public_hive_auth_from_ssh(
            ssh_key_path=str(key_path),
            project_root=Path(tmp_dir),
            watch_host="watch.example.test",
            remote_config_path="/etc/vool-hive-mind/watch-config.json",
            target_path=target_path,
            runner=fake_runner,
        )

        payload = json.loads(target_path.read_text(encoding="utf-8"))

    assert result["auth_loaded"] is True
    assert payload["auth_token"] == "real-cluster-token"
    assert payload["meet_seed_urls"] == remote_payload["upstream_base_urls"]
    assert payload["tls_ca_file"] == remote_payload["tls_ca_file"]


def test_sync_public_hive_auth_from_ssh_facade_uses_patchable_writer() -> None:
    remote_payload = {
        "auth_token": "real-cluster-token",
        "upstream_base_urls": ["https://seed-eu.example.test:8766"],
    }

    def fake_runner(cmd, capture_output=False, check=False, text=False, timeout=0):
        del cmd, capture_output, check, text, timeout
        return type("Completed", (), {"stdout": json.dumps(remote_payload)})()

    with tempfile.TemporaryDirectory() as tmp_dir:
        key_path = Path(tmp_dir) / "cluster_key"
        key_path.write_text("dummy", encoding="utf-8")
        target_path = Path(tmp_dir) / "agent-bootstrap.json"
        with mock.patch(
            "core.public_hive_bridge.write_public_hive_agent_bootstrap",
            return_value=target_path,
        ) as write_bootstrap:
            result = sync_public_hive_auth_from_ssh(
                ssh_key_path=str(key_path),
                project_root=Path(tmp_dir),
                watch_host="watch.example.test",
                remote_config_path="/etc/vool-hive-mind/watch-config.json",
                target_path=target_path,
                runner=fake_runner,
            )

    assert result["path"] == str(target_path)
    write_bootstrap.assert_called_once()


def test_ensure_public_hive_auth_rewrites_tls_ca_file_to_local_project_path() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        project_root = Path(tmp_dir) / "bundle"
        tls_dir = project_root / "config" / "meet_clusters" / "do_ip_first_4node" / "tls"
        tls_dir.mkdir(parents=True, exist_ok=True)
        ca_path = tls_dir / "cluster-ca.pem"
        ca_path.write_text("dummy-ca", encoding="utf-8")
        (project_root / "config" / "agent-bootstrap.json").write_text(
            json.dumps(
                {
                    "meet_seed_urls": ["https://seed-eu.example.test:8766"],
                    "auth_token": "bundle-token",
                    "tls_ca_file": "/opt/vool-hive-mind/config/meet_clusters/do_ip_first_4node/tls/cluster-ca.pem",
                }
            ),
            encoding="utf-8",
        )
        target_path = Path(tmp_dir) / "runtime" / "agent-bootstrap.json"

        result = ensure_public_hive_auth(project_root=project_root, target_path=target_path)
        payload = json.loads(target_path.read_text(encoding="utf-8"))

    assert result["ok"] is True
    assert payload["tls_ca_file"] == str(ca_path.resolve())


def test_write_public_hive_agent_bootstrap_stores_project_relative_tls_ca_file() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        project_root = Path(tmp_dir) / "bundle"
        tls_dir = project_root / "config" / "meet_clusters" / "do_ip_first_4node" / "tls"
        tls_dir.mkdir(parents=True, exist_ok=True)
        ca_path = tls_dir / "cluster-ca.pem"
        ca_path.write_text("dummy-ca", encoding="utf-8")
        target_path = project_root / "config" / "agent-bootstrap.json"

        written = write_public_hive_agent_bootstrap(
            project_root=project_root,
            target_path=target_path,
            meet_seed_urls=["https://seed-eu.example.test:8766"],
            auth_token="bundle-token",
            tls_ca_file="/opt/vool-hive-mind/config/meet_clusters/do_ip_first_4node/tls/cluster-ca.pem",
        )
        payload = json.loads(target_path.read_text(encoding="utf-8"))

    assert written == target_path.resolve()
    assert payload["tls_ca_file"] == "config/meet_clusters/do_ip_first_4node/tls/cluster-ca.pem"


def test_ensure_public_hive_auth_hydrates_target_from_bundled_project_config() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        project_root = Path(tmp_dir) / "bundle"
        (project_root / "config").mkdir(parents=True, exist_ok=True)
        (project_root / "config" / "agent-bootstrap.json").write_text(
            json.dumps(
                {
                    "meet_seed_urls": ["https://seed-eu.example.test:8766"],
                    "auth_token": "bundle-token",
                    "tls_insecure_skip_verify": True,
                }
            ),
            encoding="utf-8",
        )
        target_path = Path(tmp_dir) / "runtime" / "agent-bootstrap.json"

        result = ensure_public_hive_auth(project_root=project_root, target_path=target_path)

        payload = json.loads(target_path.read_text(encoding="utf-8"))

    assert result["ok"] is True
    assert result["status"] == "hydrated_from_bundle"
    assert payload["auth_token"] == "bundle-token"
    assert payload["meet_seed_urls"] == ["https://seed-eu.example.test:8766"]


def test_ensure_public_hive_auth_hydrates_target_from_discovered_local_cluster_without_ssh() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        project_root = Path(tmp_dir)
        cluster_dir = project_root / "config" / "meet_clusters" / "do_ip_first_4node"
        cluster_dir.mkdir(parents=True, exist_ok=True)
        (cluster_dir / "watch-edge-1.json").write_text(
            json.dumps(
                {
                    "public_base_url": "https://203.0.113.14:8788",
                    "upstream_base_urls": [
                        "https://203.0.113.11:8766",
                        "https://203.0.113.12:8766",
                        "https://203.0.113.13:8766",
                    ],
                    "tls_insecure_skip_verify": True,
                }
            ),
            encoding="utf-8",
        )
        (cluster_dir / "seed-eu-1.json").write_text(
            json.dumps(
                {
                    "public_base_url": "https://203.0.113.11:8766",
                    "auth_token": "eu-token",
                }
            ),
            encoding="utf-8",
        )
        (cluster_dir / "seed-us-1.json").write_text(
            json.dumps(
                {
                    "public_base_url": "https://203.0.113.12:8766",
                    "auth_token": "us-token",
                }
            ),
            encoding="utf-8",
        )
        (cluster_dir / "seed-apac-1.json").write_text(
            json.dumps(
                {
                    "public_base_url": "https://203.0.113.13:8766",
                    "auth_token": "apac-token",
                }
            ),
            encoding="utf-8",
        )
        target_path = project_root / "runtime" / "agent-bootstrap.json"

        with mock.patch("core.public_hive_bridge.find_public_hive_ssh_key") as find_ssh_key, mock.patch(
            "core.public_hive_bridge.sync_public_hive_auth_from_ssh"
        ) as sync_ssh:
            result = ensure_public_hive_auth(project_root=project_root, target_path=target_path)

        payload = json.loads(target_path.read_text(encoding="utf-8"))

    assert result["ok"] is True
    assert result["status"] == "hydrated_from_local_cluster"
    assert payload["meet_seed_urls"] == [
        "https://203.0.113.11:8766",
        "https://203.0.113.12:8766",
        "https://203.0.113.13:8766",
    ]
    assert payload["auth_tokens_by_base_url"] == {
        "https://203.0.113.11:8766": "eu-token",
        "https://203.0.113.12:8766": "us-token",
        "https://203.0.113.13:8766": "apac-token",
    }
    assert payload["tls_insecure_skip_verify"] is True
    find_ssh_key.assert_not_called()
    sync_ssh.assert_not_called()


def test_ensure_public_hive_auth_facade_uses_patchable_writer() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        project_root = Path(tmp_dir) / "bundle"
        (project_root / "config").mkdir(parents=True, exist_ok=True)
        (project_root / "config" / "agent-bootstrap.json").write_text(
            json.dumps(
                {
                    "meet_seed_urls": ["https://seed-eu.example.test:8766"],
                    "auth_token": "bundle-token",
                }
            ),
            encoding="utf-8",
        )
        target_path = Path(tmp_dir) / "runtime" / "agent-bootstrap.json"
        with mock.patch(
            "core.public_hive_bridge.write_public_hive_agent_bootstrap",
            return_value=target_path,
        ) as write_bootstrap:
            result = ensure_public_hive_auth(project_root=project_root, target_path=target_path)

    assert result["ok"] is True
    write_bootstrap.assert_called_once()


def test_ensure_public_hive_auth_resolves_relative_tls_ca_file_against_project_root() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        project_root = Path(tmp_dir) / "bundle"
        tls_dir = project_root / "config" / "meet_clusters" / "do_ip_first_4node" / "tls"
        tls_dir.mkdir(parents=True, exist_ok=True)
        ca_path = tls_dir / "cluster-ca.pem"
        ca_path.write_text("dummy-ca", encoding="utf-8")
        (project_root / "config" / "agent-bootstrap.json").write_text(
            json.dumps(
                {
                    "meet_seed_urls": ["https://seed-eu.example.test:8766"],
                    "auth_token": "bundle-token",
                    "tls_ca_file": "config/meet_clusters/do_ip_first_4node/tls/cluster-ca.pem",
                }
            ),
            encoding="utf-8",
        )
        target_path = Path(tmp_dir) / "runtime" / "agent-bootstrap.json"

        result = ensure_public_hive_auth(project_root=project_root, target_path=target_path)
        payload = json.loads(target_path.read_text(encoding="utf-8"))

    assert result["ok"] is True
    assert payload["tls_ca_file"] == str(ca_path.resolve())


def test_ensure_public_hive_auth_marks_ssh_sync_as_ok() -> None:
    remote_payload = {
        "auth_token": "real-cluster-token",
        "upstream_base_urls": ["https://seed-eu.example.test:8766"],
    }

    def fake_runner(cmd, capture_output=False, check=False, text=False, timeout=0):
        del cmd, capture_output, check, text, timeout
        return type("Completed", (), {"stdout": json.dumps(remote_payload)})()

    with tempfile.TemporaryDirectory() as tmp_dir:
        key_path = Path(tmp_dir) / "cluster_key"
        key_path.write_text("dummy", encoding="utf-8")
        with mock.patch("core.public_hive_bridge.find_public_hive_ssh_key", return_value=key_path), mock.patch(
            "core.public_hive_bridge.subprocess.run",
            side_effect=fake_runner,
        ):
            result = ensure_public_hive_auth(
                project_root=Path(tmp_dir),
                target_path=Path(tmp_dir) / "agent-bootstrap.json",
                watch_host="hive.example.test",
                remote_config_path="/etc/vool-hive-mind/watch-config.json",
                require_auth=True,
            )

    assert result["ok"] is True
    assert result["status"] == "synced_from_ssh"


def test_ensure_public_hive_auth_uses_default_remote_config_path_when_not_explicitly_set() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        project_root = Path(tmp_dir)
        cluster_dir = project_root / "config" / "meet_clusters" / "do_ip_first_4node"
        cluster_dir.mkdir(parents=True, exist_ok=True)
        (cluster_dir / "watch-edge-1.json").write_text(
            json.dumps(
                {
                    "public_base_url": "https://voolbook.example.test",
                    "auth_required": True,
                    "upstream_base_urls": ["https://voolbook.example.test"],
                }
            ),
            encoding="utf-8",
        )
        key_path = project_root / "cluster_key"
        key_path.write_text("dummy", encoding="utf-8")
        target_path = project_root / "runtime" / "agent-bootstrap.json"
        seen: dict[str, str] = {}

        def fake_sync(**kwargs):
            seen["remote_config_path"] = str(kwargs["remote_config_path"])
            return {
                "target_path": str(Path(kwargs["target_path"]).resolve()),
                "watch_host": str(kwargs["watch_host"]),
                "seed_count": 1,
            }

        with mock.patch("core.public_hive_bridge.find_public_hive_ssh_key", return_value=key_path), mock.patch(
            "core.public_hive_bridge.sync_public_hive_auth_from_ssh",
            side_effect=fake_sync,
        ):
            result = ensure_public_hive_auth(project_root=project_root, target_path=target_path)

    assert result["ok"] is True
    assert result["status"] == "synced_from_ssh"
    assert result["watch_host"] == "voolbook.example.test"
    assert result["target_path"] == str(target_path.resolve())
    assert seen["remote_config_path"] == "/etc/vool-hive-mind/watch-config.json"


def test_ensure_public_hive_auth_returns_sync_failed_instead_of_raising() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        project_root = Path(tmp_dir)
        cluster_dir = project_root / "config" / "meet_clusters" / "do_ip_first_4node"
        cluster_dir.mkdir(parents=True, exist_ok=True)
        (cluster_dir / "watch-edge-1.json").write_text(
            json.dumps(
                {
                    "public_url": "https://hive.example.test",
                    "auth_required": True,
                    "upstream_base_urls": ["https://meet-eu.example.test"],
                }
            ),
            encoding="utf-8",
        )
        key_path = project_root / "cluster_key"
        key_path.write_text("dummy", encoding="utf-8")
        target_path = project_root / "runtime" / "agent-bootstrap.json"

        with mock.patch("core.public_hive_bridge.find_public_hive_ssh_key", return_value=key_path), mock.patch(
            "core.public_hive_bridge.sync_public_hive_auth_from_ssh",
            side_effect=RuntimeError("ssh failed"),
        ):
            result = ensure_public_hive_auth(
                project_root=project_root,
                target_path=target_path,
                remote_config_path="/etc/vool-hive-mind/watch-config.json",
            )

    assert result["ok"] is False
    assert result["status"] == "sync_failed"
    assert result["watch_host"] == "hive.example.test"
    assert result["error"] == "ssh failed"


def test_public_hive_bridge_joins_existing_related_topic_before_creating_duplicate() -> None:
    seen: list[tuple[str, str]] = []

    def fake_urlopen(req, timeout=0, context=None):
        del timeout, context
        method = req.get_method()
        seen.append((method, req.full_url))
        if method == "GET" and req.full_url.endswith("/v1/hive/topics?limit=24"):
            return _FakeResponse(
                {
                    "ok": True,
                    "result": [
                        {
                            "topic_id": "topic-remote-1234",
                            "created_by_agent_id": "peer-remote-12345678",
                            "title": "OpenClaw and Liquefy integration audit",
                            "summary": "Research thread about OpenClaw bridge continuity and Liquefy integration.",
                            "topic_tags": ["openclaw", "liquefy", "research"],
                            "status": "open",
                        }
                    ],
                    "error": None,
                }
            )
        if method == "POST" and req.full_url.endswith("/v1/hive/posts"):
            envelope = json.loads(req.data.decode("utf-8"))
            assert envelope["payload"]["topic_id"] == "topic-remote-1234"
            return _FakeResponse({"ok": True, "result": {"post_id": "post-remote-1"}, "error": None})
        raise AssertionError(f"Unexpected request: {method} {req.full_url}")

    bridge = PublicHiveBridge(
        PublicHiveBridgeConfig(
            enabled=True,
            meet_seed_urls=("http://seed-eu.example.test:8766",),
            topic_target_url="http://seed-eu.example.test:8766",
            home_region="eu",
            auth_token="cluster-token",
        ),
        urlopen=fake_urlopen,
    )

    result = bridge.publish_public_task(
        task_id="task-public-1",
        task_summary="Research OpenClaw and Liquefy integration",
        task_class="research",
        assistant_response="Research thread opened.",
        topic_tags=["openclaw", "liquefy"],
    )

    assert result["ok"] is True
    assert result["status"] == "joined_existing_topic"
    assert result["topic_id"] == "topic-remote-1234"
    assert ("POST", "http://seed-eu.example.test:8766/v1/hive/topics") not in seen


def test_public_hive_bridge_posts_into_existing_agent_commons_topic() -> None:
    seen: list[tuple[str, str]] = []

    def fake_urlopen(req, timeout=0, context=None):
        del timeout, context
        method = req.get_method()
        seen.append((method, req.full_url))
        if method == "GET" and req.full_url.endswith("/v1/hive/topics?limit=48"):
            return _FakeResponse(
                {
                    "ok": True,
                    "result": [
                        {
                            "topic_id": "topic-commons-123456",
                            "created_by_agent_id": "peer-remote-12345678",
                            "title": "Agent Commons: better human-visible watcher and task-flow UX",
                            "summary": "Idle agent commons thread for bounded brainstorming and curiosity.",
                            "topic_tags": ["agent_commons", "brainstorm", "design"],
                            "status": "researching",
                        }
                    ],
                    "error": None,
                }
            )
        if method == "POST" and req.full_url.endswith("/v1/hive/posts"):
            envelope = json.loads(req.data.decode("utf-8"))
            assert envelope["payload"]["topic_id"] == "topic-commons-123456"
            assert "Commons update" in envelope["payload"]["body"]
            return _FakeResponse({"ok": True, "result": {"post_id": "post-commons-1"}, "error": None})
        raise AssertionError(f"Unexpected request: {method} {req.full_url}")

    bridge = PublicHiveBridge(
        PublicHiveBridgeConfig(
            enabled=True,
            meet_seed_urls=("http://seed-eu.example.test:8766",),
            topic_target_url="http://seed-eu.example.test:8766",
            home_region="eu",
            auth_token="cluster-token",
        ),
        urlopen=fake_urlopen,
    )

    result = bridge.publish_agent_commons_update(
        topic="Agent commons brainstorm: better human-visible watcher and task-flow UX",
        topic_kind="design",
        summary="Focus on clickable topics and live work-flow views.",
        public_body="Agent commons update: clickable topics, live flow, and better research visibility.",
        topic_tags=["agent_commons", "brainstorm", "design"],
    )

    assert result["ok"] is True
    assert result["status"] == "joined_existing_commons_topic"
    assert result["topic_id"] == "topic-commons-123456"
    assert ("POST", "http://seed-eu.example.test:8766/v1/hive/topics") not in seen


def test_public_hive_bridge_claims_posts_progress_and_submits_result() -> None:
    seen: list[tuple[str, str, dict[str, object]]] = []

    def fake_urlopen(req, timeout=0, context=None):
        del timeout, context
        method = req.get_method()
        payload = json.loads(req.data.decode("utf-8")) if getattr(req, "data", None) else {}
        seen.append((method, req.full_url, payload))
        if method == "POST" and req.full_url.endswith("/v1/hive/topic-claims"):
            assert payload["payload"]["topic_id"] == "topic-1234567890abcdef"
            return _FakeResponse({"ok": True, "result": {"claim_id": "claim-1"}, "error": None})
        if method == "POST" and req.full_url.endswith("/v1/hive/posts"):
            post_body = payload["payload"]["body"]
            if "Progress" in post_body:
                return _FakeResponse({"ok": True, "result": {"post_id": "post-progress-1"}, "error": None})
            return _FakeResponse({"ok": True, "result": {"post_id": "post-result-1"}, "error": None})
        if method == "POST" and req.full_url.endswith("/v1/hive/topic-status"):
            assert payload["payload"]["status"] == "solved"
            assert payload["payload"]["claim_id"] == "claim-1"
            return _FakeResponse({"ok": True, "result": {"topic_id": "topic-1234567890abcdef", "status": "solved"}, "error": None})
        raise AssertionError(f"Unexpected request: {method} {req.full_url}")

    bridge = PublicHiveBridge(
        PublicHiveBridgeConfig(
            enabled=True,
            meet_seed_urls=("http://seed-eu.example.test:8766",),
            topic_target_url="http://seed-eu.example.test:8766",
            home_region="eu",
            auth_token="cluster-token",
        ),
        urlopen=fake_urlopen,
    )

    claim = bridge.claim_public_topic(
        topic_id="topic-1234567890abcdef",
        note="Working it live.",
        capability_tags=["dashboard", "ux"],
    )
    progress = bridge.post_public_topic_progress(
        topic_id="topic-1234567890abcdef",
        body="Progress: watcher now emits real task events.",
        progress_state="working",
        claim_id="claim-1",
    )
    result = bridge.submit_public_topic_result(
        topic_id="topic-1234567890abcdef",
        body="Result: claims, progress, and result submission are all wired.",
        result_status="solved",
        claim_id="claim-1",
    )

    assert claim["ok"] is True
    assert claim["claim_id"] == "claim-1"
    assert progress["ok"] is True
    assert progress["post_id"] == "post-progress-1"
    assert result["ok"] is True
    assert result["post_id"] == "post-result-1"
    assert any(url.endswith("/v1/hive/topic-status") for _method, url, _payload in seen)


def test_public_hive_bridge_submit_result_settles_solved_escrow() -> None:
    import uuid

    run_migrations()
    tag = uuid.uuid4().hex[:8]
    topic_id = f"topic-{uuid.uuid4().hex}"
    poster_peer_id = f"poster-{tag}"
    helper_peer_id = f"helper-{tag}"

    assert award_credits(poster_peer_id, 40.0, "seed", receipt_id=f"seed-{tag}")
    assert escrow_credits_for_task(
        poster_peer_id,
        topic_id,
        24.0,
        receipt_id=f"escrow:{tag}",
    )

    def fake_urlopen(req, timeout=0, context=None):
        del timeout, context
        method = req.get_method()
        payload = json.loads(req.data.decode("utf-8")) if getattr(req, "data", None) else {}
        if method == "GET" and req.full_url.endswith(f"/v1/hive/topics/{topic_id}/claims?limit=200"):
            return _FakeResponse(
                {
                    "ok": True,
                    "result": [{"claim_id": "claim-1", "agent_id": helper_peer_id, "status": "active"}],
                    "error": None,
                }
            )
        if method == "POST" and req.full_url.endswith("/v1/hive/posts"):
            return _FakeResponse({"ok": True, "result": {"post_id": "post-result-1"}, "error": None})
        if method == "POST" and req.full_url.endswith("/v1/hive/topic-status"):
            assert payload["payload"]["status"] == "solved"
            assert payload["payload"]["claim_id"] == "claim-1"
            return _FakeResponse({"ok": True, "result": {"topic_id": topic_id, "status": "solved"}, "error": None})
        raise AssertionError(f"Unexpected request: {method} {req.full_url}")

    bridge = PublicHiveBridge(
        PublicHiveBridgeConfig(
            enabled=True,
            meet_seed_urls=("http://seed-eu.example.test:8766",),
            topic_target_url="http://seed-eu.example.test:8766",
            home_region="eu",
            auth_token="cluster-token",
        ),
        urlopen=fake_urlopen,
    )

    result = bridge.submit_public_topic_result(
        topic_id=topic_id,
        body="Result: solved cleanly with accepted evidence and settlement.",
        result_status="solved",
        claim_id="claim-1",
    )

    assert result["ok"] is True
    assert result["credit_settlement"]["ok"] is True
    assert result["credit_settlement"]["status"] == "settled"
    assert result["credit_settlement"]["released_amount"] == 24.0
    assert result["credit_settlement"]["refunded_amount"] == 0.0
    assert result["credit_settlement"]["settlements"] == [
        {
            "helper_peer_id": helper_peer_id,
            "amount": 24.0,
            "receipt_id": f"hive_topic_settlement:{topic_id}:solved:0",
        }
    ]
    assert get_credit_balance(helper_peer_id) == 24.0
    escrow = get_escrow_for_task(topic_id)
    assert escrow is not None
    assert escrow["remaining"] == 0.0
    assert escrow["status"] == "settled"


def test_public_hive_bridge_submit_result_partially_settles_partial_escrow() -> None:
    import uuid

    run_migrations()
    tag = uuid.uuid4().hex[:8]
    topic_id = f"topic-{uuid.uuid4().hex}"
    poster_peer_id = f"poster-{tag}"
    helper_peer_id = f"helper-{tag}"

    assert award_credits(poster_peer_id, 30.0, "seed", receipt_id=f"partial-seed-{tag}")
    assert escrow_credits_for_task(
        poster_peer_id,
        topic_id,
        20.0,
        receipt_id=f"partial-escrow:{tag}",
    )

    def fake_urlopen(req, timeout=0, context=None):
        del timeout, context
        method = req.get_method()
        payload = json.loads(req.data.decode("utf-8")) if getattr(req, "data", None) else {}
        if method == "GET" and req.full_url.endswith(f"/v1/hive/topics/{topic_id}/claims?limit=200"):
            return _FakeResponse(
                {
                    "ok": True,
                    "result": [{"claim_id": "claim-1", "agent_id": helper_peer_id, "status": "active"}],
                    "error": None,
                }
            )
        if method == "POST" and req.full_url.endswith("/v1/hive/posts"):
            return _FakeResponse({"ok": True, "result": {"post_id": "post-result-1"}, "error": None})
        if method == "POST" and req.full_url.endswith("/v1/hive/topic-status"):
            assert payload["payload"]["status"] == "partial"
            return _FakeResponse({"ok": True, "result": {"topic_id": topic_id, "status": "partial"}, "error": None})
        raise AssertionError(f"Unexpected request: {method} {req.full_url}")

    bridge = PublicHiveBridge(
        PublicHiveBridgeConfig(
            enabled=True,
            meet_seed_urls=("http://seed-eu.example.test:8766",),
            topic_target_url="http://seed-eu.example.test:8766",
            home_region="eu",
            auth_token="cluster-token",
        ),
        urlopen=fake_urlopen,
    )

    result = bridge.submit_public_topic_result(
        topic_id=topic_id,
        body="Result: partial progress landed, but more work is still needed.",
        result_status="partial",
        claim_id="claim-1",
    )

    assert result["ok"] is True
    assert result["credit_settlement"]["ok"] is True
    assert result["credit_settlement"]["status"] == "partially_settled"
    assert result["credit_settlement"]["released_amount"] == 10.0
    assert result["credit_settlement"]["refunded_amount"] == 0.0
    assert result["credit_settlement"]["remaining"] == 10.0
    assert get_credit_balance(helper_peer_id) == 10.0
    escrow = get_escrow_for_task(topic_id)
    assert escrow is not None
    assert escrow["remaining"] == 10.0
    assert escrow["status"] == "active"


def test_public_hive_bridge_attaches_route_scoped_write_grant() -> None:
    local_peer_id = get_local_peer_id()
    post_grant = build_hive_write_grant(
        granted_to=local_peer_id,
        allowed_paths=["/v1/hive/posts"],
        topic_id="topic-1234567890abcdef",
        max_uses=5,
    )
    seen: dict[str, object] = {}

    def fake_urlopen(req, timeout=0, context=None):
        del timeout, context
        seen["payload"] = json.loads(req.data.decode("utf-8"))
        return _FakeResponse({"ok": True, "result": {"post_id": "post-1"}, "error": None})

    bridge = PublicHiveBridge(
        PublicHiveBridgeConfig(
            enabled=True,
            meet_seed_urls=("http://seed-eu.example.test:8766",),
            topic_target_url="http://seed-eu.example.test:8766",
            home_region="eu",
            auth_token="cluster-token",
            write_grants_by_base_url={
                "http://seed-eu.example.test:8766": {
                    "/v1/hive/posts": post_grant,
                }
            },
        ),
        urlopen=fake_urlopen,
    )

    bridge.post_public_topic_progress(
        topic_id="topic-1234567890abcdef",
        body="Progress: scoped grants are attached.",
        progress_state="working",
        claim_id="claim-1",
    )

    envelope = dict(seen["payload"])
    assert envelope["payload"]["write_grant"]["grant_id"] == post_grant["grant_id"]


def test_public_hive_bridge_reads_review_queue_and_submits_review() -> None:
    seen: list[tuple[str, str, dict[str, object]]] = []

    def fake_urlopen(req, timeout=0, context=None):
        del timeout, context
        method = req.get_method()
        payload = json.loads(req.data.decode("utf-8")) if getattr(req, "data", None) else {}
        seen.append((method, req.full_url, payload))
        if method == "GET" and req.full_url.endswith("/v1/hive/review-queue?limit=5&object_type=post"):
            return _FakeResponse({"ok": True, "result": [{"object_type": "post", "object_id": "post-1"}], "error": None})
        if method == "POST" and req.full_url.endswith("/v1/hive/moderation/reviews"):
            return _FakeResponse({"ok": True, "result": {"current_state": "approved", "quorum_reached": True}, "error": None})
        raise AssertionError(f"Unexpected request: {method} {req.full_url}")

    bridge = PublicHiveBridge(
        PublicHiveBridgeConfig(
            enabled=True,
            meet_seed_urls=("http://seed-eu.example.test:8766",),
            topic_target_url="http://seed-eu.example.test:8766",
            home_region="eu",
            auth_token="cluster-token",
        ),
        urlopen=fake_urlopen,
    )

    queue = bridge.list_public_review_queue(object_type="post", limit=5)
    review = bridge.submit_public_moderation_review(
        object_type="post",
        object_id="post-1",
        decision="approve",
        note="Promote after manual review.",
    )

    assert queue[0]["object_id"] == "post-1"
    assert review["quorum_reached"] is True
    assert seen[-1][2]["payload"]["decision"] == "approve"


def test_public_hive_bridge_reads_review_summary_and_updates_topic_status() -> None:
    seen: list[tuple[str, str, dict[str, object]]] = []

    def fake_urlopen(req, timeout=0, context=None):
        del timeout, context
        method = req.get_method()
        payload = json.loads(req.data.decode("utf-8")) if getattr(req, "data", None) else {}
        seen.append((method, req.full_url, payload))
        if method == "GET" and "/v1/hive/moderation/reviews?object_type=post&object_id=post-1" in req.full_url:
            return _FakeResponse(
                {
                    "ok": True,
                    "result": {
                        "object_type": "post",
                        "object_id": "post-1",
                        "current_state": "review_required",
                        "quorum_reached": False,
                        "total_reviews": 1,
                    },
                    "error": None,
                }
            )
        if method == "POST" and req.full_url.endswith("/v1/hive/topic-status"):
            return _FakeResponse({"ok": True, "result": {"topic_id": "topic-smoke-123456", "status": "closed"}, "error": None})
        raise AssertionError(f"Unexpected request: {method} {req.full_url}")

    bridge = PublicHiveBridge(
        PublicHiveBridgeConfig(
            enabled=True,
            meet_seed_urls=("http://seed-eu.example.test:8766",),
            topic_target_url="http://seed-eu.example.test:8766",
            home_region="eu",
            auth_token="cluster-token",
        ),
        urlopen=fake_urlopen,
    )

    summary = bridge.get_public_review_summary(object_type="post", object_id="post-1")
    status = bridge.update_public_topic_status(topic_id="topic-smoke-123456", status="closed", note="cleanup")

    assert summary["current_state"] == "review_required"
    assert status["ok"] is True
    assert seen[-1][2]["payload"]["status"] == "closed"


def test_public_hive_bridge_updates_topic_via_dedicated_route() -> None:
    seen: list[tuple[str, str, dict[str, object]]] = []

    def fake_urlopen(req, timeout=0, context=None):
        del timeout, context
        payload = json.loads(req.data.decode("utf-8")) if getattr(req, "data", None) else {}
        seen.append((req.get_method(), req.full_url, payload))
        if req.get_method() == "POST" and req.full_url.endswith("/v1/hive/topic-update"):
            return _FakeResponse(
                {
                    "ok": True,
                    "result": {
                        "topic_id": "topic-update-123456",
                        "title": "Standalone VOOL browser shell",
                        "summary": "Updated brief.",
                    },
                    "error": None,
                }
            )
        raise AssertionError(f"Unexpected request: {req.get_method()} {req.full_url}")

    bridge = PublicHiveBridge(
        PublicHiveBridgeConfig(
            enabled=True,
            meet_seed_urls=("http://seed-eu.example.test:8766",),
            topic_target_url="http://seed-eu.example.test:8766",
            home_region="eu",
            auth_token="cluster-token",
        ),
        urlopen=fake_urlopen,
    )

    result = bridge.update_public_topic(
        topic_id="topic-update-123456",
        title="Standalone VOOL browser shell",
        summary="Updated brief.",
        topic_tags=["standalone", "browser"],
    )

    assert result["ok"] is True
    assert seen[-1][1].endswith("/v1/hive/topic-update")
    assert seen[-1][2]["payload"]["title"] == "Standalone VOOL browser shell"


def test_public_hive_bridge_deletes_topic_via_dedicated_route() -> None:
    seen: list[tuple[str, str, dict[str, object]]] = []

    def fake_urlopen(req, timeout=0, context=None):
        del timeout, context
        payload = json.loads(req.data.decode("utf-8")) if getattr(req, "data", None) else {}
        seen.append((req.get_method(), req.full_url, payload))
        if req.get_method() == "POST" and req.full_url.endswith("/v1/hive/topic-delete"):
            return _FakeResponse(
                {
                    "ok": True,
                    "result": {
                        "topic_id": "topic-delete-123456",
                        "status": "closed",
                    },
                    "error": None,
                }
            )
        raise AssertionError(f"Unexpected request: {req.get_method()} {req.full_url}")

    bridge = PublicHiveBridge(
        PublicHiveBridgeConfig(
            enabled=True,
            meet_seed_urls=("http://seed-eu.example.test:8766",),
            topic_target_url="http://seed-eu.example.test:8766",
            home_region="eu",
            auth_token="cluster-token",
        ),
        urlopen=fake_urlopen,
    )

    result = bridge.delete_public_topic(
        topic_id="topic-delete-123456",
        note="Discard unused task.",
    )

    assert result["ok"] is True
    assert seen[-1][1].endswith("/v1/hive/topic-delete")
    assert seen[-1][2]["payload"]["topic_id"] == "topic-delete-123456"


def test_public_hive_bridge_update_reports_route_unavailable_for_stale_deploy() -> None:
    def fake_urlopen(req, timeout=0, context=None):
        del timeout, context, req
        raise RuntimeError("Unknown POST path: /v1/hive/topic-update")

    bridge = PublicHiveBridge(
        PublicHiveBridgeConfig(
            enabled=True,
            meet_seed_urls=("http://seed-eu.example.test:8766",),
            topic_target_url="http://seed-eu.example.test:8766",
            home_region="eu",
            auth_token="cluster-token",
        ),
        urlopen=fake_urlopen,
    )

    result = bridge.update_public_topic(
        topic_id="topic-update-123456",
        title="Standalone VOOL browser shell",
        summary="Updated brief.",
    )

    assert result["ok"] is False
    assert result["status"] == "route_unavailable"


def test_public_hive_bridge_update_reports_route_unavailable_for_stale_http_error() -> None:
    def fake_urlopen(req, timeout=0, context=None):
        del timeout, context, req
        raise ValueError("Unknown POST path: /v1/hive/topic-update")

    bridge = PublicHiveBridge(
        PublicHiveBridgeConfig(
            enabled=True,
            meet_seed_urls=("http://seed-eu.example.test:8766",),
            topic_target_url="http://seed-eu.example.test:8766",
            home_region="eu",
            auth_token="cluster-token",
        ),
        urlopen=fake_urlopen,
    )

    result = bridge.update_public_topic(
        topic_id="topic-update-123456",
        title="Standalone VOOL browser shell",
        summary="Updated brief.",
    )

    assert result["ok"] is False
    assert result["status"] == "route_unavailable"


def test_public_hive_bridge_delete_reports_route_unavailable_for_stale_deploy() -> None:
    def fake_urlopen(req, timeout=0, context=None):
        del timeout, context, req
        raise RuntimeError("Unknown POST path: /v1/hive/topic-delete")

    bridge = PublicHiveBridge(
        PublicHiveBridgeConfig(
            enabled=True,
            meet_seed_urls=("http://seed-eu.example.test:8766",),
            topic_target_url="http://seed-eu.example.test:8766",
            home_region="eu",
            auth_token="cluster-token",
        ),
        urlopen=fake_urlopen,
    )

    result = bridge.delete_public_topic(
        topic_id="topic-delete-123456",
        note="Discard unused task.",
    )

    assert result["ok"] is False
    assert result["status"] == "route_unavailable"


def test_public_hive_bridge_delete_reports_route_unavailable_for_stale_http_error() -> None:
    def fake_urlopen(req, timeout=0, context=None):
        del timeout, context, req
        raise ValueError("Unknown POST path: /v1/hive/topic-delete")

    bridge = PublicHiveBridge(
        PublicHiveBridgeConfig(
            enabled=True,
            meet_seed_urls=("http://seed-eu.example.test:8766",),
            topic_target_url="http://seed-eu.example.test:8766",
            home_region="eu",
            auth_token="cluster-token",
        ),
        urlopen=fake_urlopen,
    )

    result = bridge.delete_public_topic(
        topic_id="topic-delete-123456",
        note="Discard unused task.",
    )

    assert result["ok"] is False
    assert result["status"] == "route_unavailable"


def test_public_hive_bridge_default_topic_filter_keeps_partial_and_needs_improvement() -> None:
    def fake_urlopen(req, timeout=0, context=None):
        del timeout, context
        if req.get_method() == "GET" and req.full_url.endswith("/v1/hive/topics?limit=24"):
            return _FakeResponse(
                {
                    "ok": True,
                    "result": [
                        {"topic_id": "topic-partial-123456", "title": "Partial pass", "status": "partial"},
                        {"topic_id": "topic-needs-123456", "title": "Needs more work", "status": "needs_improvement"},
                        {"topic_id": "topic-closed-123456", "title": "Closed task", "status": "closed"},
                    ],
                    "error": None,
                }
            )
        raise AssertionError(f"Unexpected request: {req.get_method()} {req.full_url}")

    bridge = PublicHiveBridge(
        PublicHiveBridgeConfig(
            enabled=True,
            meet_seed_urls=("http://seed-eu.example.test:8766",),
            topic_target_url="http://seed-eu.example.test:8766",
            home_region="eu",
            auth_token="cluster-token",
        ),
        urlopen=fake_urlopen,
    )

    topics = bridge.list_public_topics()

    assert [topic["status"] for topic in topics] == ["partial", "needs_improvement"]


def test_public_hive_bridge_reads_research_queue_packet_and_artifacts() -> None:
    def fake_urlopen(req, timeout=0, context=None):
        del timeout, context
        method = req.get_method()
        if method == "GET" and req.full_url.endswith("/v1/hive/research-queue?limit=5"):
            return _FakeResponse({"ok": True, "result": [{"topic_id": "topic-1", "title": "Research queue topic"}], "error": None})
        if method == "GET" and req.full_url.endswith("/v1/hive/topics/topic-1/research-packet"):
            return _FakeResponse({"ok": True, "result": {"topic": {"topic_id": "topic-1", "title": "Research queue topic"}}, "error": None})
        if method == "GET" and "/v1/hive/artifacts/search?" in req.full_url:
            return _FakeResponse({"ok": True, "result": [{"artifact_id": "artifact-1", "title": "bundle"}], "error": None})
        raise AssertionError(f"Unexpected request: {method} {req.full_url}")

    bridge = PublicHiveBridge(
        PublicHiveBridgeConfig(
            enabled=True,
            meet_seed_urls=("http://seed-eu.example.test:8766",),
            topic_target_url="http://seed-eu.example.test:8766",
            home_region="eu",
        ),
        urlopen=fake_urlopen,
    )

    queue = bridge.list_public_research_queue(limit=5)
    packet = bridge.get_public_research_packet("topic-1")
    artifacts = bridge.search_public_artifacts(query_text="bundle", topic_id="topic-1", limit=5)

    assert queue[0]["topic_id"] == "topic-1"
    assert packet["topic"]["title"] == "Research queue topic"
    assert artifacts[0]["artifact_id"] == "artifact-1"


def test_public_hive_bridge_falls_back_when_research_queue_route_is_missing() -> None:
    def fake_urlopen(req, timeout=0, context=None):
        del timeout, context
        method = req.get_method()
        if method == "GET" and req.full_url.endswith("/v1/hive/research-queue?limit=5"):
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", hdrs=None, fp=None)
        if method == "GET" and req.full_url.endswith("/v1/hive/topics?limit=32"):
            return _FakeResponse(
                {
                    "ok": True,
                    "result": [
                        {
                            "topic_id": "topic-1",
                            "title": "Research queue topic",
                            "summary": "Need a credible first research pass.",
                            "status": "researching",
                            "topic_tags": ["agent_commons", "watcher"],
                            "created_at": "2026-03-09T20:00:00+00:00",
                            "updated_at": "2026-03-09T20:10:00+00:00",
                        }
                    ],
                    "error": None,
                }
            )
        if method == "GET" and req.full_url.endswith("/v1/hive/topics/topic-1/posts?limit=120"):
            return _FakeResponse({"ok": True, "result": [{"post_id": "post-1", "body": "First note"}], "error": None})
        if method == "GET" and req.full_url.endswith("/v1/hive/topics/topic-1/claims?limit=48"):
            return _FakeResponse({"ok": True, "result": [], "error": None})
        raise AssertionError(f"Unexpected request: {method} {req.full_url}")

    bridge = PublicHiveBridge(
        PublicHiveBridgeConfig(
            enabled=True,
            meet_seed_urls=("http://seed-eu.example.test:8766",),
            topic_target_url="http://seed-eu.example.test:8766",
            home_region="eu",
        ),
        urlopen=fake_urlopen,
    )

    queue = bridge.list_public_research_queue(limit=5)

    assert queue[0]["topic_id"] == "topic-1"
    assert queue[0]["compat_fallback"] is True
    assert queue[0]["packet_schema"] == "brain_hive.research_packet.v1"
    assert queue[0]["truth_source"] == "public_bridge"
    assert queue[0]["truth_label"] == "public-bridge-derived"
    assert queue[0]["truth_transport"] == "compat_fallback"


def test_public_hive_bridge_falls_back_when_research_packet_route_is_missing() -> None:
    def fake_urlopen(req, timeout=0, context=None):
        del timeout, context
        method = req.get_method()
        if method == "GET" and req.full_url.endswith("/v1/hive/topics/topic-1/research-packet"):
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", hdrs=None, fp=None)
        if method == "GET" and req.full_url.endswith("/v1/hive/topics/topic-1"):
            return _FakeResponse(
                {
                    "ok": True,
                    "result": {
                        "topic_id": "topic-1",
                        "title": "Research queue topic",
                        "summary": "Need a credible first research pass.",
                        "status": "researching",
                        "topic_tags": ["agent_commons", "watcher"],
                        "created_at": "2026-03-09T20:00:00+00:00",
                        "updated_at": "2026-03-09T20:10:00+00:00",
                    },
                    "error": None,
                }
            )
        if method == "GET" and req.full_url.endswith("/v1/hive/topics/topic-1/posts?limit=400"):
            return _FakeResponse(
                {
                    "ok": True,
                    "result": [{"post_id": "post-1", "post_kind": "analysis", "body": "First note"}],
                    "error": None,
                }
            )
        if method == "GET" and req.full_url.endswith("/v1/hive/topics/topic-1/claims?limit=200"):
            return _FakeResponse(
                {
                    "ok": True,
                    "result": [{"claim_id": "claim-1", "agent_id": "peer-1", "status": "active"}],
                    "error": None,
                }
            )
        raise AssertionError(f"Unexpected request: {method} {req.full_url}")

    bridge = PublicHiveBridge(
        PublicHiveBridgeConfig(
            enabled=True,
            meet_seed_urls=("http://seed-eu.example.test:8766",),
            topic_target_url="http://seed-eu.example.test:8766",
            home_region="eu",
        ),
        urlopen=fake_urlopen,
    )

    packet = bridge.get_public_research_packet("topic-1")

    assert packet["topic"]["topic_id"] == "topic-1"
    assert packet["execution_state"]["active_claim_count"] == 1
    assert packet["compat_fallback"] is True
    assert packet["truth_source"] == "public_bridge"
    assert packet["truth_label"] == "public-bridge-derived"
    assert packet["truth_transport"] == "compat_fallback"


def test_public_hive_bridge_overlays_truth_fields_when_direct_research_packet_is_stale() -> None:
    def fake_urlopen(req, timeout=0, context=None):
        del timeout, context
        method = req.get_method()
        if method == "GET" and req.full_url.endswith("/v1/hive/topics/topic-1/research-packet"):
            return _FakeResponse(
                {
                    "ok": True,
                    "result": {
                        "topic": {
                            "topic_id": "topic-1",
                            "title": "Research queue topic",
                            "summary": "Need a credible first research pass.",
                            "status": "researching",
                            "topic_tags": ["agent_commons", "watcher"],
                            "created_at": "2026-03-09T20:00:00+00:00",
                            "updated_at": "2026-03-09T20:10:00+00:00",
                        }
                    },
                    "error": None,
                }
            )
        if method == "GET" and req.full_url.endswith("/v1/hive/topics/topic-1"):
            return _FakeResponse(
                {
                    "ok": True,
                    "result": {
                        "topic_id": "topic-1",
                        "title": "Research queue topic",
                        "summary": "Need a credible first research pass.",
                        "status": "researching",
                        "topic_tags": ["agent_commons", "watcher"],
                        "created_at": "2026-03-09T20:00:00+00:00",
                        "updated_at": "2026-03-09T20:10:00+00:00",
                    },
                    "error": None,
                }
            )
        if method == "GET" and req.full_url.endswith("/v1/hive/topics/topic-1/posts?limit=400"):
            return _FakeResponse(
                {
                    "ok": True,
                    "result": [
                        {
                            "post_id": "post-1",
                            "post_kind": "summary",
                            "body": "Research synthesis card",
                            "created_at": "2026-03-09T20:11:00+00:00",
                            "evidence_refs": [
                                {
                                    "kind": "research_synthesis_card",
                                    "question": "Research queue topic",
                                    "searched": ["research queue topic implementation docs"],
                                    "found": ["Credible evidence should stay visible."],
                                    "source_domains": ["developer.apple.com"],
                                    "artifacts": [{"label": "bundle artifact-1", "state": "resolved"}],
                                    "promoted_findings": ["Credible evidence should stay visible."],
                                    "confidence": "grounded",
                                    "blockers": [],
                                    "state_token": "state-1",
                                }
                            ],
                        }
                    ],
                    "error": None,
                }
            )
        if method == "GET" and req.full_url.endswith("/v1/hive/topics/topic-1/claims?limit=200"):
            return _FakeResponse({"ok": True, "result": [], "error": None})
        raise AssertionError(f"Unexpected request: {method} {req.full_url}")

    bridge = PublicHiveBridge(
        PublicHiveBridgeConfig(
            enabled=True,
            meet_seed_urls=("http://seed-eu.example.test:8766",),
            topic_target_url="http://seed-eu.example.test:8766",
            home_region="eu",
        ),
        urlopen=fake_urlopen,
    )

    packet = bridge.get_public_research_packet("topic-1")

    assert packet["truth_transport"] == "direct_overlay"
    assert packet["latest_synthesis_card"]["question"] == "Research queue topic"
    assert "research_quality_status" in packet


def test_public_hive_bridge_overlays_truth_fields_when_direct_research_queue_is_stale() -> None:
    def fake_urlopen(req, timeout=0, context=None):
        del timeout, context
        method = req.get_method()
        if method == "GET" and req.full_url.endswith("/v1/hive/research-queue?limit=5"):
            return _FakeResponse(
                {
                    "ok": True,
                    "result": [{"topic_id": "topic-1", "title": "Research queue topic", "updated_at": "2026-03-09T20:10:00+00:00"}],
                    "error": None,
                }
            )
        if method == "GET" and req.full_url.endswith("/v1/hive/topics?limit=32"):
            return _FakeResponse(
                {
                    "ok": True,
                    "result": [
                        {
                            "topic_id": "topic-1",
                            "title": "Research queue topic",
                            "summary": "Need a credible first research pass.",
                            "status": "researching",
                            "topic_tags": ["agent_commons", "watcher"],
                            "created_at": "2026-03-09T20:00:00+00:00",
                            "updated_at": "2026-03-09T20:10:00+00:00",
                        }
                    ],
                    "error": None,
                }
            )
        if method == "GET" and req.full_url.endswith("/v1/hive/topics/topic-1/posts?limit=120"):
            return _FakeResponse({"ok": True, "result": [{"post_id": "post-1", "body": "First note"}], "error": None})
        if method == "GET" and req.full_url.endswith("/v1/hive/topics/topic-1/claims?limit=48"):
            return _FakeResponse({"ok": True, "result": [], "error": None})
        raise AssertionError(f"Unexpected request: {method} {req.full_url}")

    bridge = PublicHiveBridge(
        PublicHiveBridgeConfig(
            enabled=True,
            meet_seed_urls=("http://seed-eu.example.test:8766",),
            topic_target_url="http://seed-eu.example.test:8766",
            home_region="eu",
        ),
        urlopen=fake_urlopen,
    )

    queue = bridge.list_public_research_queue(limit=5)

    assert queue[0]["truth_transport"] == "direct_overlay"
    assert "research_quality_status" in queue[0]


def test_agent_start_uses_limited_presence_when_hive_task_intake_is_disabled() -> None:
    agent = VoolAgent(backend_name="test-backend", device="openclaw-test", persona_id="default")
    disabled_prefs = type("Prefs", (), {"accept_hive_tasks": False})()

    with mock.patch("core.agent_runtime.agent.load_preferences", return_value=disabled_prefs), mock.patch.object(
        agent.public_hive_bridge,
        "sync_presence",
        return_value={"ok": True, "status": "posted"},
    ) as sync_presence, mock.patch.object(agent, "_start_public_presence_heartbeat", return_value=None):
        agent.start()

    assert sync_presence.call_args.kwargs["status"] == "limited"


def test_agent_start_skips_background_threads_for_test_runtime() -> None:
    agent = VoolAgent(backend_name="test-backend", device="openclaw-test", persona_id="default")

    with mock.patch.object(
        agent.public_hive_bridge,
        "sync_presence",
        return_value={"ok": True, "status": "posted"},
    ) as sync_presence, mock.patch.object(
        agent, "_start_public_presence_heartbeat"
    ) as start_presence, mock.patch.object(
        agent, "_start_idle_commons_loop"
    ) as start_idle:
        agent.start()

    assert sync_presence.called
    start_presence.assert_not_called()
    start_idle.assert_not_called()


def test_agent_only_exports_public_tasks_to_public_hive() -> None:
    agent = VoolAgent(backend_name="test-backend", device="openclaw-test", persona_id="default")

    public_task = type(
        "Task",
        (),
        {
            "task_id": "task-public-1",
            "task_summary": "Research OpenClaw and Liquefy integration",
            "share_scope": "public_knowledge",
        },
    )()
    private_task = type(
        "Task",
        (),
        {
            "task_id": "task-private-1",
            "task_summary": "Research OpenClaw and Liquefy integration",
            "share_scope": "local_only",
        },
    )()

    with mock.patch.object(agent.public_hive_bridge, "publish_public_task", return_value={"ok": True}) as publish:
        agent._maybe_publish_public_task(
            task=public_task,
            classification={"task_class": "research", "topic_hints": ["openclaw", "liquefy"]},
            assistant_response="Research thread opened.",
            session_id="openclaw:public-export",
        )
        publish.assert_called_once()

    with mock.patch.object(agent.public_hive_bridge, "publish_public_task", return_value={"ok": True}) as publish:
        agent._maybe_publish_public_task(
            task=private_task,
            classification={"task_class": "research", "topic_hints": ["openclaw", "liquefy"]},
            assistant_response="Research thread opened.",
            session_id="openclaw:private-export",
        )
        publish.assert_not_called()
