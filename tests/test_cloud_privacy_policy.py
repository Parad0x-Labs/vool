from __future__ import annotations

from core.cloud_privacy_policy import CloudPrivacyGrant, evaluate_cloud_privacy
from core.cloud_provider_contract import PrivacyClass


def test_secret_and_wallet_classes_never_leave() -> None:
    for privacy_class in (PrivacyClass.SECRETS, PrivacyClass.WALLET, PrivacyClass.UNKNOWN):
        assert evaluate_cloud_privacy(privacy_class=privacy_class).allowed is False


def test_redaction_is_not_consent_for_private_source(tmp_path) -> None:
    source = tmp_path / "app.py"
    source.write_text("print('safe')", encoding="utf-8")
    decision = evaluate_cloud_privacy(privacy_class=PrivacyClass.SOURCE_CODE, paths=(str(source),))
    assert decision.allowed is False
    assert decision.reason.startswith("scoped_approval_required")


def test_persistent_memory_requires_explicit_scoped_approval(tmp_path) -> None:
    memory = tmp_path / "memory.jsonl"
    assert not evaluate_cloud_privacy(
        privacy_class=PrivacyClass.PERSISTENT_MEMORY,
        paths=(str(memory),),
    ).allowed
    grant = CloudPrivacyGrant(
        privacy_classes=(PrivacyClass.PERSISTENT_MEMORY,),
        approved_paths=(str(memory),),
        approved_data_categories=("persistent_memory",),
    )
    assert evaluate_cloud_privacy(
        privacy_class=PrivacyClass.PERSISTENT_MEMORY,
        paths=(str(tmp_path / "nested" / ".." / "memory.jsonl"),),
        data_categories=("persistent_memory",),
        grant=grant,
    ).allowed


def test_approving_one_file_does_not_approve_sibling_or_directory(tmp_path) -> None:
    approved = tmp_path / "approved.py"
    sibling = tmp_path / "sibling.py"
    grant = CloudPrivacyGrant(
        privacy_classes=(PrivacyClass.SOURCE_CODE,),
        approved_paths=(str(approved),),
        approved_data_categories=("source",),
    )
    assert evaluate_cloud_privacy(
        privacy_class=PrivacyClass.SOURCE_CODE,
        paths=(str(approved),),
        data_categories=("source",),
        grant=grant,
    ).allowed
    assert not evaluate_cloud_privacy(
        privacy_class=PrivacyClass.SOURCE_CODE,
        paths=(str(sibling),),
        data_categories=("source",),
        grant=grant,
    ).allowed
    assert not evaluate_cloud_privacy(
        privacy_class=PrivacyClass.SOURCE_CODE,
        paths=(str(tmp_path),),
        data_categories=("source",),
        grant=grant,
    ).allowed


def test_secret_content_is_blocked_even_with_approval() -> None:
    grant = CloudPrivacyGrant(privacy_classes=(PrivacyClass.PERSONAL,))
    decision = evaluate_cloud_privacy(
        privacy_class=PrivacyClass.PERSONAL,
        payload_texts=("api_key=test-only-redaction-value",),
        grant=grant,
    )
    assert decision.allowed is False
    assert decision.reason == "secret_detected"


def test_wallet_named_path_is_blocked_even_when_approved(tmp_path) -> None:
    wallet_path = tmp_path / "phantom-wallet.json"
    grant = CloudPrivacyGrant(
        privacy_classes=(PrivacyClass.PRIVATE_FILES,),
        approved_paths=(str(wallet_path),),
    )
    decision = evaluate_cloud_privacy(
        privacy_class=PrivacyClass.PRIVATE_FILES,
        paths=(str(wallet_path),),
        grant=grant,
    )
    assert decision.allowed is False
    assert decision.reason == "wallet_or_key_path_blocked"
