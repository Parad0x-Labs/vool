"""Reports about a skill, sent from the website by a signed-in person.

A report holds the skill, a reason from a fixed list and an optional note the person typed, tied to
the sender's account and time. Nothing from VOOL, chats, memory or wallets is ever attached. Reports
are private: only operators read them, from the command line, and they see an opaque handle instead
of the account. Three counting safety reports pause downloads of every version of that skill until an
operator looks; the signed catalog still decides what is listed.
"""
from __future__ import annotations

import hashlib
import re
import secrets
import unicodedata

from core.registry.codec import DOWNLOADS_PAUSED, require
from core.secret_redaction import _bip39_index, contains_secret, mnemonic_redaction_available
from registry_service.community import NEW_ACCOUNT_SECONDS, _published
from registry_service.store import owns

REASONS = {
    "scam": "Scam, or asks for money, passwords, keys or a recovery phrase",
    "harmful": "Harmful, or does things its description doesn't mention",
    "stolen": "Copied from someone else without permission",
    "picture": "Misleading or offensive picture",
    "broken": "Doesn't work",
    "other": "Something else (explain in the note)",
}
# Reasons about safety. Pictures already wait for a person's look, and a broken skill harms nobody.
PAUSING = frozenset({"scam", "harmful", "stolen"})
REPORTS_TO_PAUSE = 3
REPORTS_PER_DAY = 10
MAX_NOTE = 500
PAUSED = DOWNLOADS_PAUSED
# Zero-width joiners and direction marks belong to emoji and to Hindi, Arabic and Hebrew text; the
# other format characters (direction overrides, invisible spaces) can disguise what a note says.
_ALLOWED_FORMAT = {"‌", "‍", "‎", "‏"}
_LIST_MARKERS = re.compile(r"(?<!\w)#?\d{1,2}[.):]?(?=\s)|[-/|;,•·]")
_PASSWORD = re.compile(r"(?i)\b(?:password|passcode|passphrase|pin)\s+(?:is|was)\s*:?\s*\S{4,}")
_HEX_KEY = re.compile(r"[0-9a-fA-F]{64}")


def public_reasons() -> list[dict]:
    return [{"id": key, "label": label} for key, label in REASONS.items()]


def _eligibility(db, account: str, package_id: str, *, now: int) -> tuple[str, str] | None:
    """Return (reason code, message) when the account may not report this skill, else None."""
    if owns(db, account, package_id):
        return "own_skill", "you cannot report your own skill"
    if db.execute("SELECT 1 FROM skill_reports WHERE account=? AND package_id=? AND state='open'",
                  (account, package_id)).fetchone():
        return "already_reported", "you already reported this skill; a person will look at it"
    if db.execute("SELECT count(*) FROM skill_reports WHERE account=? AND created>?",
                  (account, now - 86400)).fetchone()[0] >= REPORTS_PER_DAY:
        return "daily_limit", "you have sent the most reports allowed for now; you can send more later"
    return None


def status(store, package_id: object, viewer: str | None, *, now: int) -> dict:
    with store.connection() as db:
        package_id = _published(db, package_id)
        # A signed-out viewer, or one whose session has ended, is told to sign in.
        refusal = _eligibility(db, viewer, package_id, now=now) if viewer else ("signed_out", "")
    return {"reasons": public_reasons(), "note_limit": MAX_NOTE, "can_report": refusal is None} | (
        {"can_report_reason": refusal[0]} if refusal else {})


def _looks_secret(note: str) -> bool:
    """Wider than redaction on purpose: refusing a harmless note costs a retype, keeping a phrase costs a wallet."""
    if contains_secret(note) or _PASSWORD.search(note):
        return True
    if _HEX_KEY.search(re.sub(r"[\s:-]", "", note)):
        return True
    # A phrase written as "1. word 2. word", "word-word-word" or "word / word", or one with a typo or a missing
    # word that fails its checksum, is still a phrase: 11 list words in any 12.
    spaced = _LIST_MARKERS.sub(" ", unicodedata.normalize("NFKC", note).casefold())
    words = _bip39_index()
    listed = [token.strip(".,;:!?\"'()[]") in words for token in spaced.split()]
    return any(sum(listed[start:start + 12]) >= 11 for start in range(max(1, len(listed) - 11)))


def _note(value: object, reason: str) -> str:
    require(isinstance(value, str), "the note must be text")
    note = unicodedata.normalize("NFC", value).strip()
    require(len(note) <= MAX_NOTE, f"the note is at most {MAX_NOTE} characters")
    require(all(unicodedata.category(char) not in {"Cc", "Cf", "Co", "Cs"} or char in "\n\t" or char in _ALLOWED_FORMAT
                for char in note), "the note must be plain text")
    require(bool(note) or reason != "other", "describe the problem in the note")
    if note:
        # Never keep a key or recovery phrase someone pasted by mistake, and refuse notes while that check cannot run.
        require(mnemonic_redaction_available(), "notes can't be sent right now; you can send the report without one")
        require(not _looks_secret(note), "please take out any keys, passwords or recovery phrases; "
                                         "nobody needs them to look into a report")
    return note


