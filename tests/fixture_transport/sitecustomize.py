"""Test-only fixture transport for SCRATCH daemons.

Placed FIRST on a scratch daemon's ``PYTHONPATH`` it is imported by ``site`` at interpreter start.
It does nothing unless ``VOOL_FIXTURE_TRANSPORT_MANIFEST`` names a JSON manifest. With a manifest
it replaces ``urllib.request.urlopen`` -- the one call the governed door
(``core.remote_fetch_policy.open_remote``) makes after its veto, ledger and lifecycle work -- so
every production seam above the socket runs unchanged while the bytes come from dated synthetic
fixtures. Every host the manifest does not name is refused with ``URLError``: a scratch daemon
under this shim can never reach the internet.

Manifest shape::

    {"rules": [
       {"name": "coingecko", "host": "api.coingecko.com", "path_prefix": "/api/v3/simple/price",
        "json": {...}},
       {"name": "yahoo-gold", "host": "query1.finance.yahoo.com", "query_contains": "GC=F",
        "body_file": "/abs/path.json", "content_type": "application/json"},
       {"name": "silver-down", "host": "query1.finance.yahoo.com", "query_contains": "SI=F",
        "status": 429, "reason": "Too Many Requests"},
       {"name": "slow", "host": "html.duckduckgo.com", "delay_s": 1.5, "body": "<html>...</html>",
        "content_type": "text/html"}
    ]}

Rules match in order (host, optional path_prefix, optional query_contains). The manifest is
re-read on every call so a drive can inject and restore faults mid-run. When
``VOOL_FIXTURE_TRANSPORT_LOG`` is set, one JSON line per call (url, rule, outcome, timestamp) is
appended there -- the drive's own record of what the runtime actually asked for.
"""
from __future__ import annotations

import io
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from email.message import Message
from pathlib import Path

_MANIFEST = os.environ.get("VOOL_FIXTURE_TRANSPORT_MANIFEST", "").strip()


def _manifest_dir() -> str:
    return os.path.dirname(os.path.abspath(_MANIFEST)) if _MANIFEST else ""

_LOG = os.environ.get("VOOL_FIXTURE_TRANSPORT_LOG", "").strip()

