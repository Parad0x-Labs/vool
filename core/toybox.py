"""Inert local skin manifests and their revisioned cosmetic selection.

Contributor: sls_0x. Skins contain bounded colours and shipped frame presets,
never CSS, scripts, paths, images, provider settings or runtime behaviour.

Renderer contract "1.0" is the original preset vocabulary. "1.1" adds the
`serif-heading` typography preset and nothing else. "1.2" adds the `toon`, `capsule` and
`chamfer` frames, the `neon`, `sakura` and `xeno` ornaments and the `rounded-heading`
typography preset. All stay schema version 1, and each contract accepts exactly its own
value sets.
"""

from __future__ import annotations

import hashlib
import json
import re
from contextlib import closing, contextmanager

MAX_BYTES = 16384
MAX_INSTALLED = 25
PALETTE = {"background", "panel", "field", "ink", "muted", "accent", "accentInk", "border"}
_V10 = {
    "frame": ("pixel", "etched", "flat"),
    "density": ("comfortable", "compact"),
    "typography": ("system", "mono-heading"),
    "ornament": ("none", "voxel", "leaf"),
}
_V11 = {**_V10, "typography": _V10["typography"] + ("serif-heading",)}
_V12 = {
    **_V11,
    "frame": _V11["frame"] + ("toon", "capsule", "chamfer"),
    "typography": _V11["typography"] + ("rounded-heading",),
    "ornament": _V11["ornament"] + ("neon", "sakura", "xeno"),
}
PRESETS = {"1.0": _V10, "1.1": _V11, "1.2": _V12, "2.0": _V12}


class SkinError(ValueError):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.status = code, status


def _require(condition, message, code="skin_invalid", status=400):
    if not condition:
        raise SkinError(code, message, status)


def _keys(value, keys):
    _require(type(value) is dict and set(value) == set(keys), "Unsupported or missing skin fields.")


def _text(value, maximum):
    _require(
        type(value) is str
        and 0 < len(value) <= maximum
        and all(32 <= ord(c) < 127 for c in value)
        and "<" not in value
        and ">" not in value,
        "Use bounded plain text for skin metadata.",
    )


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, "Duplicate JSON fields are refused.")
        result[key] = value
    return result


