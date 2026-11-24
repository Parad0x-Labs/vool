"""Test-only badge signers and a small world: a real service, a real app client, keys made in the test."""
import json
import urllib.parse
from types import SimpleNamespace

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from starlette.testclient import TestClient

from core.registry import badges
from core.registry.artifact import inspect, pack
from core.registry.client import Client
from core.registry.codec import canonical, digest
from core.registry.trust import TrustedCatalog
from registry_service.api import PREFIX, create_app
from registry_service.auth import Auth
from registry_service.authority import HEADER, sign_request
from registry_service.store import Store
from tests.registry.helpers import NOW, TARGET, artifact_fixture, catalog_fixture, signed, trust_fixture

ORIGIN = "https://registry.test"
DAY = 86400
BUILD = {"version": TARGET["version"], "platform": TARGET["platform"]}


def person():
    """One appointed person's key: (key id, private key, public line)."""
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    return digest({"ed25519": public}), private, public


def entry(badge, sha256, people, *, package_id="alice/echo", version="1.0.0", kind="skill", digest_type="artifact",
          issued_at=NOW, days=None, targets=None, note=""):
    days = {"tested": 180, "editors_choice": 365}[badge] if days is None else days
    return {"badge": badge, "kind": kind, "id": package_id, "version": version, "digest_type": digest_type,
            "sha256": sha256, "issued_at": issued_at, "expires": issued_at + days * DAY,
            "targets": ([BUILD] if targets is None else targets) if badge == "tested" else [],
            "evidence_sha256": digest(badge + sha256), "issued_by": [people[0][0], people[1][0]], "note": note}


def raw_document(entries, *, sequence=1, previous=None, revoked=(), issued_at=NOW, days=90):
    """The signed part, written out by hand so a test can also make one the builder would refuse."""
    return {"schema": badges.SCHEMA, "sequence": sequence, "previous_sha256": digest(previous) if previous else "",
            "issued_at": issued_at, "expires": issued_at + days * DAY, "badges": list(entries), "team_releases": [],
            "revoked": sorted(revoked)}


def by(value, people):
    return signed(value, {keyid: private for keyid, private, _ in people})


def issuer_list(people, keys, *, version=1, expires=NOW + 365 * DAY, role="root"):
    value = {"schema": badges.ISSUERS_SCHEMA, "version": version, "expires": expires,
             "keys": {keyid: public for keyid, _, public in people}, "threshold": 2}
    return signed(value, keys[role])


def stamp_for(document, keys, *, issued_at=NOW, days=7, role="admission"):
    value = {"schema": badges.STAMP_SCHEMA, "sequence": document["sequence"], "badges_sha256": digest(document),
             "issued_at": issued_at, "expires": issued_at + days * DAY}
    return signed(value, keys[role])


def world(tmp_path, monkeypatch, *, versions=("1.0.0",)):
    """A service with one listed skill (its newest version last) and an app that browses it."""
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "app-home"))
    _, root, info = artifact_fixture(tmp_path)
    raws = []
    for version in versions:
        manifest = root / ".codex-plugin" / "plugin.json"
        manifest.write_text(json.dumps({**json.loads(manifest.read_text()), "version": version}))
        raws.append(pack(root, {**info, "version": version}, tmp_path / "scratch"))
    policy, keys, trust_root = trust_fixture()
    trust = TrustedCatalog(trust_root, tmp_path / "server/trust", now=NOW)
    trust.accept(catalog_fixture(raws[-1], policy, keys), now=NOW)
    store = Store(tmp_path / "server/accounts")
    store.publish(trust, "alice/echo", versions[-1], TARGET, raws[-1], now=NOW)
    http = TestClient(create_app(store, Auth(store, ORIGIN, []), trust, TARGET, clock=lambda: NOW, authority=object()),
                      base_url=ORIGIN)
    w = SimpleNamespace(tmp=tmp_path, keys=keys, policy=policy, root=trust_root, trust=trust, store=store, http=http, served=None,
                        people=[person(), person(), person()], sha=[inspect(raw)["sha256"] for raw in raws], fetched=[])
    w.issuers = issuer_list(w.people, keys)

    def request(url, *, limit):
        path = urllib.parse.urlsplit(url).path
        w.fetched.append(path)
        if path == PREFIX + "/badges" and w.served is not None:
            return canonical(w.served)  # a server someone took over answers whatever it likes
        response = http.get(url)
        assert response.status_code == 200 and len(response.content) <= limit
        return response.content

    w.request = request
    return w


def payload(w, document, signers=None, *, issuers=None, stamp=None):
    """What the service serves for one document: by default two appointed people, the root's list, a fresh stamp."""
    envelope = by(document, w.people[:2] if signers is None else signers)
    return {"issuers": w.issuers if issuers is None else issuers, "badges": envelope,
            "stamp": stamp_for(document, w.keys) if stamp is None else stamp}


def relay(w, value):
    """Send through the signer's door, as the signer does: its request carries an admission key's signature."""
    body = canonical(value)
    key = next(iter(w.keys["admission"].values()))
    path = PREFIX + "/authority/badges"
    return w.http.post(path, content=body, headers={HEADER: sign_request(key, "POST", path, body, now=NOW),
                                                    "Content-Type": "application/json"})


def browse(w, *, target=TARGET, now=NOW):
    """The app's Browse: the rows it would draw, by id."""
    app = Client(w.root, target, origin=ORIGIN, request=w.request, now=now)
    try:
        return {row["id"]: row for row in app.refresh(approved=True, now=now)["entries"]}
    finally:
        app.close()


def shown(w, **options):
    return browse(w, **options)["alice/echo"]["badges"]
