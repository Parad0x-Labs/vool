"""An audit that never returns is worth less than a wrong one: the operator gets nothing.

Measured live 2026-08-01 against the daemon at 8fb1a25, driving the same audit of
`api/apache/liquefy_apache_repetition_v1.py` three times. The deterministic evidence pass worked
every time -- target read, references searched, git and manifests collected. No answering lane ever
delivered:

* `ollama-local:qwen3:14b` -> `Read timed out. (read timeout=180.0)`
* `openrouter-byok:nvidia/nemotron-3-ultra-550b-a55b:free` -> completed, then rejected as a
  reasoning monologue (7,559 tokens, byte-identical across runs)
* `ollama-local:qwen3:8b` -> `Read timed out. (read timeout=180.0)`

The operator saw "I couldn't get a usable model response in this run" every time.

A/B on the real audit prompt, same model, same box, thinking on vs off:

    think=True    128.3s   eval 1923 tok   thinking 7775 chars   content  540 chars
    think=False    16.1s   eval  230 tok   thinking    0 chars   content 1123 chars

Eight times faster, no reasoning tokens at all, and MORE answer. Later Set5 live evidence on the
shipped qwen3:4b exposed a version-sensitive trap: neither `think:false` nor Qwen's `/no_think`
switch reliably disables reasoning on this runtime. Daily Auto selection therefore avoids an
uncontrolled thinking model when a reliable non-thinking model is available; this legacy audit
wire contract remains pinned here but is not treated as proof of daily-lane reliability.

`think` is an OLLAMA parameter, so it never applied to the cloud lane at all -- nemotron monologues
regardless, and `reasoning_only_markers` is what catches that. This fixes the local lane only.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from adapters.base_adapter import ModelRequest
from adapters.openai_compatible_adapter import OpenAICompatibleAdapter


def _ollama_adapter(model_name: str = "qwen3:8b", **runtime_config) -> OpenAICompatibleAdapter:
    return OpenAICompatibleAdapter(
        SimpleNamespace(
            provider_id=f"ollama:{model_name}",
            provider_name="ollama",
            model_name=model_name,
            metadata={"runtime_family": "ollama"},
            runtime_config={"base_url": "http://127.0.0.1:11434", **runtime_config},
        )
    )


def _request(*, audit: bool) -> ModelRequest:
    return ModelRequest(
        task_kind="normalization_assist",
        prompt="name the highest-risk bug",
        metadata={"workspace_audit_turn": True} if audit else {},
    )


def test_an_audit_turn_stops_the_local_model_thinking() -> None:
    """The measured difference between 16.1s and a 180s timeout."""

    assert _ollama_adapter()._ollama_think_flag(_request(audit=True)) is False


def test_ordinary_chat_auto_uses_the_saved_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOOL_DEEP_REASONING", "0")

    assert _ollama_adapter()._ollama_think_flag(_request(audit=False)) is False
    assert _ollama_adapter()._ollama_think_flag() is False


def test_a_non_thinking_model_still_omits_the_key_on_an_audit() -> None:
    """qwen2.5:7b answers HTTP 400 when `think` is present at all -- audit or not."""

    assert _ollama_adapter("qwen2.5:7b")._ollama_think_flag(_request(audit=True)) is None
    assert _ollama_adapter("qwen2.5:7b")._ollama_think_flag(_request(audit=False)) is None


@pytest.mark.parametrize("audit, expected", [(True, False), (False, False)])
def test_the_flag_reaches_the_payload_ollama_actually_receives(audit: bool, expected: bool) -> None:
    """The seam that matters. A correct flag that never reaches the wire changes nothing -- the
    same class of gap as `subject_paths` reaching the build report but never the model."""

    adapter = _ollama_adapter()
    adapter.manifest.runtime_config["think"] = False
    payload = adapter._build_ollama_payload(_request(audit=audit), force_json=False, stream=False)
    assert payload["think"] is expected


def test_the_audit_marks_its_own_turn() -> None:
    """`workspace_audit_evidence_collected` is what run_workspace_audit sets on the turn it ran in,
    and it is the only signal that separates an audit from the six other task classes that share
    `task_kind == "normalization_assist"` (chat_conversation, general_advisory, business_advisory,
    food_nutrition, relationship_advisory, creative_ideation). Keying on task_kind would switch
    thinking off for ordinary chat, which is exactly what this must not do.
    """

    from core.memory_first_router import audit_turn_metadata

    assert audit_turn_metadata({"workspace_audit_evidence_collected": True}) == {
        "workspace_audit_turn": True
    }
    assert audit_turn_metadata({}) == {}
    assert audit_turn_metadata(None) == {}


@pytest.mark.parametrize(
    "source_context, expected_think",
    [
        ({"workspace_audit_evidence_collected": True}, False),
        ({}, False),
    ],
    ids=["audit turn", "ordinary turn"],
)
def test_the_router_stamps_the_flag_the_adapter_reads(
    monkeypatch: pytest.MonkeyPatch, source_context: dict, expected_think: bool
) -> None:
    """End to end across the seam: router builds the request, adapter builds the Ollama payload.

    Written because a sabotage went unnoticed. Deleting the stamp from `_build_request` broke
    nothing -- the tests covered the helper and the adapter but not the line joining them, which is
    the only line that makes the flag reach production. This drives the real `_build_request`.
    """

    import core.memory_first_router as router_module

    monkeypatch.setenv("VOOL_DEEP_REASONING", "0")

    class _Internal:
        context_summary: dict = {}
        temperature = None
        max_output_tokens = 512
        trace_id = "t"
        metadata: dict = {}
        attachments: list = []
        messages = [SimpleNamespace(role="user", content="name the highest-risk bug")]

        def user_prompt(self) -> str:
            return "name the highest-risk bug"

        def system_prompt(self) -> str:
            return "you audit code"

        def as_openai_messages(self) -> list[dict[str, str]]:
            return [{"role": "user", "content": "name the highest-risk bug"}]

    monkeypatch.setattr(router_module, "normalize_prompt", lambda **kw: _Internal())

    router = router_module.MemoryFirstRouter.__new__(router_module.MemoryFirstRouter)
    request = router_module.MemoryFirstRouter._build_request(
        router,
        task=SimpleNamespace(task_id="task-1", task_summary="audit it"),
        classification={},
        interpretation=SimpleNamespace(raw_text="audit it", reconstructed_text="audit it"),
        context_result=SimpleNamespace(),
        persona=SimpleNamespace(),
        output_mode="plain_text",
        task_kind="normalization_assist",
        surface="api",
        source_context=source_context,
    )
    payload = _ollama_adapter()._build_ollama_payload(request, force_json=False, stream=False)
    assert payload["think"] is expected_think


# --------------------------------------------------------------------------------------
# The cloud twin of the stamp: OpenRouter reasoning off on audit turns (2026-08-01)
# --------------------------------------------------------------------------------------


def _openrouter_adapter(model_name: str = "nvidia/nemotron-3-ultra-550b-a55b:free") -> OpenAICompatibleAdapter:
    return OpenAICompatibleAdapter(
        SimpleNamespace(
            provider_id=f"openrouter-byok:{model_name}",
            provider_name="openrouter-byok",
            model_name=model_name,
            metadata={},
            runtime_config={"base_url": "https://openrouter.ai/api/v1"},
        )
    )


def test_an_audit_turn_disables_openrouter_reasoning() -> None:
    """Measured on the stepped audit's bounded nominate call: nemotron spent EXACTLY its widened
    budget (700 asked + 2048 thinking reserve = 2748 output tokens) reasoning, twice, and returned
    no usable content either time. `think` never reached the cloud lane; this knob is its twin."""

    payload = _openrouter_adapter()._build_openai_payload(
        _request(audit=True), force_json=False, stream=False
    )
    assert payload["reasoning"] == {"enabled": False}


def test_ordinary_cloud_chat_keeps_its_reasoning() -> None:
    payload = _openrouter_adapter()._build_openai_payload(
        _request(audit=False), force_json=False, stream=False
    )
    assert "reasoning" not in payload


def test_a_strict_openai_server_never_receives_the_unknown_field() -> None:
    """A non-OpenRouter server that declared nothing may 400 on unknown fields — the knob must
    stay inside the lanes where it is known safe."""

    adapter = OpenAICompatibleAdapter(
        SimpleNamespace(
            provider_id="somecloud:nemotron-clone",
            provider_name="somecloud",
            model_name="nemotron-clone",
            metadata={},
            runtime_config={"base_url": "https://api.example.com/v1"},
        )
    )
    payload = adapter._build_openai_payload(_request(audit=True), force_json=False, stream=False)
    assert "reasoning" not in payload


def test_a_declared_reasoning_provider_gets_the_knob_without_being_openrouter() -> None:
    adapter = OpenAICompatibleAdapter(
        SimpleNamespace(
            provider_id="somecloud:deepthink",
            provider_name="somecloud",
            model_name="deepthink-large",
            metadata={"supported_parameters": ["reasoning"]},
            runtime_config={"base_url": "https://api.example.com/v1"},
        )
    )
    payload = adapter._build_openai_payload(_request(audit=True), force_json=False, stream=False)
    assert payload["reasoning"] == {"enabled": False}
