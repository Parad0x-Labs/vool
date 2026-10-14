"""Optional skill pictures: strict PNG checks, a person's approval per skill, and VOOL showing them or our own tile."""
import base64
import hashlib
import io
import json
import os
import random
import struct
import subprocess
import threading
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from PIL import Image, ImageCms
from starlette.testclient import TestClient

from core.registry.admission import admit
from core.registry.artifact import inspect, metadata, native_identity, pack
from core.registry.codec import RegistryError, canonical, decode, digest, require
from core.registry.media import bind, describe, sniff
from core.registry.trust import TrustedCatalog
from registry_service.api import PREFIX, create_app
from registry_service.auth import Auth
from registry_service.curation import load_curation
from registry_service.store import Store
from tests.registry.helpers import NOW, TARGET, evidence_fixture, signed, trust_fixture
from tests.registry.test_skill_codex import METADATA, ORIGIN, SKILL, LocalGitHub, account, import_body, mutation

ADAM7 = ((0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4), (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2))


def chunk(kind, body):
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)


def pixel(x, y):
    return (x * 7) % 256, (y * 5) % 256, (x ^ y) % 256


def png(width, height, *, interlace=False, colour=2, before=(), after=(), raw=None, end=b"", split=False, hidden=b""):
    """A PNG built byte by byte, so each damaged or disguised variant differs from a good file in one place."""
    if raw is None:
        rows = []
        for x0, y0, dx, dy in ADAM7 if interlace else ((0, 0, 1, 1),):
            for y in range(y0, height, dy):
                columns = range(x0, width, dx)
                if len(columns):  # an empty pass has no rows at all
                    rows.append(b"\0" + bytes(value for x in columns for value in pixel(x, y)))
        raw = b"".join(rows)
    data = zlib.compress(raw) + hidden
    idat = [chunk(b"IDAT", data[:len(data) // 2]), chunk(b"IDAT", data[len(data) // 2:])] if split else [chunk(b"IDAT", data)]
    header = chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, colour, 0, 0, 1 if interlace else 0))
    return b"\x89PNG\r\n\x1a\n" + header + b"".join(before) + b"".join(idat) + b"".join(after) + chunk(b"IEND", b"") + end


def saved(image, kind, **options):
    buffer = io.BytesIO()
    image.save(buffer, kind, **options)
    return buffer.getvalue()


def gradient(width, height, mode="RGB"):
    image = Image.new("RGB", (width, height))
    image.putdata([pixel(x, y) for y in range(height) for x in range(width)])
    image = image.convert(mode)
    if mode in {"RGBA", "LA"}:
        image.putalpha(160)  # see-through, so encoders keep the alpha channel
    return image


def damaged(data):
    at = data.index(b"IDAT") + 10
    return data[:at] + bytes([data[at] ^ 0xFF]) + data[at + 1:]


def exif():
    value = Image.Exif()
    value[0x010F] = "Camera maker"
    return value.tobytes()


def palette_raw(width, height):
    return (b"\0" + bytes(x % 4 for x in range(width))) * height


ICON = saved(gradient(256, 256), "PNG")
BANNER = saved(gradient(1500, 500), "PNG")
ICON_SHA, BANNER_SHA = hashlib.sha256(ICON).hexdigest(), hashlib.sha256(BANNER).hexdigest()
SRGB_PROFILE = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
FOUR_COLOURS = chunk(b"PLTE", bytes(range(12)))


# -- the picture checks ------------------------------------------------------------------------


@pytest.mark.parametrize("name, data", ids=lambda value: value if isinstance(value, str) else "-", argvalues=[
    ("rgb.png", ICON),
    ("rgba.png", saved(gradient(256, 256, "RGBA"), "PNG")),
    ("palette.png", saved(gradient(256, 256, "P"), "PNG")),
    ("palette-transparent.png", saved(gradient(256, 256, "P"), "PNG", transparency=0)),
    ("grey.png", saved(gradient(256, 256, "L"), "PNG")),
    ("grey-alpha.png", saved(gradient(256, 256, "LA"), "PNG")),
    ("one-bit.png", saved(gradient(256, 256, "1"), "PNG")),
    ("sixteen-bit.png", saved(gradient(256, 256, "I;16"), "PNG")),
    ("density.png", saved(gradient(256, 256), "PNG", dpi=(144, 144))),
    ("interlaced.png", png(129, 129, interlace=True)),
    ("split-image-data.png", png(256, 256, split=True)),
    ("colour-hints.png", png(256, 256, before=[chunk(b"sRGB", b"\0"), chunk(b"sBIT", b"\x08\x08\x08"),
                                                chunk(b"bKGD", b"\0\1\0\2\0\3")])),
    ("four-colours.png", png(256, 256, colour=3, raw=palette_raw(256, 256),
                             before=[FOUR_COLOURS, chunk(b"tRNS", b"\0\x80"), chunk(b"bKGD", b"\1")])),
])
def test_real_pictures_pass_with_the_size_a_decoder_reads(name, data):
    with Image.open(io.BytesIO(data)) as image:
        image.load()  # a real decoder accepts the same bytes
        size = image.size
    assert describe("icon", "assets/" + name, data) == {
        "sha256": hashlib.sha256(data).hexdigest(), "type": "image/png", "width": size[0], "height": size[1],
        "bytes": len(data)}


@pytest.mark.parametrize("width, height", [(1, 1), (3, 2), (5, 9), (8, 8), (13, 3)])
def test_interlaced_png_row_count_matches_a_real_decoder_even_with_empty_passes(width, height):
    data = png(width, height, interlace=True)
    with Image.open(io.BytesIO(data)) as image:
        assert list(image.convert("RGB").get_flattened_data()) == [pixel(x, y) for y in range(height) for x in range(width)]
    assert sniff(data) == ("image/png", width, height)
    # Image data one byte short of what the header declares is refused.
    short = png(width, height, interlace=True, raw=zlib.decompress(png_idat(data))[:-1])
    with pytest.raises(RegistryError, match="does not match its size"):
        sniff(short)


def png_idat(data):
    offset, found = 8, b""
    while offset < len(data):
        length, kind = struct.unpack(">I4s", data[offset:offset + 8])
        if kind == b"IDAT":
            found += data[offset + 8:offset + 8 + length]
        offset += 12 + length
    return found


def two_frames():
    return {"save_all": True, "append_images": [gradient(256, 256).rotate(90)], "duration": 100}


@pytest.mark.parametrize("name, data, message", ids=lambda value: value if isinstance(value, str) else "-", argvalues=[
    ("icon.png", b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', "PNG files"),
    ("icon.png", saved(gradient(256, 256), "GIF"), "PNG files"),
    ("icon.png", saved(gradient(256, 256), "JPEG"), "PNG files"),
    ("icon.png", saved(gradient(256, 256), "WEBP"), "PNG files"),
    ("icon.png", png(256, 256, before=[chunk(b"tEXt", b"Comment\0visit my site")]), "hidden notes"),
    ("icon.png", png(256, 256, before=[chunk(b"iTXt", b"XML:com.adobe.xmp\0\0\0\0\0<x/>")]), "hidden notes"),
    ("icon.png", png(256, 256, before=[chunk(b"eXIf", exif())]), "camera details"),
    ("icon.png", png(256, 256, after=[chunk(b"tIME", b"\x07\xea\x0a\x0e\x00\x00\x00")]), "hidden notes"),
    ("icon.png", saved(gradient(256, 256), "PNG", **two_frames()), "animated"),
    ("icon.png", saved(gradient(256, 256), "PNG", icc_profile=SRGB_PROFILE), "colour profile"),
    ("icon.png", png(256, 256, before=[chunk(b"gAMA", b"\0\0\xb1\x8f")]), "colour profile"),
    ("icon.png", png(256, 256, before=[chunk(b"cHRM", bytes(32))]), "colour profile"),
    ("icon.png", png(256, 256, before=[chunk(b"prVt", b"hidden payload")]), "colour profile or other extra data"),
    ("icon.png", png(256, 256, before=[chunk(b"pHYs", b"\0\0\0\1\0\0\0\1\0" + b"a note hidden here" * 50)]), "pHYs section"),
    ("icon.png", png(256, 256, before=[chunk(b"sRGB", b"\0"), chunk(b"sRGB", b"\0")]), "repeats a section"),
    ("icon.png", png(256, 256, after=[chunk(b"sRGB", b"\0")]), "sRGB section"),
    ("icon.png", png(256, 256, before=[chunk(b"sRGB", b"\x09")]), "sRGB section"),
    ("icon.png", png(256, 256, colour=6, raw=(b"\0" + bytes(4 * 256)) * 256, before=[chunk(b"tRNS", b"\0\0")]), "tRNS section"),
    ("icon.png", png(256, 256, colour=0, raw=(b"\0" + bytes(256)) * 256, before=[FOUR_COLOURS]), "PLTE section"),
    ("icon.png", png(256, 256, colour=3, raw=palette_raw(256, 256), before=[FOUR_COLOURS, chunk(b"tRNS", bytes(5))]),
     "tRNS section"),
    ("icon.png", png(256, 256, colour=3, raw=palette_raw(256, 256), before=[chunk(b"bKGD", b"\1"), FOUR_COLOURS]),
     "palette must come first"),
    ("icon.png", png(256, 256, colour=3, raw=palette_raw(256, 256)), "palette is missing"),
    ("icon.png", png(256, 256, end=b"PK\x03\x04 a zip archive glued on"), "data after its end"),
    ("icon.png", png(256, 256, end=b"\0"), "data after its end"),
    ("icon.png", png(256, 256, hidden=b"payload hidden after the image stream"), "does not match its size"),
    ("icon.png", png(256, 256)[:-30], "cut short"),
    ("icon.png", damaged(png(256, 256)), "checksum mismatch"),
    ("icon.png", png(256, 256, raw=b"\0" + bytes(3 * 256)), "does not match its size"),  # one row of 256
    ("icon.png", png(256, 256, raw=(b"\0" + bytes(3 * 256)) * 257), "does not match its size"),
    ("icon.png", png(256, 256, raw=(b"\x07" + bytes(3 * 256)) * 256), "damaged"),  # filter type 7
    ("icon.png", png(256, 256, colour=7), "colour format"),
    ("icon.webp", ICON, "must be a .png file"),
    ("icon.png", saved(gradient(256, 200), "PNG"), "256 x 200 pixels"),
    ("icon.png", saved(gradient(64, 64), "PNG"), "64 x 64 pixels"),
    ("icon.png", png(20000, 20000, raw=b"\0" * 64), "20000 x 20000 pixels"),
    ("icon.png", saved(Image.frombytes("RGB", (1024, 1024), os.urandom(3 * 1024 * 1024)), "PNG"), "too large"),
    ("banner.png", saved(gradient(1200, 600), "PNG"), "1200 x 600 pixels"),
    ("banner.png", saved(gradient(800, 240), "PNG"), "800 x 240 pixels"),
])
def test_disguised_damaged_animated_or_oversized_pictures_are_refused(name, data, message):
    with pytest.raises(RegistryError, match=message):
        describe("banner" if name.startswith("banner") else "icon", name, data)


def test_the_size_limit_is_checked_from_the_header_before_any_image_data_is_decoded():
    # One pixel wide and four million rows tall, with image data that is not even valid.
    with pytest.raises(RegistryError, match="1 x 4000000 pixels"):
        describe("icon", "icon.png", png(1, 4_000_000, raw=b"", hidden=b"not image data"))
    with pytest.raises(RegistryError, match="too large to check"):
        sniff(png(20000, 20000, raw=b"\0" * 64))  # no limits given: the decode budget still holds


def test_png_image_data_split_by_another_section_is_refused():
    good = png(256, 256, split=True)
    describe("icon", "icon.png", good)  # adjacent pieces are fine
    first = good.index(b"IDAT") - 4
    middle = first + 12 + struct.unpack(">I", good[first:first + 4])[0]
    with pytest.raises(RegistryError, match="sRGB section"):
        describe("icon", "icon.png", good[:middle] + chunk(b"sRGB", b"\0") + good[middle:])


def fixed_checksums(data):
    """Recompute each section's checksum, so a damaged body gets past the checksum to the deeper checks."""
    result, offset = bytearray(data[:8]), 8
    while offset + 12 <= len(data):
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        if offset + 12 + length > len(data):
            break
        kind, body = data[offset + 4:offset + 8], data[offset + 8:offset + 8 + length]
        result += chunk(kind, body)
        offset += 12 + length
    return bytes(result + data[offset:])


def test_damaged_pictures_are_refused_cleanly_never_with_a_crash():
    rng = random.Random(20261014)
    seeds = [png(129, 129), png(130, 130, interlace=True), saved(gradient(128, 128, "P"), "PNG", transparency=1),
             saved(gradient(128, 128, "LA"), "PNG"), saved(gradient(128, 128, "I;16"), "PNG"),
             png(128, 128, colour=3, raw=palette_raw(128, 128), before=[FOUR_COLOURS, chunk(b"tRNS", b"\0")])]
    outcomes = {"accepted": 0, "refused": 0}
    for attempt in range(3000):
        data = bytearray(rng.choice(seeds))
        for _ in range(rng.randint(1, 4)):
            at, choice = rng.randrange(8, len(data)), rng.random()
            if choice < 0.5:
                data[at] = rng.randrange(256)
            elif choice < 0.75:
                del data[at:at + rng.randint(1, 16)]
            else:
                data[at:at] = rng.randbytes(rng.randint(1, 16))
        candidate = fixed_checksums(bytes(data)) if attempt % 2 else bytes(data)
        try:
            describe("icon", "icon.png", candidate)
            outcomes["accepted"] += 1
        except RegistryError:
            outcomes["refused"] += 1
    assert sum(outcomes.values()) == 3000 and outcomes["refused"] > 2000


def test_banner_limits_accept_the_documented_example_size():
    assert describe("banner", "art/banner.png", BANNER)["width"] == 1500
    assert describe("banner", "art/banner.png", saved(Image.new("RGB", (3000, 1200), (40, 60, 80)), "PNG"))["height"] == 1200


@pytest.mark.parametrize("value, message", [
    ("assets/icon.png", "name its path and sha256"),
    ({"path": "assets/icon.png"}, "name its path and sha256"),
    ({"path": "assets/icon.png", "sha256": "a" * 64, "url": "https://elsewhere.test/i.png"}, "name its path and sha256"),
    ({"path": "assets/icon.svg", "sha256": "a" * 64}, "must be a .png file"),
    ({"path": "assets/icon.webp", "sha256": "a" * 64}, "must be a .png file"),
    ({"path": "assets/icon", "sha256": "a" * 64}, "must be a .png file"),
    ({"path": "../icon.png", "sha256": "a" * 64}, "ambiguous package path"),
    ({"path": "https://elsewhere.test/icon.png", "sha256": "a" * 64}, "invalid package path"),
    ({"path": "assets/icon.png", "sha256": "A" * 64}, "sha256"),
])
def test_signed_metadata_names_a_picture_only_by_package_path_and_checksum(value, message):
    info = {**METADATA, "source": {"repository": "alice/skills", "commit": "b" * 40, "path": "notes"}}
    metadata({**info, "icon": {"path": "assets/icon.png", "sha256": "a" * 64}})
    with pytest.raises(RegistryError, match=message):
        metadata({**info, "icon": value})


# -- packages and the author tool --------------------------------------------------------------


def skill_source(tmp_path, package_id, *, version="1.0.0", pictures=None):
    name = package_id.split("/")[1]
    root = tmp_path / ("src-" + package_id.replace("/", "-") + "-" + version)
    (root / ".codex-plugin").mkdir(parents=True)
    (root / ".codex-plugin" / "plugin.json").write_text(json.dumps({
        "name": native_identity(package_id), "version": version, "skills": "./skills/", "requires": []}))
    (root / "skills" / name).mkdir(parents=True)
    (root / "skills" / name / "SKILL.md").write_text(
        "---\nname: " + name + "\ndescription: Summarize meeting notes into decisions\n---\nUse only the text the user provides.\n")
    (root / "LICENSE").write_text("Test fixture licence.\n")
    info = {"id": package_id, "kind": "skill", "version": version, "summary": "Summarize meeting notes into decisions",
            "category": "productivity", "subcategory": "meetings", "license": "MIT", "languages": ["en"],
            "source": {"repository": package_id.split("/")[0] + "/skills", "commit": "b" * 40, "path": name}}
    for kind, (path, data) in (pictures or {}).items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_bytes(data)
        info[kind] = path
    return root, info


def skill_package(tmp_path, package_id, **options):
    root, info = skill_source(tmp_path, package_id, **options)
    return pack(root, bind(info, lambda path: (root / path).read_bytes()), tmp_path / "scratch")


def test_packages_carry_pictures_bound_to_their_checksums(tmp_path):
    raw = skill_package(tmp_path, "alice/notes", pictures={"icon": ("assets/icon.png", ICON),
                                                           "banner": ("assets/banner.png", BANNER)})
    artifact = inspect(raw)
    assert artifact["metadata"]["icon"] == {"path": "assets/icon.png", "sha256": ICON_SHA}
    assert artifact["files"]["assets/banner.png"] == BANNER
    assert artifact["pictures"]["banner"] == {"sha256": BANNER_SHA, "type": "image/png", "width": 1500, "height": 500,
                                              "bytes": len(BANNER)}
    assert inspect(skill_package(tmp_path / "plain", "alice/notes"))["pictures"] == {}

    root, info = skill_source(tmp_path / "changed", "alice/notes", pictures={"icon": ("assets/icon.png", ICON)})
    with pytest.raises(RegistryError, match="checksum does not match"):
        pack(root, {**info, "icon": {"path": "assets/icon.png", "sha256": "0" * 64}}, tmp_path / "scratch")
    with pytest.raises(RegistryError, match="missing from the package"):
        pack(root, {**info, "icon": {"path": "assets/other.png", "sha256": ICON_SHA}}, tmp_path / "scratch")
    (root / "assets" / "icon.png").write_bytes(b"<svg/>")
    with pytest.raises(RegistryError, match="PNG files"):
        pack(root, bind(info, lambda path: (root / path).read_bytes()), tmp_path / "scratch")


def test_author_tool_takes_a_picture_path_and_reports_what_reviewers_will_see(tmp_path, capsys):
    from tools.community_skills import main

    root, info = skill_source(tmp_path, "alice/notes")
    metadata_file = tmp_path / "metadata.json"
    metadata_file.write_bytes(canonical({**info, "icon": "assets/icon.png"}))
    assert main(["init", str(tmp_path / "draft"), "--metadata", str(metadata_file), "--license-file", str(root / "LICENSE")]) == 1
    assert "after init" in json.loads(capsys.readouterr().out)["reason"]
    assert not (tmp_path / "draft").exists()

    (root / "assets").mkdir()
    (root / "assets" / "icon.png").write_bytes(ICON)
    (root / "art").mkdir()
    (root / "art" / "banner.png").write_bytes(BANNER)
    (root / "registry.json").write_bytes(canonical({**info, "icon": "assets/icon.png", "banner": "art/banner.png"}))
    output = tmp_path / "notes.vool.json"
    assert main(["pack", str(root), "--scratch", str(tmp_path / "scratch"), "--output", str(output)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["pictures"]["icon"] == {"sha256": ICON_SHA, "type": "image/png", "width": 256, "height": 256,
                                          "bytes": len(ICON)}
    assert report["pictures"]["banner"]["height"] == 500
    assert inspect(output.read_bytes())["metadata"]["banner"]["sha256"] == BANNER_SHA

    (root / "art" / "banner.png").write_bytes(png(1500, 500, before=[chunk(b"eXIf", exif())]))
    assert main(["lint", str(root), "--scratch", str(tmp_path / "scratch")]) == 1
    assert "export it again without metadata" in json.loads(capsys.readouterr().out)["reason"]


# -- the registry: a person approves each exact file for each skill ----------------------------


def signed_catalog(packages, policy, keys, *, revoked=()):
    entries = []
    for raw in packages:
        entry = admit(inspect(raw), policy, evidence_fixture(raw, policy), now=NOW)
        entry.update(schema="vool.registry.admission.v1", issued_at=NOW - 100, expires=NOW + 7200)
        entries.append(signed(entry, keys["admission"]))
    authorization = {"schema": "vool.registry.catalog_authorization.v1", "root_version": 1, "epoch": 1,
                     "issued_at": NOW, "expires": NOW + 7200, "entries_sha256": digest(entries),
                     "revoked_sha256": digest(list(revoked))}
    return signed({"schema": "vool.registry.catalog.v1", "root_version": 1, "sequence": 1, "issued_at": NOW,
                   "expires": NOW + 3600, "entries": entries, "revoked": list(revoked),
                   "authorization": signed(authorization, keys["admission"])}, keys["catalog"])


def published(tmp_path, packages, *, listed=None, revoked=()):
    """Publish every package; the app's catalog lists `listed` (default: all of them)."""
    policy, keys, root = trust_fixture()
    store = Store(tmp_path / "private" / "accounts.sqlite")
    publishing = TrustedCatalog(root, tmp_path / "trust" / "publish", now=NOW)
    if packages:
        publishing.accept(signed_catalog(packages, policy, keys), now=NOW)
    for raw in packages:
        info = inspect(raw)["metadata"]
        store.publish(publishing, info["id"], info["version"], TARGET, raw, now=NOW)
    publishing.close()
    catalog = TrustedCatalog(root, tmp_path / "trust" / "serve", now=NOW)
    listed = packages if listed is None else listed
    if listed:
        catalog.accept(signed_catalog(listed, policy, keys, revoked=revoked), now=NOW)
    return store, catalog, SimpleNamespace(root=root, policy=policy, keys=keys)


def app(store, catalog, *, approved=(), clock=NOW, github=None):
    curation = load_curation()
    curation["approved_pictures"] = [{"id": package_id, "sha256": sha} for package_id, sha in approved]
    return create_app(store, Auth(store, ORIGIN, []), catalog, TARGET, clock=lambda: clock, curation=curation,
                      github=github)


def with_pictures(tmp_path, package_id="alice/notes"):
    return skill_package(tmp_path, package_id, pictures={"icon": ("assets/icon.png", ICON),
                                                         "banner": ("assets/banner.png", BANNER)})


def picture_url(package_id, sha):
    return PREFIX + "/pictures/" + package_id + "/" + sha


def test_pictures_show_only_after_a_person_approves_that_exact_file_for_that_skill(tmp_path):
    store, catalog, _ = published(tmp_path, [with_pictures(tmp_path), skill_package(tmp_path, "bob/plain")])
    with TestClient(app(store, catalog), base_url=ORIGIN) as client:
        rows = {row["id"]: row for row in client.get(PREFIX + "/catalog").json()["entries"]}
        assert "icon" not in rows["alice/notes"] and "banner" not in rows["alice/notes"]
        assert rows["alice/notes"]["pictures_pending"] == ["icon", "banner"]
        assert not {"icon", "banner", "pictures_pending"} & set(rows["bob/plain"])
        refused = client.get(picture_url("alice/notes", ICON_SHA))
        assert refused.status_code == 400 and refused.json() == {"error": "picture not found"}

    with TestClient(app(store, catalog, approved=[("alice/notes", ICON_SHA)]), base_url=ORIGIN) as client:
        catalog_response = client.get(PREFIX + "/catalog")
        row = {row["id"]: row for row in catalog_response.json()["entries"]}["alice/notes"]
        assert row["icon"] == {"sha256": ICON_SHA, "type": "image/png", "width": 256, "height": 256,
                               "url": picture_url("alice/notes", ICON_SHA)}
        assert "banner" not in row and row["pictures_pending"] == ["banner"]
        assert client.get(PREFIX + "/profiles/alice").json()["packages"][0]["icon"] == row["icon"]
        response = client.get(row["icon"]["url"])
        assert response.status_code == 200 and response.content == ICON
        assert response.headers["content-type"] == "image/png"
        assert response.headers["cache-control"] == "public, max-age=300"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["cross-origin-resource-policy"] == "same-origin"
        assert response.headers["content-security-policy"].startswith("default-src 'none'")
        assert catalog_response.headers["cache-control"] == "no-store"  # everything else stays uncached
        assert client.get(picture_url("alice/notes", BANNER_SHA)).status_code == 400  # not approved yet
        assert client.get(picture_url("alice/notes", ICON_SHA.upper())).status_code == 400
        assert client.get(PREFIX + "/pictures/" + ICON_SHA).status_code == 404  # a picture always names its skill

    with TestClient(app(store, catalog, approved=[("alice/notes", BANNER_SHA)]), base_url=ORIGIN) as client:
        # Taking an approval out of curation hides that picture again.
        row = client.get(PREFIX + "/catalog").json()["entries"][0]
        assert "icon" not in row and row["banner"]["width"] == 1500 and row["pictures_pending"] == ["icon"]
        assert client.get(picture_url("alice/notes", ICON_SHA)).status_code == 400
        assert client.get(picture_url("alice/notes", BANNER_SHA)).content == BANNER
    catalog.close()


def test_an_approval_never_carries_over_to_another_skill_reusing_the_same_file(tmp_path):
    copycat = skill_package(tmp_path, "mallory/notes-pro", pictures={"icon": ("icon.png", ICON)})
    store, catalog, _ = published(tmp_path, [with_pictures(tmp_path), copycat, skill_package(tmp_path, "bob/plain")])
    approved = [("alice/notes", ICON_SHA), ("bob/plain", ICON_SHA)]  # bob's listing does not carry this file
    with TestClient(app(store, catalog, approved=approved), base_url=ORIGIN) as client:
        rows = {row["id"]: row for row in client.get(PREFIX + "/catalog").json()["entries"]}
        assert rows["alice/notes"]["icon"]["sha256"] == ICON_SHA
        assert "icon" not in rows["mallory/notes-pro"] and rows["mallory/notes-pro"]["pictures_pending"] == ["icon"]
        assert client.get(picture_url("alice/notes", ICON_SHA)).content == ICON
        assert client.get(picture_url("mallory/notes-pro", ICON_SHA)).status_code == 400
        assert client.get(picture_url("bob/plain", ICON_SHA)).status_code == 400
    catalog.close()


def test_approved_pictures_are_served_only_while_a_valid_signed_catalog_lists_them(tmp_path):
    notes, plain = with_pictures(tmp_path), skill_package(tmp_path, "bob/plain")
    approved = [("alice/notes", ICON_SHA)]
    store, catalog, _ = published(tmp_path, [notes, plain], listed=[plain])
    with TestClient(app(store, catalog, approved=approved), base_url=ORIGIN) as client:
        assert client.get(picture_url("alice/notes", ICON_SHA)).status_code == 400  # stored and approved, not listed
    catalog.close()
    store, catalog, _ = published(tmp_path / "revoked", [notes, plain], revoked=[inspect(notes)["sha256"]])
    with TestClient(app(store, catalog, approved=approved), base_url=ORIGIN) as client:
        assert [row["id"] for row in client.get(PREFIX + "/catalog").json()["entries"]] == ["bob/plain"]
        assert client.get(picture_url("alice/notes", ICON_SHA)).status_code == 400
    catalog.close()
    store, catalog, _ = published(tmp_path / "expired", [notes])
    with TestClient(app(store, catalog, approved=approved, clock=NOW + 4000), base_url=ORIGIN) as client:
        assert client.get(picture_url("alice/notes", ICON_SHA)).json() == {"error": "picture not found"}
    catalog.close()
    store, catalog, _ = published(tmp_path / "unsigned", [notes], listed=[])
    with TestClient(app(store, catalog, approved=approved), base_url=ORIGIN) as client:  # no signed catalog yet
        assert client.get(picture_url("alice/notes", ICON_SHA)).json() == {"error": "picture not found"}
    catalog.close()


def test_versions_published_before_pictures_existed_get_them_from_the_stored_package(tmp_path):
    store, catalog, _ = published(tmp_path, [with_pictures(tmp_path)])
    with store.connection() as db:
        stored = decode(db.execute("SELECT inspection FROM public_versions").fetchone()[0])
        del stored["pictures"]
        db.execute("UPDATE public_versions SET inspection=?", (canonical(stored),))
        db.execute("DELETE FROM public_pictures")
    with TestClient(app(store, catalog, approved=[("alice/notes", ICON_SHA)]), base_url=ORIGIN) as client:
        assert client.get(picture_url("alice/notes", ICON_SHA)).content == ICON  # before any catalog read
        assert client.get(PREFIX + "/catalog").json()["entries"][0]["icon"]["sha256"] == ICON_SHA
    catalog.close()


def test_a_refused_publish_leaves_no_picture_behind(tmp_path):
    store, catalog, trust = published(tmp_path, [with_pictures(tmp_path)])
    other_icon = saved(gradient(256, 256).rotate(90), "PNG")
    # Same id and version with different bytes: listed by another catalog, refused by the store as immutable.
    changed = skill_package(tmp_path / "changed", "alice/notes", pictures={"icon": ("assets/icon.png", other_icon)})
    rival = TrustedCatalog(trust.root, tmp_path / "trust" / "rival", now=NOW)
    rival.accept(signed_catalog([changed], trust.policy, trust.keys), now=NOW)
    with pytest.raises(RegistryError, match="immutable"):
        store.publish(rival, "alice/notes", "1.0.0", TARGET, changed, now=NOW)
    with store.connection() as db:
        stored = {row[0] for row in db.execute("SELECT sha256 FROM public_pictures")}
    assert stored == {ICON_SHA, BANNER_SHA}
    rival.close()
    catalog.close()


def test_shipped_curation_starts_with_no_approved_pictures():
    assert load_curation()["approved_pictures"] == []


def curation_file(tmp_path, pictures):
    path = tmp_path / "curation.json"
    path.write_text(json.dumps({"schema": "vool.registry.curation.v1", "official_namespaces": [], "featured": [],
                                "editors_picks": [], "reviewers": {}, "reviews": [], "approved_pictures": pictures}))
    return path


@pytest.mark.parametrize("value, message", [
    ([{"id": "alice/notes", "sha256": "a" * 64}] * 2, "duplicate approved picture"),
    (["a" * 64], "invalid approved pictures"),
    ([{"id": "alice/notes", "sha256": "not-a-checksum"}], "invalid approved pictures"),
    ([{"id": "Alice Notes", "sha256": "a" * 64}], "invalid curated package id"),
    ([{"id": "alice/notes", "sha256": "a" * 64, "kind": "icon"}], "invalid approved pictures"),
    ([{"id": "alice/notes", "sha256": "a" * 64}] * 4001, "invalid approved pictures"),
])
def test_invalid_approved_pictures_refuse_to_load(tmp_path, value, message):
    with pytest.raises(RegistryError, match=message):
        load_curation(curation_file(tmp_path, value))


def test_the_curation_file_has_room_for_every_approval_it_allows(tmp_path):
    pictures = [{"id": f"publisher-{index}/skill-{index}", "sha256": hashlib.sha256(str(index).encode()).hexdigest()}
                for index in range(4000)]
    assert len(load_curation(curation_file(tmp_path, pictures))["approved_pictures"]) == 4000


def git_repo(tmp_path, files):
    repo = tmp_path / "skills-repo"
    for name, data in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_bytes(data if isinstance(data, bytes) else data.encode())
    identity = ["-c", "user.name=sls_0x", "-c", "user.email=240776969+Parad0x-Labs@users.noreply.github.com"]
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), *identity, "commit", "-q", "-m", "skill"], check=True)
    return repo


def test_github_import_takes_pictures_by_their_path_in_the_imported_folder(tmp_path):
    repo = git_repo(tmp_path, {"skills/notes/SKILL.md": SKILL, "skills/notes/LICENSE": "Test fixture licence.\n",
                               "skills/notes/assets/icon.png": ICON, "skills/notes/banner.png": BANNER})
    store, catalog, _ = published(tmp_path, [])
    alice, token, csrf = account(store, "1", "alice")
    body = import_body(metadata={**METADATA, "icon": "assets/icon.png", "banner": "banner.png"})
    with TestClient(app(store, catalog, github=LocalGitHub(repo, 1)), base_url=ORIGIN) as client:
        client.cookies.set("vool_registry", token)
        response = client.post(PREFIX + "/imports", json=body, headers=mutation(csrf))
        assert response.status_code == 201, response.text
    with store.connection() as db:
        artifact = inspect(db.execute("SELECT artifact FROM submissions").fetchone()[0])
    assert artifact["metadata"]["icon"] == {"path": "skills/notes/assets/icon.png", "sha256": ICON_SHA}
    assert artifact["metadata"]["banner"] == {"path": "skills/notes/banner.png", "sha256": BANNER_SHA}
    assert artifact["files"]["skills/notes/assets/icon.png"] == ICON
    catalog.close()


@pytest.mark.parametrize("change, message", [
    ({"icon": "assets/missing.png"}, "not in the imported folder"),
    ({"icon": {"path": "assets/icon.png", "sha256": "a" * 64}}, "by its path in the folder"),
    ({"icon": "../skills/notes/assets/icon.png"}, "ambiguous package path"),
    ({"icon": "assets/notes.txt"}, "must be a .png file"),
])
def test_github_import_picture_refusals(tmp_path, change, message):
    repo = git_repo(tmp_path, {"skills/notes/SKILL.md": SKILL, "skills/notes/LICENSE": "Test fixture licence.\n",
                               "skills/notes/assets/icon.png": ICON, "skills/notes/assets/notes.txt": "text"})
    store, catalog, _ = published(tmp_path, [])
    alice, _, _ = account(store, "1", "alice")
    with pytest.raises(RegistryError, match=message):
        store.github_import(alice, import_body(metadata={**METADATA, **change}), LocalGitHub(repo, 1), now=NOW)
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM submissions").fetchone()[0] == 0
    catalog.close()


def test_a_skill_written_on_the_website_cannot_name_a_picture(tmp_path):
    store, catalog, _ = published(tmp_path, [])
    alice, _, _ = account(store, "1", "alice")
    with pytest.raises(RegistryError, match="no picture files"):
        store.draft(alice, {"metadata": {**METADATA, "icon": {"path": "icon.png", "sha256": ICON_SHA}},
                            "skill_markdown": SKILL, "license_text": "Test fixture licence.\n"}, now=NOW)
    catalog.close()


# -- operators ---------------------------------------------------------------------------------


def service_config(tmp_path, packages, trust):
    """A complete service configuration on disk, as an operator writes it."""
    (tmp_path / "artifacts").mkdir()
    for raw in packages:
        (tmp_path / "artifacts" / (inspect(raw)["sha256"] + ".json")).write_bytes(raw)
    (tmp_path / "root.json").write_bytes(canonical(trust.root))
    (tmp_path / "catalog.json").write_bytes(canonical(
        {"entries": [], "registry": signed_catalog(packages, trust.policy, trust.keys)}))
    config = tmp_path / "service.json"
    config.write_text(json.dumps({
        "schema": "vool.registry.service.v1", "origin": ORIGIN, "private_dir": str(tmp_path / "private"),
        "trusted_root": str(tmp_path / "root.json"), "catalog_file": str(tmp_path / "catalog.json"),
        "artifact_dir": str(tmp_path / "artifacts"), "target": TARGET, "providers": []}))
    return config


def test_operators_list_and_extract_pictures_waiting_for_a_look(tmp_path, monkeypatch, capsys):
    from registry_service import cli, curation

    packages = [with_pictures(tmp_path), skill_package(tmp_path, "bob/plain")]
    policy, keys, root = trust_fixture()
    config = service_config(tmp_path, packages, SimpleNamespace(policy=policy, keys=keys, root=root))
    monkeypatch.setattr(cli, "time", SimpleNamespace(time=lambda: NOW))
    assert cli.main(["check-config", "--config", str(config)]) == 0  # also publishes the listed versions
    assert "curation verified" in capsys.readouterr().out

    assert cli.main(["pictures-pending", "--config", str(config), "--target", str(tmp_path / "review")]) == 0
    assert json.loads(capsys.readouterr().out) == [
        {"id": "alice/notes", "version": "1.0.0", "kind": "icon", "sha256": ICON_SHA, "width": 256, "height": 256,
         "bytes": len(ICON)},
        {"id": "alice/notes", "version": "1.0.0", "kind": "banner", "sha256": BANNER_SHA, "width": 1500, "height": 500,
         "bytes": len(BANNER)}]
    assert (tmp_path / "review" / (ICON_SHA + ".png")).read_bytes() == ICON
    assert (tmp_path / "review" / (BANNER_SHA + ".png")).read_bytes() == BANNER
    assert cli.main(["pictures-pending", "--config", str(config), "--target", str(tmp_path / "review")]) == 1
    assert "choose a new folder" in capsys.readouterr().out  # never writes into an existing folder

    # A curation file that cannot load stops the service at check-config, not later at serve.
    broken = curation_file(tmp_path, [{"id": "alice/notes", "sha256": "a" * 64}] * 2)
    real = curation.load_curation
    monkeypatch.setattr(curation, "load_curation", lambda path=broken: real(path))
    assert cli.main(["check-config", "--config", str(config)]) == 1
    assert "startup refused" in capsys.readouterr().out


# -- the VOOL app ------------------------------------------------------------------------------


def native_client(monkeypatch, tmp_path, service, trust_root, *, tamper=None, fresh_run=True):
    from core.registry import client as native
    from core.registry.client import Client
    from tests.registry.audit_helpers import runtime

    runtime(tmp_path, monkeypatch)
    if fresh_run:
        monkeypatch.setattr(native, "_PICTURES", {})
        monkeypatch.setattr(native, "_LISTED", {})
        monkeypatch.setattr(native, "_PICTURE_STATE", {"browsed": False})
    calls = []

    def request(url, *, limit):
        response = service.get(url)
        calls.append((url, response.status_code))
        if response.status_code != 200:
            raise RegistryError("registry unavailable; existing discovery and local skills remain available")
        data = tamper(response.content) if tamper and "/pictures/" in url else response.content
        require(len(data) <= limit, "registry response exceeds byte limit")
        return data

    return Client(trust_root, TARGET, origin=ORIGIN, request=request, now=NOW), calls


def picture_calls(calls):
    return [(url.removeprefix(ORIGIN + PREFIX + "/pictures/"), status) for url, status in calls if "/pictures/" in url]


def test_vool_shows_an_icon_only_when_its_bytes_match_the_signed_listing(tmp_path, monkeypatch):
    from core.registry import client as native

    packages = [with_pictures(tmp_path), skill_package(tmp_path, "mallory/notes-pro", pictures={"icon": ("icon.png", ICON)})]
    store, catalog, trust = published(tmp_path, packages)
    with TestClient(app(store, catalog, approved=[("alice/notes", ICON_SHA)]), base_url=ORIGIN) as service:
        client, calls = native_client(monkeypatch, tmp_path, service, trust.root)
        with pytest.raises(RegistryError, match="browse Skill Codex first"):
            client.picture("alice/notes", ICON_SHA, approved=True, now=NOW)
        with pytest.raises(RegistryError, match="consent"):
            client.picture("alice/notes", ICON_SHA, approved=False, now=NOW)
        assert calls == []

        rows = client.refresh(approved=True, now=NOW)["entries"]
        assert {row["id"]: row["icon"]["sha256"] for row in rows} == {"alice/notes": ICON_SHA, "mallory/notes-pro": ICON_SHA}
        shown = client.picture("alice/notes", ICON_SHA, approved=True, now=NOW)
        assert shown == {"status": "ok", "id": "alice/notes", "sha256": ICON_SHA, "type": "image/png",
                         "data": base64.b64encode(ICON).decode("ascii")}
        assert picture_calls(calls) == [("alice/notes/" + ICON_SHA, 200)]
        assert client.picture("alice/notes", ICON_SHA, approved=True, now=NOW) == shown
        assert len(picture_calls(calls)) == 1  # kept in memory since this Browse, not fetched again

        # The same file on another skill is asked for again, and the registry says no person approved it there.
        with pytest.raises(RegistryError, match="unavailable"):
            client.picture("mallory/notes-pro", ICON_SHA, approved=True, now=NOW)
        assert picture_calls(calls)[-1] == ("mallory/notes-pro/" + ICON_SHA, 400)
        with pytest.raises(RegistryError, match="unavailable"):
            client.picture("mallory/notes-pro", ICON_SHA, approved=True, now=NOW)  # nothing kept from the refusal
        assert len(picture_calls(calls)) == 3

        before = len(calls)
        for package_id, sha, message in (("alice/notes", hashlib.sha256(b"elsewhere").hexdigest(), "not part of a listed skill"),
                                         ("alice/notes", BANNER_SHA, "not part of a listed skill"),  # the app shows icons only
                                         ("alice/notes", "../../etc/passwd", "invalid picture"),
                                         ("bob/other", ICON_SHA, "not part of a listed skill")):
            with pytest.raises(RegistryError, match=message):
                client.picture(package_id, sha, approved=True, now=NOW)
        assert len(calls) == before  # nothing outside the signed listing is ever requested
        with pytest.raises(RegistryError, match="out of date"):
            client.picture("alice/notes", ICON_SHA, approved=True, now=NOW + 4000)
        client.close()

        # A restart forgets the Browse: the snapshot on disk alone never sends a picture request.
        restarted, calls = native_client(monkeypatch, tmp_path, service, trust.root)
        with pytest.raises(RegistryError, match="browse Skill Codex first"):
            restarted.picture("alice/notes", ICON_SHA, approved=True, now=NOW)
        assert calls == []
        restarted.close()

        for folder, tamper, message in (
                ("changed", lambda data: saved(gradient(256, 256).rotate(180), "PNG"), "differs from the signed listing"),
                ("oversized", lambda data: data + bytes(300 * 1024), "exceeds byte limit")):
            tampered, calls = native_client(monkeypatch, tmp_path / folder, service, trust.root, tamper=tamper)
            tampered.refresh(approved=True, now=NOW)
            with pytest.raises(RegistryError, match=message):
                tampered.picture("alice/notes", ICON_SHA, approved=True, now=NOW)
            assert native._PICTURES == {}  # a refused picture is never kept
            tampered.close()
    catalog.close()


def test_taking_an_approval_away_reaches_a_running_vool_at_the_next_browse(tmp_path, monkeypatch):
    store, catalog, trust = published(tmp_path, [with_pictures(tmp_path)])
    with TestClient(app(store, catalog, approved=[("alice/notes", ICON_SHA)]), base_url=ORIGIN) as service:
        client, calls = native_client(monkeypatch, tmp_path, service, trust.root)
        client.refresh(approved=True, now=NOW)
        assert client.picture("alice/notes", ICON_SHA, approved=True, now=NOW)["sha256"] == ICON_SHA
        client.close()
    with TestClient(app(store, catalog), base_url=ORIGIN) as service:
        client, calls = native_client(monkeypatch, tmp_path, service, trust.root, fresh_run=False)
        client.refresh(approved=True, now=NOW)
        with pytest.raises(RegistryError, match="unavailable"):
            client.picture("alice/notes", ICON_SHA, approved=True, now=NOW)
        assert picture_calls(calls) == [("alice/notes/" + ICON_SHA, 400)]
        client.close()
    catalog.close()


def test_the_page_cannot_hand_the_app_a_picture_address():
    from core.web.api.addon_api import handle_addon_post

    body = {"action": "registry_picture", "id": "alice/notes", "sha256": ICON_SHA, "approved": True}
    headers = {"content-type": "application/json"}
    status, result = handle_addon_post({**body, "url": "https://elsewhere.test/pixel.png"}, headers, "127.0.0.1")
    assert status == 400 and result["error"] == "invalid_action"
    status, result = handle_addon_post(body, headers, "127.0.0.1")
    assert result["error"] == "registry_refused"  # the action itself exists; this test build has no registry root


def test_skill_cards_show_the_creator_icon_or_our_own_tile_and_search_sends_nothing(tmp_path, monkeypatch):
    from core import plugin_lifecycle
    from core.registry import client as native
    from core.vool_chat_page import render_vool_chat_html
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get, dispatch_post
    from tests.served_browser import launch_chromium

    pending = saved(gradient(256, 256).rotate(90), "PNG")
    packages = [with_pictures(tmp_path),
                skill_package(tmp_path, "bob/pending", pictures={"icon": ("icon.png", pending)}),
                skill_package(tmp_path, "carol/plain")]
    store, catalog, trust = published(tmp_path, packages)
    service = TestClient(app(store, catalog, approved=[("alice/notes", ICON_SHA), ("alice/notes", BANNER_SHA)]),
                         base_url=ORIGIN)
    client, calls = native_client(monkeypatch, tmp_path, service, trust.root)
    client.close()
    monkeypatch.setenv("VOOL_NATIVE_SKILLS_DIR", str(tmp_path / "no-native"))
    monkeypatch.setattr(native, "time", SimpleNamespace(time=lambda: NOW))
    monkeypatch.setattr(native, "configured_client", lambda: native.Client(
        trust.root, TARGET, origin=ORIGIN, request=client.request, now=NOW))
    runtime = RuntimeServices(display_name="VOOL")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, code, body, content_type="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/chat":
                return self.reply(200, render_vool_chat_html().encode(), "text/html")
            if path in {"/api/addons", "/api/plugins", "/api/intake/providers"}:
                result = dispatch_get(path=path, query={}, runtime=runtime, model_name="fixture", client_host="127.0.0.1")
                return self.reply(result.status, result.body)
            return self.reply(200, b"{}")

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if self.path != "/api/addons":
                return self.reply(200, b"{}")
            result = dispatch_post(path=self.path, body=body, headers=dict(self.headers), runtime=runtime, model_name="fixture",
                workspace_root_provider=lambda: str(tmp_path), client_host="127.0.0.1")
            return self.reply(result.status, result.body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    manager, browser = launch_chromium()
    try:
        page = browser.new_page(viewport={"width": 1280, "height": 960})
        outside = []
        page.on("request", lambda request: None if request.url.startswith(("http://127.0.0.1", "data:")) else outside.append(request.url))
        page.goto(f"http://127.0.0.1:{server.server_port}/chat", wait_until="domcontentloaded")
        page.locator("#homeToggle").click()
        page.get_by_role("button", name="Skills & Plugins", exact=True).click()
        page.locator("#addonNav").get_by_role("button", name="Skill Codex", exact=True).click()
        search = page.locator("#pluginsSearch")
        search.fill("bob")
        page.wait_for_timeout(300)
        assert calls == []  # opening the tab and typing send nothing
        search.fill("")
        page.get_by_role("button", name="Browse Skill Codex", exact=True).click()

        def card(name):
            return page.locator("#pluginsBody .addon-card").filter(has=page.get_by_role("heading", name=name, exact=True))

        approved = card("alice/notes").locator(".addon-card-head .addon-icon img")
        approved.wait_for()
        page.wait_for_function("img => img.complete && img.naturalWidth > 0", arg=approved.element_handle())
        assert approved.get_attribute("src") == "data:image/png;base64," + base64.b64encode(ICON).decode("ascii")
        assert approved.evaluate("img => [img.naturalWidth, img.naturalHeight, img.alt]") == [256, 256, ""]
        for name in ("bob/pending", "carol/plain"):
            tile = card(name).locator(".addon-card-head .addon-icon.skill-missing")
            tile.wait_for()
            assert tile.locator("svg").is_visible() and tile.locator("img").count() == 0
        assert card("carol/plain").get_attribute("aria-labelledby") == card("carol/plain").locator("h3").get_attribute("id")
        assert sorted(picture_calls(calls)) == sorted([("alice/notes/" + ICON_SHA, 200),
                                                       ("bob/pending/" + hashlib.sha256(pending).hexdigest(), 400)])
        for text in ("carol", "bob", "alice", ""):
            search.fill(text)
            page.wait_for_timeout(150)
        assert len(picture_calls(calls)) == 2  # searching redraws cards from what Browse already fetched
        assert card("alice/notes").locator(".addon-icon img").count() == 1
        assert outside == []  # pictures arrive through VOOL as checked data, never from another address

        card("alice/notes").get_by_role("button", name="Download for inspection", exact=True).click()
        page.get_by_role("button", name="Install and enable", exact=True).wait_for()
        assert page.locator("#pluginsBody img").count() == 0  # the install review stays free of creator art
        assert not plugin_lifecycle.records()
    finally:
        browser.close()
        manager.stop()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        service.close()
        catalog.close()
