"""Two unrelated builds must not overwrite each other.

Every build that named no destination used to land in the single shared
`generated/workspace-starter` (`core/agent_runtime/builder/scaffolds.py`), so successive unrelated
builds silently clobbered each other file by file.

Confirmed on disk 2026-08-01 in `~/Desktop/openclaw-skills/generated/workspace-starter`: a request
to prove a bug in an Apache log codec produced `security.py`, `test_security.py` and a `README.md`
describing a daylight-saving bug in a `validate_token()` that exists in none of them -- three
different subjects in one folder, because each build overwrote part of the last. The runtime's own
notes already recorded the symptom: "the trio QA reported as one incoherent project (calculator
README + arithmetic tests + todo-list app.py) is that collision."

The directory is derived from the REQUEST, not from a clock: re-running the same ask must land in
the same place so a retry overwrites its own previous attempt rather than littering the workspace.
"""
from __future__ import annotations

from core.agent_runtime.builder.scaffolds import _unrooted_build_dir

PROOF = "prove the bug by writing the smallest failing regression test"
CALCULATOR = "build me a calculator app"
SECURITY = "create a security module with path validation"


def test_two_different_requests_do_not_share_a_directory() -> None:
    assert _unrooted_build_dir(PROOF) != _unrooted_build_dir(CALCULATOR)
    assert _unrooted_build_dir(CALCULATOR) != _unrooted_build_dir(SECURITY)
    assert _unrooted_build_dir(PROOF) != _unrooted_build_dir(SECURITY)


def test_the_same_request_is_stable_across_runs() -> None:
    """A retry must reuse its own folder. A timestamp would litter the workspace instead."""

    assert _unrooted_build_dir(PROOF) == _unrooted_build_dir(PROOF)
    assert _unrooted_build_dir(PROOF.upper()) == _unrooted_build_dir(PROOF)


def test_nothing_lands_in_the_old_shared_directory() -> None:
    for request in (PROOF, CALCULATOR, SECURITY, "", "do a thing"):
        assert _unrooted_build_dir(request) != "generated/workspace-starter"


def test_the_directory_is_readable_and_workspace_relative() -> None:
    """The operator has to recognise it in a file tree, and it must stay inside `generated/`."""

    target = _unrooted_build_dir(CALCULATOR)
    assert target.startswith("generated/")
    assert "calculator" in target
    assert ".." not in target and not target.startswith("/")


def test_a_request_of_pure_stop_words_still_yields_a_directory() -> None:
    """An empty slug must not produce `generated/-<hash>` or a bare `generated/`."""

    target = _unrooted_build_dir("do the a an and or for with")
    assert target.startswith("generated/build-")