def contrast(a, b):
    def luminance(colour):
        channels = [int(colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
        values = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in channels]
        return sum(w * v for w, v in zip((0.2126, 0.7152, 0.0722), values, strict=True))

    a, b = sorted((luminance(a), luminance(b)))
    return (b + 0.05) / (a + 0.05)


def validate(raw):
    """Return canonical data and digest without writing or resolving any asset."""
    _require(
        type(raw) is str and len(raw.encode("utf-8")) <= MAX_BYTES, "Skin JSON must be at most 16 KiB.", "skin_size"
    )
    try:
        value = json.loads(raw, object_pairs_hook=_pairs)
    except (ValueError, RecursionError) as exc:
        if isinstance(exc, SkinError):
            raise
        raise SkinError("skin_invalid", "The skin is not valid JSON.") from exc
    required = {
            "schema_version",
            "renderer_contract",
            "kind",
            "id",
            "version",
            "name",
            "author",
            "description",
            "license",
            "assets",
            "skin",
        }
    _require(type(value) is dict, "Skin must be an object.")
    optional = {"stage", "palettes"} if value.get("renderer_contract") == "2.0" else set()
    _require(required <= set(value) and not set(value) - required - optional, "Unsupported or missing skin fields.")
    _require(
        type(value["schema_version"]) is int
        and value["schema_version"] == 1
        and type(value["renderer_contract"]) is str
        and value["renderer_contract"] in PRESETS
        and value["kind"] == "skin",
        "This build accepts version-1 app skins with renderer contract 1.0, 1.1 or 1.2.",
    )
    _require(
        type(value["id"]) is str and re.fullmatch(r"[a-z][a-z0-9-]{0,63}", value["id"]), "Invalid skin identifier."
    )
    _require(
        type(value["version"]) is str and re.fullmatch(r"[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}", value["version"]),
        "Invalid skin version.",
    )
    for field, size in (("name", 64), ("author", 64), ("description", 300), ("license", 64)):
        _text(value[field], size)
    # The Toybox status line shows the active skin's name; one named like the default would
    # read exactly like the restored state, so the importer refuses the impersonation.
    _require(
        " ".join(value["name"].lower().split()) != "vool default",
        "A skin cannot present itself as VOOL's default look.",
    )
    if value["renderer_contract"] == "2.0":
        from core.toybox_stage import validate_stage

        validate_stage(value.get("stage", {}), value["assets"], _require)
    else:
        _require(value["assets"] == [], "App skins use data only; imported assets are not supported.")
    skin = value["skin"]
    _keys(skin, {"palette", "frame", "density", "corners", "borderWidth", "gap", "typography", "ornament"})
    _keys(skin["palette"], PALETTE)
    for colour in skin["palette"].values():
        _require(
            type(colour) is str and re.fullmatch(r"#[0-9a-fA-F]{6}", colour), "Colours must be six-digit hex values."
        )
    for field, choices in PRESETS[value["renderer_contract"]].items():
        _require(type(skin[field]) is str and skin[field] in choices, "Use a supported cosmetic preset.")
    for field, low, high in (("corners", 0, 12), ("borderWidth", 1, 3), ("gap", 6, 16)):
        _require(type(skin[field]) is int and low <= skin[field] <= high, "Cosmetic sizes must be bounded integers.")
    colours = skin["palette"]
    for field in ("background", "panel", "field"):
        for ink in ("ink", "muted"):
            _require(
                contrast(colours[field], colours[ink]) >= 4.5,
                "Text contrast must be at least 4.5:1 on every surface.",
                "skin_contrast",
            )
    _require(
        contrast(colours["accent"], colours["accentInk"]) >= 4.5
        and contrast(colours["border"], colours["background"]) >= 3
        and contrast(colours["border"], colours["panel"]) >= 3
        and contrast(colours["border"], colours["field"]) >= 3,
        "Button text and control borders need readable contrast.",
        "skin_contrast",
    )
    if "palettes" in value:
        _keys(value["palettes"], ("dark", "light"))
        for variant in value["palettes"].values():
            _keys(variant, PALETTE)
            child = {**value, "assets": [], "renderer_contract": "1.2", "skin": {**skin, "palette": variant}}
            child.pop("stage", None)
            child.pop("palettes", None)
            validate(json.dumps(child))
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    _require(len(canonical.encode()) <= MAX_BYTES, "Skin JSON must be at most 16 KiB after PNG normalization.", "skin_size")
    return value, canonical, hashlib.sha256(canonical.encode()).hexdigest()


def _example(identifier, name, description, palette, frame, ornament, corners, border, typography, contract="1.1", version="2.0.0"):
    return {
        "schema_version": 1,
        "renderer_contract": contract,
        "kind": "skin",
        "id": identifier,
        "version": version,
        "name": name,
        "author": "sls_0x",
        "description": description,
        "license": "CC0-1.0",
        "assets": [],
        "skin": {
            "palette": palette,
            "frame": frame,
            "density": "comfortable",
            "corners": corners,
            "borderWidth": border,
            "gap": 12,
            "typography": typography,
            "ornament": ornament,
        },
    }


EXAMPLES = (
    _example(
        "voxel-workshop",
        "Voxel Workshop",
        "Crafted-block workshop: dressed stone, oak shelves and a turf edge, with bevelled pixel buttons and inset slots. Original art.",
        dict(
            background="#121412",
            panel="#2b2d2b",
            field="#1c1e1c",
            ink="#f3f1e7",
            muted="#bdbcae",
            accent="#83c247",
            accentInk="#10200a",
            border="#8e918a",
        ),
        "pixel",
        "voxel",
        0,
        2,
        "mono-heading",
    ),
    _example(
        "forest-relic",
        "Forest Relic",
        "Woodland survey kit: moss and ink, brass-cornered panels, map contours, leaf markers and engraved serif titles. Original art.",
        dict(
            background="#0f1613",
            panel="#18211d",
            field="#1f2a25",
            ink="#f0e7cf",
            muted="#c4bc9f",
            accent="#d6b36a",
            accentInk="#221906",
            border="#8f7f55",
        ),
        "etched",
        "leaf",
        4,
        1,
        "serif-heading",
    ),
    _example(
        "neon-courier",
        "Neon Courier",
        "Retro cartoon future: a neon night city with tube transit and saucer towers, cel-shaded cartoon controls, glowing signs and a flying car. Original art.",
        dict(background="#0a1222", panel="#0f1b31", field="#15253f", ink="#f3fbff", muted="#a8c3d8",
             accent="#3ff0c4", accentInk="#04241b", border="#6f8fb3"),
        "toon", "neon", 10, 2, "rounded-heading", "1.2", "1.0.0",
    ),
    _example(
        "sakura-skies",
        "Sakura Skies",
        "Anime golden hour: a pastel sky with cel-shaded clouds and a blossom branch, glossy pastel controls, sparkles, drifting petals and a shooting star. Original art.",
        dict(background="#fcf7fb", panel="#ffffff", field="#f7effa", ink="#2b1d40", muted="#5b4b74",
             accent="#ff7eb6", accentInk="#3d0b26", border="#8a6fae"),
        "capsule", "sakura", 12, 1, "rounded-heading", "1.2", "1.0.0",
    ),
    _example(
        "xeno-abyss",
        "Xeno Abyss",
        "Deep-space alien bio-tech: a nebula, a ringed alien world, bioluminescent tendrils, glowing glyph bands and chamfered holo-glass controls. Original art.",
        dict(background="#05060d", panel="#0a0e1c", field="#0e1428", ink="#e8f7ff", muted="#93b3c8",
             accent="#3dffb8", accentInk="#00180f", border="#4f8199"),
        "chamfer", "xeno", 8, 1, "mono-heading", "1.2", "1.0.0",
    ),
)


@contextmanager
def _db():
    from storage.db import get_connection

    with closing(get_connection()) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS toybox_skin_objects (digest TEXT PRIMARY KEY, manifest TEXT NOT NULL)")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS toybox_skin_selection (singleton INTEGER PRIMARY KEY CHECK(singleton=1), revision INTEGER NOT NULL, digest TEXT NOT NULL)"
        )
        conn.execute("CREATE TABLE IF NOT EXISTS toybox_stage_selection (singleton INTEGER PRIMARY KEY CHECK(singleton=1), motion TEXT NOT NULL, layout_override TEXT NOT NULL)")
        conn.commit()
        yield conn


