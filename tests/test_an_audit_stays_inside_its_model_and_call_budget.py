"""An audit runs on the model the operator chose, and costs what its own receipt says.

Two measured incidents on one turn. A single-file audit consumed **9 model calls / 28,741 input
tokens / 1,772 output tokens** and reported no finding; and every one of those calls ran on
`nvidia/nemotron-3-ultra-550b-a55b:free` without the product ever saying so.

Neither was a mystery once traced. The nomination prompt carries the whole numbered file excerpt,
every retry re-sent it in full, retries were budgeted per manifest and the driver then walked to the
next manifest and started over — 3 attempts x 4 manifests x 3 candidates, each one paying ~3,000
tokens for the same source. And the model was chosen by a ranking that had never been told what the
operator picked, then recorded nowhere.

So this pins two properties, on a fixture unrelated to the incident's:

* the mode decides the model, and every helper call inside the turn obeys the same mode;
* the calls are bounded, and each one carries what it was given and why it was necessary.

Lettered to the hostile-test plan: 9 manual, 10 local-only, 11 auto, 12 call count.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from core.agent_runtime.active_finding import clear_active_findings
from core.agent_runtime.audit_call_budget import (
    CONTEXT_CITED_WINDOW,
    CONTEXT_FULL_EXCERPT,
)
from core.agent_runtime.audit_routing import (
    AUTO,
    LOCAL_ONLY,
    MANUAL,
    manifest_is_cloud,
    resolve_routing_mode,
)
from core.agent_runtime.audit_session import clear_audit_capsules
from core.agent_runtime.stepped_audit import run_stepped_audit
from core.token_usage_receipt import audit_token_breakdown, audit_usage_sentence

# --------------------------------------------------------------------------------------
# A third subject again: a CSV column mapper. Nothing to do with codecs, ledgers or retries.
# --------------------------------------------------------------------------------------

_MAPPER_LINES = [
    "#!/usr/bin/env python3",
    '"""Map incoming CSV headers onto internal column names."""',
    "",
    "ALIASES = {",
    '    "e-mail": "email",',
    '    "mail": "email",',
    '    "phone number": "phone",',
    "}",
    "",
    "",
    "def normalise(header):",
    "    return header.strip().lower()",
    "",
    "",
    "def map_headers(headers):",
    "    mapped = {}",
    "    for index, header in enumerate(headers):",
    "        key = normalise(header)",
    "        mapped[ALIASES.get(key, key)] = index",
    "    return mapped",
    "",
    "",
    "def read_row(row, mapping, column):",
    "    return row[mapping[column]]",
]
# Padded to a realistic module length. The size is load-bearing for one property: a correction must
# carry a WINDOW rather than the file, and on a twenty-line file every window is the whole file.
for _n in range(30):
    _MAPPER_LINES += [
        "",
        "",
        f"def coerce_column_{_n}(value, default=None):",
        f'    """Coerce column {_n} into its declared type."""',
        "    if value is None:",
        "        return default",
        "    text = str(value).strip()",
        "    if not text:",
        "        return default",
        "    return text",
    ]
MAPPER = "\n".join(_MAPPER_LINES)
MAPPER_TARGET = "etl/csv/header_mapper.py"

_DUPLICATE_LINE = "        mapped[ALIASES.get(key, key)] = index"
DUPLICATE_LINE_NO = _MAPPER_LINES.index(_DUPLICATE_LINE) + 1

AUDIT_PROMPT = f"Audit {MAPPER_TARGET} and name the highest-risk real bug. Do not modify anything."


def _manifest(provider_id, model_name, *, local):
    """A manifest faithful enough for `provider_cost_class` to classify it the way it would live."""
    if local:
        return SimpleNamespace(
            provider_id=provider_id,
            provider_name=provider_id,
            model_name=model_name,
            adapter_type="ollama",
            source_type="subprocess",
            runtime_config={"base_url": "http://127.0.0.1:11434"},
            metadata={},
        )
    return SimpleNamespace(
        provider_id=provider_id,
        provider_name="openrouter-byok",
        model_name=model_name,
        adapter_type="openai_compatible",
        source_type="http",
        runtime_config={"base_url": "https://openrouter.example/api/v1"},
        metadata={"cost_class": "remote_unknown"},
    )


LOCAL_SMALL = _manifest("local-ollama", "qwen3:8b", local=True)
LOCAL_BIG = _manifest("local-ollama", "qwen3:14b", local=True)
FREE_CLOUD = _manifest("openrouter-byok", "vendor/large-reasoner-500b:free", local=False)


def _nomination(*, title, line_no, scenario):
    return json.dumps(
        {
            "title": title,
            "file": MAPPER_TARGET,
            "line_start": line_no,
            "line_end": line_no,
            "cited_line_text": _MAPPER_LINES[line_no - 1],
            "failure_scenario": scenario,
        }
    )


DUPLICATE_FINDING = _nomination(
    title="Two aliases for one column silently drop a row's data",
    line_no=DUPLICATE_LINE_NO,
    scenario=(
        "A file with both `mail` and `e-mail` headers maps both to `email`, so the second index "
        "overwrites the first and every row returns incorrect data for the dropped column."
    ),
)
# Two further DISTINCT claims, so a search can advance candidate-by-candidate instead of being
# rejected as a reworded repeat of the first.
WHITESPACE_FINDING = _nomination(
    title="A header with trailing whitespace maps to the wrong column",
    line_no=_MAPPER_LINES.index("    return header.strip().lower()") + 1,
    scenario=(
        "A header of `Email ` normalises to `email` while the alias table is keyed on the raw "
        "value, so read_row returns data from an incorrect column."
    ),
)
MISSING_COLUMN_FINDING = _nomination(
    title="A missing column raises instead of reporting an absent field",
    line_no=_MAPPER_LINES.index("    return row[mapping[column]]") + 1,
    scenario=(
        "read_row indexes the mapping directly, so a file without the requested column crashes on "
        "valid input rather than reporting the field as absent."
    ),
)
# Cites a line past the end of the file, so the evidence gate rejects it and a correction follows.
UNCHECKABLE_FINDING = json.dumps(
    {
        "title": "The mapper truncates data on an unknown column",
        "file": MAPPER_TARGET,
        "line_start": len(_MAPPER_LINES) + 40,
        "line_end": len(_MAPPER_LINES) + 41,
        "cited_line_text": "nothing lives here",
        "failure_scenario": "Rows are silently dropped when a column is missing.",
    }
)
SUPPORTED = json.dumps(
    {
        "verdict": "supported",
        "reason": "The cited assignment is keyed on the alias, so two aliases collide.",
        "counterexample": "headers ['mail', 'e-mail'] map to one key.",
    }
)


class _ScriptedRouter:
    def __init__(self, replies, *, pinned_manifest=None):
        self.replies = list(replies)
        self.requests = []
        self.manifests = []
        self.pinned_manifest = pinned_manifest
        self.registry = SimpleNamespace()

    def _requested_model_manifest(self, _context):
        return self.pinned_manifest

    def _invoke_manifest(self, *, manifest, request, output_mode, task, source_context):
        self.requests.append(request)
        self.manifests.append(manifest)
        if not self.replies:
            return (None, None, "script_exhausted")
        item = self.replies.pop(0)
        if isinstance(item, str) and item.startswith("ERROR:"):
            return (None, None, item[len("ERROR:") :])
        return (
            None,
            SimpleNamespace(
                output_text=str(item),
                usage={"prompt_tokens": 2900, "completion_tokens": 210},
                provider_id=manifest.provider_id,
                model_name=manifest.model_name,
                model_call_id="call-1",
                response_id="resp-1",
            ),
            None,
        )

    def steps(self):
        return [
            str(dict(getattr(request, "metadata", None) or {}).get("stepped_audit_step") or "")
            for request in self.requests
        ]

    def models(self):
        return [str(getattr(item, "model_name", "")) for item in self.manifests]

    def prompts(self):
        return [str(getattr(request, "prompt", "") or "") for request in self.requests]


class _ToolRunner:
    def __init__(self):
        self.calls = []

    def intents(self):
        return [intent for intent, _args in self.calls]

    def __call__(self, payload, **kwargs):
        self.calls.append((str(payload.get("intent") or ""), dict(payload.get("arguments") or {})))
        return SimpleNamespace(
            ok=True, handled=True, response_text="", details={"returncode": 1},
            status="executed", mode="tool_executed",
        )


def _context(**overrides):
    ctx = {
        "project_id": "proj-mapper",
        "chat_id": "chat-mapper",
        "workspace_audit_evidence_collected": True,
        "workspace_audit_evidence": {
            "all_paths": (MAPPER_TARGET,),
            "inspected_paths": (MAPPER_TARGET,),
            "sources": {MAPPER_TARGET: MAPPER},
            "workspace_root": "/tmp/mapper-ws",
            "incomplete_files": (),
        },
    }
    ctx.update(overrides)
    return ctx


def _drive(replies, *, context, pinned_manifest=None, prompt=AUDIT_PROMPT, session_id="mapper-1"):
    router = _ScriptedRouter(replies, pinned_manifest=pinned_manifest)
    tools = _ToolRunner()
    agent = SimpleNamespace(
        memory_router=router,
        _execute_tool_intent=tools,
        hive_activity_tracker=None,
        public_hive_bridge=None,
    )
    decision = run_stepped_audit(
        agent,
        task=SimpleNamespace(task_id="task-mapper"),
        effective_input=prompt,
        source_context=context,
        session_id=session_id,
    )
    return decision, router, tools


def _stepped(decision):
    return dict(dict(getattr(decision, "details", None) or {}).get("stepped_audit") or {})


def _report(decision):
    return str(getattr(decision, "output_text", "") or "")


@pytest.fixture
def ranked(monkeypatch):
    """Whatever the provider ranking would have returned, under test control."""

    def _install(manifests):
        import core.provider_routing as provider_routing

        monkeypatch.setattr(
            provider_routing, "rank_provider_candidates", lambda *a, **k: list(manifests)
        )

    return _install


@pytest.fixture(autouse=True)
def _clean_state():
    clear_audit_capsules()
    clear_active_findings()
    yield
    clear_audit_capsules()
    clear_active_findings()


# --------------------------------------------------------------------------------------
# The mode is read from state, and it decides the model
# --------------------------------------------------------------------------------------


def test_the_three_routing_modes_are_read_from_state_not_from_the_sentence() -> None:
    assert resolve_routing_mode(_context()).mode == AUTO
    assert resolve_routing_mode(_context(requested_model="vool")).mode == AUTO
    assert resolve_routing_mode(_context(requested_model="vendor/x")).mode == MANUAL
    assert resolve_routing_mode(_context(local_only_mode=True)).mode == LOCAL_ONLY
    # A pin cannot buy its way out of local-only, and the pin is still remembered so the refusal can
    # name it.
    conflicted = resolve_routing_mode(_context(local_only_mode=True, requested_model="vendor/x"))
    assert conflicted.mode == LOCAL_ONLY
    assert conflicted.requested_model == "vendor/x"
    assert conflicted.cloud_permitted is False


def test_a_manifest_whose_class_cannot_be_read_is_treated_as_cloud() -> None:
    """Fail closed: local-only's whole promise is that nothing left the machine."""
    assert manifest_is_cloud(LOCAL_SMALL) is False
    assert manifest_is_cloud(FREE_CLOUD) is True
    assert manifest_is_cloud(object()) is True


