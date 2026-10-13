from __future__ import annotations

from pathlib import Path

import pytest

from core.context_namespace import ensure_chat_namespace, set_chat_namespace_state
from core.context_retrieval import SEMANTIC_MEMORY_AGENT_ID
from core.context_scope import ContextAccessPolicy
from core.human_input_adapter import HumanInputInterpretation
from core.identity_manager import load_active_persona
from core.internal_message_schema import is_turn_directives_message
from core.memory.revocation import initialize_memory_home
from core.memory_first_router import MemoryFirstRouter
from core.memory_recall_intent import revoked_memory_recall_clauses
from core.persistent_memory import maybe_handle_memory_command, search_relevant_memory
from core.prompt_budget import fit_messages_to_context_window
from core.runtime_paths import active_vool_home, configure_runtime_home
from core.task_router import create_task_record
from core.vool_memory import VoolMemory
from core.web.api.runtime import RuntimeServices, _memory_recall_response
from tests.test_prompt_assembly_profiles import _synthetic_context_result


@pytest.fixture(autouse=True)
def isolated_memory_home(tmp_path, monkeypatch):
    from core import task_state_machine, trace_id
    from storage import db

    previous_db_override = db._DEFAULT_DB_PATH_OVERRIDE
    home = tmp_path / "memory-home"
    monkeypatch.setenv("VOOL_HOME", str(home))
    configure_runtime_home(home)
    # The typed operator profile uses the active SQLite authority. A preceding
    # suite may select it explicitly, so the fixture must own that store too.
    db.configure_default_db_path(db.resolve_runtime_db_filename(home / "data"))
    for module in (trace_id, task_state_machine):
        monkeypatch.setattr(module, "_TABLE_READY", False)
    try:
        db.init_schema()
        initialize_memory_home(home)
        yield home
    finally:
        db.configure_default_db_path(previous_db_override)
        configure_runtime_home(None)


@pytest.mark.parametrize(
    "question",
    ["Give a concise explanation of how a lighthouse works.", "Explain photosynthesis in exactly 2 sentences."],
)
def test_real_request_builder_delivers_split_protected_guidance(question):
    task = create_task_record(question)
    interpretation = HumanInputInterpretation(
        raw_text=question, normalized_text=question, reconstructed_text=question,
        intent_mode="request", topic_hints=[], reference_targets=[],
        understanding_confidence=0.95, quality_flags=[],
    )
    request = MemoryFirstRouter()._build_request(
        task=task, classification={"task_class": "unknown", "confidence_hint": 0.95, "risk_flags": []},
        interpretation=interpretation, context_result=_synthetic_context_result(),
        persona=load_active_persona("default"), output_mode="plain_text",
        task_kind="normalization_assist", surface="openclaw",
        source_context={"surface": "openclaw", "platform": "api"},
    )
    primary = request.messages[0]["content"]
    assert request.system_prompt.startswith(primary)
    assert request.system_prompt != primary
    additions = request.system_prompt[len(primary):].strip()
    assert any(is_turn_directives_message(message) and additions in message["content"]
               for message in request.messages)
    result = fit_messages_to_context_window(
        request.messages, num_ctx=24576, output_reserve_tokens=476,
        protected_system_prompt=request.system_prompt,
    )
    assert result.messages == request.messages
    assert result.telemetry["system_prompt_preserved"] is True
    assert result.telemetry["status"] == "fit"


def _context(chat="erased-chat", **extra):
    return {
        "surface": "openclaw", "platform": "api", "_owner_local": True,
        "chat_id": chat, "runtime_home": str(active_vool_home()), **extra,
    }


def _recall(question, chat="erased-chat", **extra):
    return _memory_recall_response(
        RuntimeServices(runtime_home=str(active_vool_home())),
        user_text=question, source_context=_context(chat, **extra),
    )


