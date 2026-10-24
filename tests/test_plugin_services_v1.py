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
def https_service(tmp_path):
    ca_key, ca = tmp_path / "ca.key", tmp_path / "ca.pem"
    server_key, csr, certificate = tmp_path / "server.key", tmp_path / "server.csr", tmp_path / "server.pem"
    extensions = tmp_path / "extensions.txt"
    extensions.write_text("subjectAltName=DNS:api.example.org,DNS:api.usepod.ai,DNS:api.eyebrow.cc\nextendedKeyUsage=serverAuth\n")
    commands = [
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=Disposable service fixture CA", "-keyout", str(ca_key), "-out", str(ca)],
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
    child.stdin.write(json.dumps({"access": access, "log": str(log), "certificate": str(certificate), "private_key": str(server_key)}) + "\n")
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
    prompt = secrets.token_hex(12)
    adapter = registry.build_adapter(model)
    request = ModelRequest(task_kind="chat", prompt=prompt, messages=[{"role": "user", "content": prompt}])
    chunks = list(adapter.stream_text_task(request))
    text = "".join(chunk.delta_text for chunk in chunks)
    assert text == prompt[::-1]
    assert https_service["access"] not in json.dumps([chunk.__dict__ for chunk in chunks], default=str)
    manager.disable("sample-provider")
    assert registry.get_manifest("plugin-sample-provider", "fixture-model") is None
    assert not any(model.runtime_config.get("plugin_id") == "sample-provider" for model in registry.list_manifests())
    with pytest.raises(ServiceRefused, match="disabled"):
        list(adapter.stream_text_task(request))


def enabled_scanner(package, service, *, key=True):
    reference = Path(__file__).parents[1] / "examples/plugins/vool-plugin-eyebrow/.codex-plugin/plugin.json"
    raw = json.loads(reference.read_text())
    raw["name"] = raw["vool"]["id"] = "sample-provider"
    raw["vool"]["network"] = ["api.example.org"]
    raw["vool"]["scanner"]["base_url"] = "https://api.example.org"
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
    assert report["service"] == "external-fixture"
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
