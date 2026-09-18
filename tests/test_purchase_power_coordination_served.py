"""Served proof: a coordinated purchasing-power ask is computed, not quoted, and the chip says so.

Measured on the built candidate 60a91da3 (isolated bundle drive, 2026-09-07): "How much gold and how
much silver can I buy with one bitcoin right now?" came back as two price quotes, no bitcoin fetch,
no derived figure, chip "1/1 lookup", state VERIFIED, turn closed complete. Three decisions were
wrong in sequence -- the splitter cut the shared predicate, the amount grammar read digits only, and
two "and"-coordinated targets read as an ambiguity -- and the chip counted plan nodes only.

Through the real daemon with dated fixture quotes (BTC 63250 USD; gold 4476.60 and silver 66.75 USD
per troy ounce), the expected figures are computed here from the same fixture bodies the runtime
reads, never typed in.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import tests._reader_served_rig as rig
from tests.test_proof_chip_served_lookups import MANIFEST, _daemon, _turn

pytestmark = pytest.mark.timeout(900)


def _fixture_prices() -> dict[str, float]:
    manifest = json.loads(Path(MANIFEST).read_text())
    prices: dict[str, float] = {}
    for rule in manifest.get("rules") or manifest:
        body = rule.get("json")
        query = str(rule.get("query_contains") or "")
        if str(rule.get("path_prefix") or "").endswith("/simple/price") and isinstance(body, dict):
            prices["btc"] = float(body["bitcoin"]["usd"])
        if isinstance(body, dict) and "chart" in body and query in ("GC=F", "SI=F"):
            prices["gold" if query == "GC=F" else "silver"] = float(body["chart"]["result"][0]["meta"]["regularMarketPrice"])
    assert set(prices) == {"btc", "gold", "silver"}, prices
    return prices


def _numbers(text: str) -> list[float]:
    values: list[float] = []
    for token in re.findall(r"\d[\d,]*(?:\.\d+)?", text):
        try:
            values.append(float(token.replace(",", "")))
        except ValueError:
            continue
    return values


def _has_number_near(text: str, expected: float, rel: float = 0.005) -> bool:
    return any(abs(value - expected) <= abs(expected) * rel for value in _numbers(text))


def test_both_amounts_are_computed_and_the_chip_names_three_lookups_and_two_bound_steps(tmp_path):
    prices = _fixture_prices()
    with rig.CapturingProvider(default="Here are the figures.") as provider:
        daemon, _log = _daemon(tmp_path, provider, MANIFEST)
        try:
            _rid, proof, text = _turn(daemon, "How much gold and how much silver can I buy with one bitcoin right now?")
        finally:
            daemon.stop()
    body = text.split("Could not be answered")[0]
    assert _has_number_near(body, prices["btc"] / prices["gold"]), text
    assert _has_number_near(body, prices["btc"] / prices["silver"]), text
    compact = proof["compact"]
    coverage = compact["coverage"]
    assert coverage["observations"]["succeeded"] >= 3, coverage
    assert coverage["observations"]["failed"] == 0 and coverage["observations"]["pending"] == 0, coverage
    assert coverage["derived"] == {"total": 2, "bound": 2, "unbound": 0}, coverage
    assert "unbound" not in coverage["label"], coverage
    assert "Could not be answered" not in text, text


def test_a_differently_worded_single_target_ask_on_the_same_lane(tmp_path):
    prices = _fixture_prices()
    with rig.CapturingProvider(default="Here are the figures.") as provider:
        daemon, _log = _daemon(tmp_path, provider, MANIFEST)
        try:
            _rid, proof, text = _turn(daemon, "If I sell half a bitcoin, how many ounces of silver does that get me?")
        finally:
            daemon.stop()
    assert _has_number_near(text.split("Could not be answered")[0], 0.5 * prices["btc"] / prices["silver"]), text
    coverage = proof["compact"]["coverage"]
    assert coverage["derived"]["total"] == 1 and coverage["derived"]["bound"] == 1, coverage
    assert coverage["observations"]["succeeded"] >= 2, coverage
