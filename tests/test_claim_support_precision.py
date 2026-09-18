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