if _MANIFEST:
    _real_urlopen = urllib.request.urlopen

    class _FixtureResponse(io.BytesIO):
        def __init__(self, body: bytes, url: str, status: int, headers: dict[str, str]) -> None:
            super().__init__(body)
            self.url = url
            self.status = status
            self.code = status
            self.reason = "OK"
            self.headers = Message()
            for key, value in headers.items():
                self.headers[key] = value

        def getcode(self) -> int:
            return self.status

        def geturl(self) -> str:
            return self.url

        def info(self) -> Message:
            return self.headers

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            self.close()
            return False

    def _load_manifest() -> dict:
        try:
            with open(_MANIFEST, encoding="utf-8") as handle:
                data = json.load(handle)
        except Exception:
            return {"rules": []}
        return data if isinstance(data, dict) else {"rules": []}

    def _match(rules: list, url: str) -> dict | None:
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").lower()
        query = urllib.parse.unquote(parsed.query)
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            if str(rule.get("host") or "").lower() != host:
                continue
            prefix = str(rule.get("path_prefix") or "")
            if prefix and not parsed.path.startswith(prefix):
                continue
            needle = str(rule.get("query_contains") or "")
            if needle and needle not in query and needle not in parsed.path:
                continue
            return rule
        return None

    def _record(url: str, rule: dict | None, outcome: str) -> None:
        if not _LOG:
            return
        try:
            with open(_LOG, "a", encoding="utf-8") as handle:
                handle.write(json.dumps({
                    "ts": time.time(), "url": url,
                    "rule": (rule or {}).get("name") if rule else None, "outcome": outcome,
                }) + "\n")
        except Exception:
            pass

    def _body_of(rule: dict) -> bytes:
        if "json" in rule:
            return json.dumps(rule["json"]).encode("utf-8")
        if rule.get("body_file"):
            body_path = Path(str(rule["body_file"])).expanduser()
            if not body_path.is_absolute():
                # Relative body_file resolves against the manifest's own directory, so a
                # fixture set is relocatable (the original manifests carried absolute
                # builder-machine paths; canonical test fixtures use relative names).
                body_path = Path(_manifest_dir()) / body_path
            with open(body_path, "rb") as handle:
                return handle.read()
        return str(rule.get("body") or "").encode("utf-8")

    _LOOPBACK = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}

    def _fixture_urlopen(request, timeout=None, *args, **kwargs):
        url = request.full_url if hasattr(request, "full_url") else str(request)
        # The daemon's own loopback traffic (its scripted provider, its own API) is not the
        # world: it passes through untouched. Only remote hosts are fixtures or refusals.
        if (urllib.parse.urlparse(url).hostname or "").lower() in _LOOPBACK:
            return _real_urlopen(request, *args, timeout=timeout, **kwargs) if timeout is not None else _real_urlopen(request, *args, **kwargs)
        rules = list(_load_manifest().get("rules") or [])
        rule = _match(rules, url)
        if rule is None:
            _record(url, None, "refused_unknown_host")
            raise urllib.error.URLError(
                f"fixture transport: host not in manifest ({urllib.parse.urlparse(url).hostname})"
            )
        delay = rule.get("delay_s")
        if isinstance(delay, (int, float)) and delay > 0:
            time.sleep(float(delay))
        fault = str(rule.get("fault") or "")
        if fault == "timeout":
            _record(url, rule, "injected_timeout")
            raise urllib.error.URLError(TimeoutError("fixture transport: injected timeout"))
        if fault == "unreachable":
            _record(url, rule, "injected_unreachable")
            raise urllib.error.URLError(ConnectionRefusedError(111, "fixture transport: injected unreachable"))
        status = int(rule.get("status") or 200)
        if status >= 400:
            _record(url, rule, f"http_{status}")
            raise urllib.error.HTTPError(
                url, status, str(rule.get("reason") or "injected"), Message(), io.BytesIO(_body_of(rule))
            )
        _record(url, rule, f"http_{status}")
        headers = {"Content-Type": str(rule.get("content_type") or "application/json")}
        return _FixtureResponse(_body_of(rule), url, status, headers)

    urllib.request.urlopen = _fixture_urlopen


# ---------------------------------------------------------------------------------------------
# Optional gate recorder (drives only). With VOOL_FIXTURE_GATE_LOG set, the publication gate's
# INPUT bytes are appended as JSON lines when `core.grounding_publication` is first imported, so a
# served drive can show what the gate adjudicated. Production never sets the variable.
_GATE_LOG = os.environ.get("VOOL_FIXTURE_GATE_LOG", "").strip()
if _GATE_LOG:
    import importlib.abc
    import importlib.machinery
    import sys

    class _GateRecorderFinder(importlib.abc.MetaPathFinder):
        _target = "core.grounding_publication"

        def find_spec(self, fullname, path, target=None):
            if fullname != self._target:
                return None
            spec = importlib.machinery.PathFinder.find_spec(fullname, path)
            if spec is None or spec.loader is None:
                return None
            real_exec = spec.loader.exec_module

            def exec_module(module):
                real_exec(module)
                real_gate = module.gate_publishable_content

                def recording_gate(content, *, turn_id=""):
                    out = real_gate(content, turn_id=turn_id)
                    try:
                        record = out[1] if isinstance(out, tuple) and len(out) > 1 else {}
                        publication = dict((record or {}).get("publication") or {})
                        with open(_GATE_LOG, "a", encoding="utf-8") as handle:
                            handle.write(json.dumps({
                                "ts": time.time(), "turn_id": turn_id, "content": str(content),
                                "published": str(out[0]) if isinstance(out, tuple) else "",
                                "publication": {k: publication.get(k) for k in ("state", "coverage", "supported_claim_count", "withheld_claim_count", "withheld_claims", "support_origin", "detail")},
                                "claims": (publication.get("claim_support") or {}).get("claims"),
                                "bound_note_count": len(list((record or {}).get("bound_notes") or [])),
                                "typed_observation_count": (record or {}).get("typed_observation_count"),
                            }, default=str) + "\n")
                    except Exception:
                        pass
                    return out

                module.gate_publishable_content = recording_gate

            spec.loader.exec_module = exec_module  # type: ignore[method-assign]
            return spec

    sys.meta_path.insert(0, _GateRecorderFinder())
