"""An audit that never opens the file it was asked about audited something else.

Measured live 2026-08-03. The operator asked:

    "i need you to run an audit on tsconfig.extensions.projects.json tell me if you see any
     issues or flawS?"

and received, after nine collector tools ran:

    ## Audit blocked
    The audit stopped before it could reach a verdict, so it reports no finding.
    - Reason: this turn carried no readable audit evidence, so there was nothing to audit

The collector read AGENTS.md, CONTRIBUTING.md, CLAUDE.md, README.md, package.json, Dockerfile,
docker-compose.yml and two listings. It never read `tsconfig.extensions.projects.json` — the one
file the request named.

The cause was in `_AUDIT_TARGET_RE`. Its stem was `[\\w\\-]+`, with no dots, so a multi-part
filename matched only its last two segments and `tsconfig.extensions.projects.json` was extracted
as `ts.json`. Nothing on disk is called that, so target resolution returned "" — and an empty
target means "audit the whole project", which is what ran.

Worse than a wrong answer: the missing-target branch immediately below exists precisely to say
"No file named X in this project", and a mangled name routed around it. The operator was told the
audit found nothing, when the audit had never looked.
"""
from __future__ import annotations

import pytest

from core.agent_runtime.workspace_audit import (
    _match_target_path,
    _match_target_path_on_disk,
    audit_target_in,
)


def test_the_measured_request_extracts_its_whole_filename() -> None:
    """This returned `ts.json` and cost the operator the audit they asked for."""

    assert (
        audit_target_in(
            "i need you to run an audit on tsconfig.extensions.projects.json tell me if you see "
            "any issues or flawS?"
        )
        == "tsconfig.extensions.projects.json"
    )


@pytest.mark.parametrize(
    ("request_text", "expected"),
    [
        ("audit tsconfig.core.projects.json", "tsconfig.core.projects.json"),
        ("check docker-compose.override.yml", "docker-compose.override.yml"),
        ("review jest.config.integration.js", "jest.config.integration.js"),
    ],
)
def test_other_multi_part_names_survive(request_text: str, expected: str) -> None:
    """These are ordinary filenames, not an edge case — the project has eight of them."""

    assert audit_target_in(request_text) == expected


@pytest.mark.parametrize(
    ("request_text", "expected"),
    [
        ("audit app-landing/index.html please", "app-landing/index.html"),
        (
            "lets audit api/apache/liquefy_apache_repetition_v1.py - whats the worst bug in it",
            "api/apache/liquefy_apache_repetition_v1.py",
        ),
        ("review pdf_rebuild.py", "pdf_rebuild.py"),
        ("check config.yaml", "config.yaml"),
        ("audit src/deep/nested/module.py", "src/deep/nested/module.py"),
    ],
)
def test_targets_that_already_worked_are_unchanged(request_text: str, expected: str) -> None:
    """Widening this regex is exactly how a previous target extraction broke. Controls."""

    assert audit_target_in(request_text) == expected


@pytest.mark.parametrize(
    "request_text",
    ["audit my project", "run a full code audit please", "audit the codebase", "review this repo"],
)
def test_a_request_naming_no_file_acquires_no_phantom_target(request_text: str) -> None:
    """A whole-project audit must stay one. A phantom target sends it down the missing-file
    branch and answers "No file named X" to someone who named nothing."""

    assert audit_target_in(request_text) == ""


def test_an_abbreviation_is_still_not_a_filename() -> None:
    """`e.g` and `i.e` end in a dot-word and are pre-existing exclusions; the wider stem must not
    start admitting them."""

    assert audit_target_in("audit the parser, e.g the tokenizer") == ""


# --------------------------------------------------------------------------------------
# Resolution. Extracting the name is half of it; `ts.json` died at the lookup.
# --------------------------------------------------------------------------------------


def test_the_target_resolves_against_the_inventory() -> None:
    inventory = (
        "tsconfig.projects.json",
        "tsconfig.extensions.projects.json",
        "tsconfig.core.json",
        "test/tsconfig.json",
    )
    target = audit_target_in("run an audit on tsconfig.extensions.projects.json")

    assert _match_target_path(target, inventory) == "tsconfig.extensions.projects.json"


def test_the_target_resolves_on_disk_when_the_inventory_missed_it(tmp_path) -> None:
    """The inventory caps at 200 paths, so a large repo can simply not list the named file."""

    (tmp_path / "tsconfig.extensions.projects.json").write_text("{}\n", encoding="utf-8")
    target = audit_target_in("audit tsconfig.extensions.projects.json for me")

    assert _match_target_path_on_disk(target, str(tmp_path)) == "tsconfig.extensions.projects.json"


def test_a_named_file_that_does_not_exist_still_resolves_to_nothing(tmp_path) -> None:
    """The missing-target branch must keep firing.

    It is what produces "No file named X in this project", and the mangled name routed around it —
    turning "your file is not here" into "I audited everything and found nothing".
    """

    assert _match_target_path_on_disk("definitely-not-here.json", str(tmp_path)) == ""
    assert _match_target_path("definitely-not-here.json", ("a.py", "b.py")) == ""


def test_a_config_target_is_not_filtered_out_for_not_being_source() -> None:
    """`is_source_path` excludes .json/.yaml/.toml, and the whole-project sweep uses it.

    The NAMED target must not be subject to that filter: the operator asked about a config file,
    and "it is not a source file" is not an answer to that question. Resolution deliberately does
    not consult `is_source_path`, and this pins that.
    """

    from core.agent_runtime.source_audit import is_source_path

    assert not is_source_path("tsconfig.extensions.projects.json")
    assert (
        _match_target_path("tsconfig.extensions.projects.json", ("tsconfig.extensions.projects.json",))
        == "tsconfig.extensions.projects.json"
    )
