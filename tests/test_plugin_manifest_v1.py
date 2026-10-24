"""Service declarations acquire no authority by parsing, including adversarial packages."""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from core.plugin_manifest import MANIFEST_SCHEMA, validate_schema
from core.plugin_tools import PluginManifestError, parse_manifest


def manifest():
    return {"name": "sample-provider", "version": "1.0.0", "vool": {
        "id": "sample-provider", "version": "1.0.0", "name": "Sample provider",
        "publisher": "Example", "description": "A declarative compatible provider.",
        "homepage": "https://example.org/", "license": "MIT", "kind": ["model_provider"],
        "network": ["api.example.org"], "credentials": {
            "access": {"label": "Your service key", "help_url": "https://example.org/keys", "env": "SAMPLE_ACCESS"}},
        "model_provider": {"base_url": "https://api.example.org", "auth_placement": "header",
                           "credential_slot": "access", "model_list_path": "/v1/models", "pricing_source": "models"}}}


def load(raw, tmp_path):
    return parse_manifest(raw, path=tmp_path / ".codex-plugin" / "plugin.json")


def test_declarative_provider_loads_without_tools_or_network(tmp_path):
    loaded = load(manifest(), tmp_path)
    assert loaded.contracts == ()
    assert loaded.vool["model_provider"]["credential_slot"] == "access"
    assert loaded.root == tmp_path


def test_schema_export_is_the_runtime_schema():
    path = Path(__file__).resolve().parents[1] / "docs/reference/plugin-manifest.schema.json"
    assert json.loads(path.read_text()) == MANIFEST_SCHEMA
    assert validate_schema(manifest(), MANIFEST_SCHEMA) == []


@pytest.mark.parametrize("version", ["01.0.0", "1.2.3-01", "1.2.3-a.01", "1.2.3-", "1.2.3+"])
def test_semver_numeric_identifiers_are_not_ambiguous(tmp_path, version):
    raw = manifest()
    raw["version"] = raw["vool"]["version"] = version
    with pytest.raises(PluginManifestError):
        load(raw, tmp_path)


@pytest.mark.parametrize("version", ["0.0.0", "1.2.3-alpha.0", "1.2.3-01a+build.01"])
def test_semver_accepts_valid_prerelease_and_build_identifiers(tmp_path, version):
    raw = manifest()
    raw["version"] = raw["vool"]["version"] = version
    assert load(raw, tmp_path).version == version


@pytest.mark.parametrize("flag", ["autoEnable", "autoStart", "ownerPaidKeys", "ownerModelKeys"])
def test_alternate_flag_spellings_do_not_bypass_the_manifest_wall(tmp_path, flag):
    raw = manifest()
    raw[flag] = False
    with pytest.raises(PluginManifestError):
        load(raw, tmp_path)


@pytest.mark.parametrize("field", ["id", "version", "name", "publisher", "description", "homepage", "license", "kind", "network", "credentials"])
def test_missing_required_service_metadata_refuses_the_whole_pack(field, tmp_path):
    value = manifest()
    del value["vool"][field]
    with pytest.raises(PluginManifestError):
        load(value, tmp_path)


@pytest.mark.parametrize("host", ["*.example.org", "127.0.0.1", "10.0.0.2", "::1", "localhost", "service.local", "127.1", "2130706433", "api.example.org:443", "https://api.example.org", "api.example.org/route", "API.example.org", "api.example.org."])
def test_network_declarations_refuse_addresses_wildcards_and_ambiguous_hosts(host, tmp_path):
    value = manifest()
    value["vool"]["network"] = [host]
    with pytest.raises(PluginManifestError):
        load(value, tmp_path)


@pytest.mark.parametrize("action", ["financial_or_paid_actions", "wallet.sign", "payment", "approval.grant", "cap.modify", "budget.change", "receipts.write"])
def test_money_permissions_cannot_be_requested_by_a_package(action, tmp_path):
    value = manifest()
    value["permissions"] = [action]
    with pytest.raises(PluginManifestError, match="authority"):
        load(value, tmp_path)


