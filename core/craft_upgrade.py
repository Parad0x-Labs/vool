"""Upgrade VOOL's craft to the best local model on this machine.

The shipped craft (core.writing_craft, core.visual_playbooks) was authored once. On a stronger box
- e.g. an M4 iMac that can run a far larger local model than a laptop - the user can ask VOOL to
re-author her craft directives with that model and store the result in a per-machine overlay
(core.craft_overlay), so the repo keeps the baseline and each machine upgrades locally.

This module is the machinery: detect the strongest installed Ollama model, re-author the target
field per genre through it, validate, and write data/craft_overlay.json. It is user-invoked, never
automatic, and touches the network only via the injected/derived model client - the pure logic is
unit-tested with stubs.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable

import requests

from core import craft_overlay, visual_playbooks, writing_craft
from core.provider_invocation_gateway import (
    seal_direct_provider_invocation,
)
from core.runtime_paths import data_path

_MIN_LEN = 40           # a re-authored directive shorter than this is treated as a failure
_MAX_LEN = 1400         # ... and longer than this is trimmed at a word boundary
_REFUSAL = re.compile(r"\b(i can't|i cannot|i'm sorry|as an ai|i am unable)\b", re.IGNORECASE)

# Per section: which field the upgrade re-authors, and the shipped base text for each genre.
_SECTIONS = {
    "writing_craft": {
        "field": "craft_directive",
        "label": "prose craft directive",
        "bases": lambda: {k: v.craft_directive for k, v in writing_craft.GENRE_CRAFT.items()},
        "names": lambda: {k: v.display_name for k, v in writing_craft.GENRE_CRAFT.items()},
    },
    "visual_playbooks": {
        "field": "camera_language",
        "label": "camera and visual grammar",
        "bases": lambda: {k: v.camera_language for k, v in visual_playbooks.VISUAL_PLAYBOOKS.items()},
        "names": lambda: {k: v.display_name for k, v in visual_playbooks.VISUAL_PLAYBOOKS.items()},
    },
}


def parse_param_size(details: dict) -> float:
    """Parameter count in billions from an Ollama model's details, else 0.0."""
    raw = str((details or {}).get("parameter_size") or "").strip()
    best = 0.0
    for num, unit in re.findall(r"(\d+(?:\.\d+)?)\s*([bBmM])", raw):
        value = float(num) * (1.0 if unit in "bB" else 0.001)
        best = max(best, value)
    return best


def list_local_models(ollama_url: str | None = None, *, timeout: float = 10.0) -> list[dict]:
    """Installed Ollama models (name, size, details), or [] if the daemon is unreachable."""
    from core.ollama_endpoint import ollama_base_url

    ollama_url = str(ollama_url or ollama_base_url())
    try:
        resp = requests.get(f"{str(ollama_url).rstrip('/')}/api/tags", timeout=timeout)
        resp.raise_for_status()
        models = resp.json().get("models")
    except Exception:
        return []
    return [m for m in (models or []) if isinstance(m, dict) and m.get("name")]


def select_best_model(models: list[dict]) -> str | None:
    """Pick the most capable general model: highest parameter count, then largest file size.
    Embedding models are excluded (they cannot author prose)."""
    candidates = [m for m in models if "embed" not in str(m.get("name", "")).lower()]
    if not candidates:
        return None
    best = max(candidates, key=lambda m: (parse_param_size(m.get("details") or {}), float(m.get("size") or 0)))
    return str(best.get("name"))


def build_reauthor_prompt(label: str, genre_name: str, base_text: str) -> str:
    return (
        f"You are a master creative-direction teacher. Rewrite the following {label} for the "
        f"'{genre_name}' genre so it is sharper, more concrete, and more actionable for an AI "
        "generation model - specific devices and moves, no filler, no buzzword stacking. Keep it "
        "one dense paragraph. Original wording only, no copyrighted text. Output ONLY the rewritten "
        "text, nothing else.\n\nCURRENT:\n" + str(base_text or "")
    )


