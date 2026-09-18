from __future__ import annotations

import os
import unittest

from network.pow_hashcash import required_pow_difficulty


class PowDifficultyPolicyTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("VOOL_POW_DIFFICULTY", None)

    def test_env_override_is_applied(self) -> None:
        os.environ["VOOL_POW_DIFFICULTY"] = "6"
        self.assertEqual(required_pow_difficulty(default=4), 6)

    def test_env_override_is_clamped(self) -> None:
        os.environ["VOOL_POW_DIFFICULTY"] = "99"
        self.assertEqual(required_pow_difficulty(default=4), 8)


if __name__ == "__main__":
    unittest.main()

