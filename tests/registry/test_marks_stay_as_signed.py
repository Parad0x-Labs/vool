"""The marks the service keeps are the marks that were signed, byte for byte.

The app checks the signature on the marks it is served. A check on the way in used to fill in an
empty picture list inside the signed part before the service stored it, so marks sent without
that list were served with one more field than was signed, and the app showed no pick, no
featured row and no delisting from them.
"""
import copy

from core.registry.codec import canonical, decode
from core.registry.trust import verify
from registry_service.store import Store
from tests.registry.helpers import NOW, signed, trust_fixture


def _marks(curation: dict) -> dict:
    return {"schema": "vool.registry.marks.v1", "sequence": 1, "issued_at": NOW, "tested": [], "highlighted": [],
            "suspended": [], "teams": {}, "curation": curation}


def _curation(**more) -> dict:
    return {"schema": "vool.registry.curation.v1", "official_namespaces": [], "featured": ["alice/echo"],
            "editors_picks": [], "reviewers": {}, "reviews": [], **more}


def _served(tmp_path, curation):
    """What GET /presentation hands the app as "marks": the stored document, decoded."""
    _policy, keys, root = trust_fixture()
    store = Store(tmp_path / "private" / "accounts.sqlite")
    sent = signed(_marks(curation), keys["admission"])
    as_signed = copy.deepcopy(sent)
    store.set_marks(sent, root["signed"], now=NOW)
    with store.connection() as db:
        row = db.execute("SELECT document FROM authority_marks WHERE id=1").fetchone()
    return decode(row[0]), as_signed, root


def test_marks_sent_without_a_picture_list_are_served_as_signed_and_verify_in_the_app(tmp_path):
    served, as_signed, root = _served(tmp_path, _curation())
    assert canonical(served) == canonical(as_signed)
    assert verify(served, root["signed"], "admission")["curation"]["featured"] == ["alice/echo"]


def test_marks_sent_with_a_picture_list_are_served_as_signed_too(tmp_path):
    served, as_signed, root = _served(tmp_path, _curation(approved_pictures=[{"id": "alice/echo", "sha256": "a" * 64}]))
    assert canonical(served) == canonical(as_signed)
    verify(served, root["signed"], "admission")