# --------------------------------------------------------------------------------------
# HOSTILE 9 — a pinned model answers every part of the turn
# --------------------------------------------------------------------------------------


def test_every_helper_call_runs_on_the_pinned_model(ranked) -> None:
    # The ranking is stacked against the pin on purpose: if anything consults it, a different model
    # answers and the test fails.
    ranked([FREE_CLOUD, LOCAL_BIG])
    decision, router, _tools = _drive(
        [DUPLICATE_FINDING, SUPPORTED],
        context=_context(requested_model=LOCAL_SMALL.model_name),
        pinned_manifest=LOCAL_SMALL,
    )

    assert router.steps() == ["nominate", "challenge"]
    assert set(router.models()) == {LOCAL_SMALL.model_name}, (
        "a helper call answered on a model the operator did not select"
    )
    routing = _stepped(decision)["model_routing"]
    assert routing["mode"] == MANUAL
    assert routing["models_used"] == [f"{LOCAL_SMALL.provider_id}:{LOCAL_SMALL.model_name}"]
    assert all(row["fallback"] is False for row in routing["attributions"])


def test_an_unreachable_pin_is_refused_by_name_and_never_substituted(ranked) -> None:
    ranked([LOCAL_SMALL, FREE_CLOUD])
    decision, router, _tools = _drive(
        [DUPLICATE_FINDING],
        context=_context(requested_model="vendor/absent-model"),
        pinned_manifest=None,
    )
    report = _report(decision)

    assert router.requests == [], "an unresolvable pin was answered by a different model"
    assert "vendor/absent-model" in report
    assert "will not be answered by a different model" in report


