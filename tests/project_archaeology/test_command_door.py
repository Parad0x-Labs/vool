"""The archaeology COMMAND DOOR (C21): the authority exposed like the C11 logs group.

``core.project_archaeology`` is exposed as a command group exactly the way
``core/command_registry/groups/logs_group.py`` exposes the Liquefy projection:
a ``GroupSpec(group_id="archaeology")`` plus five read-only ``CommandSpecs``
(locate / search / trace / compare / recovery_plan), every one ``read_only``,
``logs.read``-capped and model-offerable. These tests pin that door FIRST (RED)
— the group module and the ``core.project_archaeology`` authority do not exist
yet, so every import of them happens lazily inside a test and every miss is a
truthful RED, never a collection error.

Three doors are proven over the REAL seeded corpus (``conftest.py``), using the
same subprocess seams as ``tests/logs_operator/test_logs_served_chat.py``:

- the ``vool`` CLI (``core.command_registry.cli.main``) — flags are dataclass
  field names (``--text``, ``--limit``, ``--since`` ...), homes come from env;
- the model lane seam a served chat turn calls
  (``core.command_registry.model_tools.execute_model_command``);
- the vocabulary projection a chat turn is offered
  (``registry()`` then ``core.tool_registry.registry_map()``).

Safety pins: no archaeology command may declare anything but ``read_only``
effects or carry a mutation verb in its id, and the group module must be a
THIN adapter over ``core.project_archaeology`` (no sqlite/shutil/git verbs of
its own). After the doors run, the authoritative journal is still nine entries
and still verifies.
"""
from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from core.command_registry.execute import ExecutionContext, execute_command
from core.command_registry.registry import registry
from tests.project_archaeology.conftest import journal_count

REPO_ROOT = Path(__file__).resolve().parents[2]
GROUP_MODULE = "core.command_registry.groups.archaeology_group"
GROUP_MODULE_PATH = REPO_ROOT / "core" / "command_registry" / "groups" / "archaeology_group.py"


@pytest.fixture()
def arch_corpus(arch_home, blackbox):
    """The homes + seeded journal the doors read, from the shared fixtures.

    Composed directly from ``arch_home`` + ``blackbox`` rather than the pack's
    ``corpus`` fixture: ``corpus`` chains the ``memory`` fixture, whose stale
    ``ProfileChange.action`` assertion (conftest.py) errors at setup on this
    checkout. The door seams only need the seeded journal homes.
    """
    return {
        "home": arch_home["home"],
        "blackbox_root": arch_home["blackbox_root"],
        "blackbox": blackbox,
    }


ARCHAEOLOGY_COMMANDS = (
    "archaeology.locate",
    "archaeology.search",
    "archaeology.trace",
    "archaeology.compare",
    "archaeology.recovery_plan",
)

MUTATION_VERBS = ("restore", "delete", "checkout", "reset", "clean")

# Handler names follow the logs-group health-style convention: _handle_<name>.
_EXPECTED_HANDLERS = {
    command_id: f"{GROUP_MODULE}:_handle_{command_id.split('.', 1)[1]}"
    for command_id in ARCHAEOLOGY_COMMANDS
}

# "all defaulted" marker: the field must have SOME default, value not pinned.
_ANY_DEFAULT = object()

