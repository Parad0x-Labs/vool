"""A claim of having inspected something must be backed by a record of inspecting THAT thing.

Built from a live failure on 2026-07-28. The owner asked the app to audit
`app-landing/index.html`. The workspace audit ran its fixed sweep — it read `README.md` and
`site/assets/site.js`, never opened the named file — and the app then said:

    "Yes, the audit ran — it inspected the whole workspace (14 JS source files, 2017 lines).
     Its only finding was P2: no automated tests discovered."

Every existing guard passed it, for two separate reasons worth keeping in mind:

* `_FALSE_ACTION_CLAIM_RE` has only mutation vocabulary — `deleted|created|edited|saved`. Reading
  is the most common thing this product claims to have done and it had no guard at all.
* `_current_session_has_executed_receipt` is **existential**: it asks whether *anything* ran. The
  audit really did read two files, so the claim was waved through. The question has to be
  referential — did the thing this sentence names actually get opened.

The tense distinction below is load-bearing in the other direction. "You should audit index.html —
want me to?" is an OFFER; blocking it would stop the assistant proposing work at all. Only a claim
that the work is already done can be false about what ran.
"""
from __future__ import annotations

import pytest

from core import execution_records
from core.agent_runtime.action_honesty_validator import enforce_final_action_honesty
from core.runtime_tool_contracts import ToolClaim

READ_CLAIM = ToolClaim(target_argument="path", resolved_target_key="path")

# Verbatim from the failing session.
FABRICATION = (
    "Yes, the audit ran — it inspected the whole workspace (14 JS source files, 2017 lines). "
    "Its only finding was P2: no automated tests discovered."
)
REQUEST = (
    "great, i need you to run audit for this - app-landing/index.html tell me if u can spot any "
    "security issues and such?"
)
# What the audit actually read.
AUDIT_ACTUALLY_READ = ("README.md", "site/assets/site.js")


@pytest.fixture(autouse=True)
def _clean():
    execution_records.clear()
    yield
    execution_records.clear()


def _read(*paths: str, session: str = "s") -> None:
    for path in paths:
        execution_records.record(
            session_id=session,
            intent="workspace.read_file",
            arguments={"path": path},
            observation={"ok": True, "path": f"/Users/x/VOOL WEBSITE/{path}"},
            claim=READ_CLAIM,
        )


def _run(response: str, *, request: str = REQUEST, session: str = "s") -> dict:
    return enforce_final_action_honesty(
        {"response": response, "confidence": 0.9}, user_input=request, session_id=session
    )


def _blocked(result: dict) -> bool:
    return result.get("route_reason") == "unsupported_inspection_claim"


# --------------------------------------------------------------------------------------
# The failure this exists for
# --------------------------------------------------------------------------------------


def test_the_live_fabrication_is_refused() -> None:
    _read(*AUDIT_ACTUALLY_READ)
    result = _run(FABRICATION)
    assert _blocked(result)
    assert "app-landing/index.html" in result["response"]
    assert "did not actually open" in result["response"]


def test_the_replacement_names_what_did_run() -> None:
    """An honest failure has to say what the turn actually did, or it is just a different refusal."""

    _read(*AUDIT_ACTUALLY_READ)
    result = _run(FABRICATION)
    assert "workspace.read_file" in result["response"]
    assert result["confidence"] <= 0.3
    assert result["inspection_claim_validator"]["unsupported_targets"] == ["app-landing/index.html"]


def test_the_same_claim_passes_once_the_file_was_read() -> None:
    """The guard must not simply block audits — it blocks UNBACKED ones."""

    _read("README.md", "app-landing/index.html")
    assert not _blocked(_run(FABRICATION))


def test_a_bare_filename_matches_the_full_path_that_was_read() -> None:
    _read("app-landing/index.html")
    assert not _blocked(_run("I inspected index.html and it looks fine."))


def test_a_different_file_with_the_same_name_is_not_a_match() -> None:
    _read("site/index.html")
    assert _blocked(_run("I inspected app-landing/index.html and it looks fine."))


# --------------------------------------------------------------------------------------
# False-positive guards. Each is a legitimate reply that must not be touched.
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reply",
    [
        "You should audit index.html for XSS — want me to?",
        "I can review index.html if you want.",
        "Did you check index.html yet?",
        "If you audited index.html you would find inline handlers.",
        "I could have inspected config.py, but you asked about something else.",
    ],
)
def test_an_offer_or_hypothetical_is_not_a_claim(reply: str) -> None:
    _read("README.md")
    assert not _blocked(_run(reply, request="any advice"))


def test_a_truthful_read_report_passes() -> None:
    _read("README.md")
    assert not _blocked(_run("I read README.md and it explains the build.", request="what does README.md say"))


def test_a_claim_with_no_named_target_is_not_checkable() -> None:
    """"I had a look" names nothing, so there is nothing to verify it against."""

    _read("README.md")
    assert not _blocked(_run("I had a look and nothing stood out.", request="check things"))


def test_prose_abbreviations_are_not_filenames() -> None:
    _read("README.md")
    assert not _blocked(_run("To audit a file, use e.g. eslint or similar.", request="how do i audit"))


def test_a_session_with_no_execution_at_all_is_left_to_the_mutation_guard() -> None:
    """Nothing ran, so this is 'claimed work with no tools' — a different guard's job.

    Firing here would flag ordinary conversation that happens to mention a filename.
    """

    assert not _blocked(_run("I inspected index.html.", request=""))


def test_an_empty_response_is_untouched() -> None:
    _read("README.md")
    assert not _blocked(_run("   "))


# --------------------------------------------------------------------------------------
# The referential primitive
# --------------------------------------------------------------------------------------


def test_verify_claim_is_referential_not_existential() -> None:
    """The distinction that let the fabrication through: something ran, but not THIS."""

    _read(*AUDIT_ACTUALLY_READ)
    verdict = execution_records.verify_claim("s", target="app-landing/index.html")
    assert verdict["any_execution"] is True
    assert verdict["supported"] is False
    assert "workspace.read_file" in verdict["executed"]


def test_verify_claim_matches_on_trailing_path_segments() -> None:
    _read("app-landing/index.html")
    assert execution_records.verify_claim("s", target="app-landing/index.html")["supported"]
    assert execution_records.verify_claim("s", target="index.html")["supported"]


def test_verify_claim_without_a_target_degrades_to_existential() -> None:
    _read("README.md")
    assert execution_records.verify_claim("s")["supported"] is True
    execution_records.clear()
    assert execution_records.verify_claim("s")["supported"] is False


def test_a_file_merely_listed_is_not_a_file_inspected() -> None:
    """Seeing a name in a directory listing is not the same as having read it."""

    execution_records.record(
        session_id="s",
        intent="machine.list_directory",
        arguments={"path": "app-landing"},
        observation={"ok": True, "path": "app-landing", "entries": [{"name": "index.html"}]},
        claim=ToolClaim(target_argument="path", resolved_target_key="path", result_items_key="entries"),
    )
    verdict = execution_records.verify_claim("s", target="index.html")
    assert verdict["supported"] is False
    assert verdict.get("listed_only") == "machine.list_directory"