def _remember_and_forget(fact, subject, chat="erased-chat"):
    ensure_chat_namespace(chat, grant_confirmed_profile=True)
    handled, answer = maybe_handle_memory_command(
        f"Remember that {fact}", session_id=chat, source_context=_context(chat),
    )
    assert handled and "Locked in" in answer
    policy = ContextAccessPolicy.for_request(session_id=chat, source_context=_context(chat))
    hits = search_relevant_memory(subject, access_policy=policy, limit=10)
    assert any(fact.rstrip(".") in hit["text"] for hit in hits)
    handled, answer = maybe_handle_memory_command(
        f"Forget {subject}.", session_id=chat, source_context=_context(chat),
    )
    assert handled and answer.startswith("Forget applied"), answer
    assert not search_relevant_memory(subject, access_policy=policy, limit=10)


@pytest.mark.parametrize(
    ("fact", "subject", "questions"),
    [
        ("Maya's birthday is March 14.", "Maya's birthday",
         ("When is Maya's birthday?", "What do you know about Maya's birthday?")),
        ("The sailboat locker code is QRS-6725.", "the sailboat locker code",
         ("What is the sailboat locker code?", "Do you remember the sailboat locker code?")),
    ],
)
def test_completed_scoped_forget_answers_no_record_from_real_memory_authority(fact, subject, questions):
    for question in questions:
        assert _recall(question) is None
    _remember_and_forget(fact, subject)
    for question in questions:
        result = _recall(question)
        assert result is not None
        assert "no record" in result["response"].lower()
        assert "which" not in result["response"].lower()
        assert "March 14" not in result["response"]
        assert "QRS-6725" not in result["response"]
        assert result["route_reason"] == "completed_scoped_erasure"
        assert result["model_calls"] == result["web_calls"] == 0


def test_erasure_recall_respects_chat_owner_group_and_lifecycle_boundaries():
    _remember_and_forget("The harbor access code is ZX-8291.", "the harbor access code")
    question = "What is the harbor access code?"
    assert _recall(question, chat="foreign-chat") is None
    assert _recall(question, _owner_local=False) is None
    assert _recall(question, is_group=True) is None
    assert _recall("When was Isaac Newton born?") is None
    assert _recall("Print the harbor access code as a heading.") is None
    set_chat_namespace_state("erased-chat", "archived")
    assert _recall(question) is None


def test_pending_or_unreadable_erasure_never_claims_no_record():
    _remember_and_forget("The museum delivery date is April 22.", "the museum delivery date")
    question = "When is the museum delivery date?"
    assert _recall(question) is not None
    with VoolMemory(runtime_home=str(active_vool_home()), agent_id=SEMANTIC_MEMORY_AGENT_ID) as memory:
        operation = memory.begin_erasure("erased-chat", "the museum delivery date")
        assert _recall(question) is None
        memory.complete_erasure(operation)
    assert _recall(question) is not None
    authority = Path(active_vool_home()) / ".forget-authority" / "ledger.json"
    assert authority.is_file()
    authority.write_text("{malformed", encoding="utf-8")
    assert _recall(question) is None


def test_revoked_question_is_only_its_slice_and_does_not_claim_commands():
    tokens = ("the workshop cabinet code",)
    question = "What is the workshop cabinet code?"
    assert revoked_memory_recall_clauses(question, revoked_tokens=tokens) == (question,)
    mixed = f"{question} Also explain why tides rise."
    assert revoked_memory_recall_clauses(mixed, revoked_tokens=tokens) == (question,)
    assert revoked_memory_recall_clauses(
        'Write "What is the workshop cabinet code?" into a heading.', revoked_tokens=tokens
    ) == ()
    assert revoked_memory_recall_clauses(
        "What is the workshop cabinet code and convert 100 EUR to USD?", revoked_tokens=tokens
    ) == ()


