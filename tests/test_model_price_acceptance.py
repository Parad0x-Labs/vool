"""The accepted-ceiling law, server-side: record on confirm, block on rise, ask again on doubt.

Every test drives the real store under an isolated VOOL_HOME (tmp_path) and, where the seam is
the HTTP handler, the real dispatch path — no mocked mechanism stands in for the gate.
"""

from __future__ import annotations

import json
from unittest import mock

import pytest


@pytest.fixture()
def isolated_home(tmp_path, monkeypatch):
    from core import runtime_paths

    monkeypatch.setattr(runtime_paths, "VOOL_HOME", tmp_path, raising=False)
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    # data_path derives from the module state at call time; patch its root resolver defensively.
    original = runtime_paths.data_path

    def _patched(*parts):
        base = tmp_path / "data"
        base.mkdir(parents=True, exist_ok=True)
        out = base
        for part in parts:
            out = out / part
        return out

    monkeypatch.setattr(runtime_paths, "data_path", _patched)
    monkeypatch.setattr("core.model_price_acceptance.data_path", _patched)
    return tmp_path


def _stub_rates(monkeypatch, rates):
    monkeypatch.setattr(
        "core.model_price_acceptance.current_rates_for",
        lambda provider, model, _r=rates: dict(_r) if _r else None,
    )


def test_record_and_read_roundtrip(isolated_home, monkeypatch) -> None:
    from core import model_price_acceptance as mpa

    _stub_rates(monkeypatch, {"prompt_usd_per_m": 0.4, "completion_usd_per_m": 1.6})
    record = mpa.record_acceptance("openrouter", "openai/gpt-4.1-mini", cost_state="paid")
    assert record["rates_known"] is True and record["prompt_usd_per_m"] == 0.4
    read = mpa.acceptance_for("openrouter", "openai/gpt-4.1-mini")
    assert read and read["completion_usd_per_m"] == 1.6
    assert mpa.list_acceptances()[0]["model"] == "openai/gpt-4.1-mini"


def test_rise_blocks_and_decrease_never_blocks(isolated_home, monkeypatch) -> None:
    from core import model_price_acceptance as mpa

    _stub_rates(monkeypatch, {"prompt_usd_per_m": 0.4, "completion_usd_per_m": 1.6})
    mpa.record_acceptance("openrouter", "openai/gpt-4.1-mini", cost_state="paid")

    _stub_rates(monkeypatch, {"prompt_usd_per_m": 1.6, "completion_usd_per_m": 6.4})
    rise = mpa.price_above_acceptance("openrouter", "openai/gpt-4.1-mini")
    assert rise is not None
    assert rise["accepted"]["prompt_usd_per_m"] == 0.4
    assert rise["current"]["prompt_usd_per_m"] == 1.6

    _stub_rates(monkeypatch, {"prompt_usd_per_m": 0.2, "completion_usd_per_m": 0.8})
    assert mpa.price_above_acceptance("openrouter", "openai/gpt-4.1-mini") is None

    _stub_rates(monkeypatch, {"prompt_usd_per_m": 0.4, "completion_usd_per_m": 1.6})
    assert mpa.price_above_acceptance("openrouter", "openai/gpt-4.1-mini") is None


def test_no_acceptance_or_no_rates_means_the_ordinary_gate(isolated_home, monkeypatch) -> None:
    from core import model_price_acceptance as mpa

    _stub_rates(monkeypatch, {"prompt_usd_per_m": 9.0, "completion_usd_per_m": 9.0})
    assert mpa.price_above_acceptance("openrouter", "never/confirmed") is None
    # Unknown-rate acceptance (operator accepted UNCLASSIFIED cost) can never arm the rise gate.
    _stub_rates(monkeypatch, None)
    record = mpa.record_acceptance("openrouter", "mystery/model", cost_state="unknown")
    assert record["rates_known"] is False
    _stub_rates(monkeypatch, {"prompt_usd_per_m": 9.0, "completion_usd_per_m": 9.0})
    assert mpa.price_above_acceptance("openrouter", "mystery/model") is None


def test_corrupt_store_reads_as_empty_and_reasks(isolated_home, monkeypatch) -> None:
    from core import model_price_acceptance as mpa

    path = mpa._store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert mpa.list_acceptances() == []
    assert mpa.acceptance_for("openrouter", "any/model") is None


def _post_cloud_model(body: dict, client_host: str = "127.0.0.1"):
    """Drive the REAL dispatch handler for POST /api/cloud/model."""
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_post

    res = dispatch_post(
        path="/api/cloud/model",
        body=body,
        headers={"content-type": "application/json"},
        runtime=RuntimeServices(display_name="N"),
        model_name="vool",
        workspace_root_provider=lambda: "/tmp",
        client_host=client_host,
    )
    return res.status, json.loads(res.body.decode("utf-8"))


def _get(path: str, client_host: str):
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get

    res = dispatch_get(
        path=path,
        query={},
        runtime=RuntimeServices(display_name="N"),
        model_name="vool",
        client_host=client_host,
    )
    return res.status, json.loads(res.body.decode("utf-8"))


def test_served_pin_gate_emits_price_above_accepted(isolated_home, monkeypatch) -> None:
    """The real handler: recorded ceiling + risen catalog -> 409 price_above_accepted with the
    exact before/after; confirm_paid then lands the pin and REWRITES the ceiling."""
    from core import model_price_acceptance as mpa

    _stub_rates(monkeypatch, {"prompt_usd_per_m": 0.4, "completion_usd_per_m": 1.6})
    mpa.record_acceptance("openrouter", "openai/gpt-4.1-mini", cost_state="paid")
    _stub_rates(monkeypatch, {"prompt_usd_per_m": 1.6, "completion_usd_per_m": 6.4})

    monkeypatch.setattr(
        "core.cloud_model_control.classify_cloud_model_cost",
        lambda **_k: {"cost_state": "paid", "reason_code": "", "row_verified_free": False},
    )
    with mock.patch(
        "core.cloud_model_control.set_cloud_model",
        return_value=(True, "pinned", "openai/gpt-4.1-mini"),
    ) as set_model:
        status, payload = _post_cloud_model({"provider": "openrouter", "model": "openai/gpt-4.1-mini"})
        assert status == 409
        assert payload["code"] == "price_above_accepted"
        assert payload["accepted"]["prompt_usd_per_m"] == 0.4
        assert payload["current"]["prompt_usd_per_m"] == 1.6
        set_model.assert_not_called()

        status2, payload2 = _post_cloud_model({"provider": "openrouter", "model": "openai/gpt-4.1-mini", "confirm_paid": True})
        assert status2 == 200 and payload2["ok"] is True
        set_model.assert_called_once()

    rewritten = mpa.acceptance_for("openrouter", "openai/gpt-4.1-mini")
    assert rewritten and rewritten["prompt_usd_per_m"] == 1.6, "confirmation must rewrite the ceiling"


def test_acceptances_endpoint_is_owner_local(isolated_home) -> None:
    status, payload = _get("/api/cloud/acceptances", "203.0.113.9")
    assert status == 403 and payload.get("error") == "owner_local_required"
    status2, payload2 = _get("/api/cloud/acceptances", "127.0.0.1")
    assert status2 == 200 and payload2["ok"] is True and payload2["acceptances"] == []
