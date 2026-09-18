"""Agentic app-builder: list -> per-file generate -> write -> test -> fix, plus path confinement."""
from __future__ import annotations

import json

from core.agent_runtime.builder import app_builder as ab


def test_extract_path_list_variants() -> None:
    assert ab.extract_path_list('["a.py", "b/c.py"]') == ["a.py", "b/c.py"]
    assert ab.extract_path_list('```json\n["a.py"]\n```') == ["a.py"]
    assert ab.extract_path_list('{"a.py": "x", "README.md": "y"}') == ["a.py", "README.md"]
    assert ab.extract_path_list("Here are the files:\n- todo.py\n- test_todo.py\n") == ["todo.py", "test_todo.py"]
    assert ab.extract_path_list("no paths here at all") == []


def test_strip_code_fences() -> None:
    assert ab.strip_code_fences("```python\nprint(1)\n```") == "print(1)"
    assert ab.strip_code_fences("plain content\nline2") == "plain content\nline2"


def test_strip_reasoning_monologue_drops_a_thinking_models_self_talk() -> None:
    # A thinking model's template opens <think> itself, so the reasoning arrives with only a
    # CLOSING tag in front of the real answer. Verbatim shape measured from qwen3:4b.
    leaked = 'Okay, let me process this step by step. Wait...\n</think>\n\n["todo.py"]'
    assert ab.strip_reasoning_monologue(leaked) == '["todo.py"]'
    assert ab.strip_reasoning_monologue('<think>\nhmm\n</think>\nprint(1)') == "print(1)"
    # The reasoning is one leading block: the FIRST closer ends it, the rest is the answer.
    assert ab.strip_reasoning_monologue("weighing it up</think>the answer</think>tail") == "the answer</think>tail"
    # An ordinary answer is untouched.
    assert ab.strip_reasoning_monologue('["todo.py"]') == '["todo.py"]'
    assert ab.strip_reasoning_monologue("") == ""


def test_strip_reasoning_monologue_keeps_a_file_that_is_about_think_tags() -> None:
    # "build me something that parses qwen3 think blocks" must not have its own file truncated at
    # the tag it was asked to handle: a reply that OPENS the tag is content, not reasoning.
    readme = "# Parser\n\nStrips <think>...</think> blocks from a reply.\n"
    assert ab.strip_reasoning_monologue(readme) == readme
    # ...while a reply that OPENS with the tag is still the model reasoning, and still goes.
    assert ab.strip_reasoning_monologue("<think>\nweigh it up\n</think>\nre.sub('<think>.*?</think>', '', s)") == (
        "re.sub('<think>.*?</think>', '', s)"
    )


def test_model_reply_parsers_never_treat_reasoning_as_the_answer() -> None:
    # The two places a raw model reply is consumed must both ignore the monologue: otherwise the
    # reasoning is parsed into junk "paths" or written into the user's file.
    leaked_list = 'Hmm, that\'s tricky. Let me think.\n</think>\n\n["todo.py", "test_todo.py"]'
    assert ab.extract_path_list(leaked_list) == ["todo.py", "test_todo.py"]

    leaked_file = "We are given a project.\n I should use argparse.\n</think>\n\n```python\nprint(1)\n```"
    assert ab.strip_code_fences(leaked_file) == "print(1)"


def test_path_confinement_rejects_escapes() -> None:
    assert ab._confine_relative_path("todo.py", target_dir="app") == "app/todo.py"
    assert ab._confine_relative_path("app/todo.py", target_dir="app") == "app/todo.py"
    assert ab._confine_relative_path("../secret", target_dir="app") is None
    assert ab._confine_relative_path("/etc/passwd", target_dir="app") is None
    assert ab._confine_relative_path("C:/win", target_dir="app") is None
    assert ab._confine_relative_path("a/../../b", target_dir="app") is None


def _mk_runner(write_ok=True, test_results=None):
    results = list(test_results or [])
    calls: list[tuple[str, dict]] = []

    def run(intent, arguments, *, trusted_local_only=False):
        calls.append((intent, dict(arguments)))
        if intent == "sandbox.run_command":
            assert trusted_local_only, "the generated-test run must be dispatched as trusted-local"
            ok = results.pop(0) if results else True
            return ab.ToolOutcome(ok=ok, response_text="OK" if ok else "FAILED: AssertionError")
        return ab.ToolOutcome(ok=write_ok, response_text="")

    run.calls = calls  # type: ignore[attr-defined]
    return run