def _selection(conn):
    row = conn.execute("SELECT revision,digest FROM toybox_skin_selection WHERE singleton=1").fetchone()
    return (int(row[0]), str(row[1])) if row else (0, "")


def state():
    from core.toybox_fragment import skin_css
    from core.toybox_materials import thumbnail
    from core.toybox_registry_v2 import manifests

    builtins = [validate(json.dumps(example)) for example in EXAMPLES] + manifests()
    builtin_hashes = {item[2] for item in builtins}
    choices = [{"manifest": item[0], "digest": item[2], "builtin": True} for item in builtins]
    with _db() as conn:
        revision, digest = _selection(conn)
        preference = conn.execute("SELECT motion,layout_override FROM toybox_stage_selection WHERE singleton=1").fetchone()
        motion, layout_override = (str(preference[0]), str(preference[1])) if preference else ("full", "skin")
        if motion not in ("full", "calm", "off") or layout_override not in ("skin", "standard"):
            motion, layout_override = "off", "standard"
        selected, error = None, ""
        for row in conn.execute("SELECT digest,manifest FROM toybox_skin_objects ORDER BY digest"):
            try:
                manifest, _, actual = validate(row[1])
                _require(actual == row[0], "Saved skin hash does not match.", "skin_tampered")
                if row[0] == digest:
                    selected = manifest
                if row[0] not in builtin_hashes:
                    choices.append({"manifest": manifest, "digest": actual, "builtin": False})
            except SkinError:
                if row[0] == digest:
                    error = "Saved skin is damaged. Restore the default or apply another skin."
        if digest and selected is None and not error:
            error = "Saved skin is missing. Restore the default or apply another skin."
    for choice in choices:
        if choice["manifest"]["renderer_contract"] == "2.0":
            from core.toybox_fragment import preview_html
            from core.toybox_stage import thumbnail as stage_thumbnail

            choice["thumbnail"] = stage_thumbnail(choice["manifest"])
            choice["preview"] = preview_html(choice["manifest"])
        else:
            choice["thumbnail"] = thumbnail(choice["manifest"])
    return {
        "ok": True,
        "css": skin_css(selected),
        "revision": revision,
        "digest": digest,
        "selected": selected,
        "choices": choices,
        "warning": error,
        "motion": motion,
        "layout_override": layout_override,
    }


