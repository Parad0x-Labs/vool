from __future__ import annotations

import pytest

from core.null_protocol import (
    NullProtocolError,
    NullRequest,
    parse_null_uri,
    resolve_null_request,
)


def test_parse_basic_uri() -> None:
    uri = parse_null_uri("null://task/code-review")
    assert uri.service == "task"
    assert uri.path == "code-review"
    assert uri.params == {}


def test_parse_uri_with_query_params() -> None:
    uri = parse_null_uri("null://embed/search?q=foo&limit=10")
    assert uri.service == "embed"
    assert uri.path == "search"
    assert uri.params["q"] == ["foo"]
    assert uri.params["limit"] == ["10"]


def test_parse_uri_no_path_is_fine() -> None:
    uri = parse_null_uri("null://pay")
    assert uri.service == "pay"
    assert uri.path == ""


def test_parse_rejects_wrong_scheme() -> None:
    with pytest.raises(NullProtocolError, match="scheme"):
        parse_null_uri("https://task/code-review")


def test_parse_rejects_empty_string() -> None:
    with pytest.raises(NullProtocolError):
        parse_null_uri("")


def test_parse_rejects_invalid_service_chars() -> None:
    with pytest.raises(NullProtocolError, match="service"):
        parse_null_uri("null://UPPER-CASE/path")


def test_parse_preserves_raw_uri() -> None:
    raw = "null://task/code-review?q=1"
    uri = parse_null_uri(raw)
    assert uri.raw == raw


def test_resolve_returns_request_with_quote() -> None:
    req = resolve_null_request("null://task/code-review", session_id="sess-1")
    assert isinstance(req, NullRequest)
    assert req.uri.service == "task"
    assert req.session_id == "sess-1"
    assert req.quote is not None
    assert req.quote.amount_usdc > 0


def test_resolve_uses_explicit_price_param() -> None:
    req = resolve_null_request("null://task/foo?price=0.05")
    assert req.quote.amount_usdc == pytest.approx(0.05)


def test_resolve_auto_generates_session_id() -> None:
    req = resolve_null_request("null://embed/vec")
    assert req.session_id.startswith("null-")


def test_embed_service_is_cheaper_than_task() -> None:
    req_task  = resolve_null_request("null://task/x")
    req_embed = resolve_null_request("null://embed/x")
    assert req_embed.quote.amount_usdc < req_task.quote.amount_usdc


def test_pay_service_has_zero_price() -> None:
    req = resolve_null_request("null://pay/transfer")
    # price floor is price_per_token_usdc (1e-6), so just check it's very small
    assert req.quote.amount_usdc <= 0.000001
