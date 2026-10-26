"""A real daemon and owned HTTP transports for owner-boundary proofs.

Only the model's choice is scripted. Ingress, turn reasoning, search, tool
execution and finalization are production code. Every Python child inherits a
socket seal, including provider-transfer workers, so it cannot reach an installed
model or any service outside this fixture.
"""
from __future__ import annotations

import ipaddress
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psutil

from tests._reader_served_rig import CapturingProvider, ServedDaemon

PUBLIC_HOST = "news.owner-boundary.example.test"
PUBLIC_ADDRESS = "8.8.8.8"
_INSTALLED = False


def install_fixture_network():
    global _INSTALLED
    if _INSTALLED:
        return
    config = json.loads(os.environ["VOOL_OWNER_NETWORK_FIXTURE"])
    ports = {int(config[key]) for key in ("model_port", "search_port", "api_port")}
    hosts = {"127.0.0.1", "localhost", PUBLIC_HOST, PUBLIC_ADDRESS, config["lan_ip"]}
    resolve, connect = socket.getaddrinfo, socket.socket.connect

    def dns(host, port, *args, **kwargs):
        if str(host) == PUBLIC_HOST:
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (PUBLIC_ADDRESS, int(port))) ]
        if str(host) not in hosts:
            raise socket.gaierror("fixture DNS refuses an unowned host")
        return resolve(host, port, *args, **kwargs)

    def deliver(sock, address):
        if isinstance(address, tuple) and address[0] == PUBLIC_ADDRESS:
            return connect(sock, ("127.0.0.1", int(config["search_port"])))
        return connect(sock, address)

    socket.getaddrinfo = dns
    socket.socket.connect = deliver
    # Import after installing the transport map: the seal delegates to that map
    # while retaining its union-of-prohibitions ownership.
    from tests import _network_seal

    def owned_only(_kind, address):
        if isinstance(address, str):
            return  # in-process Unix sockets, never a network destination
        if not isinstance(address, tuple) or str(address[0]) not in hosts or int(address[1]) not in ports:
            raise OSError("fixture refuses an unowned network destination")

    _network_seal.push("owner-network-fixture", owned_only)
    _INSTALLED = True


def lan_address():
    interfaces = psutil.net_if_addrs()
    for name in sorted(interfaces, key=lambda value: (not value.startswith("en"), value)):
        for address in interfaces[name]:
            if address.family == socket.AF_INET:
                ip = ipaddress.ip_address(address.address)
                if not (ip.is_loopback or ip.is_unspecified or ip.is_multicast):
                    return str(ip)
    raise RuntimeError("the served LAN proof requires an actual non-loopback IPv4 address")


class SearchTransport:
    def __init__(self):
        self.searches = []
        self.pages = []
        self.title = "Fixture research bulletin " + uuid.uuid4().hex[:8]
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                if self.path.startswith("/search"):
                    fixture.searches.append(self.path)
                    body = json.dumps({"results": [{
                        "title": fixture.title,
                        "url": f"http://{PUBLIC_HOST}:{fixture.port}/article",
                        "content": "A dated fixture bulletin about geothermal research and public projects.",
                        "engine": "fixture",
                        "score": 1,
                    }]}).encode()
                    mime = "application/json"
                else:
                    fixture.pages.append(self.path)
                    body = ("<html><h1>Fixture research bulletin</h1><p>" +
                            "Geothermal research findings were published in this public fixture bulletin. " * 30 +
                            "</p></html>").encode()
                    mime = "text/html"
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.port}"

    def reset(self):
        self.searches.clear()
        self.pages.clear()

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)


def model_choice(body):
    schema = body.get("format")
    structured = schema == "json" or (isinstance(schema, dict) and "intent" in schema.get("properties", {}))
    if structured:
        text = "\n".join(str(m.get("content") or "") for m in body.get("messages", []) if m.get("role") == "user")
        intent = next((name for name in ("web.search", "token.research", "pay.x402") if "fixture-intent=" + name in text), "respond.direct")
        arguments = {"query": "geothermal projects latest news"}
        if intent == "token.research":
            arguments = {"query": "fixture asset", "token": "fixture asset"}
        elif intent == "pay.x402":
            arguments = {"url": "https://payments.example.test/resource", "max_amount": "0.01"}
        return json.dumps({"intent": intent, "arguments": arguments, "confidence": 1.0})
    return "I received the request."


