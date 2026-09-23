"""TOOLSMITH lane: local/cloud caller-provenance parity for the workspace tool dispatcher.

PRIMARY PRODUCT CONTRACT: once a normalized tool call reaches `execute_runtime_tool`, the result
must be identical regardless of whether the calling model is local, cloud, strong, weak, free, or
tool-native. RELAY owns the provider-adapter layer that TRANSLATES each provider's wire format into
this normalized call; this test starts from that normalized call and verifies the dispatcher itself
never branches on caller identity.

Concretely: `source_context` is the only channel a caller identity could leak through into the tool
layer. This drives the identical intent + arguments through `execute_runtime_tool` three times, once
per synthetic caller profile (a bare test harness, a configured local Ollama model, and a configured
free OpenRouter model), with the caller-identity fields a real provider adapter would attach
(`provider`, `model`, `caller_kind`, `tool_native`) ACTUALLY PRESENT in `source_context` alongside
`workspace` -- so a dispatcher that ever branched on them would be caught, not accidentally missed.

Each profile runs against the SAME absolute workspace path, reseeded to identical content between
profiles (rather than three different temp directories), specifically so no output can differ merely
because it happens to embed the workspace's own absolute path (e.g. `workspace.git_status`'s "not a
git repository" message names the resolved path) -- that would be a test artifact, not a real
dispatcher divergence. The only fields allowed to differ between runs are per-call identities,
unrelated to caller identity: `mutation_id` (a fresh uuid4 per call) and the blackbox record's
`turn_id` (`direct:<ledger>-<uuid4>`, minted once per effect ledger when no turn identity rides the
source context -- a direct library call mints its own, by `core.blackbox.identity`); everything
else must match byte-for-byte.
"""

from __future__ import annotations

import copy
import shutil
import tempfile
import unittest
from pathlib import Path

from core.runtime_execution_tools import execute_runtime_tool

CALLER_PROFILES = [
    {"caller_kind": "test_harness"},
    {"caller_kind": "local_model", "provider": "ollama", "model": "qwen2.5:7b", "tool_native": False},
    {"caller_kind": "cloud_model", "provider": "openrouter", "model": "meta-llama/llama-3.1-8b-instruct:free", "tool_native": True},
]


def _seed_fixture(root: Path) -> None:
    for child in root.iterdir():
        shutil.rmtree(child) if child.is_dir() else child.unlink()
    (root / "src").mkdir()
    (root / "README.md").write_text("Hello\nWorld\n", encoding="utf-8")
    (root / "src" / "app.py").write_text("def greet():\n    return 'hi'\n", encoding="utf-8")


def _strip_nondeterministic(details: dict) -> dict:
    """Remove the fields allowed to legitimately vary per call, all of them identities.

    Two sources, both per-call and neither derived from caller provenance. The mutation ledger's
    uuid4 surfaces in three shapes depending on which handler produced it: nested under
    `mutation_record` (write_file/replace_in_file/apply_unified_diff), as a bare top-level
    `mutation_id` (rollback_last_change's own details), and inside `observation` -- all three are
    the SAME uuid4, not three different sources of real divergence. The blackbox record carries
    its scope's identities: `turn_id` (`direct:<ledger>-<uuid4>` minted per effect ledger when no
    turn identity rides the source context, by `core.blackbox.identity`) and `effect_ids`
    (`effect-<uuid4>` per recorded effect, which also KEY the `outcomes` dict). The identities go;
    the content beside them stays compared -- how many effects were recorded, the multiset of
    their outcomes, whether bytes were captured, coverage gaps, recovery errors.
    """
    clean = copy.deepcopy(details)
    clean.pop("mutation_id", None)
    mutation_record = clean.get("mutation_record")
    if isinstance(mutation_record, dict):
        mutation_record.pop("mutation_id", None)
    observation = clean.get("observation")
    if isinstance(observation, dict):
        observation.pop("mutation_id", None)
    for artifact in clean.get("artifacts") or []:
        if isinstance(artifact, dict):
            artifact.pop("mutation_id", None)
    blackbox = clean.get("blackbox")
    if isinstance(blackbox, dict):
        effect_ids = blackbox.pop("effect_ids", None)
        blackbox.pop("turn_id", None)
        if effect_ids is not None:
            blackbox["effect_count"] = len(effect_ids)
        outcomes = blackbox.get("outcomes")
        if isinstance(outcomes, dict):
            # Keyed by the per-effect uuids removed above; the outcomes themselves are content.
            blackbox["outcomes"] = sorted(map(str, outcomes.values()))
    return clean


