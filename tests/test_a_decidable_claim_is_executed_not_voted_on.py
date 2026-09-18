"""A claim Python can settle is settled by Python.

Twice, an audit of the same file reported `blob.startswith(ZSTD_MAGIC) raises IndexError` on an
empty blob. `b"".startswith(b"x")` returns `False`. The check that was supposed to catch it asked a
second instance of the same model to falsify the first, and it agreed — a vote between two copies of
one wrong belief.

What is pinned here: the narrow class of claims that can be executed without touching the audited
project IS executed, refutation requires positive evidence, and everything outside that class is
left alone for the ordinary challenge. The fixtures are deliberately not the incident's file — the
property is about claim SHAPE, not about codecs.
"""
from __future__ import annotations

import pytest

from core.agent_runtime.audit_claim_execution import decidable_claims, refuting_claim

# The verbatim scenario the runtime shipped to an operator, twice.
INCIDENT_TITLE = "Decompression fails on empty input due to missing empty-blob handling"
INCIDENT_SCENARIO = (
    "When compress() receives empty input (b''), it returns b'' (line 55). Calling decompress(b'') "
    "then reaches line 117 where blob.startswith(ZSTD_MAGIC) raises IndexError because the empty "
    "blob has no bytes to check, crashing decompression instead of returning b''."
)


def test_the_incident_claim_is_refuted_by_running_it() -> None:
    claim = refuting_claim(INCIDENT_TITLE, INCIDENT_SCENARIO)

    assert claim is not None, "the claim that shipped twice is still not being checked"
    assert claim.claimed_exception == "IndexError"
    assert claim.raised == "", "startswith raised nothing; the claim said IndexError"
    assert "does not raise IndexError" in claim.counterexample()
    assert "False" in claim.counterexample()


@pytest.mark.parametrize(
    "scenario",
    [
        "On an empty bytes payload, data.startswith(MAGIC) raises IndexError.",
        "With an empty string, text.upper() will raise ValueError.",
        "An empty dict makes config.get('key') raise KeyError.",
        "On empty input the buffer.decode() throws a UnicodeDecodeError.",
    ],
)
def test_false_exception_claims_on_empty_builtins_are_refuted(scenario: str) -> None:
    claim = refuting_claim(scenario)

    assert claim is not None, f"undecided: {scenario}"
    assert claim.refuted


def test_a_true_exception_claim_is_not_refuted() -> None:
    """Refutation needs positive evidence. `''.index(...)` really does raise ValueError, so the
    checker must stay silent and let the ordinary challenge run."""
    claims = decidable_claims("On an empty string, text.index('a') raises ValueError.")

    assert claims and claims[0].decided
    assert claims[0].refuted is False
    assert claims[0].counterexample() == ""
    assert refuting_claim("On an empty string, text.index('a') raises ValueError.") is None


@pytest.mark.parametrize(
    "scenario",
    [
        # The receiver is project code — not ours to execute.
        "codec.decompress(blob) raises RuntimeError when the stream is truncated.",
        # No emptiness established, so the receiver cannot be resolved.
        "The parser.feed(chunk) raises ValueError on malformed input.",
        # A mutating method: refused by name rather than run.
        "On an empty list, items.pop() raises IndexError.",
        # No exception claimed at all — this is the silent-truncation class, which needs a real test.
        "The loop breaks on a malformed varint and returns truncated output with no error.",
    ],
)
def test_claims_outside_the_decidable_class_are_left_alone(scenario: str) -> None:
    assert refuting_claim(scenario) is None


def test_nothing_from_the_audited_project_is_imported_or_run(monkeypatch) -> None:
    """The executor must not reach the workspace. If it ever imports, this bites."""
    import builtins

    real_import = builtins.__import__
    seen: list[str] = []

    def _watch(name, *args, **kwargs):
        seen.append(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _watch)
    refuting_claim(INCIDENT_TITLE, INCIDENT_SCENARIO)

    assert not any("liquefy" in n or "apache" in n for n in seen)
    assert not any(n.startswith("importlib") for n in seen)


# --------------------------------------------------------------------------------------
# A defect is a broken promise, so the promise has to be in scope
# --------------------------------------------------------------------------------------


def test_the_project_contract_is_a_manifest_the_audit_reads() -> None:
    """Measured live: `AGENTS.md` sat unread in the workspace while the audit answered from README
    and the target. The document that states what the code PROMISES was not in the list."""
    from core.agent_runtime.workspace_audit import _MANIFESTS

    for contract in ("AGENTS.md", "CONTRIBUTING.md", "CLAUDE.md"):
        assert contract in _MANIFESTS, f"{contract} is not read by the audit"


def test_the_target_is_searched_by_its_own_names_not_only_its_stem() -> None:
    """`tests/test_engines_run.py` reaches the engine through a registry, so searching the module
    stem returned no_results and the file was never read."""
    from core.agent_runtime.workspace_audit import _target_symbols

    source = (
        "import zstandard\n"
        "ZSTD_MAGIC = b'\\x28\\xb5\\x2f\\xfd'\n"
        "\n"
        "class LiquefyApacheRepetitionV1:\n"
        "    def compress(self, raw):\n"
        "        return raw\n"
        "\n"
        "def _private_helper():\n"
        "    pass\n"
        "\n"
        "def register_engine():\n"
        "    pass\n"
    )
    names = _target_symbols(source)

    assert "LiquefyApacheRepetitionV1" in names
    assert "register_engine" in names
    assert "_private_helper" not in names, "a private helper is not how another file refers to this"
    assert len(names) <= 4, "one search per name — the list has to stay bounded"


