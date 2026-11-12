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
    from core import tool_demand_signals

    tool_demand_signals.reset_plugin_spoken_memory()
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
    "how many words are in: red green blue",          # real chat 2026-11-12: never offered, model miscounted 6 of 6
    "number of words in: red green blue",
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
    "how many files are in my folder",
    "how many characters are in this text",
    "which words rhyme with orange",
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
    # Removed while the app runs: said so (real chat 2026-11-12: silence here got an invented fingerprint).
    assert lane.unavailable_plugin_notice(ask) == lane.REMOVED.format(name="Text digest example")
    assert lane.unavailable_plugin_notice("what's a good name for a cat?") == ""


def test_a_fingerprint_repeated_exactly_counts_as_grounded() -> None:
    """Real chat 2026-11-12: the plugin returned the fingerprint as colon-separated bytes, the
    model repeated it, and the answer was refused because no 5-character piece was shared."""
    import hashlib

    value = hashlib.sha256(b"vx-7781").digest().hex(":")
    shown = ["[untrusted output]\n" + json.dumps({"sha256": value, "bytes": 7})]
    assert not is_ungrounded(f"The fingerprint of that text is {value} as asked.", shown)
    assert not is_ungrounded(f"The fingerprint of that text is {value.replace(':', '')} as asked.", shown)
    invented = hashlib.sha256(b"something else").digest().hex(":")
    assert is_ungrounded(f"The fingerprint of that text is {invented} as asked.", shown)


def test_a_refused_wording_still_shows_what_the_plugin_returned(two_enabled_packages) -> None:
    """The plugin's result was in hand; the person was shown "Incomplete ... returned output" instead."""
    from core.agent_runtime.research_tool_loop_facade import plugin_result_in_plain_words

    step = {"tool_name": "acme-word-count.count", "ok": True,
            "summary": "Plugin `acme-word-count.count` returned output; its claims need independent verification.",
            "response_text": "[untrusted output from MCP server 'acme-word-count' tool 'count']\n{\"words\": 4, \"characters\": 19}"}
    said = plugin_result_in_plain_words([step])
    # Shown as literal text inside a fence, so a plugin's words never become links or headings.
    assert said == "This is what the Word count plugin returned. VOOL has not checked it.\n\n```text\nwords: 4\ncharacters: 19\n```"
    marked = plugin_result_in_plain_words([dict(step, response_text="```\n[click](http://x.example)\n# Title")])
    assert marked.count("```") == 2 and marked.endswith("```")            # the fence cannot be closed from inside
    # Another tool in the turn, or a failed step before it: the last step is not the whole answer.
    assert plugin_result_in_plain_words([{"tool_name": "search_web", "ok": True, "response_text": "x"}, step]) == ""
    assert plugin_result_in_plain_words([dict(step, ok=False), step]) == ""
    other = dict(step, tool_name="acme-text-digest.sha256")
    assert plugin_result_in_plain_words([other, step]) == ""
    assert plugin_result_in_plain_words([dict(step, ok=False)]) == ""
    assert plugin_result_in_plain_words([dict(step, tool_name="search_web")]) == ""
    assert plugin_result_in_plain_words([]) == ""


def test_a_short_follow_up_keeps_the_plugin_on_offer(two_enabled_packages) -> None:
    """Real chat 2026-11-12: "and characters?" after a word count was read alone, got no tool,
    and the model asked what "characters" meant."""
    from core.execution_requirements import requirements_for
    from core.tool_demand_signals import plugin_follow_up

    def chat(*said):
        return {"conversation_history": [m for text in said for m in ({"role": "user", "content": text}, {"role": "assistant", "content": "ok"})][:-1]}

    first = "count the words in: red green blue"
    assert plugin_follow_up("and characters?", chat(first, "and characters?"))
    assert plugin_follow_up("now do the same for vx-22", chat(first, "and characters?", "now do the same for vx-22"))
    assert not plugin_follow_up("and characters?", chat("what's a good name for a cat?", "and characters?"))
    assert not plugin_follow_up("thanks, that helps", chat(first, "thanks, that helps"))
    assert not plugin_follow_up("what is the weather in Vilnius tomorrow", chat(first, "what is the weather in Vilnius tomorrow"))
    assert not plugin_follow_up("and characters?", chat(first, "what's a good name for a cat?", "and characters?"))
    assert not plugin_follow_up("and characters?", None)
    wanted = requirements_for("and characters?", source_context=chat(first, "and characters?"))
    assert wanted.tools_required and wanted.allowed_toolsets == ("plugin",)


