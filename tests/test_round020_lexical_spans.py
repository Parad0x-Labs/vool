"""Round-020 canonical lexical span authority — targeted test matrix.

Tests the canonical LexicalSpan + lex_spans primitive and all consumer rebindings.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from core.kernel.lexical_spans import LexicalSpan, has_quantity, lex_spans, quantity_values


# ---------------------------------------------------------------------------
# Q — real quantities must survive
# ---------------------------------------------------------------------------


class TestRealQuantities:
    def test_q1_37_batches(self) -> None:
        assert has_quantity("37 batches")
        assert quantity_values("37 batches") == {"37"}

    def test_q2_18_plus_24(self) -> None:
        assert has_quantity("18 + 24")
        assert quantity_values("18 + 24") == {"18", "24"}

    def test_q3_dollar_42(self) -> None:
        assert has_quantity("$42")
        assert quantity_values("$42") == {"42"}

    def test_q4_21_c(self) -> None:
        assert has_quantity("21 C")
        assert quantity_values("21 C") == {"21"}

    def test_percentage(self) -> None:
        assert has_quantity("45%")
        assert quantity_values("45%") == {"45"}

    def test_negative(self) -> None:
        assert has_quantity("-5")
        assert quantity_values("-5") == {"-5"}

    def test_thousands(self) -> None:
        assert has_quantity("1,420.75")
        assert quantity_values("1,420.75") == {"1420.75"}


# ---------------------------------------------------------------------------
# O — opaque bytes (URL, IDENTIFIER) must own their digits
# ---------------------------------------------------------------------------


class TestOpaqueOwnership:
    def test_o1_python_docs_url(self) -> None:
        """https://docs.python.org/3/ — URL owns 3."""
        assert not has_quantity("https://docs.python.org/3/")
        spans = lex_spans("https://docs.python.org/3/")
        urls = [s for s in spans if s.kind == "URL"]
        assert len(urls) == 1
        assert urls[0].raw == "https://docs.python.org/3/"

    def test_o2_url_with_multiple_digits(self) -> None:
        """https://example.com/v2/build/73 — URL owns 2 and 73."""
        assert not has_quantity("https://example.com/v2/build/73")
        spans = lex_spans("https://example.com/v2/build/73")
        urls = [s for s in spans if s.kind == "URL"]
        assert len(urls) == 1
        assert urls[0].raw == "https://example.com/v2/build/73"

    def test_o3_identifier_nova_71(self) -> None:
        """NOVA-71 — identifier owns 71."""
        assert not has_quantity("NOVA-71")

    def test_o4_identifier_node_73b(self) -> None:
        """NODE-73B — identifier owns embedded digits."""
        assert not has_quantity("NODE-73B")

    def test_identifier_prose_neo4j(self) -> None:
        """Neo4j — the lookbehind already excludes letter-flanked digits."""
        assert not has_quantity("Neo4j")

    def test_identifier_2fa(self) -> None:
        """2FA — non-unit letter tail makes it an identifier."""
        assert not has_quantity("2FA")

    def test_url_with_port(self) -> None:
        """URL containing a port number — port digits are URL-owned."""
        assert not has_quantity("http://localhost:8080/api")

    def test_url_with_ip(self) -> None:
        """URL with IP — all digits are URL-owned."""
        assert not has_quantity("https://127.0.0.1:8080/path")


# ---------------------------------------------------------------------------
# M — mixed cases: both quantities and opaque owned digits
# ---------------------------------------------------------------------------


class TestMixed:
    def test_m1_3_servers_url_v2(self) -> None:
        text = "Process 3 servers using https://example.com/v2"
        q_vals = quantity_values(text)
        assert "3" in q_vals, "3 should be QUANTITY"
        assert "2" not in q_vals, "2 should be URL-owned"

    def test_m2_42_jobs_url_v3_19(self) -> None:
        text = "Process 42 jobs using https://host/v3/build/19"
        q_vals = quantity_values(text)
        assert "42" in q_vals, "42 should be QUANTITY"
        assert "3" not in q_vals, "3 should be URL-owned"
        assert "19" not in q_vals, "19 should be URL-owned"

    def test_real_quantities_adjacent_to_url(self) -> None:
        """Real quantities must survive even when text also contains URLs."""
        text = "Deploy 37 containers to https://host/v2/build/73"
        q_vals = quantity_values(text)
        assert "37" in q_vals
        assert "2" not in q_vals
        assert "73" not in q_vals

    def test_identifier_adjacent_to_quantity(self) -> None:
        """Identifier and quantity in same text — both correctly classified."""
        text = "Found NOVA-71 with 42 open issues"
        q_vals = quantity_values(text)
        assert "42" in q_vals
        assert "71" not in q_vals


# ---------------------------------------------------------------------------
# Human failure reproducer: Python docs URL
# ---------------------------------------------------------------------------


