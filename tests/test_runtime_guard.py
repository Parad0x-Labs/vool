from __future__ import annotations

import unittest

from core.runtime_guard import validate_meet_public_deployment


class RuntimeGuardTests(unittest.TestCase):
    def test_loopback_bind_is_not_blocked(self) -> None:
        issues = validate_meet_public_deployment(
            bind_host="127.0.0.1",
            public_base_url="http://127.0.0.1:8766",
            auth_token=None,
        )
        self.assertEqual(issues, [])

    def test_public_bind_flags_placeholder_inputs(self) -> None:
        issues = validate_meet_public_deployment(
            bind_host="0.0.0.0",
            public_base_url="https://seed-eu-1.example.vool",
            auth_token="change-me",
        )
        self.assertTrue(any("auth token" in issue for issue in issues))
        self.assertTrue(any("public_base_url" in issue for issue in issues))

    def test_public_bind_requires_tls_by_default(self) -> None:
        issues = validate_meet_public_deployment(
            bind_host="0.0.0.0",
            public_base_url="https://seed-eu-1.valid.example",
            auth_token="strong-token-value",
            tls_certfile=None,
            tls_keyfile=None,
        )
        self.assertTrue(any("TLS cert/key" in issue for issue in issues))


if __name__ == "__main__":
    unittest.main()