# --------------------------------------------------------------------------------------
# HOSTILE 10 — local-only makes no cloud call at all
# --------------------------------------------------------------------------------------


def test_local_only_routing_makes_zero_cloud_calls(ranked) -> None:
    # The cloud model is ranked FIRST, which is exactly how the incident's model was selected.
    ranked([FREE_CLOUD, LOCAL_SMALL])
    decision, router, _tools = _drive(
        [DUPLICATE_FINDING, SUPPORTED], context=_context(local_only_mode=True)
    )

    assert router.models() and set(router.models()) == {LOCAL_SMALL.model_name}
    routing = _stepped(decision)["model_routing"]
    assert routing["mode"] == LOCAL_ONLY
    assert routing["cloud_calls"] == 0
    assert _stepped(decision)["call_budget"]["cloud_calls"] == 0


def test_the_candidate_list_itself_holds_no_cloud_model_under_local_only(ranked) -> None:
    """Two independent guarantees back this up — the list is built without cloud manifests, and the
    call choke point refuses one anyway. Each is pinned on its own, because a test that only checks
    the OUTCOME passes while either one is intact, and then neither is really tested."""
    from core.agent_runtime.audit_routing import select_audit_manifests

    ranked([FREE_CLOUD, LOCAL_SMALL, LOCAL_BIG])
    agent = SimpleNamespace(memory_router=_ScriptedRouter([]))
    context = _context(local_only_mode=True)

    manifests, reason = select_audit_manifests(agent, context, resolve_routing_mode(context))

    assert manifests, reason
    assert [m.model_name for m in manifests] == [LOCAL_SMALL.model_name, LOCAL_BIG.model_name]
    assert all(not manifest_is_cloud(m) for m in manifests)


