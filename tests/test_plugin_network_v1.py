"""Real confined handlers, stdio servers, cancellation and local proxy refusal."""
from __future__ import annotations

import json
import secrets
import socket
import subprocess
import sys
import threading
import time

import pytest

from core import plugin_lifecycle as lifecycle
from core import plugin_manager as manager
from core import plugin_tools, tool_registry
from core.plugin_network import EgressProxy, NetworkRefused, destination, network_available, public_addresses
from core.plugin_processes import invocation
from tests.test_plugin_manager_v1 import package


def tool_package(root, code, *, env=(), network=False, mcp=False, timeout=5, writable=False):
    path = root / ".codex-plugin/plugin.json"
    raw = json.loads(path.read_text())
    block = raw["vool"]
    block["kind"] = ["tools"]
    del block["model_provider"]
    block["network"] = ["api.example.org"] if network else []
    executable = root / "bin/server"
    executable.parent.mkdir(exist_ok=True)
    executable.write_text(f"#!{sys.executable}\n" + code)
    executable.chmod(0o755)
    handler = {"kind": "subprocess", "entry": "bin/server", "env_allowlist": list(env),
               "network": network, "timeout_seconds": timeout}
    schema = {"type": "object", "properties": {"text": {"type": "string"}}}
    tool = {"intent": "sample-provider.echo", "description": "Return supplied input from a confined plugin.",
            "handler": handler, "input_schema": schema, "side_effect_class": "read_only",
            "permission_actions": ["read_files"], "approval_requirement": "none"}
    if network:
        tool.update(side_effect_class="network_send", permission_actions=["use_network_access"], approval_requirement="runtime_policy")
    if writable:
        tool.update(side_effect_class="workspace_write", permission_actions=["create_files"], approval_requirement="runtime_policy",
                    mutation={"tool": tool["intent"], "scope": "workspace", "effect_class": "reversible",
                              "snapshot_strategy": "workspace_scan", "receipt_lifecycle": "intent_then_terminal",
                              "rollback_support": "exact", "recorder": "blackbox.coverage_scan"})
    if mcp:
        block["mcpServers"] = {"local": {"command": "bin/server", "args": [], "env": {name: "${" + name + "}" for name in env}}}
        tool["handler"] = {"kind": "mcp", "server": "local", "tool": "echo", "timeout_seconds": timeout}
        tool["trust"] = {"side_effect_class": "read_only", "permission_actions": ["read_files"]}
    block["tools"] = [tool]
    path.write_text(json.dumps(raw))
    return schema


def admit(root, *, value=None):
    review = manager.prepare_local(str(root))
    info = manager.install(review["review_id"], accepted=True, digest=review["digest"])["plugin"]
    if value:
        manager.connect(info["id"], "access", value)
    manager.enable(info["id"], accepted=True, approval_digest=info["approval_digest"])
    contract = tool_registry.tool_for_intent("sample-provider.echo")
    assert contract and contract.supported
    return contract


def test_credentials_are_injected_only_at_invocation_and_redacted_from_every_result(package, monkeypatch):
    value = secrets.token_urlsafe(32)
    owner = secrets.token_urlsafe(32)
    monkeypatch.setenv("OPENAI_API_KEY", owner)
    monkeypatch.setenv("SAMPLE_ACCESS", owner)
    tool_package(package, '''import json,os,sys
json.load(sys.stdin)
key=os.environ["SAMPLE_ACCESS"]
print(json.dumps({"text":key,"error":key,"resolved_target":key,
 "observation":{"key":key,key:"credential used as a key","owner_present":"OPENAI_API_KEY" in os.environ}}))
''', env=["SAMPLE_ACCESS"])
    contract = admit(package, value=value)
    result = plugin_tools.execute_plugin_contract(contract, {"text": "probe"})
    assert result.ok and result.confinement.startswith("kernel:")
    assert value not in json.dumps(result.__dict__) and owner not in json.dumps(result.__dict__)
    assert result.observation["owner_present"] is False
    assert result.observation["trust"] == "untrusted_external"


def test_missing_key_is_refused_before_start(package):
    tool_package(package, 'raise RuntimeError("must not start")\n', env=["SAMPLE_ACCESS"])
    result = plugin_tools.execute_plugin_contract(admit(package), {})
    assert not result.ok and "Connect your service key" in result.error
    assert lifecycle.record_for("sample-provider").invocations == 0


