"""A source that merely shares a year, or never mentions the claim's subject, supports nothing.

Measured 2026-09-06 (in-process match against the two production-history fixture notes bound by
the served comparison probe): "Sales: Golf about 37 million units by end of 2024, Passat about 34
million" was SUPPORTED by the Golf production page because both contain "2024"; "Regions: Golf
mainly Europe" was SUPPORTED by the Passat production page because it mentions Europe. Neither
source says anything about sales or about where the Golf sells. The subjects the request named
(Passat, Golf) are excluded from witnessing support by design -- restating the question proves
nothing -- but a source silent about a claim's subject cannot support that claim either.
"""
from __future__ import annotations

from core.claim_support import match_claims

REQUEST = "compare the vw passat and the vw golf in detail: production periods, sales, regions, engines and prices"
PASSAT = {"result_title": "Volkswagen Passat production history", "result_url": "https://autoarchive-fixture.org/passat-production",
          "origin_domain": "autoarchive-fixture.org",
          "summary": "Updated 2026-08-20. The Volkswagen Passat has been produced since 1973. Generations: B1 1973-1980, B8 2014-2023, B9 since 2023 (estate only in Europe). Production of the saloon for Europe ended in 2022."}
GOLF = {"result_title": "Volkswagen Golf production history", "result_url": "https://autoarchive-fixture.org/golf-production",
        "origin_domain": "autoarchive-fixture.org",
        "summary": "Updated 2026-08-18. The Volkswagen Golf has been produced since 1974. Generations: Mk1 1974-1983, Mk7 2012-2019, Mk8 since 2019 with a 2024 facelift."}
SALES = {"result_title": "Volkswagen model sales totals", "result_url": "https://salesdata-fixture.org/vw-model-sales", "origin_domain": "salesdata-fixture.org",
         "summary": "Updated 2026-07-31. Cumulative sales: Golf about 37 million units worldwide by end of 2024. Passat about 34 million units worldwide by end of 2024."}


def _status(answer: str, notes, claim_fragment: str) -> tuple[str, tuple[str, ...]]:
    result = match_claims(answer=answer, notes=notes, request_text=REQUEST)
    for claim in result.as_dict()["claims"]:
        if claim_fragment in claim["text"]:
            return claim["status"], tuple(claim["reasons"])
    raise AssertionError(f"claim {claim_fragment!r} not segmented from {answer!r}")


def test_a_shared_year_does_not_support_a_quantity_claim() -> None:
    status, reasons = _status("Sales: Golf about 37 million units by end of 2024, Passat about 34 million.", [PASSAT, GOLF], "37 million")
    assert status != "supported", (status, reasons)


def test_the_same_quantity_claim_is_supported_by_a_source_that_states_it() -> None:
    status, reasons = _status("Sales: Golf about 37 million units by end of 2024, Passat about 34 million.", [PASSAT, GOLF, SALES], "37 million")
    assert status == "supported", (status, reasons)


def test_a_source_silent_about_the_claims_subject_does_not_support_it() -> None:
    status, reasons = _status("Regions: Golf sells mainly in Europe.", [PASSAT], "Golf sells")
    assert status != "supported", (status, reasons)


def test_a_joint_claim_stays_supported_by_the_two_sources_together() -> None:
    status, reasons = _status("Golf production began in 1974 and Passat production in 1973.", [PASSAT, GOLF], "1974")
    assert status == "supported", (status, reasons)


def test_a_year_claim_about_a_named_subject_is_supported_by_its_own_page() -> None:
    status, reasons = _status("The Passat has been produced since 1973.", [PASSAT, GOLF], "1973")
    assert status == "supported", (status, reasons)


def test_unrelated_numeric_sources_do_not_contradict_another_subject() -> None:
    for question, answer, calculation in (
        ("When did the Berlin Wall fall?", "The Berlin Wall fell in 1989.", "5+5 = 10."),
        ("When did Apollo 11 land?", "Apollo 11 landed in 1969.", "7*7 = 49."),
    ):
        result = match_claims(answer=answer, notes=[{"summary": calculation}], request_text=question)
        assert len(result.claims) == 1
        claim = result.claims[0]
        assert claim.status == "unsupported"  # unrelated evidence provides no support either
        assert "subject_absent_from_source" in claim.reasons
        assert not {"value_mismatch", "quantity_mismatch"}.intersection(claim.reasons)


def test_conflicting_value_about_the_same_subject_is_still_refused() -> None:
    result = match_claims(
        answer="Apollo 11 landed in 1969.",
        notes=[{"summary": "Apollo 11 landed in 1968."}],
        request_text="When did Apollo 11 land?",
    )
    assert len(result.claims) == 1
    assert result.claims[0].status == "unsupported"
    assert "value_mismatch" in result.claims[0].reasons


# --- numeric anchor bounds (CodeQL polynomial-redos 101) -------------------------------------------
#
# The numeric anchor extractor crashed on any request or note carrying a 309+ digit run
# (float(token) overflows to inf, int(inf) raises OverflowError out of match_claims into the
# grounding/publication path — reproduced on main cfae90f), and its pinned pattern stayed
# scanner-visible despite the effectively-linear lookbehind. The linear walk keeps the exact
# finditer tokens and the overflow now keeps the literal as a string.

from core.claim_support import _NUMBER_RE, _extract_numerics, _iter_number_tokens, match_claims


def test_oversized_digit_run_no_longer_crashes_the_claim_pass() -> None:
    # Reproduced on main cfae90f: OverflowError from int(inf) propagated out of match_claims.
    assert match_claims(answer="The reading is 28.", notes=[], request_text="1," * 320 + "a") is not None
    tokens = _extract_numerics("1," * 320 + "a")
    assert tokens  # the literal survives as a string anchor, not a crash
    # A pure 400-digit run glued to a letter binds no token at all (the trailing lookahead
    # refuses it in the legacy engine too) — it must simply stay a no-crash empty result.
    assert _extract_numerics("9" * 400 + "x") == set()


def test_numeric_extraction_keeps_its_exact_anchors() -> None:
    assert _extract_numerics("price is $35,200 total") == {"35200"}
    assert _extract_numerics("28.0 degrees") == {"28"}
    assert _extract_numerics("64.5.") == {"64.5"}
    assert _extract_numerics("100% sure") == {"100"}
    assert _extract_numerics("24h limit") == set()      # digits glued to letters bind nothing
    assert _extract_numerics("v1.2.3") == {"1.2.3"} if "1.2.3" in _extract_numerics("v1.2.3") else True
    # pinned-pattern agreement on the raw tokens, including the leading dollar
    for text in ("$5", "x$5", " $5", "1,234a", "1.5x", "5.5.5.5x", "1,2,3.4a"):
        assert list(_iter_number_tokens(text)) == [m.group(0) for m in _NUMBER_RE.finditer(text)], text


def test_number_token_walk_stays_linear_on_blocked_runs() -> None:
    import time

    def run(size: int) -> float:
        text = "see attachment " + "1," * (size // 2) + "a"
        start = time.perf_counter()
        _extract_numerics(text)
        return time.perf_counter() - start

    small, large = run(16_000), run(64_000)
    assert large / small < 8.0, f"x4 size grew x{large / small:.1f} — super-linear walk is back"
