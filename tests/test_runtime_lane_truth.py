"""VOOL owns which provider is connected. A model must never answer it from priors.

Measured on the installed build at commit 12128c2 — 3/3 unpinned AND 3/3 pinned, by a blind QA pass.
Asked "Which cloud provider is currently connected to this runtime, and is the connection working?",
VOOL replied:

    "This runtime runs locally on your machine - there's no cloud provider connected. The
     'connection' is just the local execution environment (the sandbox/tooling available here),
     and it's working normally."

The reply contradicted itself inside one message. Its own footer read
``cloud | nemotron-3-ultra-550b-a55b:free | 1,295 tok``, and ``GET /api/cloud/status`` returned
``{"provider":"openrouter","state":"ok","detail":"authorized","http_status":200}`` at the same
moment. The turn was served BY the provider it denied having.

Nothing claimed the question, so it reached the model, and the model answered from what is usually
true of language models rather than from what was true of this runtime. The stated architecture is
that VOOL owns the trusted runtime facts and the model is a replaceable reasoning engine; the
connected provider, the key's probe verdict and the lane that served a turn are exactly those facts.

These fixtures pin four things: the question is CLAIMED (pinned and unpinned), the answer is READ
from the same source ``/api/cloud/status`` uses, no key material can reach chat, and the specific
false sentence above cannot be produced while a provider is connected.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from core import runtime_lane_truth
from core.runtime_lane_truth import (
    FACET_KEY,
    FACET_LANE,
    FACET_MODEL,
    FACET_PROVIDER,
    maybe_runtime_lane_answer,
    render_runtime_lane_answer,
    runtime_lane_question,
    runtime_lane_snapshot,
)

PINNED = "nvidia/nemotron-3-ultra-550b-a55b:free"

# A key-shaped value that the redaction helpers recognise, so "did any of it leak?" is a real
# question and not a formality.
FAKE_KEY = "sk-or-v1-0123456789abcdef0123456789abcdef0123456789abcdef"

CONNECTED = {
    "provider": "openrouter",
    "label": "OpenRouter",
    "state": "ok",
    "detail": "authorized",
    "checked_at": 1_780_000_000.0,
    "http_status": 200,
}


@pytest.fixture
def agent():
    from apps.vool_agent import VoolAgent

    return VoolAgent(backend_name="test-backend", device="openclaw-test", persona_id="default")


def _ask(agent, text: str, *, pinned: bool = False) -> dict | None:
    """Drive the REAL front door, so these fixtures measure routing and not just the detector."""
    context: dict[str, object] = {"surface": "api", "_owner_local": True}
    if pinned:
        context["requested_model"] = PINNED
    outcome = agent._handle_turn_frontdoor(
        raw_user_input=text,
        effective_input=text,
        normalized_input=text,
        source_surface="api",
        session_id=f"runtime-lane-{abs(hash(text)) % 10_000}-{int(pinned)}",
        source_context=context,
        persona=None,
        interpreted=SimpleNamespace(understanding_confidence=0.8),
    )
    return (outcome or {}).get("result")


# --------------------------------------------------------------------------------------
# The phrasings. The two named in the bug report are asserted by name, not by family.
# --------------------------------------------------------------------------------------

CLAIMED = [
    # The reproduced prompt, verbatim.
    ("Which cloud provider is currently connected to this runtime, and is the connection working?", FACET_PROVIDER),
    # Named in the fix request.
    ("are you running locally or in the cloud right now?", FACET_LANE),
    ("did a cloud model answer that?", FACET_MODEL),
    # The four families the handler owns, in the wording people actually use.
    ("which cloud provider is connected?", FACET_PROVIDER),
    ("what cloud provider are you using?", FACET_PROVIDER),
    ("is the connection working?", FACET_PROVIDER),
    ("is my cloud connection up?", FACET_PROVIDER),
    ("am i connected to a cloud provider?", FACET_PROVIDER),
    ("am I on cloud or local?", FACET_LANE),
    ("are you local or cloud?", FACET_LANE),
    ("is this running in the cloud?", FACET_LANE),
    ("was that a cloud model or a local one?", FACET_LANE),
    ("is my key working?", FACET_KEY),
    ("is my api key working?", FACET_KEY),
    ("does my openrouter key work?", FACET_KEY),
    ("is my api key valid?", FACET_KEY),
    ("which model answered this?", FACET_MODEL),
    ("what model is answering me?", FACET_MODEL),
    ("which model served this turn?", FACET_MODEL),
    ("what model answered that", FACET_MODEL),
    # Found by driving the built runtime with wording nobody had written a fixture for. Each of
    # these reached the model on the first pass of this fix and was answered from priors.
    ("so whats actually powering you right now, cloud or my own box?", FACET_LANE),
    ("whos doing the thinking here, my machine or someone elses server?", FACET_LANE),
    ("is the provider hooked up and healthy?", FACET_PROVIDER),
    ("are you connected?", FACET_PROVIDER),
]

# Questions that merely mention the same nouns. Seizing these would trade one confident wrong
# answer for another, so the guard is part of the fix rather than a nicety.
NOT_CLAIMED = [
    "what is best for llm runtime agents - Java or PY?",
    "what is best for my llm runtime agents - Java or PY?",
    "which inference backend would you recommend for my chatbot?",
    "what cloud provider should our team choose?",
    "which cloud provider is cheapest?",
    "which cloud provider does openai use?",
    "compare aws and gcp for hosting",
    "how do I check if my api key is working in python?",
    "write a function that connects to a cloud provider",
    "show me an example of an api key header",
    "explain how cloud model routing works",
    "which model is best for coding?",
    "what is the weather right now?",
    "read the file config.py",
    "what can you do?",
    "hi there",
    # This product also holds Solana keypairs; a wallet question is not a provider question.
    "is my wallet keypair working?",
    "is my solana private key still valid?",
    # Capability inventory, owned by the grounded inventory path. The full suite caught the middle
    # one: "locally ... right now" tripped a lane pattern and stole a prompt three contract
    # fixtures in tests/test_vool_runtime_contracts.py depend on.
    "one short line only: what can you actually do on this machine right now?",
    "real quick, what can you do locally on this machine right now?",
    "in one clean line, what are your actual local powers here?",
    "what can you do right now on this machine?",
    # These name a provider word directly, so the tightened lane patterns do NOT reject them and
    # only the inventory guard does. Kept separate so that guard cannot decay into dead code.
    "what can you do with the api?",
    "what can you do with the cloud?",
    "what can i do with your llm backend?",
    # "How is X connected?" asks for a mechanism, not a state. These are questions about the user's
    # own architecture and only the question form separates them from "is the provider connected?".
    "how is the api connected to the frontend?",
    "how are the backend and the database connected?",
    "explain how the api is wired to the queue",
    "how does the cloud provider handle retries?",
]


@pytest.mark.parametrize("text,facet", CLAIMED)
def test_a_runtime_truth_question_is_detected_with_the_right_facet(text: str, facet: str) -> None:
    assert runtime_lane_question(text) == facet, text


@pytest.mark.parametrize("text", NOT_CLAIMED)
def test_a_question_that_only_mentions_the_nouns_is_left_alone(text: str) -> None:
    assert runtime_lane_question(text) == "", text


# --------------------------------------------------------------------------------------
# Routing: the question must be CLAIMED at the front door, pinned and unpinned alike
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Which cloud provider is currently connected to this runtime, and is the connection working?",
        "are you running locally or in the cloud right now?",
        "did a cloud model answer that?",
        "is my api key working?",
        "which model answered this?",
    ],
)
@pytest.mark.parametrize("pinned", [False, True], ids=["unpinned", "pinned"])
def test_the_front_door_answers_it_instead_of_the_model(agent, text: str, pinned: bool) -> None:
    """The defect reproduced 3/3 in BOTH modes, so both are pinned here.

    A pinned model changes who reasons; it does not hand over the runtime's own facts. The handler
    sits above the ownership gate for the same reason the clock and the machine specs do.
    """

    result = _ask(agent, text, pinned=pinned)

    assert result is not None, f"{text!r} fell through to the model"
    assert "no model call" in str(result.get("response") or "")


@pytest.mark.parametrize("pinned", [False, True])
@pytest.mark.parametrize("text", NOT_CLAIMED[:4])
def test_recommendations_reach_reasoning_through_the_front_door(agent, text, pinned):
    assert _ask(agent, text, pinned=pinned) is None


def test_the_handler_runs_above_the_pinned_model_gate() -> None:
    """Ordering is the whole mechanism; a later edit that moves it below the gate must fail here."""

    from pathlib import Path

    source = Path(runtime_lane_truth.__file__).resolve().parents[1]
    frontdoor = (source / "core" / "agent_runtime" / "turn_frontdoor.py").read_text(encoding="utf-8")
    gate = frontdoor.index("model_owns_judgement = explicit_model_owns_semantic_turn(source_context)")
    capability = frontdoor.index("agent._maybe_handle_capability_truth_request(")

    assert capability < gate

    surface = (source / "core" / "agent_runtime" / "fast_command_surface.py").read_text(encoding="utf-8")
    handler = surface.index("def maybe_handle_capability_truth_request(")
    lane = surface.index("lane_answer = maybe_runtime_lane_answer(user_input)", handler)
    gap = surface.index("report = capability_truth_for_request_fn(", handler)

    assert lane < gap, (
        "the grounded answer must precede the capability-gap machinery, whose 'I can't confirm "
        "that' branch would otherwise hedge a question this runtime has hard evidence for"
    )


@pytest.mark.parametrize(
    "question,defers",
    [
        # A CONNECTION question that happens to contain a key-ish subject word and an incidental
        # verb. Found by driving the built runtime: the key-store handler claimed it at the front
        # door and answered "yes — a cloud key is sealed in the encrypted store" while the real
        # probe verdict was `untested`, which reads as confirming a connection nothing had checked.
        ("quick sanity check: is the openrouter connection actually alive?", True),
        ("is the cloud connection working?", True),
        # STORAGE questions. These are the key-store handler's own family and must stay with it.
        ("do we have the api key set?", False),
        ("is the openrouter key configured?", False),
        ("have we saved the cloud key?", False),
        ("we have api key set alr for Openrouter?", False),
        ("check if this already set", False),
    ],
)
def test_the_key_store_handler_defers_connection_questions_but_keeps_storage_ones(
    question: str, defers: bool
) -> None:
    """Whether a key is STORED and whether the CONNECTION works are different facts."""

    from core.agent_runtime.fast_command_surface import maybe_handle_cloud_key_status_intent

    reply = maybe_handle_cloud_key_status_intent(question, owner_local=True)

    if defers:
        assert reply is None, "a connection question must reach the handler that reads the probe"
    else:
        assert reply is not None, "a storage question must still be answered from the store"


def test_the_answer_is_recorded_as_a_deterministic_runtime_read(agent) -> None:
    """Not merely correct — attributed. The trace must show runtime state, not an unbacked claim."""

    assert agent._chat_truth_fast_path_backing_sources("runtime_lane_truth_query") == ["runtime_state"]

    from core.agent_runtime.response_policy_classification import fast_path_response_class

    assert (
        fast_path_response_class(agent, reason="runtime_lane_truth_query", response="x")
        is agent.ResponseClass.UTILITY_ANSWER
    )


# --------------------------------------------------------------------------------------
# The answer is READ, and it is the same read /api/cloud/status performs
# --------------------------------------------------------------------------------------


def test_the_snapshot_reads_the_same_source_the_status_endpoint_serves() -> None:
    """One fact, not two that can drift: the chat answer and the header pill share a call."""

    with mock.patch("core.cloud_connection_state.connection_status", return_value=CONNECTED) as status:
        snapshot = runtime_lane_snapshot()

    status.assert_called_once_with()
    assert snapshot["provider"] == "openrouter"
    assert snapshot["provider_label"] == "OpenRouter"
    assert snapshot["state"] == "ok"
    assert snapshot["http_status"] == 200


def test_a_chat_question_never_fires_a_live_probe_at_the_provider() -> None:
    """Cache-only, like the endpoint's default. A chat question must not become network traffic."""

    with mock.patch("core.cloud_connection_state.connection_status", return_value=CONNECTED) as status:
        runtime_lane_snapshot()

    assert status.call_args.kwargs.get("probe") in (None, False)


