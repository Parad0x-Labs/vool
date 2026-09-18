"""VOOL routing authority V2 Phase-0 contracts.

This package is deliberately disconnected from production routing.  Its records are
canonical, immutable shadow artifacts; they cannot authorize a provider invocation.
"""

from core.routing_authority_v2.canonical import (
    CanonicalizationError,
    canonical_bytes,
    canonical_set,
    canonical_text,
    parse_strict_json,
    typed_sha256,
)
from core.routing_authority_v2.contracts import *  # noqa: F403
from core.routing_authority_v2.contracts import __all__ as _contract_exports
from core.routing_authority_v2.money import PicoUSD, PicoUSDValidationError
from core.routing_authority_v2.shadow_store import RoutingAuthorityV2ShadowStore, ShadowStoreIntegrityError

__all__ = [
    "CanonicalizationError",
    "PicoUSD",
    "PicoUSDValidationError",
    "RoutingAuthorityV2ShadowStore",
    "ShadowStoreIntegrityError",
    "canonical_bytes",
    "canonical_set",
    "canonical_text",
    "parse_strict_json",
    "typed_sha256",
    *_contract_exports,
]