def _clean(text: str) -> str:
    out = " ".join(str(text or "").split())
    if out.startswith("```"):
        out = out.strip("`").strip()
    if len(out) > _MAX_LEN:
        out = out[:_MAX_LEN].rsplit(" ", 1)[0].strip()
    return out


def reauthor(label: str, genre_name: str, base_text: str, model_client: Callable[[str], str]) -> str | None:
    """Re-author one field; returns the new text, or None if the model failed/refused/too short."""
    try:
        raw = model_client(build_reauthor_prompt(label, genre_name, base_text))
    except Exception:
        return None
    text = _clean(raw)
    if len(text) < _MIN_LEN or _REFUSAL.search(text):
        return None
    return text


def upgrade_craft(
    *,
    model_client: Callable[[str], str],
    sections: Iterable[str] = ("writing_craft", "visual_playbooks"),
    genres: Iterable[str] | None = None,
    writer: Callable[[dict], object] | None = None,
) -> dict:
    """Re-author the target field for each genre in each section and persist the overlay.

    Returns a summary {updated: [...], skipped: [...], sections: [...]}. ``writer`` defaults to
    writing data/craft_overlay.json; inject a capture in tests.
    """
    overlay: dict[str, dict] = {}
    updated: list[str] = []
    skipped: list[str] = []
    for section in sections:
        spec = _SECTIONS.get(section)
        if spec is None:
            continue
        bases = spec["bases"]()
        names = spec["names"]()
        keys = list(genres) if genres is not None else list(bases)
        for key in keys:
            base_text = bases.get(key)
            if not base_text:
                skipped.append(f"{section}:{key}")
                continue
            new_text = reauthor(spec["label"], names.get(key, key), base_text, model_client)
            if new_text is None:
                skipped.append(f"{section}:{key}")
                continue
            overlay.setdefault(section, {})[key] = {spec["field"]: new_text}
            updated.append(f"{section}:{key}")

    persist = writer or write_overlay
    persist(overlay)
    return {"updated": updated, "skipped": skipped, "sections": list(sections)}


def write_overlay(overlay: dict) -> object:
    """Merge the new overlay into data/craft_overlay.json (per section/genre) and reload the cache."""
    path = data_path(craft_overlay.OVERLAY_FILE)
    existing: dict = {}
    try:
        if path.exists():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                existing = loaded
    except Exception:
        existing = {}
    for section, genres in (overlay or {}).items():
        existing.setdefault(section, {}).update(genres)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    craft_overlay.reload_overlay()
    return path


def ollama_client(model: str, ollama_url: str | None = None, *, timeout: float = 180.0) -> Callable[[str], str]:
    """A model_client bound to a local Ollama model (POST /api/generate, non-streaming)."""
    from core.ollama_endpoint import ollama_base_url

    base = str(ollama_url or ollama_base_url()).rstrip("/")
    # Sized: same direct-to-Ollama class as the arbiter, fact extractor and summarizer -- this
    # lane bypasses the provider adapter, so nothing else stamps num_ctx and the model would load
    # at its native context.
    from core.runtime_provider_defaults import _ollama_context_window_for_bundle_role

    num_ctx = _ollama_context_window_for_bundle_role("general", model_tag=str(model or ""))

    def _call(prompt: str) -> str:
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "think": False,
            "options": {"temperature": 0.7, "num_ctx": num_ctx},
        }
        permit = seal_direct_provider_invocation(
            provider_id="ollama:craft-upgrade",
            model_id=model,
            operation="craft_generation",
            payload=payload,
            request_id=(
                "craft-upgrade-"
                + hashlib.sha256(
                    prompt.encode("utf-8")
                ).hexdigest()
            ),
            header_names=("Content-Type",),
        )
        resp = requests.post(
            f"{base}/api/generate",
            json=permit.consume(),
            timeout=timeout,
        )
        resp.raise_for_status()
        return str(resp.json().get("response") or "")

    return _call
