"""A provider's 4xx/5xx reaches the operator as the provider's own reason, not a bare status line.

Measured on the served composer, 2026-09-02: three cloud attempts for one attachment turn each
surfaced as "400 Client Error: Bad Request for url: .../chat/completions" -- the status line,
and nothing of the JSON body in which the provider had said what was wrong. The adapter now
carries a bounded, secret-redacted excerpt of that body on the same exception class.
"""

from __future__ import annotations

from typing import Any

import pytest
import requests

from adapters import openai_compatible_adapter as adapter


class _Response:
    def __init__(self, status: int, text: str) -> None:
        self.status_code = status
        self.text = text
        self.reason = "Bad Request"
        self.url = "https://openrouter.ai/api/v1/chat/completions"

    def raise_for_status(self) -> None:
        raise requests.HTTPError(f"{self.status_code} Client Error: {self.reason} for url: {self.url}", response=self)


def test_the_body_excerpt_rides_the_same_exception_class_and_is_redacted() -> None:
    body = '{"error":{"message":"image_url is not supported by this endpoint","code":400}, "key":"sk-or-v1-abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789"}'
    with pytest.raises(requests.HTTPError) as info:
        adapter._raise_for_status_with_cause(_Response(400, body))
    text = str(info.value)
    assert text.startswith("400 Client Error")
    assert "provider said:" in text and "image_url is not supported" in text
    assert "sk-or-v1-abcdef0123456789" not in text
    assert info.value.__cause__ is not None


def test_a_body_that_is_empty_leaves_the_original_error_untouched() -> None:
    with pytest.raises(requests.HTTPError) as info:
        adapter._raise_for_status_with_cause(_Response(502, ""))
    assert "provider said" not in str(info.value)


def test_an_oversized_body_is_bounded() -> None:
    with pytest.raises(requests.HTTPError) as info:
        adapter._raise_for_status_with_cause(_Response(400, "x" * 10_000))
    assert len(str(info.value)) < 500


def test_a_healthy_response_passes_through() -> None:
    class _Ok:
        text = ""

        def raise_for_status(self) -> None:
            return None

    assert adapter._raise_for_status_with_cause(_Ok()) is None


def test_every_chat_call_site_uses_the_cause_aware_check() -> None:
    import inspect

    source = inspect.getsource(adapter)
    # The two prewarm probes keep the plain check (they never produce an operator-facing reason);
    # every chat/stream call site must go through the helper.
    body = source.split("def _ollama_prewarm_request", 1)[1]
    chat_sites = body.count("_raise_for_status_with_cause(response)")
    assert chat_sites >= 4, chat_sites
    _ = Any
