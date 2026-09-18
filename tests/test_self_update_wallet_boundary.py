"""Adversarial wallet-boundary guarantees for the self-updater.

The money-critical files — the Solana wallet, its signing key (from which the wallet's
encryption key is derived), the tx-history DB, x402 receipts, and the spend policy/ledger —
must survive ANY update, including a malicious/compromised release that ships its own copies
trying to overwrite them. These lock in that guarantee at the swap layer (no network, no keys).
"""
from __future__ import annotations

from pathlib import Path

from installer.self_update import backup_and_swap, compute_preserve_names, staged_top_level


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _money_files(runtime_root: Path, marker: str) -> dict[Path, str]:
    """The wallet/keys/tx-history/receipts/policy under a runtime data dir."""
    files = {
        runtime_root / "data" / "keys" / "solana_wallet.enc": f"{marker}-WALLET-SEED",
        runtime_root / "data" / "keys" / "node_signing_key.b64": f"{marker}-SIGNING-KEY",
        runtime_root / "data" / "vool_web0_v2.db": f"{marker}-TX-HISTORY",
        runtime_root / "data" / "receipts" / "x402-0001.json": f"{marker}-X402-RECEIPT",
        runtime_root / "data" / "spend_policy.json": f"{marker}-SPEND-POLICY",
        runtime_root / "data" / "spend_ledger.json": f"{marker}-SPEND-LEDGER",
    }
    for p, text in files.items():
        _write(p, text)
    return files


def test_malicious_release_cannot_overwrite_wallet_keys_db_or_receipts(tmp_path) -> None:
    # THE guarantee: a compromised update that SHIPS its own data/ (wallet, receipts, ...) can never
    # overwrite the user's real money files — because `data` is in the preserve set, so its staged
    # copy is excluded from the swap entirely.
    project = tmp_path / "vool-local"
    _write(project / "core" / "app.py", "OLD CODE")
    _write(project / "apps" / "vool_api_server.py", "OLD SERVER")
    real = _money_files(project, "REAL")  # vool_home=None -> data lives at project/data

    release = tmp_path / "release"
    _write(release / "core" / "app.py", "NEW CODE")
    _write(release / "apps" / "vool_api_server.py", "NEW SERVER")
    _write(release / "data" / "keys" / "solana_wallet.enc", "ATTACKER-WALLET-SEED")
    _write(release / "data" / "receipts" / "x402-0001.json", "ATTACKER-X402-RECEIPT")
    _write(release / "data" / "spend_policy.json", "ATTACKER-RAISED-CAPS")

    backup_and_swap(release, project, compute_preserve_names(project, None), tmp_path / "backup")

    for p, expected in real.items():
        assert p.read_text(encoding="utf-8") == expected, f"money file was modified: {p}"
    # ...and the actual code WAS updated.
    assert (project / "core" / "app.py").read_text(encoding="utf-8") == "NEW CODE"
    assert (project / "apps" / "vool_api_server.py").read_text(encoding="utf-8") == "NEW SERVER"


def test_staged_top_level_never_includes_user_data_dirs(tmp_path) -> None:
    release = tmp_path / "release"
    for name in ("core", "apps", "data", ".vool_runtime", ".venv"):
        (release / name).mkdir(parents=True)
    staged = {p.name for p in staged_top_level(release, compute_preserve_names(tmp_path / "proj", None))}
    assert "data" not in staged            # wallet/keys/db/receipts live here
    assert ".vool_runtime" not in staged  # ...or here
    assert ".venv" not in staged
    assert {"core", "apps"} <= staged      # code still gets swapped


def test_nested_runtime_home_survives_a_malicious_release(tmp_path) -> None:
    # Runtime home nested under the code dir: preserved by its top-level name.
    project = tmp_path / "vool-local"
    home = project / ".vool_runtime"
    _write(project / "core" / "x.py", "OLD")
    real = _money_files(home, "REAL")

    release = tmp_path / "release"
    _write(release / "core" / "x.py", "NEW")
    _write(release / ".vool_runtime" / "data" / "keys" / "solana_wallet.enc", "ATTACKER")

    preserve = compute_preserve_names(project, home)
    assert ".vool_runtime" in preserve
    backup_and_swap(release, project, preserve, tmp_path / "backup")

    for p, expected in real.items():
        assert p.read_text(encoding="utf-8") == expected, f"money file was modified: {p}"
    assert (project / "core" / "x.py").read_text(encoding="utf-8") == "NEW"


def test_separate_runtime_home_is_outside_swap_scope(tmp_path) -> None:
    # The common Windows layout: runtime home OUTSIDE the code dir. The swap only touches
    # project_root, so it cannot reach the wallet at all.
    project = tmp_path / "vool-local"
    home = tmp_path / ".vool_runtime"
    _write(project / "core" / "x.py", "OLD")
    real = _money_files(home, "REAL")

    release = tmp_path / "release"
    _write(release / "core" / "x.py", "NEW")

    backup_and_swap(release, project, compute_preserve_names(project, home), tmp_path / "backup")

    for p, expected in real.items():
        assert p.read_text(encoding="utf-8") == expected, f"money file was modified: {p}"
    assert (project / "core" / "x.py").read_text(encoding="utf-8") == "NEW"
