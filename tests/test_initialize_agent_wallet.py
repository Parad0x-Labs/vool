"""installer.initialize_agent_wallet is retired: it creates nothing and refuses typed.

The old tests (an AES-GCM envelope on disk, idempotent re-install, the pubkey on stdout) had the
removed key authority as their entire subject. The installer step now files a
``wallet_legacy_surface_retired`` fault, exits 1, prints nothing to stdout, and does not touch the
filesystem — the operator creates a wallet in the app through core.wallet.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from core.vool_wallet import WALLET_FILENAME
from core.wallet.errors import WalletFault
from installer.initialize_agent_wallet import initialize_agent_wallet, main

LEGACY = "wallet_legacy_surface_retired"
_B58_RUN = re.compile(r"[1-9A-HJ-NP-Za-km-z]{32,}")


def _untouched(home: Path) -> None:
    assert not home.exists(), "the retired installer must not create the runtime home"
    assert not list(home.parent.rglob(WALLET_FILENAME))


def test_initialize_refuses_typed_and_receipt_backed(tmp_path: Path) -> None:
    home = tmp_path / "runtime"
    with pytest.raises(WalletFault) as exc:
        initialize_agent_wallet(str(home))
    fault = exc.value
    assert fault.code == LEGACY
    assert fault.fault_id.startswith("fault-")
    assert fault.context["surface"] == "installer.initialize_agent_wallet"
    from core.faults.recorder import fault_by_id

    assert fault_by_id(fault.fault_id) is not None
    _untouched(home)


def test_initialize_refuses_every_time_not_just_the_first(tmp_path: Path) -> None:
    home = tmp_path / "runtime"
    ids = set()
    for _ in range(3):
        with pytest.raises(WalletFault) as exc:
            initialize_agent_wallet(str(home))
        ids.add(exc.value.fault_id)
    assert len(ids) == 3, "each refusal is its own receipt"
    _untouched(home)


def test_initialize_ignores_a_stale_legacy_file(tmp_path: Path) -> None:
    home = tmp_path / "runtime"
    stale = home / "data" / "keys" / WALLET_FILENAME
    stale.parent.mkdir(parents=True)
    stale.write_text("{}", encoding="utf-8")
    with pytest.raises(WalletFault):
        initialize_agent_wallet(str(home))
    assert stale.read_text(encoding="utf-8") == "{}"
    assert sorted(p.name for p in stale.parent.iterdir()) == [WALLET_FILENAME]


def test_main_exits_1_with_an_empty_stdout_and_no_key_material(tmp_path, capsys) -> None:
    home = tmp_path / "runtime"
    assert main([str(home)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""  # stdout used to carry the pubkey for the installer receipt; now nothing
    assert "core.wallet" in captured.err and LEGACY in captured.err
    assert _B58_RUN.search(captured.err) is None
    for token in ("private", "seed", "traceback"):
        assert token not in captured.err.lower()
    _untouched(home)


def test_main_without_arguments_also_refuses_without_touching_the_default_home(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "default-home"))
    assert main([]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and LEGACY in captured.err
    assert not list(tmp_path.rglob(WALLET_FILENAME))


def test_main_never_reaches_the_legacy_creator(tmp_path, monkeypatch, capsys) -> None:
    def _boom(**_kwargs):
        raise AssertionError("the installer must not call the legacy create-on-read door")

    monkeypatch.setattr("core.vool_wallet.get_or_create_wallet", _boom)
    assert main([str(tmp_path / "runtime")]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "AssertionError" not in captured.err and LEGACY in captured.err