def change(action, body):
    expected_fields = {
        "preview": {"manifest"},
        "apply": {"manifest", "revision", "confirmed"},
        "restore": {"revision", "confirmed"},
        "remove": {"digest", "revision", "confirmed"},
        "configure": {"revision", "confirmed", "motion", "layout_override"},
    }
    _require(action in expected_fields, "Unknown Toybox action.", "skin_action")
    _require(type(body) is dict, "Toybox request must be an object.")
    fields = expected_fields[action]
    _require(fields <= set(body) and not set(body) - fields - {"motion", "layout_override"}, "Unsupported or missing skin fields.")
    for field, choices in (("motion", ("full", "calm", "off")), ("layout_override", ("skin", "standard"))):
        if field in body:
            _require(type(body[field]) is str and body[field] in choices, field + ": unsupported choice.")
    manifest, canonical, digest = None, "", ""
    if action in ("preview", "apply"):
        manifest, canonical, digest = validate(body["manifest"])
    if action == "preview":
        from core.toybox_fragment import preview_html, skin_css

        return {
            "ok": True,
            "manifest": manifest,
            "digest": digest,
            "css": skin_css(manifest),
            "preview": preview_html(manifest),
        }
    _require(
        body["confirmed"] is True and type(body["revision"]) is int and body["revision"] >= 0,
        "Applying a skin needs the displayed revision and explicit confirmation.",
    )
    if action == "remove":
        digest = body["digest"]
        _require(type(digest) is str and re.fullmatch(r"[0-9a-f]{64}", digest), "Invalid skin hash.")
    with _db() as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            revision, selected = _selection(conn)
            _require(
                body["revision"] == revision,
                "Toybox changed in another window. Review and retry.",
                "skin_revision_conflict",
                409,
            )
            if action == "apply":
                present = conn.execute("SELECT manifest FROM toybox_skin_objects WHERE digest=?", (digest,)).fetchone()
                if present:
                    _require(present[0] == canonical, "Saved skin content differs from its hash.", "skin_tampered")
                else:
                    count = conn.execute("SELECT COUNT(*) FROM toybox_skin_objects").fetchone()[0]
                    _require(
                        count < MAX_INSTALLED, "Remove an old skin before adding another.", "skin_inventory_full", 409
                    )
                    conn.execute("INSERT INTO toybox_skin_objects VALUES (?,?)", (digest, canonical))
                selected = digest
            elif action == "restore":
                selected = ""
            elif action == "remove":
                conn.execute("DELETE FROM toybox_skin_objects WHERE digest=?", (digest,))
                if selected == digest:
                    selected = ""
            conn.execute(
                "INSERT INTO toybox_skin_selection VALUES (1,?,?) ON CONFLICT(singleton) DO UPDATE SET revision=excluded.revision,digest=excluded.digest",
                (revision + 1, selected),
            )
            if action == "restore":
                conn.execute("DELETE FROM toybox_stage_selection")
            elif "motion" in body or "layout_override" in body:
                previous = conn.execute("SELECT motion,layout_override FROM toybox_stage_selection WHERE singleton=1").fetchone()
                motion, layout = previous if previous else ("full", "skin")
                conn.execute("INSERT INTO toybox_stage_selection VALUES(1,?,?) ON CONFLICT(singleton) DO UPDATE SET motion=excluded.motion,layout_override=excluded.layout_override",
                             (body.get("motion", motion), body.get("layout_override", layout)))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return state()
