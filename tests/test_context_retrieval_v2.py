from __future__ import annotations

import uuid

import pytest

import core.context_retrieval as cr
from core.active_mission import capture_active_mission_slots
from core.context_namespace import ensure_chat_namespace


class _Node:
    def __init__(self, content: str, *, tags: list[str] | None = None, context_description: str = ""):
        self.content = content
        self.tags = list(tags or [])
        self.context_description = context_description


class _FakeMem:
    def __init__(self, hits):
        self._hits = hits
        self.calls: list[str] = []

    def node_search(self, q_vec, *, top_k, min_score, session_id):
        self.calls.append("semantic")
        return self._hits

    def node_search_hybrid(
        self,
        query_text,
        q_vec,
        *,
        top_k,
        min_score,
        session_id,
    ):
        self.calls.append("hybrid")
        return self._hits

    def close(self):
        pass


class _FakeMemNoHybrid:
    """An older memory backend that predates node_search_hybrid — getattr returns None."""
    def __init__(self, hits):
        self._hits = hits
        self.calls: list[str] = []

    def node_search(self, q_vec, *, top_k, min_score, session_id):
        self.calls.append("semantic")
        return self._hits

    def close(self):
        pass


class _FakeMemTopK:
    def __init__(self, hits):
        self._hits = hits
        self.calls: list[tuple[str, int]] = []

    def node_search(self, q_vec, *, top_k, min_score, session_id):
        self.calls.append(("semantic", top_k))
        return self._hits[:top_k]

    def node_search_hybrid(
        self,
        query_text,
        q_vec,
        *,
        top_k,
        min_score,
        session_id,
    ):
        self.calls.append(("hybrid", top_k))
        return self._hits[:top_k]

    def close(self):
        pass


def _wire(monkeypatch, mem):
    monkeypatch.setattr(cr, "_open_memory", lambda: mem)
    monkeypatch.setattr(cr, "embed", lambda _text: [0.1, 0.2, 0.3])


@pytest.fixture(autouse=True)
def _active_default_semantic_namespace(runtime_storage_reset) -> None:
    del runtime_storage_reset
    ensure_chat_namespace("s", grant_current_receipts=False)


def _activate(session_id: str, *, project_id: str = "") -> None:
    ensure_chat_namespace(
        session_id,
        project_id=project_id,
        grant_current_receipts=False,
    )


def _transcript():
    return [{"role": "user", "content": "what port does the gateway use?"}]


def _hits(*contents, session_id="s"):
    _activate(session_id)
    scope = cr._session_scope_key(session_id)
    return [
        (
            _Node(
                content,
                tags=["user", f"session:{scope}"],
                context_description=f"session={scope} role=user",
            ),
            0.9 - index * 0.05,
        )
        for index, content in enumerate(contents)
    ]


def _retrieved_block(result):
    return next((m["content"] for m in result if m.get("role") == "system"
                 and "<retrieved_context>" in m.get("content", "")), "")


# --- the gate ---------------------------------------------------------------------------------
def test_flag_off_uses_legacy_semantic_search(monkeypatch) -> None:
    mem = _FakeMem(_hits("The OpenClaw gateway listens on port 18789."))
    _wire(monkeypatch, mem)
    out = cr.inject_retrieved("s", "gateway port", _transcript(), env={})
    assert mem.calls == ["semantic"]                     # plain node_search, no hybrid
    assert "18789" in _retrieved_block(out)


def test_flag_on_uses_hybrid_search(monkeypatch) -> None:
    mem = _FakeMem(_hits("The OpenClaw gateway listens on port 18789."))
    _wire(monkeypatch, mem)
    out = cr.inject_retrieved("s", "gateway port", _transcript(), env={"VOOL_CONTEXT_CAPSULE_V2": "1"})
    assert mem.calls == ["hybrid"]                       # THE booster edge: BM25+semantic+recency
    assert "18789" in _retrieved_block(out)


def test_flag_on_falls_back_to_semantic_when_no_hybrid(monkeypatch) -> None:
    mem = _FakeMemNoHybrid(_hits("port 18789 fact"))
    _wire(monkeypatch, mem)
    out = cr.inject_retrieved("s", "port", _transcript(), env={"VOOL_CONTEXT_CAPSULE_V2": "1"})
    assert mem.calls == ["semantic"]                     # getattr(None) -> graceful fallback
    assert "18789" in _retrieved_block(out)              # still injects


