"""C15 mutation / sabotage proof — the preflight guards are load-bearing (CLAUDE.md §6b.4).

For each named sabotage: apply an exact byte replacement, run the test that names the
cause, REQUIRE it to go red, restore the exact original bytes (sha256 verified), and
re-run the same test to REQUIRE it green again. A sabotage that does NOT fail its named
test means the invariant is unprotected: the script exits non-zero and names it.

Run:  python -m ops.keychain_preflight_mutations
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PY = sys.executable



@dataclass
class Mutation:
    name: str
    file: Path
    old: str
    new: str
    named_red_test: str


MUTATIONS: tuple[Mutation, ...] = (
    Mutation(
        name="M1-preflight-placement-after-runtime-imports",
        file=REPO / "tests" / "conftest.py",
        old=(
            "# C15 (2026-09-03): the ONE bounded unattended preflight runs BEFORE any runtime module\n"
            "# is imported — it pins this pytest session to non-interactive storage (vault + file\n"
            "# signer, no inherited Keychain grant) and selects the isolated bundled Chromium, so no\n"
            "# collection-time import or fixture can reach the macOS keyring, the `security` CLI or a\n"
            "# signed-in browser profile first. Placement here is load-bearing: moving this call below\n"
            "# the runtime imports is exactly the sabotage the preflight tests check for.\n"
            "from core.unattended_preflight import preflight as _unattended_preflight\n"
            "\n"
            "_unattended_preflight(\"pytest-conftest\")\n"
            "\n"
        ),
        new="",  # removed from the top: the placement guard must catch the gap
        named_red_test=(
            "tests/test_keychain_unattended_preflight.py::test_conftest_preflight_call_precedes_all_runtime_imports"
        ),
    ),
    Mutation(
        name="M2-scratch-isolation-pins-removed",
        file=REPO / "core" / "unattended_preflight.py",
        old="        _enforce_scratch_isolation(report)\n",
        new="        pass  # SABOTAGE: scratch isolation pins dropped\n",
        named_red_test=(
            "tests/test_keychain_unattended_preflight.py::test_scratch_driver_through_preflight_makes_zero_credential_calls"
        ),
    ),
    Mutation(
        name="M3-browser-profile-isolation-seam-removed",
        file=REPO / "tools" / "browser" / "browser_render.py",
        old="    if profile_root:\n",
        new="    if False:  # SABOTAGE: isolation seam dropped\n",
        named_red_test=(
            "tests/test_keychain_unattended_preflight.py::test_chrome_render_uses_a_fresh_isolated_profile_and_the_pinned_binary"
        ),
    ),
    Mutation(
        name="M4-synthetic-identity-predicate-inverted",
        file=REPO / "core" / "unattended_preflight.py",
        old=(
            "        if marker.exists():\n"
            "            return False\n"
            "    return True\n"
            "\n"
            "\n"
            "def preflight_mode("
        ),
        new=(
            "        if marker.exists():\n"
            "            return True  # SABOTAGE: an established home counts as synthetic\n"
            "    return True\n"
            "\n"
            "\n"
            "def preflight_mode("
        ),
        named_red_test=(
            "tests/test_keychain_unattended_preflight.py::test_established_unattended_boot_is_recorded_never_rewritten"
        ),
    ),
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run_test(test: str, *, timeout: float = 600) -> tuple[int, str]:
    proc = subprocess.run(
        [PY, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider", test],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc.returncode, (proc.stdout + proc.stderr)[-1200:]


def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="c15-mutations-"):
        backups: dict[Path, tuple[bytes, str]] = {}
        for mutation in MUTATIONS:
            path = mutation.file
            if path not in backups:
                backups[path] = (path.read_bytes(), _sha(path))

            original = path.read_text(encoding="utf-8")
            if mutation.old not in original:
                failures.append(f"{mutation.name}: anchor not found — mutation script is stale")
                continue
            count = original.count(mutation.old)
            if count != 1:
                failures.append(f"{mutation.name}: anchor found {count} times, expected 1")
                continue

            path.write_text(original.replace(mutation.old, mutation.new, 1), encoding="utf-8")
            try:
                rc, _tail = _run_test(mutation.named_red_test)
            finally:
                path.write_bytes(backups[path][0])
                assert _sha(path) == backups[path][1], f"{mutation.name}: RESTORE MISMATCH"

            if rc == 0:
                failures.append(
                    f"{mutation.name}: INVARIANT UNPROTECTED — {mutation.named_red_test} stayed green"
                )
                continue
            rc_green, tail_green = _run_test(mutation.named_red_test)
            if rc_green != 0:
                failures.append(
                    f"{mutation.name}: restore did not heal — {mutation.named_red_test} still red:\n{tail_green}"
                )
            print(f"OK   {mutation.name}: named red ({mutation.named_red_test.split('::')[-1]}) then green after exact restore")

    if failures:
        print("\nFAILURES:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("\nAll 4 sabotages failed their named tests and every restore is sha256-verified green.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
