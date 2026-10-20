"""Comments from people who downloaded a skill, with server-side moderation.

The website never decides what is published. Refused text is not stored; held
comments stay invisible to everyone but their author until an operator acts.
"""
from __future__ import annotations

import secrets

from core.registry.codec import require
from registry_service.moderation import review

COMMENTS_PER_DAY = 5
REPORTS_TO_HOLD = 3
NEW_ACCOUNT_SECONDS = 86400


def _published(db, package_id: object) -> str:
    require(isinstance(package_id, str) and 0 < len(package_id) <= 129, "invalid package id")
    require(db.execute("SELECT 1 FROM public_versions WHERE package_id=?", (package_id,)).fetchone(), "package not published")
    return package_id


def record_download(store, account: str, package_id: str, *, now: int) -> None:
    with store.connection() as db:
        db.execute("INSERT OR IGNORE INTO account_downloads VALUES (?,?,?)", (account, package_id, now))


def comments(store, package_id: object, viewer: str | None = None) -> dict:
    with store.connection() as db:
        _published(db, package_id)
        rows = db.execute("""SELECT comments.id, accounts.namespace, comments.created, comments.body FROM comments
            JOIN accounts ON accounts.id=comments.account WHERE package_id=? AND state='published'
            ORDER BY comments.created DESC, comments.id LIMIT 200""", (package_id,)).fetchall()
        result = {"comments": [{"id": row[0], "author": row[1], "created": row[2], "body": row[3]} for row in rows],
                  "can_comment": False}
        if viewer:
            # Signed-out viewers get no reason code; the website knows from /me.
            refusal = _eligibility(db, viewer, package_id)
            result["can_comment"] = refusal is None
            if refusal:
                result["can_comment_reason"] = refusal[0]
            result["mine"] = [{"id": row[0], "state": row[1], "created": row[2], "body": row[3]} for row in db.execute(
                """SELECT id, state, created, body FROM comments WHERE package_id=? AND account=?
                AND state IN ('published','held') ORDER BY created DESC LIMIT 50""", (package_id, viewer))]
    return result


def _eligibility(db, account: str, package_id: str) -> tuple[str, str] | None:
    """Return (reason code, message) when the account may not comment, else None."""
    row = db.execute("SELECT namespace FROM accounts WHERE id=?", (account,)).fetchone()
    if not row or not row[0]:
        return "no_public_name", "choose a public name on your profile before commenting"
    if db.execute("SELECT 1 FROM muted_accounts WHERE account=?", (account,)).fetchone():
        return "muted", "commenting is turned off for this account"
    if not db.execute("SELECT 1 FROM account_downloads WHERE account=? AND package_id=?", (account, package_id)).fetchone():
        return "not_downloaded", "download this skill while signed in before commenting"
    return None


def post_comment(store, account: str, package_id: object, body: object, *, now: int) -> dict:
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        _published(db, package_id)
        refusal = _eligibility(db, account, package_id)
        require(refusal is None, refusal[1] if refusal else "")
        count = db.execute("SELECT count(*) FROM comments WHERE account=? AND created>?", (account, now - 86400)).fetchone()[0]
        require(count < COMMENTS_PER_DAY, "daily comment limit reached")
        state, reason = review(body)
        identifier = secrets.token_hex(16)
        if state == "refused":
            # Count the attempt against the daily limit without keeping its text.
            db.execute("INSERT INTO comments VALUES (?,?,?,?,?,?)", (identifier, package_id, account, now, "", "refused"))
            return {"state": "refused", "reason": reason}
        created = db.execute("SELECT created FROM accounts WHERE id=?", (account,)).fetchone()[0]
        if state == "published" and created > now - NEW_ACCOUNT_SECONDS:
            state, reason = "held", "waiting for review"
        db.execute("INSERT INTO comments VALUES (?,?,?,?,?,?)", (identifier, package_id, account, now, body.strip(), state))
    return {"id": identifier, "state": state, "reason": reason}


def report_comment(store, account: str, identifier: object, *, now: int) -> dict:
    require(isinstance(identifier, str) and len(identifier) == 32, "invalid comment id")
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT account, state FROM comments WHERE id=?", (identifier,)).fetchone()
        require(row and row["state"] == "published", "comment not found")
        require(row["account"] != account, "you cannot report your own comment")
        db.execute("INSERT OR IGNORE INTO comment_reports VALUES (?,?,?)", (identifier, account, now))
        reports = db.execute("SELECT count(*) FROM comment_reports WHERE comment=?", (identifier,)).fetchone()[0]
        if reports >= REPORTS_TO_HOLD:
            db.execute("UPDATE comments SET state='held' WHERE id=?", (identifier,))
    return {"status": "reported"}


# Operator-only functions. There is no HTTP route for these.

def held(store) -> list[dict]:
    with store.connection() as db:
        return [{"id": row[0], "package_id": row[1], "author": row[2], "created": row[3], "body": row[4], "reports": row[5]}
                for row in db.execute("""SELECT comments.id, package_id, accounts.namespace, comments.created, body,
                    (SELECT count(*) FROM comment_reports WHERE comment=comments.id) FROM comments
                    JOIN accounts ON accounts.id=comments.account WHERE state='held' ORDER BY comments.created""")]


def moderate(store, identifier: str, action: str) -> None:
    require(action in {"publish", "remove"}, "unknown moderation action")
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT state FROM comments WHERE id=?", (identifier,)).fetchone()
        require(row and row[0] in {"held", "published"}, "comment not found")
        if action == "publish":
            require(row[0] == "held", "only held comments can be published")
            db.execute("DELETE FROM comment_reports WHERE comment=?", (identifier,))
            db.execute("UPDATE comments SET state='published' WHERE id=?", (identifier,))
        else:
            # Removal keeps no text.
            db.execute("UPDATE comments SET state='removed', body='' WHERE id=?", (identifier,))


def mute(store, namespace: str, enabled: bool, *, now: int) -> None:
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT id FROM accounts WHERE namespace=?", (namespace,)).fetchone()
        require(row, "account not found")
        if enabled:
            db.execute("INSERT OR IGNORE INTO muted_accounts VALUES (?,?)", (row[0], now))
            db.execute("UPDATE comments SET state='held' WHERE account=? AND state='published'", (row[0],))
        else:
            db.execute("DELETE FROM muted_accounts WHERE account=?", (row[0],))
