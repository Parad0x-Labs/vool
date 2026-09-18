from __future__ import annotations

import types
import unittest
from unittest.mock import patch

from core.provider_routing import (
    ProviderCapabilityTruth,
    _device_probe,
    _envelope_manifest_score,
    _hardware_fit_adjustment,
)


def _cap(**over) -> ProviderCapabilityTruth:
    base = dict(
        provider_id="p",
        model_id="m",
        role_fit="drone",
        context_window=8192,
        tool_support=(),
        structured_output_support=False,
        tokens_per_second=0.0,
        ram_budget_gb=0.0,
        vram_budget_gb=0.0,
        quantization="q4",
        locality="local",
        privacy_class="local_private",
        queue_depth=0,
        max_safe_concurrency=1,
    )
    base.update(over)
    return ProviderCapabilityTruth(**base)


class HardwareFitAdjustmentTests(unittest.TestCase):
    def test_no_footprint_is_inert(self) -> None:
        # Manifests that declare no ram/vram budget must not be penalized.
        adj = _hardware_fit_adjustment(_cap(), ram_gb=24.0, vram_gb=24.0, accelerator="mps")
        self.assertEqual(adj, 0.0)

    def test_mps_over_capacity_is_strongly_penalized(self) -> None:
        adj = _hardware_fit_adjustment(_cap(ram_budget_gb=64.0), ram_gb=24.0, vram_gb=24.0, accelerator="mps")
        self.assertLess(adj, -3.0)

    def test_mps_fits_gets_small_bonus(self) -> None:
        adj = _hardware_fit_adjustment(_cap(ram_budget_gb=8.0), ram_gb=24.0, vram_gb=24.0, accelerator="mps")
        self.assertGreater(adj, 0.0)
        self.assertLessEqual(adj, 0.6)

    def test_mps_uses_ram_not_phantom_vram(self) -> None:
        # On unified memory vram_gb == ram_gb; a model needing 32 GB of VRAM must
        # be judged against the 24 GB the box actually has, not admitted because a
        # phantom 24 GB "VRAM pool" sits alongside RAM.
        adj = _hardware_fit_adjustment(
            _cap(vram_budget_gb=32.0), ram_gb=24.0, vram_gb=24.0, accelerator="mps"
        )
        self.assertLess(adj, 0.0)

    def test_cuda_vram_over_capacity_penalized(self) -> None:
        adj = _hardware_fit_adjustment(
            _cap(vram_budget_gb=16.0), ram_gb=64.0, vram_gb=8.0, accelerator="cuda"
        )
        self.assertLess(adj, -3.0)

    def test_cuda_vram_fits_bonus(self) -> None:
        adj = _hardware_fit_adjustment(
            _cap(vram_budget_gb=6.0), ram_gb=64.0, vram_gb=8.0, accelerator="cuda"
        )
        self.assertGreater(adj, 0.0)

    def test_cpu_falls_back_to_ram(self) -> None:
        over = _hardware_fit_adjustment(_cap(ram_budget_gb=32.0), ram_gb=16.0, vram_gb=0.0, accelerator="cpu")
        fits = _hardware_fit_adjustment(_cap(ram_budget_gb=8.0), ram_gb=16.0, vram_gb=0.0, accelerator="cpu")
        self.assertLess(over, 0.0)
        self.assertGreater(fits, 0.0)


class EnvelopeScoreFitTests(unittest.TestCase):
    REQ = {"notes": []}

    def _score(self, capability: ProviderCapabilityTruth) -> float:
        return _envelope_manifest_score(
            capability,
            requirements=self.REQ,
            provider_role="drone",
            rank_index=0,
            total_candidates=2,
        )

    def test_fitting_model_outscores_oversized_on_small_box(self) -> None:
        fake_probe = types.SimpleNamespace(ram_gb=24.0, vram_gb=24.0, accelerator="mps")
        with patch("core.provider_routing._device_probe", return_value=fake_probe):
            fits = self._score(_cap(ram_budget_gb=4.0))
            oversized = self._score(_cap(ram_budget_gb=64.0))
        self.assertGreater(fits, oversized)
        # The penalty is decisive: oversized drops well below the fitting model.
        self.assertGreater(fits - oversized, 3.0)

    def test_no_footprint_manifests_unchanged_by_fit(self) -> None:
        fake_probe = types.SimpleNamespace(ram_gb=24.0, vram_gb=24.0, accelerator="mps")
        with patch("core.provider_routing._device_probe", return_value=fake_probe):
            with_probe = self._score(_cap())
        with patch("core.provider_routing._device_probe", return_value=None):
            without_probe = self._score(_cap())
        self.assertEqual(with_probe, without_probe)


class RealMachineGroundingTest(unittest.TestCase):
    def test_real_probe_penalizes_an_unrunnable_model(self) -> None:
        probe = _device_probe()
        if probe is None:
            self.skipTest("hardware probe unavailable")
        adj = _hardware_fit_adjustment(
            _cap(ram_budget_gb=999.0),
            ram_gb=float(probe.ram_gb or 0.0),
            vram_gb=float(probe.vram_gb or 0.0),
            accelerator=str(probe.accelerator or "cpu"),
        )
        # A 999 GB model fits no commodity box; it must be penalized on real HW.
        self.assertLess(adj, -3.0)


if __name__ == "__main__":
    unittest.main()
