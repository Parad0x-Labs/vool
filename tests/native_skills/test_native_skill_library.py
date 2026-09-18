"""The native skill library contract: 13 first-party VOOL skill packages, shipped in `skills/`.

VOOL had exactly two native skill packages (media-studio, vool-hive-mind) and both predate the
library contract — they declare capabilities but no triggers, effects, stop conditions, recovery or
test law, and the loader DROPPED every field it did not already know. The P1 library law is that a
first-party package is a complete, versioned, machine-checkable contract:

    name / description (trigger sentence) / version / triggers / allowed-tools / capabilities /
    permissions / effects / inputs / outputs / stop-conditions / recovery / cumulative-test-law

and that every tool a package names is a REAL contract intent of this runtime (a skill naming a
tool the runtime does not have narrows the model to nothing on the turn it matches — the loader's
own validate already treats that as a defect), and every permission it declares covers what its
tools actually require.

These tests read the shipped packages through the ONE loader authority (`core.plugin_skills`);
nothing here re-implements discovery.
"""
from __future__ import annotations

import re

import pytest

from core.native_skill_library import native_skills_root
from core.plugin_skills import parse_skill


def native_skills():
    """The shipped packages as parsed records, via the CANONICAL root (reconciliation:
    core.plugin_skills no longer carries a second loader)."""
    root = native_skills_root()
    return [
        parse_skill(entry / "SKILL.md", plugin_id="native")
        for entry in sorted(root.iterdir())
        if entry.is_dir() and (entry / "SKILL.md").is_file()
    ]
from core.skill_tools import validate_skill

EXPECTED_VOOL_PACKAGES = (
    "vool-repo-onboarding",
    "vool-root-cause-repair",
    "vool-bug-reproduction",
    "vool-feature-build",
    "vool-cumulative-testing",
    "vool-code-review",
    "vool-security-audit",
    "vool-git-worktrees",
    "vool-ci-repair",
    "vool-browser-qa",
    "vool-performance",
    "vool-migration",
    "vool-release-gate",
)

CONTRACT_LIST_FIELDS = (
    "triggers",
    "allowed_tools",
    "capabilities",
    "permissions",
    "effects",
    "inputs",
    "outputs",
    "stop_conditions",
    "recovery",
    "cumulative_test_law",
)

_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def _by_name() -> dict[str, object]:
    return {skill.name: skill for skill in native_skills()}


def test_all_thirteen_vool_packages_ship_and_parse() -> None:
    names = {skill.name for skill in native_skills()}
    missing = [slug for slug in EXPECTED_VOOL_PACKAGES if slug not in names]
    assert not missing, f"native library is missing first-party packages: {missing}"


def test_the_two_preexisting_packages_still_parse() -> None:
    """The library seam is additive: media-studio and vool-hive-mind must survive it untouched."""
    names = {skill.name for skill in native_skills()}
    assert {"media-studio", "vool-hive-mind"} <= names


def test_native_root_is_the_repository_skills_directory() -> None:
    root = native_skills_root()
    assert root.is_dir()
    assert (root / "vool-repo-onboarding" / "SKILL.md").is_file()


@pytest.mark.parametrize("slug", EXPECTED_VOOL_PACKAGES)
def test_every_vool_package_carries_the_full_contract(slug: str) -> None:
    skill = _by_name()[slug]
    assert skill.description.strip(), f"{slug}: empty description can never rank"
    assert skill.version and _VERSION_RE.match(skill.version), f"{slug}: not versioned"
    for field in CONTRACT_LIST_FIELDS:
        value = getattr(skill, field)
        assert value, f"{slug}: contract field {field} is empty"


def test_native_packages_carry_the_native_plugin_id_and_path() -> None:
    for skill in native_skills():
        assert skill.plugin_id == "native", skill.name
        assert str(native_skills_root()) in skill.path, skill.name


def test_every_named_tool_is_a_real_runtime_intent() -> None:
    """A skill naming a tool this runtime does not have would narrow a matched turn to nothing.

    Real means: a builtin contract, a contract registered by an installed plugin (the
    canonical registry), or a tool declared by a plugin pack SHIPPED IN THIS REPOSITORY —
    whose manifest is the same declaring file the loader reads, and whose skill honestly
    names its install prerequisite. Anything else is exactly the drift this test exists to
    catch: a matched turn narrowed to nothing.
    """
    import json
    from pathlib import Path

    from core.runtime_tool_contracts import runtime_tool_contract_map

    known = set(runtime_tool_contract_map())
    try:
        from core.tool_registry import registered_tools

        known |= {str(contract.intent) for contract in registered_tools()}
    except Exception:
        pass
    repo_packs = Path(__file__).resolve().parents[2] / "plugins"
    for manifest in repo_packs.glob("*/.codex-plugin/plugin.json"):
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except Exception:
            continue
        for tool in payload.get("tools") or []:
            intent = str((tool or {}).get("intent") or "").strip()
            if intent:
                known.add(intent)
    for skill in native_skills():
        unknown = [tool for tool in skill.allowed_tools if tool not in known]
        assert not unknown, f"{skill.name} names tools this runtime does not have: {unknown}"


