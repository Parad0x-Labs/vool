"""TEST LAUNCHER for the served money-law drive. Not a runtime entry point, never shipped.

Before running the real daemon entry point (``apps.vool_api_server``, unchanged), it mints —
through the REAL operator authority, in this process — one labelled SYNTHETIC UsePod spend grant
(``synthetic test funds``) and writes the operator token to ``USEPOD_MONEY_TEST_OPERATOR_TOKEN``
so the driving tests can mint/expire/revoke further grants in their own processes against the
same store. No real money, no wallet, no live provider: the funds are synthetic and every proof
against them is labelled as such.
"""
from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GRANT_NOTE = "synthetic test funds: usepod money-law served drive"
MODEL = "meridian-synth-chat"


def main() -> None:
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from core.effect_budget import grant_operator_budget_authority

    operator = grant_operator_budget_authority(note=GRANT_NOTE)
    token_path = Path(os.environ["USEPOD_MONEY_TEST_OPERATOR_TOKEN"])
    token_path.write_text(operator.token_id, encoding="utf-8")
    # The spend grant itself is minted by the driving tests AFTER the credential is saved: a
    # prepaid grant binds the provider account (the credential fingerprint), which does not
    # exist yet at daemon boot.
    sys.argv = ["apps.vool_api_server", *sys.argv[1:]]
    runpy.run_module("apps.vool_api_server", run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
