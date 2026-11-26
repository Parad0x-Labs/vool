"""Offline means nothing leaves this computer, through the HTTP door or around it.

`tests/test_local_only_zero_public_egress.py` proves the HTTP door. These prove the paths that
never used it: any socket or DNS lookup in VOOL's own process during an Offline turn (the
process guard in core.private_web), plugin network, mail, and a picture asked for in an Offline
chat. Each runs the real code with a real Offline turn scope; the only stand-ins are the far ends
(no mail server, no plugin child, no image engine), which must never be reached.
"""
from __future__ import annotations

import socket
from contextlib import contextmanager

import pytest

from core import private_web
from core.auto_local_only_mode import CONTEXT_KEY, OFFLINE_KEY
from core.remote_fetch_policy import offline_refuses, offline_turn_active, remote_fetch_policy_scope
from tests.test_plugin_manager_v1 import package


@contextmanager
def offline_turn(**extra):
    with remote_fetch_policy_scope({"surface": "desktop", OFFLINE_KEY: True, **extra}):
        yield


@contextmanager
def web_turn():
    # "Computer + web": no cloud model, but the web follows its switch. Not Offline.
    with remote_fetch_policy_scope({"surface": "desktop", CONTEXT_KEY: True}):
        yield


@pytest.fixture(autouse=True)
def _route_off(monkeypatch):
    monkeypatch.setattr(private_web, "_guard_allows_public", lambda: True)


# --- the process guard -------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("event", "args"),
    [
        ("socket.connect", (None, ("93.184.216.34", 443))),
        ("socket.connect", (None, ("2606:2800:220:1:248:1893:25c8:1946", 443, 0, 0))),
        ("socket.sendto", (None, ("8.8.8.8", 53))),
        ("socket.getaddrinfo", ("smtp.gmail.com", 587, 0, 0, 0)),
        ("socket.gethostbyname", ("api.telegram.org",)),
        ("socket.gethostbyaddr", ("93.184.216.34",)),
    ],
)
def test_an_offline_turn_cannot_reach_the_internet_from_any_library(event, args):
    with offline_turn():
        with pytest.raises(private_web.OfflineEgressBlocked, match="Offline"):
            private_web._audit(event, args)


@pytest.mark.parametrize(
    ("event", "args"),
    [
        ("socket.connect", (None, ("127.0.0.1", 11434))),       # Ollama on this computer
        ("socket.connect", (None, ("::1", 11435, 0, 0))),
        ("socket.connect", (None, ("169.254.10.20", 11434))),    # a model box on the local network (link-local)
        ("socket.getaddrinfo", ("localhost", 11434, 0, 0, 0)),
        ("socket.getaddrinfo", ("imac.local", 11434, 0, 0, 0)),
        ("socket.getaddrinfo", ("nas", 993, 0, 0, 0)),           # a bare LAN name
        ("socket.getaddrinfo", ("mail.home.arpa", 993, 0, 0, 0)),
    ],
)
def test_an_offline_turn_still_reaches_this_computer_and_the_home_network(event, args):
    with offline_turn():
        private_web._audit(event, args)


def test_outside_an_offline_turn_nothing_is_stopped():
    for scope in (web_turn, None):
        if scope is None:
            assert not offline_turn_active()
            private_web._audit("socket.connect", (None, ("93.184.216.34", 443)))
            private_web._audit("socket.getaddrinfo", ("example.com", 443, 0, 0, 0))
            continue
        with scope():
            assert not offline_turn_active()
            private_web._audit("socket.connect", (None, ("93.184.216.34", 443)))
            private_web._audit("socket.getaddrinfo", ("example.com", 443, 0, 0, 0))


def test_a_worker_thread_of_an_offline_turn_is_held_too():
    import concurrent.futures
    import contextvars

    with offline_turn():
        context = contextvars.copy_context()
        with concurrent.futures.ThreadPoolExecutor(1) as pool:
            future = pool.submit(context.run, private_web._audit, "socket.connect", (None, ("1.1.1.1", 443)))
            with pytest.raises(private_web.OfflineEgressBlocked):
                future.result()