@pytest.mark.parametrize(
    ("subject", "fact", "private_question", "public_questions"),
    [
        ("Maya", "Maya's birthday is March 14.", "When is Maya's birthday?",
         ("When was Maya Angelou born?", "When is Maya Angelou's birthday?", "What is Mayan civilization?")),
        ("Alex Rivera", "Alex Rivera's locker code is YTR-4526.", "What is Alex Rivera's locker code?",
         ("What is Alex Riverand's locker code?", "What is Alex Rivera Stone's locker code?")),
    ],
)
def test_whole_subject_forget_uses_complete_entity_identity(subject, fact, private_question, public_questions):
    _remember_and_forget(fact, subject)
    result = _recall(private_question)
    assert result is not None and "no record" in result["response"].lower()
    for question in public_questions:
        assert _recall(question) is None, question


def test_full_relation_forget_does_not_claim_a_different_relation_or_entity():
    _remember_and_forget("Maya's birthday is March 14.", "Maya's birthday")
    assert _recall("When is Maya's birthday?") is not None
    assert _recall("When is the birthday of Maya?") is not None
    assert _recall("When is Maya's birthday party?") is None
    assert _recall("What is Maya's birthday cake recipe?") is None
    assert _recall("When is Maya Angelou's birthday?") is None
    assert _recall("What is the meaning of Maya's birthday in astrology?") is None


@pytest.mark.parametrize("sibling", [
    "Also explain photosynthesis.", "Also convert 100 EUR to USD at 1.10.",
    "Also what is my answer style?", "Also what is my brother's name?",
])
def test_api_erased_memory_claim_preserves_every_sibling_demand(sibling):
    """Pin the real API handoff; stopping at the agent is instrumentation, not a model reply."""
    from core.agent_runtime.answer_coverage import FAMILY_MEMORY_RECALL, demand_units, turn_slices
    from core.web.api.runtime import run_agent
    from tests.operator_profile_rig import set_owner_preferred_name

    set_owner_preferred_name("Sora")
    _remember_and_forget("Maya's birthday is March 14.", "Maya's birthday")
    question = "When is Maya's birthday?"
    message = f"{question} {sibling}"
    captured = []

    class AgentBoundaryReached(RuntimeError):
        pass

    class RecordAgentBoundary:
        def run_once(self, user_text, *, session_id_override, source_context):
            captured.append((user_text, source_context))
            raise AgentBoundaryReached

    with pytest.raises(AgentBoundaryReached):
        run_agent(
            RuntimeServices(agent=RecordAgentBoundary(), runtime_home=str(active_vool_home())),
            message, session_id="erased-chat", source_context=_context(),
            workspace_root_provider=lambda: str(active_vool_home() / "workspace"),
        )
    assert len(captured) == 1
    assert captured[0][0] == message
    context = captured[0][1]
    answers = context["turn_answer_coverage"]["answers"]
    answer = next(item for item in answers if item["family"] == FAMILY_MEMORY_RECALL)
    memory_slice = next(item.slice_id for item in turn_slices(message) if item.text == question)
    assert answer["consumed"] == [memory_slice]
    units = demand_units(message)
    assert len(units) == 2
    assert answer["consumed_units"] == [unit.unit_id for unit in units if unit.slice_id == memory_slice]
    assert len(answer["consumed_units"]) == 1
    observations = context["runtime_tool_observations"]
    observation = next(item for item in observations if item["intent"] == f"turn.slice_answer.{FAMILY_MEMORY_RECALL}")
    assert observation["slice_text"] == question
    assert "no record" in observation["response_preview"].lower()


@pytest.mark.parametrize(("fact", "subject", "question", "name"), [
    ("Maya's birthday is March 14.", "Maya's birthday", "When is Maya's birthday?", "Nia"),
    ("The sailboat locker code is QRS-6725.", "the sailboat locker code", "What is the sailboat locker code?", "Tomas"),
])
def test_erased_memory_and_intact_name_are_answered_by_their_own_authorities(fact, subject, question, name):
    from tests.operator_profile_rig import set_owner_preferred_name

    set_owner_preferred_name(name)
    _remember_and_forget(fact, subject)
    message = f"{question} What is my name?"
    answer = _recall(message)
    assert answer is not None
    assert "no record" in answer["response"].lower()
    assert name in answer["response"]
    assert answer["_memory_recall_clauses"] == (question, "What is my name?")


