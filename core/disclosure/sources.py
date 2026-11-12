"""Ephemeral first-party provenance, carried with the server-bound turn.

Only actual assembly sites register bytes. JSON from a model/document cannot
construct this capability. Unknown fragments stay local under project rules.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any


@dataclass
class SourceCatalog:
    entries: list[tuple[str, str, str]] = field(default_factory=list, repr=False)
    prepared: dict[str, dict] = field(default_factory=dict, repr=False)
    has_attachments: bool = False
    has_images: bool = False
    lock: Any = field(default_factory=threading.RLock, repr=False)

    def add(self, text: str, kind: str, source: str) -> None:
        if not text:
            return
        with self.lock:
            entry = (text, kind, source)
            if entry not in self.entries:
                # A full catalog cannot widen exposure: missing labels stay local.
                if len(self.entries) < 1024:
                    self.entries.append(entry)

    def __deepcopy__(self, memo):
        return self


def current_catalog() -> SourceCatalog | None:
    from core.remote_fetch_policy import current_remote_fetch_context
    candidate = current_remote_fetch_context().get("_disclosure_sources")
    return candidate if isinstance(candidate, SourceCatalog) else None


def register(text: str, *, kind: str, source: str) -> None:
    catalog = current_catalog()
    if catalog:
        catalog.add(text, kind, source)


def register_derived(original: str, derived: str) -> None:
    """Preserve every existing label through an exact carrier conversion.

    A conversion cannot create provenance for an unknown source. Multiple
    labels remain multiple labels so a restrictive source still wins.
    """
    catalog = current_catalog()
    if catalog is not None:
        with catalog.lock:
            labels = [(kind, source) for text, kind, source in catalog.entries if text == original]
            for kind, source in labels:
                catalog.add(derived, kind, source)


def register_prepared(entries: list[dict]) -> None:
    """Tag the already-admitted preparation before any helper can read it."""
    import copy

    catalog = current_catalog()
    if catalog is None:
        return
    with catalog.lock:
        for entry in entries:
            source = str(entry.get("attachment_id") or entry.get("id") or "")
            if not source:
                continue
            catalog.has_attachments = True
            catalog.has_images |= entry.get("kind") == "image" or bool(entry.get("images"))
            for key in ("text", "ocr_text", "data_url"):
                if isinstance(entry.get(key), str):
                    catalog.add(entry[key], "attachment", source)
            for image in entry.get("images", []):
                if isinstance(image, dict) and isinstance(image.get("data_url"), str):
                    catalog.add(image["data_url"], "attachment", source)
            if len(catalog.prepared) < 128 or source in catalog.prepared:
                catalog.prepared[source] = copy.deepcopy(entry)


class AttachmentParts(list):
    """Same provider list shape; register text/pixels as they are assembled."""
    source = ""

    def __deepcopy__(self, memo):
        import copy
        # Copying an assembled request is not a new source ingress. A list
        # subclass otherwise calls append and relabels old parts as the final
        # attachment while reconstructing the list.
        return [copy.deepcopy(part, memo) for part in self]

    def append(self, part):
        if isinstance(part, dict):
            value = part.get("text") if part.get("type") == "text" else (part.get("image_url") or {}).get("url")
            if isinstance(value, str):
                register(value, kind="attachment", source=self.source)
        super().append(part)


def project_messages(messages: list[dict], *, project_id: str, rules: dict, catalog: SourceCatalog | None) -> tuple[list[dict], int, bool]:
    from core.disclosure.rules import source_mode

    entries = list(catalog.entries) if isinstance(catalog, SourceCatalog) else []
    omitted = 0
    ask = False

    def project_text(text):
        nonlocal omitted, ask
        if not isinstance(text, str) or not text:
            return None
        # Partition at *all* registered boundaries. A restrictive source wins
        # even when its bytes also appear in a larger public fragment.
        spans = []
        for fragment, kind, source in entries:
            pos = 0
            while (pos := text.find(fragment, pos)) >= 0:
                spans.append((pos, pos + len(fragment), source_mode(project_id, kind, source, rules)))
                pos += max(1, len(fragment))
        boundaries = sorted({0, len(text), *(a for a, _, _ in spans), *(b for _, b, _ in spans)})
        parts = []
        previous_end = None
        for start, end in pairwise(boundaries):
            modes = [mode for a, b, mode in spans if a <= start and b >= end]
            mode = min(modes, key={"local": 0, "ask": 1, "allow": 2}.get) if modes else "local"
            if mode == "local":
                omitted += 1
            else:
                ask |= mode == "ask"
                if previous_end is not None and previous_end != start:
                    parts.append("\n")
                parts.append(text[start:end])
                previous_end = end
        return "".join(parts) or None

    result = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            projected = project_text(content)
        elif isinstance(content, list):
            projected = []
            for part in content:
                if not isinstance(part, dict):
                    omitted += 1
                    continue
                if part.get("type") == "text":
                    text = project_text(part.get("text"))
                    if text:
                        projected.append({"type": "text", "text": text})
                elif part.get("type") == "image_url":
                    carrier = part.get("image_url")
                    url = carrier.get("url") if isinstance(carrier, dict) else None
                    if not isinstance(url, str) or not url:
                        omitted += 1
                        continue
                    # An image cannot be partially disclosed.
                    before = omitted
                    text = project_text(url)
                    if text == url and omitted == before:
                        projected.append({"type": "image_url", "image_url": {"url": url}})
                else:
                    omitted += 1
        else:
            omitted += 1
            projected = None
        images = []
        native_images = message.get("images")
        if isinstance(native_images, list):
            for encoded in native_images:
                if not isinstance(encoded, str) or not encoded:
                    omitted += 1
                    continue
                before = omitted
                image = project_text(encoded)
                if image == encoded and omitted == before:
                    images.append(encoded)
        elif native_images is not None:
            omitted += 1
        if projected or images:
            # Tool-call payloads and other unproven leaves are not forwarded.
            role = message.get("role")
            if role not in {"system", "user", "assistant", "tool"}:
                raise ValueError("This message has no trusted role. Nothing was sent.")
            result.append({"role": role, "content": projected or "", **({"images": images} if images else {})})
    return result, omitted, ask


def register_memory_content(content: Any) -> None:
    """Classify the router's admitted source carriers at request assembly.

    Callers must own the existing evidence-origin stamp. No marker embedded
    in source text or supplied as JSON can invoke this function.
    """
    if isinstance(content, str):
        register(content, kind="memory", source="*")
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                register(block["text"], kind="memory", source="*")


def register_admitted_memory(request: Any, message: dict) -> None:
    """Only a pure memory carrier can gain the memory-class label.

    Mixed or partially classified router context stays unknown. In particular,
    a trimmed attachment or file result cannot inherit a memory allow rule.
    """
    context = getattr(request, "context", None) or (getattr(request, "metadata", None) or {}).get("context_manifest") or {}
    types = {str(item.get("source_type") or "") for item in context.get("items_included", []) if isinstance(item, dict)}
    memory_types = {"runtime_memory", "semantic_memory", "user_profile", "session_summary", "confirmed_memory"}
    if (types and types <= memory_types and message.get("role") == "system"
            and isinstance((getattr(request, "metadata", None) or {}).get("admitted_capsule_evidence"), dict)):
        register_memory_content(message.get("content"))