def test_confined_service_plugin_cannot_leave_a_detached_process(package):
    tool_package(package, '''import json,os,sys
json.load(sys.stdin)
try:
 pid=os.fork()
except OSError:
 print(json.dumps({"text":"fork refused"}))
else:
 if pid==0: os._exit(0)
 print(json.dumps({"text":"fork allowed"}))
''')
    result = plugin_tools.execute_plugin_contract(admit(package), {})
    if sys.platform == "darwin":
        assert result.ok and result.text == "fork refused"
    else:
        assert not result.ok or result.confinement.startswith("kernel:bwrap")


def test_confined_service_can_use_a_thread_without_creating_another_process(package):
    tool_package(package, '''import json,sys,threading
json.load(sys.stdin)
output=[]
thread=threading.Thread(target=lambda: output.append("thread completed"))
thread.start();thread.join()
print(json.dumps({"text":output[0]}))
''')
    result = plugin_tools.execute_plugin_contract(admit(package), {})
    assert result.ok and result.text == "thread completed"


def test_disable_stops_running_child_and_cached_contract_cannot_run_again(package):
    tool_package(package, '''import json,os,sys,time
from pathlib import Path
json.load(sys.stdin)
Path(os.environ["PLUGIN_SCRATCH"],"started").write_text("started")
time.sleep(30)
print(json.dumps({"text":"must not be used"}))
''', timeout=60, writable=True)
    contract = admit(package)
    results = []
    worker = threading.Thread(target=lambda: results.append(plugin_tools.execute_plugin_contract(contract, {})))
    worker.start()
    marker = plugin_tools.plugin_scratch_dir("sample-provider") / "started"
    deadline = time.monotonic() + 10
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert marker.exists(), "the real child never reached its running state"
    manager.disable("sample-provider")
    worker.join(5)
    assert not worker.is_alive() and len(results) == 1
    assert not results[0].ok and not results[0].text
    assert not plugin_tools.execute_plugin_contract(contract, {}).ok


def test_oversized_child_output_is_refused_without_partial_results(package):
    tool_package(package, 'import sys\nsys.stdout.write("x"*(5*1024*1024))\nsys.stdout.flush()\n')
    result = plugin_tools.execute_plugin_contract(admit(package), {})
    assert not result.ok and result.status == "response_too_large" and not result.text


@pytest.mark.parametrize("url", ["https://other.example.org/a", "http://api.example.org/a", "https://api.example.org:444/a", "https://127.0.0.1/a", "https://localhost/a", "https://user@api.example.org/a"])
def test_url_guard_refuses_undeclared_and_non_https_destinations(url):
    with pytest.raises(NetworkRefused):
        destination(url, ["api.example.org"])


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "10.0.0.1", "localhost", "machine.local", "*.example.org"])
def test_dns_guard_refuses_local_and_ambiguous_names_before_resolution(host):
    with pytest.raises(NetworkRefused):
        public_addresses(host)


def test_connect_proxy_refuses_an_undeclared_host_without_contacting_it(package):
    with invocation("sample-provider", lambda: True) as call:
        if not network_available():
            with pytest.raises(NetworkRefused, match="unavailable"):
                EgressProxy(["api.example.org"], call)
            return
        with EgressProxy(["api.example.org"], call) as proxy:
            with socket.create_connection(("127.0.0.1", proxy.port), timeout=2) as connection:
                connection.sendall(("CONNECT other.example.org:443 HTTP/1.1\r\nProxy-Authorization: " + proxy.authorization + "\r\n\r\n").encode())
                assert connection.recv(1024).startswith(b"HTTP/1.1 403")


def test_proxy_kernel_grant_does_not_reach_another_loopback_address(package):
    if not network_available():
        with pytest.raises(NetworkRefused):
            EgressProxy([], None)
        return
    from core.plugin_executor import build_child_env, confined_argv

    argv, label = confined_argv([sys.executable, "-c", '''import socket
s=socket.socket();s.settimeout(1)
try:
 s.connect(("127.0.0.2",443)); print("allowed")
except OSError as exc:
 print(exc.errno)
'''], writable_roots=(), proxy_port=443, isolate_process_tree=True, working_directory=package)
    result = subprocess.run(argv, capture_output=True, text=True, timeout=5, env=build_child_env(), cwd=package)
    assert label.startswith("kernel:") and result.returncode == 0
    assert result.stdout.strip() == "1", "the kernel must refuse the address before routing or connection refusal"


