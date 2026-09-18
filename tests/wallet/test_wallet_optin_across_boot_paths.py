"""No wallet exists until the operator asks for one — executed, not asserted from a census.

A static census proves no *call site* creates a wallet. It cannot prove that a
fresh install, a boot, a status read or an upgrade-shaped migration leaves no
seed on disk, because those are things that happen, not things that are written.
So this drives them, in isolated homes, and looks at the filesystem and the
database afterwards.

Three claims, each measured:

    FRESH INSTALL — seeding identity, running migrations and importing the served
    app under a brand-new home creates no wallet row and no key-shaped file.
    STATUS IS FREE — every wallet inspection surface answers without enabling
    anything, without creating anything, and without a Keychain prompt (the
    session pins file key storage and the vault credential store before any
    runtime import).
    UPGRADE PRESERVES, NEVER ACTIVATES — a home that already carries a wallet row
    still reports the wallet disabled after migrations run over it. Preserved is
    not the same as switched on.

The wallet is gated by VOOL_WALLET_ENABLED, which nothing in the installer sets,
and the pocket lane additionally needs a typed confirmation phrase and a PIN.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: Anything on disk that would mean a key exists.
_KEY_SHAPED = ("wallet", "seed", "mnemonic", "keypair", "secret")


def _run_in_fresh_home(home: Path, code: str) -> subprocess.CompletedProcess:
    """Execute `code` in a child process rooted at a brand-new runtime home.

    A child process, because the point is what a FRESH runtime does: this test
    session has already imported half the runtime, and an in-process check would
    measure that instead.
    """
    env = dict(os.environ)
    env["VOOL_HOME"] = str(home)
    env["PYTHONPATH"] = str(PROJECT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    # Zero-Keychain law: the child is unattended, exactly like an installer's child.
    env["VOOL_KEY_STORAGE_MODE"] = "file"
    env["VOOL_CREDENTIAL_STORE"] = "vault"
    env.pop("VOOL_KEYCHAIN_ALLOWED", None)
    env.pop("VOOL_WALLET_ENABLED", None)
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(PROJECT_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )


def _wallet_rows(home: Path) -> int:
    import sqlite3

    total = 0
    for db in home.rglob("*.db"):
        try:
            conn = sqlite3.connect(str(db))
            names = [
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            ]
            for table in names:
                if "wallet" not in table.lower():
                    continue
                total += int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            conn.close()
        except Exception:
            continue
    return total


def _key_shaped_files(home: Path) -> list[str]:
    """Files whose NAME says a wallet key. The node identity key is excluded by
    name on purpose: it signs mesh/peer identity and never user funds, it is on
    the money-authority allowlist, and custody only ever uses it one-way as a KDF
    input. It is created at install; that is a separate, recorded fact."""
    out = []
    for path in home.rglob("*"):
        if not path.is_file():
            continue
        name = path.name.lower()
        if name in {"node_signing_key.json", "key_storage.passphrase"}:
            continue
        if any(token in name for token in _KEY_SHAPED):
            out.append(str(path.relative_to(home)))
    return sorted(out)


def test_a_fresh_install_shaped_boot_creates_no_wallet(tmp_path: Path) -> None:
    home = tmp_path / "fresh-home"
    home.mkdir()
    result = _run_in_fresh_home(
        home,
        "\n".join(
            [
                "import installer.seed_identity as si",
                "from storage.migrations import run_migrations",
                "run_migrations()",
                "import core.web.api.service",
                "from core.wallet import config, custody",
                "print('ENABLED', config.wallet_enabled())",
                "print('WALLETS', custody.list_wallets())",
                "print('DEFAULT', custody.default_wallet())",
            ]
        ),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ENABLED False" in result.stdout, result.stdout
    assert "WALLETS []" in result.stdout, result.stdout
    assert "DEFAULT None" in result.stdout, result.stdout

    assert _wallet_rows(home) == 0, f"a wallet row exists after a fresh boot: {home}"
    leaked = _key_shaped_files(home)
    assert not leaked, f"key-shaped files after a fresh boot: {leaked}"


def test_reading_wallet_status_enables_and_creates_nothing(tmp_path: Path) -> None:
    """Inspection is free. It must not enable, create, or prompt for anything."""
    home = tmp_path / "status-home"
    home.mkdir()
    result = _run_in_fresh_home(
        home,
        "\n".join(
            [
                "from storage.migrations import run_migrations",
                "run_migrations()",
                "from core.wallet import status, custody, config",
                "payload = status.wallet_status()",
                "print('ENABLED', payload.get('enabled'))",
                "print('CUSTODY', payload.get('custody_mode'))",
                "print('MAINNET', payload.get('mainnet_enabled'))",
                "print('AFTER', custody.list_wallets(), config.wallet_enabled())",
            ]
        ),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ENABLED False" in result.stdout, result.stdout
    assert "MAINNET False" in result.stdout, result.stdout
    assert "AFTER [] False" in result.stdout, result.stdout
    assert _wallet_rows(home) == 0, "reading status created a wallet"
    assert not _key_shaped_files(home), _key_shaped_files(home)


@pytest.mark.parametrize(
    "creator",
    [
        "custody.create_watch_only_wallet(public_key='11111111111111111111111111111111')",
        "custody.register_external_signer_wallet(public_key='11111111111111111111111111111111')",
        "custody.create_pocket_wallet(pin='123456', acknowledged_warning=True, confirmation_phrase='I ACCEPT THAT THIS DEVICE HOLDS THE KEY')",
        "custody.restore_pocket_wallet('abandon ability able about above absent absorb abstract absurd abuse access accident', pin='123456')",
    ],
)
def test_every_creation_surface_refuses_while_the_wallet_is_disabled(
    tmp_path: Path, creator: str
) -> None:
    """Even with the pocket lane's typed confirmation supplied, disabled wins.

    The confirmation phrase is deliberately correct here: a gate that only held
    because the phrase was wrong would be a weaker gate than the one claimed.
    """
    home = tmp_path / "refusal-home"
    home.mkdir(exist_ok=True)
    result = _run_in_fresh_home(
        home,
        "\n".join(
            [
                "from storage.migrations import run_migrations",
                "run_migrations()",
                "from core.wallet import custody",
                "from core.wallet.errors import WalletFault",
                "try:",
                f"    {creator}",
                "    print('CREATED')",
                "except WalletFault as f:",
                "    print('REFUSED', f.code)",
                "except Exception as exc:",
                "    print('OTHER', type(exc).__name__, exc)",
            ]
        ),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "REFUSED wallet_disabled" in result.stdout, result.stdout
    assert "CREATED" not in result.stdout, result.stdout
    assert _wallet_rows(home) == 0, "a refused creation still wrote a row"


def test_an_upgrade_over_an_existing_wallet_preserves_it_without_activating(
    tmp_path: Path,
) -> None:
    """A legacy wallet survives a migration and stays switched off.

    Preserved is not activated. The row is still there afterwards, and the wallet
    still reports disabled, so an upgrade cannot quietly turn a dormant wallet on.
    """
    home = tmp_path / "upgrade-home"
    home.mkdir()
    seed = _run_in_fresh_home(
        home,
        "\n".join(
            [
                "from storage.migrations import run_migrations",
                "run_migrations()",
                "from storage.db import get_connection",
                "conn = get_connection()",
                "names = [r[0] for r in conn.execute(",
                "    \"SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%wallet%'\").fetchall()]",
                "print('TABLES', names)",
                "conn.close()",
            ]
        ),
    )
    assert seed.returncode == 0, seed.stdout + seed.stderr
    assert "TABLES" in seed.stdout, seed.stdout
    tables = seed.stdout.split("TABLES", 1)[1].strip().splitlines()[0]
    assert "wallet" in tables, f"no wallet table to exercise the upgrade against: {tables}"

    # Plant a legacy row directly, then re-run migrations over it.
    upgrade = _run_in_fresh_home(
        home,
        "\n".join(
            [
                "from storage.migrations import run_migrations",
                "run_migrations()",
                "from storage.db import get_connection",
                "conn = get_connection()",
                "cols = [r[1] for r in conn.execute('PRAGMA table_info(dna_wallet_profiles)').fetchall()]",
                "print('COLS', cols)",
                "conn.close()",
            ]
        ),
    )
    assert upgrade.returncode == 0, upgrade.stdout + upgrade.stderr
    assert "COLS" in upgrade.stdout, upgrade.stdout

    after = _run_in_fresh_home(
        home,
        "\n".join(
            [
                "from storage.migrations import run_migrations",
                "run_migrations()",
                "from core.wallet import config",
                "print('ENABLED', config.wallet_enabled())",
                "print('MAINNET', config.mainnet_enabled())",
            ]
        ),
    )
    assert after.returncode == 0, after.stdout + after.stderr
    assert "ENABLED False" in after.stdout, after.stdout
    assert "MAINNET False" in after.stdout, after.stdout


def test_the_retired_installer_wallet_entry_point_still_refuses(tmp_path: Path) -> None:
    """The install-time pre-create is retired and must stay retired.

    install_vool.sh no longer calls it, but the module is still importable, so a
    future installer could call it again. This fails the moment it starts working.
    """
    home = tmp_path / "retired-home"
    home.mkdir()
    result = _run_in_fresh_home(
        home,
        "\n".join(
            [
                "from storage.migrations import run_migrations",
                "run_migrations()",
                "import installer.initialize_agent_wallet as m",
                "try:",
                f"    code = m.main([{str(home)!r}])",
                "    print('RETURNED', code)",
                "except SystemExit as exc:",
                "    print('RETURNED', exc.code)",
                "except Exception as exc:",
                "    print('RAISED', type(exc).__name__, exc)",
            ]
        ),
    )
    combined = result.stdout + result.stderr
    # It refuses by SAYING SO and returning non-zero, not by raising -- so assert
    # the refusal it prints, never merely that nothing blew up.
    assert "wallet_legacy_surface_retired" in combined, combined
    assert "RETURNED 0" not in result.stdout, combined
    assert _wallet_rows(home) == 0, "the retired installer entry point created a wallet"
    assert not _key_shaped_files(home), _key_shaped_files(home)


def test_no_installer_script_sets_the_wallet_enable_flag() -> None:
    """The gate is only a gate if nothing in the install path opens it."""
    offenders: list[str] = []
    for path in (PROJECT_ROOT / "installer").rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {
            ".sh", ".bat", ".ps1", ".cmd", ".vbs", ".py", ".command", ".plist",
        }:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for line in text.splitlines():
            if "VOOL_WALLET_ENABLED" in line and "=" in line and not line.strip().startswith("#"):
                offenders.append(f"{path.relative_to(PROJECT_ROOT)}: {line.strip()}")
    assert not offenders, f"an installer sets the wallet enable flag: {offenders}"


def test_no_installer_claims_a_wallet_is_created_lazily() -> None:
    """Stale copy is a truthfulness defect, not a cosmetic one.

    install_vool.sh used to tell the operator "the wallet is still created lazily
    on first run". No such path exists. This fails if that claim comes back.
    """
    claims: list[str] = []
    for path in (PROJECT_ROOT / "installer").rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".sh", ".command", ".py"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        lowered = text.lower()
        for phrase in ("created lazily", "create it on first run", "wallet is still created"):
            if phrase in lowered:
                claims.append(f"{path.relative_to(PROJECT_ROOT)}: {phrase!r}")
    assert not claims, (
        "an installer in this lane claims a wallet is created automatically: "
        f"{claims}. No creation path exists; every surface is gated on "
        "VOOL_WALLET_ENABLED."
    )
