from __future__ import annotations

import argparse
import shlex
from pathlib import Path

from core.runtime_install_profiles import persist_install_profile_record


def _selected_models(raw_csv: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in str(raw_csv or "").split(",") if item.strip())


def _export_line(name: str, value: str) -> str:
    return f"export {name}={shlex.quote(str(value or '').strip())}"


def persist_windows_runtime_config(
    *,
    runtime_home: str,
    install_profile: str,
    model_tag: str,
    selected_models_csv: str,
    ollama_models_dir: str,
) -> tuple[Path, Path]:
    runtime_root = Path(runtime_home).expanduser().resolve()
    selected_models = _selected_models(selected_models_csv) or (str(model_tag).strip(),)
    profile_record = persist_install_profile_record(
        runtime_root,
        install_profile,
        selected_model=model_tag,
        selected_models=selected_models,
    )
    provider_env = runtime_root / "config" / "provider-env.sh"
    provider_env.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "#!/usr/bin/env bash",
        _export_line("VOOL_INSTALL_PROFILE", install_profile),
        _export_line("VOOL_OLLAMA_MODEL", model_tag),
        _export_line("VOOL_REGISTER_INSTALLED_OLLAMA_MODELS", "1"),
        _export_line("OLLAMA_MODELS", ollama_models_dir),
        _export_line("OLLAMA_API_KEY", "ollama-local"),
        "",
    ]
    provider_env.write_text("\n".join(lines), encoding="utf-8")
    return profile_record, provider_env


def main() -> int:
    parser = argparse.ArgumentParser(prog="persist_windows_runtime_config")
    parser.add_argument("runtime_home")
    parser.add_argument("install_profile")
    parser.add_argument("model_tag")
    parser.add_argument("selected_models_csv")
    parser.add_argument("ollama_models_dir")
    args = parser.parse_args()
    profile_record, provider_env = persist_windows_runtime_config(
        runtime_home=args.runtime_home,
        install_profile=args.install_profile,
        model_tag=args.model_tag,
        selected_models_csv=args.selected_models_csv,
        ollama_models_dir=args.ollama_models_dir,
    )
    print(f"Install profile written to {profile_record}")
    print(f"Provider env written to {provider_env}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
