"""A turn that pins the whole reply to one artifact is not a request to scaffold a project.

"Output ONLY valid Python code" says the deliverable IS the message. The builder claimed those
turns anyway, answered with a workspace FILE LISTING instead of the requested code, and wrote a
directory into the repository as a side effect the turn never asked for.

Measured across four consecutive operator-supplied blind sets on 2026-08-17, in BOTH model lanes,
every one `route=builder_model_build` with `model_ran=False`. The project name was taken from an
arbitrary noun in the prompt:

* `logs/`      <- "creates a table named `logs`"
* `my-app/`    <- a Docker IMAGE name, from a request for a one-line `docker build` command
* `api-service/` <- a Kubernetes POD name, from a request for a `kubectl port-forward` command
* `markdown/`  <- the NEGATIVE constraint "Do NOT wrap it in markdown code fences"

The written files were also junk: a `README.md` containing Python, fabricated assertion hashes, and
a stray code fence inside `todo.py`. One run's leftovers then failed an unrelated `ops/verify.py`
lint gate, so this defect can fail a gate it has nothing to do with.

`build_request_intent` already had the right hook -- `is_opted_out` -- carrying explicit
prohibitions ("do not write", "no files"). What it lacked was the opt-out by CONSTRAINT, which is
the form real prompts overwhelmingly use.
"""

from __future__ import annotations

import pytest

from core.agent_runtime.build_request_intent import is_build_instruction, is_opted_out
from core.agent_runtime.fast_paths_builder import looks_like_builder_request

# Verbatim from the blind sets. Each one scaffolded a directory before this was fixed.
TEXT_ONLY = {
    "bash_curl": (
        "Write a bash script that uses `curl` to fetch `http://example.com` and pipes it to "
        '`grep "title"`. Output ONLY valid bash code. Do NOT wrap it in markdown code fences. '
        "Start directly with `curl`."
    ),
    "docker_build": (
        "Write a single-line Docker command to build an image named `my-app` with the tag `v2` from "
        "the current directory. Output ONLY the raw terminal command on one line. No markdown "
        "backticks, no explanations."
    ),
    "hashlib": (
        'Write a Python script that uses `hashlib` to compute the SHA-256 hash of the string '
        '"VOOL_RUNTIME" and prints it. Output ONLY valid Python code. Do NOT wrap it in markdown '
        "code fences. Start directly with `import hashlib`."
    ),
    "sqlite_logs": (
        "Write a Python script that connects to `sqlite3`, creates a table named `logs`, and inserts "
        "a timestamp. Constraints: Output ONLY valid Python code. Start directly with the `import` "
        "statement."
    ),
    "kubectl": (
        "Write a single-line `kubectl` command to forward local port 8080 to port 80 of a pod named "
        "`api-service` in the `production` namespace. Output ONLY the raw terminal command on one "
        "line. No markdown backticks, no explanations."
    ),
    "ts_interface": (
        "Write a TypeScript interface named `UserProfile` with properties `id` (number), `username` "
        "(string), and `isActive` (boolean). Output ONLY the raw interface block. No markdown "
        "backticks."
    ),
}

# Real scaffolding work. Declassifying these would break the builder outright, which is the failure
# mode a narrow "stop writing files" patch would have caused.
REAL_BUILDS = {
    "todo_app": "build me a todo app with a python backend and tests",
    "scraper": "create a new project that scrapes hn and stores it in sqlite",
    "named_file": "create a file called notes.md in the workspace with my meeting notes",
    "into_tests": "build the CLI wrapper and write the tests into tests/",
}


@pytest.mark.parametrize("name", sorted(TEXT_ONLY))
def test_an_output_only_codegen_request_is_not_claimed_as_a_build(name: str) -> None:
    prompt = TEXT_ONLY[name]
    assert is_opted_out(prompt), f"{name}: the output constraint is not read as a build opt-out"
    assert not is_build_instruction(prompt), f"{name}: still classified as an instruction to build"
    assert not looks_like_builder_request(prompt.lower()), f"{name}: the builder fast path still claims it"


@pytest.mark.parametrize("name", sorted(REAL_BUILDS))
def test_a_genuine_build_request_is_untouched(name: str) -> None:
    prompt = REAL_BUILDS[name]
    assert not is_opted_out(prompt), f"{name}: wrongly read as opted out of building"
    assert is_build_instruction(prompt), f"{name}: a real build stopped being recognised"
    assert looks_like_builder_request(prompt.lower()), f"{name}: the builder fast path stopped claiming it"


# The prompts this fix is actually load-bearing for. `ts_interface` is deliberately NOT here: it was
# never claimed as a build (set-4 L7.3 answered it correctly on the model route), so asserting that
# the sabotage reclaims it would be asserting credit this change has not earned. The sabotage test
# caught exactly that error in an earlier draft of this file.
RECLAIMED_WITHOUT_THE_FIX = {"bash_curl", "docker_build", "hashlib", "kubectl", "sqlite_logs"}


def test_removing_the_constraint_phrases_reproduces_the_misclaims() -> None:
    """Anti-vacuity: strip the new opt-out phrases and require the affected prompts to claim again."""
    import re

    from core.agent_runtime import build_request_intent as mod

    added = {"output only", "output just", "print only", "print just",
             "reply with only", "respond with only", "start directly with"}
    kept = tuple(p for p in mod._OPT_OUT if p not in added)
    assert len(kept) < len(mod._OPT_OUT), "the constraint phrases are no longer present to remove"

    original = mod._OPT_OUT_RE
    mod._OPT_OUT_RE = re.compile(r"(?:" + "|".join(re.escape(p) for p in kept) + r")")
    try:
        reclaimed = {name for name, p in TEXT_ONLY.items() if is_build_instruction(p)}
    finally:
        mod._OPT_OUT_RE = original

    assert reclaimed == RECLAIMED_WITHOUT_THE_FIX, (
        "SABOTAGE DID NOT BITE as measured: without the constraint phrases exactly the prompts that "
        f"scaffolded directories in the blind sets must claim a build again. Expected "
        f"{sorted(RECLAIMED_WITHOUT_THE_FIX)}, got {sorted(reclaimed)}"
    )
    # Gate restored.
    assert not is_build_instruction(TEXT_ONLY["hashlib"])