def test_the_call_choke_point_refuses_a_cloud_manifest_under_local_only() -> None:
    """The backstop, exercised directly: even handed a cloud manifest, no request is issued."""
    from core.agent_runtime.audit_call_budget import read_only_ledger
    from core.agent_runtime.audit_routing import RoutingLedger
    from core.agent_runtime.stepped_audit import SteppedAuditBudget, _call_step

    context = _context(local_only_mode=True)
    router = _ScriptedRouter([DUPLICATE_FINDING])
    budget = SteppedAuditBudget(
        ledger=read_only_ledger(),
        routing=RoutingLedger(routing=resolve_routing_mode(context)),
    )

    result = _call_step(
        SimpleNamespace(memory_router=router),
        manifest=FREE_CLOUD,
        task=SimpleNamespace(task_id="t"),
        source_context=context,
        step="nominate",
        prompt="anything",
        system_prompt="anything",
        max_output_tokens=100,
        output_mode="json_object",
        budget=budget,
    )

    assert router.requests == [], "a cloud call was issued under local-only routing"
    assert result.attempted is False
    assert "local_only" in result.error


def test_local_only_with_no_local_model_refuses_rather_than_reaching_the_cloud(ranked) -> None:
    ranked([FREE_CLOUD])
    decision, router, _tools = _drive(
        [DUPLICATE_FINDING], context=_context(local_only_mode=True)
    )

    assert router.requests == []
    assert "local-only routing is on" in _report(decision)


