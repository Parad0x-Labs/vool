"""Normal daemon entrypoint with only its fake external TLS service routed locally.

No tool, permission, model response or monetary authority is replaced here.
Production destination validation runs before this real socket/TLS transport.
Public-address DNS enforcement is tested separately; this fixture deliberately
routes the three declared service names to a disposable loopback HTTPS server.
"""
from __future__ import annotations

import json
import runpy
import socket
import ssl
import sys
from pathlib import Path


def main():
    config = json.loads(sys.stdin.readline())
    if config.get("upgrade"):
        from core import cloud_escalation_policy
        from core.credential_store import store_credential

        for slot in ("llm.cloud.usepod", "security.eyebrow"):
            store_credential(slot, config["legacy_access"])
        store_credential("llm.cloud.usepod_origin", "https://api.usepod.ai")
        store_credential("llm.cloud.openai", config["unselected_access"])
        assert cloud_escalation_policy._write_raw({
            "policy": {"mode": "auto", "provider": "usepod", "model": "old-model", "daily_cap": 25},
            "chat_models": {"openclaw:" + "a" * 20: {"provider": "usepod", "model": "old-model", "revision": 2}},
            "usage": {"date": "2026-10-23", "count": 3},
        })
    from core import plugin_services

    original = plugin_services.native_request
    context = ssl.create_default_context(cafile=config["ca"])

    def connect(host, *, timeout, call=None):
        assert host in {"api.example.org", "api.usepod.ai", "api.eyebrow.cc"}
        if call:
            call.check()
        with Path(config["routes"]).open("a") as handle:
            handle.write(json.dumps({"host": host, "port": config["service_port"]}) + "\n")
        return socket.create_connection(("127.0.0.1", config["service_port"]), timeout=timeout)

    def request(*args, **kwargs):
        kwargs.setdefault("_connector", connect)
        kwargs.setdefault("_tls_context", context)
        return original(*args, **kwargs)

    plugin_services.native_request = request
    sys.argv = ["apps.vool_api_server", *sys.argv[1:]]
    runpy.run_module("apps.vool_api_server", run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
