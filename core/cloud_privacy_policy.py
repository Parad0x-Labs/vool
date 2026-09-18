from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from core.cloud_provider_contract import PrivacyClass
from core.secret_redaction import contains_secret

_NEVER_REMOTE = {PrivacyClass.SECRETS, PrivacyClass.WALLET, PrivacyClass.UNKNOWN}
_SCOPED_APPROVAL = {
    PrivacyClass.PRIVATE_FILES,
    PrivacyClass.SOURCE_CODE,
    PrivacyClass.PERSISTENT_MEMORY,
    PrivacyClass.PERSONAL,
}
_WALLET_PATH_PARTS = {"wallet", "seed", "mnemonic", "phantom", "keystore", "private-key", "private_key"}


@dataclass(frozen=True)
class CloudPrivacyGrant:
    privacy_classes: tuple[PrivacyClass, ...] = ()
    approved_paths: tuple[str, ...] = ()
    approved_data_categories: tuple[str, ...] = ()
    allow_provider_fallback: bool = False


@dataclass(frozen=True)
class CloudPrivacyDecision:
    allowed: bool
    reason: str
    normalized_paths: tuple[str, ...] = ()


def _normalized_path(value: str) -> str:
    resolved = Path(str(value or "")).expanduser().resolve(strict=False)
    return os.path.normcase(str(resolved))


def _looks_like_wallet_path(path: str) -> bool:
    lowered_parts = {part.lower() for part in Path(path).parts}
    name = Path(path).name.lower()
    return bool(lowered_parts & _WALLET_PATH_PARTS) or any(part in name for part in _WALLET_PATH_PARTS)


def evaluate_cloud_privacy(
    *,
    privacy_class: PrivacyClass | str,
    payload_texts: tuple[str, ...] = (),
    paths: tuple[str, ...] = (),
    data_categories: tuple[str, ...] = (),
    grant: CloudPrivacyGrant | None = None,
) -> CloudPrivacyDecision:
    try:
        resolved_class = PrivacyClass(str(privacy_class))
    except ValueError:
        resolved_class = PrivacyClass.UNKNOWN
    if resolved_class in _NEVER_REMOTE:
        return CloudPrivacyDecision(False, f"privacy_class_blocked:{resolved_class.value}")
    if any(contains_secret(text) for text in payload_texts):
        return CloudPrivacyDecision(False, "secret_detected")

    normalized_paths = tuple(_normalized_path(path) for path in paths if str(path or "").strip())
    if any(_looks_like_wallet_path(path) for path in normalized_paths):
        return CloudPrivacyDecision(False, "wallet_or_key_path_blocked", normalized_paths)

    active_grant = grant or CloudPrivacyGrant()
    if resolved_class in _SCOPED_APPROVAL and resolved_class not in set(active_grant.privacy_classes):
        return CloudPrivacyDecision(False, f"scoped_approval_required:{resolved_class.value}", normalized_paths)

    approved_paths = {_normalized_path(path) for path in active_grant.approved_paths}
    if normalized_paths and any(path not in approved_paths for path in normalized_paths):
        return CloudPrivacyDecision(False, "path_not_explicitly_approved", normalized_paths)

    approved_categories = set(active_grant.approved_data_categories)
    if any(str(category) not in approved_categories for category in data_categories):
        return CloudPrivacyDecision(False, "data_category_not_approved", normalized_paths)

    return CloudPrivacyDecision(True, "privacy_policy_passed", normalized_paths)


__all__ = [
    "CloudPrivacyDecision",
    "CloudPrivacyGrant",
    "evaluate_cloud_privacy",
]