def _mk_gen(paths, contents, fix="fixed content"):
    """generate_fn stub: returns the path list, then per-file content, then a fix on demand."""

    def gen(prompt):
        if "JSON array of relative file path" in prompt:
            return json.dumps(paths)
        if "tests FAILED" in prompt:
            return fix
        for name, body in contents.items():
            if f"`{name}`" in prompt:
                return body
        return "# placeholder\n"

    return gen


_PATHS = ["todo.py", "test_todo.py", "README.md"]
_CONTENT = {"todo.py": "import json\n", "test_todo.py": "import unittest\n", "README.md": "# todo\n"}


def test_build_happy_path_writes_and_passes() -> None:
    run = _mk_runner(test_results=[True])
    report = ab.build_app_from_spec(
        request="a todo cli", target_rel="vool-todo-test", source_context={"workspace": "/ws"},
        generate_fn=_mk_gen(_PATHS, _CONTENT), run_tool_fn=run,
    )
    assert report.handled is True
    assert set(report.files_written) == {
        "vool-todo-test/todo.py", "vool-todo-test/test_todo.py", "vool-todo-test/README.md"
    }
    assert report.tests_ran and report.tests_passed and report.fix_rounds == 0
    intents = [c[0] for c in run.calls]
    assert intents[0] == "workspace.ensure_directory" and "sandbox.run_command" in intents


def test_build_iterates_on_test_failure_then_passes() -> None:
    run = _mk_runner(test_results=[False, True])  # fail, then pass after the fix
    report = ab.build_app_from_spec(
        request="a todo cli", target_rel="app", source_context={"workspace": "/ws"},
        generate_fn=_mk_gen(_PATHS, _CONTENT, fix="import json  # fixed\n"), run_tool_fn=run,
    )
    assert report.tests_passed is True and report.fix_rounds == 1


def test_build_reports_honest_failure_after_budget() -> None:
    run = _mk_runner(test_results=[False] * (ab.MAX_FIX_ROUNDS + 1))  # never passes across the budget
    report = ab.build_app_from_spec(
        request="x", target_rel="app", source_context={"workspace": "/ws"},
        generate_fn=_mk_gen(_PATHS, _CONTENT), run_tool_fn=run,
    )
    assert report.tests_ran and report.tests_passed is False
    assert "still failing" in ab.render_app_build_response(report).lower()


def test_unsafe_paths_in_list_are_dropped() -> None:
    run = _mk_runner(test_results=[True])
    report = ab.build_app_from_spec(
        request="x", target_rel="app", source_context={"workspace": "/ws"},
        generate_fn=_mk_gen(["ok.py", "../evil.py", "/etc/passwd"], {"ok.py": "x = 1\n"}),
        run_tool_fn=run,
    )
    assert report.files_written == ["app/ok.py"]


# --------------------------------------------------------------------------------------
# The model keeps writing after the array it was asked for
# --------------------------------------------------------------------------------------


TRAILING_CODE_COMPLETION = (
    '["security.py", "test_security.py", "README.md"]import hmac\n'
    "import secrets\n"
    "from typing import Optional\n"
    "\n"
    "def hash_password(password: str, salt: Optional[bytes] = None) -> tuple[str, bytes]:\n"
    "    hash_value = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 100000)\n"
    "    return hash_value.hex(), salt\n"
)


def test_a_file_list_survives_the_model_continuing_past_it() -> None:
    """The exact completion that put raw model output on the operator's screen.

    `_LIST_PROMPT` asks for "ONLY a JSON array". The model produced the array and then kept
    generating the file bodies in the same completion. Path extraction used `rfind("]")`, which in
    that reply lands inside `Optional[bytes]` in the generated Python -- so `json.loads` was handed
    the array PLUS a slab of code, failed, and the line fallback rejected every line for containing
    spaces. `extract_path_list` returned [], the build reported `handled=False`, the turn fell out
    of the build lane, and one of the verbatim-passthrough points printed the whole completion.

    Measured 2026-08-01. The array is well-formed; only the scan for its end was wrong.
    """

    from core.agent_runtime.builder.app_builder import extract_path_list

    assert extract_path_list(TRAILING_CODE_COMPLETION) == [
        "security.py",
        "test_security.py",
        "README.md",
    ]


