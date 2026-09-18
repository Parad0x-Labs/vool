from __future__ import annotations

import json
from typing import Any
from unittest import mock

from core.web.api.runtime import RuntimeServices
from core.web.api.service import _attach_work_receipt, dispatch_post

# ---------------------------------------------------------------------------
# _attach_work_receipt helper
# ---------------------------------------------------------------------------

def test_attach_work_receipt_adds_receipt_to_payload() -> None:
    payload: dict[str, Any] = {"response": "hello"}
    result = _attach_work_receipt(payload, result={"response": "hello"}, session_id="sess-1")
    assert "web0_receipt" in result
    receipt = result["web0_receipt"]
    assert receipt["task_id"] == "sess-1"
    assert receipt["receipt_id"].startswith("wr-")
    assert receipt["result_hash"]


def test_attach_work_receipt_leaves_original_payload_intact() -> None:
    original: dict[str, Any] = {"response": "hello", "confidence": 0.9}
    result = _attach_work_receipt(original, result={"response": "hello"}, session_id="s")
    assert result["confidence"] == 0.9
    assert result["response"] == "hello"


def test_attach_work_receipt_no_op_on_empty_response() -> None:
    payload: dict[str, Any] = {"response": ""}
    result = _attach_work_receipt(payload, result={"response": ""}, session_id="s")
    assert "web0_receipt" not in result


def test_attach_work_receipt_survives_import_failure() -> None:
    with mock.patch("core.web.api.service._attach_work_receipt", wraps=_attach_work_receipt):
        with mock.patch("core.web0_work_receipt.issue_work_receipt", side_effect=RuntimeError("fail")):
            payload: dict[str, Any] = {"response": "text"}
            result = _attach_work_receipt(payload, result={"response": "text"}, session_id="s")
    # Should not raise; payload may or may not have the receipt depending on import cache
    assert isinstance(result, dict)


def test_attach_work_receipt_receipt_is_json_serialisable() -> None:
    payload: dict[str, Any] = {"response": "output text"}
    result = _attach_work_receipt(payload, result={"response": "output text"}, session_id="s2")
    json.dumps(result)


# ---------------------------------------------------------------------------
# /api/null  null:// route via dispatch_post
# ---------------------------------------------------------------------------

def _runtime() -> RuntimeServices:
    return RuntimeServices(display_name="VOOL")


def _fake_agent(
    runtime: RuntimeServices,
    text: str,
    *,
    session_id: str | None = None,
    source_context: dict[str, Any] | None = None,
    workspace_root_provider=None,
) -> dict[str, Any]:
    return {"response": f"processed: {text}", "confidence": 1.0}


def _post(path: str, body: dict[str, Any]) -> Any:
    # Inject a resolver that always misses so the null:// route never touches the
    # network in unit tests; behavior matches an unresolved name (local run).
    return dispatch_post(
        path=path,
        body=body,
        headers={},
        runtime=_runtime(),
        model_name="vool",
        workspace_root_provider=lambda: "/tmp",
        run_agent_provider=_fake_agent,
        resolve_null_domain_provider=lambda name: None,
    )


def test_null_route_returns_200_with_service_and_path() -> None:
    resp = _post("/api/null", {"uri": "null://task/code-review", "prompt": "review this file"})
    assert resp.status == 200
    data = json.loads(resp.body)
    assert data["service"] == "task"
    assert data["path"] == "code-review"
    assert data["result"].startswith("processed:")


def test_null_route_v1_path_also_works() -> None:
    resp = _post("/v1/null", {"uri": "null://embed/search", "prompt": "embed this"})
    assert resp.status == 200
    data = json.loads(resp.body)
    assert data["service"] == "embed"


def test_null_route_returns_receipt_id() -> None:
    resp = _post("/api/null", {"uri": "null://task/summarize", "prompt": "summarize"})
    data = json.loads(resp.body)
    assert data["receipt_id"] is not None
    assert data["receipt_id"].startswith("wr-")


def test_null_route_returns_quote() -> None:
    resp = _post("/api/null", {"uri": "null://task/run"})
    data = json.loads(resp.body)
    assert data["quote"] is not None
    assert data["quote"]["amount_usdc"] > 0


def test_null_route_zk_proof_is_none_by_default() -> None:
    resp = _post("/api/null", {"uri": "null://task/run"})
    data = json.loads(resp.body)
    assert data["zk_proof"] is None


def test_null_route_missing_uri_returns_400() -> None:
    resp = _post("/api/null", {"prompt": "no uri here"})
    assert resp.status == 400
    data = json.loads(resp.body)
    assert "uri" in data["error"]


def test_null_route_bad_scheme_returns_400() -> None:
    resp = _post("/api/null", {"uri": "https://task/code-review"})
    assert resp.status == 400


def test_null_route_session_id_in_response() -> None:
    resp = _post("/api/null", {"uri": "null://task/x"})
    data = json.loads(resp.body)
    assert data["session_id"]


def test_null_route_prompt_fallback_uses_uri_path() -> None:
    captured: list[str] = []

    def capturing_agent(
        runtime: RuntimeServices,
        text: str,
        *,
        session_id: str | None = None,
        source_context: dict[str, Any] | None = None,
        workspace_root_provider=None,
    ) -> dict[str, Any]:
        captured.append(text)
        return {"response": "ok"}

    dispatch_post(
        path="/api/null",
        body={"uri": "null://task/my-job"},
        headers={},
        runtime=_runtime(),
        model_name="vool",
        workspace_root_provider=lambda: "/tmp",
        run_agent_provider=capturing_agent,
        resolve_null_domain_provider=lambda name: None,
    )
    assert captured == ["my-job"]


# ---------------------------------------------------------------------------
# /api/chat non-streaming now includes web0_receipt
# ---------------------------------------------------------------------------

def test_chat_non_streaming_response_includes_web0_receipt() -> None:
    resp = dispatch_post(
        path="/api/chat",
        body={"messages": [{"role": "user", "content": "hello world"}]},
        headers={},
        runtime=_runtime(),
        model_name="vool",
        workspace_root_provider=lambda: "/tmp",
        run_agent_provider=lambda rt, text, **kw: {"response": "hello back", "confidence": 1.0},
    )
    assert resp.status == 200
    data = json.loads(resp.body)
    assert "web0_receipt" in data
    assert data["web0_receipt"]["receipt_id"].startswith("wr-")
