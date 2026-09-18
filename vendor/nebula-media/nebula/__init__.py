"""nebula-media public API.

Re-exports are LAZY (PEP 562): ``import nebula.worker`` or
``import nebula.media_ops`` must work with the standard library alone, since
the worker runs inside a host process that may not have numpy/scipy installed.
Accessing an encoder/metrics/page name here triggers the heavy import then.
"""

from __future__ import annotations

from typing import Any

_EXPORTS: dict[str, tuple[str, ...]] = {
    "encoder": ("CompressionResult", "compress_video"),
    "metrics": (
        "ClipQualityMetrics", "compute_psnr", "compute_ssim", "measure_clip_quality",
    ),
    "page": ("PageResult", "compress_page"),
    "quality_commitment": ("QualityCommitment", "generate_commitment", "verify_commitment"),
    "screen_codec": (
        "ScreenEncodeResult", "encode_screen_layered", "reconstruct_from_layered_mkv",
    ),
    "web0": (
        "ContentType", "PlatformTarget", "Web0EncodeResult", "encode_for_web0",
        "encode_for_x", "encode_image_web0", "encode_video_web0", "estimate_arweave_cost",
    ),
}

_ATTR_TO_MODULE: dict[str, str] = {
    attr: module for module, attrs in _EXPORTS.items() for attr in attrs
}

__all__ = [*_ATTR_TO_MODULE.keys(), "media_ops", "worker"]


def __getattr__(name: str) -> Any:  # PEP 562
    module = _ATTR_TO_MODULE.get(name)
    if module is None:
        raise AttributeError(f"module 'nebula' has no attribute {name!r}")
    import importlib

    value = getattr(importlib.import_module(f".{module}", __name__), name)
    globals()[name] = value  # cache after first heavy import
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_ATTR_TO_MODULE))
