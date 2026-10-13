"""Complete local packages and real signature fixtures for registry audit regressions."""
import json
from contextlib import contextmanager

from core import plugin_lifecycle
from core.registry.artifact import inspect, pack
from core.registry.trust import TrustedCatalog
from tests.registry.helpers import NOW, catalog_fixture, trust_fixture
from tools.community_skills import initialize


def package(tmp_path, *, publisher="alice", tools=False, permissions=(), effect="network_send"):
    source = tmp_path / "source"
    info = {"id": publisher + "/echo", "kind": "plugin" if tools else "skill", "version": "1.0.0",
            "summary": "Summarize user supplied notes", "category": "productivity", "subcategory": "notes-summaries",
            "license": "MIT", "source": {"repository": publisher + "/notes", "commit": "b" * 40, "path": "echo"}}
    initialize(source, info, b"Local fixture licence.\n")
    manifest_path = source / ".codex-plugin/plugin.json"
    manifest = json.loads(manifest_path.read_bytes())
    if tools:
        manifest.update(permissions=list(permissions), credential_bindings=["binding-mail"],
                        runtime={"contract_version": 1}, tools=[{
            "intent": manifest["name"] + ".deliver", "description": "Deliver notes using the supplied target.",
            "handler": {"kind": "subprocess", "entry": "tool.py", "network": effect == "network_send",
                        "env_allowlist": ["REGISTRY_PROVIDER_NAME"]},
            "input_schema": {"type": "object", "additionalProperties": False, "required": ["target"],
                             "properties": {"target": {"type": "string", "x-vool-kind": "query"}}},
            "side_effect_class": effect, "approval_requirement": "runtime_policy",
            "permission_actions": ["external_messages"] if effect == "network_send" else ["financial_or_paid_actions"],
            "claim": {"target_argument": "target", "resolved_target_key": "target", "result_items_key": "items"}}])
        (source / "tool.py").write_text("raise SystemExit('fixture handler must not execute')\n")
    manifest_path.write_text(json.dumps(manifest))
    return pack(source, info, tmp_path / "pack"), source, info


@contextmanager
def trusted(raw, path):
    policy, keys, root = trust_fixture()
    catalog = TrustedCatalog(root, path, now=NOW)
    try:
        catalog.accept(catalog_fixture(raw, policy, keys), now=NOW)
        yield catalog, policy, keys, root
    finally:
        catalog.close()


def runtime(tmp_path, monkeypatch):
    home = tmp_path / "runtime"
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_PLUGIN_LIFECYCLE_PATH", str(home / "plugin_lifecycle.json"))
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(home / "addons"))
    monkeypatch.setenv("VOOL_BUNDLED_PLUGINS_DIR", str(tmp_path / "no-bundled"))
    plugin_lifecycle.reset_for_tests()
    return home


def publish(store, raw, path):
    from tests.registry.helpers import TARGET

    info = inspect(raw)["metadata"]
    with trusted(raw, path) as (catalog, *_):
        store.publish(catalog, info["id"], info["version"], TARGET, raw, now=NOW)
