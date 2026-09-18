"""R2b2a1 — relay daemon loops own their outbound authority.

R2b1's amendment made unscoped outbound effects fail closed at the door. That
was correct and it broke three production relay daemons: their `run_forever`
loops invoke the door from a worker thread with no turn and no background
scope, so every outbound send is refused. The fix is one
`named_background_effect_scope` at each loop's OWNING entry — never inside the
low-level send helpers, which stay dumb.

WHAT THESE TESTS PIN, per worker (discord, telegram, telegram_chat):
* the daemon's outbound call REACHES the transport (mocked; no real network)
  under the worker's own named scope — at socket time, not by declaration;
* every receipt the loop records names that scope and no turn;
* the scope survives a swallowed transient exception (the chat bridge's
  `except Exception` must not shed the loop's authority);
* the scope is gone after the loop exits — normal (config-miss return) and
  exceptional (sentinel unwind) — in the thread that ran it;
* one worker's scope never leaks into another worker or into the main thread.

THE LOOP BOUNDS: `run_forever` is `while True`, so the harness bounds each run
two ways — a BaseException sentinel from the mocked transport (BaseException
because the chat bridge's `except Exception` must not swallow the bound), and
a sleep-count sentinel for the RED baseline, where the door refuses BEFORE the
mock transport is ever reached and a daemon that catches the refusal would
spin forever. Exit-via-sentinel is the exceptional-exit case; the only NORMAL
exit `run_forever` has is its unconfigured early return, which is tested too.
"""
from __future__ import annotations

import threading

from core.effect_gateway import current_effect_ledger, effect_receipts


class _FakeResponse:
    def __init__(self, payload: bytes = b"{}", status: int = 200):
        self._payload = payload
        self.status = status
        self.length = len(payload)

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc_info) -> bool:
        return False


class _Bounded(BaseException):
    """Loop-exit sentinel. BaseException deliberately: the chat bridge catches
    `Exception` for transient hiccups, and the bound must not be swallowed."""


def _bound_sleep(monkeypatch, max_sleeps: int = 2) -> None:
    """Second loop bound, independent of the transport: at the RED baseline the
    door refuses BEFORE the mock transport is reached, so a daemon that catches
    the refusal and sleeps would otherwise spin forever."""
    import time as time_module

    original_sleep = time_module.sleep  # captured BEFORE patching: no recursion
    state = {"sleeps": 0}

    def _bounded_sleep(seconds):
        state["sleeps"] += 1
        if state["sleeps"] > max_sleeps:
            raise _Bounded()
        original_sleep(0)

    monkeypatch.setattr(time_module, "sleep", _bounded_sleep)


def _install_mock_transport(monkeypatch, captured: dict, *, bound_after: int = 2) -> None:
    """Capture, AT SOCKET TIME, which ledger owned each call; bound the loop by
    raising _Bounded from the Nth call (after capturing it). Counts are kept
    PER THREAD so two bounded workers cannot unbound each other."""
    import urllib.request

    counts: dict[str, int] = {}

    def _fake_urlopen(request, timeout=0, context=None):
        ledger = current_effect_ledger()
        captured.setdefault("scope_names", []).append(getattr(ledger, "scope_name", None))
        captured.setdefault("urls", []).append(str(getattr(request, "full_url", "")))
        receipts = [dict(r) for r in effect_receipts()]
        captured["last_receipt"] = receipts[-1] if receipts else None
        worker = threading.current_thread().name
        counts[worker] = counts.get(worker, 0) + 1
        if counts[worker] >= bound_after:
            raise _Bounded()
        return _FakeResponse(b"{}")

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)


def _run_in_worker(target, name: str = "relay-worker-test") -> dict:
    """Run `target` in a real daemon thread — the scope must live in the
    EXECUTING thread — and record that thread's ledger state after run_forever
    returns, whether the return was normal or by exception."""
    result: dict = {}

    def _worker() -> None:
        try:
            target()
            result["exited"] = "normal"
        except _Bounded:
            result["exited"] = "bounded"
        except BaseException as exc:  # reported, never swallowed
            result["exited"] = f"error:{type(exc).__name__}"
            result["error"] = exc
        result["ledger_after_exit"] = current_effect_ledger()

    thread = threading.Thread(target=_worker, name=name, daemon=True)
    thread.start()
    thread.join(timeout=30)
    assert not thread.is_alive(), f"the bounded loop did not exit: {name}"
    return result


