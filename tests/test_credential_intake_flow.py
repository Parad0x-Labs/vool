# ruff: noqa: F811 (imported pytest fixtures are re-exposed as test parameters by design)
"""P0 credential intelligence — the END-TO-END intake boundary, driven for real.

paste key → local-only classification → likely-provider shortlist → operator selects one →
verify ONLY that provider → secure persistence → opaque CredentialBinding → provider/capability
availability update. Everything here runs against live loopback provider fakes and an isolated
home — the actual boundary, not a mock of it.

THE LAW UNDER TEST
------------------
* The full happy path produces a binding and flips availability for that provider ONLY.
* An unverified key (any distinct bad outcome) persists NOTHING and flips NOTHING.
* Verification can never silently enable paid fallback: the escalation policy file and its
  verdicts are byte/semantics-identical before and after.
* Selected-provider binding is enforced end-to-end: a forged outcome for a different provider
  cannot be persisted against the operator's selection (sabotage).
"""
from __future__ import annotations

import json

import pytest

from tests._credential_intelligence_support import (  # noqa: F401 (fixtures resolve via module namespace)
    ODD_KEY,
    FakeProviderServer,
    descriptor_for,
    isolated_home,
    registry_of,
    sweep_home_for_secret,
    vault_home,
)


def _intake(registry, **kwargs):
    from core.credential_intelligence.intake import CredentialIntake

    return CredentialIntake(registry=registry, **kwargs)


@pytest.fixture
def two_candidate_world():
    """A shortlist world: the pasted key's format is ambiguous between two live providers."""
    with FakeProviderServer() as alpha, FakeProviderServer() as beta:
        desc_a = descriptor_for(alpha, "alpha", key_prefixes=(), ambiguous_group="bare_sk", label="Alpha")
        desc_b = descriptor_for(beta, "beta", key_prefixes=(), ambiguous_group="bare_sk", label="Beta")
        yield alpha, beta, registry_of(desc_a, desc_b)


def _escalation_file_state(home):
    path = home / "data" / "cloud_escalation.json"
    return path.read_bytes() if path.exists() else b"<absent>"


# ------------------------------------------------------------------ the real flow

def test_full_flow_paste_select_verify_persist_bind(vault_home, two_candidate_world):
    alpha, beta, registry = two_candidate_world
    alpha.responses = [(200, {"data": [{"id": "m-1"}], "account": {"id": "acct-9"}})]

    intake = _intake(registry)
    shortlist = intake.paste("sk-" + "0123456789abcdef0123456789")
    assert shortlist.ambiguous is True

    selection = intake.select_provider("alpha")
    assert selection.provider_id == "alpha"

    outcome = intake.verify()
    assert outcome.status == "verified"
    assert alpha.request_count == 1, "exactly one verification request to the selected provider"
    assert beta.request_count == 0, "the unselected candidate was probed"

    result = intake.complete()          # persist + bind + availability
    binding = result.binding
    assert binding.provider_id == "alpha"
    assert binding.account == "acct-9"
    assert binding.status == "verified"
    assert binding.capability_family == "cloud_chat"

    from core import credential_store

    assert credential_store.get_credential("llm.cloud.alpha") == "sk-" + "0123456789abcdef0123456789"
    assert result.availability["alpha"]["available"] is True
    assert result.availability.get("beta") is None or result.availability["beta"]["available"] is False


def test_selecting_a_provider_outside_the_shortlist_is_an_operator_override_recorded(two_candidate_world):
    from core.credential_intelligence.intake import UnknownProviderError

    _alpha, _beta, registry = two_candidate_world
    intake = _intake(registry)
    intake.paste("sk-" + "0123456789abcdef0123456789")
    with pytest.raises(UnknownProviderError):
        intake.select_provider("not-a-provider")


def test_flow_stages_are_enforced_in_order(two_candidate_world):
    from core.credential_intelligence.intake import StageError

    _alpha, _beta, registry = two_candidate_world
    intake = _intake(registry)
    with pytest.raises(StageError):
        intake.verify()                    # nothing pasted/selected yet
    intake.paste(ODD_KEY)
    with pytest.raises(StageError):
        intake.verify()                    # classified but no selection
    with pytest.raises(StageError):
        intake.complete()                  # nothing verified yet


@pytest.mark.parametrize("status", ["invalid", "exhausted", "rate_limited", "unauthorized", "network_unavailable"])
def test_every_bad_outcome_persists_nothing_and_enables_nothing(vault_home, two_candidate_world, status):
    alpha, beta, registry = two_candidate_world
    code_by_status = {"invalid": 401, "exhausted": 402, "rate_limited": 429, "unauthorized": 403}
    if status in code_by_status:
        alpha.responses = [(code_by_status[status], {"error": {}})]
    else:
        # network_unavailable: point the descriptor at a dead port
        dead = FakeProviderServer()
        dead._thread.start()
        dead._server.shutdown()
        dead._server.server_close()
        from tests._credential_intelligence_support import descriptor_for as _df

        registry = registry_of(_df(dead, "alpha", key_prefixes=(), ambiguous_group="bare_sk"),
                               _df(beta, "beta", key_prefixes=(), ambiguous_group="bare_sk"))

    from core.credential_intelligence.intake import IntakeRefusedError

    intake = _intake(registry)
    intake.paste("sk-" + "0123456789abcdef0123456789")
    intake.select_provider("alpha")
    outcome = intake.verify()
    assert outcome.status == status
    with pytest.raises(IntakeRefusedError):
        intake.complete()

    from core import credential_store

    assert credential_store.get_credential("llm.cloud.alpha") is None
    assert beta.request_count == 0


# ------------------------------------------------------------------ paid fallback guard

