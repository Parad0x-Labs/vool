"""Badges on what is already on this device: bundled skills by the checksum of their SKILL.md, installed plugins
by the checksum of their files, both only from a signed badge document. Through the Add-ons page's own
catalogue read (core.addon_store.catalog) and the plugin list (core.plugin_catalog.read_plugin_catalog)."""
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import addon_store, plugin_catalog, plugin_lifecycle
from core import plugin_manager as manager
from core.addon_catalog import CATALOG
from core.registry import client as native_client
from core.registry.codec import canonical
from tests.registry.badge_helpers import DAY, browse, by, entry, issuer_list, payload, raw_document, relay, world
from tests.registry.helpers import NOW, TARGET
from tests.test_plugin_identity_is_checked_at_install import door

ROW = next(row for row in CATALOG if row.get("bundled_source") and row.get("sha256"))


@pytest.fixture
def device(tmp_path, monkeypatch):
    """A profile whose trust root is the test's, with a folder standing in for the release's own badge files."""
    monkeypatch.setenv("VOOL_PLUGIN_LIFECYCLE_PATH", str(tmp_path / "lifecycle.json"))
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(tmp_path / "installed"))
    monkeypatch.setenv("VOOL_BUNDLED_PLUGINS_DIR", str(tmp_path / "no-bundled"))
    monkeypatch.setenv("VOOL_NATIVE_SKILLS_DIR", str(tmp_path / "no-native"))
    plugin_lifecycle.reset_for_tests()
    w = world(tmp_path, monkeypatch)
    anchor = tmp_path / "trusted-root.json"
    anchor.write_bytes(canonical(w.root))
    monkeypatch.setenv("VOOL_REGISTRY_ROOT_FILE", str(anchor))
    w.release = tmp_path / "release"
    w.release.mkdir()
    monkeypatch.setattr(native_client, "_shipped_badges", lambda: w.release)
    monkeypatch.setattr(native_client, "time", SimpleNamespace(time=lambda: NOW))
    monkeypatch.setattr(native_client, "runtime_target", lambda: TARGET)
    yield w
    plugin_lifecycle.reset_for_tests()


def ship(w, document, signers=None, issuers=None):
    (w.release / "badges.json").write_bytes(canonical(by(document, w.people[:2] if signers is None else signers)))
    (w.release / "badge-issuers.json").write_bytes(canonical(w.issuers if issuers is None else issuers))


def bundled(w, badge="editors_choice", **options):
    return entry(badge, ROW["sha256"], w.people, package_id=ROW["id"], digest_type="skill-file", **options)


def rows():
    return {row["id"]: row["badges"] for row in addon_store.catalog()["entries"]}


def test_a_release_without_a_badge_document_shows_no_badge_anywhere(device):
    assert len(rows()) >= len(CATALOG) and set(map(tuple, rows().values())) == {()}
    # This repository ships no badge document: no badge keys exist yet.
    shipped = Path(native_client.__file__).parent
    assert not (shipped / "badges.json").exists() and not (shipped / "badge-issuers.json").exists()


def test_a_bundled_skill_gets_its_badge_from_the_releases_signed_document_only(device):
    ship(device, raw_document([bundled(device), bundled(device, "tested")]))
    shown = rows()
    assert shown.pop(ROW["id"]) == ["tested", "editors_choice"]
    assert set(map(tuple, shown.values())) == {()}  # no other row


@pytest.mark.parametrize("case", ["one signature", "list not signed by the root", "another checksum", "another id",
                                  "badge ran out", "not tested on this build"])
def test_anything_less_shows_nothing_on_a_bundled_skill(device, case, monkeypatch):
    w = device
    document, signers, issuers = raw_document([bundled(w)]), None, None
    if case == "one signature":
        signers = w.people[:1]
    elif case == "list not signed by the root":
        issuers = issuer_list(w.people, w.keys, role="admission")
    elif case == "another checksum":
        document = raw_document([entry("editors_choice", "f" * 64, w.people, package_id=ROW["id"], digest_type="skill-file")])
    elif case == "another id":
        document = raw_document([entry("editors_choice", ROW["sha256"], w.people, package_id="github-" + "0" * 24,
                                       digest_type="skill-file")])
    elif case == "badge ran out":
        document = raw_document([bundled(w, issued_at=NOW - 366 * DAY)])
    else:
        document = raw_document([bundled(w, "tested", targets=[{"version": "0.9.9", "platform": TARGET["platform"]}])])
    ship(w, document, signers, issuers)
    assert rows()[ROW["id"]] == []


def test_a_badge_written_on_a_row_is_dropped(device, monkeypatch):
    """Neither the shipped directory nor a row saved on disk can carry a badge of its own."""
    claimed = [dict(row, badges=["tested", "editors_choice"], tested=True, editors_pick=True, featured=True) for row in CATALOG]
    monkeypatch.setattr(addon_store, "CATALOG", claimed)
    found = {**ROW, "id": "github-" + "9" * 24, "badges": ["tested", "editors_choice"], "bundled_source": False}
    addon_store._write(addon_store._root() / "discovered" / (found["id"] + ".json"), found)
    shown = rows()
    assert found["id"] in shown and set(map(tuple, shown.values())) == {()}