# --- v2 improvements --------------------------------------------------------------------------
def test_v2_redacts_secrets_on_the_injection_boundary(monkeypatch) -> None:
    secret = "sk-abcdef0123456789ABCDEF"
    mem = _FakeMem(_hits(f"the deploy key is {secret} keep safe"))
    _wire(monkeypatch, mem)
    out = cr.inject_retrieved("s", "deploy key", _transcript(), env={"VOOL_CONTEXT_CAPSULE_V2": "1"})
    block = _retrieved_block(out)
    assert block                                         # something was injected
    assert secret not in block                           # ...but the raw secret was redacted


def test_legacy_does_not_redact_but_v2_does(monkeypatch) -> None:
    # Both retrieval paths enforce redaction at the provider-context boundary.
    secret = "sk-ABCDEF0123456789zzzz"
    mem = _FakeMem(_hits(f"token {secret} end"))
    _wire(monkeypatch, mem)
    legacy = _retrieved_block(cr.inject_retrieved("s", "token", _transcript(), env={}))
    v2 = _retrieved_block(cr.inject_retrieved("s", "token", _transcript(), env={"VOOL_CONTEXT_CAPSULE_V2": "1"}))
    assert secret not in legacy
    assert secret not in v2          # new path redacts


def test_v2_distills_instead_of_score_prefixed_retrieval_stuffing(monkeypatch) -> None:
    many = _hits(*[f"distinct durable fact number {i} about topic {i}" for i in range(8)])
    _wire(monkeypatch, _FakeMem(many))
    v2 = cr.inject_retrieved("s", "facts", _transcript(), env={"VOOL_CONTEXT_CAPSULE_V2": "1"})
    assert cr.get_last_retrieval_telemetry()["capsule_mode"] == "distilled"
    _wire(monkeypatch, _FakeMem(many))
    legacy = cr.inject_retrieved("s", "facts", _transcript(), env={})
    v2_items = _retrieved_block(v2).count("[score=")
    legacy_items = _retrieved_block(legacy).count("[score=")
    assert legacy_items <= 4                               # legacy cap
    assert v2_items == 0
    assert "retrieved fact:" in _retrieved_block(v2)


def test_disabled_path_resets_capsule_telemetry(monkeypatch) -> None:
    _wire(monkeypatch, _FakeMem(_hits("Buried audit code RAVEN-4821.")))
    cr.inject_retrieved("s", "audit code", _transcript(), env={"VOOL_CONTEXT_CAPSULE_V2": "1"})
    assert cr.get_last_retrieval_telemetry()["capsule_mode"] == "distilled"

    _wire(monkeypatch, _FakeMem(_hits("legacy fact 18789")))
    cr.inject_retrieved("s", "legacy fact", _transcript(), env={})
    assert cr.get_last_retrieval_telemetry()["capsule_mode"] == "disabled"


def test_v2_distills_long_context_and_preserves_exact_values(monkeypatch) -> None:
    noise = " ".join(f"noise token {i}" for i in range(2500))
    long_doc = (
        f"{noise}\n"
        "Old cap was 0.037 SOL. Latest correction: cap is now 0.05 SOL.\n"
        "Deployment region was eu-north-1. Corrected region is eu-west-3.\n"
        "Active mission: cap 0.037 SOL, domain alice.null, wallet prefix F6Fr2, Windows only, never auto-spend.\n"
        "Buried audit code RAVEN-4821. Operator stored memory value 8829145.\n"
        "The document does not define any launch gate color.\n"
        f"{noise}"
    )
    scope = cr._session_scope_key("s")
    node = _Node(long_doc, tags=["user", f"session:{scope}"], context_description=f"session={scope} role=user")
    _wire(monkeypatch, _FakeMem([(node, 0.9)]))
    out = cr.inject_retrieved(
        "s",
        "What is the latest cap, corrected region, mission, audit code, and stored memory?",
        _transcript(),
        env={"VOOL_CONTEXT_CAPSULE_V2": "1"},
    )
    block = _retrieved_block(out)
    telemetry = cr.get_last_retrieval_telemetry()
    assert "RAVEN-4821" in block
    assert "0.05 SOL" in block
    assert "eu-west-3" in block
    assert "alice.null" in block
    assert "F6Fr2" in block
    assert "Windows" in block
    assert "never auto-spend" in block
    assert "recalled identifier: 8829145" in block
    assert len(block) < 2200
    assert int(telemetry["estimated_distilled_tokens"]) < 500
    assert int(telemetry["raw_context_chars"]) > int(telemetry["distilled_chars"])
    assert int(telemetry["selected_facts_count"]) == len(telemetry["selected_facts"])
    assert telemetry["prompt_eval_count"] == 0
    assert telemetry["model_calls"] == 0
    assert telemetry["web_calls"] == 0


