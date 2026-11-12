from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from urllib.request import Request, urlopen

import pytest

from tests import _reader_served_rig as rig


def _resource(kind, pid, port, state):
    filename = os.environ.get("VOOL_TEST_RESOURCE_LOG")
    if filename:
        with open(filename, "a") as stream:
            stream.write(json.dumps({"kind": kind, "pid": pid, "port": port, "state": state}) + "\n")


@contextmanager
def _provider_context(provider):
    _resource("fake-provider", os.getpid(), provider.port, "starting")
    try:
        with provider:
            yield
    finally:
        _resource("fake-provider", os.getpid(), provider.port, "stopped")


class CloudDaemon(rig.ServedDaemon):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Separate outside servers give cloud-zero-send a real denominator:
        # background LOCAL extraction must never be counted as cloud traffic.
        self.boot_provider = rig.CapturingProvider(default='{}')
        self._boot_context = None
        self.boot_provider.metadata_requests = []
        handler = self.boot_provider._server.RequestHandlerClass
        original_get, original_post = handler.do_GET, handler.do_POST
        metadata_requests = self.boot_provider.metadata_requests
        def inventory(connection):
            if connection.path.startswith('/api/tags'):
                return connection._send({'models': [{'name': 'disclosure-boot:scripted', 'size': 0}]})
            return original_get(connection)
        def post(connection):
            if connection.path in {'/api/show', '/api/pull'}:
                body = json.loads(connection.rfile.read(int(connection.headers.get('Content-Length') or 0)))
                metadata_requests.append({'path': connection.path, 'model': body.get('model')})
                if connection.path == '/api/pull':
                    return connection._send({'error': 'Downloads are not available on this scripted provider.'}, status=403)
                return connection._send({'model_info': {}, 'details': {'parameter_size': '0B'}, 'template': ''})
            return original_post(connection)
        handler.do_GET, handler.do_POST = inventory, post

    def chat(self, message, *, disclosure_review="wait", **kwargs):
        # These drives answer previews themselves, as the chat page does, so they declare it.
        return super().chat(message, disclosure_review=disclosure_review, **kwargs)

    def stop(self):
        try:
            super().stop()
        finally:
            if self._boot_context is not None:
                context, self._boot_context = self._boot_context, None
                context.__exit__(None, None, None)
        assert not any(r['path'] == '/api/pull' for r in self.boot_provider.metadata_requests)

    def start(self, **kwargs):
        self._boot_context = _provider_context(self.boot_provider)
        self._boot_context.__enter__()
        try:
            result = super().start(**kwargs)
            if 'pulling in the background' in self.log_tail():
                raise AssertionError('The isolated daemon attempted an unexpected model download.')
            return result
        except Exception:
            self.stop()
            raise

    def env(self):
        env = super().env()
        boot = self.boot_provider.base_url
        env.update(SEARXNG_URL=self.provider.base_url, SEARXNG_TIMEOUT="2",
                   OLLAMA_HOST=boot, VOOL_OLLAMA_URL=boot, VOOL_RAW_OLLAMA_API_URL=boot,
                   VOOL_OLLAMA_CHAT_URL=boot+'/api/chat', VOOL_OLLAMA_PS_URL=boot+'/api/ps',
                   VOOL_OLLAMA_TAGS_URL=boot+'/api/tags', VOOL_FORCE_OLLAMA_MODEL='disclosure-boot:scripted')
        for name in list(env):
            if name.upper().endswith(('_API_KEY', '_API_TOKEN', '_ACCESS_TOKEN', '_AUTH_TOKEN')):
                env.pop(name, None)
        return env

    def register_provider(self):
        super().register_provider()
        script = """
from core.model_registry import ModelRegistry
from storage.model_provider_manifest import get_provider_manifest
from core.policy_engine import set_operator_policy_values
from core.cloud_escalation_policy import set_free_cloud_enabled
from core.contacts import store
from storage.migrations import run_migrations
run_migrations()
set_operator_policy_values({'system.local_only_mode': False, 'network.outbound_enabled': True,
                            'system.allow_web_fallback': True})
set_free_cloud_enabled(True)
store.create_contact(display_name='Mira Example',actor=store.ACTOR_OWNER)
manifest=get_provider_manifest('disclosure-recording','disclosure-recording:scripted')
manifest.runtime_config.update(native_ollama_chat=False,api_path='/v1/chat/completions')
manifest.metadata.update(deployment_class='cloud',runtime_family='openai-compatible')
ModelRegistry().register_manifest(manifest)
"""
        result = subprocess.run([sys.executable, "-c", script], env=self.env(), cwd=rig.REPO_ROOT,
                                capture_output=True, timeout=30, text=True)
        assert result.returncode == 0, result.stdout + result.stderr


def _json(url, body=None):
    request = Request(url, data=json.dumps(body).encode() if body is not None else None,
                      headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=10) as response:
        return json.load(response)


