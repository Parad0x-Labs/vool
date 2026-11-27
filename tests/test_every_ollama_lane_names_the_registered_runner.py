"""A lane that posts straight to Ollama names the same runner the model is registered and served on.

Owner's iMac, 2026-11-27 (24 GB, installed 0.8 build): qwen3:0.6b loaded ten times in one morning,
alternating a 16384 window (the adapter, from the model's registered manifest) and a 24576 window
(the judge lanes). Ollama reloads a resident model whenever the window changes, 3.3 GB each time
on a machine with ~100 MB free, so the window swapped and the app stalled at random moments.

Registration sizes the window from the capacity bucket recorded at install; the direct lanes sized
it from the machine's RAM. On that iMac the two buckets differed. Both now read the recorded bucket,
so every caller names one runner.
"""
from __future__ import annotations

import json
import os

from core.model_registry import ModelRegistry
from core.runtime_provider_defaults import _ensure_local_ollama_provider, ollama_direct_runner_options


def _record_bucket(home, bucket: str) -> None:
    path = home / "config" / "install-profile.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"capacity_bucket": bucket}), encoding="utf-8")


def test_a_direct_lane_sizes_the_window_registration_stamped(monkeypatch, tmp_path) -> None:
    home = tmp_path / "home"
    _record_bucket(home, "C")
    monkeypatch.setenv("VOOL_HOME", str(home))
    # This machine reads as a bigger bucket than the one recorded at install.
    monkeypatch.setenv("VOOL_CONTEXT_BUCKET", "E")
    for name in ("VOOL_OLLAMA_CONTEXT_WINDOW", "VOOL_ADAPTIVE_CONTEXT"):
        monkeypatch.delenv(name, raising=False)
    for tag, role in (("qwen3:0.6b", "lightweight_utility"), ("qwen3:8b", "general")):
        registry = ModelRegistry()
        _ensure_local_ollama_provider(registry, model_tag=tag, bundle_role=role, bucket="C", env=dict(os.environ))
        registered = int(registry.get_manifest("ollama-local", tag).runtime_config["context_window"])
        for lane_role in ("classifier", "general", "lightweight_utility"):
            assert ollama_direct_runner_options(tag, bundle_role=lane_role)["num_ctx"] == registered, (tag, lane_role)


def test_with_no_recorded_bucket_the_machine_still_sizes_it(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "fresh"))
    monkeypatch.setenv("VOOL_CONTEXT_BUCKET", "A")
    for name in ("VOOL_OLLAMA_CONTEXT_WINDOW", "VOOL_ADAPTIVE_CONTEXT"):
        monkeypatch.delenv(name, raising=False)
    assert ollama_direct_runner_options("qwen3:8b", bundle_role="general")["num_ctx"] <= 4096
