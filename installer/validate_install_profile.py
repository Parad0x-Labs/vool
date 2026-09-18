from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.runtime_backbone import build_provider_registry_snapshot
from core.runtime_install_profiles import build_install_profile_truth


def validate_install_profile(
    *,
    runtime_home: str,
    selected_model: str,
    requested_profile: str | None,
) -> tuple[bool, str]:
    snapshot = build_provider_registry_snapshot(
        # Register the very model this install is about to provision as a local Ollama lane,
        # so validation reflects the post-install state (the model gets pulled in the later
        # `ollama pull` step). Without this the snapshot only knows the *default* tag, so any
        # larger recommended model (e.g. a GPU host's qwen3:8b) is judged "unregistered" and
        # the install aborts even though `ollama pull` would fetch it moments later.
        model_tag=selected_model,
        runtime_home=runtime_home,
        requested_profile=requested_profile,
        honor_install_profile=True,
    )
    profile = build_install_profile_truth(
        requested_profile=str(requested_profile or "").strip() or None,
        selected_model=selected_model,
        runtime_home=runtime_home,
        provider_capability_truth=snapshot.capability_truth,
    )
    if profile.ready and not profile.degraded:
        return True, ""
    reasons = "; ".join(profile.reasons) or "selected install profile is not healthy on this machine."
    return False, (
        f"ERROR: install profile `{profile.profile_id}` is not ready for this machine/runtime.\n"
        f"{reasons}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="validate_install_profile")
    parser.add_argument("runtime_home")
    parser.add_argument("selected_model")
    parser.add_argument("requested_profile", nargs="?", default="")
    args = parser.parse_args(argv)
    ok, message = validate_install_profile(
        runtime_home=args.runtime_home,
        selected_model=args.selected_model,
        requested_profile=args.requested_profile,
    )
    if message:
        print(message)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