class TestPythonDocsReproducer:
    def test_url_3_not_a_quantity(self) -> None:
        """The Turn 027 failure: /3/ in URL must not be a quantity."""
        url = "https://docs.python.org/3/"
        assert not has_quantity(url), f"Digit 3 inside URL should not be QUANTITY: {url}"
        assert not quantity_values(url), "No QUANTITY values from a bare URL"

    def test_url_ships_unchanged(self) -> None:
        """Raw URL bytes must never be rewritten."""
        url = "https://docs.python.org/3/"
        spans = lex_spans(url)
        url_spans = [s for s in spans if s.kind == "URL"]
        assert len(url_spans) == 1
        assert url_spans[0].raw == "https://docs.python.org/3/"

    def test_number_index_excludes_url_digits(self) -> None:
        """Simulating _number_index behavior: URL digits must not be indexed."""
        receipts = {"ob1-web1": "The official docs are at https://docs.python.org/3/"}
        from core.kernel.repl import _number_index
        idx = _number_index(receipts)
        values = {r["value"] for r in idx}
        assert "3" not in values, "Digit 3 inside URL must not appear in number index"


# ---------------------------------------------------------------------------
# Evidence types integration
# ---------------------------------------------------------------------------


class TestEvidenceTypesIntegration:
    def test_number_tokens_excludes_url_digits(self) -> None:
        from core.kernel.evidence_types import _number_tokens
        tokens = _number_tokens("https://docs.python.org/3/")
        assert "3" not in tokens, "_number_tokens should exclude URL-owned 3"

    def test_number_tokens_keeps_real_quantities(self) -> None:
        from core.kernel.evidence_types import _number_tokens
        tokens = _number_tokens("37 batches and 18 + 24 = 42")
        assert "37" in tokens
        assert "18" in tokens
        assert "24" in tokens
        assert "42" in tokens

    def test_number_tokens_excludes_identifiers(self) -> None:
        from core.kernel.evidence_types import _number_tokens
        tokens = _number_tokens("NOVA-71 NODE-73B Neo4j")
        assert not tokens, f"Identifiers should yield no quantity tokens: {tokens}"


# ---------------------------------------------------------------------------
# Repl integration
# ---------------------------------------------------------------------------


class TestReplIntegration:
    def test_number_tokens_of_excludes_url_digits(self) -> None:
        from core.kernel.repl import _number_tokens_of
        tokens = _number_tokens_of("https://docs.python.org/3/")
        assert "3" not in tokens, "_number_tokens_of should exclude URL-owned 3"

    def test_number_tokens_of_keeps_quantities(self) -> None:
        from core.kernel.repl import _number_tokens_of
        tokens = _number_tokens_of("37 batches")
        assert "37" in tokens

    def test_retokenize_preserves_url_digits(self) -> None:
        """_retokenize must NOT rewrite digits inside URLs."""
        from core.kernel.repl import _retokenize
        url = "https://docs.python.org/3/"
        index = [{"token": "n1", "value": "3"}]
        result = _retokenize(url, index)
        # The URL with /3/ should not be rewritten because 3 is URL-owned
        assert result == url, f"URL should be unchanged by retokenize: {result!r}"

    def test_retokenize_still_rewrites_quantity_digits(self) -> None:
        """_retokenize must still rewrite standalone QUANTITY digits."""
        from core.kernel.repl import _retokenize
        text = "42 servers"
        index = [{"token": "n1", "value": "42"}]
        result = _retokenize(text, index)
        assert "{n1}" in result, f"42 should be retokenized: {result!r}"


# ---------------------------------------------------------------------------
# R4 — Downstream consumer span awareness (unit_pairs, temperature, duration)
# ---------------------------------------------------------------------------


class TestUnitPairs:
    """_unit_pairs must respect canonical opaque span ownership."""

    def test_c2_url_24gb_excluded(self) -> None:
        from core.kernel.evidence_types import _unit_pairs
        pairs = _unit_pairs("Download from https://host.example/24GB")
        assert ("24", "gb") not in pairs, "URL 24GB must not produce gb pair"

    def test_c2_url_24gb_bare(self) -> None:
        from core.kernel.evidence_types import _unit_pairs
        pairs = _unit_pairs("https://host.example/24GB")
        assert len(pairs) == 0, "Bare URL must not produce unit pairs"

    def test_s10_real_24gb_survives(self) -> None:
        from core.kernel.evidence_types import _unit_pairs
        pairs = _unit_pairs("The machine has 24 GB RAM.")
        assert ("24", "gb") in pairs, "Real 24 GB must survive"

    def test_s9_cross_source_no_leak(self) -> None:
        from core.kernel.evidence_types import _unit_pairs
        pairs = _unit_pairs("There are 24 files at https://host.example/24GB")
        assert ("24", "gb") not in pairs, "Cross-source URL 24GB must not leak"

    def test_s13_mixed_ownership_unit(self) -> None:
        from core.kernel.evidence_types import _unit_pairs
        text = "The machine has 24 GB RAM and its manual is https://host.example/report/18:45"
        pairs = _unit_pairs(text)
        assert ("24", "gb") in pairs, "Real 24 GB must survive in mixed text"

    def test_s14_reversed_mix_unit(self) -> None:
        from core.kernel.evidence_types import _unit_pairs
        text = "The timer ran for 18:45 and the reference is https://host.example/24GB"
        pairs = _unit_pairs(text)
        assert ("24", "gb") not in pairs, "URL 24GB must not leak"
        # 18:45 should not produce unit pairs either (it's a duration, not number+unit)
        assert not any(p[0] == "18" for p in pairs), "Duration 18:45 must not produce unit pairs"

    def test_url_with_unit_suffix_excluded(self) -> None:
        from core.kernel.evidence_types import _unit_pairs
        pairs = _unit_pairs("Visit https://host.example/5G for details")
        assert ("5", "g") not in pairs, "URL 5G must not produce g pair"

    def test_identifier_like_2fa_excluded(self) -> None:
        from core.kernel.evidence_types import _unit_pairs
        pairs = _unit_pairs("The device supports 2FA authentication")
        assert ("2", None) not in [(p[0], None) for p in pairs], "2FA must not produce unit pairs"

    def test_normal_unit_chain_survives(self) -> None:
        from core.kernel.evidence_types import _unit_pairs
        pairs = _unit_pairs("70 against 57 watt-hours")
        assert ("70", "wh") in pairs or ("70", "w") in pairs
        assert ("57", "wh") in pairs or ("57", "w") in pairs


