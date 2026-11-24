"""Reports about a skill, sent from the website by a signed-in person.

A report holds the skill, a reason from a fixed list and an optional note the person typed, tied to
the sender's account and time. Nothing from VOOL, chats, memory or wallets is ever attached. Reports
are private: only operators read them, from the command line, and they see an opaque handle instead
of the account. Safety reports from three different people, each with an account two weeks old, pause
downloads of every version of that skill for 48 hours; a team step keeps it paused for longer. A team's
own skill and a version marked tested are never paused by reports alone: the reports wait for the
operator. The signed catalog still decides what is listed.
"""
from __future__ import annotations

import hashlib
import re
import secrets
import time
import unicodedata

from core.registry.codec import DOWNLOADS_PAUSED, canonical, require, version_key
from core.secret_redaction import _bip39_index, contains_secret, mnemonic_redaction_available
from registry_service.community import _published, people, report_counts
from registry_service.store import CARRIED, Store, accounts_holding, identity_keys, owns

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
# Counting reports from this many different people pause a skill; one or two angry people never do.
REPORTS_TO_PAUSE = 3
# A pause that only reports caused ends by itself after this long unless a team step (skill-pause) confirms
# it, so the worst a group of reporters can do to a harmless skill is two days.
REPORT_PAUSE_SECONDS = 48 * 3600
# Reports per person per 24 hours, so one person cannot bury the operators.
REPORTS_PER_DAY = 10
# Long enough to say what is wrong, short enough to read.
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


def _theirs(account: str, keys: list[str]) -> tuple[str, tuple]:
    """SQL for "a report this person sent": by this account, or by an account they deleted (the same sign-in)."""
    return "(" + " OR ".join(["account=?"] + ["instr(reporter,?)>0"] * len(keys)) + ")", (account, *keys)


def _eligibility(db, account: str, package_id: str, *, now: int) -> tuple[str, str] | None:
    """Return (reason code, message) when the account may not report this skill, else None."""
    if owns(db, account, package_id):
        return "own_skill", "you cannot report your own skill"
    theirs, who = _theirs(account, identity_keys(db, account))
    if db.execute("SELECT 1 FROM skill_reports WHERE package_id=? AND state='open' AND " + theirs,
                  (package_id, *who)).fetchone():
        return "already_reported", "you already reported this skill; a person will look at it"
    if db.execute("SELECT count(*) FROM skill_reports WHERE created>? AND " + theirs,
                  (now - 86400, *who)).fetchone()[0] >= REPORTS_PER_DAY:
        return "daily_limit", "you have sent the most reports allowed for now; you can send more later"
    return None


def _counting(db, package_id: str) -> int:
    """How many different people have an open, counting report on this skill."""
    return people((row[0] or "").split() for row in db.execute(
        "SELECT reporter FROM skill_reports WHERE package_id=? AND state='open' AND counted=1", (package_id,)))


def _protected(db, package_id: str) -> str | None:
    """Why reports alone never pause this skill: it is a team's own, or the version on show is marked tested."""
    marks = Store._marks(db)
    if package_id.split("/")[0] in marks["teams"]:
        return "team"
    versions = db.execute("SELECT version, sha256 FROM public_versions WHERE package_id=?", (package_id,)).fetchall()
    if versions and package_id + "@" + max(versions, key=lambda row: version_key(row[0]))[1] in marks["tested"]:
        return "tested"
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


def _note(value: object, reason: str, *, limit: int = MAX_NOTE) -> str:
    require(isinstance(value, str), "the note must be text")
    note = unicodedata.normalize("NFC", value).strip()
    require(len(note) <= limit, f"the note is at most {limit} characters")
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
        keys = identity_keys(db, account)
        theirs, who = _theirs(account, keys)
        # A person whose earlier report on this skill was already looked at cannot pause it again.
        repeat = db.execute("SELECT 1 FROM skill_reports WHERE package_id=? AND state='closed' AND " + theirs,
                            (package_id, *who)).fetchone()
        # New, muted and repeating senders are heard, but cannot pause a skill.
        counted = value["reason"] in PAUSING and report_counts(db, account, package_id, now=now) and not repeat
        db.execute("INSERT INTO skill_reports (id,package_id,account,created,reason,note,counted,state,reporter) "
                   "VALUES (?,?,?,?,?,?,?,?,?)", (secrets.token_hex(16), package_id, account, now, value["reason"], note,
                                                  int(counted), "open", " ".join(keys)))
        # A pause that ended by itself keeps its row until an operator closes the reports, so the same pile of
        # reports pauses a skill once, not every 48 hours.
        if _counting(db, package_id) >= REPORTS_TO_PAUSE and _protected(db, package_id) is None:
            db.execute("INSERT OR IGNORE INTO paused_packages VALUES (?,?,?)", (package_id, now, "reports"))
    return {"status": "received"}


