"""One listing object, ``vool.listing.v1``, for a skill or a plugin: what the website and VOOL both show.

Built only from what the service already holds. Author text comes from the package (its signed row and
the listing block in its own registry.json); what the package asks for comes from its setup file, never
from what the author wrote about it; ratings, badges and team notices come from their own verified
sources. The schema is docs/reference/listing-standard-v1.schema.json.
"""
from __future__ import annotations

import hashlib

from core.registry import badges, listing
from core.registry.categories import TAXONOMY
from core.registry.codec import RegistryError, version_key

SCHEMA = "vool.listing.v1"
_EFFECTS = {"read_only", "workspace_write", "network_send", "network_publish"}
_KINDS = ("tools", "model_provider", "scanner")
_HANDLES = ("github", "x", "mastodon", "website")
PAUSED_NOTICE = "Downloads are paused while a person looks into a report."


def facts(artifact: dict) -> dict:
    """What one published package says about itself, read once and kept with it."""
    from core.registry.validation import manifest_authority

    authority = manifest_authority(artifact["manifest"])
    block = artifact["manifest"].get("vool") if isinstance(artifact["manifest"].get("vool"), dict) else {}
    described = {str(tool.get("intent")): tool.get("description") for tool in block.get("tools", [])
                 if isinstance(tool, dict)} if isinstance(block.get("tools"), list) else {}
    credentials = authority.get("credentials") if isinstance(authority.get("credentials"), dict) else {}
    tools = []
    for tool in authority["tools"]:
        effect = tool["side_effect_class"]
        # A class the listing has no word for reads as the careful one: VOOL asks every time.
        row = {"intent": tool["intent"], "effect": effect if effect in _EFFECTS else "external_unpinned",
               "approval": tool["approval_requirement"]}
        if isinstance(described.get(tool["intent"]), str) and described[tool["intent"]].strip():
            row["description"] = described[tool["intent"]][:2000]
        tools.append(row)
    access = {
        "network": sorted({str(host)[:253] for host in authority.get("network", [])})[:32],
        "credentials": [{"slot": str(slot), "label": str(spec.get("label", slot))[:120]} |
                        ({"help_url": spec["help_url"]} if isinstance(spec.get("help_url"), str) and
                         spec["help_url"].startswith("https://") else {})
                        for slot, spec in list(credentials.items())[:32] if isinstance(spec, dict)],
        "permissions": sorted(set(authority["permissions"]))[:128], "tools": tools[:128],
        "runs_programs": bool(authority.get("mcp_servers")) or any(
            tool["handler"].get("kind") in {"subprocess", "mcp"} for tool in authority["tools"]),
    }
    return {"listing": listing.kept(artifact["files"]), "access": access,
            "plugin_kinds": [kind for kind in _KINDS if kind in authority.get("kind", [])],
            "license_sha256": hashlib.sha256(artifact["files"]["LICENSE"]).hexdigest()}


def badge_facts(stored: dict, root: dict, package_id: str, version: str, sha256: str, kind: str, *, now: int) -> dict:
    """The two team badges of this exact package, from the stored badge document, verified here again.

    Anything that does not verify, has run out or names another checksum, version or kind gives no badge.
    """
    result = {"editors_choice": False, "tested_by_vool": False}
    try:
        issuer, signed, _ = badges.verify_all(stored, root, now=now)
    except (RegistryError, ValueError, TypeError, KeyError):
        return result
    targets = []
    for item in signed["badges"]:
        if (item["digest_type"], item["sha256"], item["kind"], item["id"], item["version"]) != (
                "artifact", sha256, kind, package_id, version):
            continue
        for target in (item["targets"] or [None]):
            held = badges.badges_for(badges.shown(signed, issuer, now=now, target=target), "artifact", sha256,
                                     kind=kind, package_id=package_id, version=version)
            if item["badge"] in held:
                if item["badge"] == "editors_choice":
                    result["editors_choice"] = True
                else:
                    result["tested_by_vool"] = True
                    row = {"version": target["version"], "platform": target["platform"], "date": item["issued_at"]}
                    if row not in targets:
                        targets.append(row)
    if result["tested_by_vool"]:
        result["tested_version"] = version
    return result | ({"_tested_targets": targets[:32]} if targets else {})


