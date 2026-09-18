"""Failure classification: VOOL vs PROVIDER vs MODEL vs HARNESS.

The bench's whole point is truthful attribution. When a case fails, the
owner of the failure must be named, not blurred:

* ``HARNESS``   — the bench's own machinery failed (staging, stub server,
                  wire client, timeouts of the bench itself). A harness
                  failure INVALIDATES the case's evidence; it can never be
                  counted as a product failure, and it must never be silent.
* ``PROVIDER``  — the scripted provider stub delivered the failure the case
                  asked it to deliver (HTTP 5xx/401/429, malformed body,
                  stall) and the observed product behavior mismatch is
                  attributed to how the provider behaved, not to the
                  product's handling of it. Used when the case's expectation
                  was about the product SURVIVING a provider outage and the
                  outage itself is what broke the expectation path.
* ``MODEL``     — the model lane answered (the stub's scripted content was
                  served end-to-end) but the CONTENT failed the case's
                  semantic shape (e.g. the scripted answer was supposed to
                  satisfy a constraint and the product shipped it verbatim).
                  This is model-capability attribution: the pipeline worked,
                  the authored text did not meet the bar.
* ``VOOL``      — the assembled product violated a stated product truth:
                  dropped a requested unit, bypassed a permission gate,
                  fabricated a tool success, accepted corrupted CAS,
                  mis-bound evidence to the wrong turn, served a refusal
                  whose stated reason is not the real one, answered without
                  the receipt the wire claims exists, etc.

The classifier is deterministic and total: every failure carries exactly one
class, and an unattributable failure classifies as HARNESS (the bench failed
to attribute it — which is a bench defect and must be visible as one).
"""

from __future__ import annotations

from typing import Any

from ops.served_reality.schema import (
    FAILURE_CLASS_HARNESS,
    FAILURE_CLASS_MODEL,
    FAILURE_CLASS_PROVIDER,
    FAILURE_CLASS_VOOL,
)


class BenchError(Exception):
    """A harness-side failure. Raised by rig machinery; classified HARNESS."""

    def __init__(self, message: str, *, detail: str = "") -> None:
        super().__init__(message)
        self.detail = detail


class ProductAssertionError(AssertionError):
    """A stated product truth was violated. Classified VOOL by default."""

    def __init__(self, message: str, *, detail: str = "", failure_class: str = FAILURE_CLASS_VOOL) -> None:
        super().__init__(message)
        self.detail = detail
        self.failure_class = failure_class


class ProviderExpectationError(ProductAssertionError):
    """The provider stub behaved as scripted and the expectation under test
    was about the provider's behavior itself (classified PROVIDER)."""

    def __init__(self, message: str, *, detail: str = "") -> None:
        super().__init__(message, detail=detail, failure_class=FAILURE_CLASS_PROVIDER)


class ModelExpectationError(ProductAssertionError):
    """The model's authored content failed the semantic bar (classified MODEL)."""

    def __init__(self, message: str, *, detail: str = "") -> None:
        super().__init__(message, detail=detail, failure_class=FAILURE_CLASS_MODEL)


def classify_exception(exc: BaseException) -> str:
    if isinstance(exc, BenchError):
        return FAILURE_CLASS_HARNESS
    if isinstance(exc, ProviderExpectationError):
        return FAILURE_CLASS_PROVIDER
    if isinstance(exc, ModelExpectationError):
        return FAILURE_CLASS_MODEL
    if isinstance(exc, ProductAssertionError):
        return FAILURE_CLASS_VOOL
    # An unexpected exception inside a case is a bench defect until proven
    # otherwise: the bench must either turn product misbehavior into a typed
    # assertion or admit it could not attribute the failure.
    return FAILURE_CLASS_HARNESS


def classify_provider_exchange(exchange: dict[str, Any] | None) -> str | None:
    """Classify one stub wire exchange's provider-side outcome, if any."""
    if exchange is None:
        return None
    status = exchange.get("status")
    if isinstance(status, int) and status >= 400:
        return f"provider_http_{status}"
    if exchange.get("error"):
        return f"provider_error:{exchange['error']}"
    return None
