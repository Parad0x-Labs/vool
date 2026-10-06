"""The restart-proof child env drops secrets and keeps Keychain isolation. Contributor: sls_0x."""
from __future__ import annotations

import pytest

from tests._restart_child_env import ISOLATION_PINS, scrub_child_env

PIN_VALUES = {
    "VOOL_KEY_STORAGE_MODE": "file",
    "VOOL_KEY_STORAGE_MODE": "file",
    "VOOL_CREDENTIAL_STORE": "vault",
    "VOOL_CREDENTIAL_STORE": "vault",
    "VOOL_KEYCHAIN_ALLOWED": "0",
    "VOOL_KEYCHAIN_ALLOWED": "0",
    "PYTHON_KEYRING_BACKEND": "keyring.backends.fail.Keyring",
}
FAKE_SECRETS = {
    "OPENROUTER_API_KEY": "fake-sk-0000",
    "example_apikey": "fake-0001",
    "GITHUB_TOKEN": "fake-ghp-0002",
    "DB_PASSWORD": "fake-0003",
    "CLIENT_SECRET": "fake-0004",
    "AWS_CREDENTIALS": "fake-0005",
    "VOOL_CREDENTIAL_STORE_TOKEN": "fake-0006",
    "vool_credential_store": "fake-0007",
}


def test_pin_list_covers_the_seven_isolation_variables():
    assert ISOLATION_PINS == set(PIN_VALUES)


def test_fake_secrets_are_dropped_and_ordinary_variables_kept():
    env = scrub_child_env({**FAKE_SECRETS, "PATH": "/usr/bin", "LANG": "C"})
    assert env == {"PATH": "/usr/bin", "LANG": "C"}


@pytest.mark.parametrize("name", sorted(PIN_VALUES))
def test_each_isolation_pin_is_kept_with_its_value(name):
    env = scrub_child_env({**FAKE_SECRETS, **PIN_VALUES})
    assert env.get(name) == PIN_VALUES[name]
    assert not set(FAKE_SECRETS) & set(env)