def test_a_cloud_pin_under_local_only_is_refused_by_name(ranked) -> None:
    ranked([LOCAL_SMALL])
    decision, router, _tools = _drive(
        [DUPLICATE_FINDING],
        context=_context(local_only_mode=True, requested_model=FREE_CLOUD.model_name),
        pinned_manifest=FREE_CLOUD,
    )
    report = _report(decision)

    assert router.requests == [], "local-only was overridden by a pin"
    assert FREE_CLOUD.model_name in report
    assert "not a model that runs on this machine" in report


# --------------------------------------------------------------------------------------
# HOSTILE 11 — auto may route, and every choice is attributable
# --------------------------------------------------------------------------------------


def test_auto_routing_is_permitted_and_every_choice_is_recorded(ranked) -> None:
    ranked([FREE_CLOUD, LOCAL_SMALL])
    decision, router, _tools = _drive([DUPLICATE_FINDING, SUPPORTED], context=_context())

    routing = _stepped(decision)["model_routing"]
    assert routing["mode"] == AUTO
    assert len(routing["attributions"]) == len(router.requests)
    for row, step in zip(routing["attributions"], router.steps(), strict=True):
        assert row["step"] == step
        assert row["model_name"], "a model choice was made with no model recorded"
        assert row["reason"], "an auto route was taken with no reason recorded"
    assert routing["cloud_calls"] == len(router.requests)


def test_auto_routing_replaces_a_provider_that_never_answered(ranked) -> None:
    ranked([FREE_CLOUD, LOCAL_SMALL])
    _decision, transport, _tools = _drive(
        ["ERROR:connection reset", "ERROR:connection reset", DUPLICATE_FINDING, SUPPORTED],
        context=_context(),
    )

    assert FREE_CLOUD.model_name in transport.models()
    assert LOCAL_SMALL.model_name in transport.models(), "a dead provider was never replaced"


def test_a_rejected_citation_does_not_fan_out_to_another_provider(ranked) -> None:
    """A claim the source refutes is not a transport failure. A second model re-reading the same
    file pays another full excerpt to be wrong differently — that fan-out is a third of the nine
    calls the incident made.

    The shape matters: one shape failure then two rejections leaves the citation budget UNspent, so
    the driver still has room to walk on. That is exactly when the rule has to hold on its own.
    """
    ranked([FREE_CLOUD, LOCAL_SMALL])
    _decision, router, _tools = _drive(
        ["", UNCHECKABLE_FINDING, UNCHECKABLE_FINDING, DUPLICATE_FINDING, SUPPORTED],
        context=_context(),
        prompt=f"Audit {MAPPER_TARGET} and prove the highest-risk bug with a failing test.",
    )

    assert set(router.models()) == {FREE_CLOUD.model_name}, (
        "a rejected citation fanned out to another provider and re-sent the file"
    )


# --------------------------------------------------------------------------------------
# HOSTILE 12 — the call count is bounded, and every call says why it happened
# --------------------------------------------------------------------------------------


def test_a_simple_audit_costs_the_bounded_shape_of_calls(ranked) -> None:
    """primary analysis -> optional independent challenge -> verdict. The verdict costs no call."""
    ranked([LOCAL_SMALL])
    decision, router, _tools = _drive([DUPLICATE_FINDING, SUPPORTED], context=_context())

    assert router.steps() == ["nominate", "challenge"]
    assert _stepped(decision)["call_budget"]["calls"] == 2