def test_capsule_telemetry_can_be_completed_after_model_execution() -> None:
    cr.reset_retrieval_telemetry()
    cr.update_retrieval_telemetry(
        capsule_mode="distilled",
        selected_facts=["- audit code: AUDIT-7319"],
        prompt_eval_count=412,
        model_prompt_tokens=412,
        model_calls=1,
        web_calls=0,
    )

    telemetry = cr.get_last_retrieval_telemetry()
    assert telemetry["capsule_mode"] == "distilled"
    assert telemetry["selected_facts_count"] == 1
    assert telemetry["prompt_eval_count"] == 412
    assert telemetry["model_prompt_tokens"] == 412
    assert telemetry["model_calls"] == 1
    assert telemetry["web_calls"] == 0


def test_capsule_exact_response_renders_typed_facts_without_value_hardcoding() -> None:
    session_id = "exact-recall-session"
    variants = (
        ("What is the audit code?", ["- exact code: AUDIT-7319"], "AUDIT-7319"),
        ("What is the secret code?", ["- exact code: SABLE-2048"], "SABLE-2048"),
        ("What is the latest launch cap?", ["- latest spend cap: 0.003 SOL"], "0.003 SOL"),
        ("Which deployment region is correct?", ["- current region: ap-south-1"], "ap-south-1"),
        ("What operator node ID is stored?", ["- recalled identifier: 441902"], "441902"),
        (
            "Summarize the active mission.",
            ["- active mission: cap 0.071 SOL; domain parad0x.null; wallet prefix 9xQpA; target OS Windows; rule never auto-spend"],
            "Current mission: cap 0.071 SOL; domain parad0x.null; wallet prefix 9xQpA; target OS Windows; rule never auto-spend.",
        ),
        (
            "What is the launch gate color?",
            ["- answer launch gate color: not present in the supplied context; do not invent a color"],
            "The launch gate color is not present in the supplied context.",
        ),
    )
    for query, selected_facts, expected in variants:
        assert cr.capsule_exact_response(
            query,
            {
                "capsule_mode": "distilled",
                "selected_facts": selected_facts,
                "source_session_ids": [cr._session_scope_key(session_id)],
            },
            session_id=session_id,
        ) == expected


def test_capsule_exact_response_does_not_override_unrelated_requests() -> None:
    session_id = "unrelated-request-session"
    telemetry = {
        "capsule_mode": "distilled",
        "selected_facts": ["- latest spend cap: 0.05 SOL"],
        "source_session_ids": [cr._session_scope_key(session_id)],
    }
    assert cr.capsule_exact_response(
        "Write a Python add_tax function.",
        telemetry,
        session_id=session_id,
    ) == ""


def test_capsule_exact_recall_query_gate_excludes_advisory_requests() -> None:
    assert cr.is_capsule_exact_recall_query("What operator node ID should I remember?")
    assert cr.is_capsule_exact_recall_query("What is the exact audit code?")
    assert not cr.is_capsule_exact_recall_query("Which operator node should I choose?")
    assert not cr.is_capsule_exact_recall_query("How much SOL should I budget?")


