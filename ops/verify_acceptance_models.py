from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib import request

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROFILE = REPO_ROOT / "config" / "acceptance" / "local_ollama_bundle_profile.json"
DEFAULT_TAGS_URL = "http://127.0.0.1:11434/api/tags"


def required_models(profile_path: Path) -> tuple[str, ...]:
    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    candidates = [payload.get("model"), *list(payload.get("selected_models") or [])]
    return tuple(dict.fromkeys(str(item or "").strip() for item in candidates if str(item or "").strip()))


def served_models(tags_payload: dict) -> set[str]:
    return {
        str(item.get("name") or "").strip()
        for item in list(tags_payload.get("models") or [])
        if str(item.get("name") or "").strip()
    }


def verify(profile_path: Path, *, tags_url: str = DEFAULT_TAGS_URL) -> tuple[str, ...]:
    with request.urlopen(tags_url, timeout=15.0) as response:
        available = served_models(json.loads(response.read().decode("utf-8")))
    missing = tuple(model for model in required_models(profile_path) if model not in available)
    if missing:
        raise RuntimeError(f"missing acceptance models: {', '.join(missing)}; available: {', '.join(sorted(available))}")
    return tuple(sorted(available))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Verify every model required by the canonical acceptance profile.")
    parser.add_argument("--profile", default=str(DEFAULT_PROFILE))
    parser.add_argument("--tags-url", default=DEFAULT_TAGS_URL)
    args = parser.parse_args(argv)
    profile_path = Path(args.profile).expanduser().resolve()
    try:
        available = verify(profile_path, tags_url=str(args.tags_url))
    except Exception as exc:
        print(f"Acceptance model verification failed: {exc}", file=sys.stderr)
        return 1
    print(f"Acceptance models verified from {profile_path.name}: {', '.join(required_models(profile_path))}")
    print(f"Ollama models available: {', '.join(available)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
