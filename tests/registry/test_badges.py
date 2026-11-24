"""The two team badges hold only as one signed document: two appointed people, an exact checksum, an end date.

Every case goes through the doors a person or a server uses: the signer's relay door on the service,
the public /badges route, and the app's Browse (core.registry.client.Client.refresh)."""
import pytest

from core.registry import badges
from core.registry.client import Client
from core.registry.codec import RegistryError, canonical, digest
from registry_service.api import PREFIX
from tests.registry.badge_helpers import (
    BUILD,
    DAY,
    ORIGIN,
    browse,
    by,
    entry,
    issuer_list,
    payload,
    person,
    raw_document,
    relay,
    shown,
    stamp_for,
    world,
)
from tests.registry.helpers import NOW, TARGET, signed


@pytest.fixture
def w(tmp_path, monkeypatch):
    return world(tmp_path, monkeypatch)


def both(w, sha=None):
    sha = w.sha[-1] if sha is None else sha
    return [entry("tested", sha, w.people), entry("editors_choice", sha, w.people, note="clear and small")]


def test_two_appointed_people_give_both_badges_and_nothing_else_on_the_service_changes(w):
    assert shown(w) == []  # no document yet: nothing
    document = raw_document(both(w))
    answer = relay(w, payload(w, document))
    assert answer.status_code == 200 and answer.json() == {"sequence": 1}
    served = w.http.get(PREFIX + "/badges").json()
    assert set(served) == {"issuers", "badges", "stamp"} and served["badges"]["signed"] == document
    assert shown(w) == ["tested", "editors_choice"]
    # Shipped apps read these two strictly; the badges ride on their own route.
    assert set(w.http.get(PREFIX + "/presentation").json()) == {"marks", "metrics"}
    assert "badges" not in w.http.get(PREFIX + "/catalog").json()["entries"][0]


def test_the_relay_door_needs_the_signers_request_signature(w):
    body = canonical(payload(w, raw_document(both(w))))
    refused = w.http.post(PREFIX + "/authority/badges", content=body, headers={"Content-Type": "application/json"})
    assert refused.status_code == 400 and w.store.badges()["badges"] is None


@pytest.mark.parametrize("case", ["one signature", "admission keys", "unknown key", "one key twice", "no signature"])
def test_anything_short_of_two_different_appointed_people_gives_no_badge(w, case):
    document = raw_document(both(w))
    stranger = person()
    envelope = {
        "one signature": lambda: by(document, w.people[:1]),
        # The key that signs every upload and the list, with every signature that role has.
        "admission keys": lambda: signed(document, w.keys["admission"]),
        "unknown key": lambda: by(document, [w.people[0], stranger]),
        "no signature": lambda: {"signed": document, "signatures": []},
    }.get(case, lambda: None)()
    if case == "one key twice":
        once = by(document, w.people[:1])
        envelope = {"signed": document, "signatures": once["signatures"] * 2}
    value = {"issuers": w.issuers, "badges": envelope, "stamp": stamp_for(document, w.keys)}
    assert relay(w, value).status_code == 400 and w.store.badges()["badges"] is None  # the service refuses it
    w.served = value  # and an app that is served it anyway draws nothing
    assert shown(w) == []
    assert PREFIX + "/badges" in w.fetched


@pytest.mark.parametrize("case", ["expired", "signed by the admission keys", "signed by one root key", "names a registry key",
                                  "one person only"])
