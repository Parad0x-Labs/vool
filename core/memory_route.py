"""Which memory route a turn ran, named for the turn's receipt.

The benchmarked memory path and a fresh install's path must be the same path, and a reader of a turn's receipt must be
able to tell which one ran: the legacy semantic recall (350-token injection, VOOL_CONTEXT_CAPSULE_V2 off), the
Context Capsule 2.0 route, or the capsule with the memory kernel (receipts at ingest, the evidence compiler packet,
the withdrawn-answer verifier and kernel receipts).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

KERNEL_SWITCHES: tuple[str, ...] = (
    "VOOL_MEMORY_RECEIPTS",
    "VOOL_EVIDENCE_COMPILER",
    "VOOL_EVIDENCE_HOP",
    "VOOL_EVIDENCE_VERIFY",
    "VOOL_EVIDENCE_KERNEL",
)


def memory_route(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """The route this process's switches select, and the kernel switches that are on."""

    env_map = os.environ if env is None else env
    from core.evidence_compiler import compiler_enabled, hop_enabled, verify_enabled
    from core.evidence_kernel.receipts import kernel_enabled
    from core.local_ollama_inventory import env_flag_enabled
    from core.memory_receipts import enabled as receipts_enabled

    if not env_flag_enabled(env_map, "VOOL_CONTEXT_CAPSULE_V2", default=False):
        return {"route": "legacy_semantic", "switches": []}
    on = {
        "VOOL_MEMORY_RECEIPTS": receipts_enabled(env_map),
        "VOOL_EVIDENCE_COMPILER": compiler_enabled(env_map),
        "VOOL_EVIDENCE_HOP": hop_enabled(env_map),
        "VOOL_EVIDENCE_VERIFY": verify_enabled(env_map),
        "VOOL_EVIDENCE_KERNEL": kernel_enabled(env_map),
    }
    switches = [name for name in KERNEL_SWITCHES if on[name]]
    kernel = on["VOOL_MEMORY_RECEIPTS"] and on["VOOL_EVIDENCE_COMPILER"]
    return {"route": "capsule_v2+kernel" if kernel else "capsule_v2", "switches": switches}


__all__ = ["KERNEL_SWITCHES", "memory_route"]
