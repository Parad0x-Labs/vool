"""Maintainer curation: VOOL-made namespaces, featured rows, editor's picks and named reviewers.

This file changes only through a reviewed pull request; no HTTP route or database
flag can set any of it. Curation labels never admit a package: rows still come
only from the signed catalog.
"""
from __future__ import annotations

import re
from pathlib import Path

from core.registry.codec import IDENTITY, decode, require

DEFAULT_PATH = Path(__file__).resolve().parent / "curation.json"
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _package_id(value: object) -> str:
    parts = value.split("/") if isinstance(value, str) else []
    require(len(parts) == 2 and all(IDENTITY.fullmatch(part) for part in parts), "invalid curated package id")
    return value


def load_curation(path: Path = DEFAULT_PATH) -> dict:
    value = decode(path.read_bytes(), 65536)
    require(isinstance(value, dict) and set(value) == {"schema", "official_namespaces", "featured", "editors_picks",
            "reviewers", "reviews"} and value["schema"] == "vool.registry.curation.v1", "unknown curation format")
    official = value["official_namespaces"]
    require(isinstance(official, list) and len(official) <= 8 and
            all(isinstance(name, str) and IDENTITY.fullmatch(name) for name in official), "invalid official namespaces")
    for field in ("featured", "editors_picks"):
        require(isinstance(value[field], list) and len(value[field]) <= 24 and
                len(set(map(_package_id, value[field]))) == len(value[field]), f"invalid {field}")
    reviewers = value["reviewers"]
    require(isinstance(reviewers, dict) and len(reviewers) <= 200 and
            all(isinstance(name, str) and IDENTITY.fullmatch(name) and role in {"team", "community"}
                for name, role in reviewers.items()), "invalid reviewers")
    reviews = value["reviews"]
    require(isinstance(reviews, list) and len(reviews) <= 2000, "invalid reviews")
    seen = set()
    for item in reviews:
        require(isinstance(item, dict) and set(item) == {"id", "version", "reviewer", "date"}, "invalid review entry")
        _package_id(item["id"])
        require(isinstance(item["version"], str) and 0 < len(item["version"]) <= 64 and
                item["reviewer"] in reviewers and isinstance(item["date"], str) and bool(_DATE.fullmatch(item["date"])),
                "invalid review entry")
        require(item["reviewer"] != item["id"].split("/")[0], "creators cannot review their own skills")
        key = (item["id"], item["version"], item["reviewer"])
        require(key not in seen, "duplicate review entry")
        seen.add(key)
    return value


def labels(curation: dict, package_id: str, version: str) -> dict:
    namespace = package_id.split("/")[0]
    return {
        "origin": "official" if namespace in curation["official_namespaces"] else "community",
        "featured": package_id in curation["featured"],
        "editors_pick": package_id in curation["editors_picks"],
        "community_reviews": [{"reviewer": item["reviewer"], "role": curation["reviewers"][item["reviewer"]],
                               "version": item["version"], "date": item["date"]}
                              for item in curation["reviews"] if item["id"] == package_id and item["version"] == version],
    }
