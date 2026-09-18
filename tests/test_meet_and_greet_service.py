from __future__ import annotations

import http.client
import importlib
import json
import ssl
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import Thread
from unittest.mock import patch

import apps.meet_and_greet_server as _server_mod
import core.api_write_auth as _api_write_auth_mod
import network.protocol as _protocol_mod
import network.signer as _signer_mod
from apps.meet_and_greet_server import (
    MeetAndGreetServerConfig,
    MeetMetricsCollector,
    _allow_write,
    _query_int,
    _resolve_write_rate_limit,
    build_server,
    dispatch_request,
    resolve_static_route,
)
from apps.meet_and_greet_server import (
    main as meet_main,
)
from core.brain_hive_artifacts import store_artifact_manifest
from core.discovery_index import (
    delivery_targets_for_peer,
    note_verified_peer_endpoint_delivery_result,
    record_verified_peer_endpoint_proof,
    register_peer_endpoint,
    selected_verified_endpoint_for_peer,
    verified_endpoints_for_peer,
)
from core.hive_write_grants import build_hive_write_grant
from core.meet_and_greet_models import (
    KnowledgeSearchRequest,
    MeetNodeRegisterRequest,
    PaymentStatusUpsertRequest,
    PeerEndpointRecord,
    PresenceUpsertRequest,
    PresenceWithdrawRequest,
)
from core.meet_and_greet_service import MeetAndGreetConfig, MeetAndGreetService
from core.web.meet.app import create_meet_app
from network.knowledge_models import KnowledgeAdvert
from storage.db import get_connection, reset_default_connection
from storage.migrations import run_migrations
from tests.asgi_harness import asgi_request


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MeetAndGreetServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_default_connection()
        run_migrations()
        _clear_meet_tables()
        self.service = MeetAndGreetService(MeetAndGreetConfig(local_region="eu"))

    def test_presence_registration_records_endpoint_and_delta(self) -> None:
        peer_id = f"peer-{uuid.uuid4().hex}{uuid.uuid4().hex}"
        record = self.service.register_presence(
            PresenceUpsertRequest(
                agent_id=peer_id,
                agent_name="Thomas",
                status="idle",
                capabilities=["research"],
                home_region="eu",
                current_region="eu",
                transport_mode="lan_only",
                trust_score=0.6,
                timestamp=_now(),
                lease_seconds=180,
                endpoint=PeerEndpointRecord(host="127.0.0.1", port=49152, source="api"),
            )
        )
        self.assertEqual(record.agent_id, peer_id)
        self.assertIsNotNone(record.endpoint)
        deltas = self.service.get_deltas(limit=20)
        self.assertTrue(any(delta.delta_type == "presence_register" and delta.peer_id == peer_id for delta in deltas))

    def test_signed_presence_registration_persists_api_endpoint_proof_and_endpoints_list(self) -> None:
        peer_id = _signer_mod.get_local_peer_id()
        payload = {
            "agent_id": peer_id,
            "agent_name": "Thomas",
            "status": "idle",
            "capabilities": ["research"],
            "home_region": "eu",
            "current_region": "eu",
            "transport_mode": "lan_only",
            "trust_score": 0.6,
            "timestamp": _now().isoformat(),
            "lease_seconds": 180,
            "endpoints": [
                {"host": "127.0.0.1", "port": 49152, "source": "observed"},
            ],
        }
        envelope = _api_write_auth_mod.build_signed_write_envelope(
            target_path="/v1/presence/register",
            payload=payload,
        )
        request_payload, request_meta = _api_write_auth_mod.unwrap_signed_write_with_meta(
            target_path="/v1/presence/register",
            raw_payload=envelope,
        )

        record = self.service.register_presence(
            PresenceUpsertRequest.model_validate(request_payload),
            request_meta=request_meta,
        )

        self.assertIsNotNone(record.endpoint)
        self.assertEqual((record.endpoint.host, record.endpoint.port, record.endpoint.source), ("127.0.0.1", 49152, "api"))
        self.assertEqual([(item.host, item.port, item.source) for item in record.endpoints], [("127.0.0.1", 49152, "api")])
        selected = selected_verified_endpoint_for_peer(peer_id)
        self.assertIsNotNone(selected)
        self.assertEqual(selected.source, "api")
        self.assertEqual(selected.verification_kind, "signed_api_write")
        self.assertTrue(selected.proof_hash)
        endpoints = verified_endpoints_for_peer(peer_id)
        self.assertEqual(len(endpoints), 1)

    def test_presence_record_prefers_delivery_order_over_verified_alias_order(self) -> None:
        peer_id = f"peer-{uuid.uuid4().hex}{uuid.uuid4().hex}"
        self.service.register_presence(
            PresenceUpsertRequest(
                agent_id=peer_id,
                agent_name="Thomas",
                status="idle",
                capabilities=["research"],
                home_region="eu",
                current_region="eu",
                transport_mode="lan_only",
                trust_score=0.6,
                timestamp=_now(),
                lease_seconds=180,
            )
        )

        register_peer_endpoint(peer_id, "198.51.100.40", 49440, source="self")
        register_peer_endpoint(peer_id, "198.51.100.41", 49441, source="bootstrap")
        record_verified_peer_endpoint_proof(
            peer_id,
            "198.51.100.41",
            49441,
            source="bootstrap",
            verification_kind="bootstrap_snapshot",
            proof_message_id="bootstrap-proof",
            proof_timestamp="2026-03-26T12:00:00+00:00",
        )
        note_verified_peer_endpoint_delivery_result(peer_id, "198.51.100.40", 49440, delivered=True)

        record = self.service.get_presence_record(peer_id)

        self.assertIsNotNone(record.endpoint)
        self.assertEqual((record.endpoint.host, record.endpoint.port, record.endpoint.source), ("198.51.100.40", 49440, "self"))
        self.assertEqual(
            [(item.host, item.port, item.source) for item in record.endpoints[:2]],
            [("198.51.100.40", 49440, "self"), ("198.51.100.41", 49441, "bootstrap")],
        )
        targets = delivery_targets_for_peer(peer_id, verified_limit=2, include_candidates=False)
        self.assertEqual(
            [(item.host, item.port, item.source) for item in targets[:2]],
            [("198.51.100.40", 49440, "self"), ("198.51.100.41", 49441, "bootstrap")],
        )

    def test_presence_heartbeat_preserves_agent_name(self) -> None:
        peer_id = f"peer-{uuid.uuid4().hex}{uuid.uuid4().hex}"
        self.service.register_presence(
            PresenceUpsertRequest(
                agent_id=peer_id,
                agent_name="VOOL",
                status="idle",
                capabilities=["research"],
                home_region="eu",
                current_region="eu",
                transport_mode="openclaw_api",
                trust_score=0.6,
                timestamp=_now(),
                lease_seconds=180,
            )
        )
        record = self.service.heartbeat_presence(
            PresenceUpsertRequest(
                agent_id=peer_id,
                agent_name="VOOL",
                status="idle",
                capabilities=["research"],
                home_region="eu",
                current_region="eu",
                transport_mode="openclaw_api",
                trust_score=0.6,
                timestamp=_now(),
                lease_seconds=180,
            )
        )
        self.assertEqual(record.agent_name, "VOOL")

    def test_knowledge_advertisement_search_and_snapshot(self) -> None:
        shard_id = f"shard-{uuid.uuid4().hex}{uuid.uuid4().hex}"
        peer_id = f"peer-{uuid.uuid4().hex}{uuid.uuid4().hex}"
        advert = KnowledgeAdvert(
            shard_id=shard_id,
            content_hash=shard_id,
            version=1,
            holder_peer_id=peer_id,
            home_region="eu",
            topic_tags=["telegram", "routing", "bot"],
            summary_digest="telegram-routing-digest",
            size_bytes=128,
            freshness_ts=_now(),
            ttl_seconds=900,
            trust_weight=0.7,
            access_mode="public",
            fetch_methods=["request_shard"],
            fetch_route={"method": "request_shard", "shard_id": shard_id},
            metadata={"problem_class": "python_telegram"},
            manifest_id=f"manifest-{uuid.uuid4().hex}",
        )
        entry = self.service.advertise_knowledge(advert)
        self.assertEqual(entry.shard_id, shard_id)

        matches = self.service.search_knowledge(
            KnowledgeSearchRequest(
                query_text="telegram bot routing",
                problem_class="python_telegram",
                topic_tags=["telegram", "routing"],
                min_trust_weight=0.5,
                limit=5,
            )
        )
        self.assertTrue(any(item.shard_id == shard_id for item in matches))

        snapshot = self.service.get_snapshot()
        self.assertTrue(any(item.shard_id == shard_id for item in snapshot.knowledge_index))

    def test_global_summary_hides_remote_presence_endpoint(self) -> None:
        peer_id = f"peer-{uuid.uuid4().hex}{uuid.uuid4().hex}"
        self.service.register_presence(
            PresenceUpsertRequest(
                agent_id=peer_id,
                agent_name="RemoteNode",
                status="idle",
                capabilities=["research"],
                home_region="us",
                current_region="us",
                transport_mode="wan_direct",
                trust_score=0.6,
                timestamp=_now(),
                lease_seconds=180,
                endpoint=PeerEndpointRecord(host="198.51.100.10", port=49152, source="api"),
            )
        )
        records = self.service.list_presence(target_region="eu", summary_mode="global_summary", limit=20)
        remote = next(item for item in records if item.agent_id == peer_id)
        self.assertTrue(remote.summary_only)
        self.assertIsNone(remote.endpoint)
        self.assertEqual(remote.home_region, "us")

    def test_global_summary_collapses_remote_holders_to_region_pointer(self) -> None:
        shard_id = f"shard-{uuid.uuid4().hex}{uuid.uuid4().hex}"
        for idx in range(2):
            advert = KnowledgeAdvert(
                shard_id=shard_id,
                content_hash=shard_id,
                version=1,
                holder_peer_id=f"peer-us-{idx}-{uuid.uuid4().hex}{uuid.uuid4().hex}",
                home_region="us",
                topic_tags=["telegram", "routing"],
                summary_digest="digest-telegram-routing",
                size_bytes=128,
                freshness_ts=_now(),
                ttl_seconds=900,
                trust_weight=0.7,
                access_mode="public",
                fetch_methods=["request_shard"],
                fetch_route={"method": "request_shard", "shard_id": shard_id},
                metadata={"problem_class": "python_telegram"},
                manifest_id=f"manifest-{uuid.uuid4().hex}",
            )
            self.service.advertise_knowledge(advert)

        entry = self.service.get_knowledge_entry(shard_id, target_region="eu", summary_mode="global_summary")
        self.assertEqual(entry.summary_mode, "global_summary")
        self.assertEqual(entry.region_replication_counts.get("us"), 2)
        self.assertEqual(len(entry.holders), 1)
        self.assertTrue(entry.holders[0].summary_only)
        self.assertEqual(entry.holders[0].fetch_route["method"], "meet_lookup")
        self.assertEqual(entry.holders[0].fetch_route["region"], "us")

    def test_payment_status_marker_is_stored(self) -> None:
        task_id = f"task-{uuid.uuid4().hex}"
        record = self.service.upsert_payment_status(
            PaymentStatusUpsertRequest(
                task_or_transfer_id=task_id,
                payer_peer_id=f"payer-{uuid.uuid4().hex}{uuid.uuid4().hex}",
                payee_peer_id=f"payee-{uuid.uuid4().hex}{uuid.uuid4().hex}",
                status="reserved",
                receipt_reference="receipt-1",
                metadata={"currency": "DNA", "amount_units": 25},
            )
        )
        self.assertEqual(record.task_or_transfer_id, task_id)
        self.assertEqual(record.status, "reserved")
        self.assertTrue(any(item.task_or_transfer_id == task_id for item in self.service.list_payment_status(limit=20)))

    def test_withdraw_presence_marks_peer_offline(self) -> None:
        peer_id = f"peer-{uuid.uuid4().hex}{uuid.uuid4().hex}"
        self.service.register_presence(
            PresenceUpsertRequest(
                agent_id=peer_id,
                agent_name="Maria",
                status="idle",
                capabilities=["validation"],
                home_region="eu",
                current_region="eu",
                transport_mode="lan_only",
                trust_score=0.55,
                timestamp=_now(),
                lease_seconds=180,
                endpoint=PeerEndpointRecord(host="127.0.0.1", port=49153, source="api"),
            )
        )
        self.service.withdraw_presence(
            PresenceWithdrawRequest(
                agent_id=peer_id,
                reason="manual_shutdown",
                timestamp=_now(),
            )
        )
        active = self.service.list_presence(limit=20)
        self.assertFalse(any(item.agent_id == peer_id for item in active))

    def test_create_meet_app_keeps_runtime_state_in_app(self) -> None:
        metrics = MeetMetricsCollector()
        hive_service = _server_mod.BrainHiveService()
        config = MeetAndGreetServerConfig(host="127.0.0.1", port=8766, auth_token="token")

        app = create_meet_app(
            config=config,
            service=self.service,
            hive_service=hive_service,
            metrics=metrics,
        )

        self.assertIs(app.state.config, config)
        self.assertIs(app.state.service, self.service)
        self.assertIs(app.state.hive_service, hive_service)
        self.assertIs(app.state.metrics, metrics)
        self.assertTrue(hasattr(app.state, "write_windows"))
        self.assertTrue(hasattr(app.state, "write_lock"))
        self.assertTrue(callable(app.state.policy_get))

    def test_create_meet_app_emits_request_id_header_and_echoes_client_header(self) -> None:
        app = create_meet_app(
            config=MeetAndGreetServerConfig(host="127.0.0.1", port=8766, auth_token="token"),
            service=self.service,
            hive_service=_server_mod.BrainHiveService(),
            metrics=MeetMetricsCollector(),
        )

        status, headers, _ = asgi_request(app, method="GET", path="/v1/health", headers={"X-Request-ID": "req-meet-123"})

        self.assertEqual(status, 200)
        self.assertEqual(headers["x-request-id"], "req-meet-123")
        self.assertEqual(headers["x-correlation-id"], "req-meet-123")

    def test_create_meet_app_serves_readyz(self) -> None:
        app = create_meet_app(
            config=MeetAndGreetServerConfig(host="127.0.0.1", port=8766, auth_token="token"),
            service=self.service,
            hive_service=_server_mod.BrainHiveService(),
            metrics=MeetMetricsCollector(),
        )

        status, headers, body = asgi_request(app, method="GET", path="/v1/readyz", headers={"X-Request-ID": "req-meet-ready"})

        self.assertEqual(status, 200)
        self.assertEqual(headers["x-request-id"], "req-meet-ready")
        payload = json.loads(body.decode("utf-8"))
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["result"]["status"], "ready")

    def test_register_meet_node_is_listed(self) -> None:
        node_id = f"seed-{uuid.uuid4().hex[:8]}"
        record = self.service.register_meet_node(
            MeetNodeRegisterRequest(
                node_id=node_id,
                base_url="https://seed.example.test",
                region="eu",
                role="seed",
                platform_hint="linux",
                priority=10,
                status="active",
                metadata={"notes": "primary"},
            )
        )
        self.assertEqual(record.node_id, node_id)
        self.assertTrue(any(item.node_id == node_id for item in self.service.list_meet_nodes(limit=20, active_only=False)))


class MeetAndGreetServerDispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        importlib.reload(_signer_mod)
        importlib.reload(_protocol_mod)
        importlib.reload(_api_write_auth_mod)
        importlib.reload(_server_mod)
        reset_default_connection()
        run_migrations()
        _clear_meet_tables()
        self.service = MeetAndGreetService()

    def test_health_and_presence_roundtrip(self) -> None:
        health_code, health = dispatch_request("GET", "/v1/health", {}, None, self.service)
        self.assertEqual(health_code, 200)
        self.assertTrue(health["ok"])

        ready_code, ready = dispatch_request("GET", "/v1/readyz", {}, None, self.service)
        self.assertEqual(ready_code, 200)
        self.assertTrue(ready["ok"])
        self.assertEqual(ready["result"]["status"], "ready")

        peer_id = f"peer-{uuid.uuid4().hex}{uuid.uuid4().hex}"
        payload = {
            "agent_id": peer_id,
            "agent_name": "NodeA",
            "status": "idle",
            "capabilities": ["research"],
            "home_region": "eu",
            "current_region": "eu",
            "transport_mode": "lan_only",
            "trust_score": 0.6,
            "timestamp": _now().isoformat(),
            "lease_seconds": 180,
            "endpoint": {"host": "127.0.0.1", "port": 49160, "source": "api"},
        }
        status_code, result = dispatch_request("POST", "/v1/presence/register", {}, payload, self.service)
        self.assertEqual(status_code, 200)
        self.assertTrue(result["ok"])

        active_code, active = dispatch_request("GET", "/v1/presence/active", {}, None, self.service)
        self.assertEqual(active_code, 200)
        self.assertTrue(active["ok"])
        self.assertTrue(any(item["agent_id"] == peer_id for item in active["result"]))

    def test_cluster_node_dispatch_roundtrip(self) -> None:
        node_id = f"seed-{uuid.uuid4().hex[:8]}"
        payload = {
            "node_id": node_id,
            "base_url": "https://seed.example.test",
            "region": "eu",
            "role": "seed",
            "platform_hint": "linux",
            "priority": 10,
            "status": "active",
            "metadata": {"notes": "primary"},
        }
        status_code, result = dispatch_request("POST", "/v1/cluster/nodes", {}, payload, self.service)
        self.assertEqual(status_code, 200)
        self.assertTrue(result["ok"])

        list_code, listed = dispatch_request("GET", "/v1/cluster/nodes", {}, None, self.service)
        self.assertEqual(list_code, 200)
        self.assertTrue(any(item["node_id"] == node_id for item in listed["result"]))

    def test_brain_hive_dispatch_roundtrip(self) -> None:
        agent_id = f"peer-{uuid.uuid4().hex}{uuid.uuid4().hex}"
        topic_payload = {
            "created_by_agent_id": agent_id,
            "title": "Agent commons for Telegram design",
            "summary": "Agents compare Telegram bot designs and evidence sources.",
            "topic_tags": ["telegram", "design"],
            "status": "open",
            "visibility": "agent_public",
            "evidence_mode": "candidate_only",
        }
        create_code, created = dispatch_request("POST", "/v1/hive/topics", {}, topic_payload, self.service)
        self.assertEqual(create_code, 200)
        self.assertTrue(created["ok"])
        topic_id = created["result"]["topic_id"]

        post_payload = {
            "topic_id": topic_id,
            "author_agent_id": agent_id,
            "post_kind": "analysis",
            "stance": "support",
            "body": "Prefer official documentation and signed bot event handling.",
            "evidence_refs": [{"type": "url", "value": "https://core.telegram.org"}],
        }
        post_code, post_result = dispatch_request("POST", "/v1/hive/posts", {}, post_payload, self.service)
        self.assertEqual(post_code, 200)
        self.assertTrue(post_result["ok"])

        claim_payload = {
            "agent_id": agent_id,
            "platform": "x",
            "handle": "sls_0x",
            "owner_label": "Operator",
            "visibility": "public",
            "verified_state": "self_declared",
        }
        claim_code, claim_result = dispatch_request("POST", "/v1/hive/claim-links", {}, claim_payload, self.service)
        self.assertEqual(claim_code, 200)
        self.assertTrue(claim_result["ok"])

        topic_code, topic_result = dispatch_request("GET", f"/v1/hive/topics/{topic_id}", {}, None, self.service)
        self.assertEqual(topic_code, 200)
        self.assertEqual(topic_result["result"]["topic_id"], topic_id)

        posts_code, posts_result = dispatch_request("GET", f"/v1/hive/topics/{topic_id}/posts", {}, None, self.service)
        self.assertEqual(posts_code, 200)
        self.assertEqual(len(posts_result["result"]), 1)

        agents_code, agents_result = dispatch_request("GET", "/v1/hive/agents", {}, None, self.service)
        self.assertEqual(agents_code, 200)
        profile = next(item for item in agents_result["result"] if item["agent_id"] == agent_id)
        self.assertIn("@sls_0x", profile.get("claim_label") or "")

        stats_code, stats_result = dispatch_request("GET", "/v1/hive/stats", {}, None, self.service)
        self.assertEqual(stats_code, 200)
        self.assertGreaterEqual(stats_result["result"]["total_topics"], 1)
        self.assertGreaterEqual(stats_result["result"]["total_posts"], 1)

        dashboard_code, dashboard_result = dispatch_request("GET", "/v1/hive/dashboard", {}, None, self.service)
        self.assertEqual(dashboard_code, 200)
        self.assertTrue(dashboard_result["ok"])
        self.assertTrue(dashboard_result["result"]["topics"])
        self.assertTrue(dashboard_result["result"]["recent_posts"])
        self.assertIn("task_event_stream", dashboard_result["result"])
        self.assertIn("learning_lab", dashboard_result["result"])

        topic_claim_code, topic_claim_result = dispatch_request(
            "POST",
            "/v1/hive/topic-claims",
            {},
            {
                "topic_id": topic_id,
                "agent_id": agent_id,
                "status": "active",
                "note": "Working the watcher task flow.",
                "capability_tags": ["dashboard", "ux"],
            },
            self.service,
        )
        self.assertEqual(topic_claim_code, 200)
        self.assertTrue(topic_claim_result["ok"])
        claim_id = topic_claim_result["result"]["claim_id"]

        claims_code, claims_result = dispatch_request("GET", f"/v1/hive/topics/{topic_id}/claims", {}, None, self.service)
        self.assertEqual(claims_code, 200)
        self.assertEqual(len(claims_result["result"]), 1)

        status_code, status_result = dispatch_request(
            "POST",
            "/v1/hive/topic-status",
            {},
            {
                "topic_id": topic_id,
                "updated_by_agent_id": agent_id,
                "status": "solved",
                "note": "Implementation landed.",
                "claim_id": claim_id,
            },
            self.service,
        )
        self.assertEqual(status_code, 200)
        self.assertEqual(status_result["result"]["status"], "solved")

        events_code, events_result = dispatch_request("GET", "/v1/hive/events", {}, None, self.service)
        self.assertEqual(events_code, 200)
        self.assertTrue(events_result["result"])

    def test_hive_commons_dispatch_roundtrip(self) -> None:
        author_id = f"peer-{uuid.uuid4().hex}{uuid.uuid4().hex}"
        endorser_id = f"peer-{uuid.uuid4().hex}{uuid.uuid4().hex}"
        citer_id = f"peer-{uuid.uuid4().hex}{uuid.uuid4().hex}"
        reviewer_a = f"peer-{uuid.uuid4().hex}{uuid.uuid4().hex}"
        reviewer_b = f"peer-{uuid.uuid4().hex}{uuid.uuid4().hex}"
        create_code, created = dispatch_request(
            "POST",
            "/v1/hive/topics",
            {},
            {
                "created_by_agent_id": author_id,
                "title": "Agent Commons: promotion lane",
                "summary": "Commons posts should become reviewable only after multi-agent confirmation.",
                "topic_tags": ["agent_commons", "research", "brainstorm"],
                "status": "open",
                "visibility": "agent_public",
                "evidence_mode": "candidate_only",
            },
            self.service,
        )
        self.assertEqual(create_code, 200)
        topic_id = created["result"]["topic_id"]

        post_code, post_result = dispatch_request(
            "POST",
            "/v1/hive/posts",
            {},
            {
                "topic_id": topic_id,
                "author_agent_id": author_id,
                "post_kind": "summary",
                "stance": "propose",
                "body": "Promote evidence-backed commons posts only when multiple agents confirm they are worth review.",
                "evidence_refs": [
                    {"type": "url", "value": "https://example.test/commons-spec"},
                    {"artifact_id": "artifact-commons-route"},
                ],
            },
            self.service,
        )
        self.assertEqual(post_code, 200)
        post_id = post_result["result"]["post_id"]

        endorse_code, endorse_result = dispatch_request(
            "POST",
            "/v1/hive/commons/endorsements",
            {},
            {
                "post_id": post_id,
                "agent_id": endorser_id,
                "endorsement_kind": "endorse",
            },
            self.service,
        )
        self.assertEqual(endorse_code, 200)
        self.assertEqual(endorse_result["result"]["endorsement_kind"], "endorse")

        cite_code, cite_result = dispatch_request(
            "POST",
            "/v1/hive/commons/endorsements",
            {},
            {
                "post_id": post_id,
                "agent_id": citer_id,
                "endorsement_kind": "cite",
                "note": "Backed by prior agent review.",
            },
            self.service,
        )
        self.assertEqual(cite_code, 200)
        self.assertEqual(cite_result["result"]["endorsement_kind"], "cite")

        comment_code, comment_result = dispatch_request(
            "POST",
            "/v1/hive/commons/comments",
            {},
            {
                "post_id": post_id,
                "author_agent_id": citer_id,
                "body": "This is strong enough to enter the review queue.",
            },
            self.service,
        )
        self.assertEqual(comment_code, 200)
        self.assertEqual(comment_result["result"]["post_id"], post_id)

        candidate_code, candidate_result = dispatch_request(
            "POST",
            "/v1/hive/commons/promotion-candidates",
            {},
            {
                "post_id": post_id,
                "requested_by_agent_id": author_id,
            },
            self.service,
        )
        self.assertEqual(candidate_code, 200)
        self.assertEqual(candidate_result["result"]["status"], "review_required")
        candidate_id = candidate_result["result"]["candidate_id"]

        list_code, list_result = dispatch_request(
            "GET",
            "/v1/hive/commons/promotion-candidates",
            {"status": ["review_required"]},
            None,
            self.service,
        )
        self.assertEqual(list_code, 200)
        self.assertTrue(any(item["candidate_id"] == candidate_id for item in list_result["result"]))

        endorsements_code, endorsements_result = dispatch_request(
            "GET",
            f"/v1/hive/commons/posts/{post_id}/endorsements",
            {},
            None,
            self.service,
        )
        self.assertEqual(endorsements_code, 200)
        self.assertEqual(len(endorsements_result["result"]), 2)

        comments_code, comments_result = dispatch_request(
            "GET",
            f"/v1/hive/commons/posts/{post_id}/comments",
            {},
            None,
            self.service,
        )
        self.assertEqual(comments_code, 200)
        self.assertEqual(len(comments_result["result"]), 1)

        review_code_a, review_result_a = dispatch_request(
            "POST",
            "/v1/hive/commons/promotion-reviews",
            {},
            {
                "candidate_id": candidate_id,
                "reviewer_agent_id": reviewer_a,
                "decision": "approve",
                "note": "Looks durable enough.",
            },
            self.service,
        )
        self.assertEqual(review_code_a, 200)
        self.assertEqual(review_result_a["result"]["review_state"], "pending")

        review_code_b, review_result_b = dispatch_request(
            "POST",
            "/v1/hive/commons/promotion-reviews",
            {},
            {
                "candidate_id": candidate_id,
                "reviewer_agent_id": reviewer_b,
                "decision": "approve",
                "note": "Second reviewer agrees.",
            },
            self.service,
        )
        self.assertEqual(review_code_b, 200)
        self.assertEqual(review_result_b["result"]["review_state"], "approved")

        promote_code, promote_result = dispatch_request(
            "POST",
            "/v1/hive/commons/promotions",
            {},
            {
                "candidate_id": candidate_id,
                "promoted_by_agent_id": author_id,
            },
            self.service,
        )
        self.assertEqual(promote_code, 200)
        self.assertIn("commons_promoted", promote_result["result"]["topic_tags"])

    def test_hive_research_queue_packet_and_artifact_search_routes(self) -> None:
        agent_id = f"peer-{uuid.uuid4().hex}{uuid.uuid4().hex}"
        create_code, created = dispatch_request(
            "POST",
            "/v1/hive/topics",
            {},
            {
                "created_by_agent_id": agent_id,
                "title": "VOOL Trading Learning Desk",
                "summary": "Manual trader research desk.",
                "topic_tags": ["trading_learning", "manual_trader"],
                "status": "researching",
                "visibility": "read_public",
                "evidence_mode": "mixed",
            },
            self.service,
        )
        self.assertEqual(create_code, 200)
        topic_id = created["result"]["topic_id"]
        dispatch_request(
            "POST",
            "/v1/hive/posts",
            {},
            {
                "topic_id": topic_id,
                "author_agent_id": agent_id,
                "post_kind": "analysis",
                "stance": "support",
                "body": "Posted hidden edges and flow.",
                "evidence_refs": [
                    {"kind": "trading_hidden_edges", "items": [{"metric": "max_price_change", "score": 0.81, "support": 277}]},
                    {"kind": "trading_live_flow", "items": [{"detail": "LOW_LIQ", "kind": "PASS", "ts": 1773003680.0}]},
                ],
            },
            self.service,
        )
        with tempfile.TemporaryDirectory() as tmp_dir, patch("core.liquefy_bridge._VOOL_VAULT", Path(tmp_dir)):
            store_artifact_manifest(
                source_kind="research_bundle",
                title="Autonomous research bundle",
                summary="Compressed trading research bundle.",
                payload={"topic_id": topic_id, "heuristics": ["max_price_change"]},
                topic_id=topic_id,
                tags=["trading_learning"],
            )

        queue_code, queue_result = dispatch_request("GET", "/v1/hive/research-queue", {}, None, self.service)
        self.assertEqual(queue_code, 200)
        self.assertTrue(any(item["topic_id"] == topic_id for item in queue_result["result"]))

        packet_code, packet_result = dispatch_request("GET", f"/v1/hive/topics/{topic_id}/research-packet", {}, None, self.service)
        self.assertEqual(packet_code, 200)
        self.assertEqual(packet_result["result"]["topic"]["topic_id"], topic_id)

        artifact_code, artifact_result = dispatch_request(
            "GET",
            "/v1/hive/artifacts/search",
            {"q": ["trading"], "topic_id": [topic_id]},
            None,
            self.service,
        )
        self.assertEqual(artifact_code, 200)
        self.assertEqual(len(artifact_result["result"]), 1)

    def test_hive_review_queue_and_review_summary_routes(self) -> None:
        agent_id = f"peer-{uuid.uuid4().hex}{uuid.uuid4().hex}"
        reviewer_a = f"peer-{uuid.uuid4().hex}{uuid.uuid4().hex}"
        reviewer_b = f"peer-{uuid.uuid4().hex}{uuid.uuid4().hex}"
        create_code, created = dispatch_request(
            "POST",
            "/v1/hive/topics",
            {},
            {
                "created_by_agent_id": agent_id,
                "title": "Social claim review",
                "summary": "Agent compares low-trust social evidence before promotion.",
                "topic_tags": ["social", "review"],
                "status": "open",
            },
            self.service,
        )
        self.assertEqual(create_code, 200)
        topic_id = created["result"]["topic_id"]
        post_code, post_result = dispatch_request(
            "POST",
            "/v1/hive/posts",
            {},
            {
                "topic_id": topic_id,
                "author_agent_id": agent_id,
                "post_kind": "analysis",
                "stance": "support",
                "body": "Lead only from social post https://x.com/example/status/1",
                "evidence_refs": [{"type": "url", "value": "https://x.com/example/status/1"}],
            },
            self.service,
        )
        self.assertEqual(post_code, 200)
        post_id = post_result["result"]["post_id"]

        queue_code, queue_payload = dispatch_request("GET", "/v1/hive/review-queue", {}, None, self.service)
        self.assertEqual(queue_code, 200)
        self.assertTrue(any(item["object_id"] == post_id for item in queue_payload["result"]))

        dispatch_request(
            "POST",
            "/v1/hive/moderation/reviews",
            {},
            {
                "object_type": "post",
                "object_id": post_id,
                "reviewer_agent_id": reviewer_a,
                "decision": "approve",
                "note": "Accept after bounded manual review.",
            },
            self.service,
        )
        review_code, review_payload = dispatch_request(
            "POST",
            "/v1/hive/moderation/reviews",
            {},
            {
                "object_type": "post",
                "object_id": post_id,
                "reviewer_agent_id": reviewer_b,
                "decision": "approve",
                "note": "Second reviewer confirms.",
            },
            self.service,
        )
        self.assertEqual(review_code, 200)
        self.assertTrue(review_payload["result"]["quorum_reached"])

        summary_code, summary_payload = dispatch_request(
            "GET",
            "/v1/hive/moderation/reviews",
            {"object_type": ["post"], "object_id": [post_id]},
            None,
            self.service,
        )
        self.assertEqual(summary_code, 200)
        self.assertEqual(summary_payload["result"]["current_state"], "approved")

    def test_hive_posts_include_flagged_query_exposes_review_required_posts(self) -> None:
        agent_id = f"peer-{uuid.uuid4().hex}{uuid.uuid4().hex}"
        topic_code, topic_payload = dispatch_request(
            "POST",
            "/v1/hive/topics",
            {},
            {
                "created_by_agent_id": agent_id,
                "title": "Evidence quality review",
                "summary": "Compare low-trust social leads before promotion.",
                "topic_tags": ["social", "evidence"],
                "status": "open",
            },
            self.service,
        )
        self.assertEqual(topic_code, 200)
        topic_id = topic_payload["result"]["topic_id"]
        dispatch_request(
            "POST",
            "/v1/hive/posts",
            {},
            {
                "topic_id": topic_id,
                "author_agent_id": agent_id,
                "post_kind": "analysis",
                "stance": "support",
                "body": "Is this project legit? Story lead only from https://x.com/example/status/1",
                "evidence_refs": [{"type": "url", "value": "https://x.com/example/status/1"}],
            },
            self.service,
        )

        hidden_code, hidden_payload = dispatch_request(
            "GET",
            f"/v1/hive/topics/{topic_id}/posts",
            {},
            None,
            self.service,
        )
        flagged_code, flagged_payload = dispatch_request(
            "GET",
            f"/v1/hive/topics/{topic_id}/posts",
            {"include_flagged": ["1"]},
            None,
            self.service,
        )
        self.assertEqual(hidden_code, 200)
        self.assertEqual(flagged_code, 200)
        self.assertEqual(hidden_payload["result"], [])
        self.assertEqual(len(flagged_payload["result"]), 1)

    def test_static_brain_hive_dashboard_route_renders_html(self) -> None:
        response = resolve_static_route("/brain-hive")
        self.assertIsNotNone(response)
        status_code, content_type, body = response or (500, "", b"")
        self.assertEqual(status_code, 200)
        self.assertIn("text/html", content_type)
        decoded = body.decode("utf-8")
        self.assertIn("VOOL Brain Hive", decoded)
        self.assertIn("/v1/hive/dashboard", decoded)

    def test_static_hive_dashboard_route_renders_html(self) -> None:
        response = resolve_static_route("/hive")
        self.assertIsNotNone(response)
        status_code, content_type, body = response or (500, "", b"")
        self.assertEqual(status_code, 200)
        self.assertIn("text/html", content_type)
        decoded = body.decode("utf-8")
        self.assertIn("VOOL Hive · Live coordination", decoded)
        self.assertIn("Back to route index", decoded)
        self.assertIn('href="/hive?mode=overview"', decoded)
        self.assertIn('href="/status"', decoded)

    def test_static_status_route_renders_html(self) -> None:
        response = resolve_static_route("/status")
        self.assertIsNotNone(response)
        status_code, content_type, body = response or (500, "", b"")
        self.assertEqual(status_code, 200)
        self.assertIn("text/html", content_type)
        decoded = body.decode("utf-8")
        self.assertIn("VOOL Status", decoded)
        self.assertIn("What is real. What is rough. What is not ready.", decoded)

    def test_static_root_route_renders_landing_page(self) -> None:
        response = resolve_static_route("/")
        self.assertIsNotNone(response)
        status_code, content_type, body = response or (500, "", b"")
        self.assertEqual(status_code, 200)
        self.assertIn("text/html", content_type)
        decoded = body.decode("utf-8")
        self.assertIn("One system. One lane.", decoded)
        self.assertIn("Get VOOL", decoded)
        self.assertIn('href="/feed"', decoded)
        self.assertIn('href="/hive"', decoded)
        self.assertNotIn("VOOL Brain Hive", decoded)

    def test_static_feed_route_renders_feed_surface(self) -> None:
        response = resolve_static_route("/feed")
        self.assertIsNotNone(response)
        status_code, content_type, body = response or (500, "", b"")
        self.assertEqual(status_code, 200)
        self.assertIn("text/html", content_type)
        decoded = body.decode("utf-8")
        self.assertIn("let activeTab = 'feed'", decoded)
        self.assertIn("window.location.origin + '/feed?post='", decoded)

    def test_static_agents_route_renders_agents_surface(self) -> None:
        response = resolve_static_route("/agents")
        self.assertIsNotNone(response)
        status_code, content_type, body = response or (500, "", b"")
        self.assertEqual(status_code, 200)
        self.assertIn("text/html", content_type)
        decoded = body.decode("utf-8")
        self.assertIn("let activeTab = 'agents'", decoded)
        self.assertIn('href="/feed"', decoded)

    def test_static_tasks_route_renders_tasks_surface(self) -> None:
        response = resolve_static_route("/tasks")
        self.assertIsNotNone(response)
        status_code, content_type, body = response or (500, "", b"")
        self.assertEqual(status_code, 200)
        self.assertIn("text/html", content_type)
        decoded = body.decode("utf-8")
        self.assertIn("let activeTab = 'tasks'", decoded)
        self.assertIn("No task activity", decoded)

    def test_static_proof_route_renders_proof_surface(self) -> None:
        response = resolve_static_route("/proof")
        self.assertIsNotNone(response)
        status_code, content_type, body = response or (500, "", b"")
        self.assertEqual(status_code, 200)
        self.assertIn("text/html", content_type)
        decoded = body.decode("utf-8")
        self.assertIn("let activeTab = 'proof'", decoded)
        self.assertIn("Verified work", decoded)

    def test_static_agent_route_renders_profile_surface(self) -> None:
        response = resolve_static_route("/agent/TestBot")
        self.assertIsNotNone(response)
        status_code, content_type, body = response or (500, "", b"")
        self.assertEqual(status_code, 200)
        self.assertIn("text/html", content_type)
        decoded = body.decode("utf-8")
        self.assertIn("/v1/voolbook/profile/", decoded)
        self.assertIn("Public summary", decoded)

    def test_static_task_route_renders_topic_surface(self) -> None:
        response = resolve_static_route("/task/topic-123")
        self.assertIsNotNone(response)
        status_code, content_type, body = response or (500, "", b"")
        self.assertEqual(status_code, 200)
        self.assertIn("text/html", content_type)
        decoded = body.decode("utf-8")
        self.assertIn("/v1/hive/topics/topic-123", decoded)
        self.assertIn("Agent work flow", decoded)

    def test_static_brain_hive_topic_detail_route_renders_html(self) -> None:
        response = resolve_static_route("/brain-hive/topic/topic-123")
        self.assertIsNotNone(response)
        status_code, content_type, body = response or (500, "", b"")
        self.assertEqual(status_code, 200)
        self.assertIn("text/html", content_type)
        decoded = body.decode("utf-8")
        self.assertIn("/v1/hive/topics/topic-123", decoded)
        self.assertIn("/v1/hive/topics/topic-123/posts", decoded)

    def test_public_bind_requires_auth_token(self) -> None:
        with self.assertRaises(ValueError):
            build_server(MeetAndGreetServerConfig(host="0.0.0.0", port=8766), service=self.service)

    def test_public_bind_requires_auth_for_api_get(self) -> None:
        try:
            server = build_server(
                MeetAndGreetServerConfig(host="0.0.0.0", port=0, auth_token="test-token"),
                service=self.service,
            )
        except PermissionError:
            self.skipTest("Local socket binds are not permitted in this sandbox.")
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            _host, port = server.server_address
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/v1/cluster/nodes")
            response = conn.getresponse()
            self.assertEqual(response.status, 401)
            conn.close()

            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/v1/cluster/nodes", headers={"X-Vool-Meet-Token": "test-token"})
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            conn.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2.0)

    def test_write_rate_limiter_blocks_after_limit(self) -> None:
        windows = {}
        import threading

        self.assertTrue(_allow_write("127.0.0.1", 2, windows, threading.Lock()))
        self.assertTrue(_allow_write("127.0.0.1", 2, windows, threading.Lock()))
        self.assertFalse(_allow_write("127.0.0.1", 2, windows, threading.Lock()))

    def test_signed_hive_writes_use_peer_scoped_rate_limit_bucket(self) -> None:
        with patch("apps.meet_and_greet_server.policy_engine.get", return_value=600):
            bucket, limit = _resolve_write_rate_limit(
                "0.0.0.0",
                "/v1/hive/posts",
                client_host="198.51.100.8",
                request_meta={"signer_peer_id": "peer-abc"},
                default_limit=120,
            )
        self.assertEqual(bucket, "hive:peer-abc:/v1/hive/posts")
        self.assertEqual(limit, 600)

    def test_unsigned_writes_keep_client_host_bucket(self) -> None:
        bucket, limit = _resolve_write_rate_limit(
            "0.0.0.0",
            "/v1/presence/register",
            client_host="198.51.100.8",
            request_meta={},
            default_limit=120,
        )
        self.assertEqual(bucket, "198.51.100.8")
        self.assertEqual(limit, 120)

    def test_http_server_requires_signed_write_envelope(self) -> None:
        try:
            server = _server_mod.build_server(
                _server_mod.MeetAndGreetServerConfig(host="127.0.0.1", port=0, require_signed_writes=True),
                service=self.service,
            )
        except PermissionError:
            self.skipTest("Local socket binds are not permitted in this sandbox.")
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address
            conn = http.client.HTTPConnection(host, port, timeout=5)
            unsigned_payload = json.dumps(
                {
                    "created_by_agent_id": f"peer-{uuid.uuid4().hex}{uuid.uuid4().hex}",
                    "title": "Unsigned write should fail",
                    "summary": "This should not be accepted without a signed envelope.",
                    "topic_tags": ["security"],
                    "status": "open",
                    "visibility": "agent_public",
                    "evidence_mode": "candidate_only",
                }
            )
            conn.request("POST", "/v1/hive/topics", body=unsigned_payload, headers={"Content-Type": "application/json"})
            response = conn.getresponse()
            self.assertEqual(response.status, 400)
            conn.close()

            signed_payload = _api_write_auth_mod.build_signed_write_envelope(
                target_path="/v1/hive/topics",
                payload={
                    "created_by_agent_id": _signer_mod.get_local_peer_id(),
                    "title": "Signed write accepted",
                    "summary": "Signed write should pass through the live HTTP server.",
                    "topic_tags": ["security"],
                    "status": "open",
                    "visibility": "agent_public",
                    "evidence_mode": "candidate_only",
                },
            )
            conn = http.client.HTTPConnection(host, port, timeout=5)
            conn.request("POST", "/v1/hive/topics", body=json.dumps(signed_payload), headers={"Content-Type": "application/json"})
            response = conn.getresponse()
            body = json.loads(response.read().decode("utf-8"))
            self.assertEqual(response.status, 200)
            self.assertTrue(body["ok"])
            conn.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2.0)

    def test_http_server_rejects_spoofed_topic_update_actor(self) -> None:
        try:
            server = _server_mod.build_server(
                _server_mod.MeetAndGreetServerConfig(host="127.0.0.1", port=0, require_signed_writes=True),
                service=self.service,
            )
        except PermissionError:
            self.skipTest("Local socket binds are not permitted in this sandbox.")
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address
            spoofed_actor_id = f"peer-{uuid.uuid4().hex}{uuid.uuid4().hex}"
            signed_payload = _api_write_auth_mod.build_signed_write_envelope(
                target_path="/v1/hive/topic-update",
                payload={
                    "topic_id": "topic-1234567890abcdef",
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
            self.assertEqual(response.status, 403)
            self.assertIn("updated_by_agent_id", str(body.get("error") or "").lower())
            conn.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2.0)

    def test_http_server_rejects_spoofed_topic_delete_actor(self) -> None:
        try:
            server = _server_mod.build_server(
                _server_mod.MeetAndGreetServerConfig(host="127.0.0.1", port=0, require_signed_writes=True),
                service=self.service,
            )
        except PermissionError:
            self.skipTest("Local socket binds are not permitted in this sandbox.")
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address
            spoofed_actor_id = f"peer-{uuid.uuid4().hex}{uuid.uuid4().hex}"
            signed_payload = _api_write_auth_mod.build_signed_write_envelope(
                target_path="/v1/hive/topic-delete",
                payload={
                    "topic_id": "topic-1234567890abcdef",
                    "deleted_by_agent_id": spoofed_actor_id,
                    "note": "Spoofed delete attempt.",
                },
            )
            conn = http.client.HTTPConnection(host, port, timeout=5)
            conn.request(
                "POST",
                "/v1/hive/topic-delete",
                body=json.dumps(signed_payload),
                headers={"Content-Type": "application/json"},
            )
            response = conn.getresponse()
            body = json.loads(response.read().decode("utf-8"))
            self.assertEqual(response.status, 403)
            self.assertIn("deleted_by_agent_id", str(body.get("error") or "").lower())
            conn.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2.0)

    def test_http_server_rejects_spoofed_commons_actor(self) -> None:
        try:
            server = _server_mod.build_server(
                _server_mod.MeetAndGreetServerConfig(host="127.0.0.1", port=0, require_signed_writes=True),
                service=self.service,
            )
        except PermissionError:
            self.skipTest("Local socket binds are not permitted in this sandbox.")
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address
            spoofed_actor_id = f"peer-{uuid.uuid4().hex}{uuid.uuid4().hex}"
            signed_payload = _api_write_auth_mod.build_signed_write_envelope(
                target_path="/v1/hive/commons/comments",
                payload={
                    "post_id": "post-1234567890abcdef",
                    "author_agent_id": spoofed_actor_id,
                    "body": "Spoofed commons comment attempt.",
                },
            )
            conn = http.client.HTTPConnection(host, port, timeout=5)
            conn.request(
                "POST",
                "/v1/hive/commons/comments",
                body=json.dumps(signed_payload),
                headers={"Content-Type": "application/json"},
            )
            response = conn.getresponse()
            body = json.loads(response.read().decode("utf-8"))
            self.assertEqual(response.status, 403)
            self.assertIn("author_agent_id", str(body.get("error") or "").lower())
            conn.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2.0)

    def test_http_server_rejects_spoofed_voolbook_register_actor(self) -> None:
        try:
            server = _server_mod.build_server(
                _server_mod.MeetAndGreetServerConfig(host="127.0.0.1", port=0, require_signed_writes=True),
                service=self.service,
            )
        except PermissionError:
            self.skipTest("Local socket binds are not permitted in this sandbox.")
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address
            spoofed_peer_id = f"peer-{uuid.uuid4().hex}{uuid.uuid4().hex}"
            signed_payload = _api_write_auth_mod.build_signed_write_envelope(
                target_path="/v1/voolbook/register",
                payload={
                    "peer_id": spoofed_peer_id,
                    "handle": f"spoof_{uuid.uuid4().hex[:8]}",
                },
            )
            conn = http.client.HTTPConnection(host, port, timeout=5)
            conn.request(
                "POST",
                "/v1/voolbook/register",
                body=json.dumps(signed_payload),
                headers={"Content-Type": "application/json"},
            )
            response = conn.getresponse()
            body = json.loads(response.read().decode("utf-8"))
            self.assertEqual(response.status, 403)
            self.assertIn("peer_id", str(body.get("error") or "").lower())
            conn.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2.0)

    def test_http_server_rejects_voolbook_post_token_identity_mismatch(self) -> None:
        from core.voolbook_identity import register_voolbook_account

        local_peer_id = _signer_mod.get_local_peer_id()
        local_reg = register_voolbook_account(f"owner_{uuid.uuid4().hex[:8]}", peer_id=local_peer_id)
        other_reg = register_voolbook_account(
            f"other_{uuid.uuid4().hex[:8]}",
            peer_id=f"peer-{uuid.uuid4().hex}{uuid.uuid4().hex}",
        )
        try:
            server = _server_mod.build_server(
                _server_mod.MeetAndGreetServerConfig(host="127.0.0.1", port=0, require_signed_writes=True),
                service=self.service,
            )
        except PermissionError:
            self.skipTest("Local socket binds are not permitted in this sandbox.")
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address
            mismatched_payload = _api_write_auth_mod.build_signed_write_envelope(
                target_path="/v1/voolbook/post",
                payload={
                    "voolbook_peer_id": local_peer_id,
                    "content": "Signed social post with mismatched token should fail.",
                },
            )
            conn = http.client.HTTPConnection(host, port, timeout=5)
            conn.request(
                "POST",
                "/v1/voolbook/post",
                body=json.dumps(mismatched_payload),
                headers={
                    "Content-Type": "application/json",
                    "X-VoolBook-Token": other_reg.token,
                },
            )
            response = conn.getresponse()
            body = json.loads(response.read().decode("utf-8"))
            self.assertEqual(response.status, 403)
            self.assertIn("identity mismatch", str(body.get("error") or "").lower())
            conn.close()

            valid_payload = _api_write_auth_mod.build_signed_write_envelope(
                target_path="/v1/voolbook/post",
                payload={
                    "voolbook_peer_id": local_peer_id,
                    "content": "Signed social post with matching token should pass.",
                },
            )
            conn = http.client.HTTPConnection(host, port, timeout=5)
            conn.request(
                "POST",
                "/v1/voolbook/post",
                body=json.dumps(valid_payload),
                headers={
                    "Content-Type": "application/json",
                    "X-VoolBook-Token": local_reg.token,
                },
            )
            response = conn.getresponse()
            body = json.loads(response.read().decode("utf-8"))
            self.assertEqual(response.status, 200)
            self.assertTrue(body["ok"])
            self.assertEqual(body["result"]["peer_id"], local_peer_id)
            self.assertEqual(body["result"]["origin_kind"], "human")
            self.assertEqual(body["result"]["origin_channel"], "voolbook_token")
            conn.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2.0)

    def test_http_server_sets_voolbook_post_provenance_from_auth_channel(self) -> None:
        from core.voolbook_identity import register_voolbook_account

        local_peer_id = _signer_mod.get_local_peer_id()
        reg = register_voolbook_account(f"owner_{uuid.uuid4().hex[:8]}", peer_id=local_peer_id)
        try:
            server = _server_mod.build_server(
                _server_mod.MeetAndGreetServerConfig(host="127.0.0.1", port=0, require_signed_writes=True),
                service=self.service,
            )
        except PermissionError:
            self.skipTest("Local socket binds are not permitted in this sandbox.")
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address

            ai_payload = _api_write_auth_mod.build_signed_write_envelope(
                target_path="/v1/voolbook/post",
                payload={
                    "voolbook_peer_id": local_peer_id,
                    "content": "Autonomous agent post.",
                    "origin_kind": "human",
                    "origin_channel": "spoofed",
                    "origin_peer_id": "spoofed-peer",
                },
            )
            conn = http.client.HTTPConnection(host, port, timeout=5)
            conn.request(
                "POST",
                "/v1/voolbook/post",
                body=json.dumps(ai_payload),
                headers={"Content-Type": "application/json"},
            )
            response = conn.getresponse()
            body = json.loads(response.read().decode("utf-8"))
            self.assertEqual(response.status, 200)
            self.assertEqual(body["result"]["origin_kind"], "ai")
            self.assertEqual(body["result"]["origin_channel"], "signed_write")
            self.assertEqual(body["result"]["origin_peer_id"], local_peer_id)
            conn.close()

            human_payload = _api_write_auth_mod.build_signed_write_envelope(
                target_path="/v1/voolbook/post",
                payload={
                    "voolbook_peer_id": local_peer_id,
                    "content": "Human-operated post under the same profile.",
                    "origin_kind": "ai",
                    "origin_channel": "spoofed",
                    "origin_peer_id": "spoofed-peer",
                },
            )
            conn = http.client.HTTPConnection(host, port, timeout=5)
            conn.request(
                "POST",
                "/v1/voolbook/post",
                body=json.dumps(human_payload),
                headers={
                    "Content-Type": "application/json",
                    "X-VoolBook-Token": reg.token,
                },
            )
            response = conn.getresponse()
            body = json.loads(response.read().decode("utf-8"))
            self.assertEqual(response.status, 200)
            self.assertEqual(body["result"]["origin_kind"], "human")
            self.assertEqual(body["result"]["origin_channel"], "voolbook_token")
            self.assertEqual(body["result"]["origin_peer_id"], local_peer_id)
            conn.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2.0)

    def test_http_server_rejects_spoofed_voolbook_edit_and_delete_actor(self) -> None:
        from core.voolbook_identity import register_voolbook_account
        from storage.voolbook_store import create_post

        local_peer_id = _signer_mod.get_local_peer_id()
        reg = register_voolbook_account(f"editor_{uuid.uuid4().hex[:8]}", peer_id=local_peer_id)
        post = create_post(local_peer_id, reg.profile.handle, "Original social post.")
        try:
            server = _server_mod.build_server(
                _server_mod.MeetAndGreetServerConfig(host="127.0.0.1", port=0, require_signed_writes=True),
                service=self.service,
            )
        except PermissionError:
            self.skipTest("Local socket binds are not permitted in this sandbox.")
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address
            spoofed_peer_id = f"peer-{uuid.uuid4().hex}{uuid.uuid4().hex}"
            for suffix, payload in (
                (
                    "edit",
                    {
                        "voolbook_peer_id": spoofed_peer_id,
                        "content": "Hacked content",
                    },
                ),
                (
                    "delete",
                    {
                        "voolbook_peer_id": spoofed_peer_id,
                    },
                ),
            ):
                with self.subTest(route=suffix):
                    signed_payload = _api_write_auth_mod.build_signed_write_envelope(
                        target_path=f"/v1/voolbook/post/{post.post_id}/{suffix}",
                        payload=payload,
                    )
                    conn = http.client.HTTPConnection(host, port, timeout=5)
                    conn.request(
                        "POST",
                        f"/v1/voolbook/post/{post.post_id}/{suffix}",
                        body=json.dumps(signed_payload),
                        headers={
                            "Content-Type": "application/json",
                            "X-VoolBook-Token": reg.token,
                        },
                    )
                    response = conn.getresponse()
                    body = json.loads(response.read().decode("utf-8"))
                    self.assertEqual(response.status, 403)
                    self.assertIn("voolbook_peer_id", str(body.get("error") or "").lower())
                    conn.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2.0)

    def test_http_server_failed_status_validation_does_not_mutate_topic(self) -> None:
        creator_id = _signer_mod.get_local_peer_id()
        missing_claim_id = f"missing-claim-{uuid.uuid4().hex}"
        topic = dispatch_request(
            "POST",
            "/v1/hive/topics",
            {},
            {
                "created_by_agent_id": creator_id,
                "title": "HTTP status validation ordering audit",
                "summary": "Status writes must not persist when claim validation fails at the HTTP server boundary.",
                "topic_tags": ["security", "audit"],
                "status": "researching",
                "visibility": "agent_public",
                "evidence_mode": "candidate_only",
            },
            self.service,
        )[1]["result"]
        try:
            server = _server_mod.build_server(
                _server_mod.MeetAndGreetServerConfig(host="127.0.0.1", port=0, require_signed_writes=True),
                service=self.service,
            )
        except PermissionError:
            self.skipTest("Local socket binds are not permitted in this sandbox.")
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address
            signed_payload = _api_write_auth_mod.build_signed_write_envelope(
                target_path="/v1/hive/topic-status",
                payload={
                    "topic_id": topic["topic_id"],
                    "updated_by_agent_id": creator_id,
                    "status": "closed",
                    "claim_id": missing_claim_id,
                    "note": "This should fail before any state mutation.",
                },
            )
            conn = http.client.HTTPConnection(host, port, timeout=5)
            conn.request(
                "POST",
                "/v1/hive/topic-status",
                body=json.dumps(signed_payload),
                headers={"Content-Type": "application/json"},
            )
            response = conn.getresponse()
            body = json.loads(response.read().decode("utf-8"))
            self.assertEqual(response.status, 404)
            self.assertIn("resource not found", str(body.get("error") or "").lower())
            conn.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2.0)

        status_code, topic_result = dispatch_request(
            "GET",
            f"/v1/hive/topics/{topic['topic_id']}",
            {},
            None,
            self.service,
        )
        self.assertEqual(status_code, 200)
        self.assertEqual(topic_result["result"]["status"], "researching")

    def test_public_http_server_requires_scoped_hive_write_grant_for_hive_posts(self) -> None:
        with patch(
            "apps.meet_and_greet_server.policy_engine.get",
            side_effect=lambda path, default=None: (
                True if path == "economics.public_hive_require_scoped_write_grants" else default
            ),
        ):
            try:
                server = _server_mod.build_server(
                    _server_mod.MeetAndGreetServerConfig(host="0.0.0.0", port=0, auth_token="test-token", require_signed_writes=True),
                    service=self.service,
                )
            except PermissionError:
                self.skipTest("Local socket binds are not permitted in this sandbox.")
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            _host, port = server.server_address
            agent_id = _signer_mod.get_local_peer_id()
            topic_payload = _api_write_auth_mod.build_signed_write_envelope(
                target_path="/v1/hive/topics",
                payload={
                    "created_by_agent_id": agent_id,
                    "title": "Scoped grant test",
                    "summary": "This topic should be forced into review because the grant says so.",
                    "topic_tags": ["security"],
                    "status": "open",
                    "visibility": "agent_public",
                    "evidence_mode": "candidate_only",
                    "write_grant": build_hive_write_grant(
                        granted_to=agent_id,
                        allowed_paths=["/v1/hive/topics"],
                        max_uses=2,
                        review_required_by_default=True,
                    ),
                },
            )
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request(
                "POST",
                "/v1/hive/topics",
                body=json.dumps(topic_payload),
                headers={"Content-Type": "application/json", "X-Vool-Meet-Token": "test-token"},
            )
            response = conn.getresponse()
            topic_body = json.loads(response.read().decode("utf-8"))
            self.assertEqual(response.status, 200)
            topic_id = topic_body["result"]["topic_id"]
            self.assertEqual(topic_body["result"]["moderation_state"], "review_required")
            conn.close()

            unsigned_post = _api_write_auth_mod.build_signed_write_envelope(
                target_path="/v1/hive/posts",
                payload={
                    "topic_id": topic_id,
                    "author_agent_id": agent_id,
                    "post_kind": "analysis",
                    "stance": "support",
                    "body": "Missing grant should fail.",
                    "evidence_refs": [],
                },
            )
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request(
                "POST",
                "/v1/hive/posts",
                body=json.dumps(unsigned_post),
                headers={"Content-Type": "application/json", "X-Vool-Meet-Token": "test-token"},
            )
            response = conn.getresponse()
            self.assertEqual(response.status, 400)
            conn.close()

            granted_post = _api_write_auth_mod.build_signed_write_envelope(
                target_path="/v1/hive/posts",
                payload={
                    "topic_id": topic_id,
                    "author_agent_id": agent_id,
                    "post_kind": "analysis",
                    "stance": "support",
                    "body": "Scoped grant allows this post with concrete analysis attached.",
                    "evidence_refs": [],
                    "write_grant": build_hive_write_grant(
                        granted_to=agent_id,
                        allowed_paths=["/v1/hive/posts"],
                        topic_id=topic_id,
                        max_uses=2,
                    ),
                },
            )
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request(
                "POST",
                "/v1/hive/posts",
                body=json.dumps(granted_post),
                headers={"Content-Type": "application/json", "X-Vool-Meet-Token": "test-token"},
            )
            response = conn.getresponse()
            body = json.loads(response.read().decode("utf-8"))
            self.assertEqual(response.status, 200)
            self.assertTrue(body["ok"])
            conn.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2.0)

    def test_public_http_server_enforces_daily_hive_write_quota(self) -> None:
        with patch("core.public_hive_quotas.policy_engine.get") as get_policy:
            try:
                get_policy.side_effect = lambda path, default=None: {
                    "economics.public_hive_unknown_peer_trust": 0.30,
                    "economics.public_hive_low_trust_threshold": 0.45,
                    "economics.public_hive_high_trust_threshold": 0.75,
                    "economics.public_hive_daily_quota_low": 1.0,
                    "economics.public_hive_daily_quota_mid": 3.0,
                    "economics.public_hive_daily_quota_high": 6.0,
                    "economics.public_hive_route_costs": {
                        "/v1/hive/topics": 1.0,
                    },
                }.get(path, default)
                server = _server_mod.build_server(
                    _server_mod.MeetAndGreetServerConfig(host="0.0.0.0", port=0, auth_token="test-token", require_signed_writes=True),
                    service=self.service,
                )
            except PermissionError:
                self.skipTest("Local socket binds are not permitted in this sandbox.")
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                _host, port = server.server_address
                agent_id = _signer_mod.get_local_peer_id()

                first_topic = _api_write_auth_mod.build_signed_write_envelope(
                    target_path="/v1/hive/topics",
                    payload={
                        "created_by_agent_id": agent_id,
                        "title": "Quota topic one",
                        "summary": "First public topic should fit inside quota.",
                        "topic_tags": ["quota"],
                        "status": "open",
                        "visibility": "agent_public",
                        "evidence_mode": "candidate_only",
                        "write_grant": build_hive_write_grant(
                            granted_to=agent_id,
                            allowed_paths=["/v1/hive/topics"],
                        ),
                    },
                )
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request(
                    "POST",
                    "/v1/hive/topics",
                    body=json.dumps(first_topic),
                    headers={"Content-Type": "application/json", "X-Vool-Meet-Token": "test-token"},
                )
                response = conn.getresponse()
                self.assertEqual(response.status, 200)
                conn.close()

                second_topic = _api_write_auth_mod.build_signed_write_envelope(
                    target_path="/v1/hive/topics",
                    payload={
                        "created_by_agent_id": agent_id,
                        "title": "Quota topic two",
                        "summary": "Second public topic should be blocked by daily quota.",
                        "topic_tags": ["quota"],
                        "status": "open",
                        "visibility": "agent_public",
                        "evidence_mode": "candidate_only",
                        "write_grant": build_hive_write_grant(
                            granted_to=agent_id,
                            allowed_paths=["/v1/hive/topics"],
                        ),
                    },
                )
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request(
                    "POST",
                    "/v1/hive/topics",
                    body=json.dumps(second_topic),
                    headers={"Content-Type": "application/json", "X-Vool-Meet-Token": "test-token"},
                )
                response = conn.getresponse()
                body = json.loads(response.read().decode("utf-8"))
                self.assertEqual(response.status, 429)
                self.assertIn("quota exhausted", body["error"].lower())
                conn.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2.0)

    def test_public_http_server_blocks_low_trust_commons_review_route(self) -> None:
        with patch("core.public_hive_quotas.policy_engine.get") as get_policy:
            try:
                get_policy.side_effect = lambda path, default=None: {
                    "economics.public_hive_unknown_peer_trust": 0.30,
                    "economics.public_hive_route_costs": {
                        "/v1/hive/commons/promotion-reviews": 0.25,
                    },
                    "economics.public_hive_min_route_trusts": {
                        "/v1/hive/commons/promotion-reviews": 0.75,
                    },
                }.get(path, default)
                server = _server_mod.build_server(
                    _server_mod.MeetAndGreetServerConfig(host="0.0.0.0", port=0, auth_token="test-token", require_signed_writes=True),
                    service=self.service,
                )
            except PermissionError:
                self.skipTest("Local socket binds are not permitted in this sandbox.")
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                _host, port = server.server_address
                agent_id = _signer_mod.get_local_peer_id()
                review_payload = _api_write_auth_mod.build_signed_write_envelope(
                    target_path="/v1/hive/commons/promotion-reviews",
                    payload={
                        "candidate_id": "candidate-1234567890abcdef",
                        "reviewer_agent_id": agent_id,
                        "decision": "approve",
                        "note": "Low-trust peer should not be allowed to review promotion.",
                    },
                )
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request(
                    "POST",
                    "/v1/hive/commons/promotion-reviews",
                    body=json.dumps(review_payload),
                    headers={"Content-Type": "application/json", "X-Vool-Meet-Token": "test-token"},
                )
                response = conn.getresponse()
                body = json.loads(response.read().decode("utf-8"))
                self.assertEqual(response.status, 403)
                self.assertIn("trust is too low", body["error"].lower())
                conn.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2.0)

    def test_http_server_sets_cors_headers(self) -> None:
        try:
            server = build_server(
                MeetAndGreetServerConfig(host="127.0.0.1", port=0, require_signed_writes=False),
                service=self.service,
            )
        except PermissionError:
            self.skipTest("Local socket binds are not permitted in this sandbox.")
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address
            conn = http.client.HTTPConnection(host, port, timeout=5)
            conn.request("OPTIONS", "/v1/health")
            response = conn.getresponse()
            self.assertIn(response.status, {200, 204})
            self.assertEqual(response.getheader("Access-Control-Allow-Origin"), "*")
            self.assertIn("GET", response.getheader("Access-Control-Allow-Methods") or "")
            conn.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2.0)

    def test_http_server_head_supports_health_and_static_routes(self) -> None:
        try:
            server = build_server(
                MeetAndGreetServerConfig(host="127.0.0.1", port=0, require_signed_writes=False),
                service=self.service,
            )
        except PermissionError:
            self.skipTest("Local socket binds are not permitted in this sandbox.")
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address
            for path in ("/v1/health", "/v1/readyz", "/", "/feed", "/tasks", "/agents", "/proof", "/hive", "/brain-hive"):
                with self.subTest(path=path):
                    conn = http.client.HTTPConnection(host, port, timeout=5)
                    conn.request("HEAD", path)
                    response = conn.getresponse()
                    body = response.read()
                    self.assertEqual(response.status, 200)
                    self.assertEqual(body, b"")
                    self.assertGreater(int(response.getheader("Content-Length") or "0"), 0)
                    conn.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2.0)

    def test_create_meet_app_persists_write_rate_limit_across_restart(self) -> None:
        def policy_get(path, default=None):
            if path == "economics.authenticated_write_requests_per_minute":
                return 1
            return default

        headers = {"Content-Type": "application/json", "X-Vool-Meet-Token": "token"}
        app_one = create_meet_app(
            config=MeetAndGreetServerConfig(host="0.0.0.0", port=8766, auth_token="token"),
            service=self.service,
            hive_service=_server_mod.BrainHiveService(),
            metrics=MeetMetricsCollector(),
            policy_get=policy_get,
        )
        first_payload = _api_write_auth_mod.build_signed_write_envelope(
            target_path="/v1/payments/status",
            payload={
                "task_or_transfer_id": f"pay-{uuid.uuid4().hex}",
                "payer_peer_id": _signer_mod.get_local_peer_id(),
                "payee_peer_id": "peer-target-1234567890abcdef",
                "status": "reserved",
                "metadata": {"source": "first"},
            },
        )
        first_status, _, first_body = asgi_request(
            app_one,
            method="POST",
            path="/v1/payments/status",
            headers=headers,
            body=json.dumps(first_payload).encode("utf-8"),
        )
        self.assertEqual(first_status, 200, first_body.decode("utf-8"))

        app_two = create_meet_app(
            config=MeetAndGreetServerConfig(host="0.0.0.0", port=8766, auth_token="token"),
            service=self.service,
            hive_service=_server_mod.BrainHiveService(),
            metrics=MeetMetricsCollector(),
            policy_get=policy_get,
        )
        second_payload = _api_write_auth_mod.build_signed_write_envelope(
            target_path="/v1/payments/status",
            payload={
                "task_or_transfer_id": f"pay-{uuid.uuid4().hex}",
                "payer_peer_id": _signer_mod.get_local_peer_id(),
                "payee_peer_id": "peer-target-1234567890abcdef",
                "status": "reserved",
                "metadata": {"source": "second"},
            },
        )
        second_status, _, second_body = asgi_request(
            app_two,
            method="POST",
            path="/v1/payments/status",
            headers=headers,
            body=json.dumps(second_payload).encode("utf-8"),
        )
        self.assertEqual(second_status, 429, second_body.decode("utf-8"))
        second_envelope = json.loads(second_body.decode("utf-8"))
        self.assertIn("rate limit", str(second_envelope.get("error") or "").lower())

    def test_public_http_server_blocks_metrics_for_get_and_head(self) -> None:
        try:
            server = build_server(
                MeetAndGreetServerConfig(host="0.0.0.0", port=0, auth_token="test-token", require_signed_writes=True),
                service=self.service,
            )
        except PermissionError:
            self.skipTest("Local socket binds are not permitted in this sandbox.")
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            _host, port = server.server_address
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/metrics", headers={"X-Vool-Meet-Token": "test-token"})
            response = conn.getresponse()
            body = json.loads(response.read().decode("utf-8"))
            self.assertEqual(response.status, 403)
            self.assertIn("metrics", str(body.get("error") or "").lower())
            conn.close()

            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("HEAD", "/metrics", headers={"X-Vool-Meet-Token": "test-token"})
            response = conn.getresponse()
            body = response.read()
            self.assertEqual(response.status, 403)
            self.assertEqual(body, b"")
            conn.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2.0)

    def test_metrics_snapshot_route_and_prometheus_export(self) -> None:
        metrics = MeetMetricsCollector()
        metrics.record(method="GET", path="/v1/health", status_code=200, latency_ms=3.5)
        metrics.record(method="POST", path="/v1/hive/topics", status_code=401, latency_ms=5.0)
        code, payload = dispatch_request("GET", "/v1/metrics", {}, None, self.service, metrics=metrics)
        self.assertEqual(code, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["result"]["requests_total"], 2)
        self.assertEqual(payload["result"]["errors_total"], 1)
        self.assertEqual(payload["result"]["status_counts"]["200"], 1)
        self.assertEqual(payload["result"]["status_counts"]["401"], 1)
        rendered = metrics.render_prometheus()
        self.assertIn("vool_meet_requests_total 2", rendered)
        self.assertIn('vool_meet_route_total{method="POST",path="/v1/hive/topics"} 1', rendered)

    def test_dispatch_request_sanitizes_validation_errors(self) -> None:
        status_code, payload = dispatch_request("POST", "/v1/presence/register", {}, {"agent_id": "only-agent-id"}, self.service)
        self.assertEqual(status_code, 422)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "Invalid request payload.")

    def test_dispatch_request_internal_error_maps_to_500(self) -> None:
        class ExplodingService:
            def health(self):
                raise RuntimeError("boom")

        status_code, payload = dispatch_request("GET", "/v1/health", {}, None, ExplodingService())
        self.assertEqual(status_code, 500)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "Request handling failed.")

    def test_query_int_caps_large_values(self) -> None:
        with patch("apps.meet_and_greet_server.policy_engine.get", return_value=50):
            value = _query_int({"limit": ["1000000"]}, "limit")
        self.assertEqual(value, 50)


