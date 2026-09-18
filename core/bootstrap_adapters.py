from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from core.runtime_paths import data_path


class BootstrapMirrorAdapter(ABC):
    @abstractmethod
    def publish_snapshot(self, topic_name: str, snapshot: dict[str, Any]) -> bool:
        raise NotImplementedError

    @abstractmethod
    def fetch_snapshot(self, topic_name: str) -> dict[str, Any] | None:
        raise NotImplementedError


class FileTopicAdapter(BootstrapMirrorAdapter):
    def __init__(self, base_dir: str | Path | None = None) -> None:
        target_dir = data_path("bootstrap") if base_dir is None else Path(base_dir)
        self.base_dir = Path(target_dir).expanduser().resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _topic_path(self, topic_name: str) -> Path:
        safe = "".join(ch for ch in topic_name if ch.isalnum() or ch in {"_", "-"}).strip() or "topic"
        return self.base_dir / f"{safe}.json"

    def publish_snapshot(self, topic_name: str, snapshot: dict[str, Any]) -> bool:
        path = self._topic_path(topic_name)
        path.write_text(json.dumps(snapshot, sort_keys=True, indent=2), encoding="utf-8")
        return True

    def fetch_snapshot(self, topic_name: str) -> dict[str, Any] | None:
        path = self._topic_path(topic_name)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None


class HttpJsonMirrorAdapter(BootstrapMirrorAdapter):
    """
    Expects a lightweight mirror service with:
    POST {base_url}/publish/{topic}
    GET  {base_url}/topics/{topic}

    This is the best place to bridge Telegram/Discord/pubsub later,
    without polluting core node logic.
    """

    def __init__(self, base_url: str, timeout_seconds: float = 3.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def publish_snapshot(self, topic_name: str, snapshot: dict[str, Any]) -> bool:
        url = f"{self.base_url}/publish/{urllib.parse.quote(topic_name)}"
        data = json.dumps(snapshot, sort_keys=True).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                return 200 <= resp.status < 300
        except (urllib.error.URLError, urllib.error.HTTPError):
            return False

    def fetch_snapshot(self, topic_name: str) -> dict[str, Any] | None:
        url = f"{self.base_url}/topics/{urllib.parse.quote(topic_name)}"
        req = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                if resp.status < 200 or resp.status >= 300:
                    return None
                data = resp.read().decode("utf-8")
                return json.loads(data)
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError):
            return None