def test_ordinary_list_shapes_still_parse() -> None:
    """The fix must not be bought by breaking the shapes that already worked."""

    from core.agent_runtime.builder.app_builder import extract_path_list

    assert extract_path_list('["a.py", "b.py"]') == ["a.py", "b.py"]
    assert extract_path_list('```json\n["a.py"]\n```') == ["a.py"]
    assert extract_path_list('{"a.py": "...", "b.py": "..."}') == ["a.py", "b.py"]


def test_a_reply_with_no_list_still_yields_nothing() -> None:
    """`[]` must stay reachable -- it is what tells the caller the model produced nothing usable."""

    from core.agent_runtime.builder.app_builder import extract_path_list

    assert extract_path_list("I could not decide on the files") == []
    assert extract_path_list("") == []


# --------------------------------------------------------------------------------------
# "Prove the bug by writing a test that FAILS"
# --------------------------------------------------------------------------------------


def _prove_it_build(test_stdout: str, returncode: int, *, expected: str = "fail"):
    """Drive the real build lane with a stubbed model and runner."""

    from core.agent_runtime.builder import app_builder as ab

    body = "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_x(self):\n        self.assertTrue(False)\n"

    def generate(prompt: str) -> str:
        if "JSON array" in prompt:
            # The model keeps writing past the array -- the shape that broke path extraction.
            return '["test_bug.py"]import os\nx: list[str] = []\n'
        return body

    class _Outcome:
        def __init__(self, ok=True, response_text="", details=None):
            self.ok, self.response_text, self.details = ok, response_text, details or {}

    def run_tool(intent, arguments=None, **kwargs):
        if intent == "sandbox.run_command":
            return _Outcome(True, test_stdout, {"returncode": returncode})
        return _Outcome(True, "")

    return ab.build_app_from_spec(
        request="prove the bug by writing the smallest failing regression test",
        target_rel="g",
        source_context={"workspace": "/tmp/ws"},
        generate_fn=generate,
        run_tool_fn=run_tool,
        expected_test_outcome=expected,
        subject_paths=("api/apache/liquefy_apache_repetition_v1.py",),
    )


FAILING_OUTPUT = "test_x (test_bug.T) ... FAIL\n\nFAILED (failures=1)\nRan 1 test in 0.001s\n"
PASSING_OUTPUT = "test_x (test_bug.T) ... ok\n\nOK\nRan 1 test in 0.001s\n"


def test_a_test_that_fails_as_asked_is_the_deliverable_not_a_defect() -> None:
    """The fix loop must not run.

    `app_builder` regenerates the source up to MAX_FIX_ROUNDS specifically to turn a failing test
    green. When the operator asked for a test that proves a bug BY FAILING, that loop is working
    against the request -- it spends the budget destroying the deliverable.
    """

    report = _prove_it_build(FAILING_OUTPUT, 1)

    assert report.fix_rounds == 0, "the fix loop tried to repair a deliberately failing test"
    assert report.proof_failed is False
    assert report.test_returncode == 1


def test_a_passing_test_is_a_failed_proof_not_a_successful_build() -> None:
    """The gate that catches a fabricated finding.

    Measured 2026-08-01: asked to prove a bug, the lane produced a suite that exits 0 and reported
    success. A green suite means the claimed bug was NOT reproduced.
    """

    from core.agent_runtime.builder.controller import build_finished_successfully

    report = _prove_it_build(PASSING_OUTPUT, 0)

    assert report.proof_failed is True
    assert build_finished_successfully(
        files_written=report.files_written,
        tests_ran=report.tests_ran,
        tests_passed=report.tests_passed,
        proof_failed=report.proof_failed,
    ) is False, "a failed proof was scored as a successful build"


def test_the_real_exit_code_is_used_not_scraped_text() -> None:
    """`sandbox.run_command` reports 'executed' even for a failing suite, so the verdict used to be
    scraped from stdout. The code was in the outcome details the whole time."""

    report = _prove_it_build("garbled output the scraper cannot read\n", 1, expected="unspecified")
    assert report.test_returncode == 1
    assert report.tests_passed is False


def test_an_ordinary_build_still_repairs_a_failing_test() -> None:
    """The prove-it branch must not disable the fix loop for everyone else."""

    report = _prove_it_build(FAILING_OUTPUT, 1, expected="unspecified")
    assert report.fix_rounds > 0, "the ordinary build/test/fix loop stopped repairing"


