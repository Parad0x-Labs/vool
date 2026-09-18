"""Public, immutable renderer libraries only; never a general filesystem door."""
from functools import lru_cache
from pathlib import Path

ASSETS = frozenset({"chart-4.5.1.min.js", "mermaid-12.0.0.min.js"})
ROOT = Path(__file__).resolve().parents[2] / "web_assets"


@lru_cache(maxsize=2)
def _read(name: str) -> bytes:
    return (ROOT / name).read_bytes()


def handle_chat_asset_get(path: str):
    from core.web.api.service import ApiResponse

    name = path.removeprefix("/chat-assets/")
    if path != "/chat-assets/" + name or name not in ASSETS:
        return ApiResponse(status=404, content_type="text/plain", body=b"Not found")
    return ApiResponse(
        status=200, content_type="text/javascript; charset=utf-8", body=_read(name),
        headers={"Cache-Control": "public, max-age=31536000, immutable",
                 "X-Content-Type-Options": "nosniff"},
    )
