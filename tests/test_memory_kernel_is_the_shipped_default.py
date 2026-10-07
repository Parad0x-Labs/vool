"""A fresh install runs the memory path the benchmarks measure, and every turn's receipt names the route it ran.

Traced 2026-10-07: every memory benchmark of the kernel (receipts, compiler, hop, verifier, kernel receipts) set five
switches in the harness that no product entry point set, so a fresh install ran Context Capsule 2.0 without the kernel.
The product runtime defaults now set all five; an explicit value, 0 included, still wins.
"""
from __future__ import annotations

import pytest

from core.memory_route import KERNEL_SWITCHES, memory_route
from core.runtime_provider_defaults import apply_product_runtime_defaults


def test_a_fresh_process_runs_the_capsule_with_the_kernel():
    env: dict[str, str] = {}
    apply_product_runtime_defaults(env)
    assert all(env[name] == "1" for name in KERNEL_SWITCHES), env
    assert memory_route(env) == {"route": "capsule_v2+kernel", "switches": list(KERNEL_SWITCHES)}


@pytest.mark.parametrize("name", KERNEL_SWITCHES)
def test_an_explicit_zero_for_any_switch_wins(name):
    env = {name: "0"}
    apply_product_runtime_defaults(env)
    assert env[name] == "0"
    assert name not in memory_route(env)["switches"]


def test_every_switch_off_is_the_plain_capsule_and_capsule_off_is_the_legacy_recall():
    env = {name: "0" for name in KERNEL_SWITCHES}
    apply_product_runtime_defaults(env)
    assert memory_route(env) == {"route": "capsule_v2", "switches": []}
    assert memory_route({"VOOL_CONTEXT_CAPSULE_V2": "0"}) == {"route": "legacy_semantic", "switches": []}
