"""Plugin and MCP mutation proof: registered third-party tools that mutate local state run
between Blackbox observations through the executor's own seam -- and undeclared ones never run."""
from __future__ import annotations

import hashlib
from pathlib import Path

from tests.blackbox_coverage._ctx import ctx
def _plugin_contract(intent="demo.writer", *, mutation, side_effect_class="workspace_write"):
    from core.runtime_tool_contracts import RuntimeToolContract

    return RuntimeToolContract(
        intent=intent,
        description="demo plugin writer",
        tool_surface="plugin",
        capability_id=intent,
        capability_claim="writes",
        supported=True,
        unsupported_reason="",
        input_schema={"path": "str"},
        output_schema={"ok": "bool"},
        side_effect_class=side_effect_class,
        approval_requirement="manual",
        timeout_policy="60s",
        retry_policy="none",
        artifact_emission="none",
        error_contract="typed",
        handler="plugin",
        source="plugin:demo",
        mutation=mutation,
    )


SCAN_DECLARATION = dict(
    scope="workspace",
    effect_class="reversible",
    snapshot_strategy="workspace_scan",
    receipt_lifecycle="intent_then_terminal",
    rollback_support="exact",
    recorder="blackbox.coverage_scan",
)


def _dispatch(workspace: Path, intent: str, arguments: dict, handler):
    from core.tool_intent_executor import _with_blackbox_coverage

    return _with_blackbox_coverage(
        intent,
        arguments,
        source_context=ctx(workspace),
        dispatch=handler,
    )


class TestPluginMutation:
    def test_plugin_writes_journal_between_observations(self, workspace, store_dir):
        from core.tool_registry import register

        register(_plugin_contract(mutation={"tool": "demo.writer", **SCAN_DECLARATION}))
        seed = workspace / "plugin-seed.txt"
        seed.write_bytes(b"plugin-before")

        def handler():
            seed.write_bytes(b"plugin-after")
            (workspace / "plugin-new.txt").write_bytes(b"created")

            from core.tool_intent_executor import ToolIntentExecution

            return ToolIntentExecution(
                handled=True, ok=True, status="executed", response_text="done",
                user_safe_response_text="done", mode="tool_executed", tool_name="demo.writer",
                details={"executed": True},
            )

        result = _dispatch(workspace, "demo.writer", {"path": str(seed)}, handler)
        assert result.ok, result.response_text
        from core.blackbox.store import default_store

        store = default_store()
        terminals = [e for e in store.entries() if e.get("kind") == "coverage_scan_terminal"]
        assert terminals
        keyring = store.cas_keyring
        drift = {row["path"]: row for row in terminals[-1]["drift"]}
        assert "sha256" not in drift["plugin-seed.txt"]["before"]
        assert drift["plugin-seed.txt"]["before"]["content_id"] == keyring.id_for(hashlib.sha256(b"plugin-before").hexdigest())
        assert drift["plugin-seed.txt"]["after"]["content_id"] == keyring.id_for(hashlib.sha256(b"plugin-after").hexdigest())
        assert drift["plugin-seed.txt"]["before"]["blob"], "preimage blob in the CAS"
        assert drift["plugin-new.txt"]["drift_kind"] == "created"
        assert terminals[-1]["rollback_capable"] is True
        assert store.verify().ok

    def test_seated_contract_that_lost_its_capability_never_runs(self, workspace, store_dir):
        """Registration is the primary gate (a mutating contract with no declaration cannot even
        register). The dispatch gate is the second door: a REGISTERED mutating contract whose
        capability seat went missing mid-process refuses instead of running unrecorded."""
        from core.blackbox.coverage import registry as coverage_registry
        from core.tool_intent_executor import ToolIntentExecution
        from core.tool_registry import register

        register(_plugin_contract(intent="demo.ghost", mutation={"tool": "demo.ghost", **SCAN_DECLARATION}))
        assert coverage_registry.unregister_capability("demo.ghost") is True  # the seat is gone

        def handler():
            (workspace / "should-not-exist.txt").write_bytes(b"no")
            return ToolIntentExecution(handled=True, ok=True, status="executed", response_text="",
                                      user_safe_response_text="", mode="tool_executed",
                                      tool_name="demo.ghost", details={})

        result = _dispatch(workspace, "demo.ghost", {}, handler)
        assert result.ok is False
        assert result.status == "blackbox_coverage_required"
        assert result.details["executed"] is False
        assert not (workspace / "should-not-exist.txt").exists()


