"""Test harness: an in-memory device authority. Lives here, never in the runtime.

Injected in-process through `core.wallet.device_auth.set_device_authority_for_tests`; records prompts and stores,
denies on request, can report itself unavailable. No macOS, no Keychain, no prompt.
"""
from __future__ import annotations

from core.wallet.device_auth import DeviceAuthDenied, DeviceAuthUnavailable


class FakeDeviceAuthority:
    def __init__(self, *, available: bool = True) -> None:
        self._available = available
        self.secrets: dict[str, bytes] = {}
        self.prompts: list[str] = []
        self.stores: list[str] = []
        self.deny_next = False

    def available(self) -> bool:
        return self._available

    def describe(self) -> str:
        return "fake device authentication (tests)"

    def store_unlock_secret(self, wallet_id: str, secret: bytes, *, reason: str) -> None:
        if not self._available:
            raise DeviceAuthUnavailable("fake authority unavailable")
        self.secrets[str(wallet_id)] = bytes(secret)
        self.stores.append(reason)

    def read_unlock_secret(self, wallet_id: str, *, reason: str) -> bytes:
        if not self._available:
            raise DeviceAuthUnavailable("fake authority unavailable")
        self.prompts.append(reason)
        if self.deny_next:
            self.deny_next = False
            raise DeviceAuthDenied("denied")
        if str(wallet_id) not in self.secrets:
            raise DeviceAuthDenied("not_found")
        return self.secrets[str(wallet_id)]

    def forget(self, wallet_id: str) -> None:
        self.secrets.pop(str(wallet_id), None)
