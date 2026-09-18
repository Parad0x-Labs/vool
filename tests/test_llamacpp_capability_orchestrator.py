from __future__ import annotations

from unittest import mock

from core.hardware_tier import MachineProbe
from core.llamacpp_capability_probe import CapabilityProbeResult
from installer.llamacpp_capability_orchestrator import detect_and_verify_best_backend
from installer.llamacpp_runtime_bootstrap import BootstrapError, InstalledBackend


def _probe(gpu_name: str = "NVIDIA GeForce GTX 1080") -> MachineProbe:
    return MachineProbe(cpu_cores=8, ram_gb=7.9, gpu_name=gpu_name, vram_gb=8.0, accelerator="cpu")


def _installed(backend: str) -> InstalledBackend:
    return InstalledBackend(backend=backend, release_tag="b9856", install_dir=f"/fake/{backend}", server_exe_path=f"/fake/{backend}/llama-server.exe")


def _capability_result(*, backend: str, status: str) -> CapabilityProbeResult:
    return CapabilityProbeResult(
        schema="vool.llamacpp_capability_probe.v1",
        probed_at_epoch=0.0,
        probe_version=1,
        gpu_name="NVIDIA GeForce GTX 1080",
        gpu_vendor="nvidia",
        backend_tested=backend,
        binary_release_tag="b9856",
        cpu_baseline_tokens_per_second=2.0,
        gpu_tokens_per_second=36.0 if status == "gpu_confirmed_fast" else 2.1,
        speedup_ratio=18.0 if status == "gpu_confirmed_fast" else 1.05,
        status=status,
        verdict_backend=backend if status == "gpu_confirmed_fast" else "cpu",
        detail="",
    )


def test_no_gpu_skips_everything_and_never_downloads(tmp_path) -> None:
    probe = MachineProbe(cpu_cores=4, ram_gb=8.0, gpu_name=None, vram_gb=None, accelerator="cpu")

    with mock.patch("installer.llamacpp_capability_orchestrator.download_probe_fixture_model") as download_mock:
        outcome = detect_and_verify_best_backend(probe=probe, runtime_home=tmp_path)

    download_mock.assert_not_called()
    assert outcome.result.status == "skipped_no_gpu"
    assert outcome.attempts == ()


def test_first_candidate_usable_stops_immediately_without_trying_the_rest(tmp_path) -> None:
    with (
        mock.patch("installer.llamacpp_capability_orchestrator.download_probe_fixture_model", return_value=tmp_path / "probe.gguf"),
        mock.patch("installer.llamacpp_capability_orchestrator.install_llamacpp_backend", return_value=_installed("cuda")) as install_mock,
        mock.patch(
            "installer.llamacpp_capability_orchestrator.probe_llamacpp_capability",
            return_value=_capability_result(backend="cuda", status="gpu_confirmed_fast"),
        ) as probe_mock,
    ):
        outcome = detect_and_verify_best_backend(probe=_probe(), runtime_home=tmp_path)

    assert outcome.result.usable
    assert outcome.result.verdict_backend == "cuda"
    assert install_mock.call_count == 1  # never tried vulkan since cuda already won
    assert probe_mock.call_count == 1
    assert len(outcome.attempts) == 1
    assert outcome.attempts[0].backend == "cuda"
    assert outcome.attempts[0].outcome == "usable"


def test_first_candidate_rejected_falls_through_to_vulkan_which_succeeds(tmp_path) -> None:
    results_by_backend = {
        "cuda": _capability_result(backend="cuda", status="gpu_rejected_slow"),
        "vulkan": _capability_result(backend="vulkan", status="gpu_confirmed_fast"),
    }

    def fake_probe(*, backend_tested, **kwargs):
        return results_by_backend[backend_tested]

    with (
        mock.patch("installer.llamacpp_capability_orchestrator.download_probe_fixture_model", return_value=tmp_path / "probe.gguf"),
        mock.patch(
            "installer.llamacpp_capability_orchestrator.install_llamacpp_backend",
            side_effect=lambda *, candidate, **kwargs: _installed(candidate.backend),
        ) as install_mock,
        mock.patch("installer.llamacpp_capability_orchestrator.probe_llamacpp_capability", side_effect=fake_probe) as probe_mock,
    ):
        outcome = detect_and_verify_best_backend(probe=_probe(), runtime_home=tmp_path)

    assert outcome.result.usable
    assert outcome.result.verdict_backend == "vulkan"
    assert install_mock.call_count == 2  # tried cuda, then vulkan
    assert probe_mock.call_count == 2
    assert outcome.attempts[0].backend == "cuda"
    assert outcome.attempts[0].outcome == "rejected"
    assert outcome.attempts[1].backend == "vulkan"
    assert outcome.attempts[1].outcome == "usable"