def test_an_issuer_list_that_is_out_of_date_or_not_the_roots_gives_no_badge(w, case):
    document = raw_document(both(w))
    if case == "expired":
        issuers = issuer_list(w.people, w.keys, expires=NOW - 1)
    elif case == "signed by the admission keys":
        issuers = issuer_list(w.people, w.keys, role="admission")
    elif case == "signed by one root key":
        whole = issuer_list(w.people, w.keys)
        issuers = {"signed": whole["signed"], "signatures": whole["signatures"][:1]}  # this root needs two
    elif case == "names a registry key":
        keyid, private = next(iter(w.keys["admission"].items()))
        own = (keyid, private, w.root["signed"]["keys"][keyid])
        issuers = issuer_list([w.people[0], own], w.keys)
        w.people[1] = own
        document = raw_document(both(w))
    else:
        value = {"schema": badges.ISSUERS_SCHEMA, "version": 1, "expires": NOW + DAY,
                 "keys": {w.people[0][0]: w.people[0][2]}, "threshold": 1}
        issuers = signed(value, w.keys["root"])
    value = payload(w, document, issuers=issuers)
    assert relay(w, value).status_code == 400
    w.served = value
    assert shown(w) == []


def test_a_document_past_its_date_gives_no_badge(w):
    document = raw_document(both(w)[1:], issued_at=NOW - 91 * DAY)  # ran out yesterday
    document["badges"][0]["issued_at"] = NOW - 91 * DAY
    document["badges"][0]["expires"] = NOW + 200 * DAY
    value = payload(w, document)
    assert relay(w, value).status_code == 400
    w.served = value
    assert shown(w) == []


def test_a_badge_past_its_own_date_is_gone_while_the_other_stays(w):
    old = entry("tested", w.sha[-1], w.people, issued_at=NOW - 181 * DAY)  # ran out yesterday
    document = raw_document([old, entry("editors_choice", w.sha[-1], w.people)])
    assert relay(w, payload(w, document)).status_code == 200
    assert shown(w) == ["editors_choice"]


@pytest.mark.parametrize("badge,days", [("tested", 181), ("editors_choice", 366), ("document", 91)])
def test_no_badge_or_document_can_be_made_to_last_longer_than_its_limit(w, badge, days):
    if badge == "document":
        document = raw_document(both(w), days=days)
    else:
        document = raw_document([entry(badge, w.sha[-1], w.people, days=days)])
    with pytest.raises(RegistryError):
        badges.new_document(sequence=1, previous=None, entries=document["badges"], revoked=[], now=NOW, lifetime=days * DAY
                            if badge == "document" else badges.DOCUMENT_SECONDS)
    value = payload(w, document)
    assert relay(w, value).status_code == 400
    w.served = value
    assert shown(w) == []
    # One day shorter is the limit itself, and holds.
    if badge == "document":
        document = raw_document(both(w), days=days - 1)
    else:
        document = raw_document([entry(badge, w.sha[-1], w.people, days=days - 1)])
    w.served = payload(w, document)
    assert shown(w) == (["tested", "editors_choice"] if badge == "document" else [badge])


def test_the_sequence_never_goes_back_on_the_service_or_in_the_app(w):
    first = raw_document(both(w))
    second = raw_document([], sequence=2, previous=first)  # the team took both badges away
    assert relay(w, payload(w, first)).status_code == 200
    assert shown(w) == ["tested", "editors_choice"]
    assert relay(w, payload(w, second)).status_code == 200
    assert shown(w) == []
    again = relay(w, payload(w, first))
    assert again.status_code == 400 and "does not follow" in again.json()["error"]
    w.served = payload(w, first)  # a server that replays the older, validly signed document
    assert shown(w) == []
    # Another document under a number already used is refused as well.
    other = raw_document(both(w)[:1], sequence=2, previous=first)
    assert relay(w, payload(w, other)).status_code == 400
    w.served = payload(w, other)
    assert shown(w) == []
    # A document that skips the chain is not taken by the service.
    assert relay(w, payload(w, raw_document(both(w), sequence=3, previous=first))).status_code == 400
    assert relay(w, payload(w, raw_document(both(w), sequence=3, previous=second))).status_code == 200


