"""Tests for VoolBook agent identity: registration, tokens, profiles, edge cases."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from storage.db import get_connection
from storage.migrations import run_migrations


class VoolBookIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()
        conn = get_connection()
        try:
            conn.execute("DELETE FROM voolbook_posts")
            conn.execute("DELETE FROM voolbook_tokens")
            conn.execute("DELETE FROM voolbook_profiles")
            conn.execute("DELETE FROM agent_names")
            conn.commit()
        finally:
            conn.close()

    @patch("core.voolbook_identity.get_local_peer_id", return_value="aabbccdd" * 8)
    def test_register_and_verify_token(self, _mock_pid):
        from core.voolbook_identity import register_voolbook_account, verify_token

        reg = register_voolbook_account("Vool_Master", peer_id="aabbccdd" * 8)
        self.assertEqual(reg.profile.handle, "Vool_Master")
        self.assertEqual(reg.profile.status, "active")
        self.assertTrue(len(reg.token) == 64)

        peer = verify_token(reg.token)
        self.assertEqual(peer, "aabbccdd" * 8)

    @patch("core.voolbook_identity.get_local_peer_id", return_value="aabbccdd" * 8)
    def test_token_revocation(self, _mock_pid):
        from core.voolbook_identity import register_voolbook_account, revoke_token, verify_token

        reg = register_voolbook_account("Test_Agent", peer_id="aabbccdd" * 8)
        self.assertIsNotNone(verify_token(reg.token))

        revoked = revoke_token("aabbccdd" * 8)
        self.assertTrue(revoked)

        self.assertIsNone(verify_token(reg.token))

    @patch("core.voolbook_identity.get_local_peer_id", return_value="aabbccdd" * 8)
    def test_token_rotation(self, _mock_pid):
        from core.voolbook_identity import register_voolbook_account, rotate_token, verify_token

        reg = register_voolbook_account("Rotating_Agent", peer_id="aabbccdd" * 8)
        old_token = reg.token

        new_token = rotate_token("aabbccdd" * 8)
        self.assertNotEqual(old_token, new_token)
        self.assertIsNone(verify_token(old_token))
        self.assertEqual(verify_token(new_token), "aabbccdd" * 8)

    @patch("core.voolbook_identity.get_local_peer_id", return_value="aabbccdd" * 8)
    def test_profile_crud(self, _mock_pid):
        from core.voolbook_identity import get_profile, register_voolbook_account, update_profile

        register_voolbook_account("Profile_Bot", peer_id="aabbccdd" * 8)
        profile = get_profile("aabbccdd" * 8)
        self.assertIsNotNone(profile)
        self.assertEqual(profile.handle, "Profile_Bot")
        self.assertEqual(profile.bio, "")

        updated = update_profile("aabbccdd" * 8, bio="I research everything.")
        self.assertEqual(updated.bio, "I research everything.")
        self.assertEqual(updated.handle, "Profile_Bot")

    @patch("core.voolbook_identity.get_local_peer_id", return_value="aabbccdd" * 8)
    def test_profile_by_handle(self, _mock_pid):
        from core.voolbook_identity import get_profile_by_handle, register_voolbook_account

        register_voolbook_account("HandleTest", peer_id="aabbccdd" * 8)

        profile = get_profile_by_handle("handletest")
        self.assertIsNotNone(profile)
        self.assertEqual(profile.handle, "HandleTest")

        profile2 = get_profile_by_handle("HANDLETEST")
        self.assertIsNotNone(profile2)
        self.assertEqual(profile2.peer_id, "aabbccdd" * 8)

        self.assertIsNone(get_profile_by_handle("nonexistent"))

    @patch("core.voolbook_identity.get_local_peer_id", return_value="aabbccdd" * 8)
    def test_deactivate_account(self, _mock_pid):
        from core.voolbook_identity import (
            deactivate_account,
            get_profile,
            register_voolbook_account,
            verify_token,
        )

        reg = register_voolbook_account("Doomed_Agent", peer_id="aabbccdd" * 8)
        self.assertTrue(deactivate_account("aabbccdd" * 8))

        profile = get_profile("aabbccdd" * 8)
        self.assertEqual(profile.status, "deactivated")
        self.assertIsNone(verify_token(reg.token))

    @patch("core.voolbook_identity.get_local_peer_id", return_value="aabbccdd" * 8)
    def test_duplicate_registration_rejected(self, _mock_pid):
        from core.voolbook_identity import register_voolbook_account

        register_voolbook_account("Unique_Bot", peer_id="aabbccdd" * 8)

        with self.assertRaises(ValueError):
            register_voolbook_account("Another_Name", peer_id="aabbccdd" * 8)

    def test_handle_taken_by_different_peer(self):
        from core.voolbook_identity import register_voolbook_account

        register_voolbook_account("Shared_Name", peer_id="aaaa" * 16)

        with self.assertRaises(ValueError) as ctx:
            register_voolbook_account("shared_name", peer_id="bbbb" * 16)
        self.assertIn("already claimed", str(ctx.exception))

    def test_invalid_handle_rejected(self):
        from core.voolbook_identity import register_voolbook_account

        with self.assertRaises(ValueError):
            register_voolbook_account("ab", peer_id="cccc" * 16)

        with self.assertRaises(ValueError):
            register_voolbook_account("has space", peer_id="cccc" * 16)

        with self.assertRaises(ValueError):
            register_voolbook_account("special!char", peer_id="cccc" * 16)

    def test_registration_can_replace_existing_mesh_name_claim_for_same_peer(self):
        from core.agent_name_registry import claim_agent_name, get_agent_name
        from core.voolbook_identity import register_voolbook_account

        peer_id = "eeee" * 16
        ok, _ = claim_agent_name(peer_id, "VOOL")
        self.assertTrue(ok)

        reg = register_voolbook_account("sls_0x", peer_id=peer_id)
        self.assertEqual(reg.profile.handle, "sls_0x")
        self.assertEqual(get_agent_name(peer_id), "sls_0x")

    def test_bio_truncated_at_280(self):
        from core.voolbook_identity import register_voolbook_account

        long_bio = "x" * 500
        reg = register_voolbook_account("Long_Bio_Bot", peer_id="dddd" * 16, bio=long_bio)
        self.assertEqual(len(reg.profile.bio), 280)

    @patch("core.privacy_guard.machine_identity_markers", return_value=["dev-machine"])
    def test_registration_rejects_machine_identity_in_handle_or_bio(self, _mock_markers):
        from core.voolbook_identity import register_voolbook_account

        with self.assertRaises(ValueError):
            register_voolbook_account("dev-machine", peer_id="eeee" * 16)

        with self.assertRaises(ValueError):
            register_voolbook_account("Clean_Handle", peer_id="ffff" * 16, bio="Running on dev-machine")

    @patch("core.privacy_guard.machine_identity_markers", return_value=["dev-machine"])
    def test_update_profile_rejects_display_name_or_bio_with_private_markers(self, _mock_markers):
        from core.voolbook_identity import register_voolbook_account, update_profile

        register_voolbook_account("ProfileSafe", peer_id="1111" * 16)

        with self.assertRaises(ValueError):
            update_profile("1111" * 16, display_name="dev-machine")

        with self.assertRaises(ValueError):
            update_profile("1111" * 16, bio="/home/vool-user/private/project")

    @patch("core.voolbook_identity.get_local_peer_id", return_value="aabbccdd" * 8)
    def test_verify_empty_token_returns_none(self, _mock_pid):
        from core.voolbook_identity import verify_token

        self.assertIsNone(verify_token(""))
        self.assertIsNone(verify_token("deadbeef" * 8))

    @patch("core.voolbook_identity.get_local_peer_id", return_value="aabbccdd" * 8)
    def test_increment_counters(self, _mock_pid):
        from core.voolbook_identity import (
            get_profile,
            increment_claim_count,
            increment_post_count,
            register_voolbook_account,
        )

        register_voolbook_account("Counter_Bot", peer_id="aabbccdd" * 8)

        increment_post_count("aabbccdd" * 8)
        increment_post_count("aabbccdd" * 8)
        increment_claim_count("aabbccdd" * 8)

        profile = get_profile("aabbccdd" * 8)
        self.assertEqual(profile.post_count, 2)
        self.assertEqual(profile.claim_count, 1)

    @patch("core.voolbook_identity.get_local_peer_id", return_value="aabbccdd" * 8)
    def test_list_profiles(self, _mock_pid):
        from core.voolbook_identity import list_profiles, register_voolbook_account

        register_voolbook_account("Agent_A", peer_id="aaaa" * 16)
        register_voolbook_account("Agent_B", peer_id="bbbb" * 16)

        profiles = list_profiles()
        self.assertEqual(len(profiles), 2)
        handles = {p.handle for p in profiles}
        self.assertIn("Agent_A", handles)
        self.assertIn("Agent_B", handles)

    @patch("core.voolbook_identity.get_local_peer_id", return_value="aabbccdd" * 8)
    def test_has_voolbook_account(self, _mock_pid):
        from core.voolbook_identity import has_voolbook_account, register_voolbook_account

        self.assertFalse(has_voolbook_account())
        register_voolbook_account("Check_Bot", peer_id="aabbccdd" * 8)
        self.assertTrue(has_voolbook_account())

    @patch("core.voolbook_identity.get_local_peer_id", return_value="aabbccdd" * 8)
    def test_local_token_persistence(self, _mock_pid):
        import os
        import tempfile
        from unittest.mock import patch as _patch

        from core.voolbook_identity import load_local_token, save_token_locally

        with tempfile.TemporaryDirectory() as tmpdir:
            fake_path = os.path.join(tmpdir, "voolbook_token.secret")
            with _patch("core.voolbook_identity._token_path", return_value=__import__("pathlib").Path(fake_path)):
                save_token_locally("test_token_123")
                loaded = load_local_token()
                self.assertEqual(loaded, "test_token_123")


if __name__ == "__main__":
    unittest.main()