@pytest.mark.parametrize(
    "state,detail,expected",
    [
        ("ok", "authorized", "connected and authorized"),
        ("failed", "unauthorized", "NOT working"),
        ("untested", "key present, not yet verified", "no live probe has verified it yet"),
    ],
)
def test_each_real_connection_state_is_reported_as_itself(state: str, detail: str, expected: str) -> None:
    answer = render_runtime_lane_answer(
        {**CONNECTED, "state": state, "detail": detail, "provider_label": "OpenRouter"},
        facet=FACET_PROVIDER,
    )

    assert "OpenRouter" in answer
    assert expected in answer


def test_the_exact_false_claim_cannot_be_produced_while_a_provider_is_connected() -> None:
    """The regression, stated as the sentence that was actually served."""

    with mock.patch("core.cloud_connection_state.connection_status", return_value=CONNECTED):
        answer = maybe_runtime_lane_answer(
            "Which cloud provider is currently connected to this runtime, and is the connection working?"
        )

    lowered = answer.lower()
    assert "openrouter" in lowered
    assert "no cloud provider connected" not in lowered
    assert "there's no cloud provider" not in lowered
    assert "runs locally on your machine" not in lowered


def test_no_key_configured_is_reported_as_no_key_not_as_a_connection() -> None:
    """The inverse error matters just as much: an absent provider must not be invented."""

    with mock.patch(
        "core.cloud_connection_state.connection_status",
        return_value={"provider": "openrouter", "label": "OpenRouter", "state": "no_key",
                      "detail": "no cloud key configured", "checked_at": None, "http_status": None},
    ):
        answer = maybe_runtime_lane_answer("which cloud provider is connected?")

    assert "none connected" in answer.lower()
    assert "Key: none stored" in answer