def test_a_read_only_audit_that_finds_nothing_cannot_reach_nine_calls(ranked) -> None:
    """The incident's own shape: candidates that keep failing verification. It cost 9 calls."""
    ranked([LOCAL_SMALL, LOCAL_BIG, FREE_CLOUD])
    refuted = json.dumps({"verdict": "refuted", "reason": "the cited line does not do that."})
    decision, router, _tools = _drive(
        [DUPLICATE_FINDING, refuted] * 6, context=_context()
    )

    budget = _stepped(decision)["call_budget"]
    assert len(router.requests) <= budget["max_total_calls"] <= 5
    assert budget["budget_refusals"], "the search was truncated with no record of why"


def test_every_call_records_what_it_was_given_and_why_it_was_necessary(ranked) -> None:
    ranked([LOCAL_SMALL])
    decision, _router, _tools = _drive(
        [UNCHECKABLE_FINDING, DUPLICATE_FINDING, SUPPORTED], context=_context()
    )
    trace = _stepped(decision)["call_budget"]["trace"]

    assert [row["call"] for row in trace] == list(range(1, len(trace) + 1))
    assert [row["purpose"] for row in trace] == ["nominate", "nominate", "challenge"]
    assert trace[0]["result"].startswith("rejected:")
    assert all(row["model_name"] == LOCAL_SMALL.model_name for row in trace)
    # The first call is the only one that carries the whole file.
    assert trace[0]["context_source"] == CONTEXT_FULL_EXCERPT
    assert trace[1]["context_source"] == CONTEXT_CITED_WINDOW
    assert trace[2]["context_source"] == CONTEXT_CITED_WINDOW
    for row in trace[1:]:
        assert row["why_another_call"], "an extra call was made with no justification recorded"


def test_a_correction_does_not_re_send_the_whole_file(ranked) -> None:
    """The single largest line item in 28,741 tokens: every retry re-sent the same excerpt."""
    ranked([LOCAL_SMALL])
    decision, router, _tools = _drive(
        [UNCHECKABLE_FINDING, DUPLICATE_FINDING, SUPPORTED], context=_context()
    )
    first, correction = router.prompts()[0], router.prompts()[1]

    assert len(correction) < len(first) / 2, "the correction re-sent the file it was correcting"
    assert "REJECTED" in correction, "the correction did not carry what was wrong"
    trace = _stepped(decision)["call_budget"]["trace"]
    assert trace[1]["assembled_context_tokens_estimated"] < trace[0][
        "assembled_context_tokens_estimated"
    ]


def test_a_call_the_budget_refused_is_not_counted_as_a_call(ranked) -> None:
    """A refused call and a call that reported no usage are different facts. The second is a hole in
    a real call's receipt (rule 10); the first never happened, and counting it inflates the turn."""
    ranked([LOCAL_SMALL])
    refuted = json.dumps({"verdict": "refuted", "reason": "the cited line does not do that."})
    # Three DISTINCT candidates, so the search advances until the ceiling refuses the third
    # candidate's challenge — a refusal that reaches the usage list rather than being pre-empted
    # inside the nomination loop.
    decision, router, _tools = _drive(
        [
            DUPLICATE_FINDING, refuted,
            WHITESPACE_FINDING, refuted,
            MISSING_COLUMN_FINDING, refuted,
        ],
        context=_context(),
    )

    stepped = _stepped(decision)
    assert router.steps() == ["nominate", "challenge", "nominate", "challenge", "nominate"]
    assert stepped["call_budget"]["budget_refusals"], "the ceiling never fired, so nothing refused"
    made = len(router.requests)
    assert stepped["call_budget"]["calls"] == made
    assert stepped["token_breakdown"]["calls"] == made
    # …including the aggregate the rest of the runtime reads, which is fed by the usage list rather
    # than by the ledger and so can drift from it independently.
    assert int(dict(decision.details or {}).get("token_usage", {}).get("calls") or 0) == made
    assert stepped["token_breakdown"]["complete"] is True, (
        "a call that never happened was recorded as a call with no usage"
    )