# The exact input dataclass contracts (field name -> (type name, default)).
_EXPECTED_INPUT_FIELDS: dict[str, dict[str, tuple[str, object]]] = {
    "archaeology.locate": {
        "reference": ("str", ""),
        "workspace_root": ("str", ""),
        "limit": ("int", 25),
    },
    "archaeology.search": {
        "text": ("str", _ANY_DEFAULT),
        "store": ("str", _ANY_DEFAULT),
        "kind": ("str", _ANY_DEFAULT),
        "session_id": ("str", _ANY_DEFAULT),
        "turn_id": ("str", _ANY_DEFAULT),
        "path_prefix": ("str", _ANY_DEFAULT),
        "since": ("str", _ANY_DEFAULT),
        "until": ("str", _ANY_DEFAULT),
        "workspace_root": ("str", _ANY_DEFAULT),
        "limit": ("int", _ANY_DEFAULT),
    },
    "archaeology.trace": {
        "turn_id": ("str", _ANY_DEFAULT),
        "session_id": ("str", _ANY_DEFAULT),
        "effect_id": ("str", _ANY_DEFAULT),
        "attempt_id": ("str", _ANY_DEFAULT),
        "path": ("str", _ANY_DEFAULT),
        "since": ("str", _ANY_DEFAULT),
        "until": ("str", _ANY_DEFAULT),
        "workspace_root": ("str", _ANY_DEFAULT),
        "limit": ("int", _ANY_DEFAULT),
    },
    "archaeology.compare": {
        "kind": ("str", "git_commits"),
        "a": ("str", ""),
        "b": ("str", ""),
        "workspace_root": ("str", ""),
    },
    "archaeology.recovery_plan": {
        "reference": ("str", ""),
        "path": ("str", ""),
        "turn_id": ("str", ""),
        "workspace_root": ("str", ""),
        "limit": ("int", 25),
    },
}

FORBIDDEN_IN_HANDLER_SOURCE = (
    "import sqlite3",
    "INSERT INTO",
    "UPDATE ",
    "DELETE FROM",
    "shutil",
    "os.remove",
    "rmtree",
    "checkout",
    "git reset",
)

CLI_MAIN = "import sys; from core.command_registry.cli import main; sys.exit(main(sys.argv[1:]))"


# -- subprocess helpers (same seams as tests/logs_operator/test_logs_served_chat.py)