def test_the_lane_that_actually_served_the_last_turn_is_reported(agent) -> None:
    """The footer said `cloud | nemotron`; the prose said local. They now come from one row."""

    served = {
        "provider_id": "openrouter-byok:nvidia/nemotron-3-ultra-550b-a55b:free",
        "model_id": PINNED,
        "cost_class": "free_cloud",
        "lane": "cloud",
        "created_at": "2026-07-30T09:12:00+00:00",
        "prompt_tokens": 1200,
        "output_tokens": 95,
    }
    with mock.patch("core.usage_meter.last_served_call", return_value=served), mock.patch(
        "core.cloud_connection_state.connection_status", return_value=CONNECTED
    ):
        answer = maybe_runtime_lane_answer("did a cloud model answer that?")

    assert "Last model call: cloud" in answer
    assert PINNED in answer
    assert "2026-07-30 09:12 UTC" in answer


def test_the_meter_row_is_what_makes_the_last_lane_readable_after_the_turn() -> None:
    """``get_turn_usage`` is a per-turn thread-local, reset at the start of every turn, so it is
    empty by construction on the fast-path turn that answers this question. The metered row is the
    only surviving evidence — reading it is the difference between an answer and a guess."""

    from core.memory_first_router import get_turn_usage, reset_turn_usage
    from core.usage_meter import last_served_call, record_usage

    reset_turn_usage()
    assert get_turn_usage() is None

    record_usage(
        provider_id="openrouter-byok:vendor/model",
        model_id="vendor/model",
        cost_class="paid_cloud",
        prompt_tokens=10,
        output_tokens=5,
    )
    row = last_served_call()

    assert row is not None
    assert row["lane"] == "cloud"
    assert row["model_id"] == "vendor/model"