def test_a_withdrawn_checksum_stays_withdrawn_after_a_replay_and_after_a_later_document(w):
    first = raw_document(both(w))
    second = raw_document([], sequence=2, previous=first, revoked=[w.sha[-1]])
    assert relay(w, payload(w, first)).status_code == 200 and shown(w) == ["tested", "editors_choice"]
    assert relay(w, payload(w, second)).status_code == 200 and shown(w) == []
    w.served = payload(w, first)
    assert shown(w) == []  # the replay of the older document
    third = raw_document(both(w), sequence=3, previous=second)  # signed by two people, without the withdrawal
    w.served = payload(w, third)
    assert shown(w) == []  # this app saw the withdrawal once: that checksum never shows a badge here again
    with pytest.raises(RegistryError):  # and the same document cannot both withdraw and badge a checksum
        badges.document(raw_document(both(w), revoked=[w.sha[-1]]), now=NOW)


def test_a_new_version_of_a_badged_skill_shows_no_badge(tmp_path, monkeypatch):
    w = world(tmp_path, monkeypatch, versions=("1.0.0", "1.1.0"))
    assert w.sha[0] != w.sha[1]
    document = raw_document(both(w, w.sha[0]))  # both badges, for the bytes of 1.0.0
    assert relay(w, payload(w, document)).status_code == 200
    row = browse(w)["alice/echo"]
    assert row["version"] == "1.1.0" and row["badges"] == []
    # The same checksum under another version number or another kind is not a match either.
    changes = ({"version": "1.0.0"}, {"kind": "plugin"}, {"package_id": "alice/other"})
    for number, change in enumerate(changes, 2):
        named = entry("editors_choice", w.sha[1], w.people, **{"version": "1.1.0", **change})
        w.served = payload(w, raw_document([named], sequence=number))
        assert shown(w) == [], change
    w.served = payload(w, raw_document([entry("editors_choice", w.sha[1], w.people, version="1.1.0")], sequence=5))
    assert shown(w) == ["editors_choice"]


@pytest.mark.parametrize("targets", [[{"version": "0.9.9", "platform": TARGET["platform"]}],
                                     [{"version": TARGET["version"], "platform": "macos-x86_64"}]])
def test_tested_by_vool_shows_only_on_a_vool_build_it_was_tested_on(w, targets):
    document = raw_document([entry("tested", w.sha[-1], w.people, targets=targets), entry("editors_choice", w.sha[-1], w.people)])
    assert relay(w, payload(w, document)).status_code == 200
    assert shown(w) == ["editors_choice"]
    w.served = payload(w, raw_document([entry("tested", w.sha[-1], w.people, targets=[*targets, BUILD])], sequence=2))
    assert shown(w) == ["tested"]


@pytest.mark.parametrize("case", ["missing", "out of date", "names another document", "signed by the catalog key",
                                  "lasts longer than a week"])
def test_a_document_without_a_current_stamp_gives_no_badge(w, case):
    document = raw_document(both(w))
    stamp = {"missing": None,
             "out of date": stamp_for(document, w.keys, issued_at=NOW - 8 * DAY),
             "names another document": stamp_for(raw_document([]), w.keys),
             "signed by the catalog key": stamp_for(document, w.keys, role="catalog"),
             "lasts longer than a week": stamp_for(document, w.keys, days=8)}[case]
    value = {"issuers": w.issuers, "badges": by(document, w.people[:2]), "stamp": stamp}
    assert relay(w, value).status_code == 400
    w.served = value
    assert shown(w) == []


def test_a_kept_document_stops_counting_when_its_stamp_runs_out(w):
    """A server that goes quiet cannot keep a badge alive: once the stamp is a week old the badge is gone."""
    document = raw_document(both(w))
    almost_stale = stamp_for(document, w.keys, issued_at=NOW - 7 * DAY + 60)  # one minute left
    assert relay(w, payload(w, document, stamp=almost_stale)).status_code == 200
    assert shown(w) == ["tested", "editors_choice"]
    assert shown(w, now=NOW + 59) == ["tested", "editors_choice"]
    assert shown(w, now=NOW + 60) == []  # the service still serves the same stamp
    w.http.close()  # and with the badge route unreachable, the kept copy counts for as little

    def offline(url, *, limit):
        if url.endswith("/badges"):
            raise OSError("unreachable")
        return w.request(url, limit=limit)

    app = Client(w.root, TARGET, origin=ORIGIN, request=offline, now=NOW + 60)
    try:
        assert badges.held(app.catalog, now=NOW + 59, target=TARGET)
        with pytest.raises(RegistryError):
            badges.held(app.catalog, now=NOW + 60, target=TARGET)
    finally:
        app.close()