class NetworkDaemon(ServedDaemon):
    def __init__(self, home: Path, *, search: SearchTransport, provider: CapturingProvider):
        super().__init__(home, provider=provider, model="owner-boundary:fixture", provider_name="owner-boundary-fixture")
        self.search = search
        self.lan_ip = lan_address()

    def env(self):
        env = super().env()
        for key in list(env):
            if key.endswith(("_API_KEY", "_ACCESS_TOKEN")) or key.startswith(("DISCORD_", "TELEGRAM_", "SLACK_", "USEPOD_")):
                env.pop(key, None)
        bootstrap = self.home / "fixture-bootstrap"
        bootstrap.mkdir(parents=True, exist_ok=True)
        (bootstrap / "sitecustomize.py").write_text(
            "import os, sys\ntry:\n from tests._owner_network_rig import install_fixture_network\n install_fixture_network()\n"
            "except BaseException as exc:\n print('fixture seal failed:', repr(exc), file=sys.stderr)\n os._exit(97)\n",
            encoding="utf-8",
        )
        env.update({
            "PYTHONPATH": os.pathsep.join((str(bootstrap), env["PYTHONPATH"])),
            "HOME": str(self.home / "process-home"),
            "NO_PROXY": "*", "no_proxy": "*",
            "VOOL_ENABLE_WEB": "1", "SEARXNG_URL": self.search.base_url,
            "WEB_SEARCH_PROVIDER_ORDER": "searxng", "ALLOW_BROWSER_FALLBACK": "0",
            "VOOL_ALLOWED_HOSTS": "127.0.0.1,localhost," + self.lan_ip,
            "VOOL_OWNER_NETWORK_FIXTURE": json.dumps({"model_port": self.provider.port,
                "search_port": self.search.port, "api_port": self.port, "lan_ip": self.lan_ip}),
        })
        for key in ("VOOL_DISABLE_WEB", "VOOL_NO_WEB"):
            env.pop(key, None)
        return env

    def start(self, *, timeout=120):
        self.home.mkdir(parents=True, exist_ok=True)
        config = {"system": {"local_only_mode": False, "allow_web_fallback": True},
                  "network": {"outbound_enabled": True},
                  "web": {"allowed_engines": ["searxng"], "provider_order": ["searxng"],
                          "searxng_url": self.search.base_url, "playwright_enabled": False,
                          "allow_browser_fallback": False}}
        seed = ("from core.runtime_paths import active_config_home_dir\nimport yaml\n"
                "p=active_config_home_dir()/'default_policy.yaml'\np.parent.mkdir(parents=True,exist_ok=True)\n"
                f"p.write_text(yaml.safe_dump({config!r}),encoding='utf-8')\n")
        seeded = subprocess.run([sys.executable, "-c", seed], cwd=str(Path(__file__).resolve().parents[1]),
                                env=self.env(), capture_output=True, text=True, timeout=30)
        if seeded.returncode:
            raise RuntimeError("fixture policy setup failed: " + seeded.stderr)
        self.register_provider()
        with self.log_path.open("wb") as log:
            self.process = subprocess.Popen([sys.executable, "-m", "apps.vool_api_server", "--port", str(self.port),
                                             "--bind", "0.0.0.0"], cwd=str(Path(__file__).resolve().parents[1]),
                                            env=self.env(), stdout=log, stderr=subprocess.STDOUT,
                                            stdin=subprocess.DEVNULL, start_new_session=True)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"fixture daemon exited {self.process.returncode}: {self.log_tail()}")
            try:
                with urllib.request.urlopen(self.base_url + "/healthz", timeout=2) as response:
                    if response.status == 200:
                        return self
            except (OSError, urllib.error.URLError):
                time.sleep(0.2)
        raise RuntimeError("fixture daemon did not become ready: " + self.log_tail())

    def request(self, path, body=None, *, remote=False, stream=False):
        host = self.lan_ip if remote else "127.0.0.1"
        headers = {"Host": f"127.0.0.1:{self.port}", "Content-Type": "application/json",
                   "X-Forwarded-For": "127.0.0.1", "Forwarded": "for=127.0.0.1"}
        request = urllib.request.Request(f"http://{host}:{self.port}" + path,
                                         data=None if body is None else json.dumps(body).encode(), headers=headers)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=90) as response:
            data = response.read()
            return [json.loads(line) for line in data.splitlines() if line] if stream else json.loads(data)

    def turn(self, text, *, session, remote=False, channel=False, stream=False):
        body = {"model": self.model, "mode": "auto", "session_id": session,
                "messages": [{"role": "user", "content": text}], "stream": stream,
                "source_context": {"allow_remote_fetch": True, "_owner_local": True, "_http_ingress": False}}
        if channel:
            body["source_context"].update(surface="channel", platform="discord")
        return self.request("/api/chat", body, remote=remote, stream=stream)