def _run_sequence(workspace: str, profile: dict) -> list[tuple[bool, str, str, dict]]:
    """The identical normalized call sequence every caller profile drives. `profile`'s
    caller-identity fields ride along in `source_context` right next to `workspace` -- exactly
    where a real provider adapter would attach them -- so this proves they are actually inert,
    not merely absent from the test."""
    source_context = {"workspace": workspace, **{k: v for k, v in profile.items() if k != "caller_kind"}}
    calls = [
        ("workspace.list_tree", {}),
        ("workspace.read_file", {"path": "src/app.py"}),
        ("workspace.git_status", {}),
        ("workspace.replace_in_file", {"path": "src/app.py", "old_text": "hi", "new_text": "hello"}),
        (
            "workspace.apply_unified_diff",
            {
                "patch": (
                    "--- a/README.md\n+++ b/README.md\n@@ -1,2 +1,2 @@\n"
                    "-Hello\n+Hello there\n World\n"
                )
            },
        ),
        ("workspace.rollback_last_change", {}),
        ("workspace.rollback_last_change", {}),
    ]
    results = []
    for intent, arguments in calls:
        result = execute_runtime_tool(intent, arguments, source_context=source_context)
        assert result is not None, f"{intent} was not handled by the dispatcher"
        results.append((result.ok, result.status, result.response_text, _strip_nondeterministic(result.details)))
    return results


class LocalCloudParityTests(unittest.TestCase):
    def test_identical_tool_calls_produce_identical_results_across_caller_profiles(self) -> None:
        per_profile_results = {}
        with tempfile.TemporaryDirectory() as workspace:
            for profile in CALLER_PROFILES:
                _seed_fixture(Path(workspace))
                per_profile_results[profile["caller_kind"]] = _run_sequence(workspace, profile)

        baseline_kind = CALLER_PROFILES[0]["caller_kind"]
        baseline = per_profile_results[baseline_kind]
        for profile in CALLER_PROFILES[1:]:
            caller_kind = profile["caller_kind"]
            with self.subTest(caller=caller_kind):
                self.assertEqual(
                    per_profile_results[caller_kind],
                    baseline,
                    f"tool dispatch result differs for caller_kind={caller_kind!r} vs {baseline_kind!r} "
                    "-- caller provenance must never change a tool result",
                )

    def test_provenance_fields_placed_in_source_context_are_ignored_by_workspace_root_resolution(self) -> None:
        """Sabotage target: if a future change makes `_workspace_root` (or any tool handler) branch
        on `source_context["provider"]`/`["model"]`/`["caller_kind"]`, this must fail. Two calls with
        the SAME workspace but DIFFERENT provenance metadata attached must resolve to the same root
        and return the same read."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _seed_fixture(Path(tmpdir))

            local_ctx = {"workspace": tmpdir, "provider": "ollama", "model": "qwen2.5:7b", "caller_kind": "local_model"}
            cloud_ctx = {"workspace": tmpdir, "provider": "openrouter", "model": "some-free-model:free", "caller_kind": "cloud_model", "tool_native": True}

            local_result = execute_runtime_tool("workspace.read_file", {"path": "README.md"}, source_context=local_ctx)
            cloud_result = execute_runtime_tool("workspace.read_file", {"path": "README.md"}, source_context=cloud_ctx)

            assert local_result is not None and cloud_result is not None
            self.assertEqual(local_result.ok, cloud_result.ok)
            self.assertEqual(local_result.status, cloud_result.status)
            self.assertEqual(local_result.response_text, cloud_result.response_text)
            self.assertEqual(local_result.details["hash"], cloud_result.details["hash"])
            self.assertEqual(local_result.details, cloud_result.details)


if __name__ == "__main__":
    unittest.main()