def test_a_local_model_row_reports_the_local_lane() -> None:
    from core.usage_meter import last_served_call, record_usage

    record_usage(
        provider_id="ollama-local:qwen3:8b",
        model_id="qwen3:8b",
        cost_class="free_local",
        prompt_tokens=7,
        output_tokens=3,
    )
    row = last_served_call()

    assert row is not None and row["lane"] == "local" and row["model_id"] == "qwen3:8b"


# --------------------------------------------------------------------------------------
# Key STATUS only. Never key material.
# --------------------------------------------------------------------------------------


@pytest.fixture
def openrouter_is_active():
    """Pin OpenRouter as the active provider for the duration of a test.

    Which provider is active is saved runtime state, and a full-suite run leaves whatever an
    earlier test saved — the first version of the leak fixtures below resolved "Custom
    (OpenAI-compatible)" mid-suite, found no key there, and passed on an empty slot while proving
    nothing. Only the provider CHOICE is pinned; key resolution and ``connection_status`` stay real,
    because they are the code under test.
    """
    with mock.patch("core.cloud_connection_state._resolve_active_provider", return_value="openrouter"):
        yield


def _seed_probe_verdict(state: str, *, detail: str, http_status: int | None, key: str) -> None:
    """Make the REAL ``connection_status`` return ``state`` for a real, resolvable key.

    Writing the cache the way a completed probe would is what lets the leak test exercise every
    branch of ``_key_line``. Without it the isolated test home has no probe record, every run lands
    on ``untested``, and a leak planted in the ``ok`` branch ships green — which is exactly what a
    first version of this fixture did.
    """
    from core.cloud_connection_state import _identity_digest, _write_cached

    _write_cached(
        {
            "state": state,
            "detail": detail,
            "http_status": http_status,
            "checked_at": 1_780_000_000.0,
            "key_digest": _identity_digest("openrouter", key),
        },
        provider="openrouter",
    )