@pytest.mark.parametrize(
    "argv",
    [
        ["curl", "https://example.com/?d=secret"],
        ["git", "clone", "https://example.com/r.git"],
        ["ollama", "pull", "qwen3:8b"],
        ["python3", "-m", "pip", "install", "x"],
        ["open", "https://example.com/?d=secret"],            # the person's own browser
        ["xdg-open", "https://example.com/"],
        ["/usr/bin/chromium", "--proxy-server=socks5://127.0.0.1:9050", "https://example.com/"],
    ],
)
def test_an_offline_turn_starts_no_program_that_goes_online(argv):
    with offline_turn():
        with pytest.raises(private_web.OfflineEgressBlocked, match="Offline"):
            private_web._audit("subprocess.Popen", (argv[0], argv, None, None))


@pytest.mark.parametrize(
    "argv",
    [
        ["open", "/U/Pictures/VOOL-Renders/out.png"],
        ["open", "http://127.0.0.1:11435/chat"],
        ["python3", "main.py", "--port", "8188", "--disable-all-custom-nodes"],  # the image engine
        ["ollama", "list"],
    ],
)
def test_an_offline_turn_still_starts_local_programs(argv):
    with offline_turn():
        private_web._audit("subprocess.Popen", (argv[0], argv, None, None))


def test_a_real_socket_is_stopped_before_it_connects():
    private_web.install_egress_guard(refresh_state=False)
    with offline_turn():
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            with pytest.raises(ConnectionRefusedError, match="Offline"):
                probe.connect(("93.184.216.34", 443))
        finally:
            probe.close()


def test_the_offline_reading_is_the_door_s_own():
    with offline_turn():
        assert offline_refuses("https://example.com")
        assert offline_refuses("")                      # no destination is not a local one
        assert not offline_refuses("smtp://169.254.10.5")
    with remote_fetch_policy_scope({"surface": "desktop", "requested_model": "vool-offline"}):
        assert offline_turn_active()                    # named only in the composer selection
    with web_turn():
        assert not offline_refuses("https://example.com")


# --- plugins ------------------------------------------------------------------------------------

def test_an_offline_turn_gives_a_network_plugin_no_network(package, monkeypatch):
    from core import plugin_executor, plugin_tools
    from core import plugin_lifecycle as lifecycle
    from tests.test_plugin_network_v1 import admit, tool_package

    # Enabling a program plugin needs the device's walls (macOS sandbox, Linux bwrap). The refusal
    # under test comes before any child or proxy exists, so the walls are only asserted present.
    monkeypatch.setattr(plugin_executor, "kernel_confinement_available", lambda: True)

    tool_package(package, 'raise RuntimeError("must not start")\n', network=True)
    contract = admit(package)
    for context in ({"surface": "desktop", OFFLINE_KEY: True}, {"surface": "desktop", "requested_model": "vool-offline"}):
        result = plugin_tools.execute_plugin_contract(contract, {}, source_context=context)
        assert not result.ok and "Offline" in result.error
    with offline_turn():
        result = plugin_tools.execute_plugin_contract(contract, {}, source_context={"surface": "desktop"})
        assert not result.ok and "Offline" in result.error
    assert lifecycle.record_for("sample-provider").invocations == 0


# --- mail ---------------------------------------------------------------------------------------

@pytest.fixture
def mailbox(monkeypatch):
    from core import email_accounts, email_tools

    holder = {"creds": {}}

    class _Selection:
        ok = True
        account = "default"
        status = "ok"
        message = ""

    monkeypatch.setattr(email_accounts, "select_account", lambda kind, account="": _Selection())
    monkeypatch.setattr(email_tools, "_load_account", lambda kind, account: dict(holder["creds"]))

    def _no_wire(*a, **k):
        raise AssertionError("an Offline turn opened a mail connection")

    monkeypatch.setattr(email_tools, "_smtp_connect", _no_wire)
    monkeypatch.setattr(email_tools, "_imap_connect", _no_wire)
    return holder


@pytest.mark.parametrize(
    "creds",
    [
        {"host": "smtp.gmail.com", "port": 587, "username": "me@example.com", "password": "x"},
        {"provider": "gmail", "refresh_token": "x"},
        {"provider": "graph", "refresh_token": "x"},
    ],
)
def test_an_offline_turn_sends_no_mail(mailbox, creds):
    from core import email_tools

    mailbox["creds"] = creds
    with offline_turn():
        result = email_tools.send_email(to="a@example.com", subject="s", body="b")
    assert not result.ok and result.status == "offline"
    assert "Offline" in result.message