def test_cross_session_capsule_cap_cannot_override_current_session_mission() -> None:
    suffix = uuid.uuid4().hex
    session_a = f"A-{suffix}"
    session_b = f"B-{suffix}"
    capture_active_mission_slots(
        session_a,
        "Active mission: cap 0.05 SOL, domain parad0x.null.",
    )
    capture_active_mission_slots(
        session_b,
        "Active mission: cap 0.037 SOL, domain alice.null.",
    )
    telemetry = {
        "capsule_mode": "distilled",
        "selected_facts": [
            "- latest spend cap: 0.05 SOL",
            "- active mission: cap 0.05 SOL; domain parad0x.null",
        ],
        "source_session_ids": [cr._session_scope_key(session_a)],
    }

    response = cr.capsule_exact_response(
        "what is my spend cap?",
        telemetry,
        session_id=session_b,
    )

    assert "0.037 SOL" in response
    assert "0.05 SOL" not in response


def test_capsule_exact_response_requires_same_session_provenance() -> None:
    source_session = "source-session-111"
    target_session = "target-session-222"
    telemetry = {
        "capsule_mode": "distilled",
        "selected_facts": ["- exact code: RCPT-9182"],
        "source_session_ids": [cr._session_scope_key(source_session)],
    }

    assert cr.capsule_exact_response(
        "what is the exact receipt code?",
        telemetry,
        session_id=target_session,
    ) == ""
    assert cr.capsule_exact_response(
        "what is the exact receipt code?",
        telemetry,
        session_id=source_session,
    ) == "RCPT-9182"


def test_operator_node_id_exact_recall_requires_current_session() -> None:
    current_session = "operator-current-session"
    other_session = "operator-other-session"
    current = {
        "capsule_mode": "distilled",
        "selected_facts": ["- recalled identifier: 8829145"],
        "source_session_ids": [cr._session_scope_key(current_session)],
    }
    other = {
        "capsule_mode": "distilled",
        "selected_facts": ["- recalled identifier: 1111111"],
        "source_session_ids": [cr._session_scope_key(other_session)],
    }

    assert cr.capsule_exact_response(
        "What operator node ID should I remember?",
        current,
        session_id=current_session,
    ) == "8829145"
    assert cr.capsule_exact_response(
        "What operator node ID should I remember?",
        other,
        session_id=current_session,
    ) == ""


def test_operator_node_id_same_legacy_prefix_does_not_authorize_override() -> None:
    shared_prefix = "operator-shared-pref"
    source_session = f"{shared_prefix}-source"
    current_session = f"{shared_prefix}-current"
    assert source_session[:20] == current_session[:20]
    telemetry = {
        "capsule_mode": "distilled",
        "selected_facts": ["- recalled identifier: 1111111"],
        "source_session_ids": [cr._session_scope_key(source_session)],
    }

    assert cr.capsule_exact_response(
        "What operator node ID should I remember?",
        telemetry,
        session_id=current_session,
    ) == ""


def test_operator_node_advisory_prompt_does_not_use_exact_override() -> None:
    session_id = "operator-advisory-session"
    telemetry = {
        "capsule_mode": "distilled",
        "selected_facts": ["- recalled identifier: 8829145"],
        "source_session_ids": [cr._session_scope_key(session_id)],
    }

    assert cr.capsule_exact_response(
        "Which operator node should I choose?",
        telemetry,
        session_id=session_id,
    ) == ""


def test_capsule_exact_response_rejects_sessions_with_same_legacy_prefix() -> None:
    common_prefix = "shared-session-prefix"
    source_session = f"{common_prefix}-source"
    target_session = f"{common_prefix}-target"
    assert source_session[:20] == target_session[:20]
    telemetry = {
        "capsule_mode": "distilled",
        "selected_facts": ["- exact code: RCPT-7712"],
        "source_session_ids": [cr._session_scope_key(source_session)],
    }

    assert cr.capsule_exact_response(
        "what is the exact receipt code?",
        telemetry,
        session_id=target_session,
    ) == ""


def test_advisory_budget_prompt_never_uses_exact_override() -> None:
    session_id = "budget-advice-session"
    telemetry = {
        "capsule_mode": "distilled",
        "selected_facts": ["- latest spend cap: 2 SOL"],
        "source_session_ids": [cr._session_scope_key(session_id)],
    }

    for prompt in (
        "how much SOL should I budget?",
        "what cap should I choose?",
        "should I spend more?",
        "help me plan budget",
    ):
        assert cr.capsule_exact_response(prompt, telemetry, session_id=session_id) == ""


