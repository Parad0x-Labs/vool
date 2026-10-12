"""An endpoint that makes reasoning mandatory must never be sent reasoning-disabled.

Measured 2026-10-05 on a live 8-turn chat through OpenRouter with z-ai/glm-5.3-flashx: every
helper call (request decomposition, the entity-ambiguity check) went out as
`reasoning: {"enabled": false}` and came back HTTP 400 "Reasoning is mandatory for this endpoint and
cannot be disabled." 4 of 8 turns in one chat and 5 of 8 in another answered only with a failure
notice. The same request without the field returned 200. The catalog publishes the fact:
`"reasoning": {"mandatory": true, "supported_efforts": ["max", "high", "low"]}`.

Uses a REAL local HTTP server, like tests/test_missing_textual_content_repair.py, so requests.post,
the status line and the body are the real ones.
"""
from __future__ import annotations

import http.server
import json
import threading
from types import SimpleNamespace

import pytest

import adapters.openai_compatible_adapter as adapter_module
from adapters.base_adapter import ModelRequest
from adapters.openai_compatible_adapter import OpenAICompatibleAdapter
from core.openrouter_catalog import OpenRouterModel, parse_openrouter_catalog

_MANDATORY_BODY = {
    "error": {
        "message": "Reasoning is mandatory for this endpoint and cannot be disabled.",
        "code": 400,
        "metadata": {"provider_name": None},
    }
}
_OK_BODY = {
    "id": "gen-1",
    "model": "fixture-reasoner",
    "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "ok"}}],
    "usage": {"prompt_tokens": 10, "completion_tokens": 2},
}


class _Handler(http.server.BaseHTTPRequestHandler):
    seen: list[dict] = []
    refuse_disabled = True
    other_400 = False
    other_message = "max_tokens is too large"

    def log_message(self, fmt, *args):
        pass

    def do_POST(self):
        cls = type(self)
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        cls.seen.append(body)
        if cls.other_400:
            status, reply = 400, {"error": {"message": cls.other_message, "code": 400}}
        elif cls.refuse_disabled and dict(body.get("reasoning") or {}).get("enabled") is False:
            status, reply = 400, _MANDATORY_BODY
        else:
            status, reply = 200, _OK_BODY
        data = json.dumps(reply).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture()
def server(monkeypatch):
    _Handler.seen = []
    _Handler.refuse_disabled = True
    _Handler.other_400 = False
    _Handler.other_message = "max_tokens is too large"
    monkeypatch.setattr(adapter_module, "_REASONING_MANDATORY_LEARNED", set())
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1]
    httpd.shutdown()
    thread.join(timeout=5)


def _adapter(port: int, model: str = "fixture-reasoner") -> OpenAICompatibleAdapter:
    return OpenAICompatibleAdapter(
        SimpleNamespace(
            provider_name="fixture-cloud",
            provider_id=f"fixture-cloud:{model}",
            model_name=model,
            metadata={"runtime_family": "openai-compatible", "supported_parameters": ["reasoning"]},
            runtime_config={"base_url": f"http://127.0.0.1:{port}", "api_path": "/v1/chat/completions", "timeout_seconds": 5.0},
        )
    )


def _disabled_request() -> ModelRequest:
    return ModelRequest(
        task_kind="chat",
        prompt="split this",
        output_mode="plain_text",
        reasoning_mode="disabled",
        max_output_tokens=400,
        messages=[{"role": "user", "content": "split this"}],
    )


def test_the_catalog_row_carries_the_published_reasoning_contract() -> None:
    rows = parse_openrouter_catalog(
        {
            "data": [
                {"id": "z-ai/glm-5.3-flashx", "pricing": {"prompt": "0.00000037", "completion": "0.00000125"},
                 "reasoning": {"mandatory": True, "supported_efforts": ["max", "high", "low"], "default_effort": "max"}},
                {"id": "anthropic/claude-sonnet-4.5", "pricing": {"prompt": "0.000003", "completion": "0.000015"},
                 "reasoning": {"mandatory": False}},
                {"id": "openai/gpt-4.1-mini", "pricing": {"prompt": "0.0000004", "completion": "0.0000016"}},
            ]
        },
        fetched_at="2026-10-05T00:00:00Z",
    )
    by_id = {row.model_id: row for row in rows}
    assert by_id["z-ai/glm-5.3-flashx"].reasoning_mandatory is True
    assert by_id["z-ai/glm-5.3-flashx"].reasoning_efforts == ("max", "high", "low")
    assert by_id["anthropic/claude-sonnet-4.5"].reasoning_mandatory is False
    assert by_id["openai/gpt-4.1-mini"].reasoning_mandatory is False
    assert by_id["openai/gpt-4.1-mini"].reasoning_efforts == ()