def test_an_offline_turn_opens_no_public_mailbox(mailbox):
    from core import email_tools

    mailbox["creds"] = {"host": "imap.gmail.com", "port": 993, "username": "me@example.com", "password": "x"}
    with offline_turn():
        account, creds, refusal = email_tools._selected_account("imap", "")
    assert creds is None and refusal is not None and refusal.status == "offline"


def test_a_mail_server_on_the_home_network_stays_reachable_offline(mailbox):
    from core import email_tools

    mailbox["creds"] = {"host": "169.254.10.5", "port": 993, "username": "me", "password": "x"}
    with offline_turn():
        account, creds, refusal = email_tools._selected_account("imap", "")
    assert refusal is None and creds["host"] == "169.254.10.5"


def test_mail_outside_offline_is_unchanged(mailbox):
    from core import email_tools

    mailbox["creds"] = {"host": "smtp.gmail.com", "port": 587, "username": "me@example.com", "password": "x"}
    with web_turn():
        account, creds, refusal = email_tools._selected_account("smtp", "")
    assert refusal is None and creds["host"] == "smtp.gmail.com"


# --- pictures -----------------------------------------------------------------------------------

def _pictures(monkeypatch, *, has_local: bool):
    import core.resource_governor as gov
    from core import generated_files as gf
    from core import local_media_render as lmr
    from core import media_tools

    monkeypatch.setattr(gov, "plan_render", lambda: (gov.RENDER_TIERS[0], gov.GovernorDecision(ok=True, usable_after_gb=12.0)))
    monkeypatch.setattr(gov, "reclaim_if_pressured", lambda: [])
    monkeypatch.setattr(gf, "record_generated_file", lambda *a, **k: None)
    monkeypatch.setattr(lmr, "local_render_available", lambda: has_local)
    monkeypatch.setattr(media_tools, "has_image_service", lambda *a, **k: True)  # a cloud key exists
    # The engine is not up yet: before Offline picked it, a cloud key won over a cold engine.
    engine = {"up": False}
    monkeypatch.setattr(lmr, "comfyui_reachable", lambda *a, **k: engine["up"])
    monkeypatch.setattr(lmr, "comfyui_autostart_enabled", lambda: True)
    monkeypatch.setattr(lmr, "ensure_comfyui", lambda **k: (engine.__setitem__("up", True), (True, "up"))[1])
    rendered: list[str] = []
    monkeypatch.setattr(lmr, "run_local_image_render", lambda prompt, **k: (rendered.append(prompt) or True, "/U/Pictures/VOOL-Renders/out.png"))
    cloud = {"n": 0}

    def _cloud(*a, **k):
        cloud["n"] += 1
        raise AssertionError("an Offline chat sent the prompt to an online image service")

    monkeypatch.setattr(media_tools, "generate_image", _cloud)
    import core.authorized_tool_execution as ate

    monkeypatch.setattr(ate, "execute_authorized_runtime_tool", _cloud)
    return rendered, cloud


class _Agent:
    def _emit_runtime_event(self, ctx, **kwargs):
        pass

    def _fast_path_result(self, *, session_id, user_input, response, confidence, source_context, reason):
        return {"response": response, "reason": reason}


def test_an_offline_picture_is_made_on_this_computer_even_with_a_cloud_key(monkeypatch):
    from core.agent_runtime import fast_paths_media as fpm

    rendered, cloud = _pictures(monkeypatch, has_local=True)
    context = {"operating_mode": "auto", OFFLINE_KEY: True}
    res = fpm.maybe_handle_image_generation(_Agent(), "draw me a lighthouse at dusk", session_id="s", source_context=context)
    assert rendered and cloud["n"] == 0
    assert "local render" in res["response"]


def test_an_offline_picture_without_the_engine_is_refused_not_sent_online(monkeypatch):
    from core.agent_runtime import fast_paths_media as fpm

    rendered, cloud = _pictures(monkeypatch, has_local=False)
    context = {"operating_mode": "auto", OFFLINE_KEY: True}
    res = fpm.maybe_handle_image_generation(_Agent(), "make a picture of a red fox", session_id="s", source_context=context)
    assert not rendered and cloud["n"] == 0
    assert "Offline" in res["response"] and "fal.ai" not in res["response"]
