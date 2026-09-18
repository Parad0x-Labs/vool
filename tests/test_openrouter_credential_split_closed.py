"""A10 pass-002 — the OpenRouter credential SPLIT stays CLOSED on every served consumer.

Pre-fix truth (independently reproduced at SHA f81dcdbb): with ONLY
``VOOL_OPENROUTER_API_KEY`` set and an isolated empty credential store,
``fast_command_surface._cloud_key_configured`` read only the PRIMARY alias
(``credential_env_for`` = ``env_names[0]``), while cloud status
(``_resolve_key``) and memory-first routing scanned the full alias tuple — and
both the escalation-broker registration gate (``cloud_runtime``) and the
adapter transport lane raised/fell back to nothing under the compat alias.
One fact, two verdicts.

Repair law: every readiness / availability / transport consumer derives from
the FULL priority-ordered tuple owned by ``core.cloud_providers.key_env_names``
— no second alias list anywhere.

Isolation law: every test here monkeypatches ``core.credential_store``
accessors to an in-memory fixture BEFORE touching anything else and asserts
zero real-store writes. The host Keychain never influences a verdict.
"""
from __future__ import annotations

import os
import unittest
from unittest import mock

VOOL_ALIAS = "VOOL_OPENROUTER_API_KEY"
VENDOR_ALIAS = "OPENROUTER_API_KEY"
OPENROUTER_ALIASES = (VENDOR_ALIAS, VOOL_ALIAS)
SLOT = "llm.cloud.openrouter"

_CONSUMER_KEYS = (
    "fast_command",
    "cloud_status_resolve_key",
    "memory_first_routing",
    "escalation_broker_registration",
    "adapter_transport_lane",
)


def _isolated_store():
    """Patch every credential_store accessor to an isolated in-memory fixture."""
    from core import credential_store

    mem: dict[str, str] = {}
    ctx = mock.patch.multiple(
        credential_store,
        get_credential=mock.DEFAULT,
        has_credential=mock.DEFAULT,
        store_credential=mock.DEFAULT,
        delete_credential=mock.DEFAULT,
    )
    started = ctx.__enter__()
    started["get_credential"].side_effect = lambda name: mem.get(str(name))
    started["has_credential"].side_effect = lambda name: str(name) in mem
    started["store_credential"].side_effect = AssertionError(
        "test must never write to the real credential store"
    )
    started["delete_credential"].side_effect = lambda name: mem.pop(str(name), None) is not None

    def stop() -> None:
        ctx.__exit__(None, None, None)

    return mem, stop


def _scrub_alias_env() -> None:
    for name in OPENROUTER_ALIASES:
        os.environ.pop(name, None)


def _read_consumers() -> dict[str, bool]:
    from adapters.openrouter_cloud_provider import OpenRouterCloudProvider
    from core import cloud_runtime
    from core.agent_runtime.fast_command_surface import _cloud_key_configured
    from core.cloud_credential_broker import CloudCredentialBroker
    from core.cloud_connection_state import _resolve_key
    from core.memory_first_router import _openrouter_key_present

    broker = cloud_runtime.build_default_cloud_broker()
    transports = getattr(broker, "_transports", None)
    if transports is None:
        raise RuntimeError("CloudModelBroker._transports not found — test contract drifted")

    # Exactly how PolicyBoundCloudTransport authenticates each served call;
    # resolve() returning None there raises cloud_credentials_missing (fail-closed).
    creds = CloudCredentialBroker()
    adapter_value = creds.resolve(
        OpenRouterCloudProvider.credential_name,
        env_name=OpenRouterCloudProvider()._resolved_credential_env(),
    )
    return {
        "fast_command": bool(_cloud_key_configured()),
        "cloud_status_resolve_key": bool(_resolve_key("openrouter")),
        "memory_first_routing": bool(_openrouter_key_present()),
        "escalation_broker_registration": "openrouter" in {str(k) for k in transports},
        "adapter_transport_lane": bool(adapter_value),
    }


class OpenRouterCredentialSplitClosed(unittest.TestCase):
    def setUp(self) -> None:
        _scrub_alias_env()
        self._mem, self._stop_store = _isolated_store()

    def tearDown(self) -> None:
        try:
            _scrub_alias_env()
            self._stop_store()
        finally:
            pass

    def _assert_converged(self, expected: bool) -> None:
        result = _read_consumers()
        for key in _CONSUMER_KEYS:
            self.assertEqual(
                result[key],
                expected,
                f"{key}={result[key]} expected {expected} — served consumers disagree",
            )

    def test_vool_alias_only_all_present(self) -> None:
        os.environ[VOOL_ALIAS] = "sk-or-test-presence-only"
        self._assert_converged(True)

    def test_vendor_alias_only_all_present(self) -> None:
        os.environ[VENDOR_ALIAS] = "sk-or-test-presence-only"
        self._assert_converged(True)

    def test_no_alias_empty_store_fail_closed(self) -> None:
        self._assert_converged(False)

    def test_vault_slot_only_arms_every_consumer(self) -> None:
        mem = self._mem
        mem[SLOT] = "sk-or-vault-fixture-presence-only"
        self._assert_converged(True)

    def test_vendor_alias_wins_precedence_when_both_set(self) -> None:
        # Vendor-first order is FROZEN: the transport lane must pick the vendor alias.
        os.environ[VENDOR_ALIAS] = "vendor-value"
        os.environ[VOOL_ALIAS] = "vool-value"
        from adapters.openrouter_cloud_provider import OpenRouterCloudProvider
        from core.cloud_providers import key_env_names

        self.assertEqual(key_env_names("openrouter"), OPENROUTER_ALIASES)
        provider = OpenRouterCloudProvider()
        self.assertEqual(provider._resolved_credential_env(), VENDOR_ALIAS)

    def test_alias_vocabulary_single_sourced(self) -> None:
        from adapters.openrouter_cloud_provider import OpenRouterCloudProvider
        from core.cloud_providers import config_for, credential_env_for, key_env_names

        self.assertEqual(key_env_names("openrouter"), config_for("openrouter").env_names)
        self.assertEqual(credential_env_for("openrouter"), key_env_names("openrouter")[0])
        self.assertEqual(OpenRouterCloudProvider.credential_env, VENDOR_ALIAS)
        self.assertEqual(OpenRouterCloudProvider.credential_name, SLOT)
        # Empty/unknown providers stay fail-closed.
        self.assertEqual(key_env_names("no-such-provider"), ())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