def test_api_erased_and_intact_answers_leave_generic_sibling_unanswered():
    from core.agent_runtime.answer_coverage import demand_units, turn_slices
    from core.web.api.runtime import run_agent
    from tests.operator_profile_rig import set_owner_preferred_name

    set_owner_preferred_name("Leena")
    _remember_and_forget("The harbor access code is ZX-8291.", "the harbor access code")
    message = "What is the harbor access code? What is my name? Also explain why tides rise."
    captured = []

    class AgentBoundaryReached(RuntimeError):
        pass

    class RecordAgentBoundary:
        def run_once(self, user_text, *, session_id_override, source_context):
            captured.append(source_context)
            raise AgentBoundaryReached

    with pytest.raises(AgentBoundaryReached):
        run_agent(
            RuntimeServices(agent=RecordAgentBoundary(), runtime_home=str(active_vool_home())),
            message, session_id="erased-chat", source_context=_context(),
            workspace_root_provider=lambda: str(active_vool_home() / "workspace"),
        )
    assert len(captured) == 1
    answer = captured[0]["turn_answer_coverage"]["answers"][0]
    owned_slices = [item.slice_id for item in turn_slices(message)][:2]
    assert answer["consumed"] == owned_slices
    assert answer["consumed_units"] == [unit.unit_id for unit in demand_units(message) if unit.slice_id in owned_slices]
    assert len(answer["consumed_units"]) == 2
    assert "no record" in answer["response"].lower()
    assert "Leena" in answer["response"]


@pytest.mark.parametrize("question", ["What is my answer style?", "What is my project codename?"])
def test_known_name_does_not_answer_an_unavailable_private_field(question):
    from tests.operator_profile_rig import set_owner_preferred_name

    ensure_chat_namespace("erased-chat", grant_confirmed_profile=True)
    set_owner_preferred_name("Ari")
    assert "Ari" in _recall("What is my name?")["response"]
    assert _recall(question) is None


@pytest.mark.parametrize("question", [
    "What do you remember about my answer style?", "What do you remember about my project codename?",
    "What do you remember about Maya's birthday?", "What do you remember about photosynthesis?",
    "What do you remember about my answer style file?",
    "Where is my profile?",
])
def test_memory_wording_does_not_grant_whole_user_profile_authority(question):
    from tests.operator_profile_rig import set_owner_preferred_name

    ensure_chat_namespace("erased-chat", grant_confirmed_profile=True)
    set_owner_preferred_name("Ari")
    assert "Ari" in _recall("What is my name?")["response"]
    assert _recall(question) is None


def test_explicit_whole_profile_and_available_field_recall_keep_their_authority():
    from tests.operator_profile_rig import set_owner_preferred_name

    ensure_chat_namespace("erased-chat", grant_confirmed_profile=True)
    set_owner_preferred_name("Ren")
    with VoolMemory(runtime_home=str(active_vool_home()), agent_id=SEMANTIC_MEMORY_AGENT_ID) as memory:
        memory.block_write("preferences", "Answer style: short paragraphs")
    broad = _recall("What do you remember about me?")
    assert "Ren" in broad["response"] and "short paragraphs" in broad["response"]
    field = _recall("What do you remember about my answer style?")
    assert "short paragraphs" in field["response"]
    assert "Ren" not in field["response"]