@pytest.mark.parametrize(
    "text",
    [
        "is my api key working?",
        "does my openrouter key work?",
        "Which cloud provider is currently connected to this runtime, and is the connection working?",
        "which model answered this?",
        "are you running locally or in the cloud right now?",
    ],
)
@pytest.mark.parametrize(
    "state,detail,http_status",
    [
        ("ok", "authorized", 200),
        ("failed", "unauthorized", 401),
        ("untested", "key present, not yet verified", None),
    ],
)
def test_a_real_resolvable_key_never_reaches_the_answer(
    monkeypatch, openrouter_is_active, text: str, state: str, detail: str, http_status: int | None
) -> None:
    """The guard tested against a key the runtime CAN resolve, on the UNMOCKED read path.

    ``OPENROUTER_API_KEY`` is the first env name ``cloud_connection_state._resolve_key`` consults,
    so this is the same value the probe would send as a Bearer header. ``connection_status`` is
    deliberately left real here: mocking it would skip the only code that touches the key, and the
    assertion would pass on an empty slot while proving nothing.

    Every probe verdict is covered, because each renders a different line and a leak only has to
    reach one of them.
    """

    monkeypatch.setenv("OPENROUTER_API_KEY", FAKE_KEY)
    _seed_probe_verdict(state, detail=detail, http_status=http_status, key=FAKE_KEY)

    answer = maybe_runtime_lane_answer(text)

    assert "OpenRouter" in answer, "the real key must have been resolved for this to be a test"
    assert FAKE_KEY not in answer
    for fragment_length in (12, 20, 32):
        assert FAKE_KEY[:fragment_length] not in answer, "not even a prefix of the key may print"
    assert "sk-or" not in answer


