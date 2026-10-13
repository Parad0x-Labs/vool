"""Private accounts and submission state; GitHub remains the public content authority.

The schema has no password, email, access-token, IP or user-agent columns. Scanner
output is private until an independently signed admission is published.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import re
import secrets
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlsplit

from core.registry.artifact import inspect
from core.registry.codec import HEX, IDENTITY, RegistryError, canonical, decode, require
from core.registry.intake import secret_findings


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS accounts (id TEXT PRIMARY KEY, namespace TEXT UNIQUE,
                    profile BLOB NOT NULL, created INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS namespace_reservations (namespace TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS namespace_owners (namespace TEXT PRIMARY KEY,
                    account TEXT REFERENCES accounts(id) ON DELETE CASCADE, verified INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS publisher_names (namespace TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS identities (provider TEXT, subject TEXT,
                    account TEXT REFERENCES accounts(id) ON DELETE CASCADE, PRIMARY KEY(provider,subject));
                CREATE TABLE IF NOT EXISTS sessions (hash TEXT PRIMARY KEY, account TEXT REFERENCES accounts(id)
                    ON DELETE CASCADE, csrf TEXT NOT NULL, expires INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS oauth (hash TEXT PRIMARY KEY, provider TEXT, verifier TEXT,
                    nonce TEXT, browser TEXT, expires INTEGER, link_account TEXT);
                CREATE INDEX IF NOT EXISTS oauth_browser_provider ON oauth(browser,provider);
                CREATE INDEX IF NOT EXISTS oauth_expiry ON oauth(expires);
                CREATE TABLE IF NOT EXISTS submissions (id TEXT PRIMARY KEY, account TEXT REFERENCES accounts(id)
                    ON DELETE CASCADE, created INTEGER, package_id TEXT, version TEXT, sha256 TEXT,
                    artifact BLOB, state TEXT, findings BLOB, consent INTEGER DEFAULT 0,
                    review BLOB, github_url TEXT);
                CREATE TABLE IF NOT EXISTS public_versions (package_id TEXT, version TEXT, sha256 TEXT UNIQUE,
                    entry BLOB, artifact BLOB, published INTEGER, inspection BLOB, PRIMARY KEY(package_id,version));
                CREATE TABLE IF NOT EXISTS stars (account TEXT REFERENCES accounts(id) ON DELETE CASCADE,
                    package_id TEXT, created INTEGER, PRIMARY KEY(account,package_id));
                CREATE TABLE IF NOT EXISTS download_tickets (id TEXT PRIMARY KEY, sha256 TEXT,
                    expires INTEGER, served INTEGER DEFAULT 0);
                CREATE TABLE IF NOT EXISTS download_counts (package_id TEXT PRIMARY KEY, requests INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS service_keys (name TEXT PRIMARY KEY, value BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS upload_leases (id TEXT PRIMARY KEY,
                    account TEXT REFERENCES accounts(id) ON DELETE CASCADE, namespace TEXT NOT NULL, expires INTEGER NOT NULL);
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(public_versions)")}
            if "inspection" not in columns:
                db.execute("ALTER TABLE public_versions ADD COLUMN inspection BLOB")
            columns = {row[1] for row in db.execute("PRAGMA table_info(submissions)")}
            if "publisher_bound" not in columns:
                db.execute("ALTER TABLE submissions ADD COLUMN publisher_bound INTEGER NOT NULL DEFAULT 0")
            # A public name survives private account deletion without retaining
            # provider/account identifiers. Legacy state must be unambiguous.
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR IGNORE INTO service_keys VALUES ('download-hmac',?)", (secrets.token_bytes(32),))
            self._ticket_key = db.execute("SELECT value FROM service_keys WHERE name='download-hmac'").fetchone()[0]
            require(isinstance(self._ticket_key, bytes) and len(self._ticket_key) == 32, "invalid private download key")
            namespaces = {row[0] for row in db.execute("SELECT namespace FROM accounts WHERE namespace IS NOT NULL")}
            for row in db.execute("SELECT package_id,version,entry FROM public_versions"):
                entry = decode(row["entry"])
                info = entry.get("metadata") if isinstance(entry, dict) else None
                require(isinstance(info, dict) and info.get("id") == row["package_id"] and
                        info.get("version") == row["version"] and isinstance(row["package_id"], str) and
                        len(row["package_id"].split("/")) == 2 and
                        all(IDENTITY.fullmatch(part) for part in row["package_id"].split("/")),
                        "published namespace migration requires valid matching metadata")
                namespaces.add(row["package_id"].split("/")[0])
                db.execute("INSERT OR IGNORE INTO publisher_names VALUES (?)", (row["package_id"].split("/")[0],))
            for namespace in namespaces:
                require(isinstance(namespace, str) and bool(IDENTITY.fullmatch(namespace)),
                        "namespace migration requires valid names")
                db.execute("INSERT OR IGNORE INTO namespace_reservations VALUES (?)", (namespace,))
            # Legacy account handles are not proof of a GitHub publisher link.
            db.execute("""INSERT OR IGNORE INTO namespace_owners(namespace,account)
                SELECT namespace,id FROM accounts WHERE namespace IS NOT NULL
                AND namespace NOT IN (SELECT namespace FROM publisher_names)""")
        path.chmod(0o600)

    @contextlib.contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA secure_delete=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def identity(self, provider: str, subject: str, *, now: int, link_account: str = "") -> str:
        require(provider in {"github", "google", "apple"} and isinstance(subject, str) and
                0 < len(subject) <= 255, "invalid verified identity")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT account FROM identities WHERE provider=? AND subject=?", (provider, subject)).fetchone()
            if row:
                require(not link_account or row[0] == link_account, "identity belongs to another account")
                return row[0]
            account = link_account or secrets.token_hex(16)
            if link_account:
                require(db.execute("SELECT 1 FROM accounts WHERE id=?", (account,)).fetchone(), "account disappeared")
            else:
                db.execute("INSERT INTO accounts VALUES (?, NULL, ?, ?)", (account, canonical({"handles": {}, "bio": ""}), now))
            db.execute("INSERT INTO identities VALUES (?,?,?)", (provider, subject, account))
            return account

    def profile(self, account: str, value: dict | None = None) -> dict:
        with self.connection() as db:
            if value is not None:
                db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM accounts WHERE id=?", (account,)).fetchone()
            require(row, "account not found")
            if value is not None:
                require(isinstance(value, dict) and set(value) == {"namespace", "bio", "handles"}, "invalid profile fields")
                namespace = value["namespace"]
                require(isinstance(namespace, str) and bool(IDENTITY.fullmatch(namespace)) and
                        namespace not in {"vool", "parad0x-labs", "official", "native"}, "invalid or reserved namespace")
                require(row["namespace"] in (None, namespace), "published namespace is stable; transfer requires maintainer review")
                require(isinstance(value["bio"], str) and len(value["bio"]) <= 500, "bio exceeds limit")
                handles = value["handles"]
                require(isinstance(handles, dict) and set(handles) <= {"github", "x", "mastodon", "website"} and
                        all(isinstance(text, str) and len(text) <= 200 and all(ord(c) >= 32 for c in text)
                            for text in handles.values()), "invalid public handles")
                for kind, text in handles.items():
                    if kind in {"github", "x"}:
                        require(bool(re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", text)), "invalid social handle")
                    else:
                        url = urlsplit(text)
                        require(url.scheme == "https" and url.hostname and not url.username and not url.password,
                                "public links must be HTTPS and contain no credentials")
                try:
                    if row["namespace"] is None:
                        db.execute("INSERT INTO namespace_reservations VALUES (?)", (namespace,))
                        db.execute("INSERT INTO namespace_owners(namespace,account) VALUES (?,?)", (namespace, account))
                    db.execute("UPDATE accounts SET namespace=?, profile=? WHERE id=?", (namespace, canonical(value), account))
                except sqlite3.IntegrityError:
                    raise RegistryError("namespace already reserved; transfer requires maintainer review") from None
                row = db.execute("SELECT * FROM accounts WHERE id=?", (account,)).fetchone()
            return {"id": account, "namespace": row["namespace"], **decode(row["profile"])}

    def public_profile(self, namespace: str) -> dict:
        with self.connection() as db:
            row = db.execute("""SELECT profile FROM accounts JOIN namespace_owners AS owner
                ON owner.account=accounts.id AND owner.namespace=accounts.namespace
                WHERE owner.namespace=? AND (owner.verified=1 OR NOT EXISTS
                (SELECT 1 FROM publisher_names WHERE namespace=owner.namespace))""", (namespace,)).fetchone()
        return {"namespace": namespace, **decode(row[0])} if row else {"namespace": namespace, "bio": "", "handles": {}}

    def reserve_publication(self, package_id: str) -> None:
        """Operator-only pending/direct publisher reservation; no author HTTP route."""
        parts = package_id.split("/") if isinstance(package_id, str) else []
        require(len(parts) == 2 and all(IDENTITY.fullmatch(part) for part in parts), "invalid publisher package id")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR IGNORE INTO namespace_reservations VALUES (?)", (parts[0],))
            db.execute("INSERT OR IGNORE INTO publisher_names VALUES (?)", (parts[0],))

    @staticmethod
    def _upload_namespace(db, account: str) -> str:
        row = db.execute("""SELECT accounts.namespace FROM accounts JOIN namespace_owners AS owner
            ON owner.account=accounts.id AND owner.namespace=accounts.namespace WHERE accounts.id=?
            AND (owner.verified=1 OR NOT EXISTS
            (SELECT 1 FROM publisher_names WHERE namespace=owner.namespace))""", (account,)).fetchone()
        require(row and row[0], "namespace required; known publishers need a verified account association")
        return row[0]

    def session(self, account: str, *, now: int) -> tuple[str, str]:
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.connection() as db:
            db.execute("DELETE FROM sessions WHERE expires<=?", (now,))
            db.execute("INSERT INTO sessions VALUES (?,?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), account, csrf, now + 86400))
        return token, csrf

    def authenticate(self, token: str, *, now: int, csrf: str | None = None) -> str:
        require(isinstance(token, str) and 0 < len(token) <= 128, "authentication required")
        with self.connection() as db:
            row = db.execute("SELECT * FROM sessions WHERE hash=? AND expires>?", (hashlib.sha256(token.encode()).hexdigest(), now)).fetchone()
        require(row, "session expired or unknown")
        require(csrf is None or secrets.compare_digest(row["csrf"], csrf), "CSRF token mismatch")
        return row["account"]

    def logout(self, token: str) -> None:
        with self.connection() as db:
            db.execute("DELETE FROM sessions WHERE hash=?", (hashlib.sha256(token.encode()).hexdigest(),))

    def delete_account(self, account: str) -> None:
        # Public Git history and licensed publications cannot be erased by this
        # private account operation. Delete credentials, stars and quarantine.
        with self.connection() as db:
            db.execute("DELETE FROM accounts WHERE id=?", (account,))
            db.execute("DELETE FROM oauth WHERE link_account=?", (account,))

    @contextlib.contextmanager
    def upload_slot(self, account: str, *, now: int):
        """Reserve quota and one of four shared slots before reading or parsing."""
        token = secrets.token_hex(16)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM upload_leases WHERE expires<=?", (now,))
            namespace = self._upload_namespace(db, account)
            pending = db.execute("SELECT count(*) FROM upload_leases WHERE account=?", (account,)).fetchone()[0]
            count = db.execute("SELECT count(*) FROM submissions WHERE account=? AND created>?", (account, now - 86400)).fetchone()[0]
            require(count + pending < 10, "daily submission quota reached")
            require(pending == 0, "concurrent upload already active for this account")
            require(db.execute("SELECT count(*) FROM upload_leases").fetchone()[0] < 4, "concurrent upload capacity reached")
            db.execute("INSERT INTO upload_leases VALUES (?,?,?,?)", (token, account, namespace, now + 300))
        try:
            yield token
        finally:
            with self.connection() as db:
                db.execute("DELETE FROM upload_leases WHERE id=? AND account=?", (token, account))

    def _check_upload_slot(self, db, account: str, token: str, now: int) -> str:
        namespace = self._upload_namespace(db, account)
        row = db.execute("SELECT namespace FROM upload_leases WHERE id=? AND account=? AND expires>?",
                         (token, account, now)).fetchone()
        require(row and row[0] == namespace, "upload reservation expired or ownership changed")
        return namespace

    def upload(self, account: str, raw: bytes, *, now: int, _slot: str | None = None) -> dict:
        if _slot is None:
            with self.upload_slot(account, now=now) as slot:
                return self.upload(account, raw, now=now, _slot=slot)
        started = time.monotonic()
        with self.connection() as db:
            namespace = self._check_upload_slot(db, account, _slot, now)
        artifact = inspect(raw)
        require(namespace == artifact["metadata"]["id"].split("/")[0], "package publisher must match your namespace")
        findings = secret_findings(artifact["files"])
        identifier = secrets.token_hex(16)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            completed = now + int(time.monotonic() - started)
            self._check_upload_slot(db, account, _slot, completed)
            count = db.execute("SELECT count(*) FROM submissions WHERE account=? AND created>?", (account, completed - 86400)).fetchone()[0]
            require(count < 10, "daily submission quota reached")
            db.execute("INSERT INTO submissions (id,account,created,package_id,version,sha256,artifact,state,findings) VALUES (?,?,?,?,?,?,?,?,?)",
                       (identifier, account, completed, artifact["metadata"]["id"], artifact["metadata"]["version"], artifact["sha256"],
                        None if findings else raw, "rejected" if findings else "quarantined", canonical(findings)))
        return self.submission(account, identifier)

    def draft(self, account: str, value: dict, *, now: int, _slot: str | None = None) -> dict:
        """Simple website SKILL.md + licence intake; GitHub provenance is bound later."""
        if _slot is None:
            with self.upload_slot(account, now=now) as slot:
                return self.draft(account, value, now=now, _slot=slot)
        started = time.monotonic()
        with self.connection() as db:
            namespace = self._check_upload_slot(db, account, _slot, now)
        from core.plugin_package import execution_directory
        from core.registry.artifact import pack
        from tools.community_skills import initialize, preflight

        require(isinstance(value, dict) and set(value) == {"metadata", "skill_markdown", "license_text"}, "invalid skill draft")
        info = dict(value["metadata"])
        require("source" not in info and info.get("kind") == "skill", "draft source is assigned by the server")
        info["source"] = {"upload_id": secrets.token_hex(16), "namespace": namespace}
        require(all(isinstance(value[field], str) and 0 < len(value[field]) <= 32768
                    for field in ("skill_markdown", "license_text")), "draft text exceeds limit")
        scratch = self.path.parent / "scratch"
        with execution_directory(scratch) as owned:
            source = owned / "author"
            initialize(source, info, value["license_text"].encode("utf-8"))
            name = info["id"].split("/")[1]
            (source / "skills" / name / "SKILL.md").write_text(value["skill_markdown"], encoding="utf-8")
            raw = pack(source, info, scratch)
            local = preflight(raw, scratch)
        result = self.upload(account, raw, now=now + int(time.monotonic() - started), _slot=_slot)
        if local["findings"] and result["state"] != "rejected":
            with self.connection() as db:
                db.execute("UPDATE submissions SET state='held', findings=? WHERE id=?", (canonical(local["findings"]), result["id"]))
        # This marker is assigned only to bytes constructed by this service,
        # never to an author-supplied artifact or self-declared social handle.
        with self.connection() as db:
            db.execute("UPDATE submissions SET publisher_bound=1 WHERE id=? AND account=?", (result["id"], account))
        return self.submission(account, result["id"])

    def submission(self, account: str, identifier: str) -> dict:
        with self.connection() as db:
            row = db.execute("SELECT * FROM submissions WHERE id=? AND account=?", (identifier, account)).fetchone()
        require(row, "submission not found")
        return {key: row[key] for key in ("id", "created", "package_id", "version", "sha256", "state", "consent", "github_url")} | {
            "findings": decode(row["findings"])}

    def submissions(self, account: str) -> list[dict]:
        with self.connection() as db:
            ids = [row[0] for row in db.execute("SELECT id FROM submissions WHERE account=? ORDER BY created DESC LIMIT 100", (account,))]
        return [self.submission(account, identifier) for identifier in ids]

    def review(self, identifier: str, envelope: dict, policy: dict, root: dict, *, now: int) -> None:
        """Worker-only method, never an author HTTP route. Requires threshold review signatures."""
        from core.registry.admission import admit, prepublication
        from core.registry.trust import root_document, verify

        root_document(root, now)
        evidence = verify(envelope, root, "review")
        require(evidence["policy_sha256"] in root["policies"], "review uses an untrusted policy")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM submissions WHERE id=?", (identifier,)).fetchone()
            require(row and row["artifact"] and row["state"] in {"quarantined", "held", "preflighted", "reviewed", "ready_for_github"}, "submission cannot be reviewed")
            artifact = inspect(row["artifact"])
            try:
                if evidence.get("schema") == "vool.registry.prepublication.v1":
                    prepublication(artifact, policy, evidence, now=now)
                    state = "preflighted"
                else:
                    admit(artifact, policy, evidence, now=now)
                    state = "reviewed"
            except RegistryError as exc:
                secrets_found = any(isinstance(check, dict) and check.get("name") == "secrets" and check.get("findings")
                                    for check in evidence.get("checks", []))
                db.execute("UPDATE submissions SET state=?, findings=?, artifact=CASE WHEN ? THEN NULL ELSE artifact END WHERE id=?", (
                    "rejected" if secrets_found else "held", canonical([
                    {"check": "admission", "reason": str(exc)}]), bool(secrets_found), identifier))
                return
            db.execute("UPDATE submissions SET state=?, findings=?, review=? WHERE id=?",
                       (state, canonical([]), canonical(envelope), identifier))

    def consent(self, account: str, identifier: str, sha256: str, *, public: bool) -> dict:
        require(public is True, "explicit consent to permanent public GitHub history is required")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM submissions WHERE id=? AND account=?", (identifier, account)).fetchone()
            require(row and row["sha256"] == sha256 and row["state"] in {"preflighted", "reviewed"}, "exact bytes must pass review before public consent")
            db.execute("UPDATE submissions SET consent=1, state='ready_for_github' WHERE id=?", (identifier,))
        return self.submission(account, identifier)

    def export_submission(self, identifier: str, policy: dict, root: dict, *, now: int) -> tuple[bytes, dict]:
        """Offline GitHub publisher outbox; no author credentials or untrusted code execution."""
        with self.connection() as db:
            row = db.execute("SELECT * FROM submissions WHERE id=? AND state='ready_for_github' AND consent=1", (identifier,)).fetchone()
        require(row, "submission is not approved for public export")
        from core.registry.admission import admit, prepublication
        from core.registry.trust import root_document, verify

        root_document(root, now)
        review = decode(row["review"])
        evidence = verify(review, root, "review")
        require(evidence["policy_sha256"] in root["policies"], "untrusted export policy")
        if evidence.get("schema") == "vool.registry.prepublication.v1":
            prepublication(inspect(row["artifact"]), policy, evidence, now=now)
        else:
            admit(inspect(row["artifact"]), policy, evidence, now=now)
        # Verification runs outside the write lock. Re-read current consent,
        # ownership and exact bytes under one transaction before handing out
        # the snapshot. This cannot revoke a file already handed to a publisher.
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("""SELECT submissions.* FROM submissions
                JOIN accounts ON accounts.id=submissions.account
                WHERE submissions.id=? AND state='ready_for_github' AND consent=1""", (identifier,)).fetchone()
            require(current and all(current[field] == row[field] for field in
                    ("account", "package_id", "version", "sha256", "artifact", "review")),
                    "submission changed during export; recheck review and consent")
            return current["artifact"], review

    def publish(self, catalog, package_id: str, version: str, target: dict, raw: bytes, *, now: int) -> None:
        """Called only after TrustedCatalog verifies the signed GitHub catalog snapshot."""
        entry = catalog.resolve(package_id, version, target=target, now=now)
        artifact = inspect(raw)
        require(artifact["sha256"] == entry["artifact_sha256"] and len(raw) == entry["artifact_length"] and
                artifact["native_digest"] == entry["native_digest"] and artifact["metadata"] == entry["metadata"], "published artifact differs from admission")
        package_id, version = entry["metadata"]["id"], entry["metadata"]["version"]
        from core.registry.rules import analyze, notices

        inspection = analyze(artifact["files"])
        inspection["native_control_overlaps"] = notices(
            set(entry["native_rule_review"]["overlaps"]) | {item["control"] for item in inspection["native_control_overlaps"]})
        require(not inspection["conflicts"], "native control override instructions block publication")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT sha256 FROM public_versions WHERE package_id=? AND version=?", (package_id, version)).fetchone()
            require(not previous or previous[0] == artifact["sha256"], "published versions are immutable")
            db.execute("INSERT OR IGNORE INTO namespace_reservations VALUES (?)", (package_id.split("/")[0],))
            namespace = package_id.split("/")[0]
            db.execute("INSERT OR IGNORE INTO publisher_names VALUES (?)", (namespace,))
            bound = db.execute("""SELECT submissions.account FROM submissions JOIN accounts
                ON accounts.id=submissions.account WHERE sha256=? AND artifact=? AND publisher_bound=1
                AND consent=1 AND state IN ('ready_for_github','published') AND accounts.namespace=?""",
                (artifact["sha256"], raw, namespace)).fetchone() if "upload_id" in artifact["metadata"]["source"] else None
            if bound:
                db.execute("UPDATE namespace_owners SET verified=1 WHERE namespace=? AND account=?", (namespace, bound[0]))
            else:
                # A GitHub import cannot turn an earlier website claimant into
                # its publisher. Verified external account linking is pending.
                db.execute("UPDATE namespace_owners SET verified=0 WHERE namespace=?", (namespace,))
            db.execute("INSERT OR IGNORE INTO public_versions (package_id,version,sha256,entry,artifact,published,inspection) VALUES (?,?,?,?,?,?,?)", (package_id, version,
                artifact["sha256"], canonical(entry), raw, now, canonical(inspection)))
            db.execute("UPDATE public_versions SET inspection=? WHERE package_id=? AND version=?", (canonical(inspection), package_id, version))
            db.execute("UPDATE submissions SET state='published' WHERE sha256=? AND consent=1", (artifact["sha256"],))

    def public_artifact(self, sha256: str) -> tuple[bytes, dict]:
        with self.connection() as db:
            row = db.execute("SELECT artifact,entry FROM public_versions WHERE sha256=?", (sha256,)).fetchone()
        require(row, "approved artifact not found")
        return row[0], decode(row[1])

    def public_inspection(self, sha256: str) -> dict:
        with self.connection() as db:
            row = db.execute("SELECT inspection FROM public_versions WHERE sha256=?", (sha256,)).fetchone()
        require(row and row[0], "approved artifact inspection not found")
        return decode(row[0])

    def star(self, account: str, package_id: str, enabled: bool, *, now: int) -> dict:
        require(type(enabled) is bool, "star value must be boolean")
        with self.connection() as db:
            require(db.execute("SELECT 1 FROM public_versions WHERE package_id=?", (package_id,)).fetchone(), "package not published")
            if enabled:
                db.execute("INSERT OR IGNORE INTO stars VALUES (?,?,?)", (account, package_id, now))
            else:
                db.execute("DELETE FROM stars WHERE account=? AND package_id=?", (account, package_id))
        return self.stats(package_id)

    def stats(self, package_id: str) -> dict:
        with self.connection() as db:
            stars = db.execute("SELECT count(*) FROM stars WHERE package_id=?", (package_id,)).fetchone()[0]
            row = db.execute("SELECT requests FROM download_counts WHERE package_id=?", (package_id,)).fetchone()
        return {"stars": stars, "download_requests": row[0] if row else 0}

    def ticket(self, sha256: str, *, now: int) -> str:
        require(isinstance(sha256, str) and bool(HEX.fullmatch(sha256)), "invalid artifact checksum")
        with self.connection() as db:
            require(db.execute("SELECT 1 FROM public_versions WHERE sha256=?", (sha256,)).fetchone(), "approved artifact not found")
        # Fixed binary encoding fits the native client's bounded ticket field:
        # checksum (32), expiry (8), nonce (16), HMAC-SHA256 (32). No issue row.
        payload = bytes.fromhex(sha256) + (now + 900).to_bytes(8, "big") + secrets.token_bytes(16)
        return base64.urlsafe_b64encode(payload + hmac.digest(self._ticket_key, payload, "sha256")).decode().rstrip("=")

    def download(self, sha256: str, ticket: str, *, now: int) -> bytes:
        require(isinstance(ticket, str) and bool(re.fullmatch(r"[A-Za-z0-9_-]{118}", ticket)), "invalid download ticket")
        token = base64.b64decode(ticket + "==", altchars=b"-_", validate=True)
        require(len(token) == 88 and base64.urlsafe_b64encode(token).decode().rstrip("=") == ticket and
                hmac.compare_digest(token[56:], hmac.digest(self._ticket_key, token[:56], "sha256")), "invalid download ticket signature")
        expires = int.from_bytes(token[32:40], "big")
        require(token[:32].hex() == sha256 and now < expires <= now + 900, "download ticket expired or mismatched")
        raw, entry = self.public_artifact(sha256)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM download_tickets WHERE expires<=?", (now,))
            served = db.execute("INSERT OR IGNORE INTO download_tickets(id,sha256,expires,served) VALUES (?,?,?,1)",
                (hashlib.sha256(token).hexdigest(), sha256, expires))
            if served.rowcount:
                db.execute("INSERT INTO download_counts VALUES (?,1) ON CONFLICT(package_id) DO UPDATE SET requests=requests+1", (entry["metadata"]["id"],))
        return raw

    def prune(self, *, now: int) -> None:
        with self.connection() as db:
            db.execute("DELETE FROM sessions WHERE expires<=?", (now,))
            db.execute("DELETE FROM oauth WHERE expires<=?", (now,))
            db.execute("DELETE FROM download_tickets WHERE expires<=?", (now,))
            db.execute("UPDATE submissions SET artifact=NULL, review=NULL, state='expired' WHERE created<? AND state NOT IN ('published','rejected','expired')", (now - 7 * 86400,))
            db.execute("DELETE FROM upload_leases WHERE expires<=?", (now,))
