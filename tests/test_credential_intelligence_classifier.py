"""P0 credential intelligence — the LOCAL-ONLY format classifier and provider shortlist.

THE LAW UNDER TEST
------------------
Classification of a pasted key is a pure local computation:

* **zero model calls** — no model lane, prompt builder, or provider adapter is imported or
  consulted (structural AST law + a runtime sys.modules delta);
* **zero speculative provider calls** — no socket leaves the machine before the operator has
  selected exactly one provider (proven against live loopback fakes that count requests);
* a bare/ambiguous format produces a SHORTLIST plus an ask, never a stored guess;
* a recognized-but-unconfigured family is reported honestly ("that looks like a Slack token")
  rather than silently mapped onto some other provider.
"""
from __future__ import annotations

import ast
import inspect
import sys

import pytest

from tests._credential_intelligence_support import ODD_KEY, FakeProviderServer, registry_of

# ------------------------------------------------------------------ format classification

def test_known_prefixes_classify_to_their_family():
    from core.credential_intelligence.format_classifier import classify_format

    fmt = classify_format("sk-or-v1-" + "a" * 48)
    assert fmt.family == "openrouter"
    assert fmt.prefix == "sk-or-"
    assert fmt.plausible_key is True

    assert classify_format("sk-ant-" + "b" * 40).family == "anthropic"
    assert classify_format("sk-proj-" + "c" * 40).family == "openai"
    assert classify_format("gsk_" + "d" * 30).family == "groq"
    assert classify_format("AIza" + "e" * 30).family == "google"
    assert classify_format("tvly-" + "f" * 30).family == "tavily"


def test_bare_sk_is_its_own_ambiguous_family_never_a_provider_guess():
    from core.credential_intelligence.format_classifier import classify_format

    fmt = classify_format("sk-" + "0123456789abcdef0123456789")
    assert fmt.family == "openai_style"      # the FORMAT is known; the PROVIDER is not
    assert fmt.prefix == "sk-"


def test_jwt_and_pem_and_nonkeys_are_classified():
    from core.credential_intelligence.format_classifier import classify_format

    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    assert classify_format(jwt).is_jwt is True

    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIBOgIBAAJBAK\n-----END RSA PRIVATE KEY-----"
    assert classify_format(pem).is_pem is True

    short = classify_format("abc")
    assert short.plausible_key is False

    spaced = classify_format("not a key at all ok")
    assert spaced.plausible_key is False


def test_classification_touches_no_secrets_on_the_object():
    from core.credential_intelligence.format_classifier import KeyFormat

    assert "secret" not in KeyFormat.__dataclass_fields__
    assert "value" not in KeyFormat.__dataclass_fields__


# ------------------------------------------------------------------ structural purity laws

_FORBIDDEN_IMPORT_FRAGMENTS = ("model", "adapter", "daemon", "prompt", "agent", "memory", "ollama", "http", "socket", "urllib", "requests")


def _imported_names(module) -> set[str]:
    tree = ast.parse(inspect.getsource(module))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


@pytest.mark.parametrize("module_name", ["format_classifier", "shortlist"])
def test_classifier_modules_import_no_network_or_model_lanes(module_name):
    import importlib

    module = importlib.import_module(f"core.credential_intelligence.{module_name}")
    for name in _imported_names(module):
        lowered = name.lower()
        assert not any(frag in lowered for frag in _FORBIDDEN_IMPORT_FRAGMENTS), (
            f"{module_name} must stay a pure local computation; found import {name!r}"
        )


def test_no_model_or_transport_module_loads_during_classification():
    """Runtime half of the purity law: classifying + shortlisting a fresh key must not
    lazily import any model lane, adapter, or transport library."""
    import importlib

    fmt_mod = importlib.import_module("core.credential_intelligence.format_classifier")
    shortlist_mod = importlib.import_module("core.credential_intelligence.shortlist")
    registry_mod = importlib.import_module("core.credential_intelligence.provider_registry")

    before = set(sys.modules)
    fmt = fmt_mod.classify_format("sk-or-v1-" + "a" * 48)
    registry = registry_mod.default_registry()
    shortlist_mod.build_shortlist(fmt, registry)
    new = set(sys.modules) - before
    offenders = {m for m in new if any(f in m.lower() for f in ("adapter", "ollama", "daemon", "prompt"))}
    assert not offenders, f"classification lazily imported forbidden modules: {sorted(offenders)}"