# ------------------------------------------------------------------ discord


def test_relay_discord_worker_reaches_transport_under_its_own_scope(monkeypatch):
    captured: dict = {}
    _install_mock_transport(monkeypatch, captured)
    _bound_sleep(monkeypatch)

    from relay.bridge_workers.discord_bridge import DiscordBridge

    bridge = DiscordBridge()
    bridge.webhook_url = "https://discord.invalid/webhook"
    bridge.bot_token = "test-token"
    bridge.default_channel_id = "123"
    monkeypatch.setattr(DiscordBridge, "is_configured", lambda self: True)
    monkeypatch.setattr(DiscordBridge, "_ensure_agent", lambda self: None)

    result = _run_in_worker(bridge.run_forever)

    assert result["exited"] == "bounded", result
    assert captured["urls"], "the discord worker never reached the transport"
    assert set(captured["scope_names"]) == {"relay.discord"}, captured["scope_names"]
    assert captured["last_receipt"]["scope_name"] == "relay.discord", captured["last_receipt"]
    assert captured["last_receipt"]["turn_id"] == "", captured["last_receipt"]
    assert result["ledger_after_exit"] is None, result


def test_relay_discord_normal_exit_leaves_no_scope(monkeypatch):
    captured: dict = {}
    _install_mock_transport(monkeypatch, captured)
    _bound_sleep(monkeypatch)

    from relay.bridge_workers.discord_bridge import DiscordBridge

    bridge = DiscordBridge()
    monkeypatch.setattr(DiscordBridge, "is_configured", lambda self: False)

    result = _run_in_worker(bridge.run_forever)

    assert result["exited"] == "normal", result
    assert captured.get("urls", []) == [], captured  # nothing was sent
    assert result["ledger_after_exit"] is None, result


# ------------------------------------------------------------------ telegram


def test_relay_telegram_worker_reaches_transport_under_its_own_scope(monkeypatch):
    captured: dict = {}
    _install_mock_transport(monkeypatch, captured)
    _bound_sleep(monkeypatch)

    from relay.bridge_workers.telegram_bridge import TelegramBridge

    bridge = TelegramBridge()
    bridge.bot_token = "test-token"
    bridge.chat_id = "42"
    monkeypatch.setattr(TelegramBridge, "is_configured", lambda self: True)

    result = _run_in_worker(bridge.run_forever)

    assert result["exited"] == "bounded", result
    assert captured["urls"], "the telegram worker never reached the transport"
    assert set(captured["scope_names"]) == {"relay.telegram"}, captured["scope_names"]
    assert captured["last_receipt"]["scope_name"] == "relay.telegram", captured["last_receipt"]
    assert captured["last_receipt"]["turn_id"] == "", captured["last_receipt"]
    assert result["ledger_after_exit"] is None, result


def test_relay_telegram_normal_exit_leaves_no_scope(monkeypatch):
    captured: dict = {}
    _install_mock_transport(monkeypatch, captured)
    _bound_sleep(monkeypatch)

    from relay.bridge_workers.telegram_bridge import TelegramBridge

    bridge = TelegramBridge()
    monkeypatch.setattr(TelegramBridge, "is_configured", lambda self: False)

    result = _run_in_worker(bridge.run_forever)

    assert result["exited"] == "normal", result
    assert captured.get("urls", []) == [], captured
    assert result["ledger_after_exit"] is None, result


# ------------------------------------------------------------------ telegram chat


def test_relay_telegram_chat_worker_reaches_transport_under_its_own_scope(monkeypatch):
    captured: dict = {}
    _install_mock_transport(monkeypatch, captured)
    _bound_sleep(monkeypatch)

    from relay.bridge_workers.telegram_chat_bridge import TelegramChatBridge

    bridge = TelegramChatBridge()
    monkeypatch.setattr(TelegramChatBridge, "is_configured", lambda self: True)

    result = _run_in_worker(bridge.run_forever)

    assert result["exited"] == "bounded", result
    assert captured["urls"], "the chat worker never reached the transport"
    assert set(captured["scope_names"]) == {"relay.telegram_chat"}, captured["scope_names"]
    assert captured["last_receipt"]["scope_name"] == "relay.telegram_chat", captured["last_receipt"]
    assert result["ledger_after_exit"] is None, result