def test_a_follow_up_is_only_a_continuation_of_the_plugin_turn(two_enabled_packages) -> None:
    """Review 2026-11-12: any short turn after a plugin turn was pulled to the plugin, so
    "tell me a joke about cats" was forced through a tool call."""
    from core.execution_requirements import requirements_for
    from core.tool_demand_signals import plugin_follow_up, plugin_follow_up_targets

    def chat(*said):
        return {"conversation_history": [m for text in said for m in ({"role": "user", "content": text}, {"role": "assistant", "content": "ok"})][:-1]}

    first = "count the words in: red green blue"
    for ordinary in ("tell me a joke about cats", "and what is the capital of France?", "ok", "thanks!",
                     "how are you today"):
        assert not plugin_follow_up(ordinary, chat(first, ordinary)), ordinary
    for continued in ("ok now do the same for vx-22", "same for: one two three", "and characters?",
                      "do it again for alpha beta"):
        assert plugin_follow_up_targets(continued, chat(first, continued)) == ("acme-word-count",), continued
    # An acknowledgement in between is stepped over; an ordinary turn in between ends it.
    assert plugin_follow_up("and characters?", chat(first, "ok thanks", "and characters?"))
    assert not plugin_follow_up("same for vx-22", chat(first, "tell me a joke about cats", "same for vx-22"))
    # A follow-up another rule owns stays with that rule.
    weather = "and what is the weather in Vilnius tomorrow?"
    wanted = requirements_for(weather, source_context=chat(first, weather))
    assert wanted.allowed_toolsets != ("plugin",)


def test_a_removed_plugin_is_named_only_when_the_person_named_it(two_enabled_packages) -> None:
    """Review 2026-11-12: after a removal, an everyday sentence sharing one word with the removed
    plugin's description was answered with "that plugin was removed"."""
    from core.agent_runtime import fast_paths_plugin as lane
    from core.plugin_tools import set_plugin_tools_setting
    from core.tool_demand_signals import removed_plugins_named_in_words

    set_plugin_tools_setting(True)
    assert lane.unavailable_plugin_notice("count the words in: red green blue") == ""     # seen while present
    manager.uninstall("acme-word-count", remove_key=True)
    assert removed_plugins_named_in_words("use the word count plugin on: red green blue") == ("Word count",)
    for everyday in ("how many words should a cover letter have?", "what characters are in Hamlet?",
                     "count me in for friday"):
        assert removed_plugins_named_in_words(everyday) == (), everyday
        assert lane.unavailable_plugin_notice(everyday) == "", everyday


def test_a_follow_up_to_a_plugin_that_was_turned_off_is_explained(two_enabled_packages) -> None:
    """Review 2026-11-12: the notice read only the turn's own words, so "and characters?" after
    the plugin was turned off went to the model with no tool and no explanation."""
    from core.agent_runtime import fast_paths_plugin as lane
    from core.plugin_tools import set_plugin_tools_setting

    set_plugin_tools_setting(True)
    first = "count the words in: red green blue"
    context = {"conversation_history": [{"role": "user", "content": first}, {"role": "assistant", "content": "3"},
                                        {"role": "user", "content": "and characters?"}]}
    assert lane.unavailable_plugin_notice("and characters?", context) == ""
    manager.disable("acme-word-count")
    assert lane.unavailable_plugin_notice("and characters?", context) == lane.TURNED_OFF.format(name="Word count")
    assert lane.unavailable_plugin_notice("thanks!", context) == ""


def test_a_labelled_or_short_value_from_the_tool_counts_as_grounded() -> None:
    """Review 2026-11-12: "md5:<hex>" and a dotted address repeated exactly were refused."""
    value = "9e:10:7d:9d:37:2b:b6:82:6b:d8:1d:35:42:a4:19:d6"
    shown = ['[untrusted output]\n{"sum": "md5:' + value + '", "ip": "10.0.12.7"}']
    assert not is_ungrounded(f"The value it gave back is {value} for that text.", shown)
    assert not is_ungrounded("The value it gave back is 10.0.12.7 for that host.", shown)
    assert is_ungrounded("The value it gave back is 10.0.99.7 for that host.", shown)
    assert is_ungrounded("The value it gave back is 9e:10:7d:9d:00:00:00:00 for that text.", shown)
