from __future__ import annotations

import unittest

from core.meet_proof_pack import run_adversarial_proof_pack, run_cross_region_convergence_proof
from storage.migrations import run_migrations


class MeetProofPackTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()

    def test_adversarial_pack_passes(self) -> None:
        results = run_adversarial_proof_pack()
        self.assertTrue(results)
        self.assertTrue(all(item.passed for item in results))

    def test_cross_region_convergence_passes(self) -> None:
        result = run_cross_region_convergence_proof()
        self.assertTrue(result.passed, result.details)