def test_a_flag_on_a_row_gives_no_badge(w):
    """The older marks still order the list, but a pick or a tested mark there is not a badge."""
    marks = {"schema": "vool.registry.marks.v1", "sequence": 1, "issued_at": NOW,
             "tested": ["alice/echo@" + w.sha[-1]], "highlighted": ["alice/echo"], "suspended": [], "teams": {},
             "curation": {"schema": "vool.registry.curation.v1", "official_namespaces": [], "featured": ["alice/echo"],
                          "editors_picks": ["alice/echo"], "approved_pictures": [], "reviewers": {}, "reviews": []}}
    w.store.set_marks(signed(marks, w.keys["admission"]), w.root["signed"], now=NOW)
    listed = w.http.get(PREFIX + "/catalog").json()["entries"][0]
    assert listed["tested"] is True and listed["editors_pick"] is True  # the service's row says so
    row = browse(w)["alice/echo"]
    assert row["editors_pick"] is True and row["featured"] is True
    assert row["badges"] == []


def test_the_daily_check_carries_a_withdrawal_without_a_browse(w):
    first = raw_document(both(w))
    assert relay(w, payload(w, first)).status_code == 200 and shown(w) == ["tested", "editors_choice"]
    assert relay(w, payload(w, raw_document([], sequence=2, previous=first, revoked=[w.sha[-1]]))).status_code == 200
    app = Client(w.root, TARGET, origin=ORIGIN, request=w.request, now=NOW)
    try:
        del w.fetched[:]
        assert app.recall(now=NOW)["status"] == "ok"
        assert w.fetched == [PREFIX + "/snapshot", PREFIX + "/badges"]  # two public reads, nothing about the person
        w.served = payload(w, first)
        assert app.recall(now=NOW)["status"] == "ok"  # a replay does not stop the check for removed add-ons
        assert badges.held(app.catalog, now=NOW, target=TARGET) == {}
    finally:
        app.close()


def test_the_service_keeps_the_newest_issuer_list_and_takes_a_fresh_stamp(w):
    document = raw_document(both(w))
    newer = issuer_list(w.people, w.keys, version=2)
    assert relay(w, payload(w, document, issuers=newer)).status_code == 200
    older = relay(w, payload(w, document))
    assert older.status_code == 400 and "older than the one in place" in older.json()["error"]
    fresh = stamp_for(document, w.keys, issued_at=NOW + 60)
    assert relay(w, payload(w, document, issuers=newer, stamp=fresh)).status_code == 200
    assert w.store.badges()["stamp"] == fresh
    assert relay(w, payload(w, document, issuers=newer)).status_code == 400  # the earlier stamp again
    w.served = payload(w, document)  # an app that saw list 2 is not walked back to list 1
    assert shown(w) == ["tested", "editors_choice"]  # (it keeps what it verified before)
    assert digest(w.store.badges()["issuers"]["signed"]) == digest(newer["signed"])


def test_a_badge_signed_for_by_someone_no_longer_appointed_waits_for_two_current_people(w):
    document = raw_document(both(w))
    assert relay(w, payload(w, document)).status_code == 200 and shown(w) == ["tested", "editors_choice"]
    # The owner appoints a new pair; the first person is off the list.
    w.issuers = issuer_list(w.people[1:], w.keys, version=2)
    carried = raw_document(both(w), sequence=2, previous=document)
    assert relay(w, payload(w, carried, signers=w.people[1:])).status_code == 200
    assert shown(w) == []
    renewed = raw_document([entry("tested", w.sha[-1], w.people[1:])], sequence=3, previous=carried)
    assert relay(w, payload(w, renewed, signers=w.people[1:])).status_code == 200
    assert shown(w) == ["tested"]