def _arch_env(homes: dict) -> dict[str, str]:
    """The corpus homes, as the CLI/model doors read them: env only, no flags."""
    env = dict(os.environ)
    env.update(
        {
            "VOOL_HOME": str(homes["home"]),
            "VOOL_BLACKBOX_DIR": str(homes["blackbox_root"]),
            "VOOL_LIQUEFY_LOGS": "0",  # the corpus home runs without the projection lane
            "VOOL_KEY_STORAGE_MODE": "file",
            "VOOL_KEY_PASSPHRASE": "arch-lane-test-only",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    return env


def _boot_subprocess_home(env: dict[str, str]) -> None:
    """Migrate the subprocess home once — what every real VOOL boot does."""
    boot = (
        "import sys\n"
        f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
        "from storage.migrations import run_migrations\n"
        "run_migrations()\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", boot],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=120,
    )
    assert completed.returncode == 0, completed.stderr[-600:]


def _run_py(script: str, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=120,
    )


def _model_door_script(intent: str, arguments: dict) -> str:
    """The exact seam a served chat turn's operator.command.* branch calls."""
    return (
        "import sys, json\n"
        f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
        "from core.command_registry.model_tools import execute_model_command\n"
        f"execution = execute_model_command({intent!r}, {arguments!r},\n"
        "    task_id='arch-1', session_id='sess-night')\n"
        "envelope = execution.details['envelope']\n"
        "print(json.dumps({'ok': execution.ok, 'mode': execution.mode,\n"
        "                  'summary': execution.response_text,\n"
        "                  'data': envelope['data'], 'fault': envelope['fault']}))\n"
    )


# -- 1. registry shape ---------------------------------------------------------


def test_registry_exposes_the_archaeology_group_and_exactly_five_read_only_commands() -> None:
    reg = registry()

    group = reg.group_of("archaeology")
    assert group is not None, "the archaeology group must be registered"
    assert group.group_id == "archaeology"

    registered = sorted(
        spec.command_id for spec in reg.commands() if spec.command_id.startswith("archaeology.")
    )
    assert registered == sorted(ARCHAEOLOGY_COMMANDS), registered

    for command_id in ARCHAEOLOGY_COMMANDS:
        spec = reg.lookup(command_id)
        assert spec is not None, command_id
        assert spec.group == "archaeology"
        assert spec.effects == "read_only", command_id
        assert spec.model_offerable is True, command_id
        assert "logs.read" in spec.capabilities, command_id
        assert spec.exit_codes == (0, 2, 10, 42), command_id
        assert spec.handler is not None and spec.handler.dotted == _EXPECTED_HANDLERS[command_id], command_id


def test_input_schemas_match_the_contract_dataclasses() -> None:
    reg = registry()
    for command_id, expected_fields in _EXPECTED_INPUT_FIELDS.items():
        spec = reg.lookup(command_id)
        assert spec is not None, command_id
        schema = spec.input_schema
        assert dataclasses.is_dataclass(schema), command_id
        fields = {f.name: f for f in dataclasses.fields(schema)}
        assert set(fields) == set(expected_fields), (command_id, sorted(fields))
        for name, (type_name, default) in expected_fields.items():
            field = fields[name]
            actual_type = getattr(field.type, "__name__", str(field.type))
            assert actual_type == type_name, (command_id, name, actual_type)
            has_default = (
                field.default is not dataclasses.MISSING
                or field.default_factory is not dataclasses.MISSING
            )
            assert has_default, (command_id, name, "every field must be defaulted")
            if default is not _ANY_DEFAULT:
                assert field.default == default, (command_id, name, field.default)


# -- 2. fault/safety pins ------------------------------------------------------


def test_no_archaeology_command_declares_mutation_effects_or_mutation_verb_ids() -> None:
    reg = registry()
    for spec in reg.commands():
        if not spec.command_id.startswith("archaeology."):
            continue
        assert spec.effects == "read_only", (
            f"{spec.command_id} declares effects={spec.effects!r}; archaeology is a reader"
        )
        for verb in MUTATION_VERBS:
            assert verb not in spec.command_id, (
                f"{spec.command_id} carries the mutation verb {verb!r}"
            )


def test_group_module_is_a_thin_adapter_over_core_project_archaeology() -> None:
    if not GROUP_MODULE_PATH.exists():
        pytest.fail("module missing")
    source = GROUP_MODULE_PATH.read_text(encoding="utf-8")
    for forbidden in FORBIDDEN_IN_HANDLER_SOURCE:
        assert forbidden not in source, (
            f"{GROUP_MODULE} must not {forbidden!r} itself; it adapts the authority"
        )
    assert "core.project_archaeology" in source, (
        "the group must bind to core.project_archaeology, not re-implement it"
    )


# -- 3. served-door seams over the seeded corpus homes --------------------------


def test_cli_door_archaeology_search_recovers_eff_push_77_over_seeded_homes(arch_corpus) -> None:
    env = _arch_env(arch_corpus)
    _boot_subprocess_home(env)
    cli = subprocess.run(
        [sys.executable, "-c", CLI_MAIN,
         "archaeology.search", "--json", "--text", "pre-receive"],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=120,
    )
    assert cli.returncode == 0, cli.stderr[-600:]
    envelope = json.loads(cli.stdout)
    assert envelope["ok"] is True, envelope
    results = envelope["data"]["results"]
    hit = [row for row in results if row.get("effect_id") == "eff-push-77"]
    assert hit, results
    assert any(row.get("outcome") == "failed" for row in hit), hit


def test_model_door_locate_executes_and_recovers_eff_push_77(arch_corpus) -> None:
    env = _arch_env(arch_corpus)
    _boot_subprocess_home(env)
    seam = _run_py(
        _model_door_script("operator.command.archaeology.locate", {"reference": "eff-push-77"}),
        env,
    )
    assert seam.returncode == 0, seam.stderr[-600:]
    payload = json.loads(seam.stdout)
    assert payload["ok"] is True, payload
    assert payload["mode"] == "tool_executed", payload
    results = payload["data"]["results"]
    assert any(row.get("effect_id") == "eff-push-77" for row in results), results
    assert "eff-push-77" in payload["summary"], payload["summary"]


def test_model_door_locate_unknown_reference_fails_closed_with_validation_fault(arch_corpus) -> None:
    env = _arch_env(arch_corpus)
    _boot_subprocess_home(env)
    seam = _run_py(
        _model_door_script("operator.command.archaeology.locate", {"reference": "no-such-000"}),
        env,
    )
    assert seam.returncode == 0, seam.stderr[-600:]
    payload = json.loads(seam.stdout)
    assert payload["ok"] is False, payload
    assert payload["mode"] == "tool_failed", payload
    fault = payload["fault"]
    assert fault is not None, payload
    # The observed, pinned fault_code: a no-match reference is fault_validation
    # with detail.not_found — never a silent empty success.
    assert fault["code"] == "fault_validation", fault
    assert (fault.get("detail") or {}).get("not_found") is True, fault


def test_unknown_input_key_is_a_usage_fault_like_logs_search() -> None:
    # The observed exemplar behavior this mirrors: the registry rejects unknown
    # input keys at dispatch (before any handler) with the "usage" fault.
    logs_envelope = execute_command("logs.search", {"no_such_key": "x"})
    assert logs_envelope.ok is False
    assert logs_envelope.fault is not None and logs_envelope.fault.code == "usage", logs_envelope.fault
    assert "unknown input keys" in (logs_envelope.fault.detail or {}).get("reason", "")

    archaeology_envelope = execute_command(
        "archaeology.locate", {"no_such_key": "x"},
        context=ExecutionContext(projection="cli"),
    )
    assert archaeology_envelope.ok is False
    fault = archaeology_envelope.fault
    assert fault is not None and fault.code == "usage", fault


def test_model_vocabulary_offers_exactly_the_five_archaeology_reads(arch_corpus) -> None:
    env = _arch_env(arch_corpus)
    vocab_script = (
        f"import sys, json; sys.path.insert(0, {str(REPO_ROOT)!r});\n"
        "from core.command_registry.registry import registry\n"
        "registry()  # builds the command registry and projects model tools\n"
        "from core.tool_registry import registry_map\n"
        "print(json.dumps(sorted(i for i in registry_map() if i.startswith('operator.command.archaeology'))))"
    )
    vocab = _run_py(vocab_script, env)
    assert vocab.returncode == 0, vocab.stderr[-400:]
    offered = json.loads(vocab.stdout)
    assert set(offered) == {f"operator.command.{cid}" for cid in ARCHAEOLOGY_COMMANDS}, offered
    for intent in offered:
        for verb in MUTATION_VERBS:
            assert verb not in intent, (intent, verb)


# -- 4. read-only through the door ---------------------------------------------


def test_doors_leave_the_journal_at_nine_entries_and_still_verifying(arch_corpus) -> None:
    env = _arch_env(arch_corpus)
    _boot_subprocess_home(env)

    cli = subprocess.run(
        [sys.executable, "-c", CLI_MAIN,
         "archaeology.search", "--json", "--text", "pre-receive"],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=120,
    )
    assert cli.returncode == 0, cli.stderr[-600:]
    assert json.loads(cli.stdout)["ok"] is True, cli.stdout

    seam = _run_py(
        _model_door_script("operator.command.archaeology.locate", {"reference": "eff-push-77"}),
        env,
    )
    assert seam.returncode == 0, seam.stderr[-600:]
    assert json.loads(seam.stdout)["ok"] is True, seam.stdout

    # The doors ran for real; the authoritative journal is untouched and intact.
    assert journal_count(arch_corpus["blackbox_root"]) == 9
    from core.blackbox.store import BlackboxStore

    report = BlackboxStore(arch_corpus["blackbox_root"]).verify()
    assert report.ok is True, report
