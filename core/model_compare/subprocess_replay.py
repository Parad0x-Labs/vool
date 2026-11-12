"""Prepared messages for explicitly configured JSON stdio providers.

``comparison_request_format=openai_messages_v1`` declares that the command
accepts a complete chat-completion request on stdin. Legacy prompt-only
commands never acquire that capability merely by being configured as local.
No transcript flattening, hidden context, or provider completion is inferred.
"""
from __future__ import annotations

from typing import Any


def availability(manifest: Any, *, images: bool = False) -> str:
    from core.chat_attachments import manifest_supports_images
    from core.model_registry import ModelRegistry
    from core.model_selection_policy import provider_cost_class

    if manifest.runtime_config.get('comparison_request_format') != 'openai_messages_v1':
        return 'This command has not been set up to receive complete chat requests.'
    if provider_cost_class(manifest) != 'free_local' or manifest.metadata.get('deployment_class') == 'cloud':
        return 'This command needs its own approved cloud spending and disclosure path.'
    if images and manifest_supports_images(manifest) is not True:
        return 'This model cannot read the images in this task.'
    adapter = ModelRegistry().build_adapter(manifest)
    from core.provider_execution_boundary import invoke_provider_execution_boundary

    if adapter.validate_runtime() or invoke_provider_execution_boundary(adapter, 'health_check').get('ok') is not True:
        return 'This local model is not ready for this task.'
    return ''


def prepare(request: Any, manifest: Any) -> dict:
    from core.model_compare.prepared import has_images, replay_for, source_payload

    replay = replay_for(request)
    if replay is None:
        raise ValueError('This comparison has no prepared input.')
    reason = availability(manifest, images=has_images(source_payload(replay.source())))
    if reason:
        raise ValueError(reason)
    return replay.apply({'model': manifest.model_name, 'stream': False},
        provider=manifest.provider_id, url='stdio:/chat/completions')
