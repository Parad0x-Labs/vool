"""ARGUS repair D2/D6, 2026-08-06: a permission the operator denied must never come back as a grant.

D2's exact incident: "Audit the codec. Do not create a new test file. Prove it." — the old
`repository_test_creation` subject regex allowed at most ONE modifier word between the verb and
"test(s)" (`(?:a\\s+|new\\s+)?`), so "create A NEW test FILE" (two stacked modifiers plus a trailing
"file") matched nothing. The clause's explicit denial became invisible to `mentioned`, and the bare
"Prove it." sentence right after it silently granted the very axis just denied.

D6's exact incident: "Temporary files outside the repository are allowed. Safe read-only commands
are allowed. Prove it." was refused an isolated proof unless the operator ALSO separately restated
"repository writes forbidden" — a permission that is False by default and, after the D2 repair, is
never silently inferred to True, so requiring it restated was a redundant, needless refusal.

Every test below drives the real `derive_execution_policy` parser — the production function ARGUS
reviewed — not a hand-built policy object.
"""
from __future__ import annotations

import pytest

from core.agent_runtime import audit_policy
from core.agent_runtime.audit_policy import READ_ONLY_AUDIT, derive_execution_policy


def test_the_exact_argus_incident_denies_test_creation_and_repository_writes():
    policy = derive_execution_policy("Audit the codec. Do not create a new test file. Prove it.")

    assert policy.repository_test_creation is False
    assert policy.repository_write is False
    assert policy.proof_write_scope != "repository_generated"


@pytest.mark.parametrize(
    "denial_clause",
    [
        "Do not create a test.",
        "Do not create a new test.",
        "Do not create a new test file.",
        "Do not add a test file.",
        "Do not write any tests.",
        "No repository tests.",
    ],
)
def test_ordinary_denial_variants_stay_denied(denial_clause):
    policy = derive_execution_policy(f"Audit the codec. {denial_clause} Prove it.")

    assert policy.repository_test_creation is False, denial_clause
    assert policy.repository_write is False, denial_clause
    assert policy.proof_write_scope != "repository_generated", denial_clause


@pytest.mark.parametrize(
    "isolated_alternative_clause",
    [
        "Temporary files outside the repository are allowed.",
        "Use an isolated external reproduction.",
    ],
)
def test_an_explicit_isolated_alternative_does_not_also_grant_in_repo_test_creation(
    isolated_alternative_clause,
):
    """The operator asked for external/isolated proof specifically — granting the in-repo axis TOO
    would be an unrequested widening, not a narrowing, of what was actually asked for."""
    policy = derive_execution_policy(f"Audit the codec. {isolated_alternative_clause} Prove it.")

    assert policy.repository_test_creation is False, isolated_alternative_clause
    assert policy.repository_write is False, isolated_alternative_clause


def test_prove_it_without_touching_the_repository_denies_test_creation_too():
    policy = derive_execution_policy("Audit the codec. Prove it without touching the repository.")

    assert policy.repository_write is False
    assert policy.repository_test_creation is False
    assert policy.proof_write_scope != "repository_generated"


def test_d6_exact_case_selects_the_isolated_path_without_a_redundant_write_denial():
    policy = derive_execution_policy(
        "Temporary files outside the repository are allowed. "
        "Safe read-only commands are allowed. "
        "Prove it."
    )

    assert policy.repository_write is False
    assert policy.repository_test_creation is False
    assert policy.temporary_external_files is True
    assert policy.read_only_commands is True
    assert policy.isolated_reproduction is True
    assert policy.isolated_proof_authorized is True
    assert policy.proof_write_scope == "external_temp_root"


def test_a_bare_follow_up_proof_request_still_authorizes_the_conventional_in_repo_path():
    """Preserved, not broken: the ORIGINAL incident's own contract — a bare "prove it" follow-up
    after a read-only audit turn, with no alternative proof mechanism on offer and no explicit
    denial of the repository itself, authorizes the conventional in-repo test. Removing this
    entirely (rather than narrowing it, as this repair does) would have been an over-correction that
    broke the audit lane's most common real usage pattern."""
    initial = derive_execution_policy(
        "Inspect this workspace and audit x.py — find the single highest-risk real bug. "
        "Do not modify anything, and use only what is on this machine."
    )
    follow_up = derive_execution_policy(
        "Prove the bug you identified before fixing it.", prior=initial
    )

    assert follow_up.repository_test_creation is True
    assert follow_up.in_repo_proof_authorized is True
    assert follow_up.proof_write_scope == "repository_generated"


def test_a_described_regression_test_still_grants_test_creation_on_its_own_mention():
    """A message that explicitly DESCRIBES creating a test ("create the smallest deterministic
    regression test") is not a bare, undecorated "prove it" — the axis is granted by its own
    explicit mention, independent of any fallback."""
    policy = derive_execution_policy(
        "Prove the bug you identified before fixing it. Create the smallest deterministic "
        "regression test that reproduces the failure. Rules: Do not modify production code yet. "
        "Use a temporary test file or the project's existing test structure. Run the test locally.",
        prior=READ_ONLY_AUDIT,
    )

    assert policy.repository_write is False
    assert policy.repository_test_creation is True
    assert policy.proof_write_scope == "repository_generated"


def test_sabotage_reintroducing_the_old_single_modifier_regex_breaks_the_denial(monkeypatch):
    """Mutates the REAL production regex back to the pre-repair shape (at most one modifier word
    between the verb and "test(s)", no trailing "file(s)", no bare "repository tests" noun phrase)
    and proves the exact ARGUS incident text is denied its repair — the axis is granted again even
    though the operator explicitly said not to."""
    import re

    old_pattern = re.compile(
        r"\brepository\s+test\s+creation\b|"
        r"\b(?:creat\w*|writ\w*|add\w*|generat\w*)\s+(?:a\s+|new\s+)?tests?\b(?!\s+(?:outside|external))|"
        r"\btest\s+creation\b",
        re.IGNORECASE,
    )
    sabotaged_subjects = dict(audit_policy._AXIS_SUBJECTS)
    sabotaged_subjects["repository_test_creation"] = old_pattern
    monkeypatch.setattr(audit_policy, "_AXIS_SUBJECTS", sabotaged_subjects)

    policy = derive_execution_policy("Audit the codec. Do not create a new test file. Prove it.")

    assert policy.repository_test_creation is True, (
        "sabotage setup failed to reproduce the pre-repair regex gap"
    )
    assert policy.proof_write_scope == "repository_generated", (
        "the real regression must fail under the sabotaged (pre-D2) regex — the denial was silently "
        "overridden by the bare proof-request fallback, exactly the incident this repair fixes"
    )


def test_sabotage_removing_the_isolated_alternative_condition_over_grants(monkeypatch):
    """Reverts the REAL production gate `repository_test_creation_fallback_blocked` back to the
    pre-D2 shape (never blocks the fallback) and proves the D6 exact case now over-grants the
    in-repo axis on top of the isolated alternative it already has — exactly the unwarranted
    widening this repair exists to prevent."""
    monkeypatch.setattr(
        audit_policy, "repository_test_creation_fallback_blocked", lambda **_: False
    )

    policy = derive_execution_policy(
        "Audit the codec. Temporary files outside the repository are allowed. Prove it."
    )

    assert policy.repository_test_creation is True, (
        "the real regression must fail once the fallback is no longer gated on the isolated "
        "alternative already on offer — an extra, unrequested `repository_test_creation` grant "
        "now sits on the policy even though the isolated path already satisfies the proof request; "
        "this is the D6/over-grant shape the repair prevents"
    )
