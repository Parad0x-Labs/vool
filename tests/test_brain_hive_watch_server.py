from __future__ import annotations

import http.client
import json
import os
import threading
import time
import unittest
from unittest.mock import patch
from urllib import request

from apps.brain_hive_watch_server import (
    BrainHiveWatchServerConfig,
    _proxy_voolbook_get,
    build_server,
    fetch_dashboard_from_upstreams,
    fetch_topic_from_upstreams,
    fetch_topic_posts_from_upstreams,
    main,
)
from core.brain_hive_dashboard import (
    _augment_dashboard_with_trading_scanner,
    _build_trading_learning_payload,
    _safe_list_recent_topic_claims_feed,
    _safe_list_topic_claims,
    build_dashboard_snapshot,
    render_dashboard_html,
    render_topic_detail_html,
)
from core.vool_workstation_ui import VOOL_WORKSTATION_DEPLOYMENT_VERSION
from core.web.watch.app import create_watch_app
from tests.asgi_harness import asgi_request


class BrainHiveWatchServerTests(unittest.TestCase):
    def test_main_builds_runtime_config_from_env(self) -> None:
        with patch.dict(
            os.environ,
            {
                "VOOL_WATCH_HOST": "0.0.0.0",
                "VOOL_WATCH_PORT": "9911",
                "VOOL_WATCH_UPSTREAM": "http://meet-eu:8765,http://meet-us:8765",
                "VOOL_WATCH_TIMEOUT_SECONDS": "9",
                "VOOL_WATCH_AUTH_TOKEN": "watch-token",
                "VOOL_WATCH_CACHE_TTL_SECONDS": "12.5",
            },
            clear=False,
        ):
            with patch("apps.brain_hive_watch_server.serve") as serve_mock, patch(
                "argparse.ArgumentParser.parse_args",
                return_value=type(
                    "Args",
                    (),
                    {
                        "host": "0.0.0.0",
                        "port": 9911,
                        "upstream_base_urls": ["http://meet-eu:8765", "http://meet-us:8765"],
                        "timeout_seconds": 9,
                        "auth_token": "watch-token",
                        "tls_certfile": "",
                        "tls_keyfile": "",
                        "tls_ca_file": "",
                        "tls_insecure_skip_verify": False,
                        "cache_ttl_seconds": 12.5,
                    },
                )(),
            ):
                result = main()

        self.assertEqual(result, 0)
        serve_mock.assert_called_once()
        cfg = serve_mock.call_args.args[0]
        self.assertEqual(cfg.host, "0.0.0.0")
        self.assertEqual(cfg.port, 9911)
        self.assertEqual(cfg.upstream_base_urls, ("http://meet-eu:8765", "http://meet-us:8765"))
        self.assertEqual(cfg.request_timeout_seconds, 9)
        self.assertEqual(cfg.auth_token, "watch-token")
        self.assertEqual(cfg.dashboard_cache_ttl_seconds, 12.5)

    def test_create_watch_app_keeps_runtime_state_in_app(self) -> None:
        cfg = BrainHiveWatchServerConfig(
            host="127.0.0.1",
            port=8788,
            upstream_base_urls=("http://127.0.0.1:8766",),
        )

        app = create_watch_app(
            config=cfg,
            workstation_version="test-version",
            validate_tls_config=lambda inner_cfg: None,
            requires_public_tls=lambda host: False,
            watch_tls_enabled=lambda inner_cfg: False,
            fetch_dashboard_from_upstreams=lambda *args, **kwargs: {},
            fetch_topic_from_upstreams=lambda *args, **kwargs: {},
            fetch_topic_posts_from_upstreams=lambda *args, **kwargs: [],
            proxy_voolbook_get=lambda *args, **kwargs: {},
            http_get_json=lambda *args, **kwargs: {},
            normalize_base_url=lambda url: url.rstrip("/"),
            ssl_context_for_url=lambda *args, **kwargs: None,
        )

        self.assertIs(app.state.config, cfg)
        self.assertEqual(app.state.workstation_version, "test-version")
        self.assertIsInstance(app.state.dashboard_cache, dict)
        self.assertIsInstance(app.state.dashboard_cache_lock, threading.Lock().__class__)

    def test_create_watch_app_emits_request_id_header_and_echoes_client_header(self) -> None:
        cfg = BrainHiveWatchServerConfig(
            host="127.0.0.1",
            port=8788,
            upstream_base_urls=("http://127.0.0.1:8766",),
        )

        app = create_watch_app(
            config=cfg,
            workstation_version="test-version",
            validate_tls_config=lambda inner_cfg: None,
            requires_public_tls=lambda host: False,
            watch_tls_enabled=lambda inner_cfg: False,
            fetch_dashboard_from_upstreams=lambda *args, **kwargs: {},
            fetch_topic_from_upstreams=lambda *args, **kwargs: {},
            fetch_topic_posts_from_upstreams=lambda *args, **kwargs: [],
            proxy_voolbook_get=lambda *args, **kwargs: {},
            http_get_json=lambda *args, **kwargs: {},
            normalize_base_url=lambda url: url.rstrip("/"),
            ssl_context_for_url=lambda *args, **kwargs: None,
        )

        status, headers, _ = asgi_request(app, method="GET", path="/healthz", headers={"X-Request-ID": "req-watch-123"})

        self.assertEqual(status, 200)
        self.assertEqual(headers["x-request-id"], "req-watch-123")
        self.assertEqual(headers["x-correlation-id"], "req-watch-123")

    def test_serve_runs_uvicorn_with_factory_app_and_tls_settings(self) -> None:
        cfg = BrainHiveWatchServerConfig(
            host="0.0.0.0",
            port=8788,
            upstream_base_urls=("http://127.0.0.1:8766",),
            tls_certfile="/tmp/watch-cert.pem",
            tls_keyfile="/tmp/watch-key.pem",
            tls_ca_file="/tmp/watch-ca.pem",
        )

        with patch("apps.brain_hive_watch_server.create_watch_app", return_value=object()) as create_app_mock, patch(
            "uvicorn.Server"
        ) as server_cls:
            server = server_cls.return_value
            from apps.brain_hive_watch_server import serve

            serve(cfg)

        create_app_mock.assert_called_once()
        config = server_cls.call_args.args[0]
        self.assertEqual(config.host, "0.0.0.0")
        self.assertEqual(config.port, 8788)
        self.assertFalse(config.access_log)
        self.assertEqual(config.ssl_certfile, "/tmp/watch-cert.pem")
        self.assertEqual(config.ssl_keyfile, "/tmp/watch-key.pem")
        self.assertEqual(config.ssl_ca_certs, "/tmp/watch-ca.pem")
        server.run.assert_called_once_with()

    def test_fetch_dashboard_falls_back_to_second_upstream(self) -> None:
        calls: list[tuple[str, str | None]] = []

        def fake_fetch(url: str, token: str | None) -> dict:
            calls.append((url, token))
            if "seed-eu" in url:
                raise ValueError("eu unavailable")
            return {"ok": True, "result": {"stats": {"active_agents": 3}}}

        result = fetch_dashboard_from_upstreams(
            ("https://seed-eu.example.vool", "https://seed-us.example.vool"),
            auth_token="cluster-token",
            fetch_json=fake_fetch,
        )
        self.assertEqual(result["source_meet_url"], "https://seed-us.example.vool")
        self.assertEqual(result["stats"]["active_agents"], 3)
        self.assertEqual(len(calls), 2)
        self.assertEqual(
            {call[0] for call in calls},
            {
                "https://seed-eu.example.vool/v1/hive/dashboard",
                "https://seed-us.example.vool/v1/hive/dashboard",
            },
        )
        self.assertEqual({call[1] for call in calls}, {"cluster-token"})

    def test_fetch_dashboard_raises_when_all_upstreams_fail(self) -> None:
        def fake_fetch(url: str, token: str | None) -> dict:
            raise ValueError("unreachable")

        with self.assertRaisesRegex(ValueError, "All upstream meet nodes failed"):
            fetch_dashboard_from_upstreams(
                ("https://seed-eu.example.vool", "https://seed-us.example.vool"),
                fetch_json=fake_fetch,
            )

    def test_fetch_dashboard_uses_per_upstream_token_override(self) -> None:
        tokens: dict[str, str | None] = {}

        def fake_fetch(url: str, token: str | None) -> dict:
            tokens[url] = token
            return {"ok": True, "result": {"stats": {"active_agents": 1}}}

        fetch_dashboard_from_upstreams(
            ("https://seed-eu.example.vool",),
            auth_token="cluster-token",
            auth_tokens_by_base_url={"https://seed-eu.example.vool": "eu-token"},
            fetch_json=fake_fetch,
        )
        self.assertEqual(tokens["https://seed-eu.example.vool/v1/hive/dashboard"], "eu-token")

    def test_fetch_dashboard_prefers_fresher_trading_presence(self) -> None:
        def fake_fetch(url: str, token: str | None) -> dict:
            if "seed-eu" in url:
                return {
                    "ok": True,
                    "result": {
                        "generated_at": "2026-03-09T00:20:00+00:00",
                        "stats": {"active_agents": 1},
                        "trading_learning": {
                            "latest_heartbeat": {"last_tick_ts": 1773000000.0},
                            "topics": [{"updated_at": "2026-03-09T00:00:00+00:00"}],
                        },
                    },
                }
            return {
                "ok": True,
                "result": {
                    "generated_at": "2026-03-09T00:20:00+00:00",
                    "stats": {"active_agents": 1},
                    "trading_learning": {
                        "latest_heartbeat": {"last_tick_ts": 1773000000.0},
                        "topics": [{"updated_at": "2026-03-09T00:19:45+00:00"}],
                    },
                },
            }

        result = fetch_dashboard_from_upstreams(
            ("https://seed-eu.example.vool", "https://seed-us.example.vool"),
            fetch_json=fake_fetch,
        )

        self.assertEqual(result["source_meet_url"], "https://seed-us.example.vool")

    def test_fetch_dashboard_queries_upstreams_in_parallel(self) -> None:
        active_calls = 0
        peak_calls = 0
        lock = threading.Lock()

        def fake_fetch(url: str, token: str | None) -> dict:
            nonlocal active_calls, peak_calls
            with lock:
                active_calls += 1
                peak_calls = max(peak_calls, active_calls)
            try:
                time.sleep(0.05)
                return {"ok": True, "result": {"stats": {"active_agents": 1}}}
            finally:
                with lock:
                    active_calls -= 1

        fetch_dashboard_from_upstreams(
            (
                "https://seed-eu.example.vool",
                "https://seed-us.example.vool",
                "https://seed-apac.example.vool",
            ),
            fetch_json=fake_fetch,
        )

        self.assertGreaterEqual(peak_calls, 2)

    def test_proxy_voolbook_get_queries_upstreams_in_parallel_and_returns_first_ok(self) -> None:
        active_calls = 0
        peak_calls = 0
        lock = threading.Lock()

        def fake_fetch(url: str, *, timeout_seconds: int, auth_token: str | None = None, tls_ca_file: str | None = None, tls_insecure_skip_verify: bool = False) -> dict:
            nonlocal active_calls, peak_calls
            with lock:
                active_calls += 1
                peak_calls = max(peak_calls, active_calls)
            try:
                if "seed-eu" in url:
                    time.sleep(0.08)
                    return {"ok": False, "error": "slow not ok"}
                time.sleep(0.01)
                return {"ok": True, "result": {"posts": [{"post_id": "p1"}]}}
            finally:
                with lock:
                    active_calls -= 1

        with patch("apps.brain_hive_watch_server._http_get_json", side_effect=fake_fetch):
            result = _proxy_voolbook_get(
                ("https://seed-eu.example.vool", "https://seed-us.example.vool"),
                "/v1/voolbook/feed?limit=1",
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["result"]["posts"][0]["post_id"], "p1")
        self.assertGreaterEqual(peak_calls, 2)

    def test_fetch_dashboard_collapses_duplicate_visible_agents_but_keeps_raw_presence_counts(self) -> None:
        def fake_fetch(url: str, token: str | None) -> dict:
            return {
                "ok": True,
                "result": {
                    "generated_at": "2026-03-14T00:01:31+00:00",
                    "stats": {
                        "active_agents": 3,
                        "presence_agents": 3,
                        "region_stats": [{"region": "eu", "online_agents": 3}],
                    },
                    "agents": [
                        {
                            "agent_id": "agent-channel",
                            "display_name": "VOOL",
                            "online": True,
                            "status": "busy",
                            "home_region": "eu",
                            "current_region": "eu",
                            "transport_mode": "vool_agent",
                            "capabilities": ["research", "tool_router"],
                        },
                        {
                            "agent_id": "agent-bg-1",
                            "display_name": "VOOL",
                            "online": True,
                            "status": "idle",
                            "home_region": "eu",
                            "current_region": "eu",
                            "transport_mode": "background_openclaw",
                            "capabilities": ["research", "tool_router"],
                        },
                        {
                            "agent_id": "agent-bg-2",
                            "display_name": "VOOL",
                            "online": True,
                            "status": "idle",
                            "home_region": "eu",
                            "current_region": "eu",
                            "transport_mode": "background_openclaw",
                            "capabilities": ["research", "tool_router"],
                        },
                    ],
                },
            }

        result = fetch_dashboard_from_upstreams(("https://seed-eu.example.vool",), fetch_json=fake_fetch)

        self.assertEqual(result["stats"]["presence_agents"], 3)
        self.assertEqual(result["stats"]["raw_online_agents"], 3)
        self.assertEqual(result["stats"]["raw_visible_agents"], 3)
        self.assertEqual(result["stats"]["active_agents"], 1)
        self.assertEqual(result["stats"]["visible_agents"], 1)
        self.assertEqual(result["stats"]["duplicate_visible_agents"], 2)
        self.assertEqual(result["stats"]["region_stats"][0]["online_agents"], 1)
        self.assertEqual(len(result["agents"]), 1)

    def test_dashboard_snapshot_uses_topic_posts_for_trading_learning(self) -> None:
        class FakeRecord:
            def __init__(self, data: dict) -> None:
                self._data = data

            def model_dump(self, mode: str = "json") -> dict:
                return dict(self._data)

        class FakeHive:
            def get_stats(self) -> FakeRecord:
                return FakeRecord({"active_agents": 0, "task_stats": {}, "region_stats": [], "total_topics": 1, "total_posts": 0})

            def list_topics(self, *, limit: int = 100, include_flagged: bool = False, status: str | None = None):
                return [
                    FakeRecord(
                        {
                            "topic_id": "topic-1",
                            "title": "VOOL Trading Learning Desk",
                            "summary": "Manual trader desk",
                            "status": "researching",
                            "topic_tags": ["trading_learning", "manual_trader"],
                            "created_at": "2026-03-09T00:00:00+00:00",
                            "updated_at": "2026-03-09T00:40:00+00:00",
                            "created_by_agent_id": "agent-1",
                            "creator_display_name": "VOOL",
                            "creator_claim_label": "VOOL",
                            "linked_task_id": "trading-learning-manual-trader",
                        }
                    )
                ][:limit]

            def list_agent_profiles(self, *, limit: int = 100):
                return []

            def list_recent_posts_feed(self, *, limit: int = 50):
                return []

            def list_posts(self, topic_id: str, *, limit: int = 200, include_flagged: bool = False):
                if topic_id != "topic-1":
                    return []
                return [
                    FakeRecord(
                        {
                            "post_id": "post-1",
                            "topic_id": "topic-1",
                            "topic_title": "VOOL Trading Learning Desk",
                            "author_agent_id": "agent-1",
                            "author_display_name": "VOOL",
                            "author_claim_label": "VOOL",
                            "post_kind": "summary",
                            "stance": "summarize",
                            "body": "fresh trading summary",
                            "created_at": "2026-03-09T00:39:30+00:00",
                            "evidence_refs": [
                                {"kind": "trading_learning_lab_summary", "summary": {"token_learnings": 2349, "missed_opportunities": 47}},
                                {"kind": "trading_runtime_heartbeat", "heartbeat": {"last_tick_ts": 1773016770.0, "tracked_tokens": 48}},
                            ],
                        }
                    )
                ][:limit]

        summary_stub = {
            "mesh_index": {"knowledge_manifests": 0, "own_indexed_shards": 0, "remote_indexed_shards": 0},
            "learning": {
                "total_learning_shards": 0,
                "local_generated_shards": 0,
                "peer_received_shards": 0,
                "web_derived_shards": 0,
                "recent_learning": [],
                "top_problem_classes": [],
                "top_topic_tags": [],
            },
            "knowledge_lanes": {
                "private_store_shards": 0,
                "shareable_store_shards": 0,
                "legacy_unscoped_store_shards": 0,
                "candidate_rows": 0,
                "artifact_manifests": 0,
                "mesh_manifests": 0,
                "own_mesh_manifests": 0,
                "remote_mesh_manifests": 0,
                "share_scope_supported": True,
                "artifact_lane_supported": True,
            },
            "memory": {
                "local_task_count": 0,
                "finalized_response_count": 0,
                "mesh_learning_rows": 0,
                "recent_tasks": [],
                "recent_final_responses": [],
            },
        }

        with patch("core.brain_hive_dashboard.build_user_summary", return_value=summary_stub), patch(
            "core.brain_hive_dashboard.count_artifact_manifests", return_value=0
        ):
            snapshot = build_dashboard_snapshot(hive=FakeHive(), topic_limit=12, post_limit=24, agent_limit=24)

        trading = snapshot["trading_learning"]
        self.assertEqual(snapshot["knowledge_overview"]["mesh_manifests"], 0)
        self.assertEqual(trading["lab_summary"]["token_learnings"], 2349)
        self.assertEqual(trading["lab_summary"]["missed_opportunities"], 47)
        self.assertEqual(trading["latest_heartbeat"]["tracked_tokens"], 48)

    def test_dashboard_snapshot_always_reports_visible_agents(self) -> None:
        class FakeRecord:
            def __init__(self, data: dict) -> None:
                self._data = data

            def model_dump(self, mode: str = "json") -> dict:
                return dict(self._data)

        class FakeHive:
            def get_stats(self) -> FakeRecord:
                return FakeRecord({"active_agents": 1, "task_stats": {}, "region_stats": [], "total_topics": 0, "total_posts": 0})

            def list_topics(self, *, limit: int = 100, include_flagged: bool = False, status: str | None = None):
                return []

            def list_agent_profiles(self, *, limit: int = 100):
                return [
                    FakeRecord({"agent_id": "agent-online", "display_name": "VOOL", "online": True}),
                    FakeRecord({"agent_id": "agent-offline", "display_name": "VOOL-old", "online": False}),
                ][:limit]

            def list_recent_posts_feed(self, *, limit: int = 50):
                return []

        summary_stub = {
            "mesh_index": {"knowledge_manifests": 0, "own_indexed_shards": 0, "remote_indexed_shards": 0},
            "learning": {
                "total_learning_shards": 0,
                "local_generated_shards": 0,
                "peer_received_shards": 0,
                "web_derived_shards": 0,
                "recent_learning": [],
                "top_problem_classes": [],
                "top_topic_tags": [],
            },
            "knowledge_lanes": {
                "private_store_shards": 0,
                "shareable_store_shards": 0,
                "legacy_unscoped_store_shards": 0,
                "candidate_rows": 0,
                "artifact_manifests": 0,
                "mesh_manifests": 0,
                "own_mesh_manifests": 0,
                "remote_mesh_manifests": 0,
                "share_scope_supported": True,
                "artifact_lane_supported": True,
            },
            "memory": {
                "local_task_count": 0,
                "finalized_response_count": 0,
                "mesh_learning_rows": 0,
                "recent_tasks": [],
                "recent_final_responses": [],
            },
        }

        with patch("core.brain_hive_dashboard.build_user_summary", return_value=summary_stub), patch(
            "core.brain_hive_dashboard.count_artifact_manifests", return_value=0
        ):
            snapshot = build_dashboard_snapshot(hive=FakeHive(), topic_limit=12, post_limit=24, agent_limit=24)

        self.assertEqual(snapshot["stats"]["presence_agents"], 1)
        self.assertEqual(snapshot["stats"]["active_agents"], 1)
        self.assertEqual(snapshot["stats"]["visible_agents"], 2)
        self.assertEqual(len(snapshot["agents"]), 2)

    def test_dashboard_snapshot_collapses_duplicate_visible_agents_but_keeps_raw_presence_counts(self) -> None:
        class FakeRecord:
            def __init__(self, data: dict) -> None:
                self._data = data

            def model_dump(self, mode: str = "json") -> dict:
                return dict(self._data)

        class FakeHive:
            def get_stats(self) -> FakeRecord:
                return FakeRecord({"active_agents": 3, "task_stats": {}, "region_stats": [], "total_topics": 0, "total_posts": 0})

            def list_topics(self, *, limit: int = 100, include_flagged: bool = False, status: str | None = None):
                return []

            def list_agent_profiles(self, *, limit: int = 100):
                return [
                    FakeRecord({
                        "agent_id": "agent-live",
                        "display_name": "VOOL",
                        "online": True,
                        "status": "busy",
                        "home_region": "eu",
                        "transport_mode": "vool_agent",
                        "capabilities": ["research", "tool_router"],
                    }),
                    FakeRecord({
                        "agent_id": "agent-bg-1",
                        "display_name": "VOOL",
                        "online": True,
                        "status": "idle",
                        "home_region": "eu",
                        "transport_mode": "background_openclaw",
                        "capabilities": ["research", "tool_router"],
                    }),
                    FakeRecord({
                        "agent_id": "agent-bg-2",
                        "display_name": "VOOL",
                        "online": True,
                        "status": "idle",
                        "home_region": "eu",
                        "transport_mode": "background_openclaw",
                        "capabilities": ["research", "tool_router"],
                    }),
                ][:limit]

            def list_recent_posts_feed(self, *, limit: int = 50):
                return []

        summary_stub = {
            "mesh_index": {"knowledge_manifests": 0, "own_indexed_shards": 0, "remote_indexed_shards": 0},
            "learning": {
                "total_learning_shards": 0,
                "local_generated_shards": 0,
                "peer_received_shards": 0,
                "web_derived_shards": 0,
                "recent_learning": [],
                "top_problem_classes": [],
                "top_topic_tags": [],
            },
            "knowledge_lanes": {
                "private_store_shards": 0,
                "shareable_store_shards": 0,
                "legacy_unscoped_store_shards": 0,
                "candidate_rows": 0,
                "artifact_manifests": 0,
                "mesh_manifests": 0,
                "own_mesh_manifests": 0,
                "remote_mesh_manifests": 0,
                "share_scope_supported": True,
                "artifact_lane_supported": True,
            },
            "memory": {
                "local_task_count": 0,
                "finalized_response_count": 0,
                "mesh_learning_rows": 0,
                "recent_tasks": [],
                "recent_final_responses": [],
            },
        }

        with patch("core.brain_hive_dashboard.build_user_summary", return_value=summary_stub), patch(
            "core.brain_hive_dashboard.count_artifact_manifests", return_value=0
        ):
            snapshot = build_dashboard_snapshot(hive=FakeHive(), topic_limit=12, post_limit=24, agent_limit=24)

        self.assertEqual(snapshot["stats"]["presence_agents"], 3)
        self.assertEqual(snapshot["stats"]["raw_online_agents"], 3)
        self.assertEqual(snapshot["stats"]["raw_visible_agents"], 3)
        self.assertEqual(snapshot["stats"]["active_agents"], 1)
        self.assertEqual(snapshot["stats"]["visible_agents"], 1)
        self.assertEqual(snapshot["stats"]["duplicate_visible_agents"], 2)
        self.assertEqual(len(snapshot["agents"]), 1)

    def test_dashboard_html_uses_custom_api_endpoint(self) -> None:
        html = render_dashboard_html(api_endpoint="/api/dashboard", topic_base_path="/task")
        self.assertIn("/api/dashboard", html)
        self.assertIn("/task", html)
        self.assertIn("VOOL Brain Hive · Live dashboard", html)
        self.assertIn('rel="canonical" href="https://voolbook.com/hive"', html)
        self.assertIn("VOOL Operator Workstation", html)
        self.assertIn("Verified work", html)
        self.assertIn("workstation v1", html)
        self.assertIn("wk-topbar", html)
        self.assertIn(">Overview<", html)
        self.assertIn(">Hive<", html)
        self.assertIn(">Trace unavailable here<", html)
        self.assertIn('data-workstation-trace-state="not-live"', html)
        self.assertIn(">Human<", html)
        self.assertIn(">Agent<", html)
        self.assertIn(">Raw<", html)
        self.assertIn(VOOL_WORKSTATION_DEPLOYMENT_VERSION, html)
        self.assertIn('data-workstation-surface="brain-hive"', html)
        self.assertIn("https://x.com/Parad0x_Labs", html)
        self.assertIn("https://x.com/vool_ai", html)
        self.assertIn("https://github.com/Parad0x-Labs/", html)
        self.assertIn("https://discord.gg/WuqCDnyfZ8", html)
        self.assertIn("https://pump.fun/coin/8EeDdvCRmFAzVD4takkBrNNwkeUTUQh4MscRK5Fzpump", html)
        self.assertNotIn("footerCopyToken", html)
        self.assertNotIn("footerTokenLink", html)
        self.assertIn("Follow VOOL on X", html)
        self.assertIn('data-nb-route="feed">Feed<', html)
        self.assertIn('data-nb-route="tasks">Tasks<', html)
        self.assertIn('data-nb-route="agents">Agents<', html)
        self.assertIn('data-nb-route="proof">Proof<', html)
        self.assertIn('data-nb-route="hive">Hive<', html)
        self.assertIn("/hive", html)
        self.assertIn("Active learnings", html)
        self.assertIn("learningProgramList", html)
        self.assertIn("fold-card", html)
        self.assertIn("buildTradingEvidenceSummary", html)
        self.assertIn("split(/\\n+/)", html)
        self.assertIn("renderTaskEventFold", html)
        self.assertIn("workstationHomeBoard", html)
        self.assertIn("dashboard-stage", html)
        self.assertIn("dashboard-overview-grid", html)
        self.assertIn("claimStreamList", html)
        self.assertIn("recentChangeList", html)
        self.assertIn("Raw presence rows", html)
        self.assertIn("Collapsed duplicates", html)
        self.assertIn("Distinct peers online", html)
        self.assertIn("Active tasks now", html)
        self.assertIn("Recent task events", html)
        self.assertIn("Completion data", html)
        self.assertIn("Failure data", html)
        self.assertIn("Stale peer/source rows", html)
        self.assertIn("Truth / debug", html)
        self.assertIn("Old raw peer counts were misleading here because", html)
        self.assertIn("No verified completion data has reached this watcher yet.", html)
        self.assertIn("No verified failure data has reached this watcher yet.", html)
        self.assertIn("objectModelRail", html)
        self.assertIn("brainInspectorTitle", html)
        self.assertIn("brainInspectorTruth", html)
        self.assertIn("brainInspectorTruthNote", html)
        self.assertIn('href="/hive?mode=overview"', html)
        self.assertIn('data-mode-link="overview"', html)
        self.assertIn("renderWorkstationChrome", html)
        self.assertIn("function tradingPresenceState(", html)
        self.assertIn("const presenceState = tradingPresenceState(trading, data.generated_at, data.agents || []);", html)
        self.assertIn("const uiState = { openDetails: Object.create(null) };", html)
        self.assertIn("function renderInto(containerId, html, {preserveDetails = false} = {})", html)
        self.assertIn("data-open-key", html)
        self.assertIn("data-open-chip", html)
        self.assertIn("Token Trading", html)
        self.assertIn("Agent Knowledge Growth", html)
        self.assertIn("What this route is for", html)
        self.assertIn('href="/hive?mode=fabric"', html)
        self.assertIn('data-mode-link="fabric"', html)
        self.assertIn("Mesh manifests", html)

    def test_dashboard_html_public_surface_uses_shared_site_shell(self) -> None:
        html = render_dashboard_html(
            api_endpoint="/api/dashboard",
            topic_base_path="/task",
            initial_mode="fabric",
            public_surface=True,
        )
        self.assertIn("VOOL Hive · Live coordination", html)
        self.assertIn('<header class="ns-header">', html)
        self.assertIn('href="/hive?mode=fabric" data-mode-link="fabric" aria-current="page"', html)
        self.assertIn("Back to route index", html)
        self.assertIn("Live coordination", html)
        self.assertIn("Public routes", html)
        self.assertNotIn("VOOL Operator Workstation", html)
        self.assertNotIn('data-workstation-surface="brain-hive"', html)
        self.assertNotIn('<nav class="nb-topbar"', html)
        self.assertNotIn('<header class="wk-topbar"', html)

    def test_build_trading_learning_payload_extracts_learning_lab_and_flow(self) -> None:
        payload = _build_trading_learning_payload(
            topics=[
                {
                    "topic_id": "topic-1",
                    "title": "VOOL Trading Learning Desk",
                    "summary": "Manual trader desk",
                    "topic_tags": ["trading_learning", "manual_trader"],
                }
            ],
            posts=[
                {
                    "topic_id": "topic-1",
                    "topic_title": "VOOL Trading Learning Desk",
                    "created_at": "2026-03-08T21:01:32+00:00",
                    "evidence_refs": [
                        {"kind": "trading_learning_lab_summary", "summary": {"token_learnings": 2234, "discoveries": 353}},
                        {"kind": "trading_decision_funnel", "summary": {"pass": 111, "buy_rejected": 65, "buy": 0}},
                        {
                            "kind": "trading_missed_mooners",
                            "items": [{"id": 50, "token_mint": "MintA", "token_name": "TokenA", "potential_gain_pct": 250.0}],
                        },
                        {
                            "kind": "trading_hidden_edges",
                            "items": [{"id": 1, "metric": "max_price_change", "score": 0.76, "support": 277}],
                        },
                        {
                            "kind": "trading_discoveries",
                            "items": [{"id": 353, "source": "pattern_miner", "discovery": "Edge found", "ts": 1773002889.0}],
                        },
                        {
                            "kind": "trading_pattern_health",
                            "summary": {"total_patterns": 209, "by_action": [{"action": "BUY", "count": 94}]},
                        },
                        {
                            "kind": "trading_live_flow",
                            "items": [{"kind": "PASS", "token_mint": "MintA", "token_name": "TokenA", "detail": "LOW_LIQ", "ts": 1773003680.0}],
                        },
                        {
                            "kind": "trading_runtime_heartbeat",
                            "heartbeat": {"tick": 5, "tracked_tokens": 48},
                        },
                    ],
                }
            ],
        )
        self.assertEqual(payload["topic_count"], 1)
        self.assertEqual(payload["lab_summary"]["token_learnings"], 2234)
        self.assertEqual(payload["decision_funnel"]["pass"], 111)
        self.assertEqual(len(payload["missed_mooners"]), 1)
        self.assertEqual(len(payload["hidden_edges"]), 1)
        self.assertEqual(len(payload["discoveries"]), 1)
        self.assertEqual(len(payload["flow"]), 1)
        self.assertEqual(payload["pattern_health"]["total_patterns"], 209)
        self.assertEqual(payload["latest_heartbeat"]["tick"], 5)

    def test_build_trading_learning_payload_reads_compact_learning_lab_summary(self) -> None:
        payload = _build_trading_learning_payload(
            topics=[
                {
                    "topic_id": "topic-1",
                    "title": "VOOL Trading Learning Desk",
                    "summary": "Manual trader desk",
                    "topic_tags": ["trading_learning", "manual_trader"],
                }
            ],
            posts=[
                {
                    "topic_id": "topic-1",
                    "topic_title": "VOOL Trading Learning Desk",
                    "created_at": "2026-03-09T07:05:45+00:00",
                    "evidence_refs": [
                        {
                            "kind": "trading_learning_lab_summary",
                            "summary": {
                                "token_learnings": 2642,
                                "missed_opportunities": 62,
                                "discoveries": 445,
                                "hidden_edges": 20,
                                "mined_patterns": 198,
                                "learning_events": 11802,
                                "decision_funnel": {"pass": 180, "buy_rejected": 27, "buy": 0},
                                "missed_mooner_items": [{"id": 68, "token_mint": "MintMiss"}],
                                "hidden_edge_items": [{"id": 1, "metric": "max_price_change", "score": 0.76}],
                                "discovery_items": [{"id": 445, "discovery": "Edge found", "ts": 1773002889.0}],
                                "pattern_health": {"total_patterns": 198},
                                "flow_items": [{"kind": "PASS", "token_name": "TokenA", "detail": "LOW_LIQ", "ts": 1773003680.0}],
                            },
                        },
                        {"kind": "trading_runtime_heartbeat", "heartbeat": {"tick": 104, "tracked_tokens": 48}},
                    ],
                }
            ],
        )

        self.assertEqual(payload["lab_summary"]["token_learnings"], 2642)
        self.assertEqual(payload["decision_funnel"]["pass"], 180)
        self.assertEqual(len(payload["missed_mooners"]), 1)
        self.assertEqual(len(payload["hidden_edges"]), 1)
        self.assertEqual(len(payload["discoveries"]), 1)
        self.assertEqual(len(payload["flow"]), 1)
        self.assertEqual(payload["pattern_health"]["total_patterns"], 198)
        self.assertEqual(payload["latest_heartbeat"]["tick"], 104)

    def test_augment_dashboard_with_trading_scanner_adds_live_agent(self) -> None:
        stats, agents = _augment_dashboard_with_trading_scanner(
            stats={"active_agents": 0},
            agents=[],
            trading_learning={
                "latest_summary": {"total_calls": 0},
                "latest_heartbeat": {
                    "last_tick_ts": 1773014430.0,
                    "tracked_tokens": 48,
                },
            },
            generated_at="2026-03-09T00:01:31+00:00",
        )

        self.assertEqual(stats["presence_agents"], 0)
        self.assertEqual(stats["active_agents"], 1)
        self.assertEqual(stats["visible_agents"], 1)
        self.assertEqual(len(agents), 1)
        self.assertEqual(agents[0]["agent_id"], "vool:trading-scanner")
        self.assertTrue(agents[0]["online"])

    def test_augment_dashboard_with_trading_scanner_skips_old_heartbeat(self) -> None:
        stats, agents = _augment_dashboard_with_trading_scanner(
            stats={"active_agents": 0},
            agents=[],
            trading_learning={
                "latest_summary": {"total_calls": 0},
                "latest_heartbeat": {
                    "last_tick_ts": 1772990000.0,
                    "tracked_tokens": 48,
                },
            },
            generated_at="2026-03-09T00:30:00+00:00",
        )

        self.assertEqual(stats["active_agents"], 0)
        self.assertEqual(agents, [])

    def test_augment_dashboard_with_trading_scanner_falls_back_to_recent_topic_activity(self) -> None:
        stats, agents = _augment_dashboard_with_trading_scanner(
            stats={"active_agents": 0},
            agents=[],
            trading_learning={
                "latest_summary": {"total_calls": 0},
                "latest_heartbeat": {
                    "last_tick_ts": 1772990000.0,
                    "tracked_tokens": 48,
                },
                "topics": [
                    {
                        "topic_id": "topic-1",
                        "updated_at": "2026-03-09T00:01:10+00:00",
                    }
                ],
            },
            generated_at="2026-03-09T00:01:31+00:00",
        )

        self.assertEqual(stats["presence_agents"], 0)
        self.assertEqual(stats["active_agents"], 1)
        self.assertEqual(stats["visible_agents"], 1)
        self.assertEqual(len(agents), 1)
        self.assertEqual(agents[0]["agent_id"], "vool:trading-scanner")
        self.assertTrue(agents[0]["online"])

    def test_augment_dashboard_with_trading_scanner_keeps_visible_stale_topic_activity(self) -> None:
        stats, agents = _augment_dashboard_with_trading_scanner(
            stats={"active_agents": 0},
            agents=[],
            trading_learning={
                "latest_summary": {"total_calls": 0},
                "latest_heartbeat": {
                    "last_tick_ts": 1772990000.0,
                    "tracked_tokens": 48,
                },
                "topics": [
                    {
                        "topic_id": "topic-1",
                        "updated_at": "2026-03-09T00:08:00+00:00",
                    }
                ],
            },
            generated_at="2026-03-09T00:15:00+00:00",
        )

        self.assertEqual(stats["presence_agents"], 0)
        self.assertEqual(stats["active_agents"], 0)
        self.assertEqual(stats["visible_agents"], 1)
        self.assertEqual(len(agents), 1)
        self.assertFalse(agents[0]["online"])
        self.assertEqual(agents[0]["status"], "stale")

    def test_safe_list_recent_topic_claims_feed_handles_older_service(self) -> None:
        class LegacyHiveService:
            pass

        self.assertEqual(_safe_list_recent_topic_claims_feed(LegacyHiveService(), limit=48), [])

    def test_safe_list_topic_claims_handles_older_service(self) -> None:
        class LegacyHiveService:
            pass

        self.assertEqual(_safe_list_topic_claims(LegacyHiveService(), "topic-1", limit=24), [])

    def test_fetch_topic_from_upstreams_falls_back_to_second_upstream(self) -> None:
        calls: list[tuple[str, str | None]] = []

        def fake_fetch(url: str, token: str | None) -> dict:
            calls.append((url, token))
            if "seed-eu" in url:
                raise ValueError("eu unavailable")
            return {"ok": True, "result": {"topic_id": "topic-123", "title": "Agent commons: tooling"}, "error": None}

        result = fetch_topic_from_upstreams(
            ("https://seed-eu.example.vool", "https://seed-us.example.vool"),
            topic_id="topic-123",
            auth_token="cluster-token",
            fetch_json=fake_fetch,
        )
        self.assertEqual(result["topic_id"], "topic-123")
        self.assertEqual(result["source_meet_url"], "https://seed-us.example.vool")
        self.assertEqual(len(calls), 2)

    def test_fetch_topic_posts_from_upstreams_uses_matching_token_override(self) -> None:
        tokens: dict[str, str | None] = {}

        def fake_fetch(url: str, token: str | None) -> dict:
            tokens[url] = token
            return {"ok": True, "result": [{"post_id": "post-1"}], "error": None}

        result = fetch_topic_posts_from_upstreams(
            ("https://seed-eu.example.vool",),
            topic_id="topic-123",
            auth_token="cluster-token",
            auth_tokens_by_base_url={"https://seed-eu.example.vool": "eu-token"},
            fetch_json=fake_fetch,
        )
        self.assertEqual(result[0]["post_id"], "post-1")
        self.assertEqual(tokens["https://seed-eu.example.vool/v1/hive/topics/topic-123/posts?limit=120"], "eu-token")

    def test_topic_detail_html_uses_custom_endpoints(self) -> None:
        html = render_topic_detail_html(
            topic_api_endpoint="/api/topic/topic-123",
            posts_api_endpoint="/api/topic/topic-123/posts",
        )
        self.assertIn("/api/topic/topic-123", html)
        self.assertIn("/api/topic/topic-123/posts", html)
        self.assertIn("VOOL Operator Workstation", html)
        self.assertIn("VOOL Task · Live work detail", html)
        self.assertIn("wk-topbar", html)
        self.assertIn("workstation v1", html)
        self.assertIn("Back to Hive", html)
        self.assertIn(">Hive<", html)
        self.assertIn(">Trace unavailable here<", html)
        self.assertIn(">Human<", html)
        self.assertIn(">Agent<", html)
        self.assertIn(">Raw<", html)
        self.assertIn(VOOL_WORKSTATION_DEPLOYMENT_VERSION, html)
        self.assertIn('data-workstation-surface="brain-hive-topic"', html)
        self.assertIn("Showing latest", html)
        self.assertIn("buildLineStructuredSummary", html)
        self.assertIn("split(/\\n+/)", html)

    @unittest.skipUnless(os.environ.get("VOOL_LIVE_ROUTE_PROOF") == "1", "live route proof only")
    def test_watch_server_live_brain_hive_route_carries_workstation_proof_headers(self) -> None:
        server = build_server(
            BrainHiveWatchServerConfig(
                host="127.0.0.1",
                port=0,
                upstream_base_urls=("http://127.0.0.1:8766",),
            )
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = int(server.server_address[1])
            with request.urlopen(f"http://127.0.0.1:{port}/brain-hive", timeout=5) as response:
                body = response.read().decode("utf-8")
                self.assertEqual(response.headers.get("X-Vool-Workstation-Version"), VOOL_WORKSTATION_DEPLOYMENT_VERSION)
                self.assertEqual(response.headers.get("X-Vool-Workstation-Surface"), "brain-hive")
                self.assertIn(VOOL_WORKSTATION_DEPLOYMENT_VERSION, body)
                self.assertIn('data-workstation-surface="brain-hive"', body)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1)

    @unittest.skipUnless(os.environ.get("VOOL_LIVE_ROUTE_PROOF") == "1", "live route proof only")
    def test_watch_server_live_topic_route_carries_workstation_proof_headers(self) -> None:
        server = build_server(
            BrainHiveWatchServerConfig(
                host="127.0.0.1",
                port=0,
                upstream_base_urls=("http://127.0.0.1:8766",),
            )
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = int(server.server_address[1])
            with request.urlopen(f"http://127.0.0.1:{port}/brain-hive/topic/topic-123", timeout=5) as response:
                body = response.read().decode("utf-8")
                self.assertEqual(response.headers.get("X-Vool-Workstation-Version"), VOOL_WORKSTATION_DEPLOYMENT_VERSION)
                self.assertEqual(response.headers.get("X-Vool-Workstation-Surface"), "brain-hive-topic")
                self.assertIn(VOOL_WORKSTATION_DEPLOYMENT_VERSION, body)
                self.assertIn('data-workstation-surface="brain-hive-topic"', body)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1)

    def test_watch_server_root_renders_landing_page(self) -> None:
        server = build_server(
            BrainHiveWatchServerConfig(
                host="127.0.0.1",
                port=0,
                upstream_base_urls=("http://127.0.0.1:8766",),
            )
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = int(server.server_address[1])
            with request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as response:
                body = response.read().decode("utf-8")
                self.assertIn("One system. One lane.", body)
                self.assertIn("Get VOOL", body)
                self.assertNotIn("VOOL Brain Hive", body)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1)

    def test_watch_server_feed_route_renders_feed_surface(self) -> None:
        server = build_server(
            BrainHiveWatchServerConfig(
                host="127.0.0.1",
                port=0,
                upstream_base_urls=("http://127.0.0.1:8766",),
            )
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = int(server.server_address[1])
            with request.urlopen(f"http://127.0.0.1:{port}/feed", timeout=5) as response:
                body = response.read().decode("utf-8")
                self.assertIn("let activeTab = 'feed'", body)
                self.assertIn("window.location.origin + '/feed?post='", body)
                self.assertIn('href="/feed" data-tab="feed" class="is-active" aria-current="page">Worklog<', body)
                self.assertIn('href="/#public-routes">Back to route index</a>', body)
                self.assertIn('href="/feed?view=recent">Recent<', body)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1)

    def test_watch_server_public_surface_routes_render_expected_shells(self) -> None:
        server = build_server(
            BrainHiveWatchServerConfig(
                host="127.0.0.1",
                port=0,
                upstream_base_urls=("http://127.0.0.1:8766",),
            )
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = int(server.server_address[1])
            route_expectations = (
                ("/", "One system. One lane."),
                ("/agents", "let activeTab = 'agents'"),
                ("/tasks", "let activeTab = 'tasks'"),
                ("/proof", "let activeTab = 'proof'"),
                ("/status", "VOOL Status"),
                ("/hive", "Back to route index"),
                ("/agent/TestBot", "Public summary"),
                ("/task/topic-123", "Agent work flow"),
            )
            for path, marker in route_expectations:
                with self.subTest(path=path):
                    with request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as response:
                        body = response.read().decode("utf-8")
                        self.assertEqual(response.status, 200)
                        self.assertIn(marker, body)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1)

    def test_watch_server_head_supports_public_routes(self) -> None:
        server = build_server(
            BrainHiveWatchServerConfig(
                host="127.0.0.1",
                port=0,
                upstream_base_urls=("http://127.0.0.1:8766",),
            )
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = int(server.server_address[1])
            for path in (
                "/",
                "/feed",
                "/tasks",
                "/agents",
                "/proof",
                "/status",
                "/hive",
                "/hive?mode=fabric",
                "/brain-hive",
                "/task/topic-123",
                "/agent/TestBot",
                "/health",
                "/api/dashboard",
            ):
                with self.subTest(path=path):
                    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                    conn.request("HEAD", path)
                    response = conn.getresponse()
                    body = response.read()
                    self.assertEqual(response.status, 200)
                    self.assertEqual(body, b"")
                    self.assertGreaterEqual(int(response.getheader("Content-Length") or "0"), 0)
                    conn.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1)

    def test_watch_server_redirects_alternate_public_host_to_canonical_host(self) -> None:
        server = build_server(
            BrainHiveWatchServerConfig(
                host="127.0.0.1",
                port=0,
                upstream_base_urls=("http://127.0.0.1:8766",),
            )
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = int(server.server_address[1])
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/feed?view=recent", headers={"Host": "www.voolbook.com"})
            response = conn.getresponse()
            body = response.read()
            self.assertEqual(response.status, 308)
            self.assertEqual(response.getheader("Location"), "https://voolbook.com/feed?view=recent")
            self.assertEqual(body, b"")
            conn.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1)

    def test_watch_server_hive_modes_are_shareable(self) -> None:
        server = build_server(
            BrainHiveWatchServerConfig(
                host="127.0.0.1",
                port=0,
                upstream_base_urls=("http://127.0.0.1:8766",),
            )
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = int(server.server_address[1])
            with request.urlopen(f"http://127.0.0.1:{port}/hive?mode=fabric", timeout=5) as response:
                body = response.read().decode("utf-8")
                self.assertEqual(response.status, 200)
                self.assertIn('rel="canonical" href="https://voolbook.com/hive?mode=fabric"', body)
                self.assertIn('href="/hive?mode=overview"', body)
                self.assertIn('href="/hive?mode=fabric"', body)
                self.assertIn('data-mode-link="fabric"', body)
                self.assertIn("Back to route index", body)
                self.assertIn("Live coordination", body)
                self.assertIn("Trace unavailable", body)
                self.assertNotIn('<header class="wk-topbar"', body)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1)

    def test_watch_server_post_og_url_uses_feed_canonical_route(self) -> None:
        server = build_server(
            BrainHiveWatchServerConfig(
                host="127.0.0.1",
                port=0,
                upstream_base_urls=("https://seed-eu.example.vool",),
            )
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch(
                "apps.brain_hive_watch_server._http_get_json",
                return_value={
                    "ok": True,
                    "result": {
                        "posts": [
                            {
                                "post_id": "post-123",
                                "content": "Visible proof drop",
                                "author": {"display_name": "VOOL", "handle": "VOOL"},
                            }
                        ]
                    },
                },
            ):
                port = int(server.server_address[1])
                with request.urlopen(f"http://127.0.0.1:{port}/feed?post=post-123", timeout=5) as response:
                    body = response.read().decode("utf-8")
                    self.assertIn("https://voolbook.com/feed?post=post-123", body)
                    self.assertNotIn("https://voolbook.com/?post=post-123", body)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1)

    def test_build_server_rejects_partial_tls_config(self) -> None:
        with self.assertRaises(ValueError):
            build_server(
                BrainHiveWatchServerConfig(
                    host="127.0.0.1",
                    port=0,
                    tls_certfile="/tmp/fake-cert.pem",
                    tls_keyfile=None,
                )
            )

    def test_public_watch_bind_requires_tls(self) -> None:
        with self.assertRaisesRegex(ValueError, "require TLS"):
            build_server(
                BrainHiveWatchServerConfig(
                    host="0.0.0.0",
                    port=8788,
                    upstream_base_urls=("http://127.0.0.1:8766",),
                )
            )

    def test_public_watch_bind_with_tls_is_allowed(self) -> None:
        class FakeTlsContext:
            def wrap_socket(self, socket_obj, *, server_side: bool = False):
                return ("wrapped", socket_obj, server_side)

        class FakeServer:
            def __init__(self) -> None:
                self.socket = object()

        fake_server = FakeServer()
        with patch("apps.brain_hive_watch_server.ThreadingHTTPServer", return_value=fake_server), patch(
            "apps.brain_hive_watch_server._build_tls_context",
            return_value=FakeTlsContext(),
        ):
            server = build_server(
                BrainHiveWatchServerConfig(
                    host="0.0.0.0",
                    port=8788,
                    upstream_base_urls=("http://127.0.0.1:8766",),
                    tls_certfile="/tmp/fake-cert.pem",
                    tls_keyfile="/tmp/fake-key.pem",
                )
            )

        self.assertIs(server, fake_server)
        self.assertEqual(fake_server.socket[0], "wrapped")
        self.assertTrue(fake_server.socket[2])

    def test_dashboard_endpoint_reuses_short_cache(self) -> None:
        calls = {"count": 0}

        def fake_fetch(*_args, **_kwargs):
            calls["count"] += 1
            return {
                "stats": {"active_agents": 2},
                "topics": [{"topic_id": "topic-1", "title": "Fresh cache topic"}],
            }

        try:
            server = build_server(
                BrainHiveWatchServerConfig(
                    host="127.0.0.1",
                    port=0,
                    upstream_base_urls=("http://127.0.0.1:8766",),
                    dashboard_cache_ttl_seconds=60.0,
                )
            )
        except PermissionError:
            self.skipTest("Local socket binds are not permitted in this sandbox.")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        with patch("apps.brain_hive_watch_server.fetch_dashboard_from_upstreams", side_effect=fake_fetch):
            thread.start()
            try:
                port = int(server.server_address[1])
                with request.urlopen(f"http://127.0.0.1:{port}/api/dashboard", timeout=5) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                with request.urlopen(f"http://127.0.0.1:{port}/api/dashboard", timeout=5) as response:
                    cached_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=1)

        self.assertEqual(calls["count"], 1)
        self.assertEqual(payload["cache_state"], "miss")
        self.assertEqual(cached_payload["cache_state"], "hit")
        self.assertEqual(cached_payload["result"]["topics"][0]["title"], "Fresh cache topic")

    def test_dashboard_endpoint_serves_stale_cache_when_upstream_fails(self) -> None:
        calls = {"count": 0}

        def fake_fetch(*_args, **_kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                return {
                    "stats": {"active_agents": 1},
                    "topics": [{"topic_id": "topic-1", "title": "Cached topic"}],
                }
            raise ValueError("upstream unavailable")

        try:
            server = build_server(
                BrainHiveWatchServerConfig(
                    host="127.0.0.1",
                    port=0,
                    upstream_base_urls=("http://127.0.0.1:8766",),
                    dashboard_cache_ttl_seconds=0.01,
                )
            )
        except PermissionError:
            self.skipTest("Local socket binds are not permitted in this sandbox.")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        with patch("apps.brain_hive_watch_server.fetch_dashboard_from_upstreams", side_effect=fake_fetch):
            thread.start()
            try:
                port = int(server.server_address[1])
                with request.urlopen(f"http://127.0.0.1:{port}/api/dashboard", timeout=5) as response:
                    first_payload = json.loads(response.read().decode("utf-8"))
                time.sleep(0.03)
                with request.urlopen(f"http://127.0.0.1:{port}/api/dashboard", timeout=5) as response:
                    stale_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=1)

        self.assertEqual(calls["count"], 2)
        self.assertEqual(first_payload["cache_state"], "miss")
        self.assertEqual(stale_payload["cache_state"], "stale_fallback")
        self.assertEqual(stale_payload["result"]["topics"][0]["title"], "Cached topic")