def _source(info: dict) -> dict:
    source = info["source"]
    if "repository" not in source:
        return {"type": "upload"}
    return {"type": "github", "repository": source["repository"], "commit": source["commit"], "path": source["path"],
            "url": "https://github.com/" + source["repository"] + "/tree/" + source["commit"] + "/" + source["path"]}


def _rating(summary: dict | None) -> dict:
    if not summary:
        return {"shown": False, "count": 0, "average": None, "scope": "all_versions", "frozen": False,
                "removed_by_team": 0}
    result = {"shown": summary["shown"], "count": summary["count"], "average": summary["average"],
              "scope": "current_major" if summary["scope"] == "current_major" else "all_versions",
              "frozen": summary["frozen"], "removed_by_team": summary["removed_by_team"]}
    if summary["distribution"] is not None:
        result["distribution"] = summary["distribution"]
    return result


def changelog(versions: list[dict]) -> list[dict]:
    """Newest first, from each published version's own notes."""
    ordered = sorted(versions, key=lambda item: version_key(item["version"]), reverse=True)[:50]
    return [{"version": item["version"], "date": item["date"]} | ({"notes": item["notes"]} if item.get("notes") else {})
            for item in ordered]


def document(row: dict, kept: dict, *, package_bytes: int, versions: list[dict], rating: dict | None,
             badge: dict, verified: dict, notices: list[dict], undeclared: list[str] | None = None) -> dict:
    """The object for one listed add-on. ``row`` is its catalog row; ``kept`` what ``facts`` kept for its package."""
    author = kept.get("listing") or {}
    category = TAXONOMY.get(row["category"], ("", {}))
    profile = row["creator"]
    creator = {"namespace": row["id"].split("/")[0], "display_name": row["id"].split("/")[0], "origin": row["origin"],
               "verified": verified}
    if profile.get("bio"):
        creator["bio"] = profile["bio"][:500]
    handles = {key: profile["handles"][key] for key in _HANDLES if isinstance(profile.get("handles", {}).get(key), str)}
    if handles:
        creator["links"] = handles
    compatibility = dict(author.get("compatibility", {}), checked_targets=row["targets"][:32])
    if badge.get("_tested_targets"):
        compatibility["tested_targets"] = badge["_tested_targets"]
    requirements = row["requirements"]
    result = {
        "schema": SCHEMA, "kind": row["kind"], "id": row["id"], "name": row.get("title") or row["id"].split("/")[1],
        "tagline": row["summary"], "category": row["category"], "subcategory": row["subcategory"],
        "category_label": category[0], "subcategory_label": category[1].get(row["subcategory"], ""),
        "creator": creator, "version": row["version"], "sha256": row["sha256"], "package_bytes": package_bytes,
        "changelog": changelog(versions), "compatibility": compatibility, "needs": list(row.get("needs", [])),
        "access": dict(kept["access"], dependencies=list(requirements["dependencies"])[:64]),
        "pictures": {kind: row[kind] for kind in ("icon", "banner") if kind in row} |
                    ({"pending": row["pictures_pending"]} if row.get("pictures_pending") else {}),
        "license": {"id": row["license"], "sha256": kept["license_sha256"]}, "source": _source(row),
        # No count of installs is kept: a pass is handed out with a download and nothing is written down.
        "counts": {"installs": None, "download_requests": row["download_requests"], "saves": row["stars"]},
        "rating": _rating(rating),
        "badges": {key: value for key, value in badge.items() if not key.startswith("_")} |
                  {"featured": bool(row.get("featured")), "highlighted": bool(row.get("highlighted"))},
        "status": ({"state": "downloads_paused", "reason": "under_review", "notice": PAUSED_NOTICE}
                   if row.get("downloads_paused") else {"state": "listed"}),
        "dates": {"published_at": row["published_at"], "updated_at": row["updated_at"]},
        "notices": {"authority": row["authority_notice"], "native_control_overlaps": row["native_control_overlaps"],
                    "moderation": notices} | ({"undeclared_needs": undeclared} if undeclared else {}),
    }
    if "languages" in row:
        result["languages"] = row["languages"]
    if "description" in author:
        result["description"] = author["description"]
    if "links" in author:
        result["links"] = author["links"]
    if row["kind"] == "plugin":
        result["plugin_kinds"] = kept["plugin_kinds"]
    return result