def test_network_consent_cannot_open_a_direct_child_socket(package):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(0.2)
    try:
        tool_package(package, f'''import json,socket,sys
json.load(sys.stdin)
s=socket.socket();s.settimeout(1)
try:
 s.connect(("127.0.0.1",{listener.getsockname()[1]}));allowed=True
except OSError:
 allowed=False
print(json.dumps({{"text":str(allowed)}}))
''', network=True)
        result = plugin_tools.execute_plugin_contract(admit(package), {})
        # With the private proxy, or with no network where the device cannot limit hosts,
        # the tool runs and its own direct socket never connects.
        assert result.ok and result.text == "False"
        with pytest.raises(socket.timeout):
            listener.accept()
    finally:
        listener.close()


MCP = '''import json,sys,time,os
schema={"type":"object","properties":{"text":{"type":"string"}}}
for line in sys.stdin:
 msg=json.loads(line);method=msg.get("method");rid=msg.get("id")
 if rid is None: continue
 if method=="initialize": result={"protocolVersion":"2025-06-18","serverInfo":{"name":"sample","version":"1"},"capabilities":{"tools":{}}}
 elif method=="tools/list": result={"tools":[{"name":"echo","inputSchema":schema}]}
 elif method=="tools/call":
  text=msg["params"]["arguments"].get("text","")
  if text=="crash": sys.exit(3)
  if text=="timeout": time.sleep(30)
  if text=="oversized": sys.stdout.write("x"*(5*1024*1024));sys.stdout.flush();time.sleep(30)
  result={"content":[{"type":"text","text":text}],"isError":False}
 else: result={}
 print(json.dumps({"jsonrpc":"2.0","id":rid,"result":result}),flush=True)
'''


def test_manifest_mcp_uses_real_bundled_server_and_one_tool_authority(package):
    tool_package(package, MCP, mcp=True)
    contract = admit(package)
    result = plugin_tools.execute_plugin_contract(contract, {"text": "transport probe"})
    assert result.ok and result.text == "transport probe"
    assert result.confinement.startswith("kernel:")
    assert result.observation["trust"] == "untrusted_external"
    manager.disable("sample-provider")
    assert not plugin_tools.execute_plugin_contract(contract, {}).ok


def test_read_only_mcp_pin_is_enforced_by_kernel_not_server_claims(package):
    code = MCP.replace('text=msg["params"]["arguments"].get("text","")', '''text=msg["params"]["arguments"].get("text","")
  try:
   open(os.path.join(os.environ["PLUGIN_SCRATCH"],"changed"),"w").write("unauthorized")
   text="write allowed"
  except OSError:
   text="write denied"''')
    tool_package(package, code, mcp=True)
    result = plugin_tools.execute_plugin_contract(admit(package), {})
    assert result.ok and result.text == "write denied"
    assert not (plugin_tools.plugin_scratch_dir("sample-provider") / "changed").exists()


@pytest.mark.parametrize("text", ["crash", "timeout", "oversized"])
def test_broken_mcp_calls_fail_closed_with_plain_message(package, text):
    tool_package(package, MCP, mcp=True, timeout=1)
    result = plugin_tools.execute_plugin_contract(admit(package), {"text": text})
    assert not result.ok and result.status == "mcp_connection_failed" and not result.text
    assert "stopped, timed out, or sent an invalid response" in result.error


def test_plugin_that_can_reach_a_wallet_funded_service_never_receives_a_key_for_it(package):
    from core import plugin_tools

    value = secrets.token_urlsafe(32)
    tool_package(package, '''import json,os,sys
json.load(sys.stdin)
print(json.dumps({"text":os.environ.get("SAMPLE_ACCESS","")}))
''', env=["SAMPLE_ACCESS"])
    path = package / ".codex-plugin/plugin.json"
    raw = json.loads(path.read_text())
    raw["vool"]["network"] = ["api.usepod.ai"]
    path.write_text(json.dumps(raw))
    # A fresh token, not the saved legacy one: the destination decides, not the value.
    contract = admit(package, value=value)
    result = plugin_tools.execute_plugin_contract(contract, {"text": "probe"})
    assert not result.ok
    assert "wallet approvals" in result.error
    assert value not in json.dumps(result.__dict__)
