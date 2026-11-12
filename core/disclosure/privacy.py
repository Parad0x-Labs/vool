"""Preserve a prepared request's privacy class across an explicit comparison."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.cloud_provider_contract import PrivacyClass


@dataclass(frozen=True)
class PrivacyScope:
    classes: tuple[PrivacyClass, ...] = (PrivacyClass.USER_APPROVED,)
    paths: tuple[str, ...] = field(default=(), repr=False)
    categories: tuple[str, ...] = ()

    def preflight(self, payload: dict | None = None) -> None:
        import json

        from core.cloud_privacy_policy import CloudPrivacyGrant, evaluate_cloud_privacy
        # No private class is granted here. This separates a hard prohibition
        # (including wallet paths) from the owner decision still required below.
        ungranted = CloudPrivacyGrant(approved_paths=self.paths, approved_data_categories=self.categories)
        for kind in self.classes:
            decision = evaluate_cloud_privacy(privacy_class=kind, paths=self.paths,
                payload_texts=(json.dumps(payload, ensure_ascii=False),) if payload is not None else (), grant=ungranted)
            if not decision.allowed and not decision.reason.startswith('scoped_approval_required:'):
                raise ValueError('Privacy controls keep this prepared request on your computer.')

    def approve_exact(self, payload: dict, *, all_fields: bool = False) -> None:
        from core.cloud_privacy_policy import CloudPrivacyGrant, evaluate_cloud_privacy
        # Created only after this destination's exact-byte owner decision. This
        # value is not persisted, returned, or usable for a later provider/tool.
        grant = CloudPrivacyGrant(privacy_classes=self.classes, approved_paths=self.paths,
                                  approved_data_categories=self.categories)
        texts = []
        for message in [*payload.get('messages', []), {'content': payload.get('system')}, {'content': payload.get('prompt')}]:
            content = message.get('content')
            if isinstance(content, str):
                texts.append(content)
            elif isinstance(content, list):
                texts.extend(block['text'] for block in content if isinstance(block, dict) and isinstance(block.get('text'), str))
        if all_fields:
            import json
            texts.append(json.dumps(payload, ensure_ascii=False))
        for kind in self.classes:
            decision = evaluate_cloud_privacy(privacy_class=kind, payload_texts=tuple(texts),
                                              paths=self.paths, data_categories=self.categories, grant=grant)
            if not decision.allowed:
                raise ValueError('Privacy controls refused this request. Nothing was sent.')


def for_request(request: Any, catalog: Any) -> PrivacyScope:
    from core.disclosure.sources import SourceCatalog
    metadata = request.metadata
    prior = metadata.get('_disclosure_privacy')
    if isinstance(prior, PrivacyScope):
        return prior
    classes = {PrivacyClass.USER_APPROVED}
    paths = set()
    # Explicit classifications can only add restrictions. A JSON field cannot
    # remove a source class registered by actual ingress or assembly.
    absent = object()
    context = getattr(request, 'context', None) or {}
    explicit = metadata.get('privacy_class', context.get('privacy_class', absent))
    if explicit is not absent:
        try:
            classes.add(PrivacyClass(str(explicit)))
        except ValueError:
            classes.add(PrivacyClass.UNKNOWN)
    if isinstance(catalog, SourceCatalog):
        if catalog.has_attachments:
            classes.add(PrivacyClass.PRIVATE_FILES)
        for _, kind, source in catalog.entries:
            if kind == 'memory':
                classes.add(PrivacyClass.PERSISTENT_MEMORY)
            elif kind in {'folder', 'attachment'}:
                classes.add(PrivacyClass.PRIVATE_FILES)
                if kind == 'folder':
                    paths.add(source)
    return PrivacyScope(tuple(sorted(classes,key=str)),tuple(sorted(paths)))