def test_the_releases_document_counts_past_its_date_until_this_device_verifies_a_newer_one(device):
    w = device
    old = raw_document([bundled(w, issued_at=NOW - 120 * DAY)], issued_at=NOW - 120 * DAY)  # ran out a month ago
    ship(w, old)
    assert rows()[ROW["id"]] == ["editors_choice"]
    # Browse verifies a newer document that no longer lists it: from then on the newer one decides.
    assert relay(w, payload(w, raw_document([], sequence=2))).status_code == 200
    browse(w)
    assert rows()[ROW["id"]] == []


def test_a_shipped_copy_that_does_not_verify_counts_for_nothing_and_hides_nothing(device):
    w = device
    plugin = dict(package_id="acme-parcel", kind="plugin", digest_type="package")
    assert relay(w, payload(w, raw_document([entry("editors_choice", "c" * 64, w.people, **plugin)]))).status_code == 200
    browse(w)
    ship(w, raw_document([bundled(w)], sequence=9), signers=w.people[:1])  # one signature
    assert rows()[ROW["id"]] == []
    index = native_client.local_badges()  # what this device verified at Browse still stands
    assert index == {("package", "c" * 64): {"kind": "plugin", "id": "acme-parcel", "version": "1.0.0", "badges": ["editors_choice"]}}
    (w.release / "badges.json").write_bytes(b"{not json")
    assert native_client.local_badges() == index


def test_a_withdrawal_seen_once_also_ends_the_badge_the_release_shipped(device):
    w = device
    ship(w, raw_document([bundled(w)], sequence=5))
    assert rows()[ROW["id"]] == ["editors_choice"]
    assert relay(w, payload(w, raw_document([], sequence=2, revoked=[ROW["sha256"]]))).status_code == 200
    browse(w)  # an older number than the release's, but its withdrawal is remembered
    assert rows()[ROW["id"]] == []


def test_an_installed_plugin_gets_a_badge_only_for_its_exact_files(device, door):
    w = device
    folder = door("acme-parcel")
    review = manager.prepare_local(str(folder))
    manager.install(review["review_id"], accepted=True, digest=review["digest"])
    package = plugin_lifecycle.record_for("acme-parcel").verified_digest

    def shown():
        page = {row["id"]: row["badges"] for row in addon_store.catalog()["plugins"]}
        listing = {row["id"]: row.get("badges") for row in plugin_catalog.read_plugin_catalog()["plugins"]}
        assert page["acme-parcel"] == listing["acme-parcel"]
        return page["acme-parcel"]

    assert shown() == []
    named = dict(package_id="acme-parcel", kind="plugin", digest_type="package")
    ship(w, raw_document([entry("tested", package, w.people, **named), entry("editors_choice", package, w.people, **named)]))
    assert shown() == ["tested", "editors_choice"]
    # The checksum of another package, or these bytes under another plugin's name: nothing.
    ship(w, raw_document([entry("editors_choice", "e" * 64, w.people, **named)]))
    assert shown() == []
    ship(w, raw_document([entry("editors_choice", package, w.people, **{**named, "package_id": "other-plugin"})]))
    assert shown() == []
    ship(w, raw_document([entry("editors_choice", package, w.people, **{**named, "kind": "skill", "digest_type": "skill-file"})]))
    assert shown() == []
    # One changed byte in the installed files: the badge was for other bytes.
    ship(w, raw_document([entry("editors_choice", package, w.people, **named)]))
    assert shown() == ["editors_choice"]
    installed = plugin_lifecycle.record_for("acme-parcel").root
    os.chmod(installed + "/bin/tool", 0o755)
    with open(installed + "/bin/tool", "a") as handle:
        handle.write("# changed\n")
    plugin_catalog.invalidate_storage_listing()
    assert shown() == []


def test_a_plugin_cannot_write_itself_a_badge(device, door):
    folder = door("acme-parcel")
    path = folder / ".codex-plugin" / "plugin.json"
    raw = json.loads(path.read_text())
    raw["badges"] = ["tested", "editors_choice"]
    raw["interface"] = {"displayName": "Parcel desk", "shortDescription": "Tested by VOOL. Editor's Choice."}
    path.write_text(json.dumps(raw))
    review = manager.prepare_local(str(folder))
    manager.install(review["review_id"], accepted=True, digest=review["digest"])
    assert [(row["id"], row["badges"]) for row in addon_store.catalog()["plugins"]] == [("acme-parcel", [])]
    listed = {row["id"]: row["badges"] for row in plugin_catalog.read_plugin_catalog()["plugins"] if "badges" in row}
    assert listed == {"acme-parcel": []}