if __name__ == "__main__":
    unittest.main()


def _clear_meet_tables() -> None:
    reset_default_connection()
    conn = get_connection()
    try:
        for table in (
            "hive_moderation_events",
            "hive_moderation_reviews",
            "hive_write_grants",
            "public_hive_write_quota_events",
            "hive_claim_links",
            "hive_topic_claims",
            "hive_posts",
            "hive_topics",
            "artifact_manifests",
            "agent_names",
            "scoreboard",
            "presence_leases",
            "knowledge_tombstones",
            "index_deltas",
            "knowledge_manifests",
            "knowledge_holders",
            "meet_nodes",
            "meet_sync_state",
            "payment_status",
            "voolbook_tokens",
            "voolbook_posts",
            "voolbook_profiles",
            "agent_capabilities",
            "peers",
            "peer_endpoints",
            "peer_endpoint_observations",
            "identity_revocations",
            "identity_key_history",
            "nonce_cache",
        ):
            try:
                conn.execute(f"DELETE FROM {table}")
            except Exception:
                continue
        conn.commit()
    finally:
        conn.close()
        reset_default_connection()


def test_clear_meet_tables_clears_peer_trust_rows() -> None:
    run_migrations()
    peer_id = _signer_mod.get_local_peer_id()
    claimant_id = f"peer-{uuid.uuid4().hex}{uuid.uuid4().hex}"
    now = _now().isoformat()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO peers (
                peer_id, display_alias, trust_score, successful_shards, failed_shards,
                strike_count, status, last_seen_at, created_at, updated_at
            ) VALUES (?, ?, ?, 0, 0, 0, 'active', ?, ?, ?)
            """,
            (peer_id, "local-test-peer", 0.95, now, now, now),
        )
        conn.commit()
    finally:
        conn.close()

    service = MeetAndGreetService()
    topic_status, topic_payload = dispatch_request(
        "POST",
        "/v1/hive/topics",
        {},
        {
            "created_by_agent_id": peer_id,
            "title": "Cleanup claim regression",
            "summary": "Helpers must clear Hive topic claims between tests.",
            "topic_tags": ["cleanup", "claims"],
            "status": "open",
            "visibility": "agent_public",
            "evidence_mode": "candidate_only",
        },
        service,
    )
    assert topic_status == 200
    claim_status, claim_payload = dispatch_request(
        "POST",
        "/v1/hive/topic-claims",
        {},
        {
            "topic_id": topic_payload["result"]["topic_id"],
            "agent_id": claimant_id,
            "note": "Cleanup coverage",
            "capability_tags": ["qa"],
        },
        service,
    )
    assert claim_status == 200
    claim_id = str(claim_payload["result"]["claim_id"])

    _clear_meet_tables()

    conn = get_connection()
    try:
        row = conn.execute("SELECT 1 FROM peers WHERE peer_id = ? LIMIT 1", (peer_id,)).fetchone()
        claim_row = conn.execute("SELECT 1 FROM hive_topic_claims WHERE claim_id = ? LIMIT 1", (claim_id,)).fetchone()
    finally:
        conn.close()
    assert row is None
    assert claim_row is None


def test_meet_main_runs_uvicorn_with_factory_app_and_tls_settings() -> None:
    fake_uvicorn = unittest.mock.Mock()
    fake_server = unittest.mock.Mock()
    fake_uvicorn.Config.return_value = unittest.mock.sentinel.config
    fake_uvicorn.Server.return_value = fake_server
    args = unittest.mock.Mock(
        host="127.0.0.1",
        port=8766,
        auth_token="token",
        no_signed_writes=False,
        tls_certfile="cert.pem",
        tls_keyfile="key.pem",
        tls_ca_file="ca.pem",
        tls_require_client_cert=True,
    )

    with patch(
        "apps.meet_and_greet_server.argparse.ArgumentParser.parse_args",
        return_value=args,
    ), patch.dict(
        "sys.modules",
        {"uvicorn": fake_uvicorn},
    ), patch(
        "apps.meet_and_greet_server.create_meet_app",
        return_value=unittest.mock.sentinel.app,
    ) as create_app_mock:
        exit_code = meet_main()

    assert exit_code == 0
    create_app_mock.assert_called_once()
    fake_uvicorn.Config.assert_called_once()
    _, kwargs = fake_uvicorn.Config.call_args
    assert kwargs["host"] == "127.0.0.1"
    assert kwargs["port"] == 8766
    assert kwargs["ssl_certfile"] == "cert.pem"
    assert kwargs["ssl_keyfile"] == "key.pem"
    assert kwargs["ssl_ca_certs"] == "ca.pem"
    assert kwargs["ssl_cert_reqs"] == ssl.CERT_REQUIRED
    assert fake_uvicorn.Config.call_args.args[0] is unittest.mock.sentinel.app
    fake_server.run.assert_called_once_with()
