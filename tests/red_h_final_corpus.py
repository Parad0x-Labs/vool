#!/usr/bin/env python3
"""RED-FINAL driver — run the frozen RED-C corpus under this lane's probe prefix.

WHY THIS FILE EXISTS AT ALL
---------------------------
`tests/red_c_adversarial_corpus.py` and `tests/red_b_served_gauntlet.py` hard-code
`AUDIT_PREFIX = "audit-AUD-20260829-003-RED1-"`, and `ApiChat.send` ASSERTS on it.  The
final-round rules of engagement mandate `audit-final-<lane>-` instead.  Editing either
frozen file would change its SHA-256 and destroy the byte-identity that the whole
baseline/after comparison rests on (RED1_BASELINE.md records the digests).

So this driver imports both modules and rebinds the module-level constant AT RUNTIME.
No byte on disk changes; `shasum -a 256` on both instruments is unchanged after a run.
The corpus, the mint rules, the classification and the served accounting are executed
EXACTLY as frozen -- this file adds no logic of its own and deliberately contains no
scoring, so it cannot flatter the runtime.

USAGE
    PYTHONPATH="$PWD" PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -u \
        tests/red_h_final_corpus.py --tree "$PWD" --mint auto --serve \
        --base-url http://127.0.0.1:11437 --out <scratch>/red_c_final.json
"""
from __future__ import annotations

import hashlib
import os
import sys

_TESTS = os.path.dirname(os.path.abspath(__file__))
if _TESTS not in sys.path:
    sys.path.insert(0, _TESTS)

FINAL_PREFIX = "audit-final-corpus-"


def _sha(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def main() -> int:
    import red_b_served_gauntlet as red_b
    import red_c_adversarial_corpus as red_c

    before = {
        "red_b": _sha(red_b.__file__),
        "red_c": _sha(red_c.__file__),
    }
    # Rebind in memory only.  Both modules read the global at call time.
    red_b.AUDIT_PREFIX = FINAL_PREFIX
    red_c.AUDIT_PREFIX = FINAL_PREFIX

    print("RED-FINAL driver: probe prefix rebound in memory to", FINAL_PREFIX)
    print("  red_b_served_gauntlet.py sha256 :", before["red_b"])
    print("  red_c_adversarial_corpus.py sha256:", before["red_c"])
    print("  (verify unchanged after the run with shasum -a 256)")
    print()

    rc = red_c.main()

    after = {"red_b": _sha(red_b.__file__), "red_c": _sha(red_c.__file__)}
    if after != before:
        print("INSTRUMENT MUTATED ON DISK -- RESULTS VOID", file=sys.stderr)
        return 3
    print("\ninstrument bytes unchanged on disk after the run: OK")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
