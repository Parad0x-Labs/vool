"""KAS — the external adapter/channel boundary.

The architecture law this package exists to make MECHANICAL:

    KAS owns external adapters and channels. VOOL owns permissions, semantic truth,
    effects, privacy and finality.

An adapter under :mod:`core.kas.adapters` is a TRANSLATOR and nothing else. It turns a typed
VOOL request into a provider's wire shape and a provider's wire response back into typed VOOL
data. It holds no authority: it cannot decide whether an effect may happen, cannot open a
socket, cannot read a secret, cannot redact, and cannot declare anything final.

Everything an adapter is allowed to do reaches it through :class:`KasTransport`, which is
constructed by VOOL (:mod:`core.kas.transport`) and injected. That is the whole outward
surface, so "did this adapter bypass an authority?" is answerable by reading the adapter's
imports rather than by trusting its author — and :mod:`core.kas.conformance` reads them.
"""

from core.kas.contract import (
    AdapterConfig,
    ExternalAdapter,
    ForgeAdapter,
    ForgeArtifact,
    ForgeCiJob,
    ForgePullRequest,
    ForgeRepository,
    KasRequest,
    KasResponse,
    KasTransport,
    TransportDeniedError,
    TransportUnknownError,
)
from core.kas.registry import forge_adapter, register_adapter, registered_adapters

__all__ = [
    "AdapterConfig",
    "ExternalAdapter",
    "ForgeAdapter",
    "ForgeArtifact",
    "ForgeCiJob",
    "ForgePullRequest",
    "ForgeRepository",
    "KasRequest",
    "KasResponse",
    "KasTransport",
    "TransportDeniedError",
    "TransportUnknownError",
    "forge_adapter",
    "register_adapter",
    "registered_adapters",
]
