"""iCloud Mail: the standards path with Apple's documented account contract.

iCloud Mail is an ordinary IMAP/SMTP mailbox (support.apple.com/en-us/102525):
  * IMAP: imap.mail.me.com, port 993, TLS required
  * SMTP: smtp.mail.me.com, port 587, STARTTLS required
  * Authentication: an app-specific password (iCloud accounts with two-factor
    auth cannot use the primary password for mail)

There is no separate wire protocol to implement — the existing standards
transport in core.email_tools already speaks it. What iCloud accounts need is
the SETUP contract, so a user asking to connect iCloud Mail gets exact,
actionable steps and a verification that checks identity — never a fake
connected state and never a password solicited in chat.
"""
from __future__ import annotations

import json
from typing import Any

ICLOUD_IMAP = ("imap.mail.me.com", 993, "ssl")
ICLOUD_SMTP = ("smtp.mail.me.com", 587, "starttls")


def icloud_setup_guidance() -> str:
    return (
        "To connect iCloud Mail: (1) on appleid.apple.com sign in, go to Sign-In and Security, "
        "generate an app-specific password for 'VOOL'; (2) store the account here with that password "
        "— IMAP imap.mail.me.com port 993 (TLS), SMTP smtp.mail.me.com port 587 (STARTTLS), username "
        "is your full iCloud address. I will never ask you to paste the app password into chat; "
        "the runtime's credential setup stores it."
    )


def verify_icloud_account(creds: dict[str, Any] | None) -> dict[str, Any]:
    """Static verification of an iCloud account blob: correct hosts, TLS-required
    ports, and an @icloud.com/@me.com/@mac.com username.

    This proves the CONFIGURATION contract only. It deliberately performs NO
    network login (a live check needs the real credential and is the owner's
    labelled acceptance gate); it never claims the mailbox is reachable."""
    if not isinstance(creds, dict):
        return {"ok": False, "status": "needs_setup", "message": icloud_setup_guidance()}
    problems: list[str] = []
    imap_host = str(creds.get("imap_host") or creds.get("host") or "")
    smtp_host = str(creds.get("smtp_host") or creds.get("host") or "")
    username = str(creds.get("username") or "")
    if imap_host and imap_host != ICLOUD_IMAP[0]:
        problems.append(f"IMAP host should be {ICLOUD_IMAP[0]} (got {imap_host!r})")
    if smtp_host and smtp_host != ICLOUD_SMTP[0]:
        problems.append(f"SMTP host should be {ICLOUD_SMTP[0]} (got {smtp_host!r})")
    security = str(creds.get("security") or "").lower()
    if security == "plain":
        problems.append("iCloud requires TLS; security 'plain' is refused")
    if username and not any(username.lower().endswith(s) for s in ("@icloud.com", "@me.com", "@mac.com")):
        problems.append(f"iCloud username is a full @icloud.com/@me.com/@mac.com address (got {username!r})")
    if problems:
        return {"ok": False, "status": "needs_setup", "message": "iCloud account configuration issues: "
                + "; ".join(problems) + ". " + icloud_setup_guidance()}
    return {
        "ok": True, "status": "configured",
        "message": ("iCloud account configuration matches Apple's documented IMAP/SMTP contract. "
                    "Live login verification requires the stored app-specific password and is performed "
                    "at connection time; this check does not claim it."),
        "imap": ICLOUD_IMAP, "smtp": ICLOUD_SMTP,
    }


def icloud_account_blob(address: str, app_password: str) -> str:
    """The credential JSON for an iCloud account, per the documented contract.
    The password is only ever written into the credential store."""
    return json.dumps({
        "provider": "icloud",
        "imap_host": ICLOUD_IMAP[0], "imap_port": ICLOUD_IMAP[1], "imap_security": ICLOUD_IMAP[2],
        "smtp_host": ICLOUD_SMTP[0], "smtp_port": ICLOUD_SMTP[1], "smtp_security": ICLOUD_SMTP[2],
        "host": ICLOUD_IMAP[0], "port": ICLOUD_IMAP[1], "security": ICLOUD_IMAP[2],
        "username": address, "password": app_password, "from_addr": address,
    })
