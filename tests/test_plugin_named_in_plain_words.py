"""A person names an added plugin in plain words; nobody types a dotted tool name.

RED (2026-11-11, real chat, local model, two reviewed and enabled packages, chat tools on):
"What is the SHA-256 fingerprint of the text ...?" and "count the words in: ..." never reached a
lane with tools, because the only words that named a plugin were its dotted intent token. The
model answered alone and invented a fingerprint. The owning seam is the demand signal: the words
now say WHETHER the plugin family is wanted, from the closed vocabulary of the reviewed packages'
own names and descriptions. No tool is singled out, and a request another rule already owns is
left where it was.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from core import plugin_manager as manager
from core.model_output_guard import is_ungrounded
from core.tool_demand_signals import resolve_demand_signals


def _package(root: Path, plugin_id: str, name: str, description: str, tool: str, tool_description: str) -> Path:
    block = {
        "name": plugin_id, "version": "1.0.0", "description": description,
        "vool": {
            "id": plugin_id, "version": "1.0.0", "name": name, "publisher": "Example", "license": "MIT",
            "description": description, "homepage": "https://example.org/", "kind": ["tools"],
            "network": [], "credentials": {},
            "mcpServers": {"local": {"command": "bin/server", "args": [], "env": {}}},
            "tools": [{
                "intent": f"{plugin_id}.{tool}", "description": tool_description,
                "input_schema": {"type": "object", "required": ["text"], "additionalProperties": False,
                                 "properties": {"text": {"type": "string", "maxLength": 4096}}},
                "handler": {"kind": "mcp", "server": "local", "tool": tool, "timeout_seconds": 10},
                "trust": {"side_effect_class": "read_only", "permission_actions": ["read_files"]},
            }],
        },
    }
    (root / ".codex-plugin").mkdir(parents=True)
    (root / "bin").mkdir()
    (root / ".codex-plugin/plugin.json").write_text(json.dumps(block))
    (root / "bin/server").write_text("#!/bin/sh\nexit 0\n")   # never started by these tests
    (root / "bin/server").chmod(0o755)
    (root / "LICENSE").write_text("MIT License\n")
    return root


@pytest.fixture
def two_enabled_packages(tmp_path, monkeypatch):
    from core import capability_graph, tool_registry

    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "profile"))
    monkeypatch.setenv("VOOL_PLUGIN_LIFECYCLE_PATH", str(tmp_path / "lifecycle.json"))
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(tmp_path / "installed"))
    monkeypatch.setenv("VOOL_PLUGIN_SCRATCH_ROOT", str(tmp_path / "scratch"))
    monkeypatch.setenv("VOOL_CREDENTIAL_STORE", "vault")
    tool_registry.reset()
    try:
        for plugin_id, name, description, tool, tool_description in (
            ("acme-text-digest", "Text digest example", "Compute the SHA-256 digest of supplied text.",
             "sha256", "Compute the SHA-256 fingerprint as hexadecimal bytes and the byte length of the supplied text."),
            ("acme-word-count", "Word count", "Count the words and characters in supplied text.",
             "count", "Count the words and characters in the supplied text."),
        ):
            source = _package(tmp_path / plugin_id, plugin_id, name, description, tool, tool_description)
            review = manager.prepare_local(str(source), update=False)
            info = manager.install(review["review_id"], accepted=True, digest=review["digest"])["plugin"]
            manager.enable(info["id"], accepted=True, approval_digest=info["approval_digest"])
        yield
    finally:
        tool_registry.reset()
        capability_graph.reset()
        capability_graph.init_graph()
        capability_graph.bootstrap_from_registry()
        shutil.rmtree(tmp_path / "installed", ignore_errors=True)


@pytest.mark.parametrize("said", [
    "What is the SHA-256 fingerprint of the text vx-81ac3 ?",
    "fingerprint for vx-0091",
    "sha256 fingerprnt of vx-5512 pls",
    "fingerpint of abc",
    "use the text digest plugin on abc",
    "Count the words and characters with the word count plugin: red green blue",
    "cna u count teh words in: red green blue",
    "wrd count of: red green blue",
    "how many words and characters: red green blue",
])
def test_plain_words_for_an_added_plugin_ask_for_the_plugin_family(two_enabled_packages, said) -> None:
    signals = resolve_demand_signals(said)
    assert "plugin" in signals.required_families, signals
    # Which tool runs stays the model's choice among the tools the owner switched on.
    assert signals.explicit_intents == ()


@pytest.mark.parametrize("said", [
    "count the files in my folder",
    "what is the weather in Vilnius tomorrow",
    "how long is this text",
    "and characters?",
    "hello there",
    "summarise this article for me",
])
def test_ordinary_requests_are_left_where_they_were(two_enabled_packages, said) -> None:
    assert "plugin" not in resolve_demand_signals(said).required_families


def test_a_request_another_rule_owns_is_not_pulled_to_the_plugin(two_enabled_packages) -> None:
    signals = resolve_demand_signals("create a file called words.txt and count the words and characters in it")
    assert signals.explicit_intents and "plugin" not in signals.required_families, signals


def test_the_dotted_name_still_seats_its_own_tool(two_enabled_packages) -> None:
    signals = resolve_demand_signals("use the acme-word-count.count tool on: red green blue")
    assert signals.explicit_intents == ("acme-word-count.count",) and signals.required_families == ("plugin",)


def test_an_answer_built_from_the_tool_result_counts_as_grounded() -> None:
    """The activity label of a plugin step says nothing of its result; judged by the label alone,
    the correct answer was refused as ungrounded and the person was shown the label."""
    from core.agent_runtime.research_tool_loop_facade import synthesis_grounding_observations

    label = "Plugin `acme-word-count.count` returned output; its claims need independent verification."
    step = {"summary": label, "response_text": "[untrusted output]\n{\"words\": 4, \"characters\": 19}",
            "observation": {"tool_surface": "plugin", "ok": True, "status": "executed"}}
    answer = "The text has 4 words and 19 characters in total."
    assert is_ungrounded(answer, [label, json.dumps(step["observation"])])          # what the gate read before
    assert not is_ungrounded(answer, synthesis_grounding_observations([step]))      # what the model was shown


def test_a_plugin_that_cannot_run_is_explained_before_any_model_answers(two_enabled_packages) -> None:
    """Chat tools off, plugin off, or files changed after review: the person is told which and
    where to fix it. Left to a model with no such tool, the turn got an invented fingerprint."""
    from core import plugin_lifecycle as lifecycle
    from core.agent_runtime import fast_paths_plugin as lane
    from core.plugin_tools import set_plugin_tools_setting

    ask = "What is the SHA-256 fingerprint of the text vx-7781 ?"
    set_plugin_tools_setting(True)
    assert lane.unavailable_plugin_notice(ask) == ""                      # it can run: the model gets the tool
    set_plugin_tools_setting(False)
    assert lane.unavailable_plugin_notice(ask) == lane.SWITCHED_OFF.format(name="Text digest example")
    assert lane.unavailable_plugin_notice("what's a good name for a cat?") == ""
    set_plugin_tools_setting(True)
    manager.disable("acme-text-digest")
    assert lane.unavailable_plugin_notice("fingerpint of abc pls") == lane.TURNED_OFF.format(name="Text digest example")
    assert lane.unavailable_plugin_notice("use the acme-text-digest.sha256 tool on abc") == lane.TURNED_OFF.format(name="Text digest example")
    assert lane.unavailable_plugin_notice("how many words and characters: red green blue") == ""   # the other one still runs
    info = manager.inspect("acme-text-digest")
    manager.enable("acme-text-digest", accepted=True, approval_digest=info["approval_digest"])
    server = Path(lifecycle.record_for("acme-text-digest").root) / "bin/server"
    server.parent.chmod(0o755)
    server.chmod(0o755)
    server.write_text(server.read_text() + "\n# changed after review\n")
    changed = lane.CHANGED.format(name="Text digest example")
    assert lane.unavailable_plugin_notice(ask) == changed
    assert lane.unavailable_plugin_notice("fingerprint for vx-2") == changed      # still said on the next ask
    manager.uninstall("acme-text-digest", remove_key=True)
    assert lane.unavailable_plugin_notice(ask) == ""                              # gone: nothing of it remains to name