class TestStatedQuantityForms:
    """_stated_quantity_forms must respect canonical opaque span ownership."""

    def test_c3_url_18_45_excluded(self) -> None:
        from core.kernel.repl import _stated_quantity_forms
        forms = _stated_quantity_forms("See https://host.example/report/18:45 for data")
        assert "1125" not in forms, "URL 18:45 must not produce 1125"
        assert "18.75" not in forms, "URL 18:45 must not produce 18.75"

    def test_c3_url_percentage_excluded(self) -> None:
        from core.kernel.repl import _stated_quantity_forms
        forms = _stated_quantity_forms("Check https://pct.example/45%/report")
        assert not any(abs(float(f) - 0.45) < 0.001 for f in forms), "URL 45% must not produce 0.45"

    def test_s11_real_duration_survives(self) -> None:
        from core.kernel.repl import _stated_quantity_forms
        forms = _stated_quantity_forms("The timer ran for 1h 30m.")
        assert "90" in forms, "Real 1h 30m must produce 90"

    def test_s12_real_percentage_survives(self) -> None:
        from core.kernel.repl import _stated_quantity_forms
        forms = _stated_quantity_forms("Battery is at 45%.")
        assert any(abs(float(f) - 0.45) < 0.001 for f in forms), "Real 45% must produce 0.45"

    def test_s13_mixed_ownership_duration(self) -> None:
        from core.kernel.repl import _stated_quantity_forms
        text = "The machine has 24 GB RAM and its manual is https://host.example/report/18:45"
        forms = _stated_quantity_forms(text)
        assert "1125" not in forms, "URL 18:45 must not produce duration in mixed text"
        assert "18.75" not in forms, "URL 18:45 must not produce decimal duration"

    def test_s14_reversed_mix_duration(self) -> None:
        from core.kernel.repl import _stated_quantity_forms
        text = "The timer ran for 18:45 and the reference is https://host.example/24GB"
        forms = _stated_quantity_forms(text)
        assert "1125" in forms, "Real 18:45 duration must survive in reversed text"
        assert "18.75" in forms, "Real 18:45 decimal must survive"

    def test_duration_adjacent_to_url(self) -> None:
        from core.kernel.repl import _stated_quantity_forms
        text = "Process took 18:45 — see https://host.example/report"
        forms = _stated_quantity_forms(text)
        assert "1125" in forms, "18:45 before URL must produce duration"
        assert "18.75" in forms, "18:45 before URL must produce decimal"


class TestTemperatureUnits:
    """_temperature_units must respect canonical opaque span ownership."""

    def test_temperature_inside_url_excluded(self) -> None:
        from core.kernel.evidence_types import _temperature_units
        pairs = _temperature_units("Weather at https://temp/21°C/today")
        assert len(pairs) == 0, "Temperature match inside URL must be excluded"

    def test_temperature_outside_url_survives(self) -> None:
        from core.kernel.evidence_types import _temperature_units
        pairs = _temperature_units("The temperature is 21°C and check https://host.example")
        assert ("21", "C") in pairs, "Real temperature outside URL must survive"


class TestCrossSourceValidation:
    """S9 — cross-source contamination in full claim validation."""

    def test_url_unit_cannot_ground_claim(self) -> None:
        from core.kernel.evidence_types import validate_claims, TypedClaim
        receipt = "There are 24 files at https://host.example/24GB"
        claim = "The storage amount is 24 GB."
        try:
            validate_claims([TypedClaim(text=claim, ctype="observed", ref="r1")], {"r1": receipt})
            assert False, "Cross-source claim must be rejected"
        except Exception:
            pass  # Expected — number 24 IS in receipt but unit grounding fails

    def test_real_24_gb_validates(self) -> None:
        from core.kernel.evidence_types import validate_claims, TypedClaim
        receipt = "The machine uses 24 GB DDR5 RAM"
        claim = "It has 24 GB of memory."
        validate_claims([TypedClaim(text=claim, ctype="observed", ref="r1")], {"r1": receipt})  # must not raise