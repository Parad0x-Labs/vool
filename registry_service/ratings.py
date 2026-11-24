"""Ratings from people who installed an add-on, and the record of what the team removed.

A rating is 1 to 5 stars and an optional text: one per account per add-on, which its owner can change
or delete. A first rating needs an install pass. The service puts a pass on every package download made
through the install route; VOOL keeps it with the installed copy and hands it over only when the person
presses Rate. A pass proves that download, not that the add-on was used.

What is kept: the rating a person chose to publish, and for a used pass its hash, the add-on and the day.
No pass is recorded when it is handed out, and the used-pass table has no account, so nothing here says
which add-ons a person installed and did not rate. Nothing in this module reads or stores where a request
came from or what program sent it.

Team steps arrive signed, with the marks (registry_service.operations). Each one writes a row in
moderation_log in the same transaction, and the public reads what happened from /moderation/notices.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import statistics
import time
import unicodedata

from core.registry.codec import IDENTITY, digest, require, version_key
from registry_service.moderation import review

PASS_HEADER = "X-Vool-Review-Pass"
PASS_KEY = "review-pass-hmac"
# A pass can be used from the day after the download, for 180 days.
PASS_WAIT_DAYS = 1
PASS_LIFE_DAYS = 180
# New ratings per account per 24 hours. There is no weekly or monthly limit; this only stops scripts.
RATINGS_PER_DAY = 20
# A rating counts once its account is this old, or its GitHub account is.
COUNT_ACCOUNT_AGE = 7 * 86400
COUNT_GITHUB_AGE = 30 * 86400
# Text from an account younger than this waits for a person's look.
TEXT_ACCOUNT_AGE = 30 * 86400
# No average is shown, and nothing is sorted, under this many counted ratings.
MIN_SHOWN = 5
# Sorting pulls every add-on toward the registry-wide mean with the weight of this many ratings.
PRIOR_WEIGHT = 10
PRIOR_MEAN = 3.5
PRIOR_AFTER = 50
# More new ratings on one add-on in a day than this, or than three times its usual day, are held.
BURST_FLOOR = 10
BURST_DAYS = 14
REPORTS_TO_HOLD = 3
REPORTS_PER_DAY = 10
REPORT_ACCOUNT_AGE = 14 * 86400
# Text the team removed is kept this long, for a second look, then erased.
REMOVED_TEXT_DAYS = 30
APPEALS_PER_DAY = 10
MAX_APPEAL = 1000
PAGE = 50
ANONYMOUS = "VOOL user"

# Why the team acted. The code goes in the step; the sentence is what the public reads.
REASONS = {
    "fake": "These ratings did not come from people who used the add-on.",
    "automated": "These ratings were made by a script.",
    "self_rating": "The creator rated their own add-on.",
    "paid": "These ratings were paid for.",
    "abusive": "The text was abusive.",
    "off_topic": "The text was not about the add-on.",
    "harmful": "This add-on can cause harm.",
    "stolen": "This add-on was copied from someone else without permission.",
    "misleading": "This add-on does not do what its page says.",
    "creator_request": "The creator asked for this.",
    "mistake_reversed": "An earlier team step was a mistake and was undone.",
}
APPEAL_ANSWERS = {"upheld": "The team looked again and is undoing the step.",
                  "rejected": "The team looked again and the step stands."}

SCHEMA = """
    CREATE TABLE IF NOT EXISTS ratings (id TEXT NOT NULL UNIQUE,
        account TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE, package_id TEXT NOT NULL,
        stars INTEGER NOT NULL, body TEXT NOT NULL, version TEXT NOT NULL, artifact_sha256 TEXT NOT NULL,
        proof TEXT NOT NULL, state TEXT NOT NULL, show_name INTEGER NOT NULL, text_sig TEXT,
        created INTEGER NOT NULL, updated INTEGER NOT NULL, void_reason TEXT, voided_at INTEGER, void_case INTEGER,
        removed_body TEXT, text_removed_at INTEGER, text_case INTEGER, PRIMARY KEY(account, package_id));
    CREATE INDEX IF NOT EXISTS ratings_package ON ratings(package_id, state, created);
    CREATE INDEX IF NOT EXISTS ratings_text ON ratings(text_sig);
    CREATE TABLE IF NOT EXISTS review_passes_spent (digest TEXT PRIMARY KEY, package_id TEXT NOT NULL,
        day INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS rating_reports (rating TEXT NOT NULL,
        account TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE, created INTEGER NOT NULL,
        PRIMARY KEY(rating, account));
    CREATE TABLE IF NOT EXISTS rating_freezes (package_id TEXT PRIMARY KEY, reason TEXT NOT NULL,
        created INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS banned_accounts (account TEXT PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
        reason TEXT NOT NULL, created INTEGER NOT NULL, case_id INTEGER);
    CREATE TABLE IF NOT EXISTS moderation_log (id INTEGER PRIMARY KEY AUTOINCREMENT, sequence INTEGER NOT NULL,
        step TEXT NOT NULL, action TEXT NOT NULL, target TEXT NOT NULL, reason TEXT, created INTEGER NOT NULL,
        affected INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS moderation_notices (log INTEGER NOT NULL REFERENCES moderation_log(id),
        package_id TEXT NOT NULL, affected INTEGER NOT NULL, PRIMARY KEY(log, package_id));
    CREATE TABLE IF NOT EXISTS appeals (id TEXT PRIMARY KEY,
        account TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE, case_id INTEGER NOT NULL,
        text TEXT NOT NULL, state TEXT NOT NULL, answer TEXT NOT NULL, created INTEGER NOT NULL);
    CREATE INDEX IF NOT EXISTS appeals_account ON appeals(account, created);
"""
_PASS = re.compile(r"[A-Za-z0-9_-]{135}")
_ID = re.compile(r"[0-9a-f]{32}")
_HANDLE = re.compile(r"[0-9a-f]{12}")
_HOUR = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}")


def install_schema(db) -> None:
    """The tables above and the pass key. The table of signed-in downloads is gone: a pass replaces it."""
    db.executescript(SCHEMA)
    db.execute("DROP TABLE IF EXISTS account_downloads")
    db.execute("INSERT OR IGNORE INTO service_keys VALUES (?,?)", (PASS_KEY, secrets.token_bytes(32)))


def _key(db) -> bytes:
    value = db.execute("SELECT value FROM service_keys WHERE name=?", (PASS_KEY,)).fetchone()[0]
    require(isinstance(value, bytes) and len(value) == 32, "invalid private pass key")
    return value


def _day(day: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(day * 86400))


# ---- the install pass ----

def make_pass(key: bytes, package_id: str, sha256: str, day: int, ticket: bytes) -> str:
    """One pass per download ticket: the same ticket, tried again, gets the same pass. Nothing is written."""
    body = (b"\x01" + hashlib.sha256(package_id.encode()).digest()[:16] + bytes.fromhex(sha256) +
            day.to_bytes(4, "big") + hmac.digest(key, b"vool-review-pass-nonce\0" + ticket, "sha256")[:16])
    return base64.urlsafe_b64encode(body + hmac.digest(key, body, "sha256")).decode().rstrip("=")


def read_pass(db, text: object, package_id: str, *, now: int) -> dict:
    """The version and checksum a pass names, or the sentence that says why it can't be used here."""
    unreadable = "this install pass can't be read; press Rate in VOOL again"
    require(isinstance(text, str) and bool(_PASS.fullmatch(text)), unreadable)
    try:
        token = base64.b64decode(text + "=", altchars=b"-_", validate=True)
    except ValueError:
        require(False, unreadable)
    require(len(token) == 101 and token[0] == 1 and
            hmac.compare_digest(token[69:], hmac.digest(_key(db), token[:69], "sha256")), unreadable)
    row = db.execute("SELECT version FROM public_versions WHERE package_id=? AND sha256=?",
                     (package_id, token[17:49].hex())).fetchone()
    require(token[1:17] == hashlib.sha256(package_id.encode()).digest()[:16] and row is not None,
            "install pass is for another add-on")
    day, today = int.from_bytes(token[49:53], "big"), now // 86400
    require(today >= day + PASS_WAIT_DAYS,
            "install pass not valid yet; you can rate from " + _day(day + PASS_WAIT_DAYS))
    require(today <= day + PASS_LIFE_DAYS, "install pass expired")
    spent = hashlib.sha256(token).hexdigest()
    require(not db.execute("SELECT 1 FROM review_passes_spent WHERE digest=?", (spent,)).fetchone(),
            "install pass already used")
    return {"version": row[0], "sha256": token[17:49].hex(), "digest": spent, "day": day}


# ---- who and what ----

def _published(db, package_id: object) -> str:
    require(isinstance(package_id, str) and 0 < len(package_id) <= 129, "invalid package id")
    require(db.execute("SELECT 1 FROM public_versions WHERE package_id=?", (package_id,)).fetchone(),
            "this add-on is not listed")
    return package_id


def banned(db, account: str) -> bool:
    return db.execute("SELECT 1 FROM banned_accounts WHERE account=?", (account,)).fetchone() is not None


def _own(store, db, account: str, package_id: str) -> bool:
    """The creator, and a member of the team that publishes it."""
    from registry_service.store import owns

    return owns(db, account, package_id) or store._team_member(db, account, package_id.split("/")[0])


def _handle(db, account: str) -> str:
    """What operators see for an account without a public name; the same handle as on its reports."""
    from registry_service.reports import _handle as handle
    from registry_service.store import identity_keys

    keys = identity_keys(db, account)
    return handle(keys[0] if keys else account)


def _account_for(db, target: str) -> str:
    """The account a team step names: by public name, or by the handle operators see."""
    row = db.execute("SELECT id FROM accounts WHERE namespace=?", (target,)).fetchone()
    if row:
        return row[0]
    if _HANDLE.fullmatch(target):
        for (account,) in db.execute("SELECT DISTINCT account FROM identities").fetchall():
            if _handle(db, account) == target:
                return account
    require(False, "account not found")


def text_sig(body: str) -> str | None:
    """A hash of the words alone, so the same sentence with other spacing or capitals is the same text."""
    words = re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", body).casefold())
    return hashlib.sha256(" ".join(words).encode()).hexdigest() if words else None


def _counts(db, account: str, now: int) -> bool:
    created = db.execute("SELECT created FROM accounts WHERE id=?", (account,)).fetchone()[0]
    since = db.execute("SELECT min(since) FROM identities WHERE account=? AND provider='github'", (account,)).fetchone()[0]
    return created <= now - COUNT_ACCOUNT_AGE or (since is not None and since <= now - COUNT_GITHUB_AGE)


def _burst(db, package_id: str, now: int) -> bool:
    """Whether today's new ratings on this add-on are far above its usual day."""
    today = db.execute("SELECT count(*) FROM ratings WHERE package_id=? AND created>?", (package_id, now - 86400)).fetchone()[0]
    days = [0] * BURST_DAYS
    for (created,) in db.execute("SELECT created FROM ratings WHERE package_id=? AND created>? AND created<=?",
                                 (package_id, now - (BURST_DAYS + 1) * 86400, now - 86400)):
        days[min(BURST_DAYS - 1, (now - 86400 - created) // 86400)] += 1
    return today + 1 > max(BURST_FLOOR, 3 * statistics.median(days))


def _said(reason: str) -> str:
    # The text filter is the one comments use; its sentences name what was sent.
    return reason.replace("Comments", "Rating text").replace("comments are", "rating text is").replace("comment text", "rating text")


# ---- a person's own rating ----

def put(store, account: str, value: object, *, now: int) -> dict:
    require(isinstance(value, dict) and {"id", "stars"} <= set(value) <= {"id", "stars", "body", "pass", "show_name"},
            "invalid rating fields")
    require(type(value["stars"]) is int and 1 <= value["stars"] <= 5, "stars must be 1 to 5")
    body, show = value.get("body", ""), value.get("show_name", True)
    require(isinstance(body, str), "rating text must be text")
    require(type(show) is bool, "show_name is true or false")
    body, state, reason = body.strip(), "published", ""
    if body:
        state, reason = review(body)
        if state == "refused":
            return {"state": "refused", "reason": _said(reason)}  # nothing is kept, and no pass is used
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        package_id = _published(db, value["id"])
        require(not banned(db, account), "rating is turned off for this account")
        require(not _own(store, db, account, package_id), "you cannot rate your own add-on")
        require(not db.execute("SELECT 1 FROM rating_freezes WHERE package_id=?", (package_id,)).fetchone(),
                "ratings are closed for this add-on")
        row = db.execute("SELECT * FROM ratings WHERE account=? AND package_id=?", (account, package_id)).fetchone()
        require(row is None or row["state"] != "voided",
                "the VOOL team removed this rating; you can ask for another look")
        require(row is not None or value.get("pass") is not None, "install pass required")
        proof = read_pass(db, value["pass"], package_id, now=now) if value.get("pass") is not None else None
        if row is None:
            require(db.execute("SELECT count(*) FROM ratings WHERE account=? AND created>?",
                               (account, now - 86400)).fetchone()[0] < RATINGS_PER_DAY, "rating limit reached")
        signature = text_sig(body) if body else None
        created = db.execute("SELECT created FROM accounts WHERE id=?", (account,)).fetchone()[0]
        if state == "published" and (
                db.execute("SELECT 1 FROM muted_accounts WHERE account=?", (account,)).fetchone() or
                (body and created > now - TEXT_ACCOUNT_AGE) or
                (signature and db.execute("SELECT 1 FROM ratings WHERE text_sig=? AND account<>?",
                                          (signature, account)).fetchone()) or
                (row is None and _burst(db, package_id, now)) or (row is not None and row["state"] == "held")):
            state, reason = "held", "waiting for review"
        if row is None:
            identifier = secrets.token_hex(16)
            db.execute("INSERT INTO ratings (id,account,package_id,stars,body,version,artifact_sha256,proof,state,"
                       "show_name,text_sig,created,updated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (identifier, account, package_id, value["stars"], body, proof["version"], proof["sha256"],
                        "installed", state, int(show), signature, now, now))
        else:
            identifier = row["id"]
            # An edit keeps the version it was written about; a pass for a later version moves it there.
            db.execute("UPDATE ratings SET stars=?, body=?, state=?, show_name=?, text_sig=?, updated=?, version=?, "
                       "artifact_sha256=?, removed_body=NULL, text_removed_at=NULL, text_case=NULL WHERE id=?",
                       (value["stars"], body, state, int(show), signature, now,
                        proof["version"] if proof else row["version"], proof["sha256"] if proof else row["artifact_sha256"],
                        identifier))
        if proof:
            db.execute("INSERT INTO review_passes_spent VALUES (?,?,?)", (proof["digest"], package_id, proof["day"]))
        counted = state == "published" and _counts(db, account, now)
    return {"id": identifier, "state": state, "counted": counted} | ({"reason": reason} if state == "held" else {})


def delete(store, account: str, package_id: object) -> dict:
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT id, state FROM ratings WHERE account=? AND package_id=?", (account, package_id)).fetchone()
        require(row, "rating not found")
        # A rating the team removed stays as removed, so deleting it and rating again does not bring it back.
        require(row["state"] != "voided", "the VOOL team removed this rating; you can ask for another look")
        db.execute("DELETE FROM rating_reports WHERE rating=?", (row["id"],))
        db.execute("DELETE FROM ratings WHERE id=?", (row["id"],))
    return {"status": "deleted"}


# ---- what the public reads ----

_COUNTED = ("ratings.state='published' AND ratings.account NOT IN (SELECT account FROM banned_accounts) AND "
            "(accounts.created<=? OR EXISTS (SELECT 1 FROM identities WHERE identities.account=ratings.account "
            "AND provider='github' AND since<=?))")


def score(stars: list[int] | tuple[int, ...], mean: float = PRIOR_MEAN) -> float:
    """The sorting score: three 5s (3.85) stay under three hundred ratings at 4.4 (4.37)."""
    return (PRIOR_WEIGHT * mean + sum(stars)) / (PRIOR_WEIGHT + len(stars))


def _summaries(db, *, now: int, package_id: str | None = None) -> dict[str, dict]:
    counted: dict[str, list[tuple[int, str]]] = {}
    for row in db.execute("SELECT package_id, stars, version FROM ratings JOIN accounts ON accounts.id=ratings.account "
                          "WHERE " + _COUNTED, (now - COUNT_ACCOUNT_AGE, now - COUNT_GITHUB_AGE)):
        counted.setdefault(row[0], []).append((row[1], row[2]))
    everything = [stars for rows in counted.values() for stars, _ in rows]
    mean = sum(everything) / len(everything) if len(everything) >= PRIOR_AFTER else PRIOR_MEAN
    removed = dict(db.execute("SELECT package_id, count(*) FROM ratings WHERE state='voided' OR account IN "
                              "(SELECT account FROM banned_accounts) GROUP BY package_id").fetchall())
    frozen = {row[0] for row in db.execute("SELECT package_id FROM rating_freezes")}
    newest: dict[str, str] = {}
    for name, version in db.execute("SELECT package_id, version FROM public_versions"):
        if name not in newest or version_key(version) > version_key(newest[name]):
            newest[name] = version
    result = {}
    for name in ([package_id] if package_id else sorted(set(counted) | set(removed) | frozen)):
        rows, scope = counted.get(name, []), "all"
        major = newest.get(name, "").split(".")[0]
        current = [item for item in rows if item[1].split(".")[0] == major]
        if len(current) >= MIN_SHOWN and len(current) < len(rows):
            # Enough people rated the newest major version: the headline is about that version alone.
            rows, scope = current, "current_major"
        stars = [item[0] for item in rows]
        shown = len(stars) >= MIN_SHOWN
        result[name] = {"shown": shown, "average": round(sum(stars) / len(stars), 1) if shown else None,
                        "count": len(stars),
                        "distribution": [stars.count(value) for value in range(1, 6)] if shown else None,
                        "scope": scope, "frozen": name in frozen, "removed_by_team": removed.get(name, 0),
                        "score": round(score(stars, mean), 4) if shown else None}
    return result


def summaries(store, *, now: int) -> dict:
    """Every rated add-on, best first, for VOOL's cards. An add-on under five counted ratings has no average and sorts last."""
    with store.connection() as db:
        rows = _summaries(db, now=now)
    order = sorted(rows, key=lambda name: (rows[name]["score"] is None, -(rows[name]["score"] or 0), name))
    return {"ratings": [{"id": name, "shown": rows[name]["shown"], "average": rows[name]["average"],
                         "count": rows[name]["count"], "removed": rows[name]["removed_by_team"]} for name in order[:4096]]}


def _author(row) -> str:
    return row["namespace"] if row["show_name"] and row["namespace"] else ANONYMOUS


def listing(store, package_id: object, viewer: str | None, *, after: object = None, now: int) -> dict:
    with store.connection() as db:
        package_id = _published(db, package_id)
        where, args = "", []
        if after is not None:
            require(isinstance(after, str) and bool(_ID.fullmatch(after)), "invalid cursor")
            anchor = db.execute("SELECT created FROM ratings WHERE id=? AND package_id=?", (after, package_id)).fetchone()
            require(anchor, "invalid cursor")
            where, args = " AND (ratings.created<? OR (ratings.created=? AND ratings.id>?))", [anchor[0], anchor[0], after]
        rows = db.execute("SELECT ratings.*, accounts.namespace FROM ratings JOIN accounts ON accounts.id=ratings.account "
                          "WHERE package_id=? AND state='published' AND ratings.account NOT IN "
                          "(SELECT account FROM banned_accounts)" + where +
                          " ORDER BY ratings.created DESC, ratings.id LIMIT ?", (package_id, *args, PAGE + 1)).fetchall()
        summary = _summaries(db, now=now, package_id=package_id)[package_id]
        result = {"summary": {key: summary[key] for key in ("shown", "average", "count", "distribution", "scope",
                                                            "frozen", "removed_by_team")},
                  "ratings": [{"id": row["id"], "author": _author(row), "stars": row["stars"], "body": row["body"],
                               "version": row["version"], "proof": row["proof"], "created": row["created"],
                               "updated": row["updated"]} | ({"text_removed": True} if row["text_removed_at"] else {})
                              for row in rows[:PAGE]],
                  "next": rows[PAGE - 1]["id"] if len(rows) > PAGE else None, "can_rate": False}
        if viewer:
            mine = db.execute("SELECT * FROM ratings WHERE account=? AND package_id=?", (viewer, package_id)).fetchone()
            refusal = ("banned" if banned(db, viewer) else "own_addon" if _own(store, db, viewer, package_id) else
                       "frozen" if summary["frozen"] else "removed" if mine and mine["state"] == "voided" else None)
            result["can_rate"] = refusal is None
            if refusal:
                result["can_rate_reason"] = refusal
            elif mine is None:
                result["pass_needed"] = True  # a first rating starts from the Rate button in VOOL
            if mine:
                result["mine"] = {"id": mine["id"], "state": mine["state"], "stars": mine["stars"], "body": mine["body"],
                                  "version": mine["version"], "show_name": bool(mine["show_name"]),
                                  "created": mine["created"], "updated": mine["updated"]}
                case = mine["void_case"] if mine["state"] == "voided" else mine["text_case"]
                if case:
                    result["mine"] |= {"case": str(case), "reason": mine["void_reason"] or "",
                                       "sentence": REASONS.get(mine["void_reason"] or "", "")}
    return result


def report(store, account: str, identifier: object, *, now: int) -> dict:
    from registry_service.community import people
    from registry_service.store import identity_keys

    require(isinstance(identifier, str) and bool(_ID.fullmatch(identifier)), "invalid rating id")
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT account, package_id FROM ratings WHERE id=? AND state='published'", (identifier,)).fetchone()
        require(row, "rating not found")
        require(row["account"] != account, "you cannot report your own rating")
        # The creator reads every rating of their add-on and removes none of them.
        require(not _own(store, db, account, row["package_id"]), "the creator of an add-on cannot report its ratings")
        created = db.execute("SELECT created FROM accounts WHERE id=?", (account,)).fetchone()[0]
        require(created <= now - REPORT_ACCOUNT_AGE, "new accounts can report a rating after 14 days")
        require(not banned(db, account) and not db.execute("SELECT 1 FROM muted_accounts WHERE account=?",
                                                         (account,)).fetchone(), "reporting is turned off for this account")
        require(db.execute("SELECT count(*) FROM rating_reports WHERE account=? AND created>?",
                           (account, now - 86400)).fetchone()[0] < REPORTS_PER_DAY,
                "you have sent the most reports allowed for now; you can send more later")
        db.execute("INSERT OR IGNORE INTO rating_reports VALUES (?,?,?)", (identifier, account, now))
        senders = [identity_keys(db, who) for (who,) in db.execute(
            "SELECT account FROM rating_reports WHERE rating=?", (identifier,)).fetchall()]
        if people(senders) >= REPORTS_TO_HOLD:
            # Out of sight and out of the count until a person on the team looks; the team's step decides.
            db.execute("UPDATE ratings SET state='held' WHERE id=?", (identifier,))
    return {"status": "reported"}


def notices(store, package_id: object) -> dict:
    """What the team did about this add-on or its ratings. Who did it, and on what evidence, is not public."""
    with store.connection() as db:
        require(isinstance(package_id, str) and db.execute(
            "SELECT 1 FROM public_versions WHERE package_id=? UNION SELECT 1 FROM moderation_notices WHERE package_id=?",
            (package_id, package_id)).fetchone(), "this add-on is not listed")
        rows = db.execute("SELECT log.id, log.action, log.reason, log.created, notice.affected FROM moderation_notices "
                          "AS notice JOIN moderation_log AS log ON log.id=notice.log WHERE notice.package_id=? "
                          "ORDER BY log.id DESC LIMIT 100", (package_id,)).fetchall()
    return {"notices": [{"case": str(row[0]), "action": row[1], "reason": row[2], "sentence": REASONS.get(row[2] or "", ""),
                         "date": time.strftime("%Y-%m-%d", time.gmtime(row[3])), "affected": row[4]} for row in rows]}


# ---- asking for another look ----

def appeal(store, account: str, value: object, *, now: int) -> dict:
    from registry_service.reports import _note
    from registry_service.store import owns

    require(isinstance(value, dict) and set(value) == {"case", "text"}, "invalid appeal fields")
    require(isinstance(value["case"], str) and value["case"].isdigit() and len(value["case"]) <= 12, "case not found")
    case = int(value["case"])
    text = _note(value["text"], "other", limit=MAX_APPEAL)
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        require(db.execute("SELECT 1 FROM moderation_log WHERE id=?", (case,)).fetchone(), "case not found")
        theirs = db.execute("SELECT 1 FROM ratings WHERE account=? AND (void_case=? OR text_case=?) UNION "
                            "SELECT 1 FROM banned_accounts WHERE account=? AND case_id=?",
                            (account, case, case, account, case)).fetchone() or any(
            owns(db, account, row[0]) for row in db.execute(
                "SELECT package_id FROM moderation_notices WHERE log=?", (case,)).fetchall())
        require(theirs, "this is not your case; only the creator or the person who wrote the rating can ask")
        require(not db.execute("SELECT 1 FROM appeals WHERE account=? AND case_id=? AND state='open'",
                               (account, case)).fetchone(), "you already asked for another look; the team will answer")
        require(db.execute("SELECT count(*) FROM appeals WHERE account=? AND created>?", (account, now - 86400)).fetchone()[0]
                < APPEALS_PER_DAY, "you have asked for the most looks allowed for now; you can ask again later")
        identifier = secrets.token_hex(16)
        db.execute("INSERT INTO appeals VALUES (?,?,?,?,?,?,?)", (identifier, account, case, text, "open", "", now))
    return {"id": identifier, "state": "open"}


def appeals(store, account: str) -> dict:
    with store.connection() as db:
        rows = db.execute("SELECT id, case_id, state, answer FROM appeals WHERE account=? ORDER BY created DESC LIMIT 100",
                          (account,)).fetchall()
    return {"appeals": [{"id": row[0], "case": str(row[1]), "state": row[2]} | ({"answer": row[3]} if row[3] else {})
                        for row in rows]}


# ---- operator views (command line only; no HTTP route) ----

def appeals_open(store) -> list[dict]:
    with store.connection() as db:
        return [{"id": row["id"], "case": str(row["case_id"]), "action": row["action"], "target": row["target"],
                 "reason": row["reason"], "from": row["namespace"] or _handle(db, row["account"]),
                 "created": row["created"], "text": row["text"]}
                for row in db.execute("SELECT appeals.*, accounts.namespace, log.action, log.target, log.reason FROM appeals "
                                      "JOIN accounts ON accounts.id=appeals.account JOIN moderation_log AS log "
                                      "ON log.id=appeals.case_id WHERE appeals.state='open' ORDER BY appeals.created").fetchall()]


def appeal_close(store, identifier: str, outcome: str) -> None:
    """Answer an appeal. Undoing the step itself is a team step (rating-restore, ratings-unfreeze, account-unban)."""
    require(outcome in APPEAL_ANSWERS, "close an appeal as upheld or rejected")
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        # A looked-at appeal keeps no text.
        changed = db.execute("UPDATE appeals SET state=?, answer=?, text='' WHERE id=? AND state='open'",
                             (outcome, APPEAL_ANSWERS[outcome], identifier)).rowcount
        require(changed == 1, "no open appeal with this id")


def held(store) -> list[dict]:
    """Ratings waiting for a person: new accounts' text, repeated text, a burst, or three reports."""
    with store.connection() as db:
        return [{"id": row["id"], "package_id": row["package_id"], "author": row["namespace"] or _handle(db, row["account"]),
                 "stars": row["stars"], "body": row["body"], "version": row["version"], "created": row["created"],
                 "reports": row["reports"], "same_text": row["same_text"]}
                for row in db.execute("""SELECT ratings.*, accounts.namespace,
                    (SELECT count(*) FROM rating_reports WHERE rating=ratings.id) AS reports,
                    (SELECT count(*) FROM ratings AS other WHERE other.text_sig=ratings.text_sig AND other.id<>ratings.id) AS same_text
                    FROM ratings JOIN accounts ON accounts.id=ratings.account WHERE state='held' ORDER BY ratings.created""").fetchall()]


def pending(store) -> dict:
    """Opaque ids and counts for the signer's public state; no text and no names."""
    with store.connection() as db:
        value = {"counts": {"ratings": db.execute("SELECT count(*) FROM ratings WHERE state='held'").fetchone()[0],
                            "appeals": db.execute("SELECT count(*) FROM appeals WHERE state='open'").fetchone()[0]},
                 "ratings": [row[0] for row in db.execute("SELECT id FROM ratings WHERE state='held' ORDER BY id LIMIT 1000")],
                 "appeals": [row[0] for row in db.execute("SELECT id FROM appeals WHERE state='open' ORDER BY id LIMIT 1000")]}
    return pending_summary(value)


def pending_summary(value) -> dict:
    require(isinstance(value, dict) and set(value) == {"counts", "ratings", "appeals"} and
            isinstance(value["counts"], dict) and set(value["counts"]) == {"ratings", "appeals"} and
            all(type(n) is int and 0 <= n <= 10**9 for n in value["counts"].values()), "invalid rating queue")
    for kind in ("ratings", "appeals"):
        require(isinstance(value[kind], list) and len(value[kind]) <= 1000 and
                all(isinstance(item, str) and _ID.fullmatch(item) for item in value[kind]), "invalid rating queue ids")
    return value


def prune(db, *, now: int) -> None:
    """Text the team removed is erased after 30 days, and a used pass is forgotten once it would have expired anyway."""
    limit = now - REMOVED_TEXT_DAYS * 86400
    db.execute("UPDATE ratings SET body='', text_sig=NULL WHERE state='voided' AND voided_at<=? AND body<>''", (limit,))
    db.execute("UPDATE ratings SET removed_body=NULL WHERE text_removed_at<=? AND removed_body IS NOT NULL", (limit,))
    db.execute("DELETE FROM review_passes_spent WHERE day<?", (now // 86400 - PASS_LIFE_DAYS - 1,))


# ---- the team's signed steps ----

# Each step: the words after the action, and which of them is the target.
STEPS = {
    "review-text-remove": ("rating", "reason"), "rating-void": ("rating", "reason"), "rating-restore": ("rating",),
    "ratings-void-account": ("account", "reason"), "account-ban": ("account", "reason"), "account-unban": ("account",),
    "ratings-void-window": ("package", "from", "to", "reason"),
    "ratings-freeze": ("package", "reason"), "ratings-unfreeze": ("package",),
}
FORMS = {
    "review-text-remove": "review-text-remove rating-id reason", "rating-void": "rating-void rating-id reason",
    "rating-restore": "rating-restore rating-id", "ratings-void-account": "ratings-void-account publisher-or-handle reason",
    "account-ban": "account-ban publisher-or-handle reason", "account-unban": "account-unban publisher-or-handle",
    "ratings-void-window": "ratings-void-window publisher/name 2026-01-31T00 2026-01-31T12 reason",
    "ratings-freeze": "ratings-freeze publisher/name reason", "ratings-unfreeze": "ratings-unfreeze publisher/name",
}
_GROUPS = {"review-text-remove": "rating", "rating-void": "rating", "rating-restore": "rating",
           "ratings-void-account": "void-account", "account-ban": "ban", "account-unban": "ban",
           "ratings-void-window": "window", "ratings-freeze": "freeze", "ratings-unfreeze": "freeze"}


def reason_code(value: object) -> str:
    require(isinstance(value, str) and value in REASONS,
            "the reason must be one of: " + ", ".join(REASONS))
    return value


def _package(value: object) -> bool:
    return isinstance(value, str) and len(value.split("/")) == 2 and all(IDENTITY.fullmatch(part) for part in value.split("/"))


def _hour(value: str) -> int:
    require(isinstance(value, str) and bool(_HOUR.fullmatch(value)), "write the hour as 2026-01-31T00 (UTC)")
    try:
        import calendar

        return calendar.timegm(time.strptime(value, "%Y-%m-%dT%H"))
    except ValueError:
        require(False, "write the hour as 2026-01-31T00 (UTC)")


def step_from_words(action: str, words: list[str]) -> dict:
    """One line of the team's steps file as the step that gets signed."""
    shape = STEPS[action]
    require(len(words) == len(shape), "write it as \"" + FORMS[action] + "\"")
    step = {"action": action, "target": words[0]} | dict(zip(shape[1:], words[1:], strict=True))
    validate_step(step)
    return step


def validate_step(item: dict) -> tuple[str, str]:
    """Refuse a step that is not exactly one of the forms above. Returns what it may not be repeated on."""
    action = item["action"]
    shape = STEPS[action]
    require(set(item) == {"action", "target", *shape[1:]} and all(isinstance(value, str) for value in item.values()),
            "invalid operator step")
    target = item["target"]
    require({"rating": bool(_ID.fullmatch(target)), "account": bool(IDENTITY.fullmatch(target)),
             "package": _package(target)}[shape[0]], "invalid target for " + action)
    if "reason" in shape:
        reason_code(item["reason"])
    if action == "ratings-void-window":
        require(_hour(item["from"]) < _hour(item["to"]), "the window must end after it starts")
        return _GROUPS[action], target + " " + item["from"] + " " + item["to"]
    return _GROUPS[action], target


def _log(db, item: dict, *, now: int, sequence: int, affected: dict[str, int]) -> int:
    case = db.execute("INSERT INTO moderation_log (sequence,step,action,target,reason,created,affected) VALUES (?,?,?,?,?,?,?)",
                      (sequence, digest(item), item["action"], item["target"], item.get("reason"), now,
                       sum(affected.values()))).lastrowid
    db.executemany("INSERT INTO moderation_notices VALUES (?,?,?)", [(case, name, count) for name, count in affected.items()])
    return case


def _void(db, where: str, args: tuple, item: dict, *, now: int, sequence: int) -> None:
    rows = db.execute("SELECT id, package_id FROM ratings WHERE state<>'voided' AND " + where, args).fetchall()
    affected: dict[str, int] = {}
    for row in rows:
        affected[row["package_id"]] = affected.get(row["package_id"], 0) + 1
    case = _log(db, item, now=now, sequence=sequence, affected=affected)
    db.executemany("UPDATE ratings SET state='voided', void_reason=?, voided_at=?, void_case=? WHERE id=?",
                   [(item["reason"], now, case, row["id"]) for row in rows])


def apply_step(db, item: dict, *, now: int, sequence: int) -> None:
    """One team step, inside the transaction of the marks that carry it; a step that fits nothing stops them all."""
    action, target = item["action"], item["target"]
    if action in {"rating-void", "review-text-remove", "rating-restore"}:
        row = db.execute("SELECT * FROM ratings WHERE id=?", (target,)).fetchone()
        require(row, "rating not found")
        if action == "rating-void":
            require(row["state"] != "voided", "that rating is already removed")
            _void(db, "id=?", (target,), item, now=now, sequence=sequence)
        elif action == "review-text-remove":
            require(row["body"] != "", "that rating has no text")
            case = _log(db, item, now=now, sequence=sequence, affected={row["package_id"]: 1})
            # The stars stay; the words go. They are kept 30 days in case the person asks for another look.
            db.execute("UPDATE ratings SET removed_body=body, body='', text_sig=NULL, text_removed_at=?, text_case=? WHERE id=?",
                       (now, case, target))
        else:
            require(row["state"] in {"voided", "held"} or row["text_removed_at"], "that rating is not removed or held")
            _log(db, item, now=now, sequence=sequence, affected={row["package_id"]: 1})
            body = row["removed_body"] if row["text_removed_at"] and row["removed_body"] else row["body"]
            db.execute("DELETE FROM rating_reports WHERE rating=?", (target,))
            db.execute("UPDATE ratings SET state='published', body=?, text_sig=?, void_reason=NULL, voided_at=NULL, void_case=NULL, "
                       "removed_body=NULL, text_removed_at=NULL, text_case=NULL WHERE id=?",
                       (body, text_sig(body) if body else None, target))
    elif action == "ratings-void-window":
        _published(db, target)
        _void(db, "package_id=? AND created>=? AND created<?", (target, _hour(item["from"]), _hour(item["to"])), item,
              now=now, sequence=sequence)
    elif action in {"ratings-freeze", "ratings-unfreeze"}:
        _published(db, target)
        if action == "ratings-freeze":
            db.execute("INSERT OR REPLACE INTO rating_freezes VALUES (?,?,?)", (target, item["reason"], now))
        else:
            require(db.execute("DELETE FROM rating_freezes WHERE package_id=?", (target,)).rowcount, "ratings are not closed for that add-on")
        _log(db, item, now=now, sequence=sequence, affected={target: 0})
    else:
        account = _account_for(db, target)
        if action == "ratings-void-account":
            _void(db, "account=?", (account,), item, now=now, sequence=sequence)
            return
        affected = dict(db.execute("SELECT package_id, 1 FROM ratings WHERE account=? AND state<>'voided'", (account,)).fetchall())
        case = _log(db, item, now=now, sequence=sequence, affected=affected)
        if action == "account-ban":
            # As a mute, and more: no new ratings, the ones there read as removed, reports and stars stop counting.
            db.execute("INSERT OR REPLACE INTO banned_accounts VALUES (?,?,?,?)", (account, item["reason"], now, case))
            db.execute("UPDATE comments SET state='held' WHERE account=? AND state='published'", (account,))
            db.execute("DELETE FROM stars WHERE account=?", (account,))
        else:
            require(db.execute("DELETE FROM banned_accounts WHERE account=?", (account,)).rowcount, "that account is not banned")


def marks_changed(db, before: dict, after: dict, *, now: int) -> None:
    """Delisting and removal are lists in the signed marks; each change to them is a row in the record too."""
    for field, action, undone in (("delisted", "delisted", "relisted"), ("withdrawals", "removed", None)):
        old = {item["id"]: item["reason"] for item in before.get(field, [])}
        new = {item["id"]: item["reason"] for item in after.get(field, [])}
        for name in sorted(set(new) - set(old)):
            _log(db, {"action": action, "target": name, "reason": new[name]}, now=now, sequence=after["sequence"],
                 affected={name: 0})
        for name in sorted(set(old) - set(new)) if undone else []:
            _log(db, {"action": undone, "target": name}, now=now, sequence=after["sequence"], affected={name: 0})


def listed_reasons(value: object, field: str) -> list:
    """`delisted` and `withdrawals` in the marks: each add-on once, each with a reason from the fixed list."""
    require(isinstance(value, list) and len(value) <= 4096 and all(
        isinstance(item, dict) and set(item) == {"id", "reason"} and _package(item["id"]) and item["reason"] in REASONS
        for item in value) and len({item["id"] for item in value}) == len(value), "invalid " + field)
    return value


def delisted(marks: dict) -> dict[str, str]:
    return {item["id"]: item["reason"] for item in marks.get("delisted", [])}


DELISTED = "this add-on is no longer listed"