def test_the_build_report_is_a_formatted_receipt() -> None:
    """A build reply is a receipt, composed in the runtime rather than asked of the model.

    The operator was shown `["security.py", ...]import hmac...` -- a raw completion -- because
    nothing here produced a human deliverable.
    """

    from core.agent_runtime.builder.app_builder import render_app_build_response

    rendered = render_app_build_response(_prove_it_build(PASSING_OUTPUT, 0))

    assert "not proven" in rendered
    assert "| File | Lines | Status |" in rendered
    assert "✅" in rendered and "❌" in rendered
    assert "Exit code" in rendered
    assert "api/apache/liquefy_apache_repetition_v1.py" in rendered, "the subject is not stated"
    # Every table row must be well formed -- the test digest contains a literal '|'.
    for line in rendered.splitlines():
        if line.startswith("| ") and "---" not in line:
            assert line.rstrip().endswith("|"), line


# --------------------------------------------------------------------------------------
# The builder must know what it was asked ABOUT
# --------------------------------------------------------------------------------------


def test_the_audited_file_reaches_the_generation_prompt(tmp_path) -> None:
    """Passing the subject to the report is not the same as telling the model.

    Measured twice on 2026-08-01: asked to prove a bug in
    `api/apache/liquefy_apache_repetition_v1.py`, the lane generated an unrelated security project
    about SQL injection and command injection. `subject_paths` reached AppBuildReport but the
    generation prompts still received only the raw sentence -- and "prove the bug you identified"
    names no file at all, so the model had nothing to work from and invented a subject.

    The subject is spliced in verbatim rather than summarised, so the file has to be REAL -- but it
    is built here rather than read off the author's Desktop. It used to point at
    `/Users/<user>/Desktop/openclaw-skills/...`, a path on exactly one machine, so this passed there
    and failed on every CI run: `subject_block` returns "" for a file it cannot open, the subject
    never reached the prompt, and the assertion below caught it correctly every time. The build is
    small but genuine, so the "spliced verbatim, not described" property still holds.
    """

    from core.agent_runtime.builder import app_builder as ab

    subject_rel = "api/apache/liquefy_apache_repetition_v1.py"
    subject_file = tmp_path / subject_rel
    subject_file.parent.mkdir(parents=True)
    subject_file.write_text(
        "def collapse_repeats(chunks):\n"
        "    # off-by-one: the final chunk is dropped when the run reaches the end\n"
        "    out = []\n"
        "    for i in range(len(chunks) - 1):\n"
        "        if chunks[i] != chunks[i + 1]:\n"
        "            out.append(chunks[i])\n"
        "    return out\n",
        encoding="utf-8",
    )

    prompts: list[str] = []

    def generate(prompt: str) -> str:
        prompts.append(prompt)
        return '["test_bug.py"]' if "JSON array" in prompt else "import unittest\n"

    class _Outcome:
        def __init__(self, ok=True, response_text="", details=None):
            self.ok, self.response_text, self.details = ok, response_text, details or {}

    def run_tool(intent, arguments=None, **kwargs):
        if intent == "sandbox.run_command":
            return _Outcome(True, "FAILED (failures=1)\nRan 1 test\n", {"returncode": 1})
        return _Outcome(True, "")

    workspace = str(tmp_path)
    ab.build_app_from_spec(
        request="prove the bug you identified",
        target_rel="g",
        source_context={"workspace": workspace, "workspace_root": workspace},
        generate_fn=generate,
        run_tool_fn=run_tool,
        expected_test_outcome="fail",
        subject_paths=(subject_rel,),
    )

    assert prompts, "the generator was never called"
    listing = prompts[0]
    assert "liquefy_apache_repetition_v1.py" in listing, "the subject is not named in the prompt"
    assert "do not invent an unrelated" in listing


def test_no_subject_leaves_the_prompt_exactly_as_it_was() -> None:
    """A build that names no subject must behave as it always did."""

    from core.agent_runtime.builder.app_builder import subject_block

    assert subject_block((), workspace_root="/tmp") == ""
    assert subject_block(("does/not/exist.py",), workspace_root="/tmp") == ""
    assert subject_block(("",), workspace_root="/tmp") == ""