# ------------------------------------------------------------------ shortlist

def _registry_with_fakes():
    """A registry whose every candidate endpoint is a live loopback fake — so a speculative
    provider call during shortlist-building is OBSERVABLE, not theoretical."""
    from tests._credential_intelligence_support import descriptor_for

    servers = {}
    descriptors = []
    for pid, prefixes in (
        ("openai", ("sk-proj-",)),
        ("deepseek", ()),
        ("moonshot", ()),
        ("openrouter", ("sk-or-",)),
    ):
        server = FakeProviderServer()
        servers[pid] = server
        descriptors.append(descriptor_for(
            server, provider_id=pid, key_prefixes=prefixes,
            # all three of the bare-sk family belong to the ambiguous group, including
            # openai (sk-proj- is its specific prefix, but its plain keys are bare sk-)
            ambiguous_group="bare_sk" if pid != "openrouter" else "",
        ))
    return descriptors, servers


def test_unique_prefix_shortlists_one_high_confidence_provider():
    from core.credential_intelligence.format_classifier import classify_format
    from core.credential_intelligence.provider_registry import ProviderRegistry
    from core.credential_intelligence.shortlist import build_shortlist
    from tests._credential_intelligence_support import descriptor_for

    with FakeProviderServer() as server:
        registry = ProviderRegistry({"openrouter": descriptor_for(server, "openrouter", key_prefixes=("sk-or-",))})
        shortlist = build_shortlist(classify_format("sk-or-v1-" + "a" * 48), registry)
        assert [e.provider_id for e in shortlist.entries] == ["openrouter"]
        assert shortlist.entries[0].confidence == "high"
        assert shortlist.unrecognized is False
        assert shortlist.ambiguous is False


def test_bare_sk_shortlists_all_three_never_picks_one():
    from core.credential_intelligence.format_classifier import classify_format
    from core.credential_intelligence.shortlist import build_shortlist
    from tests._credential_intelligence_support import registry_of

    descriptors, _servers = _registry_with_fakes()
    shortlist = build_shortlist(classify_format("sk-" + "0123456789abcdef0123456789"), registry_of(*descriptors))
    assert shortlist.ambiguous is True
    assert sorted(e.provider_id for e in shortlist.entries) == ["deepseek", "moonshot", "openai"]
    assert shortlist.entries[0].confidence != "high"


def test_unrecognized_format_shortlists_nothing_and_says_so():
    from core.credential_intelligence.format_classifier import classify_format
    from core.credential_intelligence.shortlist import build_shortlist

    shortlist = build_shortlist(classify_format(ODD_KEY), registry_of())
    assert shortlist.entries == ()
    assert shortlist.unrecognized is True


def test_recognized_family_without_a_configured_provider_is_reported_not_mapped():
    from core.credential_intelligence.format_classifier import classify_format
    from core.credential_intelligence.shortlist import build_shortlist

    shortlist = build_shortlist(classify_format("xoxb-" + "1" * 24), registry_of())
    assert shortlist.entries == ()
    assert shortlist.unrecognized is False
    assert shortlist.recognized_family == "slack"


def test_shortlist_building_makes_zero_provider_calls():
    """The zero-speculative-calls law, proven against live servers: every candidate in the
    registry is a counting fake, and NONE of them may see a request during classification."""
    from core.credential_intelligence.format_classifier import classify_format
    from core.credential_intelligence.shortlist import build_shortlist
    from tests._credential_intelligence_support import registry_of

    descriptors, servers = _registry_with_fakes()
    try:
        for server in servers.values():
            server._thread.start()
        registry = registry_of(*descriptors)
        build_shortlist(classify_format("sk-or-v1-" + "a" * 48), registry)
        build_shortlist(classify_format("sk-" + "0123456789abcdef0123456789"), registry)
        for pid, server in servers.items():
            assert server.request_count == 0, f"shortlist-building called provider {pid}"
    finally:
        for server in servers.values():
            server._server.shutdown()
            server._server.server_close()
