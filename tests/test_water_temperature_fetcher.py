"""AUD-20260829-003, C4 — the Baltic water temperature is caveated, never one universal number.

C4 is graded FAIL and "never moved at any commit". The lane is written to the right law -- its
module docstring says "NO model, NO prose fallback, NO invented numbers: any failure returns
None" -- and NOTHING EXECUTED IT. Grepping `water_temperature_lookup`, `resolve_water_point` and
`marine-api` across `tests/` returns no hits: the geocoding, the anchor resolution, the marine
call, the hourly fallback and the null handling had never been run by a test.

A law nobody exercises is a claim, so this is the harness. Every arm patches the ONE remote seam
the module uses and asserts the structural outcome -- a typed result with its own label and named
source, or None. No arm asserts prose.

The patch idiom is a FACTORY, not `return_value=` over a `@contextlib.contextmanager`: that form
hands back one single-use instance, and this lane makes up to four remote calls, so a shared
instance dies on the second with `AttributeError: ... has no attribute 'args'`. That exact defect
silently disabled three weather decline tests in this repo.
"""
from __future__ import annotations

import json
from unittest import mock

import pytest

from core.fresh_data import water_temperature as wt

_BALTIC = "the Baltic Sea"


class _Response:
    """Hand-rolled so it can be entered as many times as the failover chain needs."""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def read(self, *_args) -> bytes:
        return self._payload


def _serving(*payloads: dict):
    """A `_open_remote` stand-in that answers each call in turn and records the URLs it saw."""
    calls: list[str] = []
    queue = [json.dumps(p).encode() for p in payloads]

    def _open(request, timeout=None, **_kwargs):
        calls.append(getattr(request, "full_url", str(request)))
        payload = queue.pop(0) if len(queue) > 1 else queue[0]
        return _Response(payload)

    _open.calls = calls
    return _open


def _marine(current: dict | None = None, hourly: dict | None = None) -> dict:
    return {"current": current or {}, "hourly": hourly or {}}


def test_the_happy_path_returns_a_typed_result_with_its_own_point_and_source():
    """The Baltic anchor short-circuits geocoding, so this is one marine call."""
    opener = _serving(
        _marine(current={"sea_surface_temperature": 17.2, "time": "2026-09-05T09:00"})
    )
    with mock.patch.object(wt, "_open_remote", side_effect=opener):
        result = wt.water_temperature_lookup(_BALTIC)

    assert result is not None
    assert result.temperature_c == 17.2
    assert result.temperature_f == pytest.approx(63.0, abs=0.1)
    # THE CAVEAT: a NAMED POINT, not "the Baltic". One universal number for a whole sea is
    # exactly what this criterion forbids.
    assert "Jurmala" in result.label and "Baltic" in result.label
    assert result.source == "open-meteo.com (marine)"
    assert result.observed == "2026-09-05T09:00"
    assert any("marine" in url for url in opener.calls), opener.calls


def test_a_transport_failure_returns_none_and_never_a_number():
    def _boom(_request, timeout=None, **_kwargs):
        raise TimeoutError("marine endpoint blackholed")

    with mock.patch.object(wt, "_open_remote", side_effect=_boom):
        assert wt.water_temperature_lookup(_BALTIC) is None


def test_an_all_null_payload_returns_none():
    opener = _serving(_marine(current={"sea_surface_temperature": None, "time": ""},
                              hourly={"time": [], "sea_surface_temperature": []}))
    with mock.patch.object(wt, "_open_remote", side_effect=opener):
        assert wt.water_temperature_lookup(_BALTIC) is None


def test_a_non_numeric_value_returns_none():
    opener = _serving(_marine(current={"sea_surface_temperature": "warm", "time": "x"}))
    with mock.patch.object(wt, "_open_remote", side_effect=opener):
        assert wt.water_temperature_lookup(_BALTIC) is None


def test_an_empty_geocoder_returns_none_for_an_unanchored_place():
    """A place with no anchor must decline rather than resolve to something else."""
    opener = _serving({"results": []})
    with mock.patch.object(wt, "_open_remote", side_effect=opener):
        assert wt.water_temperature_lookup("the Sea of Nowhere At All") is None


def test_the_hourly_fallback_carries_its_own_timestamp_not_the_current_one():
    """When `current` has no reading the last hourly value at or before now is used, and it is
    labelled with ITS OWN time -- a reading is only as current as its timestamp says."""
    opener = _serving(
        _marine(
            current={"sea_surface_temperature": None, "time": "2026-09-05T09:00"},
            hourly={
                "time": ["2000-01-01T00:00", "2000-01-01T01:00"],
                "sea_surface_temperature": [16.4, 16.9],
            },
        )
    )
    with mock.patch.object(wt, "_open_remote", side_effect=opener):
        result = wt.water_temperature_lookup(_BALTIC)

    assert result is not None
    assert result.temperature_c == 16.9
    assert result.observed == "2000-01-01T01:00"


# ------------------------------------------------------------------ the rendered rows


def _outcome(ok: bool, result: dict | None, failure_reason: str = ""):
    from types import SimpleNamespace

    return SimpleNamespace(
        ok=ok,
        result=result,
        failure_reason=failure_reason,
        subtask=SimpleNamespace(
            operation="water_temperature", arguments={"place": _BALTIC}, unit_ids=(), slice_id=""
        ),
    )


def test_the_served_row_names_the_point_the_unit_and_the_source():
    from core.agent_runtime.live_data_render import _water_blocks

    blocks: list[str] = []
    _water_blocks(
        blocks,
        [
            _outcome(
                True,
                {
                    "label": "Jurmala, Latvia (Baltic Sea)",
                    "temperature_c": 17.2,
                    "temperature_f": 63.0,
                    "source": "open-meteo.com (marine)",
                    "observed": "2026-09-05T09:00",
                },
            )
        ],
    )
    line = "\n".join(blocks)
    assert "Jurmala, Latvia (Baltic Sea)" in line
    assert "17.2°C" in line and "°F" in line
    assert "open-meteo.com (marine)" in line


def test_a_failed_observation_renders_its_reason_and_no_number():
    from core.agent_runtime.live_data_render import _water_blocks

    blocks: list[str] = []
    _water_blocks(blocks, [_outcome(False, None, failure_reason="marine endpoint unreachable")])
    line = "\n".join(blocks)
    assert "unavailable" in line and "marine endpoint unreachable" in line
    assert "°C" not in line, line
