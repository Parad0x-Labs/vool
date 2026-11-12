"""A paid-model cap that is not a finite number is never a ceiling.

Red-team M2 (0.7.7 train, 2026-10-30): ``_positive_policy_float`` accepted "inf", which lifted every paid-model
ceiling, and the spend ledger admitted any call when a cap was NaN (``projected > nan`` is False): five $900
reservations went through. A non-finite policy value now reads as the default, and the ledger refuses a
non-finite cap outright. NO REAL CALL IS MADE: these drive the ledger and the policy reader only.
"""
from __future__ import annotations

import math

import pytest

from core import paid_call_reservation, policy_engine
from core.model_spend_ledger import SpendLimits, reserve_spend


@pytest.fixture(autouse=True)
def _isolated_runtime(tmp_path, monkeypatch):
    from core import runtime_paths
    from storage.db import configure_default_db_path, reset_default_connection

    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    configure_default_db_path(tmp_path / "data" / "test.db")
    reset_default_connection()
    yield
    reset_default_connection()
    configure_default_db_path(None)
    runtime_paths.configure_runtime_home(None)


@pytest.mark.parametrize("raw", ["inf", "Infinity", "-inf", "nan", float("inf"), float("nan")])
@pytest.mark.parametrize("key, default", [
    ("per_call_max_usd", paid_call_reservation._DEFAULT_PER_CALL_USD),
    ("per_task_max_usd", paid_call_reservation._DEFAULT_PER_TASK_USD),
    ("daily_max_usd", paid_call_reservation._DEFAULT_DAILY_USD),
    ("monthly_max_usd", paid_call_reservation._DEFAULT_MONTHLY_USD),
])
def test_a_non_finite_policy_cap_reads_as_the_default(monkeypatch, raw, key, default):
    real = policy_engine.get
    wanted = f"model_orchestration.paid_call_{key}"
    monkeypatch.setattr(policy_engine, "get", lambda name, fallback=None: raw if name == wanted else real(name, fallback))
    value = paid_call_reservation._positive_policy_float(key, default)
    assert value == default and math.isfinite(value)


def _limits(**override):
    base = {"per_call_usd": 1000.0, "per_task_usd": 1000.0, "daily_usd": 1000.0, "monthly_usd": 1000.0}
    return SpendLimits(**{**base, **override})


@pytest.mark.parametrize("field", ["per_task_usd", "daily_usd", "monthly_usd", "per_call_usd"])
@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_the_ledger_refuses_under_a_non_finite_cap(field, bad):
    granted = []
    for n in range(5):
        try:
            granted.append(reserve_spend(model_call_id=f"call-{n}", task_id="task-1", subtask_id="s", model_id="paid/model",
                                         maximum_usd=900.0, limits=_limits(**{field: bad})))
        except PermissionError:
            pass
    assert granted == []


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_a_device_cap_that_is_not_finite_refuses(bad):
    with pytest.raises(PermissionError, match="device_monthly_spend_cap_exceeded"):
        reserve_spend(model_call_id="call-d", task_id="task-d", subtask_id="s", model_id="paid/model", maximum_usd=1.0,
                      limits=_limits(spender="phone:abc", spender_monthly_usd=bad))


def test_finite_caps_still_admit_and_bind():
    limits = _limits(daily_usd=2.0)
    reserve_spend(model_call_id="ok-1", task_id="t", subtask_id="s", model_id="paid/model", maximum_usd=1.5, limits=limits)
    with pytest.raises(PermissionError, match="daily_spend_cap_exceeded"):
        reserve_spend(model_call_id="ok-2", task_id="t", subtask_id="s", model_id="paid/model", maximum_usd=1.0, limits=limits)
