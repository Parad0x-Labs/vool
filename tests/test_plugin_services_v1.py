"""Native declarations cross a real local TLS socket to a separate service process."""
from __future__ import annotations

import json
import secrets
import socket
import ssl
import subprocess
import sys
from pathlib import Path

import pytest
import requests

from core import plugin_lifecycle as lifecycle
from core import plugin_manager as manager
from core.plugin_services import ServiceRefused, native_request
from tests.test_plugin_manager_v1 import installed, package


@pytest.fixture
def https_service(tmp_path, request):
    ca_key, ca = tmp_path / "ca.key", tmp_path / "ca.pem"
    server_key, csr, certificate = tmp_path / "server.key", tmp_path / "server.csr", tmp_path / "server.pem"
    extensions = tmp_path / "extensions.txt"
    extensions.write_text("subjectAltName=DNS:api.example.org,DNS:api.usepod.ai,DNS:api.eyebrow.cc,IP:127.0.0.1\nextendedKeyUsage=serverAuth\nauthorityKeyIdentifier=keyid,issuer\n")
    commands = [
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-addext", "keyUsage=critical,keyCertSign,cRLSign", "-subj", "/CN=Disposable service fixture CA", "-keyout", str(ca_key), "-out", str(ca)],
        ["openssl", "req", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=api.example.org", "-keyout", str(server_key), "-out", str(csr)],
        ["openssl", "x509", "-req", "-in", str(csr), "-CA", str(ca), "-CAkey", str(ca_key), "-CAcreateserial", "-days", "1", "-extfile", str(extensions), "-out", str(certificate)],
    ]
    for command in commands:
        result = subprocess.run(command, capture_output=True, timeout=15)
        assert result.returncode == 0, result.stderr.decode()
    access = secrets.token_urlsafe(32)
    log = tmp_path / "egress.jsonl"
    errors = (tmp_path / "service-errors.txt").open("w")
    child = subprocess.Popen([sys.executable, str(Path(__file__).with_name("plugin_https_fixture_server.py"))],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors, text=True, start_new_session=True,
                             env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"})
    child.stdin.write(json.dumps({"access": access, "log": str(log), "certificate": str(certificate), "private_key": str(server_key),
                                 "tool_dispatch": getattr(request, "param", {}).get("tool_dispatch", False), "requests_log": str(tmp_path / "provider-requests.jsonl")}) + "\n")
    child.stdin.flush()
    port = json.loads(child.stdout.readline())["port"]
    context = ssl.create_default_context(cafile=str(ca))

    def connector(host, *, timeout, call=None):
        assert host in {"api.example.org", "api.usepod.ai", "api.eyebrow.cc"}
        if call:
            call.check()
        # Fixture routing uses a real socket to the required fake external service.
        # Production public DNS enforcement is exercised separately.
        return socket.create_connection(("127.0.0.1", port), timeout=timeout)

    try:
        yield {"access": access, "log": log, "connector": connector, "context": context, "ca": ca, "port": port}
    finally:
        child.terminate()
        child.wait(timeout=5)
        child.stdin.close()
        child.stdout.close()
        errors.close()


def enabled_provider(package, service, *, path_auth=False, key=None):
    path = package / ".codex-plugin/plugin.json"
    raw = json.loads(path.read_text())
    if path_auth:
        raw["vool"]["model_provider"].update(auth_placement="path", auth_path="/proxy/{credential}")
    path.write_text(json.dumps(raw))
    info = installed(package)
    if key is not False:
        manager.connect(info["id"], "access", key or service["access"])
    manager.enable(info["id"], accepted=True, approval_digest=info["approval_digest"])
    return info


def call(service, path="/v1/models", *, payload=None, timeout=3, stream=False):
    return native_request("sample-provider", "model_provider", "GET" if payload is None else "POST",
                          "https://api.example.org" + path, payload=payload, timeout=timeout, stream=stream,
                          _connector=service["connector"], _tls_context=service["context"])


@pytest.mark.parametrize("path_auth", [False, True])
def test_native_model_list_uses_real_tls_and_only_own_key(package, https_service, path_auth):
    enabled_provider(package, https_service, path_auth=path_auth)
    response = call(https_service)
    assert response.json()["data"][0]["id"] == "fixture-model"
    records = [json.loads(line) for line in https_service["log"].read_text().splitlines()]
    assert records[0]["authorized"] and records[0]["path_auth"] is path_auth
    assert records[0]["authorization_header"] is not path_auth
    assert https_service["access"] not in https_service["log"].read_text()
    assert https_service["access"] not in response.url


def test_install_and_key_connect_make_no_requests_until_explicit_use(package, https_service):
    info = installed(package)
    manager.connect(info["id"], "access", https_service["access"])
    with pytest.raises(ServiceRefused, match="disabled"):
        call(https_service)
    assert not https_service["log"].exists()


def test_missing_key_stops_before_a_socket_is_opened(package, https_service):
    enabled_provider(package, https_service, key=False)
    with pytest.raises(ServiceRefused, match="Connect your service key"):
        call(https_service)
    assert not https_service["log"].exists()


def test_wrong_key_is_a_plain_failure_without_retry(package, https_service):
    enabled_provider(package, https_service, key=secrets.token_urlsafe(32))
    with pytest.raises(ServiceRefused, match="rejected this key"):
        call(https_service)
    assert len(https_service["log"].read_text().splitlines()) == 1


@pytest.mark.parametrize("mode,message", [("error", "could not complete"), ("oversized", "exceeds the size limit"),
                                         ("payment", "cannot authorize"), ("redirect", "redirect")])
def test_real_service_failures_are_bounded_and_fail_closed(package, https_service, mode, message):
    enabled_provider(package, https_service)
    with pytest.raises(ServiceRefused, match=message):
        call(https_service, "/v1/chat/completions", payload={"fixture_mode": mode})
    assert len(https_service["log"].read_text().splitlines()) == 1


def test_service_timeout_has_no_automatic_retry(package, https_service):
    enabled_provider(package, https_service)
    with pytest.raises(requests.exceptions.Timeout, match="timed out"):
        call(https_service, "/v1/chat/completions", timeout=0.2, payload={"fixture_mode": "timeout"})
    assert len(https_service["log"].read_text().splitlines()) == 1


def test_disabled_plugin_cannot_reuse_a_cached_transport_approval(package, https_service):
    info = enabled_provider(package, https_service)
    manager.disable("sample-provider")
    with pytest.raises(ServiceRefused, match="disabled"):
        native_request("sample-provider", "model_provider", "GET", "https://api.example.org/v1/models", expected_approval=info["approval_digest"])
    assert not https_service["log"].exists()
    assert not lifecycle.is_available("sample-provider")


@pytest.mark.parametrize("path_auth", [False, True])
def test_models_enter_existing_registry_and_real_adapter_streams(package, https_service, monkeypatch, path_auth):
    from adapters.base_adapter import ModelRequest
    from core import plugin_providers, plugin_services
    from core.model_registry import ModelRegistry

    enabled_provider(package, https_service, path_auth=path_auth)
    transport = plugin_services.native_request

    def routed(*args, **kwargs):
        return transport(*args, **kwargs, _connector=https_service["connector"], _tls_context=https_service["context"])

    # Only local test routing/CA are supplied. All HTTP, TLS, registry, vault,
    # adapter and protocol behavior run unchanged against the real process.
    monkeypatch.setattr(plugin_services, "native_request", routed)
    monkeypatch.setattr(plugin_providers, "native_request", routed)
    result = plugin_providers.refresh_models("sample-provider")
    assert result["provider_ids"] == ["plugin-sample-provider:fixture-model"]
    registry = ModelRegistry()
    model = registry.get_manifest("plugin-sample-provider", "fixture-model")
    assert model and model.runtime_config["plugin_id"] == "sample-provider"
    assert model.metadata["plugin_reported_pricing"] == {"prompt": "0", "completion": "0"}
    from core.runtime_provider_defaults import register_chat_cloud_model

    assert register_chat_cloud_model("plugin-sample-provider", "fixture-model") == model.provider_id
    assert model.metadata["supported_parameters"] == ["tools", "max_tokens"]
    prompt = secrets.token_hex(12)
    adapter = registry.build_adapter(model)
    assert adapter._supports_native_tools()
    request = ModelRequest(task_kind="chat", prompt=prompt, messages=[{"role": "user", "content": prompt}])
    chunks = list(adapter.stream_text_task(request))
    text = "".join(chunk.delta_text for chunk in chunks)
    assert text == prompt[::-1]
    assert https_service["access"] not in json.dumps([chunk.__dict__ for chunk in chunks], default=str)
    manager.disable("sample-provider")
    assert registry.get_manifest("plugin-sample-provider", "fixture-model") is None
    with pytest.raises(ValueError, match="unavailable"):
        register_chat_cloud_model("plugin-sample-provider", "fixture-model")
    assert not any(model.runtime_config.get("plugin_id") == "sample-provider" for model in registry.list_manifests())
    with pytest.raises(ServiceRefused, match="disabled"):
        list(adapter.stream_text_task(request))


def enabled_scanner(package, service, *, key=True):
    reference = Path(__file__).parent / "fixtures/plugins/sample-scanner/.codex-plugin/plugin.json"
    raw = json.loads(reference.read_text())
    raw["name"] = raw["vool"]["id"] = "sample-provider"
    (package / ".codex-plugin/plugin.json").write_text(json.dumps(raw))
    info = installed(package)
    if key:
        manager.connect(info["id"], "access", service["access"])
    manager.enable(info["id"], accepted=True, approval_digest=info["approval_digest"])


def test_scanner_declaration_posts_exact_public_source_over_real_tls(package, https_service, monkeypatch):
    from core import plugin_scanners
    from tests.test_addon_store import SKILL

    enabled_scanner(package, https_service)

    def routed(*args, **kwargs):
        return native_request(*args, **kwargs, _connector=https_service["connector"], _tls_context=https_service["context"])

    monkeypatch.setattr(plugin_scanners, "native_request", routed)
    report = plugin_scanners.scan_skill("example", SKILL.decode())
    assert report["eligible"] and report["scanner_plugin"] == "sample-provider"
    assert report["artifacts"][0]["name"] == "example"
    # VOOL's own check always runs; the plugin report is added beside it, never in its place.
    assert report["service"] == "VOOL local check" and report["local_report"]["status"] == "clean"
    assert report["plugin_report"]["service"] == "external-fixture"
    assert len(https_service["log"].read_text().splitlines()) == 1
    assert https_service["access"] not in json.dumps(report)


def test_local_scan_is_the_default_without_any_installed_scanner(package, https_service):
    from core import plugin_scanners
    from tests.test_addon_store import SKILL

    report = plugin_scanners.scan_skill("example", SKILL.decode())
    assert report["eligible"] and report["service"] == "VOOL local check"
    assert report["scan_notice"] == "Checked on this device only. No remote scanner was contacted."
    assert not https_service["log"].exists()


def test_enabled_scanner_missing_its_key_refuses_instead_of_silently_skipping(package, https_service, monkeypatch):
    from core import plugin_scanners
    from core.addon_security import AddonError
    from tests.test_addon_store import SKILL

    enabled_scanner(package, https_service, key=False)

    def routed(*args, **kwargs):
        return native_request(*args, **kwargs, _connector=https_service["connector"], _tls_context=https_service["context"])

    monkeypatch.setattr(plugin_scanners, "native_request", routed)
    with pytest.raises(AddonError, match="Connect your service key"):
        plugin_scanners.scan_skill("example", SKILL.decode())
    assert not https_service["log"].exists()


@pytest.mark.parametrize("stream", [False, True])
def test_provider_transport_stop_interrupts_real_tls_before_headers(package, https_service, monkeypatch, stream):
    import threading
    import time

    from core import plugin_services
    from core.plugin_services import ProviderTransport
    from core.provider_http import ProviderCallCancelled

    info = enabled_provider(package, https_service)
    original = plugin_services.native_request

    def routed(*args, **kwargs):
        return original(*args, **kwargs, _connector=https_service["connector"], _tls_context=https_service["context"])

    monkeypatch.setattr(plugin_services, "native_request", routed)
    stopped = threading.Event()

    def stop_after_request_arrives():
        deadline = time.monotonic() + 3
        while not https_service["log"].exists() and time.monotonic() < deadline:
            time.sleep(0.005)
        stopped.set()

    stop = threading.Thread(target=stop_after_request_arrives)
    stop.start()
    start = time.monotonic()
    try:
        with pytest.raises(ProviderCallCancelled, match="model_call_cancelled"):
            ProviderTransport("sample-provider", info["approval_digest"]).post(
                "https://api.example.org/v1/chat/completions", json={"fixture_mode": "timeout"},
                timeout=(1, 3), stream=stream, cancel_check=stopped.is_set)
    finally:
        stop.join(timeout=4)
    assert time.monotonic() - start < 2
    assert len(https_service["log"].read_text().splitlines()) == 1


def test_a_paid_call_through_a_provider_plugin_is_held_against_the_owners_limits(package, https_service, monkeypatch, tmp_path):
    """Owner requirement 2026-11-12: a service brings its own plugin and its paid calls work the way
    a key-based cloud provider's do. A plugin's model is a paid cloud model whatever price the
    service reports, so its call is reserved against the owner's limits before anything is sent:
    refused for anyone but the owner, refused past the per-call and the daily limit, and booked
    in the same spend ledger. Nothing here reaches the service's chat door."""
    from dataclasses import replace
    from types import SimpleNamespace

    from core import cloud_escalation_policy as cep
    from core import os_consent_gate, plugin_providers, plugin_services, runtime_paths
    from core.model_registry import ModelRegistry
    from core.model_selection_policy import is_verified_free_cloud_manifest, provider_cost_class
    from core.model_spend_ledger import get_spend_reservation
    from core.paid_call_reservation import release_owner_pick_paid_call, reserve_owner_pick_paid_call, spend_limits
    from storage.db import configure_default_db_path

    enabled_provider(package, https_service)
    transport = plugin_services.native_request

    def routed(*args, **kwargs):
        return transport(*args, **kwargs, _connector=https_service["connector"], _tls_context=https_service["context"])

    monkeypatch.setattr(plugin_services, "native_request", routed)
    monkeypatch.setattr(plugin_providers, "native_request", routed)
    plugin_providers.refresh_models("sample-provider")
    model = ModelRegistry().get_manifest("plugin-sample-provider", "fixture-model")
    # The service reports a price of 0. That is the service's word, not a receipt: still paid.
    assert model.metadata["plugin_reported_pricing"] == {"prompt": "0", "completion": "0"}
    assert provider_cost_class(model) == "paid_cloud" and not is_verified_free_cloud_manifest(model)

    home = tmp_path / "ledger-home"
    monkeypatch.setenv("VOOL_HOME", str(home))
    runtime_paths.configure_runtime_home(home)
    configure_default_db_path(home / "data" / "test.db")
    os_consent_gate.set_consent_override_for_tests(lambda reason: False)
    try:
        cep.save_policy(replace(cep.load_policy(), mode="off", daily_cap=25))
        limits = spend_limits()
        owner = {"_owner_local": True, "session_id": "s"}

        def ask(context, *, prompt_tokens=200, task_id=None):
            denial: dict[str, str] = {}
            task = SimpleNamespace(task_id=task_id or "task-" + secrets.token_hex(6), prompt_tokens=prompt_tokens,
                                   max_output_tokens=400)
            return reserve_owner_pick_paid_call(manifest=model, task=task, source_context=dict(context),
                                                task_kind="chat", denial=denial), denial.get("reason", "")

        granted, reason = ask({"_owner_local": False, "session_id": "s"})   # a phone or a chat app, not the owner's own session
        assert granted is None and reason == "not_owner_local"
        granted, reason = ask(owner)
        assert granted is not None, reason
        held = get_spend_reservation(granted.model_call_id)
        assert held.status == "reserved" and held.reserved_usd >= limits.per_call_usd > 0
        release_owner_pick_paid_call(granted, reason="call_failed")
        assert get_spend_reservation(granted.model_call_id).status != "reserved"
        # Holds stand until settled or released: the day's limit bounds them.
        standing, last = [], ""
        for _ in range(int(limits.daily_usd / limits.per_call_usd) + 2):
            granted, last = ask(owner)
            if granted is None:
                break
            standing.append(granted)
        assert last in {"daily_spend_cap_exceeded", "monthly_spend_cap_exceeded"} and standing
        assert len(standing) <= int(limits.daily_usd / limits.per_call_usd)
    finally:
        os_consent_gate.set_consent_override_for_tests(None)
        configure_default_db_path(None)
        runtime_paths.configure_runtime_home(None)
    paths = [json.loads(line)["path"] for line in https_service["log"].read_text().splitlines()]
    assert "/v1/chat/completions" not in paths