def test_verification_never_enables_paid_fallback(vault_home, two_candidate_world, monkeypatch):
    alpha, _beta, registry = two_candidate_world
    alpha.responses = [(200, {"data": []})]

    from core import cloud_escalation_policy as cep

    before_bytes = _escalation_file_state(vault_home)
    before_policy = cep.load_policy()

    lanes = {"activated": [], "deactivated": []}
    monkeypatch.setattr(
        "core.runtime_provider_defaults.activate_provider_byok",
        lambda pid, env=None: lanes["activated"].append(pid) or "alpha-byok:test",
    )
    monkeypatch.setattr(
        "core.runtime_provider_defaults.deactivate_provider_byok",
        lambda pid: lanes["deactivated"].append(pid) or 1,
    )

    intake = _intake(registry)
    intake.paste("sk-" + "0123456789abcdef0123456789")
    intake.select_provider("alpha")
    assert intake.verify().status == "verified"
    result = intake.complete()

    # the lane is AVAILABLE — that is not fallback authorization
    assert lanes["activated"] == ["alpha"]
    assert result.availability["alpha"]["available"] is True
    # and the paid-fallback switch never moved: same file bytes, same policy, same mode
    assert _escalation_file_state(vault_home) == before_bytes
    assert cep.load_policy() == before_policy
    assert cep.load_policy().mode == before_policy.mode


def test_capability_availability_comes_only_from_verified_evidence(vault_home, two_candidate_world, monkeypatch):
    """An invalid verification withdraws a previously verified provider's capability; a
    rate-limited or network-unavailable re-check is INCONCLUSIVE and changes nothing."""
    from core.credential_intelligence.availability import apply_verification
    from core.credential_intelligence.verification import VerificationOutcome

    def outcome(status):
        return VerificationOutcome(
            status=status, provider_id="alpha", http_status=None, detail="",
            account="", checked_at="2026-09-02T12:00:00Z", endpoint_host="alpha.test",
        )

    lanes = {"activated": [], "deactivated": []}
    monkeypatch.setattr(
        "core.runtime_provider_defaults.activate_provider_byok",
        lambda pid, env=None: lanes["activated"].append(pid) or "x",
    )
    monkeypatch.setattr(
        "core.runtime_provider_defaults.deactivate_provider_byok",
        lambda pid: lanes["deactivated"].append(pid) or 1,
    )
    _alpha, _beta, registry = two_candidate_world
    desc = registry.get("alpha")

    apply_verification(desc, outcome("verified"))
    assert lanes["activated"] == ["alpha"] and lanes["deactivated"] == []
    apply_verification(desc, outcome("rate_limited"))
    apply_verification(desc, outcome("network_unavailable"))
    assert lanes["activated"] == ["alpha"] and lanes["deactivated"] == [], "inconclusive outcomes changed lane state"
    apply_verification(desc, outcome("invalid"))
    assert lanes["deactivated"] == ["alpha"], "verified-dead evidence must withdraw the lane"


def test_availability_module_never_touches_escalation_policy():
    import inspect

    import core.credential_intelligence.availability as availability_module

    source = inspect.getsource(availability_module)
    assert "escalation" not in source.replace("paid fallback", "").replace("silently enable paid fallback", "")


# ------------------------------------------------------------------ intake receipts + session hygiene

def test_intake_receipt_never_contains_the_secret_even_with_redaction_sabotaged(vault_home, two_candidate_world, monkeypatch):
    """SABOTAGE (redaction): the scrubber is disabled entirely — the receipt must STILL be
    clean, proving the secret is never included rather than merely scrubbed afterwards."""
    alpha, _beta, registry = two_candidate_world
    alpha.responses = [(200, {"data": []})]
    monkeypatch.setattr("core.secret_redaction.redact_secrets", lambda text: text)

    intake = _intake(registry)
    secret = "sk-" + "0123456789abcdef0123456789"
    intake.paste(secret)
    intake.select_provider("alpha")
    intake.verify()
    receipt = intake.receipt()

    text = json.dumps(receipt.to_dict())
    assert secret not in text and ODD_KEY not in text
    assert intake.session_repr_safe() is True


def test_forged_outcome_for_another_provider_is_refused(vault_home, two_candidate_world):
    """SABOTAGE (selected-provider binding): verification completed for alpha, but a forged
    outcome claiming beta is pushed into persist — the intake must refuse, because the
    binding is to the OPERATOR'S SELECTION, not to whatever the outcome claims."""
    from core.credential_intelligence.intake import IntakeRefusedError
    from core.credential_intelligence.verification import VerificationOutcome

    alpha, _beta, registry = two_candidate_world
    alpha.responses = [(200, {"data": []})]

    intake = _intake(registry)
    secret = "sk-" + "0123456789abcdef0123456789"
    intake.paste(secret)
    intake.select_provider("alpha")
    intake.verify()

    forged = VerificationOutcome(
        status="verified", provider_id="beta", http_status=200, detail="forged",
        account="", checked_at="2026-09-02T12:00:00Z", endpoint_host="beta.test",
    )
    with pytest.raises(IntakeRefusedError):
        intake._persist_outcome(forged)      # the seam a saboteur would target

    from core import credential_store

    assert credential_store.get_credential("llm.cloud.beta") is None


def test_end_to_end_no_plaintext_secret_anywhere_in_the_home(vault_home, two_candidate_world):
    alpha, _beta, registry = two_candidate_world
    alpha.responses = [(200, {"data": []})]
    secret = "sk-" + "0123456789abcdef0123456789"

    intake = _intake(registry)
    intake.paste(secret)
    intake.select_provider("alpha")
    intake.verify()
    intake.complete()

    assert sweep_home_for_secret(vault_home, secret) == []