@pytest.mark.parametrize("flag", ["autostart", "auto_enable", "owner_credentials", "owner_keys"])
def test_automatic_start_and_owner_key_requests_are_rejected_even_when_false(flag, tmp_path):
    value = manifest()
    value[flag] = False
    with pytest.raises(PluginManifestError):
        load(value, tmp_path)


def test_provider_cannot_send_its_key_to_an_undeclared_host(tmp_path):
    value = manifest()
    value["vool"]["model_provider"]["base_url"] = "https://other.example.org"
    with pytest.raises(PluginManifestError, match="destination"):
        load(value, tmp_path)


def test_credentials_are_names_only_and_never_refer_to_other_slots(tmp_path):
    value = manifest()
    value["vool"]["credentials"]["access"]["value"] = "not-a-real-value"
    with pytest.raises(PluginManifestError):
        load(value, tmp_path)
    value = manifest()
    value["credential_bindings"] = ["plugin.another-provider.access"]
    with pytest.raises(PluginManifestError, match="another plugin"):
        load(value, tmp_path)
    value = manifest()
    value["vool"]["model_provider"]["credential_slot"] = "another-provider.access"
    with pytest.raises(PluginManifestError):
        load(value, tmp_path)


def mcp_manifest():
    value = manifest()
    value["vool"]["kind"] = ["tools"]
    del value["vool"]["model_provider"]
    value["vool"]["mcpServers"] = {"local": {"command": "bin/server", "args": [], "env": {"SAMPLE_ACCESS": "${SAMPLE_ACCESS}"}}}
    value["vool"]["tools"] = [{"intent": "sample-provider.echo", "description": "Return the supplied text through the local server.", "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}}, "handler": {"kind": "mcp", "server": "local", "tool": "echo"}}]
    return value


@pytest.mark.parametrize("command", ["../outside", "/usr/bin/python3", "npx", "uvx", "C:server", "${ROOT}/server", "bin/../../outside"])
def test_mcp_commands_must_be_bundled_files(command, tmp_path):
    value = mcp_manifest()
    value["vool"]["mcpServers"]["local"]["command"] = command
    with pytest.raises(PluginManifestError):
        load(value, tmp_path)


def test_symlink_command_cannot_escape_package(tmp_path):
    (tmp_path / "outside").mkdir()
    root = tmp_path / "package"
    root.mkdir()
    (root / "bin").symlink_to(tmp_path / "outside", target_is_directory=True)
    with pytest.raises(PluginManifestError, match="leaves"):
        load(mcp_manifest(), root)


def test_unpinned_mcp_tool_keeps_unknown_side_effects(tmp_path):
    value = mcp_manifest()
    contract = load(value, tmp_path).contracts[0]
    assert contract.side_effect_class == "external_unpinned"
    assert contract.permission_actions == ()
    assert contract.approval_requirement == "explicit_user_opt_in"
    pinned = deepcopy(value)
    pinned["vool"]["tools"][0]["trust"] = {"side_effect_class": "read_only", "permission_actions": ["read_files"]}
    contract = load(pinned, tmp_path).contracts[0]
    assert contract.side_effect_class == "read_only"
    assert contract.permission_actions == ("read_files",)
    pinned["vool"]["tools"][0]["trust"]["permission_actions"] = ["modify_files"]
    with pytest.raises(PluginManifestError, match="not admissible"):
        load(pinned, tmp_path)


def test_mcp_tool_cannot_impersonate_wallet_authority(tmp_path):
    value = mcp_manifest()
    value["vool"]["tools"][0]["handler"]["tool"] = "wallet.approve"
    with pytest.raises(PluginManifestError, match="money"):
        load(value, tmp_path)


def test_legacy_skill_provenance_remains_information_only(tmp_path):
    loaded = load({"name": "old-skill", "vool": {"origin": "user", "staged_by": "import"}}, tmp_path)
    assert loaded.vool == {} and loaded.contracts == ()


@pytest.mark.parametrize("version", ["1", "01.2.3", "1.2", "v1.2.3", "1.2.3."])
def test_non_semver_version_is_refused(version, tmp_path):
    value = manifest()
    value["version"] = value["vool"]["version"] = version
    with pytest.raises(PluginManifestError):
        load(value, tmp_path)