def test_a_test_that_references_the_target_is_pulled_into_scope() -> None:
    """The conventional-name rule assumes a test is named after its subject. This one is not."""
    from core.agent_runtime.workspace_audit import _related_to_target

    target = "api/apache/liquefy_apache_repetition_v1.py"
    all_paths = (target, "tests/test_engines_run.py", "docs/notes.md", "api/other.py")

    without = _related_to_target(target, all_paths, referencing=set())
    assert "tests/test_engines_run.py" not in without

    with_hit = _related_to_target(target, all_paths, referencing={"tests/test_engines_run.py"})
    assert "tests/test_engines_run.py" in with_hit
    assert with_hit[0] == target, "the named file must stay first so no limit can drop it"


def test_the_nomination_prompt_carries_the_contract_and_the_tests() -> None:
    from types import SimpleNamespace

    from core.agent_runtime.stepped_audit import _supporting_context

    evidence = SimpleNamespace(
        sources={
            "api/apache/engine.py": "class Engine: pass",
            "AGENTS.md": "MRTV: every round trip is verified bit-perfect.",
            "tests/test_engines_run.py": "def test_roundtrip():\n    assert out  # non-empty is enough",
        }
    )
    block = _supporting_context(evidence, "api/apache/engine.py")

    assert "MRTV" in block, "the guarantee the code must not break is missing"
    assert "non-empty is enough" in block, "the test that lets the defect through is missing"
    assert "class Engine" not in block, "the target is already in the prompt; do not send it twice"


# --------------------------------------------------------------------------------------
# The audit must not grade the project on its own discarded homework
# --------------------------------------------------------------------------------------


def test_the_audit_does_not_read_its_own_generated_artifacts() -> None:
    """Measured live 2026-08-01 on `~/Desktop/openclaw-skills`: an audit of ONE file read EIGHT
    `generated/api-apache-…/test_…_bug.py` files that earlier failed runs had written, handed them
    to the model as "existing tests", and the model kept renominating the claim they encode."""
    from core.agent_runtime.source_audit import is_runtime_artifact_path, is_source_path

    artifact = "generated/api-apache-liquefy-apache-333a91/test_liquefy_apache_repetition_v1_bug.py"
    assert is_runtime_artifact_path(artifact)
    assert is_source_path(artifact) is False, "the audit would read its own previous output"

    # A real test in a real test tree is untouched.
    assert is_source_path("tests/test_engines_run.py") is True
    assert is_runtime_artifact_path("tests/test_engines_run.py") is False
    # …and so is a project file that merely has the word in its name.
    assert is_source_path("api/generated_ids.py") is True


def test_a_generated_artifact_cannot_enter_scope_as_a_referencing_test() -> None:
    """The scope rule admits a test that REFERENCES the target. Every one of those eight artifacts
    references it — that is what they were generated to do."""
    from core.agent_runtime.workspace_audit import _related_to_target

    target = "api/apache/liquefy_apache_repetition_v1.py"
    artifact = "generated/api-apache-liquefy-apache-333a91/test_liquefy_apache_repetition_v1_bug.py"
    scoped = _related_to_target(target, (target, artifact, "tests/test_engines_run.py"),
                                referencing={artifact, "tests/test_engines_run.py"})

    assert artifact not in scoped
    assert "tests/test_engines_run.py" in scoped


def test_a_resource_gated_model_is_abandoned_not_re_asked() -> None:
    """`model_load_gated_low_memory` is a fact about the box. Re-asking the same manifest cannot
    change it, and two identical refusals spent the whole nomination budget live."""
    from core.agent_runtime.audit_call_budget import read_only_ledger

    ledger = read_only_ledger()
    for _ in range(2):
        call = ledger.open_call(step="nominate", context_source="full_file_excerpt")
        ledger.close_call(call, result="error:ollama-local:qwen3:14b: model_load_gated_low_memory")

    assert ledger.count == 2, "the calls still happened and are still on the receipt"
    assert ledger.spent_on("nominate") == 0, "a pre-inference refusal burned a reasoning attempt"
    assert ledger.may_call("nominate") is True, "the model that could answer had no attempts left"


def test_the_failure_sentence_names_the_model_that_answered() -> None:
    """Live: two dead qwen3:14b attempts, nemotron answered and returned empty — and the operator
    was told qwen3:14b answered."""
    from types import SimpleNamespace

    from core.agent_runtime.stepped_audit import _failure_sentence

    last = SimpleNamespace(error="", text="", model_name="vendor/answering-model")
    sentence = _failure_sentence(["qwen3:14b:model_load_gated_low_memory"], last, "vendor/answering-model")

    assert "vendor/answering-model" in sentence
    assert "qwen3:14b" not in sentence