def test_capsule_override_rejects_current_mission_forbidden_term() -> None:
    session_id = f"forbidden-{uuid.uuid4().hex}"
    capture_active_mission_slots(
        session_id,
        "Active mission: cap 0.037 SOL, domain alice.null, do not mention Web3.",
    )
    telemetry = {
        "capsule_mode": "distilled",
        "selected_facts": ["- exact code: Web3"],
        "source_session_ids": [cr._session_scope_key(session_id)],
    }

    assert cr.capsule_exact_response(
        "what is the exact audit code?",
        telemetry,
        session_id=session_id,
    ) == ""


def test_capsule_final_validator_rejects_remote_and_conflicting_claims() -> None:
    session_id = f"validator-{uuid.uuid4().hex}"
    capture_active_mission_slots(
        session_id,
        "Active mission: cap 0.037 SOL, domain alice.null.",
    )

    assert not cr.validate_capsule_exact_response(
        "According to https://example.com, 0.037 SOL",
        "what is my spend cap?",
        session_id=session_id,
    )
    assert not cr.validate_capsule_exact_response(
        "0.05 SOL",
        "what is my spend cap?",
        session_id=session_id,
    )
    assert cr.validate_capsule_exact_response(
        "0.037 SOL",
        "what is my spend cap?",
        session_id=session_id,
    )


def test_capsule_telemetry_records_memory_source_session(monkeypatch) -> None:
    session_id = "session-source-123"
    _activate(session_id)
    source_scope = cr._session_scope_key(session_id)
    node = _Node(
        "The exact receipt code is RCPT-8831.",
        tags=["user", f"session:{source_scope}"],
        context_description=f"session={source_scope} role=user",
    )
    _wire(monkeypatch, _FakeMem([(node, 0.93)]))

    cr.inject_retrieved(
        session_id,
        "What is the exact receipt code?",
        _transcript(),
        env={"VOOL_CONTEXT_CAPSULE_V2": "1"},
    )

    telemetry = cr.get_last_retrieval_telemetry()
    assert telemetry["source_session_ids"] == [source_scope]
    assert telemetry["current_session_scope"] == cr._session_scope_key(
        session_id
    )


def test_exact_recall_filters_foreign_session_hits_before_distillation(monkeypatch) -> None:
    session_a = "audit-session-a"
    session_b = "audit-session-b"
    _activate(session_b)
    scope_a = cr._session_scope_key(session_a)
    scope_b = cr._session_scope_key(session_b)
    foreign = _Node(
        "The current audit code is RCPT-7319.",
        tags=["user", f"session:{scope_a}"],
        context_description=f"session={scope_a} role=user",
    )
    current = _Node(
        "The current audit code is SABLE-2048.",
        tags=["user", f"session:{scope_b}"],
        context_description=f"session={scope_b} role=user",
    )
    _wire(monkeypatch, _FakeMem([(foreign, 0.99), (current, 0.91)]))

    out = cr.inject_retrieved(
        session_b,
        "What is the exact audit code?",
        _transcript(),
        env={"VOOL_CONTEXT_CAPSULE_V2": "1"},
    )

    block = _retrieved_block(out)
    telemetry = cr.get_last_retrieval_telemetry()
    assert "SABLE-2048" in block
    assert "RCPT-7319" not in block
    assert telemetry["selected_facts"] == ["- exact code: SABLE-2048"]
    assert telemetry["source_session_ids"] == [scope_b]
    assert telemetry["dropped_foreign_session_count"] == 1


def test_exact_recall_drops_no_provenance_hits_before_model_prompt(monkeypatch) -> None:
    session_id = "audit-no-provenance"
    _activate(session_id)
    _wire(
        monkeypatch,
        _FakeMem([(_Node("The current audit code is RCPT-7319."), 0.9)]),
    )

    out = cr.inject_retrieved(
        session_id,
        "What is the exact audit code?",
        _transcript(),
        env={"VOOL_CONTEXT_CAPSULE_V2": "1"},
    )

    telemetry = cr.get_last_retrieval_telemetry()
    assert _retrieved_block(out) == ""
    assert telemetry["selected_facts"] == []
    assert telemetry["source_session_ids"] == []
    assert telemetry["dropped_no_provenance_count"] == 1