def test_the_seeded_verdict_really_drives_the_unmocked_read(monkeypatch, openrouter_is_active) -> None:
    """Guards the guard: if seeding stopped working, the leak test above would silently narrow to
    a single branch again and keep passing."""

    monkeypatch.setenv("OPENROUTER_API_KEY", FAKE_KEY)
    _seed_probe_verdict("ok", detail="authorized", http_status=200, key=FAKE_KEY)

    answer = maybe_runtime_lane_answer("is my api key working?")

    assert "accepted by a live auth probe" in answer
    assert "HTTP 200" in answer


def test_the_answer_passes_through_the_shared_redaction_helper() -> None:
    """A second, independent guard: if a provider label or model id ever carries a key shape it is
    masked on the way out rather than printed. Reuses ``core.secret_redaction``, not a local copy."""

    from core.secret_redaction import redact_secrets

    poisoned = {**CONNECTED, "provider_label": f"OpenRouter {FAKE_KEY}"}
    answer = render_runtime_lane_answer(poisoned, facet=FACET_PROVIDER)

    assert FAKE_KEY not in answer
    assert "[redacted-api-key]" in answer
    assert redact_secrets(answer) == answer, "rendering must already be redacted, not redactable"


def test_the_key_line_states_status_and_nothing_else() -> None:
    for state, expected in (
        ("ok", "accepted by a live auth probe"),
        ("failed", "REJECTED"),
        ("untested", "not yet verified by a live probe"),
        ("no_key", "none stored"),
    ):
        answer = render_runtime_lane_answer({**CONNECTED, "state": state}, facet=FACET_KEY)
        key_line = next(line for line in answer.splitlines() if line.startswith("Key:"))
        assert expected in key_line


def test_the_module_never_reaches_for_key_material_at_all() -> None:
    """Redaction is the backstop. The primary guarantee is that the value is never fetched.

    ``connection_status`` resolves the key internally to digest it, and returns a state — never the
    secret. This module must consume that state and never open the credential store itself.
    """

    from pathlib import Path

    source = Path(runtime_lane_truth.__file__).read_text(encoding="utf-8")
    body = source.split('"""', 2)[-1]  # skip the module docstring, which names these on purpose

    for forbidden in ("_resolve_key", "get_credential", "credential_store", "OPENROUTER_API_KEY", "os.environ"):
        assert forbidden not in body, f"{forbidden} must never be reachable from the chat answer"


# --------------------------------------------------------------------------------------
# Degradation: an unreadable source costs one line, never the whole answer
# --------------------------------------------------------------------------------------


def test_an_unreadable_source_degrades_one_line_and_still_answers() -> None:
    """Falling back to the model is what produced the wrong answer, so it is not a fallback."""

    with mock.patch("core.cloud_connection_state.connection_status", side_effect=RuntimeError("boom")), mock.patch(
        "core.usage_meter.last_served_call", side_effect=RuntimeError("boom")
    ):
        answer = maybe_runtime_lane_answer("which cloud provider is connected?")

    assert "could not read its own connection state" in answer
    assert "Last model call: none recorded" in answer
    assert "no model call" in answer


def test_depletion_idioms_are_not_lane_questions() -> None:
    """"running low on space on this machine" is a disk question, not a serving question.

    Measured live 2026-09-16: this exact sentence matched the serve-verb + locality pattern
    ("running" + "on this machine") and the largest-folders ask was answered with the canned
    "Last model call: ..." runtime-status text instead."""
    from core.runtime_lane_truth import runtime_lane_question

    for text in (
        "cool, anyways, I am running low on space on this machine, can you check what are the largest folders?",
        "we are running out of disk on this box, help me clean it",
        "the server is running slow on this machine, any ideas?",
        "I'm running short on memory on my own laptop, what can I close?",
    ):
        assert runtime_lane_question(text) == "", text
    # The genuine lane questions keep their answers.
    for text, want in (
        ("are you running locally right now?", "lane"),
        ("is this running in the cloud?", "lane"),
        ("what model answered this?", "model"),
    ):
        assert runtime_lane_question(text) == want, text
