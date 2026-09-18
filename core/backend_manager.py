from __future__ import annotations

import importlib.util
import platform
from dataclasses import dataclass


@dataclass
class HardwareProfile:
    os_name: str
    machine: str
    is_apple_silicon: bool
    is_nvidia_capable: bool
    has_torch: bool
    has_mlx: bool
    has_onnxruntime: bool
    torch_cuda_available: bool
    torch_mps_available: bool
    onnx_providers: list[str]

@dataclass
class BackendSelection:
    backend_name: str
    device: str
    reason: str

class BackendManager:
    """
    Auto-detects machine hardware and selects the best local backend.
    Enforces that 'Backend decides compute. Persona decides soul.'
    """
    def detect_hardware(self) -> HardwareProfile:
        os_name = platform.system().lower()
        machine = platform.machine().lower()

        has_torch = self._module_exists("torch")
        has_mlx = self._module_exists("mlx")
        has_onnxruntime = self._module_exists("onnxruntime")

        torch_cuda_available = False
        torch_mps_available = False
        is_nvidia_capable = False
        onnx_providers: list[str] = []

        if has_torch:
            try:
                import torch  # type: ignore
                torch_cuda_available = bool(torch.cuda.is_available())
                torch_mps_available = bool(
                    getattr(torch.backends, "mps", None)
                    and torch.backends.mps.is_available()
                )
                is_nvidia_capable = torch_cuda_available
            except Exception:
                torch_cuda_available = False
                torch_mps_available = False

        if has_onnxruntime:
            try:
                import onnxruntime as ort  # type: ignore
                onnx_providers = list(ort.get_available_providers())
            except Exception:
                onnx_providers = []

        is_apple_silicon = os_name == "darwin" and machine in {"arm64", "aarch64"}

        return HardwareProfile(
            os_name=os_name,
            machine=machine,
            is_apple_silicon=is_apple_silicon,
            is_nvidia_capable=is_nvidia_capable,
            has_torch=has_torch,
            has_mlx=has_mlx,
            has_onnxruntime=has_onnxruntime,
            torch_cuda_available=torch_cuda_available,
            torch_mps_available=torch_mps_available,
            onnx_providers=onnx_providers,
        )

    def select_backend(self, hw: HardwareProfile | None = None) -> BackendSelection:
        hw = hw or self.detect_hardware()

        # 1) Apple Silicon: MLX first
        if hw.is_apple_silicon and hw.has_mlx:
            return BackendSelection(
                backend_name="MLXBackend",
                device="apple_silicon",
                reason="Apple Silicon detected; MLX available.",
            )

        # 2) Apple fallback: Torch MPS
        if hw.is_apple_silicon and hw.has_torch and hw.torch_mps_available:
            return BackendSelection(
                backend_name="TorchMPSBackend",
                device="mps",
                reason="Apple Silicon detected; PyTorch MPS available.",
            )

        # 3) NVIDIA path
        if hw.has_torch and hw.torch_cuda_available:
            return BackendSelection(
                backend_name="TorchCUDABackend",
                device="cuda",
                reason="CUDA-capable GPU detected.",
            )

        # 4) ONNX GPU-ish paths
        if hw.has_onnxruntime:
            if "CoreMLExecutionProvider" in hw.onnx_providers:
                return BackendSelection(
                    backend_name="ONNXRuntimeBackend",
                    device="coreml",
                    reason="ONNX Runtime CoreML provider available.",
                )

            if "CUDAExecutionProvider" in hw.onnx_providers:
                return BackendSelection(
                    backend_name="ONNXRuntimeBackend",
                    device="cuda",
                    reason="ONNX Runtime CUDA provider available.",
                )

        # 5) CPU fallback
        if hw.has_onnxruntime:
            return BackendSelection(
                backend_name="ONNXRuntimeBackend",
                device="cpu",
                reason="Using ONNX Runtime CPU fallback.",
            )

        if hw.has_torch:
            return BackendSelection(
                backend_name="TorchCPUBackend",
                device="cpu",
                reason="Using PyTorch CPU fallback.",
            )

        return BackendSelection(
            backend_name="NoBackend",
            device="none",
            reason="No supported ML backend detected.",
        )

    def healthcheck(self, selection: BackendSelection) -> bool:
        return selection.backend_name != "NoBackend"

    @staticmethod
    def _module_exists(module_name: str) -> bool:
        return importlib.util.find_spec(module_name) is not None