@pytest.mark.parametrize("choice", ["send", "replace", "parts", "cancel"])
def test_real_daemon_attachment_asks_and_sends_the_shown_bytes(tmp_path, choice):
    raw = []
    def answer(body):
        text = rig._all_text(body.get("messages", []))
        if "[Person 1]" in text:
            return "[Person 1] runs the workshop. It opens at noon."
        return "The document describes a repair workshop and its opening hours."

    provider = rig.CapturingProvider(reply_fn=answer)
    handler = provider._server.RequestHandlerClass
    original = handler.do_POST
    original_get = handler.do_GET

    def inventory(self):
        if self.path.startswith("/api/tags"):
            # A separate, scripted local capability keeps the fresh-home
            # no-model front door from ending the task before the pinned
            # cloud adapter. It is a fake outside server, never a local LLM. It also lists the
            # model it serves: on the v0.8 line a loopback server's own inventory decides whether
            # its model is installed, and an unlisted pin is never routed.
            return self._send({"models": [{"name": "disclosure-local:scripted", "size": 0},
                                          {"name": "disclosure-recording:scripted", "size": 0}]})
        return original_get(self)

    def capture(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        raw.append(body)
        self.rfile = io.BytesIO(body)
        return original(self)

    handler.do_POST = capture
    handler.do_GET = inventory
    home = tmp_path / "daemon-home"
    daemon = CloudDaemon(home, provider=provider, model="disclosure-recording:scripted",
                         provider_name="disclosure-recording")
    with _provider_context(provider):
        daemon.start()
        _resource("daemon", daemon.process.pid, daemon.port, "running")
        try:
            certification = daemon.certify(timeout=60)
            assert certification.get("state") == "verified", certification
            raw.clear()
            provider.reset()
            session = rig.canonical_session("disclosure-document-" + choice)
            attachment = daemon.upload(data=b"Mira Example runs the workshop. Email mira@example.invalid. It opens at noon.\n", name="workshop.txt",
                                       declared_type="text/plain", session_id=session)
            attachment_id = attachment.get("attachment_id") or attachment.get("attachment", {}).get("id") or attachment.get("id")
            assert attachment_id, attachment
            with ThreadPoolExecutor() as executor:
                result = executor.submit(daemon.chat, "Summarize the attached workshop document.",
                                         session_id=session, attachments=[attachment_id], turn_id="disclosure-" + choice)
                deadline = time.monotonic() + 35
                observed = []
                while time.monotonic() < deadline and not result.done():
                    pending = _json(daemon.base_url + "/api/disclosure/pending")["requests"]
                    for preview in pending:
                        if preview["state"] != "waiting":
                            continue
                        observed.append(preview)
                        decision = {"id": preview["id"], "sha256": preview["sha256"],
                                    "action": choice if len(observed) == 1 else "send"}
                        if decision["action"] == "parts":
                            sections = []
                            for index, message in enumerate(json.loads(preview["body"]).get("messages", [])):
                                content = message.get("content")
                                if isinstance(content, list):
                                    sections.extend(f"b:{index}:{n}" for n, block in enumerate(content)
                                                    if "Mira Example" not in json.dumps(block))
                                elif "Mira Example" not in str(content):
                                    sections.append(f"m:{index}")
                            decision["sections"] = sections
                        _json(daemon.base_url + "/api/disclosure/decision", decision)
                    time.sleep(0.05)
                if not result.done():
                    for preview in _json(daemon.base_url + "/api/disclosure/pending")["requests"]:
                        if preview["state"] == "waiting":
                            _json(daemon.base_url + "/api/disclosure/decision", {
                                "id": preview["id"], "sha256": preview["sha256"], "action": "cancel"})
                response = result.result(timeout=10)
            evidence = os.environ.get("VOOL_DISCLOSURE_EVIDENCE_ROOT")
            if evidence:
                Path(evidence, "served-choice-" + choice + ".json").write_text(json.dumps({
                    "response": response, "previews": observed, "calls": provider.calls,
                    "attachment": attachment, "received": [b.decode() for b in raw]}, indent=2))
            assert observed, {"response": response, "calls": provider.calls, "log": daemon.log_tail()}
            assert any("workshop" in p["body"] for p in observed)
            if choice == "cancel":
                assert raw == [], {"raw": raw, "response": response}
            elif choice == "send":
                for preview in observed:
                    assert preview["body"].encode("utf-8") in raw
            else:
                assert len(observed) >= 2
                assert observed[-1]["body"].encode("utf-8") in raw
                assert all(b"Mira Example" not in body and b"mira@example.invalid" not in body for body in raw)
                if choice == "replace":
                    assert "Mira Example" in response["message"]["content"], response
            evidence = os.environ.get("VOOL_DISCLOSURE_EVIDENCE_ROOT")
            if evidence:
                Path(evidence, "served-M1.json").write_text(json.dumps({
                    "response": response, "previews": observed, "received": [b.decode() for b in raw]}, indent=2))
        finally:
            daemon.stop()
            _resource("daemon", daemon.process.pid, daemon.port, "stopped")
