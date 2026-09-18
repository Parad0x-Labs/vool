"""Downloads, verifies, and unpacks a prebuilt native-Windows llama.cpp binary.

No WSL2, no compilation, no CUDA Toolkit install required for the common case:
llama.cpp's GitHub releases ship ready-to-run Windows binaries per backend (CUDA,
HIP/Radeon, Vulkan, SYCL, CPU). This is what makes GPU acceleration genuinely
one-click-viable for end users, instead of replicating the manual from-source WSL2
build (Ubuntu/gcc/CUDA-Toolkit version fights, 10+ minute compile) that a from-scratch
dev setup requires.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.llamacpp_capability_probe import LlamaCppBackendCandidate
from installer.llamacpp_backend_select import DEFAULT_RELEASE_TAG, GITHUB_REPO, asset_name_for_tag

_MANIFEST_RELATIVE_PATH = Path("config") / "llamacpp-runtime.json"
_RUNTIME_RELATIVE_ROOT = Path("runtime") / "llamacpp"


class BootstrapError(RuntimeError):
    pass


@dataclass(frozen=True)
class InstalledBackend:
    backend: str
    release_tag: str
    install_dir: str
    server_exe_path: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "release_tag": self.release_tag,
            "install_dir": self.install_dir,
            "server_exe_path": self.server_exe_path,
        }


def fetch_release_assets(*, tag: str = DEFAULT_RELEASE_TAG, timeout_seconds: float = 15.0) -> dict[str, dict[str, str]]:
    """Returns {asset_name: {"url": ..., "digest": "sha256:..."}} for a release tag."""
    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/tags/{tag}"
    request = urllib.request.Request(api_url, headers={"Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise BootstrapError(f"could not fetch llama.cpp release metadata for tag {tag}: {exc}") from exc
    assets: dict[str, dict[str, str]] = {}
    for asset in payload.get("assets", []):
        name = str(asset.get("name") or "")
        if not name:
            continue
        assets[name] = {
            "url": str(asset.get("browser_download_url") or ""),
            "digest": str(asset.get("digest") or ""),
        }
    return assets


def install_llamacpp_backend(
    *,
    candidate: LlamaCppBackendCandidate,
    runtime_home: str | Path,
    tag: str = DEFAULT_RELEASE_TAG,
    force: bool = False,
) -> InstalledBackend:
    """Idempotent: returns the existing install if the manifest already records this
    exact (backend, tag) pair, unless force=True."""
    runtime_root = Path(runtime_home).expanduser().resolve()
    install_dir = runtime_root / _RUNTIME_RELATIVE_ROOT / candidate.backend
    manifest_path = runtime_root / _MANIFEST_RELATIVE_PATH

    if not force:
        existing = _read_manifest_entry(manifest_path, candidate.backend)
        if existing is not None and existing.release_tag == tag and Path(existing.server_exe_path).exists():
            return existing

    assets = fetch_release_assets(tag=tag)
    asset_name = asset_name_for_tag(candidate, tag=tag)
    asset = assets.get(asset_name)
    if asset is None or not asset.get("url"):
        raise BootstrapError(f"asset {asset_name} not found in llama.cpp release {tag}")

    install_dir.mkdir(parents=True, exist_ok=True)
    zip_path = install_dir / asset_name
    _download_with_retry(url=asset["url"], destination=zip_path)
    _verify_digest(zip_path, expected_digest=asset.get("digest") or "")
    _safe_extract(zip_path, destination=install_dir)
    zip_path.unlink(missing_ok=True)

    if candidate.requires_runtime_asset:
        runtime_asset = assets.get(candidate.requires_runtime_asset)
        if runtime_asset and runtime_asset.get("url"):
            runtime_zip_path = install_dir / candidate.requires_runtime_asset
            _download_with_retry(url=runtime_asset["url"], destination=runtime_zip_path)
            _verify_digest(runtime_zip_path, expected_digest=runtime_asset.get("digest") or "")
            _safe_extract(runtime_zip_path, destination=install_dir)
            runtime_zip_path.unlink(missing_ok=True)

    server_exe = install_dir / "llama-server.exe"
    if not server_exe.exists():
        raise BootstrapError(f"llama-server.exe missing from extracted {asset_name}")

    installed = InstalledBackend(
        backend=candidate.backend,
        release_tag=tag,
        install_dir=str(install_dir),
        server_exe_path=str(server_exe),
    )
    _write_manifest_entry(manifest_path, installed)
    return installed


def download_probe_fixture_model(*, runtime_home: str | Path, force: bool = False) -> Path:
    """SmolLM2-135M-Instruct-Q4_K_M.gguf: a tiny (~90MB), quality-irrelevant fixture
    used ONLY for the live capability-probe timing run, never for real inference."""
    runtime_root = Path(runtime_home).expanduser().resolve()
    model_path = runtime_root / "models" / "probe" / "smollm2-135m-instruct-q4_k_m.gguf"
    if model_path.exists() and not force:
        return model_path
    model_path.parent.mkdir(parents=True, exist_ok=True)
    url = (
        "https://huggingface.co/bartowski/SmolLM2-135M-Instruct-GGUF/resolve/main/"
        "SmolLM2-135M-Instruct-Q4_K_M.gguf?download=1"
    )
    # Pinned so the file the benchmark feeds to the executed llama-server is verified (a
    # MITM/upstream swap fails the hash -> BootstrapError -> safe CPU fallback, never runs an
    # unverified model). If bartowski ever re-uploads, update this hash; a stale pin only
    # degrades a GPU host to CPU sizing, it never breaks the install.
    _download_with_retry(
        url=url,
        destination=model_path,
        expected_sha256="2e8040ceae7815abe0dcb3540b9995eaa1fa0d2ca9e797d0a635ae4433c68c2d",
        max_seconds=300.0,
    )
    return model_path


def _download_with_retry(*, url: str, destination: Path, expected_sha256: str | None = None, max_seconds: float = 600.0) -> None:
    curl_binary = shutil.which("curl")
    partial_path = destination.with_suffix(f"{destination.suffix}.partial")
    if not curl_binary:
        raise BootstrapError("curl is required to download llama.cpp runtime assets")
    try:
        completed = subprocess.run(
            [
                curl_binary, "-fL", "--retry", "2", "--retry-delay", "2",
                # curl's own timers are best-effort only: on some builds --retry re-arms
                # --max-time, so they are NOT a reliable wall. The subprocess `timeout` below
                # is the AUTHORITATIVE bound that Python enforces by killing a stuck curl, so a
                # stalled connection can never hang the installer's hardware-detection step.
                "--connect-timeout", "15", "--max-time", str(int(max_seconds)),
                "--speed-limit", "1024", "--speed-time", "30",
                "--continue-at", "-", "-o", str(partial_path), url,
            ],
            check=False,
            timeout=max_seconds + 60.0,
        )
    except subprocess.TimeoutExpired as exc:
        partial_path.unlink(missing_ok=True)
        raise BootstrapError(f"download exceeded {max_seconds + 60:.0f}s and was aborted: {url}") from exc
    if completed.returncode != 0 or not partial_path.exists() or partial_path.stat().st_size <= 0:
        partial_path.unlink(missing_ok=True)
        raise BootstrapError(f"download failed for {url}")
    if expected_sha256:
        # Reuse the GitHub-digest verifier (raises BootstrapError + deletes on mismatch).
        _verify_digest(partial_path, expected_digest=f"sha256:{str(expected_sha256).strip().lower()}")
    partial_path.replace(destination)


def _verify_digest(path: Path, *, expected_digest: str) -> None:
    expected = str(expected_digest or "").strip().lower()
    if not expected.startswith("sha256:"):
        return  # GitHub didn't publish a digest for this asset; skip rather than fail closed on unrelated assets
    expected_hex = expected.split(":", 1)[1]
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    actual_hex = hasher.hexdigest().lower()
    if actual_hex != expected_hex:
        path.unlink(missing_ok=True)
        raise BootstrapError(f"sha256 mismatch for {path.name}: expected {expected_hex}, got {actual_hex}")


def _safe_extract(zip_path: Path, *, destination: Path) -> None:
    destination_resolved = destination.resolve()
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            member_path = (destination_resolved / member.filename).resolve()
            if destination_resolved not in member_path.parents and member_path != destination_resolved:
                raise BootstrapError(f"unsafe zip entry escapes destination: {member.filename}")
        archive.extractall(destination_resolved)


def _read_manifest_entry(manifest_path: Path, backend: str) -> InstalledBackend | None:
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    entry = raw.get(backend) if isinstance(raw, dict) else None
    if not isinstance(entry, dict):
        return None
    try:
        return InstalledBackend(
            backend=str(entry["backend"]),
            release_tag=str(entry["release_tag"]),
            install_dir=str(entry["install_dir"]),
            server_exe_path=str(entry["server_exe_path"]),
        )
    except KeyError:
        return None


def _write_manifest_entry(manifest_path: Path, installed: InstalledBackend) -> None:
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raw = {}
    except (OSError, json.JSONDecodeError):
        raw = {}
    raw[installed.backend] = installed.to_dict()
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


__all__ = [
    "BootstrapError",
    "InstalledBackend",
    "download_probe_fixture_model",
    "fetch_release_assets",
    "install_llamacpp_backend",
]