def test_all_candidates_rejected_returns_last_measured_result(tmp_path) -> None:
    with (
        mock.patch("installer.llamacpp_capability_orchestrator.download_probe_fixture_model", return_value=tmp_path / "probe.gguf"),
        mock.patch(
            "installer.llamacpp_capability_orchestrator.install_llamacpp_backend",
            side_effect=lambda *, candidate, **kwargs: _installed(candidate.backend),
        ),
        mock.patch(
            "installer.llamacpp_capability_orchestrator.probe_llamacpp_capability",
            side_effect=lambda *, backend_tested, **kwargs: _capability_result(backend=backend_tested, status="gpu_rejected_slow"),
        ),
    ):
        outcome = detect_and_verify_best_backend(probe=_probe(), runtime_home=tmp_path)

    assert not outcome.result.usable
    assert outcome.result.status == "gpu_rejected_slow"
    assert len(outcome.attempts) == 2  # cuda and vulkan both tried and both rejected
    assert all(attempt.outcome == "rejected" for attempt in outcome.attempts)


def test_install_failure_for_one_candidate_falls_through_to_next(tmp_path) -> None:
    def fake_install(*, candidate, **kwargs):
        if candidate.backend == "cuda":
            raise BootstrapError("cudart asset missing from release")
        return _installed(candidate.backend)

    with (
        mock.patch("installer.llamacpp_capability_orchestrator.download_probe_fixture_model", return_value=tmp_path / "probe.gguf"),
        mock.patch("installer.llamacpp_capability_orchestrator.install_llamacpp_backend", side_effect=fake_install),
        mock.patch(
            "installer.llamacpp_capability_orchestrator.probe_llamacpp_capability",
            return_value=_capability_result(backend="vulkan", status="gpu_confirmed_fast"),
        ) as probe_mock,
    ):
        outcome = detect_and_verify_best_backend(probe=_probe(), runtime_home=tmp_path)

    assert outcome.result.usable
    assert outcome.result.verdict_backend == "vulkan"
    assert probe_mock.call_count == 1  # cuda never reached the probe step, only vulkan did
    assert outcome.attempts[0].backend == "cuda"
    assert outcome.attempts[0].outcome == "install_failed"
    assert outcome.attempts[1].backend == "vulkan"
    assert outcome.attempts[1].outcome == "usable"


def test_probe_fixture_download_failure_falls_back_to_cpu_without_crashing(tmp_path) -> None:
    with (
        mock.patch(
            "installer.llamacpp_capability_orchestrator.download_probe_fixture_model",
            side_effect=BootstrapError("network unreachable"),
        ),
        mock.patch("installer.llamacpp_capability_orchestrator.install_llamacpp_backend") as install_mock,
    ):
        outcome = detect_and_verify_best_backend(probe=_probe(), runtime_home=tmp_path)

    install_mock.assert_not_called()  # no point installing a backend with no fixture to test it against
    assert not outcome.result.usable
    assert outcome.attempts[0].outcome == "install_failed"


def test_all_candidates_install_failed_falls_back_to_cpu_result(tmp_path) -> None:
    with (
        mock.patch("installer.llamacpp_capability_orchestrator.download_probe_fixture_model", return_value=tmp_path / "probe.gguf"),
        mock.patch(
            "installer.llamacpp_capability_orchestrator.install_llamacpp_backend",
            side_effect=BootstrapError("network unreachable"),
        ),
    ):
        outcome = detect_and_verify_best_backend(probe=_probe(), runtime_home=tmp_path)

    assert not outcome.result.usable
    assert outcome.result.status == "skipped_no_gpu"  # honest cpu-fallback verdict, not a crash
    assert len(outcome.attempts) == 2
    assert all(attempt.outcome == "install_failed" for attempt in outcome.attempts)