def test_local_provider_manifest_binds_selected_environment_and_refreshes_endpoint(monkeypatch):
    from core.model_registry import ModelRegistry
    from core.runtime_provider_defaults import _ensure_local_ollama_provider

    monkeypatch.setenv("VOOL_RAW_OLLAMA_API_URL", "http://127.0.0.1:9")
    registry = ModelRegistry()
    env = {"VOOL_RAW_OLLAMA_API_URL": "http://127.0.0.1:19001", "VOOL_CONTEXT_BUCKET": "A"}
    assert _ensure_local_ollama_provider(registry, model_tag="qwen3:8b", bundle_role="general", env=env)
    assert registry.get_manifest("ollama-local", "qwen3:8b").runtime_config["base_url"] == env["VOOL_RAW_OLLAMA_API_URL"]
    assert not _ensure_local_ollama_provider(registry, model_tag="qwen3:8b", bundle_role="general", env=env)
    changed = {**env, "VOOL_RAW_OLLAMA_API_URL": "http://127.0.0.1:19002"}
    assert _ensure_local_ollama_provider(registry, model_tag="qwen3:8b", bundle_role="general", env=changed)
    assert registry.get_manifest("ollama-local", "qwen3:8b").runtime_config["base_url"] == changed["VOOL_RAW_OLLAMA_API_URL"]


def test_fresh_boot_auth_refuses_discovery_before_any_ssh_or_key_lookup(monkeypatch):
    from core import policy_engine
    from core.web.api.runtime import _bootstrap_public_hive_auth

    policy_engine.load(force_reload=True)
    assert policy_engine.get("network.outbound_enabled") is False
    calls = []

    def unexpected_discovery(**kwargs):
        calls.append(kwargs)
        raise AssertionError("fresh boot must not discover SSH auth")

    monkeypatch.setattr("core.web.api.runtime.ensure_public_hive_auth", unexpected_discovery)
    target = active_vool_home() / "config" / "agent-bootstrap.json"
    result = _bootstrap_public_hive_auth(project_root=Path.cwd(), target_path=target)
    assert result == {"ok": False, "status": "disabled_by_policy"}
    assert calls == []
    assert not target.exists()


def test_saved_outbound_consent_preserves_boot_auth_and_local_only_still_refuses(monkeypatch):
    from core import policy_engine
    from core.web.api.runtime import _bootstrap_public_hive_auth

    policy_engine.set_operator_policy_values({"network.outbound_enabled": True, "system.local_only_mode": False})
    calls = []

    def authorized_discovery(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "status": "existing_auth"}

    monkeypatch.setattr("core.web.api.runtime.ensure_public_hive_auth", authorized_discovery)
    target = active_vool_home() / "config" / "agent-bootstrap.json"
    assert _bootstrap_public_hive_auth(project_root=Path.cwd(), target_path=target) == {
        "ok": True, "status": "existing_auth",
    }
    assert calls == [{"project_root": Path.cwd(), "target_path": target}]
    policy_engine.set_operator_policy_values({"system.local_only_mode": True})
    assert _bootstrap_public_hive_auth(project_root=Path.cwd(), target_path=target) == {
        "ok": False, "status": "disabled_by_policy",
    }
    assert len(calls) == 1


@pytest.mark.parametrize("consent", [None, 1, "true", "false", {}, []])
def test_malformed_saved_outbound_values_cannot_start_auth_discovery(consent, monkeypatch):
    import yaml

    from core import policy_engine
    from core.web.api.runtime import _bootstrap_public_hive_auth

    policy_path = active_vool_home() / "config" / "default_policy.yaml"
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    policy_path.write_text(yaml.safe_dump({"network": {"outbound_enabled": consent}}), encoding="utf-8")
    policy_engine.load(force_reload=True)

    def unexpected_discovery(**kwargs):
        raise AssertionError("malformed saved consent must not discover SSH auth")

    monkeypatch.setattr("core.web.api.runtime.ensure_public_hive_auth", unexpected_discovery)
    assert _bootstrap_public_hive_auth(
        project_root=Path.cwd(), target_path=active_vool_home() / "config" / "agent-bootstrap.json"
    ) == {"ok": False, "status": "disabled_by_policy"}


