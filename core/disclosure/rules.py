"""Owner-confirmed project rules. Proposals alone confer no permission."""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

_LOCK = threading.RLock()
_PROPOSALS: dict[str, tuple[str, float, dict[str, Any]]] = {}


def _path() -> Path:
    from core.runtime_paths import active_config_home_dir
    return active_config_home_dir() / "disclosure-projects.json"


def _load() -> dict:
    try:
        data = json.loads(_path().read_text())
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict):
        raise ValueError("Project cloud rules could not be read. Nothing was sent.")
    return data


def _project(project_id: str) -> dict:
    from core.project_store import get_project
    project = get_project(project_id)
    if project is None:
        raise ValueError("Choose an existing project.")
    return project


def read_rules(project_id: str) -> dict:
    if not project_id:
        return {"revision": 0, "rules": []}
    with _LOCK:
        value = _load().get(project_id, {"revision": 0, "rules": []})
        if not isinstance(value, dict) or type(value.get("revision")) is not int or not isinstance(value.get("rules"), list):
            raise ValueError("Project cloud rules could not be read. Nothing was sent.")
        return value


def list_rules() -> list[dict]:
    from core.project_store import list_projects
    return [{"project_id": p["id"], "name": p["name"], **read_rules(p["id"])} for p in list_projects()]


def propose(project_id: str, rules: list[dict]) -> dict:
    project = _project(project_id)
    if not isinstance(rules, list) or len(rules) > 128:
        raise ValueError("Choose up to 128 source rules.")
    cleaned = []
    for rule in rules:
        if not isinstance(rule, dict) or set(rule) != {"kind", "source", "mode"}:
            raise ValueError("Choose a source and a cloud rule.")
        kind, source, mode = (rule[k] for k in ("kind", "source", "mode"))
        if kind not in {"attachment", "folder", "memory", "tool"} or mode not in {"local", "ask", "allow"}:
            raise ValueError("Choose a supported source and cloud rule.")
        if not isinstance(source, str) or not source or len(source) > 4096:
            raise ValueError("Choose a source.")
        if kind == "folder":
            if source == "*":
                raise ValueError("Choose a folder inside this project; use . for the project folder.")
            root = Path(project["root"]).resolve()
            path = (root / source).resolve()
            if not path.is_relative_to(root):
                raise ValueError("Choose a folder inside this project.")
            source = path.relative_to(root).as_posix()
        elif source != "*" and kind in {"memory", "tool"}:
            raise ValueError("Choose the whole source class.")
        item = {"kind": kind, "source": source, "mode": mode}
        if any(r["kind"] == kind and r["source"] == source for r in cleaned):
            raise ValueError("Choose one rule per source.")
        cleaned.append(item)
    with _LOCK:
        payload = {"project_id": project_id, "revision": read_rules(project_id)["revision"], "rules": cleaned}
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        ident = uuid.uuid4().hex
        _PROPOSALS[ident] = (str(_path()), time.monotonic(), payload)
        for old, (_, created, _) in list(_PROPOSALS.items()):
            if time.monotonic() - created > 300:
                del _PROPOSALS[old]
        if len(_PROPOSALS) > 128:
            del _PROPOSALS[next(iter(_PROPOSALS))]
        return {"id": ident, "sha256": digest, **payload}


def confirm(ident: str, digest: str) -> dict:
    with _LOCK:
        entry = _PROPOSALS.get(ident)
        if entry is None or entry[0] != str(_path()) or time.monotonic() - entry[1] > 300:
            raise ValueError("This proposal expired. Review the rule again.")
        payload = entry[2]
        expected = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        if digest != expected or read_rules(payload["project_id"])["revision"] != payload["revision"]:
            raise ValueError("The rules changed. Review them again.")
        _project(payload["project_id"])
        data = _load()
        updated = {"revision": payload["revision"] + 1, "rules": payload["rules"]}
        data[payload["project_id"]] = updated
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        path.chmod(0o600)
        del _PROPOSALS[ident]
        return updated


def source_mode(project_id: str, kind: str, source: str, rules: dict) -> str:
    if kind == "conversation":
        return "allow"
    matches = []
    for rule in rules["rules"]:
        if rule["kind"] != kind:
            continue
        selector = rule["source"]
        if kind == "folder":
            project = _project(project_id)
            root = Path(project["root"]).resolve()
            path = Path(source).resolve()
            selected = (root / selector).resolve()
            # Both paths remain under the actual project even for legacy or
            # tampered selectors. A wildcard is never a path permission.
            if path.is_relative_to(root) and selected.is_relative_to(root) and path.is_relative_to(selected):
                matches.append(rule["mode"])
        elif selector == "*" or source == selector:
            matches.append(rule["mode"])
    return min(matches, key={"local": 0, "ask": 1, "allow": 2}.get) if matches else "local"