class TestMcpMutation:
    def test_mcp_local_write_is_journaled(self, workspace, store_dir):
        """MCP tools reach the same seam (execute_mcp_intent dispatches under the wrapper); the
        proof drives the seam with a real mcp.*-shaped registered contract, no live server."""
        from core.tool_registry import register

        register(
            _plugin_contract(
                intent="mcp.demo.writeFile",
                mutation={"tool": "mcp.demo.writeFile", **SCAN_DECLARATION},
            )
        )

        def handler():
            (workspace / "mcp-out.txt").write_bytes(b"mcp-bytes")

            from core.tool_intent_executor import ToolIntentExecution

            return ToolIntentExecution(
                handled=True, ok=True, status="executed", response_text="done",
                user_safe_response_text="done", mode="tool_executed", tool_name="mcp.demo.writeFile",
                details={"executed": True},
            )

        result = _dispatch(workspace, "mcp.demo.writeFile", {}, handler)
        assert result.ok
        from core.blackbox.store import default_store

        terminals = [e for e in default_store().entries() if e.get("kind") == "coverage_scan_terminal"]
        assert terminals
        keyring = default_store().cas_keyring
        drift = {row["path"]: row for row in terminals[-1]["drift"]}
        assert drift["mcp-out.txt"]["drift_kind"] == "created"
        assert drift["mcp-out.txt"]["after"]["content_id"] == keyring.id_for(hashlib.sha256(b"mcp-bytes").hexdigest())

    def test_declared_irreversible_mcp_local_effect_journals_postimage_only(self, workspace, store_dir):
        from core.tool_registry import register

        register(
            _plugin_contract(
                intent="demo.cleanup",
                side_effect_class="workspace_write",
                mutation=dict(
                    tool="demo.cleanup",
                    scope="machine",
                    effect_class="irreversible",
                    snapshot_strategy="postimage_only",
                    receipt_lifecycle="terminal_only",
                    rollback_support="none",
                    recorder="blackbox.coverage_post",
                ),
            )
        )

        def handler():
            (workspace / "gone.tmp").unlink(missing_ok=True)

            from core.tool_intent_executor import ToolIntentExecution

            return ToolIntentExecution(
                handled=True, ok=True, status="executed", response_text="done",
                user_safe_response_text="done", mode="tool_executed", tool_name="demo.cleanup",
                details={"removed_paths": [str(workspace / "gone.tmp")]},
            )

        (workspace / "gone.tmp").write_bytes(b"temp")
        result = _dispatch(workspace, "demo.cleanup", {}, handler)
        assert result.ok
        from core.blackbox.store import default_store

        posts = [e for e in default_store().entries() if e.get("kind") == "coverage_post_terminal"]
        assert posts
        assert posts[-1]["rollback_capable"] is False, "an irreversible effect never claims rollback"


class TestCodingLaneShape:
    def test_the_future_coding_assistant_tool_registers_through_the_same_gate(self, workspace, store_dir):
        """The coding-assistant lane registers its editor through tool_registry like any plugin:
        one declaration, and its writes are journaled by the same coverage seam."""
        from core.tool_registry import register

        register(
            _plugin_contract(
                intent="assist.edit_file",
                mutation=dict(
                    tool="assist.edit_file",
                    scope="workspace",
                    effect_class="reversible",
                    snapshot_strategy="declared_paths",
                    receipt_lifecycle="intent_then_terminal",
                    rollback_support="exact",
                    recorder="blackbox.coverage_declared",
                ),
            )
        )
        target = workspace / "code.py"
        target.write_bytes(b"print('before')\n")

        def handler():
            target.write_bytes(b"print('after')\n")

            from core.tool_intent_executor import ToolIntentExecution

            return ToolIntentExecution(
                handled=True, ok=True, status="executed", response_text="edited",
                user_safe_response_text="edited", mode="tool_executed", tool_name="assist.edit_file",
                details={"executed": True, "path": str(target)},
            )

        result = _dispatch(workspace, "assist.edit_file", {"path": str(target)}, handler)
        assert result.ok
        from core.blackbox.store import default_store

        entries = default_store().entries()
        intended = [e for e in entries if e.get("kind") == "effect_intended" and e.get("path") == str(target)]
        terminal = [e for e in entries if e.get("kind") == "effect_terminal" and e.get("path") == str(target)]
        assert intended and terminal
        keyring = default_store().cas_keyring
        assert "sha256" not in intended[0]["before"]
        assert intended[0]["before"]["content_id"] == keyring.id_for(hashlib.sha256(b"print('before')\n").hexdigest())
        assert terminal[0]["after"]["content_id"] == keyring.id_for(hashlib.sha256(b"print('after')\n").hexdigest())
