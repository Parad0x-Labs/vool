"""Encrypted credential store: seal -> retrieve, encrypted at rest, names-not-values, tamper-safe."""
from __future__ import annotations

import json

import pytest

from core import credential_store as cs
from core import runtime_paths


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    # Each test gets its own VOOL_HOME so the credential file is isolated from the real store.
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)  # beat any leftover override
    yield
    runtime_paths.configure_runtime_home(None)


def test_store_and_get_roundtrip() -> None:
    assert cs.get_credential("email.smtp.gmail") is None
    cs.store_credential("email.smtp.gmail", "hunter2-app-password", label="gmail SMTP")
    assert cs.get_credential("email.smtp.gmail") == "hunter2-app-password"
    assert cs.has_credential("email.smtp.gmail") is True


def test_value_is_encrypted_on_disk() -> None:
    secret = "super-secret-x-api-key-do-not-leak"
    cs.store_credential("x.api.key", secret, label="X API")
    blob = runtime_paths.data_path("credentials.enc.json").read_text(encoding="utf-8")
    assert secret not in blob  # never plaintext at rest
    parsed = json.loads(blob)
    assert "x.api.key" in parsed and "ct_b64" in parsed["x.api.key"]


def test_list_and_has_never_leak_values() -> None:
    cs.store_credential("a", "value-a", label="Cred A")
    cs.store_credential("b", "value-b")
    listed = cs.list_credentials()
    assert {e["name"] for e in listed} == {"a", "b"}
    dumped = json.dumps(listed)
    assert "value-a" not in dumped and "value-b" not in dumped  # labels only, never values
    assert any(e["label"] == "Cred A" for e in listed)


def test_delete() -> None:
    cs.store_credential("temp", "x")
    assert cs.delete_credential("temp") is True
    assert cs.has_credential("temp") is False
    assert cs.delete_credential("temp") is False


def test_missing_returns_none() -> None:
    assert cs.get_credential("nope") is None
    assert cs.has_credential("nope") is False


def test_tampered_ciphertext_fails_closed() -> None:
    cs.store_credential("k", "real-value")
    path = runtime_paths.data_path("credentials.enc.json")
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["k"]["ct_b64"] = raw["k"]["ct_b64"][:-4] + "AAAA"  # corrupt the ciphertext
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert cs.get_credential("k") is None  # returns None, does not raise


def test_empty_name_rejected() -> None:
    with pytest.raises(ValueError):
        cs.store_credential("", "x")