@pytest.mark.parametrize("installed", [False, True])
def test_fresh_boot_model_preparation_creates_no_download_worker(installed, monkeypatch):
    from core import policy_engine
    from core.web.api.runtime import start_ollama_model_pull

    policy_engine.load(force_reload=True)
    assert policy_engine.get("network.outbound_enabled") is False
    monkeypatch.setattr("core.web.api.runtime.ollama_model_installed", lambda model, **kwargs: installed)

    def unexpected_worker(*args, **kwargs):
        raise AssertionError("fresh boot must not start a model download worker")

    monkeypatch.setattr("core.web.api.runtime.threading.Thread", unexpected_worker)
    progress = start_ollama_model_pull("isolated-download-boundary:1b")
    assert progress.wait(timeout=0)
    assert progress.status == ("ready" if installed else "skipped")
    if installed:
        assert progress.public["percent"] == 100
    else:
        assert "outbound" in progress.public["detail"].lower()


@pytest.mark.parametrize("consent", [None, 1, "true", "false", {}, []])
def test_malformed_outbound_consent_cannot_start_automatic_model_download(consent, monkeypatch):
    import yaml

    from core import policy_engine
    from core.web.api.runtime import start_ollama_model_pull

    policy_path = active_vool_home() / "config" / "default_policy.yaml"
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    policy_path.write_text(yaml.safe_dump({"network": {"outbound_enabled": consent}}), encoding="utf-8")
    policy_engine.load(force_reload=True)
    monkeypatch.setattr("core.web.api.runtime.ollama_model_installed", lambda model, **kwargs: False)

    def unexpected_worker(*args, **kwargs):
        raise AssertionError("malformed saved consent must not start a download worker")

    monkeypatch.setattr("core.web.api.runtime.threading.Thread", unexpected_worker)
    progress = start_ollama_model_pull("isolated-download-boundary:1b")
    assert progress.wait(timeout=0)
    assert progress.status == "skipped"


def test_local_only_vetoes_automatic_model_download_despite_saved_consent(monkeypatch):
    from core import policy_engine
    from core.web.api.runtime import start_ollama_model_pull

    policy_engine.set_operator_policy_values({"network.outbound_enabled": True, "system.local_only_mode": True})
    monkeypatch.setattr("core.web.api.runtime.ollama_model_installed", lambda model, **kwargs: False)

    def unexpected_worker(*args, **kwargs):
        raise AssertionError("Local Only must not start a model download worker")

    monkeypatch.setattr("core.web.api.runtime.threading.Thread", unexpected_worker)
    progress = start_ollama_model_pull("isolated-download-boundary:1b")
    assert progress.wait(timeout=0)
    assert progress.status == "skipped"


@pytest.mark.parametrize("endpoint", [
    "http://models.example:11434", "http://192.168.50.8:11434", "https://127.0.0.1.example:443",
    "http://[::2]:11434", "http://[malformed",
])
def test_refused_remote_ollama_endpoint_is_never_queried_by_automatic_startup(endpoint, monkeypatch):
    from types import SimpleNamespace

    from core import policy_engine
    from core.web.api import runtime

    policy_engine.load(force_reload=True)
    assert policy_engine.get("network.outbound_enabled") is False
    monkeypatch.setenv("VOOL_RAW_OLLAMA_API_URL", endpoint)
    monkeypatch.setenv("VOOL_SKIP_PROVIDER_PREWARM", "0")
    calls = []

    def inventory(*args, **kwargs):
        calls.append("inventory")
        return ()

    def installed(*args, **kwargs):
        calls.append("installed")
        return False

    def snapshot(*args, **kwargs):
        calls.append("snapshot")
        return SimpleNamespace(prewarm_results=(), capability_truth=())

    monkeypatch.setattr(runtime, "ollama_model_installed", installed)
    monkeypatch.setattr(runtime, "installed_ollama_model_inventory", inventory)
    monkeypatch.setattr(runtime, "build_provider_registry_snapshot", snapshot)
    progress = runtime.start_ollama_model_pull("isolated-download-boundary:1b")
    assert progress.wait(timeout=0) and progress.status == "skipped"
    assert runtime.candidate_aware_daily_runtime_model_tag("configured:1b", env={}) == "configured:1b"
    runtime.log_prewarm_results(object(), model_tag="configured:1b")
    assert runtime.startup_provider_capability_truth(object(), model_tag="configured:1b", env={}) == ()
    assert calls == []


