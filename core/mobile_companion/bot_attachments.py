"""Bound-chat file intake through the existing attachment staging contract."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from urllib.parse import urlsplit

from core import remote_fetch_policy
from core.mobile_companion.policy import enabled

MAX_BYTES = 3 * 1024 * 1024


def descriptors(platform, message):
    if platform == "discord":
        return [
            {
                "id": str(x.get("id") or ""),
                "url": str(x.get("url") or ""),
                "filename": str(x.get("filename") or "attachment"),
                "media_type": str(x.get("content_type") or ""),
                "size": x.get("size"),
            }
            for x in message.get("attachments") or []
        ]
    rows = []
    document = message.get("document") or message.get("audio") or message.get("voice")
    if document:
        rows.append(
            {
                "id": str(document.get("file_id") or ""),
                "filename": str(document.get("file_name") or "recording.ogg"),
                "media_type": str(document.get("mime_type") or ""),
                "size": document.get("file_size"),
            }
        )
    photo = (message.get("photo") or [])[-1:]
    for item in photo:
        rows.append(
            {
                "id": str(item.get("file_id") or ""),
                "filename": "photo.jpg",
                "media_type": "image/jpeg",
                "size": item.get("file_size"),
            }
        )
    return rows


def stage(worker, identity, chat_id, files):
    from core.mobile_companion.bot_runtime import service
    from core.web.api.mobile_companion_api import _action_attachments_upload

    if len(files) > 4:
        raise ValueError("Send at most four attachments at once.")
    stable = [
        {**{key: value for key, value in item.items() if key != "url"}, "path": urlsplit(item.get("url", "")).path}
        for item in files
    ]
    digest = hashlib.sha256(json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    with service().dispatch_lock:
        if not worker.active() or not enabled(worker.device(), "attachments"):
            raise ValueError("Attachments are off.")
        cached = (worker.device().get("bot_attachment_cache") or {}).get(identity)
        if cached:
            if cached["digest"] != digest:
                raise ValueError("This attachment request changed.")
            return cached["ids"]
    collected = []
    for item in files:
        if not item["id"] or (
            item.get("size") is not None and (type(item["size"]) is not int or not 0 < item["size"] <= MAX_BYTES)
        ):
            raise ValueError("Attachment is too large or has no stable identity.")
        if not worker.active() or not enabled(worker.device(), "attachments"):
            raise ValueError("Attachments are off.")
        if worker.platform == "telegram":
            metadata = worker.call("getFile", {"file_id": item["id"]})
            path = str((metadata.get("result") or {}).get("file_path") or "")
            if (
                not metadata.get("ok")
                or not re.fullmatch(r"[A-Za-z0-9_./-]{1,512}", path)
                or any(p in {"", ".", ".."} for p in path.split("/"))
            ):
                raise ValueError("Attachment location unavailable.")
            url = worker.remote_base + "/file/bot" + worker.token + "/" + path
        else:
            url = item["url"]
            parsed = urlsplit(url)
            test_origin = urlsplit(worker.remote_base)
            local_test = (
                test_origin.scheme == "http"
                and test_origin.hostname == "127.0.0.1"
                and (parsed.scheme, parsed.netloc) == (test_origin.scheme, test_origin.netloc)
            )
            if not local_test and (
                parsed.scheme != "https"
                or parsed.hostname not in {"cdn.discordapp.com", "media.discordapp.net"}
                or parsed.port not in (None, 443)
            ):
                raise ValueError("Attachment location refused.")
            if parsed.username or parsed.password or parsed.fragment:
                raise ValueError("Attachment location refused.")
        with remote_fetch_policy.open_remote_url(url, timeout=4, no_proxy=True, redirect_policy="refuse") as response:
            data = response.read(MAX_BYTES + 1)
        if not data or len(data) > MAX_BYTES:
            raise ValueError("Attachment is empty or too large.")
        collected.append((item, data))
    with service().dispatch_lock:
        device = worker.device()
        if not worker.active() or not enabled(device, "chat") or not enabled(device, "attachments"):
            raise ValueError("Attachments are off.")
        cache = dict(device.get("bot_attachment_cache") or {})
        if identity in cache:
            if cache[identity]["digest"] != digest:
                raise ValueError("This attachment request changed.")
            return cache[identity]["ids"]
        ids = []
        for item, data in collected:
            code, value = _action_attachments_upload(
                {
                    "chat_id": chat_id,
                    "filename": item["filename"],
                    "media_type": item["media_type"],
                    "content_b64": base64.b64encode(data).decode(),
                },
                service=service(),
                device=device,
            )
            if code != 201:
                raise ValueError("This attachment could not be added. Check the file on your computer.")
            ids.append(value["attachment"]["id"])
        cache[identity] = {"digest": digest, "ids": ids}
        service().store.mark_device(worker.device_id, bot_attachment_cache=dict(list(cache.items())[-128:]))
        return ids
