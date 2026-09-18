from __future__ import annotations

import importlib
import unittest
from datetime import datetime, timezone

import core.api_write_auth as api_write_auth
import network.protocol as protocol_mod
import network.signer as signer_mod
from core.identity_lifecycle import revoke_identity
from storage.db import get_connection
from storage.migrations import run_migrations


class IdentityLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        importlib.reload(signer_mod)
        importlib.reload(protocol_mod)
        importlib.reload(api_write_auth)
        run_migrations()
        conn = get_connection()
        try:
            for table in ("identity_revocations", "identity_key_history", "nonce_cache"):
                try:
                    conn.execute(f"DELETE FROM {table}")
                except Exception:
                    continue
            conn.commit()
        finally:
            conn.close()

    def tearDown(self) -> None:
        conn = get_connection()
        try:
            for table in ("identity_revocations", "identity_key_history", "nonce_cache"):
                try:
                    conn.execute(f"DELETE FROM {table}")
                except Exception:
                    continue
            conn.commit()
        finally:
            conn.close()

    def test_signed_write_rejects_revoked_peer(self) -> None:
        peer_id = signer_mod.get_local_peer_id()
        revoke_identity(peer_id, scope="signed_write", reason="closed_test_block")
        envelope = api_write_auth.build_signed_write_envelope(
            target_path="/v1/presence/register",
            payload={
                "agent_id": peer_id,
                "status": "idle",
                "capabilities": ["research"],
                "home_region": "eu",
                "current_region": "eu",
                "transport_mode": "lan_only",
                "trust_score": 0.5,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "lease_seconds": 180,
            },
        )
        with self.assertRaisesRegex(ValueError, "revoked"):
            api_write_auth.unwrap_signed_write(target_path="/v1/presence/register", raw_payload=envelope)

    def test_mesh_message_rejects_revoked_peer(self) -> None:
        peer_id = signer_mod.get_local_peer_id()
        raw = protocol_mod.encode_message(
            msg_id="msg-12345678",
            msg_type="PING",
            sender_peer_id=peer_id,
            nonce="nonce-12345678",
            payload={},
        )
        revoke_identity(peer_id, scope="mesh_message", reason="closed_test_block")
        with self.assertRaisesRegex(ValueError, "Invalid signature"):
            protocol_mod.Protocol.decode_and_validate(raw)