@pytest.mark.parametrize("endpoint", ["http://127.0.0.1:11434", "http://localhost:11434", "http://[::1]:11434"])
def test_automatic_local_inventory_remains_available_without_outbound_consent(endpoint):
    from core import policy_engine
    from core.web.api.runtime import _automatic_ollama_access_allowed, _automatic_provider_env

    policy_engine.load(force_reload=True)
    assert policy_engine.get("network.outbound_enabled") is False
    env = {"VOOL_RAW_OLLAMA_API_URL": endpoint, "VOOL_REGISTER_INSTALLED_OLLAMA_MODELS": "1"}
    assert _automatic_ollama_access_allowed(env)
    assert _automatic_provider_env(env) is env


def test_saved_remote_startup_consent_is_preserved_and_local_only_still_vetoes():
    from core import policy_engine
    from core.web.api.runtime import _automatic_ollama_access_allowed, _automatic_provider_env

    env = {"VOOL_RAW_OLLAMA_API_URL": "http://models.example:11434", "VOOL_REGISTER_INSTALLED_OLLAMA_MODELS": "1"}
    policy_engine.set_operator_policy_values({"network.outbound_enabled": True, "system.local_only_mode": False})
    assert _automatic_ollama_access_allowed(env)
    assert _automatic_provider_env(env) is env
    policy_engine.set_operator_policy_values({"system.local_only_mode": True})
    assert not _automatic_ollama_access_allowed(env)
    assert _automatic_provider_env(env)["VOOL_REGISTER_INSTALLED_OLLAMA_MODELS"] == "0"


def test_automatic_provider_registration_retains_metadata_without_remote_discovery(monkeypatch):
    from core import policy_engine
    from core.model_registry import ModelRegistry
    from core.web.api.runtime import ensure_default_provider, ollama_model_installed

    policy_engine.load(force_reload=True)
    assert policy_engine.get("network.outbound_enabled") is False
    env = {
        "VOOL_RAW_OLLAMA_API_URL": "http://models.example:11434", "VOOL_REGISTER_INSTALLED_OLLAMA_MODELS": "1",
        "VOOL_ALLOW_OLLAMA_TAGS_FOR_DEFAULT": "1", "VOOL_LOCAL_MODELS_ENABLED": "1",
    }
    # The existing manifest factory resolves its endpoint from the process.
    # Pin that genuine source as well as the curated startup input.
    monkeypatch.setenv("VOOL_RAW_OLLAMA_API_URL", env["VOOL_RAW_OLLAMA_API_URL"])
    calls = []

    def inventory(**kwargs):
        calls.append(kwargs)
        return ("qwen3:8b",)

    monkeypatch.setattr("core.runtime_provider_defaults.installed_ollama_model_names", inventory)
    monkeypatch.setattr("core.web.api.runtime.installed_ollama_model_names", inventory)
    registry = ModelRegistry()
    ensure_default_provider(registry, "qwen3:8b", env=env, install_profile="local-only")
    manifest = registry.get_manifest("ollama-local", "qwen3:8b")
    assert manifest is not None
    assert manifest.runtime_config["base_url"].startswith("http://models.example:11434")
    assert calls == []
    # Explicit inventory keeps its existing seam; the automatic gate does not
    # impersonate the model's absence or replace its configured endpoint.
    assert ollama_model_installed("qwen3:8b", env=env)
    assert len(calls) == 1
