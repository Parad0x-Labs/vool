"""CI #681 (tests/usepod/test_usepod_served_tool_flow.py forbidden / malformed): with the paid pin
cleared, the clean loop still ran two billed steps on the UsePod model, because its endpoint was
on a loopback address and the loop counted it as "local". A billed model is never the loop's free
local brain; a free model on this machine still is."""
from __future__ import annotations

from types import SimpleNamespace

from core.clean_loop import brain


def _manifest(*, base_url: str, cost_class: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        source_type="http",
        adapter_type="openai_compatible",
        runtime_config={"base_url": base_url},
        metadata={"cost_class": cost_class} if cost_class else {},
        model_name="m",
        provider_id="p:m",
    )


def test_a_paid_model_on_a_loopback_address_is_not_local():
    assert not brain._is_local(_manifest(base_url="http://127.0.0.1:9911/v1", cost_class="paid_cloud"))


def test_a_free_model_on_this_machine_is_still_local():
    assert brain._is_local(_manifest(base_url="http://127.0.0.1:11434"))
    assert brain._is_local(_manifest(base_url="http://localhost:11434", cost_class="free_local"))


def test_a_remote_model_is_not_local():
    assert not brain._is_local(_manifest(base_url="https://api.example.test/v1"))