def test_every_declared_capability_exists_in_this_runtime() -> None:
    """Declared capabilities are requirements, not decoration: each must resolve in the graph.

    Scoped to the vool library: the two pre-contract packages declare the media worker's own
    capability vocabulary, which predates the runtime graph — that drift is exactly what the P1
    contract ends for first-party packages, and legacy packages are grandfathered, not blessed.
    """
    from core.capability_graph import CapabilityId, capabilities_for_skill

    for slug in EXPECTED_VOOL_PACKAGES:
        skill = _by_name()[slug]
        for capability in skill.capabilities:
            resolved = capabilities_for_skill(skill.name, [capability])
            assert resolved, f"{skill.name} declares capability {capability!r} this runtime lacks"
            assert CapabilityId(capability) in {r for r in resolved} or resolved, capability


def test_declared_permissions_cover_what_the_named_tools_require() -> None:
    """A permission declaration that under-states its tools is a permission lie in print."""
    from core.runtime_tool_contracts import runtime_tool_contract_map

    contracts = runtime_tool_contract_map()
    for skill in native_skills():
        if not skill.permissions:
            continue  # packages predating the contract declare no permissions; additive seam
        declared = set(skill.permissions)
        for tool in skill.allowed_tools:
            contract = contracts.get(tool)
            if contract is None:
                continue
            required = {str(a) for a in getattr(contract, "permission_actions", ()) or ()}
            missing = required - declared
            assert not missing, (
                f"{skill.name}: tool {tool} requires actions {sorted(required)} but the package "
                f"only declares {sorted(declared)}"
            )


def test_effects_use_the_runtime_side_effect_vocabulary() -> None:
    """`effects` must speak the contract vocabulary (`side_effect_class`), not free prose."""
    from core.runtime_tool_contracts import runtime_tool_contracts

    vocabulary = {
        str(getattr(contract, "side_effect_class", "") or "")
        for contract in runtime_tool_contracts()
    }
    vocabulary.discard("")
    for skill in native_skills():
        unknown = [effect for effect in skill.effects if effect not in vocabulary]
        assert not unknown, f"{skill.name}: effects outside the runtime vocabulary: {unknown}"


def test_no_package_names_a_direct_execution_bypass() -> None:
    """The no-bypass law, machine-checked at the only place it can be: the package surface.

    Skills reach effects through the runtime tool door (`execute_runtime_tool`) and nothing else;
    a package that names a raw transport as its way of working is declaring the exact bypass the
    effect authorities exist to close. Scoped to the vool library (legacy packages are
    grandfathered); every vool package must also STATE the door it goes through.
    """
    bypass_vocabulary = ("urlopen", "urllib.request", "raw socket", "subprocess.popen")
    for slug in EXPECTED_VOOL_PACKAGES:
        skill = _by_name()[slug]
        low = skill.body.lower()
        for marker in bypass_vocabulary:
            assert marker not in low, f"{slug} declares the bypass {marker!r} in its body"
        assert "execute_runtime_tool" in low or "runtime tool door" in low, (
            f"{slug} never states the door its tools go through"
        )


@pytest.mark.parametrize("slug", EXPECTED_VOOL_PACKAGES)
def test_the_real_validator_accepts_every_vool_package(slug: str) -> None:
    """`skill.validate` is the toolchain's own verdict — the shipped library must pass it."""
    verdict = validate_skill(str(native_skills_root() / slug / "SKILL.md"))
    assert verdict["status"] == "ok", f"{slug}: {verdict.get('problems')}"
    assert verdict["unknown_tools"] == []


def test_legacy_packages_gain_capability_visibility_without_new_frontmatter() -> None:
    """media-studio always declared `capabilities:`; the loader used to throw that away."""
    skill = _by_name()["media-studio"]
    assert "media.inspect" in skill.capabilities
    assert skill.version == "0.1.0"


def test_parse_of_a_minimal_skill_is_unchanged(tmp_path) -> None:
    """The seam is additive: a skill with none of the new keys parses to the old shape."""
    path = tmp_path / "plain" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        "---\nname: plain\n(description)\ndescription: just an older skill\n---\n\nbody\n",
        encoding="utf-8",
    )
    skill = parse_skill(path)
    assert skill is not None
    assert skill.version == ""
    assert skill.capabilities == ()
    assert skill.stop_conditions == ()
    assert skill.cumulative_test_law == ()