def paused(store, *, now: int) -> frozenset[str]:
    """Skills whose downloads are paused at `now`. A pause from reports alone has ended after 48 hours."""
    with store.connection() as db:
        return frozenset(row[0] for row in db.execute(
            "SELECT package_id FROM paused_packages WHERE cause<>'reports' OR created>?", (now - REPORT_PAUSE_SECONDS,)))


# Operator-only functions. There is no HTTP route for these.

def _handle(sender: str) -> str:
    """Lets an operator see that reports came from the same person without learning who it is."""
    return hashlib.sha256(b"vool-report-handle\0" + sender.encode()).hexdigest()[:12]


def open_reports(store, *, now: int | None = None) -> list[dict]:
    """Every skill with open reports or a pause, so no pause is ever out of sight.

    `paused` is the cause while downloads are paused at `now` (the clock's time when none is given). A pause
    from reports alone shows when it ends, and stays listed as ended until an operator closes the reports.
    `counting` is how many different people's reports count; `protected` says why that paused nothing.
    """
    now = int(time.time()) if now is None else now
    with store.connection() as db:
        grouped = {}
        for row in db.execute("SELECT package_id, cause, created FROM paused_packages"):
            entry = grouped[row[0]] = {"package_id": row[0], "paused": row[1], "reports": [], "paused_since": row[2]}
            if row[1] == "reports":
                entry["pause_lapses"] = row[2] + REPORT_PAUSE_SECONDS
                if entry["pause_lapses"] <= now:
                    entry.update(paused=None, pause_lapsed=True)
        for row in db.execute("""SELECT id, package_id, account, created, reason, note, counted, reporter FROM skill_reports
                WHERE state='open' ORDER BY package_id, created, rowid"""):
            grouped.setdefault(row["package_id"], {"package_id": row["package_id"], "paused": None, "reports": []})
            sender = (row["reporter"] or "").split()
            grouped[row["package_id"]]["reports"].append(
                {"id": row["id"], "reporter": _handle(sender[0] if sender else row["account"] or row["id"]),
                 "created": row["created"], "reason": row["reason"], "note": row["note"], "counted": bool(row["counted"])} |
                ({} if row["account"] else {"account_deleted": True}))
        for package_id, entry in grouped.items():
            entry["counting"] = _counting(db, package_id)
            protected = _protected(db, package_id)
            if protected:
                entry["protected"] = protected
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


def mute_sender(db, report_id: str, *, now: int) -> None:
    """Mute the person behind one report. The caller owns the transaction (the CLI below, or a signed team step)."""
    row = db.execute("SELECT account, reporter FROM skill_reports WHERE id=?", (report_id,)).fetchone()
    keys = (row["reporter"] or "").split() if row else []
    require(row is not None and (row["account"] is not None or keys), "report not found")
    accounts = {row["account"]} if row["account"] else set()
    if not row["account"]:
        # The sender deleted their account. The mute waits on what the deletion left, so it returns with the
        # same sign-in, and it lands at once on an account that sign-in already came back with.
        for key in keys:
            db.execute("INSERT INTO retired_identities (identity,deleted_at,muted,recent) VALUES (?,0,1,?) "
                       "ON CONFLICT(identity) DO UPDATE SET muted=1",
                       (key, canonical({kind: [] for kind in CARRIED})))
        accounts = accounts_holding(db, keys)
    for account in accounts:
        db.execute("INSERT OR IGNORE INTO muted_accounts VALUES (?,?)", (account, now))
        db.execute("UPDATE comments SET state='held' WHERE account=? AND state='published'", (account,))
        db.execute("UPDATE skill_reports SET counted=0 WHERE account=? AND state='open'", (account,))
    for key in keys:
        db.execute("UPDATE skill_reports SET counted=0 WHERE state='open' AND instr(reporter,?)>0", (key,))


def mute_reporter(store, report_id: str, *, now: int) -> None:
    """Mute the person behind one report: their reports stop counting and their comments are held."""
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        mute_sender(db, report_id, now=now)
