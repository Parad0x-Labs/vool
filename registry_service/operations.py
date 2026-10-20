"""Signed operator actions share one transaction with the marks that requested them."""
from __future__ import annotations

import re

from core.registry.codec import HEX, IDENTITY, digest, require

COMMENT = {"comment-publish", "comment-remove"}
ACCOUNT = {"mute", "unmute"}
PACKAGE = {"reports-close", "skill-pause", "skill-resume"}
ACTIONS = COMMENT | ACCOUNT | PACKAGE | {"reporter-mute"}
_ID = re.compile(r"[0-9a-f]{32}")


def validate(value) -> list[dict]:
    require(isinstance(value, list) and len(value) <= 1000, "invalid operator steps")
    seen = set()
    for item in value:
        require(isinstance(item, dict) and set(item) == {"action", "target"} and
                item["action"] in ACTIONS and isinstance(item["target"], str), "invalid operator step")
        action, target = item["action"], item["target"]
        if action in COMMENT | {"reporter-mute"}:
            valid = bool(_ID.fullmatch(target))
            group = "comment" if action in COMMENT else "reporter"
        elif action in ACCOUNT:
            valid, group = bool(IDENTITY.fullmatch(target)), "account"
        else:
            parts = target.split("/")
            valid, group = len(parts) == 2 and all(IDENTITY.fullmatch(part) for part in parts), "package"
        require(valid, "invalid target for " + action)
        key = (group, target)
        require(key not in seen, "conflicting or repeated operator steps for " + target)
        seen.add(key)
    return value


def _close(db, package_id):
    db.execute("UPDATE skill_reports SET state='closed', note='' WHERE package_id=? AND state='open'", (package_id,))


def apply(db, value: list[dict], *, now: int) -> None:
    """Caller owns BEGIN/commit. Retries apply once; removing then re-adding a line applies it again."""
    validate(value)
    db.execute("CREATE TABLE IF NOT EXISTS authority_actions (digest TEXT PRIMARY KEY)")
    previous = {row[0] for row in db.execute("SELECT digest FROM authority_actions")}
    wanted = {digest(item) for item in value}
    for item in value:
        signature = digest(item)
        if signature in previous:
            continue
        action, target = item["action"], item["target"]
        if action in COMMENT:
            row = db.execute("SELECT state FROM comments WHERE id=?", (target,)).fetchone()
            require(row and row[0] in {"held", "published"}, "comment not found")
            if action == "comment-publish":
                db.execute("DELETE FROM comment_reports WHERE comment=?", (target,))
                db.execute("UPDATE comments SET state='published' WHERE id=?", (target,))
            else:
                db.execute("UPDATE comments SET state='removed', body='' WHERE id=?", (target,))
        elif action in ACCOUNT | {"reporter-mute"}:
            query = "SELECT account FROM skill_reports WHERE id=?" if action == "reporter-mute" else \
                "SELECT id FROM accounts WHERE namespace=?"
            row = db.execute(query, (target,)).fetchone()
            require(row and row[0], "report not found" if action == "reporter-mute" else "account not found")
            if action == "unmute":
                db.execute("DELETE FROM muted_accounts WHERE account=?", (row[0],))
            else:
                db.execute("INSERT OR IGNORE INTO muted_accounts VALUES (?,?)", (row[0], now))
                db.execute("UPDATE comments SET state='held' WHERE account=? AND state='published'", (row[0],))
                if action == "reporter-mute":
                    db.execute("UPDATE skill_reports SET counted=0 WHERE account=? AND state='open'", (row[0],))
        else:
            require(db.execute("SELECT 1 FROM public_versions WHERE package_id=?", (target,)).fetchone(),
                    "package not published")
            _close(db, target)
            if action == "skill-pause":
                db.execute("INSERT OR REPLACE INTO paused_packages VALUES (?,?,?)", (target, now, "operator"))
            elif action == "skill-resume":
                db.execute("DELETE FROM paused_packages WHERE package_id=?", (target,))
            else:
                db.execute("DELETE FROM paused_packages WHERE package_id=? AND cause='reports'", (target,))
        db.execute("INSERT INTO authority_actions VALUES (?)", (signature,))
    for signature in previous - wanted:
        require(bool(HEX.fullmatch(signature)), "invalid saved operator step")
        db.execute("DELETE FROM authority_actions WHERE digest=?", (signature,))


def pending(store) -> dict:
    """Public-safe opaque IDs and counts; private text and reporter identities never leave the store."""
    with store.connection() as db:
        comments = [row[0] for row in db.execute("SELECT id FROM comments WHERE state='held' ORDER BY id LIMIT 1000")]
        reports = [row[0] for row in db.execute("SELECT id FROM skill_reports WHERE state='open' ORDER BY id LIMIT 1000")]
        counts = {"comments": db.execute("SELECT count(*) FROM comments WHERE state='held'").fetchone()[0],
                  "reports": db.execute("SELECT count(*) FROM skill_reports WHERE state='open'").fetchone()[0],
                  "paused": db.execute("SELECT count(*) FROM paused_packages").fetchone()[0]}
    from registry_service.curation import load_curation, picture_approvals

    marks = store.marks()
    approvals = picture_approvals(marks.get("curation", load_curation()))
    with store.connection() as db:
        artifacts = {(row[0], row[1]): row[2] for row in db.execute("SELECT package_id,version,sha256 FROM public_versions")}
    pictures = [{**{key: item[key] for key in ("id", "version", "kind", "sha256")},
                 "artifact_sha256": artifacts[(item["id"], item["version"])]}
                for item in store.published_pictures() if (item["id"], item["sha256"]) not in approvals and
                item["id"] + "@" + artifacts[(item["id"], item["version"])] not in marks["tested"]]
    counts["pictures"] = len(pictures)
    return summary({"counts": counts, "comments": comments, "reports": reports, "pictures": pictures[:1000]})


def summary(value) -> dict:
    require(isinstance(value, dict) and set(value) == {"counts", "comments", "reports", "pictures"},
            "invalid moderation summary")
    counts = value["counts"]
    require(isinstance(counts, dict) and set(counts) == {"comments", "reports", "paused", "pictures"} and
            all(type(n) is int and 0 <= n <= 10**9 for n in counts.values()), "invalid moderation counts")
    for kind in ("comments", "reports"):
        require(isinstance(value[kind], list) and len(value[kind]) <= 1000 and
                all(isinstance(item, str) and _ID.fullmatch(item) for item in value[kind]), "invalid moderation ids")
    require(isinstance(value["pictures"], list) and len(value["pictures"]) <= 1000, "invalid pending pictures")
    for item in value["pictures"]:
        from core.registry.codec import VERSION

        require(isinstance(item, dict) and set(item) == {"id", "version", "kind", "sha256", "artifact_sha256"} and
                item["kind"] in {"icon", "banner"} and all(isinstance(item[field], str) and HEX.fullmatch(item[field])
                for field in ("sha256", "artifact_sha256")) and isinstance(item["id"], str) and
                len(item["id"].split("/")) == 2 and all(IDENTITY.fullmatch(part) for part in item["id"].split("/")) and
                isinstance(item["version"], str) and VERSION.fullmatch(item["version"]), "invalid pending picture")
    return value