def test_a_catalog_mandatory_reasoner_gets_its_lowest_effort_not_disabled(monkeypatch) -> None:
    row = OpenRouterModel(
        model_id="z-ai/glm-5.3-flashx", name="GLM", context_length=200000,
        prompt_usd_per_token=3.7e-7, completion_usd_per_token=1.25e-6, request_usd=None,
        supported_parameters=("reasoning", "include_reasoning"), input_modalities=("text",),
        output_modalities=("text",), fetched_at="2026-10-05T00:00:00Z",
        reasoning_mandatory=True, reasoning_efforts=("max", "high", "low"),
    )
    monkeypatch.setattr("core.openrouter_catalog.cached_catalog_row", lambda model_id: row)
    monkeypatch.setattr(adapter_module, "_REASONING_MANDATORY_LEARNED", set())
    adapter = OpenAICompatibleAdapter(
        SimpleNamespace(
            provider_name="openrouter-byok", provider_id="openrouter-byok:z-ai/glm-5.3-flashx",
            model_name="z-ai/glm-5.3-flashx", metadata={},
            runtime_config={"base_url": "https://openrouter.ai/api"},
        )
    )
    payload = adapter._build_openai_payload(_disabled_request(), force_json=False, stream=False)

    assert payload["reasoning"] == {"effort": "low"}
    # It always reasons, so the answer budget keeps a thinking reserve on top.
    assert payload["max_tokens"] > 400


def test_a_model_that_allows_disabling_still_gets_disabled(monkeypatch) -> None:
    monkeypatch.setattr("core.openrouter_catalog.cached_catalog_row", lambda model_id: None)
    monkeypatch.setattr(adapter_module, "_REASONING_MANDATORY_LEARNED", set())
    adapter = OpenAICompatibleAdapter(
        SimpleNamespace(
            provider_name="openrouter-byok", provider_id="openrouter-byok:anthropic/claude-sonnet-4.5",
            model_name="anthropic/claude-sonnet-4.5", metadata={},
            runtime_config={"base_url": "https://openrouter.ai/api"},
        )
    )
    payload = adapter._build_openai_payload(_disabled_request(), force_json=False, stream=False)

    assert payload["reasoning"] == {"enabled": False}


def test_an_unlisted_refusal_is_learned_and_resent_once_at_low_effort(server) -> None:
    adapter = _adapter(server)

    response = adapter.run_text_task(_disabled_request())

    assert response.output_text == "ok"
    assert [body.get("reasoning") for body in _Handler.seen] == [{"enabled": False}, {"effort": "low"}]
    # The next call does not pay the refusal again.
    adapter.run_text_task(_disabled_request())
    assert _Handler.seen[-1]["reasoning"] == {"effort": "low"}
    assert len(_Handler.seen) == 3


def test_any_other_400_still_fails_closed_with_one_request(server) -> None:
    _Handler.other_400 = True

    with pytest.raises(Exception) as caught:
        _adapter(server).run_text_task(_disabled_request())

    assert "max_tokens is too large" in str(caught.value)
    assert len(_Handler.seen) == 1
    assert set() == adapter_module._REASONING_MANDATORY_LEARNED


# ── re-audit: only the endpoint's own refusal teaches, and only that endpoint ──


@pytest.mark.parametrize(
    "message",
    [
        "max_tokens invalid; documentation says reasoning is mandatory for another endpoint",
        "Invalid value for max_tokens. Note: reasoning is mandatory on some models.",
        "temperature must be <= 2 (see docs: Reasoning is mandatory for this endpoint on o-series)",
    ],
)
def test_a_400_that_only_mentions_mandatory_reasoning_is_that_other_error(server, monkeypatch, message) -> None:
    monkeypatch.setattr("core.openrouter_catalog.cached_catalog_row", lambda _model: None)
    _Handler.other_400 = True
    _Handler.other_message = message

    with pytest.raises(Exception) as caught:
        _adapter(server).run_text_task(_disabled_request())

    assert message[:20] in str(caught.value)
    assert len(_Handler.seen) == 1
    assert set() == adapter_module._REASONING_MANDATORY_LEARNED


def test_a_refusal_learned_at_one_endpoint_does_not_change_another(server, monkeypatch) -> None:
    monkeypatch.setattr("core.openrouter_catalog.cached_catalog_row", lambda _model: None)
    refusing = _adapter(server, model="same-model")
    assert refusing.run_text_task(_disabled_request()).output_text == "ok"
    assert [body.get("reasoning") for body in _Handler.seen] == [{"enabled": False}, {"effort": "low"}]

    # The same model name at another provider and endpoint never refused anything: it keeps the
    # disabled reasoning and the plain answer budget it asked for.
    other = OpenAICompatibleAdapter(
        SimpleNamespace(
            provider_name="other-cloud", provider_id="other-cloud:same-model", model_name="same-model",
            metadata={"runtime_family": "openai-compatible", "supported_parameters": ["reasoning"]},
            runtime_config={"base_url": "http://127.0.0.1:9", "api_path": "/v1/chat/completions"},
        )
    )
    payload = other._build_openai_payload(_disabled_request(), force_json=False, stream=False)
    assert payload["reasoning"] == {"enabled": False}
    assert payload["max_tokens"] == 400
    # ...while the endpoint that refused keeps what it learned
    again = refusing._build_openai_payload(_disabled_request(), force_json=False, stream=False)
    assert again["reasoning"] == {"effort": "low"}
