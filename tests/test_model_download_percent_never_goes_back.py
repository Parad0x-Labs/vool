"""The Download percent in Settings covers every file of the model and never goes back.

Seen on the real app (2026-11-11, qwen3.5:4b): Ollama reports each file of a model (weights, vision
projector, metadata) with its own completed/total, so the page showed "78%", then "51%", then
"94%" as the download moved from file to file.
"""

from __future__ import annotations

import contextlib
import json

from core.web.api.runtime import ModelPullProgress, _stream_ollama_pull


def _events() -> list[dict[str, object]]:
    weights, projector = 2_600, 700
    return [
        {"status": "pulling manifest"},
        {"status": "pulling weights", "digest": "sha256:w", "total": weights, "completed": 2_000},
        {"status": "pulling projector", "digest": "sha256:p", "total": projector, "completed": 100},
        {"status": "pulling weights", "digest": "sha256:w", "total": weights, "completed": weights},
        {"status": "pulling projector", "digest": "sha256:p", "total": projector, "completed": 350},
        {"status": "pulling projector", "digest": "sha256:p", "total": projector, "completed": projector},
        {"status": "success"},
    ]


def test_the_percent_spans_every_file_and_only_climbs(monkeypatch) -> None:
    seen: list[int] = []
    progress = ModelPullProgress("qwen3.5:4b", env={"VOOL_RAW_OLLAMA_API_URL": "http://127.0.0.1:9"})
    original_update = progress.update

    def recording_update(**kwargs):
        original_update(**kwargs)
        if kwargs.get("percent") is not None:
            seen.append(int(progress.public["percent"]))

    progress.update = recording_update  # type: ignore[method-assign]
    lines = [json.dumps(event).encode("utf-8") + b"\n" for event in _events()]

    @contextlib.contextmanager
    def fake_urlopen(request, timeout=None):
        yield iter(lines)

    monkeypatch.setattr("core.provider_http.urlopen", fake_urlopen)
    _stream_ollama_pull("qwen3.5:4b", progress=progress)

    assert seen == sorted(seen), seen
    assert seen[0] == 76  # 2000 of the 2600 weights bytes: the only file known so far
    assert seen[-1] == 100
