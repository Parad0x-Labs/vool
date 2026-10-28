"""R14 (round 2): the K-08 exemption for UNSEALED results, pinned at its one seam.

A deterministic lane that answers before any model round returns a result that was never
admitted (no ``vool_response_commit``). When a chat-boundary backstop (final-action honesty,
url-grounding) rewrites such a result's bytes, that application is the legal COMPLETION of
the sanitizer pipeline, not a post-admission mutation -- measured as K-page-reader HTTP 500
on the served rig before the fix. A SEALED result keeps the strict verify: divergence
raises. Red on 15c535fc: the exemption existed only as two inline `and not _arrived_unsealed`
conditions with no seam to pin.
"""
from __future__ import annotations

import pytest

from core.web.api.runtime import _k08_verify_immutable


def test_an_unsealed_result_completes_instead_of_raising():
    # The mutation happened (unchanged=False) and the result arrived unsealed: the
    # completion stands.
    _k08_verify_immutable(
        unchanged=False,
        message="K-08 violation: url-grounding would mutate post-admission bytes",
        arrived_unsealed=True,
    )


def test_a_sealed_result_with_divergent_bytes_still_raises():
    with pytest.raises(RuntimeError, match="K-08 violation: final-action-honesty"):
        _k08_verify_immutable(
            unchanged=False,
            message="K-08 violation: final-action-honesty would mutate post-admission bytes",
            arrived_unsealed=False,
        )


def test_byte_identical_reapplication_never_raises():
    for sealed in (True, False):
        _k08_verify_immutable(
            unchanged=True,
            message="K-08 violation: exact-response-control would mutate post-admission bytes",
            arrived_unsealed=sealed,
        )