# --------------------------------------------------------------------------------------
# Token accounting — four numbers, never blended into one
# --------------------------------------------------------------------------------------


def test_the_four_token_figures_stay_distinct() -> None:
    rows = [
        {
            "input_tokens": 3000,
            "output_tokens": 100,
            "assembled_context_tokens_estimated": 2500,
            "cloud": True,
        },
        {
            "input_tokens": 400,
            "output_tokens": 60,
            "assembled_context_tokens_estimated": 200,
            "cloud": True,
        },
    ]
    breakdown = audit_token_breakdown(rows)

    assert breakdown["calls"] == 2
    assert breakdown["largest_single_call_input"] == 3000
    assert breakdown["total_provider_input"] == 3400
    assert breakdown["assembled_project_context_largest"] == 2500
    assert breakdown["assembled_project_context_cumulative"] == 2700
    assert breakdown["system_tool_overhead"] == 700
    # The cumulative total is never presented as the size of one prompt.
    assert breakdown["largest_single_call_input"] != breakdown["total_provider_input"]
    assert breakdown["complete"] is True


def test_a_missing_usage_block_makes_the_total_a_labelled_lower_bound() -> None:
    rows = [
        {"input_tokens": 3000, "output_tokens": 100, "assembled_context_tokens_estimated": 2500},
        {"input_tokens": None, "output_tokens": None, "assembled_context_tokens_estimated": 200},
    ]
    breakdown = audit_token_breakdown(rows)
    sentence = audit_usage_sentence(breakdown)

    assert breakdown["lower_bound"] is True
    assert breakdown["calls_missing_usage"] == 1
    assert "LOWER BOUND" in sentence
    assert "1 of 2" in sentence


def test_an_overhead_that_cannot_be_derived_is_not_invented() -> None:
    """A provider that counted fewer tokens than the assembler estimated leaves no overhead to
    report. Reported as unknown rather than as zero, which would read as "there was none"."""
    rows = [{"input_tokens": 100, "output_tokens": 10, "assembled_context_tokens_estimated": 900}]
    assert audit_token_breakdown(rows)["system_tool_overhead"] is None


def test_an_expensive_turn_says_so_and_a_cheap_one_does_not() -> None:
    cheap = audit_token_breakdown(
        [{"input_tokens": 900, "output_tokens": 40, "assembled_context_tokens_estimated": 700}]
    )
    assert "unusually high" not in audit_usage_sentence(cheap)

    expensive = audit_token_breakdown(
        [
            {"input_tokens": 3193, "output_tokens": 197, "assembled_context_tokens_estimated": 2900}
            for _ in range(9)
        ]
    )
    sentence = audit_usage_sentence(expensive)
    assert "9 model calls" in sentence
    assert "unusually high" in sentence
    assert "recorded for optimisation" in sentence


# --------------------------------------------------------------------------------------
# The answer is an answer; the state is in Activity
# --------------------------------------------------------------------------------------


def test_the_answer_carries_no_internal_state_or_generated_source(ranked) -> None:
    ranked([LOCAL_SMALL])
    decision, _router, _tools = _drive(
        [UNCHECKABLE_FINDING, DUPLICATE_FINDING, SUPPORTED], context=_context()
    )
    report = _report(decision)
    lowered = report.lower()

    for leaked in (
        "terminal_state",
        "context_source",
        "call_budget",
        "assembled_context_tokens_estimated",
        "candidate_unproven",
        "full_file_excerpt",
        "bounded audit state",
        "unittest.main",
    ):
        assert leaked not in report, f"internal vocabulary reached the answer: {leaked}"
    assert "why_another_call" not in report
    assert "activity" in lowered, "the operator is not told where the detail lives"
    # …and the detail really is there.
    stepped = _stepped(decision)
    assert stepped["call_budget"]["trace"]
    assert stepped["token_breakdown"]["calls"]
