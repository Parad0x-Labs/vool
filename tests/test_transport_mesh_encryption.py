from __future__ import annotations

import base64
import os
import unittest
from unittest.mock import patch

import network.transport as transport


class MeshEncryptionTests(unittest.TestCase):
    def test_encrypt_decrypt_roundtrip_with_psk(self) -> None:
        key = base64.b64encode(b"k" * 32).decode("ascii")
        payload = b'{"msg":"hello"}'
        with patch.dict(os.environ, {"VOOL_MESH_PSK_B64": key}, clear=False):
            encrypted = transport._encrypt_for_mesh(payload)
            self.assertNotEqual(encrypted, payload)
            decrypted = transport._decrypt_for_mesh(encrypted)
            self.assertEqual(decrypted, payload)

    def test_encrypted_payload_requires_key(self) -> None:
        key = base64.b64encode(b"z" * 32).decode("ascii")
        payload = b'{"msg":"secret"}'
        with patch.dict(os.environ, {"VOOL_MESH_PSK_B64": key}, clear=False):
            encrypted = transport._encrypt_for_mesh(payload)
        with patch.dict(os.environ, {"VOOL_MESH_PSK_B64": ""}, clear=False), self.assertRaises(ValueError):
            transport._decrypt_for_mesh(encrypted)

    def test_mesh_encryption_required_blocks_plain_payloads(self) -> None:
        payload = b'{"msg":"plain"}'
        with patch("network.transport.policy_engine.get") as get_policy:
            def _get(path: str, default=None):
                if path == "system.require_mesh_encryption":
                    return True
                return default

            get_policy.side_effect = _get
            with self.assertRaises(ValueError):
                transport._encrypt_for_mesh(payload)
            with self.assertRaises(ValueError):
                transport._decrypt_for_mesh(payload)


if __name__ == "__main__":
    unittest.main()
