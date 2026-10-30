"""Plugins that run their own code, on a real Mac, without the optional isolated runtime.

Routed to the macOS job of CI (the Seatbelt sandbox exists only there). These run the real
install, consent, enable, invoke, disable and uninstall path: no confinement override, no
Docker socket, nothing mocked.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import shutil
import sys
from pathlib import Path

import pytest

from core import plugin_lifecycle as lifecycle
from core import plugin_manager as manager
from core import plugin_tools, tool_registry
from tests.test_plugin_manager_v1 import installed, package
from tests.test_plugin_network_v1 import admit, tool_package

TEMPLATE = Path(__file__).resolve().parents[1] / "examples/plugins/template"


@pytest.fixture(autouse=True)
def _no_isolated_runtime(monkeypatch):
    # A stock Mac: no container runtime is configured for VOOL.
    monkeypatch.delenv("VOOL_PLUGIN_DOCKER_SOCKET", raising=False)
    monkeypatch.delenv("VOOL_PLUGIN_CONFINEMENT", raising=False)
    assert sys.platform == "darwin"


def test_template_plugin_runs_in_the_macos_sandbox_from_install_to_uninstall(package):
    shutil.rmtree(package)
    shutil.copytree(TEMPLATE, package)
    info = installed(package)
    assert info["executable_runtime_available"] is True and not info["executable_runtime_refusal"]
    manager.enable(info["id"], accepted=True, approval_digest=info["approval_digest"])
    contract = tool_registry.tool_for_intent("vool-plugin-template.sha256")
    assert contract and contract.supported

    text = "plugin on a Mac " + secrets.token_hex(8)
    result = plugin_tools.execute_plugin_contract(contract, {"text": text})
    assert result.ok, result.error
    assert result.confinement == "kernel:sandbox-exec"
    output = json.loads(result.text)
    assert output["sha256"].replace(":", "") == hashlib.sha256(text.encode()).hexdigest()
    assert output["bytes"] == len(text.encode())

    manager.disable(info["id"])
    stopped = plugin_tools.execute_plugin_contract(contract, {"text": text})
    assert not stopped.ok
    assert not lifecycle.is_available(info["id"])

    manager.uninstall(info["id"], remove_key=True)
    assert lifecycle.record_for(info["id"]).stage == lifecycle.STAGE_UNINSTALLED
    assert not Path(lifecycle.record_for(info["id"]).root).exists()


def test_handler_key_travels_only_over_its_private_input_on_macos(package, monkeypatch):
    value = secrets.token_urlsafe(32)
    tool_package(package, '''import json,os,sys
payload=json.load(sys.stdin)
given=payload["context"]["credentials"].get("SAMPLE_ACCESS","")
print(json.dumps({"text":json.dumps({"in_environment":"SAMPLE_ACCESS" in os.environ,
 "value_in_environment":any(given and given in v for v in os.environ.values()),
 "received":bool(given),"length":len(given)})}))
''', env=["SAMPLE_ACCESS"])
    contract = admit(package, value=value)
    result = plugin_tools.execute_plugin_contract(contract, {"text": "probe"})
    assert result.ok, result.error
    assert result.confinement == "kernel:sandbox-exec"
    seen = json.loads(result.text)
    # The starting environment is readable by any same-user program on macOS; the key is
    # never placed there, only on the handler's private input.
    assert seen == {"in_environment": False, "value_in_environment": False, "received": True, "length": len(value)}
    assert value not in json.dumps(result.__dict__)


def test_server_that_takes_its_key_from_the_environment_stays_off_on_a_stock_mac(package):
    tool_package(package, "import sys\nsys.exit(0)\n", env=["SAMPLE_ACCESS"], mcp=True)
    review = manager.prepare_local(str(package))
    info = manager.install(review["review_id"], accepted=True, digest=review["digest"])["plugin"]
    assert info["executable_runtime_available"] is False
    assert "cannot keep private" in info["executable_runtime_refusal"]
    with pytest.raises(lifecycle.LifecycleError, match="cannot keep private"):
        manager.enable(info["id"], accepted=True, approval_digest=info["approval_digest"])
    assert not lifecycle.is_available(info["id"])


def test_plugin_network_reaches_only_its_private_proxy_on_macos(package):
    tool_package(package, '''import json,os,socket,sys
from urllib.parse import urlsplit
json.load(sys.stdin)
direct=socket.socket();direct.settimeout(3)
try:
 direct.connect(("1.1.1.1",443));direct_ok=True
except OSError:
 direct_ok=False
proxy=urlsplit(os.environ["HTTPS_PROXY"])
via=socket.create_connection((proxy.hostname,proxy.port),timeout=3)
import base64
auth="Basic "+base64.b64encode((proxy.username+":").encode()).decode()
via.sendall(("CONNECT other.example.org:443 HTTP/1.1\\r\\nProxy-Authorization: "+auth+"\\r\\n\\r\\n").encode())
answer=via.recv(64).decode(errors="replace")
print(json.dumps({"text":json.dumps({"direct":direct_ok,"undeclared":answer.split("\\r\\n")[0]})}))
''', network=True)
    contract = admit(package)
    result = plugin_tools.execute_plugin_contract(contract, {"text": "probe"})
    assert result.ok, result.error
    assert result.confinement == "kernel:sandbox-exec"
    seen = json.loads(result.text)
    assert seen["direct"] is False, "the sandbox must refuse every destination but the private proxy"
    assert seen["undeclared"].startswith("HTTP/1.1 403")


def test_plugin_code_cannot_read_another_programs_starting_environment_on_macos(package):
    import subprocess

    marker = "VOOL_SIBLING_" + secrets.token_hex(8)
    sibling = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                               env={"PATH": "/usr/bin:/bin", marker: secrets.token_urlsafe(32)}, start_new_session=True)
    probe = '''import ctypes,json,sys
def read(pid):
 libc=ctypes.CDLL(None,use_errno=True);mib=(ctypes.c_int*3)(1,49,pid)
 length=ctypes.c_size_t(262144);buffer=ctypes.create_string_buffer(length.value)
 code=libc.sysctl(mib,3,buffer,ctypes.byref(length),None,0)
 return buffer.raw[:length.value] if code==0 else b""
'''
    try:
        # The same read outside the sandbox sees the sibling's environment, so the probe is sound.
        unconfined = {}
        exec(probe, unconfined)
        assert marker.encode() in unconfined["read"](sibling.pid)
        tool_package(package, probe + '''payload=json.load(sys.stdin)
data=read(int(payload["arguments"]["text"]))
print(json.dumps({"text":"probe complete","observation":{"visible":MARKER.encode() in data}}))
'''.replace("MARKER", repr(marker)))
        result = plugin_tools.execute_plugin_contract(admit(package), {"text": str(sibling.pid)})
        assert result.ok, result.error
        assert result.confinement == "kernel:sandbox-exec"
        assert result.observation["visible"] is False
    finally:
        sibling.terminate()
        sibling.wait(timeout=5)
