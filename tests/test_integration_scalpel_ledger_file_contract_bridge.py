"""Harbourmaster integration bridge: one file-suffix vocabulary, not two.

Scalpel (`fix/generic-investigation-routing-20260807`) extracted
`core.agent_runtime.file_target_contract.REAL_FILE_EXTENSIONS` precisely because the rule "a dotted
token is not a path" was fixed in one extractor on 2026-08-01 and recurred in another six days
later: an allowlist living inside one consumer is a fix that does not travel. Ledger
(`fix/evidence-answer-truth-binding-20260807`) arrived carrying its own identical 65-entry copy as
`core.runtime_evidence.FILE_SUFFIXES`.

Both copies were verified set-identical before they were joined, so retiring one is a
de-duplication, not a behaviour change. These tests pin that the ledger now *follows* the shared
contract rather than merely happening to agree with it today -- which is the whole difference
between a shared contract and a coincidence.

Why it matters concretely: a suffix in one set and missing from the other makes `host_of` read a
filename as a domain, which turns a local file read into a phantom remote call and rewrites a
truthful "I did not use the web" into a contradiction. That exact failure was measured on the
ledger lane's first run, with `README.md` parsed as the domain `readme.md`.
"""

from __future__ import annotations

import core.runtime_evidence as runtime_evidence
from core.agent_runtime.file_target_contract import REAL_FILE_EXTENSIONS
from core.runtime_evidence import FILE_SUFFIXES, host_of


def test_ledger_vocabulary_is_the_shared_contract_not_a_copy() -> None:
    # Identity, not equality: a duplicated list could still be `==` today and drift tomorrow.
    assert FILE_SUFFIXES is REAL_FILE_EXTENSIONS, (
        "runtime_evidence.FILE_SUFFIXES must BE the shared contract object, not an equal copy -- "
        "equality is what the two lanes already had before this bridge, and it is exactly what "
        "drifts"
    )


def test_the_public_name_is_preserved_for_existing_callers() -> None:
    """`core.agent_runtime.evidence_claim_binder` imports `FILE_SUFFIXES` by name; the bridge moved
    only the right-hand side."""
    assert "FILE_SUFFIXES" in runtime_evidence.__all__
    import core.agent_runtime.evidence_claim_binder as binder

    assert binder.FILE_SUFFIXES is REAL_FILE_EXTENSIONS


def test_the_joined_vocabulary_is_still_the_verified_65_entries() -> None:
    assert len(FILE_SUFFIXES) == 65
    assert len(REAL_FILE_EXTENSIONS) == 65


def test_a_readme_is_a_file_not_a_host() -> None:
    """The measured failure the shared vocabulary exists to prevent."""
    assert host_of("README.md") == ""
    assert host_of("notes.md") == ""
    # A real bare URL still resolves, so the guard did not simply refuse everything.
    assert host_of("github.com/VOOL-ai/openclaw-skills") == "github"


def test_ledger_consumer_follows_a_change_to_the_shared_contract(monkeypatch) -> None:
    """Mutation proof, in the required direction: change the SHARED contract and the ledger's
    consumer must demonstrably follow it.

    `zzz` is not a suffix in either list, so `host_of("archive.zzz")` resolves as a host today --
    returning the registrable LABEL `archive`, not the suffix. Add `zzz` to the shared contract and
    the ledger must start treating `archive.zzz` as a file instead. If the ledger still kept its own
    copy, this test would fail -- which is precisely what it is for.
    """
    assert host_of("archive.zzz") == "archive", "precondition: zzz is not a known file suffix"

    extended = frozenset(REAL_FILE_EXTENSIONS | {"zzz"})
    monkeypatch.setattr(runtime_evidence, "FILE_SUFFIXES", extended)

    assert host_of("archive.zzz") == "", (
        "the ledger's host detection must read the shared vocabulary at use time; if this still "
        "resolves as a host, the consumer is bound to a private copy and the two can drift again"
    )
    # The rest of the vocabulary is unaffected by the extension.
    assert host_of("README.md") == ""
    assert host_of("github.com/x") == "github"