def test_exact_recall_drops_mixed_provenance_hits_before_model_prompt(monkeypatch) -> None:
    session_a = "stored-id-session-a"
    session_b = "stored-id-session-b"
    _activate(session_b)
    scope_a = cr._session_scope_key(session_a)
    scope_b = cr._session_scope_key(session_b)
    mixed = _Node(
        "The stored ID is 8829145.",
        tags=["user", f"session:{scope_a}", f"session:{scope_b}"],
        context_description=f"session={scope_a} role=user",
    )
    current = _Node(
        "The stored ID is 441902.",
        tags=["user", f"session:{scope_b}"],
        context_description=f"session={scope_b} role=user",
    )
    _wire(monkeypatch, _FakeMem([(mixed, 0.99), (current, 0.91)]))

    out = cr.inject_retrieved(
        session_b,
        "What stored ID should I remember?",
        _transcript(),
        env={"VOOL_CONTEXT_CAPSULE_V2": "1"},
    )

    block = _retrieved_block(out)
    telemetry = cr.get_last_retrieval_telemetry()
    assert "441902" in block
    assert "8829145" not in block
    assert telemetry["source_session_ids"] == [scope_b]
    assert telemetry["dropped_foreign_session_count"] == 1


def test_exact_recall_same_prefix_session_hit_is_not_packed(monkeypatch) -> None:
    source_session = "same-prefix-session-source"
    current_session = "same-prefix-session-current"
    _activate(current_session)
    assert source_session[:14] == current_session[:14]
    scope = cr._session_scope_key(source_session)
    node = _Node(
        "The current audit code is RCPT-7319.",
        tags=["user", f"session:{scope}"],
        context_description=f"session={scope} role=user",
    )
    _wire(monkeypatch, _FakeMem([(node, 0.99)]))

    out = cr.inject_retrieved(
        current_session,
        "What is the exact audit code?",
        _transcript(),
        env={"VOOL_CONTEXT_CAPSULE_V2": "1"},
    )

    telemetry = cr.get_last_retrieval_telemetry()
    assert _retrieved_block(out) == ""
    assert telemetry["selected_facts"] == []
    assert telemetry["source_session_ids"] == []
    assert telemetry["dropped_foreign_session_count"] == 1


def test_exact_recall_fetches_beyond_foreign_hits_to_find_current_session(monkeypatch) -> None:
    current_session = "deep-current-session"
    _activate(current_session)
    current_scope = cr._session_scope_key(current_session)
    hits = []
    for idx in range(20):
        foreign_scope = cr._session_scope_key(f"deep-foreign-{idx}")
        hits.append(
            (
                _Node(
                    f"The stored ID is {880000 + idx}.",
                    tags=["user", f"session:{foreign_scope}"],
                    context_description=f"session={foreign_scope} role=user",
                ),
                0.99 - idx * 0.01,
            )
        )
    hits.append(
        (
            _Node(
                "The stored ID is 441902.",
                tags=["user", f"session:{current_scope}"],
                context_description=f"session={current_scope} role=user",
            ),
            0.70,
        )
    )
    mem = _FakeMemTopK(hits)
    _wire(monkeypatch, mem)

    out = cr.inject_retrieved(
        current_session,
        "What stored ID should I remember?",
        _transcript(),
        env={"VOOL_CONTEXT_CAPSULE_V2": "1"},
    )

    telemetry = cr.get_last_retrieval_telemetry()
    assert mem.calls == [("hybrid", 64)]
    assert "441902" in _retrieved_block(out)
    assert telemetry["source_session_ids"] == [current_scope]
    assert telemetry["dropped_foreign_session_count"] == 20


def test_v2_distills_absent_values_as_do_not_invent(monkeypatch) -> None:
    noise = " ".join(f"noise token {i}" for i in range(800))
    long_doc = f"{noise}\nThis context does not define any launch gate color.\n{noise}"
    _wire(monkeypatch, _FakeMem(_hits(long_doc)))
    out = cr.inject_retrieved(
        "s",
        "What is the launch gate color?",
        _transcript(),
        env={"VOOL_CONTEXT_CAPSULE_V2": "1"},
    )
    block = _retrieved_block(out).lower()
    assert "launch gate color: not present" in block
    assert "do not invent" in block