def file_report(store, account: str, value: object, *, now: int) -> dict:
    require(isinstance(value, dict) and {"id", "reason"} <= set(value) <= {"id", "reason", "note"},
            "invalid report fields")
    require(value["reason"] in REASONS, "choose one of the listed reasons")
    note = _note(value.get("note", ""), value["reason"])
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        package_id = _published(db, value["id"])
        refusal = _eligibility(db, account, package_id, now=now)
        require(refusal is None, refusal[1] if refusal else "")
        created = db.execute("SELECT created FROM accounts WHERE id=?", (account,)).fetchone()[0]
        muted = db.execute("SELECT 1 FROM muted_accounts WHERE account=?", (account,)).fetchone()
        # An account whose earlier report on this skill was already looked at cannot pause it again.
        repeat = db.execute("SELECT 1 FROM skill_reports WHERE account=? AND package_id=? AND state='closed'",
                            (account, package_id)).fetchone()
        # Brand-new, muted and repeating accounts are heard, but cannot pause a skill.
        counted = value["reason"] in PAUSING and created <= now - NEW_ACCOUNT_SECONDS and not muted and not repeat
        db.execute("INSERT INTO skill_reports VALUES (?,?,?,?,?,?,?,?)", (
            secrets.token_hex(16), package_id, account, now, value["reason"], note, int(counted), "open"))
        counting = db.execute("SELECT count(*) FROM skill_reports WHERE package_id=? AND state='open' AND counted=1",
                              (package_id,)).fetchone()[0]
        if counting >= REPORTS_TO_PAUSE:
            db.execute("INSERT OR IGNORE INTO paused_packages VALUES (?,?,?)", (package_id, now, "reports"))
    return {"status": "received"}


def paused(store) -> frozenset[str]:
    with store.connection() as db:
        return frozenset(row[0] for row in db.execute("SELECT package_id FROM paused_packages"))


# Operator-only functions. There is no HTTP route for these.

def _handle(account: str) -> str:
    """Lets an operator see that reports came from the same account without learning which account it is."""
    return hashlib.sha256(b"vool-report-handle\0" + account.encode()).hexdigest()[:12]


def open_reports(store) -> list[dict]:
    """Every skill with open reports or a pause, so no pause is ever out of sight."""
    with store.connection() as db:
        grouped = {row[0]: {"package_id": row[0], "paused": row[1], "reports": []}
                   for row in db.execute("SELECT package_id, cause FROM paused_packages")}
        for row in db.execute("""SELECT id, package_id, account, created, reason, note, counted FROM skill_reports
                WHERE state='open' ORDER BY package_id, created, rowid"""):
            grouped.setdefault(row["package_id"], {"package_id": row["package_id"], "paused": None, "reports": []})
            grouped[row["package_id"]]["reports"].append(
                {"id": row["id"], "reporter": _handle(row["account"]), "created": row["created"],
                 "reason": row["reason"], "note": row["note"], "counted": bool(row["counted"])})
    return [grouped[package_id] for package_id in sorted(grouped)]


def _close(db, package_id: str) -> None:
    # A looked-at report keeps no text.
    db.execute("UPDATE skill_reports SET state='closed', note='' WHERE package_id=? AND state='open'", (package_id,))


def close(store, package_id: str) -> None:
    """The reports were looked at: close them. A pause the reports caused ends; an operator's pause stays."""
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        require(db.execute("""SELECT 1 FROM skill_reports WHERE package_id=? AND state='open'
            UNION SELECT 1 FROM paused_packages WHERE package_id=?""", (package_id, package_id)).fetchone(),
                "no open reports or pause for this skill")
        _close(db, package_id)
        db.execute("DELETE FROM paused_packages WHERE package_id=? AND cause='reports'", (package_id,))


def pause(store, package_id: str, *, now: int) -> None:
    """Keep downloads paused by an operator's decision, until `resume` or a revocation in the signed catalog."""
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        package_id = _published(db, package_id)
        _close(db, package_id)
        db.execute("INSERT OR REPLACE INTO paused_packages VALUES (?,?,?)", (package_id, now, "operator"))


def resume(store, package_id: str) -> None:
    """End any pause and close the open reports: downloads continue."""
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        require(db.execute("SELECT 1 FROM paused_packages WHERE package_id=?", (package_id,)).fetchone(),
                "this skill is not paused")
        _close(db, package_id)
        db.execute("DELETE FROM paused_packages WHERE package_id=?", (package_id,))


def mute_reporter(store, report_id: str, *, now: int) -> None:
    """Mute the account behind one report: its reports stop counting and its comments are held."""
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT account FROM skill_reports WHERE id=?", (report_id,)).fetchone()
        require(row is not None and row[0] is not None, "report not found")
        db.execute("INSERT OR IGNORE INTO muted_accounts VALUES (?,?)", (row[0], now))
        db.execute("UPDATE comments SET state='held' WHERE account=? AND state='published'", (row[0],))
        db.execute("UPDATE skill_reports SET counted=0 WHERE account=? AND state='open'", (row[0],))
