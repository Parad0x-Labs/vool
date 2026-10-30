"""Phone access switched off and on keeps its port, so a paired phone still reaches the computer.

Measured 2026-10-27 on an iMac: each off/on picked a new random port (55822, then 56030), and the paired
phone kept dialing the old one ("can't reach my computer") until it was given the new address by hand.
"""
from __future__ import annotations

import socket
from types import SimpleNamespace

from core.mobile_companion import bot_runtime
from core.mobile_companion import listener as listener_module


def _listener(tmp_path, monkeypatch):
    monkeypatch.setattr(listener_module, "_settings_path", lambda: tmp_path / "listener.json")
    monkeypatch.setattr(bot_runtime, "manager_for", lambda runtime: SimpleNamespace(restore=lambda: None))
    return listener_module.CompanionListener(SimpleNamespace())


def test_off_and_on_keeps_the_port(tmp_path, monkeypatch):
    listener = _listener(tmp_path, monkeypatch)
    try:
        first = listener.configure(enabled=True, address="127.0.0.1")["port"]
        listener.configure(enabled=False)
        assert listener_module.load_settings()["enabled"] is False
        assert listener.configure(enabled=True, address="127.0.0.1")["port"] == first
    finally:
        listener.close()


def test_a_taken_port_falls_back_to_a_new_one(tmp_path, monkeypatch):
    listener = _listener(tmp_path, monkeypatch)
    blocker = socket.socket()
    try:
        first = listener.configure(enabled=True, address="127.0.0.1")["port"]
        listener.configure(enabled=False)
        blocker.bind(("127.0.0.1", first))
        blocker.listen()
        second = listener.configure(enabled=True, address="127.0.0.1")["port"]
        assert second not in (0, first)
    finally:
        blocker.close()
        listener.close()