def test_relay_telegram_chat_scope_survives_a_swallowed_transient_error(monkeypatch):
    """The chat loop catches `Exception` to survive hiccups. The catch must not
    shed the loop's scope: the NEXT poll still runs under relay.telegram_chat."""
    import urllib.request

    captured: dict = {}
    counts: dict[str, int] = {}

    def _flaky_urlopen(request, timeout=0, context=None):
        ledger = current_effect_ledger()
        captured.setdefault("scope_names", []).append(getattr(ledger, "scope_name", None))
        counts["w"] = counts.get("w", 0) + 1
        if counts["w"] == 1:
            raise RuntimeError("transient network blip")  # Exception: swallowed by the loop
        if counts["w"] >= 3:
            raise _Bounded()
        return _FakeResponse(b"{}")

    monkeypatch.setattr(urllib.request, "urlopen", _flaky_urlopen)
    _bound_sleep(monkeypatch)

    from relay.bridge_workers.telegram_chat_bridge import TelegramChatBridge

    bridge = TelegramChatBridge()
    monkeypatch.setattr(TelegramChatBridge, "is_configured", lambda self: True)

    result = _run_in_worker(bridge.run_forever)

    assert result["exited"] == "bounded", result
    assert len(captured["scope_names"]) >= 2, captured
    assert set(captured["scope_names"]) == {"relay.telegram_chat"}, captured["scope_names"]
    assert result["ledger_after_exit"] is None, result


def test_relay_telegram_chat_normal_exit_leaves_no_scope(monkeypatch):
    captured: dict = {}
    _install_mock_transport(monkeypatch, captured)
    _bound_sleep(monkeypatch)

    from relay.bridge_workers.telegram_chat_bridge import TelegramChatBridge

    bridge = TelegramChatBridge()
    monkeypatch.setattr(TelegramChatBridge, "is_configured", lambda self: False)

    result = _run_in_worker(bridge.run_forever)

    assert result["exited"] == "normal", result
    assert captured.get("urls", []) == [], captured
    assert result["ledger_after_exit"] is None, result


# ------------------------------------------------------------------ isolation


def test_two_relay_workers_never_share_or_leak_a_scope(monkeypatch):
    """Two daemons running at once each bind their OWN scope; neither sees the
    other's, and the main thread never sees a relay scope at all."""
    import urllib.request

    captured: dict = {}
    counts: dict[str, int] = {}

    def _fake_urlopen(request, timeout=0, context=None):
        ledger = current_effect_ledger()
        captured.setdefault("scope_names", []).append(getattr(ledger, "scope_name", None))
        worker = threading.current_thread().name
        counts[worker] = counts.get(worker, 0) + 1
        if counts[worker] >= 2:
            raise _Bounded()
        return _FakeResponse(b"{}")

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    _bound_sleep(monkeypatch)

    from relay.bridge_workers.discord_bridge import DiscordBridge
    from relay.bridge_workers.telegram_bridge import TelegramBridge

    discord = DiscordBridge()
    discord.webhook_url = "https://discord.invalid/webhook"
    monkeypatch.setattr(DiscordBridge, "is_configured", lambda self: True)
    monkeypatch.setattr(DiscordBridge, "_ensure_agent", lambda self: None)
    telegram = TelegramBridge()
    telegram.bot_token = "test-token"
    telegram.chat_id = "42"
    monkeypatch.setattr(TelegramBridge, "is_configured", lambda self: True)

    assert current_effect_ledger() is None  # main thread: never scoped
    discord_result = _run_in_worker(discord.run_forever, name="discord-worker-test")
    telegram_result = _run_in_worker(telegram.run_forever, name="telegram-worker-test")
    assert current_effect_ledger() is None  # and never scoped afterwards

    assert discord_result["exited"] == "bounded", discord_result
    assert telegram_result["exited"] == "bounded", telegram_result
    assert captured["scope_names"], "no worker ever reached the transport"
    assert set(captured["scope_names"]) == {"relay.discord", "relay.telegram"}, captured
    assert discord_result["ledger_after_exit"] is None
    assert telegram_result["ledger_after_exit"] is None