def test_v2_distills_labelled_code_variants_without_value_hardcoding(monkeypatch) -> None:
    variants = (
        ("audit", "AUDIT-7319"),
        ("secret", "SABLE-2048"),
        ("operator", "OPERATOR-X91"),
        ("receipt", "RCPT-55301"),
    )
    for label, value in variants:
        noise = " ".join(f"{label}_noise_{i}" for i in range(600))
        document = f"{noise}\nThe current {label} code is {value}.\n{noise}"
        scope = cr._session_scope_key("s")
        node = _Node(document, tags=["user", f"session:{scope}"], context_description=f"session={scope} role=user")
        _wire(monkeypatch, _FakeMem([(node, 0.9)]))
        out = cr.inject_retrieved(
            "s",
            f"What is the {label} code? Return the exact code only.",
            _transcript(),
            env={"VOOL_CONTEXT_CAPSULE_V2": "1"},
        )
        block = _retrieved_block(out)
        assert value in block
        assert "exact code:" in block.lower()
        assert int(cr.get_last_retrieval_telemetry()["estimated_distilled_tokens"]) < 500


def test_v2_distills_labelled_wallet_index_as_identifier(monkeypatch) -> None:
    document = "Please remember: my Web0 launch wallet index is 8829145 and the cap is 0.037 SOL."
    _wire(
        monkeypatch,
        _FakeMem(_hits(document, session_id="wallet-index-session")),
    )

    out = cr.inject_retrieved(
        "wallet-index-session",
        "What is my Web0 launch wallet index?",
        _transcript(),
        env={"VOOL_CONTEXT_CAPSULE_V2": "1"},
    )

    block = _retrieved_block(out)
    assert "recalled identifier: 8829145" in block
    assert "wallet prefix index" not in block


# --- invariants preserved ---------------------------------------------------------------------
def test_v2_dedup_drops_content_already_in_transcript(monkeypatch) -> None:
    dup = "the gateway listens on port 18789 for the dashboard connection every time"
    tr = [{"role": "user", "content": dup}]
    _wire(monkeypatch, _FakeMem(_hits(dup, "a genuinely fresh unrelated fact about arweave")))
    out = cr.inject_retrieved("s", "port", tr, env={"VOOL_CONTEXT_CAPSULE_V2": "1"})
    block = _retrieved_block(out)
    assert "arweave" in block            # fresh fact kept
    assert block.count("18789") == 0     # the duplicate was dropped


def test_v2_inserts_block_before_last_user_turn(monkeypatch) -> None:
    tr = [{"role": "system", "content": "sys"},
          {"role": "assistant", "content": "hi"},
          {"role": "user", "content": "what port?"}]
    _wire(monkeypatch, _FakeMem(_hits("port 18789")))
    out = cr.inject_retrieved("s", "port", tr, env={"VOOL_CONTEXT_CAPSULE_V2": "1"})
    roles = [m["role"] for m in out]
    # the injected system block sits immediately before the final user turn
    assert out[-1]["role"] == "user"
    assert out[-2]["role"] == "system" and "<retrieved_context>" in out[-2]["content"]
    assert roles.count("user") == 1


def test_empty_query_returns_transcript_unchanged(monkeypatch) -> None:
    tr = _transcript()
    _wire(monkeypatch, _FakeMem(_hits("x")))
    assert cr.inject_retrieved("s", "  ", tr, env={"VOOL_CONTEXT_CAPSULE_V2": "1"}) is tr


# --- store-side importance: explicit "remember this" must be persisted --------------------------
def test_explicit_remember_requests_are_scored_high_enough_to_store() -> None:
    # Regression for the teammate's bug: "remember the number 8932…" scored 0.20 (< 0.35 threshold)
    # and was silently dropped, so it could never be recalled. It must now clear the store bar.
    T = cr._IMPORTANCE_THRESHOLD
    stored = [
        "hey can you just remember something for me? the number 8932173982718939281",
        "remember the number 8932173982718939281",
        "my wallet seed index is 8932173982718939281",
        "note that the staging password is hunter2",
        "don't forget my flight is at 6pm",
    ]
    for m in stored:
        assert cr._score_importance(m) >= T, m

    # ...without turning filler into stored noise (keeps the memory signal-dense).
    for m in ("lol ok thanks that is cool", "what do you think about the weather", "haha nice"):
        assert cr._score_importance(m) < T, m
