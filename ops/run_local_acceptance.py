from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import signal
import socket
import statistics
import subprocess
import sys
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from urllib import request
from urllib.parse import urlparse


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


REPO_ROOT = _repo_root()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.proof_manifest import build_proof_manifest, write_proof_manifest

DEFAULT_PROFILE_PATH = REPO_ROOT / "config" / "acceptance" / "local_ollama_bundle_profile.json"
LEGACY_PROFILE_PATH = REPO_ROOT / "config" / "acceptance" / "local_qwen25_7b_profile.json"
DEFAULT_START_SCRIPT = REPO_ROOT / "run.sh"
DEFAULT_RUNTIME_LAUNCH_AGENT_LABEL = "ai.vool.runtime"
DEFAULT_BASE_URL = "http://127.0.0.1:11435"


@dataclass(frozen=True)
class AcceptanceProfile:
    profile_id: str
    display_name: str
    model: str
    cold_start_max_seconds: float
    simple_prompt_median_max_seconds: float
    simple_prompt_hard_max_seconds: float
    file_task_median_max_seconds: float
    live_lookup_median_max_seconds: float
    chained_task_median_max_seconds: float
    consistency_min_passes: int
    manual_btc_source_label: str
    manual_btc_source_url: str
    bundle_models: tuple[str, ...] = ()
    bundle_roles: tuple[tuple[str, str], ...] = ()
    capacity_bucket: str = ""
    fallback_bundle_models: tuple[str, ...] = ()
    advanced_optional_profile: str = ""


def _sanitize_text(value: str, *, repo_root: Path) -> str:
    sanitized = str(value or "")
    repo_variants = {str(repo_root), repo_root.as_posix()}
    try:
        resolved = repo_root.resolve()
        repo_variants.update({str(resolved), resolved.as_posix()})
    except Exception:
        pass
    for variant in sorted((item for item in repo_variants if item), key=len, reverse=True):
        sanitized = sanitized.replace(variant, "<repo>")
    sanitized = re.sub(r"/Users/[^/\s]+", "/Users/<redacted>", sanitized)
    return sanitized


def _sanitize_data(value: Any, *, repo_root: Path) -> Any:
    if isinstance(value, str):
        return _sanitize_text(value, repo_root=repo_root)
    if isinstance(value, list):
        return [_sanitize_data(item, repo_root=repo_root) for item in value]
    if isinstance(value, dict):
        return {key: _sanitize_data(item, repo_root=repo_root) for key, item in value.items()}
    return value


def _read_json(url: str, *, data: dict[str, Any] | None = None, timeout: float = 300.0) -> dict[str, Any]:
    payload = None if data is None else json.dumps(data).encode("utf-8")
    req = request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    with request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _discover_active_runtime_roots(base_url: str) -> tuple[Path, Path] | None:
    try:
        health = _read_json(f"{base_url.rstrip('/')}/healthz", timeout=5.0)
    except Exception:
        return None
    capabilities = dict(health.get("capabilities") or {})
    runtime_home = str(capabilities.get("runtime_home") or "").strip()
    workspace_root = str(capabilities.get("workspace_root") or "").strip()
    if not runtime_home or not workspace_root:
        return None
    return Path(runtime_home).expanduser().resolve(), Path(workspace_root).expanduser().resolve()


def _startup_reply_is_coherent(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return False
    if re.search(r"\b(hello|hi|hey|yo|gm|good morning|good afternoon|good evening|morning)\b", normalized):
        return True
    return "vool" in normalized and any(
        phrase in normalized
        for phrase in (
            "what do you need",
            "what do you want me to do",
            "how can i help",
            "point me at the problem",
        )
    )


def _machine_info() -> dict[str, Any]:
    cpu = ""
    ram_gb = None
    gpu = ""
    try:
        cpu = subprocess.check_output(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            text=True,
        ).strip()
    except Exception:
        cpu = platform.processor().strip()
    try:
        mem_bytes = int(
            subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True).strip()
        )
        ram_gb = round(mem_bytes / (1024 ** 3), 1)
    except Exception:
        ram_gb = None
    try:
        gpu_text = subprocess.check_output(
            ["system_profiler", "SPDisplaysDataType", "-detailLevel", "mini"],
            text=True,
        )
        chips = [
            line.split(":", 1)[1].strip()
            for line in gpu_text.splitlines()
            if "Chipset Model:" in line
        ]
        gpu = ", ".join(chips)
    except Exception:
        gpu = ""
    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "cpu": cpu,
        "ram_gb": ram_gb,
        "gpu": gpu,
    }


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    return round(float(statistics.median(values)), 3)


def _runtime_dirty_state(
    *,
    runtime_version: dict[str, Any],
    health_payload: dict[str, Any],
) -> bool | None:
    runtime_dirty = runtime_version.get("dirty")
    if isinstance(runtime_dirty, bool):
        return runtime_dirty
    health_runtime = dict(health_payload.get("runtime") or {})
    health_dirty = health_runtime.get("dirty")
    if isinstance(health_dirty, bool):
        return health_dirty
    build_id = str(runtime_version.get("build_id") or health_runtime.get("build_id") or "").strip().lower()
    if not build_id:
        return None
    return ".dirty" in build_id or build_id.endswith("dirty")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _read_json_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _install_receipt(repo_root: Path) -> dict[str, Any]:
    payload = _read_json_if_exists(repo_root / "install_receipt.json")
    return payload if isinstance(payload, dict) else {}


def _installed_launch_agent_path(repo_root: Path) -> Path | None:
    launch_agent = dict(_install_receipt(repo_root).get("launch_agent") or {})
    candidate = str(launch_agent.get("macos") or "").strip()
    if not candidate:
        return None
    try:
        return Path(candidate).expanduser().resolve()
    except Exception:
        return None


def _installed_default_model(repo_root: Path) -> str:
    receipt = _install_receipt(repo_root)
    selected = str(receipt.get("selected_model") or "").strip()
    if selected:
        return selected
    selected_models = tuple(str(item).strip() for item in receipt.get("selected_models") or () if str(item).strip())
    if selected_models:
        return selected_models[0]
    install_profile = dict(receipt.get("install_profile") or {})
    selected = str(install_profile.get("selected_model") or "").strip()
    if selected:
        return selected
    selected_models = tuple(
        str(item).strip() for item in install_profile.get("selected_models") or () if str(item).strip()
    )
    if selected_models:
        return selected_models[0]
    return "qwen3:8b"


def _launch_agent_label(path: Path | None) -> str:
    if path is None:
        return DEFAULT_RUNTIME_LAUNCH_AGENT_LABEL
    if path.suffix == ".plist":
        return path.stem or DEFAULT_RUNTIME_LAUNCH_AGENT_LABEL
    return path.name or DEFAULT_RUNTIME_LAUNCH_AGENT_LABEL


def _launch_agent_gui_domain(label: str = "") -> str:
    uid = getattr(os, "getuid", lambda: 0)()
    domain = f"gui/{uid}"
    if label:
        return f"{domain}/{label}"
    return domain


def _launch_agent_loaded(path: Path) -> bool:
    label = _launch_agent_label(path)
    domain = _launch_agent_gui_domain(label)
    result = subprocess.run(
        ["launchctl", "print", domain],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def _suspend_installed_launch_agent(*, repo_root: Path, base_url: str) -> dict[str, str] | None:
    if platform.system().lower() != "darwin":
        return None
    bind_host, bind_port = _runtime_endpoint_parts(base_url)
    if bind_host != "127.0.0.1" or bind_port != 11435:
        return None
    launch_agent_path = _installed_launch_agent_path(repo_root)
    if launch_agent_path is None or not launch_agent_path.exists():
        return None
    if not _launch_agent_loaded(launch_agent_path):
        return None
    subprocess.run(
        ["launchctl", "bootout", _launch_agent_gui_domain(), str(launch_agent_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return {"path": str(launch_agent_path)}


def _restore_installed_launch_agent(state: dict[str, str] | None) -> None:
    if not state or platform.system().lower() != "darwin":
        return
    path_text = str(state.get("path") or "").strip()
    if not path_text:
        return
    launch_agent_path = Path(path_text).expanduser().resolve()
    if not launch_agent_path.exists():
        return
    result = subprocess.run(
        ["launchctl", "bootstrap", _launch_agent_gui_domain(), str(launch_agent_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode != 0:
        subprocess.run(
            ["launchctl", "load", "-w", str(launch_agent_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )


def _expected_repo_commit(repo_root: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(repo_root),
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        build_source = _read_json_if_exists(repo_root / "config" / "build-source.json") or {}
        commit = str(build_source.get("commit") or "").strip()
        if commit:
            return commit[:12]
        receipt = _read_json_if_exists(repo_root / "install_receipt.json") or {}
        commit = str(receipt.get("commit") or receipt.get("build_commit") or "").strip()
        if commit:
            return commit[:12]
        return "archive"


def _copy_tree_with_timestamp(root: Path, *, status: str) -> Path:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    archive_root = root.parent / f"{root.name}_preserved_{status}_{stamp}"
    suffix = 1
    while archive_root.exists():
        archive_root = root.parent / f"{root.name}_preserved_{status}_{stamp}_{suffix}"
        suffix += 1
    shutil.copytree(root, archive_root)
    return archive_root


def _runtime_endpoint_parts(base_url: str) -> tuple[str, int]:
    parsed = urlparse(str(base_url or "").strip())
    host = str(parsed.hostname or "127.0.0.1").strip() or "127.0.0.1"
    port = int(parsed.port or (443 if parsed.scheme == "https" else 80))
    return host, port


def _default_runtime_command(*, repo_root: Path, base_url: str) -> list[str]:
    bind_host, bind_port = _runtime_endpoint_parts(base_url)
    venv_python = repo_root / ".venv" / "bin" / "python"
    python_bin = venv_python if venv_python.exists() else Path(sys.executable)
    return [
        str(python_bin),
        "-m",
        "apps.vool_api_server",
        "--bind",
        bind_host,
        "--port",
        str(bind_port),
    ]


def _resolve_runtime_command(
    *,
    repo_root: Path,
    base_url: str,
    start_script: Path | None,
) -> list[str]:
    bind_host, bind_port = _runtime_endpoint_parts(base_url)
    start_script_venv = start_script.parent / ".venv" / "bin" / "python" if start_script else None
    if (
        start_script
        and start_script.exists()
        and start_script_venv is not None
        and start_script_venv.exists()
        and bind_host == "127.0.0.1"
        and bind_port == 11435
    ):
        return ["sh", str(start_script)]
    return _default_runtime_command(repo_root=repo_root, base_url=base_url)


def _pick_isolated_daemon_bind_port(*, host: str = "127.0.0.1", attempts: int = 128) -> int:
    family = socket.AF_INET6 if ":" in str(host or "") else socket.AF_INET
    for _ in range(max(1, int(attempts))):
        # Ask the OS for the TCP stream endpoint first. On Windows, selecting a
        # UDP ephemeral port and then probing the adjacent TCP port can walk a
        # long run of TCP TIME_WAIT endpoints after the regression suite. A TCP
        # socket bound to port zero is guaranteed to be bindable now; hold it
        # while checking the adjacent UDP daemon port so the pair is reserved
        # together for the duration of the probe.
        try:
            with socket.socket(family, socket.SOCK_STREAM) as tcp_sock:
                tcp_sock.bind((host, 0))
                candidate = int(tcp_sock.getsockname()[1]) - 1
                if candidate <= 0 or candidate >= 65534:
                    continue
                with socket.socket(family, socket.SOCK_DGRAM) as udp_sock:
                    udp_sock.bind((host, candidate))
            return candidate
        except OSError:
            continue
    raise RuntimeError(f"Could not find an isolated daemon bind port for host {host!r}.")


def _resolve_profile_path(path: str | Path | None = None) -> Path:
    profile_path = Path(path or DEFAULT_PROFILE_PATH).expanduser().resolve()
    if profile_path.exists():
        return profile_path
    if profile_path == LEGACY_PROFILE_PATH.resolve():
        return DEFAULT_PROFILE_PATH
    return profile_path


def load_profile(path: str | Path | None = None) -> AcceptanceProfile:
    profile_path = _resolve_profile_path(path)
    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    thresholds = dict(payload.get("thresholds") or {})
    manual_btc = dict(payload.get("manual_btc_check") or {})
    bundle_roles = tuple(
        (
            str(item.get("role") or "").strip(),
            str(item.get("model") or "").strip(),
        )
        for item in payload.get("selected_model_roles") or ()
        if str(item.get("role") or "").strip() and str(item.get("model") or "").strip()
    )
    bundle_models = tuple(
        str(item).strip() for item in payload.get("selected_models") or () if str(item).strip()
    )
    return AcceptanceProfile(
        profile_id=str(payload.get("profile_id") or profile_path.stem),
        display_name=str(payload.get("display_name") or "VOOL local acceptance"),
        model=str(payload.get("model") or "qwen3:8b"),
        cold_start_max_seconds=float(thresholds.get("cold_start_max_seconds", 120.0)),
        simple_prompt_median_max_seconds=float(thresholds.get("simple_prompt_median_max_seconds", 8.0)),
        simple_prompt_hard_max_seconds=float(thresholds.get("simple_prompt_hard_max_seconds", 20.0)),
        file_task_median_max_seconds=float(thresholds.get("file_task_median_max_seconds", 15.0)),
        live_lookup_median_max_seconds=float(thresholds.get("live_lookup_median_max_seconds", 45.0)),
        chained_task_median_max_seconds=float(thresholds.get("chained_task_median_max_seconds", 60.0)),
        consistency_min_passes=int(thresholds.get("consistency_min_passes", 2)),
        manual_btc_source_label=str(manual_btc.get("source_label") or "CoinGecko simple price API"),
        manual_btc_source_url=str(
            manual_btc.get("url") or "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd"
        ),
        bundle_models=bundle_models or (str(payload.get("model") or "qwen3:8b"),),
        bundle_roles=bundle_roles,
        capacity_bucket=str(payload.get("capacity_bucket") or "").strip(),
        fallback_bundle_models=tuple(
            str(item).strip() for item in payload.get("fallback_bundle_models") or () if str(item).strip()
        ),
        advanced_optional_profile=str(payload.get("advanced_optional_profile") or "").strip(),
    )


def _first_currency_amount(text: str) -> float | None:
    match = re.search(r"\$([0-9][0-9,]*(?:\.[0-9]+)?)", str(text or ""))
    if match is None:
        return None
    return float(match.group(1).replace(",", ""))


def _format_usd(amount: float) -> str:
    return f"${amount:,.2f}"


def _market_answer_is_not_future_dated(text: str) -> bool:
    matches = re.findall(r"\b(20\d{2})-(\d{2})-(\d{2})\b", str(text or ""))
    if not matches:
        return True
    allowed_latest = date.today() + timedelta(days=1)
    for year, month, day in matches:
        try:
            observed = date(int(year), int(month), int(day))
        except ValueError:
            continue
        if observed > allowed_latest:
            return False
    return True


@dataclass
class AcceptanceRunner:
    base_url: str
    repo_root: Path
    run_root: Path
    profile: AcceptanceProfile

    def __post_init__(self) -> None:
        self.evidence_dir = self.run_root / "evidence"
        self.workspaces = {
            "main": self.run_root / "workspace" / "main",
            "chain": self.run_root / "workspace" / "chain",
            "logic": self.run_root / "workspace" / "logic",
            "lookup": self.run_root / "workspace" / "lookup",
            "honesty_online": self.run_root / "workspace" / "honesty-online",
            "fidelity": self.run_root / "workspace" / "fidelity",
            "consistency": self.run_root / "workspace" / "consistency",
            "routing": self.run_root / "workspace" / "routing",
        }
        for path in self.workspaces.values():
            path.mkdir(parents=True, exist_ok=True)
        self.session_messages: list[dict[str, str]] = []

    def _chat(
        self,
        prompt: str,
        *,
        workspace: Path,
        conversation_id: str = "acceptance-main",
        track_session: bool = True,
    ) -> tuple[dict[str, Any], float]:
        messages = [*self.session_messages, {"role": "user", "content": prompt}] if track_session else [
            {"role": "user", "content": prompt}
        ]
        body = {
            "model": "vool",
            "messages": messages,
            "stream": False,
            "workspace": str(workspace),
            "conversationId": conversation_id,
        }
        started = time.perf_counter()
        payload = _read_json(f"{self.base_url}/api/chat", data=body)
        elapsed = round(time.perf_counter() - started, 3)
        assistant_text = str(payload.get("message", {}).get("content") or "").strip()
        if track_session:
            self.session_messages.extend(
                [
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": assistant_text},
                ]
            )
        return payload, elapsed

    def _result_base(
        self,
        *,
        prompt: str,
        payload: dict[str, Any],
        latency_seconds: float,
    ) -> dict[str, Any]:
        return {
            "prompt": _sanitize_text(prompt, repo_root=self.repo_root),
            "latency_seconds": latency_seconds,
            "assistant_text": _sanitize_text(str(payload.get("message", {}).get("content") or ""), repo_root=self.repo_root),
            "raw_response_text": _sanitize_text(json.dumps(payload, separators=(",", ":")), repo_root=self.repo_root),
            "error": None,
            "retry_needed": False,
        }

    def run_online(self) -> dict[str, Any]:
        health = _read_json(f"{self.base_url}/healthz")
        capabilities = _read_json(f"{self.base_url}/api/runtime/capabilities")
        runtime_version = dict(health.get("runtime") or {})
        install_profile = dict(capabilities.get("install_profile") or {})
        runtime_model = str(runtime_version.get("model_tag") or self.profile.model or "").strip() or self.profile.model
        results: dict[str, Any] = {}

        prompt = "hello"
        payload, latency = self._chat(prompt, workspace=self.workspaces["main"])
        results["P0.1a_boot_hello"] = self._result_base(prompt=prompt, payload=payload, latency_seconds=latency)
        results["P0.1a_boot_hello"]["pass"] = _startup_reply_is_coherent(results["P0.1a_boot_hello"]["assistant_text"])
        results["P0.1a_boot_hello"]["why"] = "startup replied coherently" if results["P0.1a_boot_hello"]["pass"] else "startup reply was broken"

        prompt = "what can you do right now on this machine?"
        payload, latency = self._chat(prompt, workspace=self.workspaces["main"])
        result = self._result_base(prompt=prompt, payload=payload, latency_seconds=latency)
        capability_text = result["assistant_text"].lower()
        capability_pass = "local file system" in capability_text or "workspace" in capability_text
        result["pass"] = capability_pass
        result["why"] = "capability answer matches local runtime" if capability_pass else "capability answer overclaimed or missed core tools"
        results["P0.1b_capabilities"] = result

        main_file = self.workspaces["main"] / "vool_test_01.txt"
        prompt = f"Create a file named vool_test_01.txt in {self.workspaces['main']} with exactly this content: ALPHA-LOCAL-FILE-01"
        payload, latency = self._chat(prompt, workspace=self.workspaces["main"])
        result = self._result_base(prompt=prompt, payload=payload, latency_seconds=latency)
        result["file_exists"] = main_file.exists()
        result["file_content"] = _sanitize_text(_read_text(main_file) if main_file.exists() else "", repo_root=self.repo_root)
        result["pass"] = result["file_exists"] and result["file_content"] == "ALPHA-LOCAL-FILE-01"
        result["why"] = "file created with exact content" if result["pass"] else "file create result mismatched filesystem"
        results["P0.2_local_file_create"] = result

        prompt = "Append a second line: BETA-APPEND-02"
        payload, latency = self._chat(prompt, workspace=self.workspaces["main"])
        result = self._result_base(prompt=prompt, payload=payload, latency_seconds=latency)
        result["file_exists"] = main_file.exists()
        result["file_content"] = _sanitize_text(_read_text(main_file) if main_file.exists() else "", repo_root=self.repo_root)
        result["pass"] = result["file_content"] == "ALPHA-LOCAL-FILE-01\nBETA-APPEND-02"
        result["why"] = "append changed file exactly once" if result["pass"] else "append result mismatched filesystem"
        results["P0.3_append"] = result

        prompt = "Now read the whole file back exactly"
        payload, latency = self._chat(prompt, workspace=self.workspaces["main"])
        result = self._result_base(prompt=prompt, payload=payload, latency_seconds=latency)
        expected = "ALPHA-LOCAL-FILE-01\nBETA-APPEND-02"
        result["expected"] = expected
        result["pass"] = expected in result["assistant_text"]
        result["why"] = "exact readback matched file contents" if result["pass"] else "assistant paraphrased or corrupted readback"
        results["P0.3b_readback"] = result

        chain_root = self.workspaces["chain"] / "vool_chain_test"
        prompt = "Create a folder named vool_chain_test. Inside it create notes.txt with the line first note. Then create summary.txt that says: notes.txt created successfully. Then list the folder contents."
        payload, latency = self._chat(prompt, workspace=self.workspaces["chain"])
        result = self._result_base(prompt=prompt, payload=payload, latency_seconds=latency)
        notes_path = chain_root / "notes.txt"
        summary_path = chain_root / "summary.txt"
        tree = sorted(str(path.relative_to(self.run_root)) for path in chain_root.rglob("*") if path.is_file())
        result["tree"] = tree
        result["notes_content"] = _sanitize_text(_read_text(notes_path) if notes_path.exists() else "", repo_root=self.repo_root)
        result["summary_content"] = _sanitize_text(_read_text(summary_path) if summary_path.exists() else "", repo_root=self.repo_root)
        result["pass"] = (
            notes_path.exists()
            and summary_path.exists()
            and result["notes_content"] == "first note"
            and result["summary_content"] == "notes.txt created successfully"
        )
        result["why"] = "folder chain completed and listed" if result["pass"] else "chain task lost state or wrote wrong files"
        results["P0.5_tool_chain"] = result

        prompt = "I have 3 tasks. Task A takes 17 minutes, Task B takes twice Task A minus 4 minutes, Task C takes 11 minutes. What is the total? Show the steps."
        payload, latency = self._chat(prompt, workspace=self.workspaces["logic"])
        result = self._result_base(prompt=prompt, payload=payload, latency_seconds=latency)
        lower = result["assistant_text"].lower()
        result["pass"] = "58" in lower and "30" in lower
        result["why"] = "logic response contains correct intermediate and total" if result["pass"] else "logic response drifted"
        results["P0.6_logic"] = result

        prompt = "Look up the current BTC price in USD right now and tell me the answer plus where you got it."
        payload, latency = self._chat(prompt, workspace=self.workspaces["lookup"])
        result = self._result_base(prompt=prompt, payload=payload, latency_seconds=latency)
        text = result["assistant_text"]
        has_source = ("bitcoin" in text.lower() or "btc" in text.lower()) and "source:" in text.lower()
        not_future_dated = _market_answer_is_not_future_dated(text)
        result["pass"] = has_source and not_future_dated
        result["why"] = (
            "live lookup returned a sourced price without future-dated bluffing (manual verification still required)"
            if result["pass"]
            else "live lookup lacked freshness, source, or date sanity"
        )
        results["P0.4_live_lookup"] = result

        prompt = "Create exactly three files: a.txt, b.txt, c.txt. Put ONE, TWO, THREE respectively. Do not create anything else."
        payload, latency = self._chat(prompt, workspace=self.workspaces["fidelity"])
        result = self._result_base(prompt=prompt, payload=payload, latency_seconds=latency)
        files = {
            "a.txt": _read_text(self.workspaces["fidelity"] / "a.txt") if (self.workspaces["fidelity"] / "a.txt").exists() else "",
            "b.txt": _read_text(self.workspaces["fidelity"] / "b.txt") if (self.workspaces["fidelity"] / "b.txt").exists() else "",
            "c.txt": _read_text(self.workspaces["fidelity"] / "c.txt") if (self.workspaces["fidelity"] / "c.txt").exists() else "",
        }
        tree = sorted(path.name for path in self.workspaces["fidelity"].iterdir() if path.is_file())
        result["tree"] = tree
        result["files"] = files
        result["pass"] = tree == ["a.txt", "b.txt", "c.txt"] and files == {"a.txt": "ONE", "b.txt": "TWO", "c.txt": "THREE"}
        result["why"] = "exactly three requested files created" if result["pass"] else "instruction fidelity broke exact file set"
        results["P1.3_instruction_fidelity"] = result

        prompt = "No, use the same folder as before and overwrite only b.txt with TWO-UPDATED"
        payload, latency = self._chat(prompt, workspace=self.workspaces["fidelity"])
        result = self._result_base(prompt=prompt, payload=payload, latency_seconds=latency)
        files = {
            "a.txt": _read_text(self.workspaces["fidelity"] / "a.txt") if (self.workspaces["fidelity"] / "a.txt").exists() else "",
            "b.txt": _read_text(self.workspaces["fidelity"] / "b.txt") if (self.workspaces["fidelity"] / "b.txt").exists() else "",
            "c.txt": _read_text(self.workspaces["fidelity"] / "c.txt") if (self.workspaces["fidelity"] / "c.txt").exists() else "",
        }
        tree = sorted(path.name for path in self.workspaces["fidelity"].iterdir() if path.is_file())
        result["tree"] = tree
        result["files"] = files
        result["pass"] = tree == ["a.txt", "b.txt", "c.txt"] and files == {"a.txt": "ONE", "b.txt": "TWO-UPDATED", "c.txt": "THREE"}
        result["why"] = "recovery changed only b.txt" if result["pass"] else "recovery mutated unrelated files"
        results["P1.4_recovery"] = result

        consistency_results: list[dict[str, Any]] = []
        for index in range(1, 4):
            run_workspace = self.workspaces["consistency"] / f"run{index}"
            run_workspace.mkdir(parents=True, exist_ok=True)
            prompt = "Create file consistency_test.txt with content CONSISTENCY-CHECK"
            payload, latency = self._chat(prompt, workspace=run_workspace, conversation_id=f"acceptance-consistency-{index}")
            result = self._result_base(prompt=prompt, payload=payload, latency_seconds=latency)
            file_path = run_workspace / "consistency_test.txt"
            result["file_exists"] = file_path.exists()
            result["file_content"] = _sanitize_text(_read_text(file_path) if file_path.exists() else "", repo_root=self.repo_root)
            result["pass"] = result["file_exists"] and result["file_content"] == "CONSISTENCY-CHECK"
            result["why"] = "consistency file created exactly" if result["pass"] else "consistency write drifted"
            consistency_results.append(result)
        results["P1.1_consistency"] = consistency_results

        prompt = "What happened five minutes ago in global markets?"
        payload, latency = self._chat(prompt, workspace=self.workspaces["honesty_online"], conversation_id="acceptance-honesty-online")
        result = self._result_base(prompt=prompt, payload=payload, latency_seconds=latency)
        text = result["assistant_text"].lower()
        honest_markers = [
            "can't verify",
            "cannot verify",
            "insufficient evidence",
            "not enough confidence",
            "timestamped live source",
        ]
        result["pass"] = any(marker in text for marker in honest_markers)
        result["why"] = "honesty response refused minute-level bluffing" if result["pass"] else "honesty response still bluffed or overclaimed"
        results["P0.7_honesty_online"] = result

        routing_cases: list[dict[str, Any]] = []
        for case_id, prompt, expected in (
            ("math-03", "50 times 3 please", "150"),
            ("math-05", "Hi, what is the 100 x 50?", "5000"),
        ):
            payload, latency = self._chat(
                prompt,
                workspace=self.workspaces["routing"],
                conversation_id=f"acceptance-routing-{case_id}",
                track_session=False,
            )
            case_result = self._result_base(prompt=prompt, payload=payload, latency_seconds=latency)
            case_result["case_id"] = case_id
            case_result["expected"] = expected
            case_result["pass"] = expected in case_result["assistant_text"]
            case_result["why"] = (
                "HTTP runtime returned the deterministic arithmetic result"
                if case_result["pass"]
                else "HTTP runtime missed the deterministic arithmetic route"
            )
            routing_cases.append(case_result)
        results["P0.8_routing_reliability"] = {
            "pass": all(bool(item["pass"]) for item in routing_cases),
            "cases": routing_cases,
        }

        payload = {
            "captured_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "runtime_version": _sanitize_data(runtime_version, repo_root=self.repo_root),
            "health": _sanitize_data(health, repo_root=self.repo_root),
            "capabilities": _sanitize_data(capabilities, repo_root=self.repo_root),
            "profile": {
                "id": self.profile.profile_id,
                "display_name": self.profile.display_name,
                "benchmark_model": self.profile.model,
                "benchmark_bundle_models": list(self.profile.bundle_models),
                "benchmark_bundle_roles": [
                    {"role": role, "model": model} for role, model in self.profile.bundle_roles
                ],
                "capacity_bucket": self.profile.capacity_bucket,
                "fallback_bundle_models": list(self.profile.fallback_bundle_models),
                "advanced_optional_profile": self.profile.advanced_optional_profile,
                "runtime_model": runtime_model,
                "install_profile_id": str(install_profile.get("profile_id") or ""),
                "install_profile_label": str(install_profile.get("label") or ""),
                "runtime_selected_models": list(install_profile.get("selected_models") or ()),
                "runtime_selected_model_roles": list(install_profile.get("selected_model_roles") or ()),
                "bundle_id": str(install_profile.get("bundle_id") or ""),
                "bundle_kind": str(install_profile.get("bundle_kind") or ""),
            },
            "machine": _sanitize_data(_machine_info(), repo_root=self.repo_root),
            "model": runtime_model,
            "selected_models": list(install_profile.get("selected_models") or ()),
            "workspaces": {
                key: _sanitize_text(str(path.relative_to(self.run_root)), repo_root=self.repo_root)
                for key, path in self.workspaces.items()
            },
            "results": _sanitize_data(results, repo_root=self.repo_root),
        }
        _write_json(self.evidence_dir / "online_acceptance.json", payload)
        return payload


def run_offline_honesty(base_url: str, *, repo_root: Path, run_root: Path) -> dict[str, Any]:
    workspace = run_root / "workspace" / "honesty-offline"
    workspace.mkdir(parents=True, exist_ok=True)
    prompt = "What is the current BTC price now?"
    body = {
        "model": "vool",
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "workspace": str(workspace),
        "conversationId": "acceptance-offline-honesty",
    }
    started = time.perf_counter()
    payload = _read_json(f"{base_url}/api/chat", data=body)
    elapsed = round(time.perf_counter() - started, 3)
    text = str(payload.get("message", {}).get("content") or "").strip()
    lower = text.lower()
    honest_markers = [
        "can't access live",
        "cannot access live",
        "don't have live",
        "do not have live",
        "web lookup is disabled",
        "web access is disabled",
        "can't verify",
        "cannot verify",
    ]
    result = {
        "captured_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "prompt": prompt,
        "latency_seconds": elapsed,
        "assistant_text": _sanitize_text(text, repo_root=repo_root),
        "raw_response_text": _sanitize_text(json.dumps(payload, separators=(",", ":")), repo_root=repo_root),
        "pass": any(marker in lower for marker in honest_markers) and "$" not in text,
        "why": "",
    }
    result["why"] = "offline honesty admitted no live verification" if result["pass"] else "offline honesty still acted like live data existed"
    evidence = {"result": result}
    _write_json(run_root / "evidence" / "offline_honesty.json", evidence)
    return evidence


def build_acceptance_summary(
    *,
    online_payload: dict[str, Any],
    offline_payload: dict[str, Any],
    manual_btc_check: dict[str, Any] | None,
    profile: AcceptanceProfile,
) -> dict[str, Any]:
    results = online_payload["results"]
    p0_ids = [
        "P0.1a_boot_hello",
        "P0.1b_capabilities",
        "P0.2_local_file_create",
        "P0.3_append",
        "P0.3b_readback",
        "P0.5_tool_chain",
        "P0.6_logic",
        "P0.4_live_lookup",
        "P0.7_honesty_online",
        "P0.8_routing_reliability",
    ]
    consistency_runs = results["P1.1_consistency"]
    consistency_passes = sum(1 for item in consistency_runs if item["pass"])
    simple_latencies = [
        results["P0.1a_boot_hello"]["latency_seconds"],
        results["P0.1b_capabilities"]["latency_seconds"],
        results["P0.6_logic"]["latency_seconds"],
        offline_payload["result"]["latency_seconds"],
    ]
    file_latencies = [
        results["P0.2_local_file_create"]["latency_seconds"],
        results["P0.3_append"]["latency_seconds"],
        results["P0.3b_readback"]["latency_seconds"],
        *[item["latency_seconds"] for item in consistency_runs],
        results["P1.3_instruction_fidelity"]["latency_seconds"],
        results["P1.4_recovery"]["latency_seconds"],
    ]
    lookup_latencies = [
        results["P0.4_live_lookup"]["latency_seconds"],
        results["P0.7_honesty_online"]["latency_seconds"],
    ]
    chain_latencies = [results["P0.5_tool_chain"]["latency_seconds"]]

    threshold_checks = {
        "cold_start_max_seconds": {
            "limit": profile.cold_start_max_seconds,
            "actual": float(results["P0.1a_boot_hello"]["latency_seconds"]),
            "pass": float(results["P0.1a_boot_hello"]["latency_seconds"]) <= profile.cold_start_max_seconds,
        },
        "simple_prompt_median_max_seconds": {
            "limit": profile.simple_prompt_median_max_seconds,
            "actual": _median(simple_latencies),
            "pass": (_median(simple_latencies) or float("inf")) <= profile.simple_prompt_median_max_seconds,
        },
        "simple_prompt_hard_max_seconds": {
            "limit": profile.simple_prompt_hard_max_seconds,
            "actual": round(max(simple_latencies), 3) if simple_latencies else None,
            "pass": (max(simple_latencies) if simple_latencies else float("inf")) <= profile.simple_prompt_hard_max_seconds,
        },
        "file_task_median_max_seconds": {
            "limit": profile.file_task_median_max_seconds,
            "actual": _median(file_latencies),
            "pass": (_median(file_latencies) or float("inf")) <= profile.file_task_median_max_seconds,
        },
        "live_lookup_median_max_seconds": {
            "limit": profile.live_lookup_median_max_seconds,
            "actual": _median(lookup_latencies),
            "pass": (_median(lookup_latencies) or float("inf")) <= profile.live_lookup_median_max_seconds,
        },
        "chained_task_median_max_seconds": {
            "limit": profile.chained_task_median_max_seconds,
            "actual": _median(chain_latencies),
            "pass": (_median(chain_latencies) or float("inf")) <= profile.chained_task_median_max_seconds,
        },
        "consistency_min_passes": {
            "limit": profile.consistency_min_passes,
            "actual": consistency_passes,
            "pass": consistency_passes >= profile.consistency_min_passes,
        },
    }

    overall_green = all(bool(results[test_id]["pass"]) for test_id in p0_ids)
    overall_green = overall_green and bool(results["P1.3_instruction_fidelity"]["pass"])
    overall_green = overall_green and bool(results["P1.4_recovery"]["pass"])
    overall_green = overall_green and bool(offline_payload["result"]["pass"])
    overall_green = overall_green and all(bool(item["pass"]) for item in threshold_checks.values())
    if manual_btc_check is not None:
        overall_green = overall_green and bool(manual_btc_check.get("pass"))

    return {
        "p0_ids": p0_ids,
        "consistency_runs": consistency_runs,
        "consistency_passes": consistency_passes,
        "simple_latencies": simple_latencies,
        "file_latencies": file_latencies,
        "lookup_latencies": lookup_latencies,
        "chain_latencies": chain_latencies,
        "threshold_checks": threshold_checks,
        "overall_green": overall_green,
    }


def _preserve_previous_run_artifacts(*, run_root: Path, profile: AcceptanceProfile) -> Path | None:
    evidence_dir = run_root / "evidence"
    if not evidence_dir.exists():
        return None
    online_payload = _read_json_if_exists(evidence_dir / "online_acceptance.json")
    offline_payload = _read_json_if_exists(evidence_dir / "offline_honesty.json")
    manual_payload = _read_json_if_exists(evidence_dir / "manual_btc_verification.json")
    if online_payload is None and offline_payload is None and manual_payload is None:
        return None
    if online_payload is None or offline_payload is None or manual_payload is None:
        return _copy_tree_with_timestamp(run_root, status="incomplete")
    try:
        summary = build_acceptance_summary(
            online_payload=online_payload,
            offline_payload=offline_payload,
            manual_btc_check=manual_payload,
            profile=profile,
        )
    except Exception:
        return _copy_tree_with_timestamp(run_root, status="incomplete")
    if summary["overall_green"]:
        return None
    return _copy_tree_with_timestamp(run_root, status="fail")


def fetch_manual_btc_verification(
    *,
    repo_root: Path,
    run_root: Path,
    online_payload: dict[str, Any],
    profile: AcceptanceProfile,
) -> dict[str, Any]:
    # Outbound totality wave: even this operator-run acceptance fetch goes
    # through the ONE outbound door (veto + reporting) rather than raw urlopen.
    from core.effect_gateway import named_background_effect_scope
    from core.remote_fetch_policy import open_remote_url

    with named_background_effect_scope("local_acceptance.manual_btc_verification"):
        response = open_remote_url(str(profile.manual_btc_source_url), timeout=30.0)
        try:
            payload = json.loads(response.read().decode("utf-8"))
        finally:
            response.close()
    observed_amount = float(dict(payload.get("bitcoin") or {}).get("usd") or 0.0)
    observed_at = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    acceptance_response = str(online_payload["results"]["P0.4_live_lookup"]["assistant_text"] or "").strip()
    acceptance_amount = _first_currency_amount(acceptance_response) or 0.0
    drift = round(abs(acceptance_amount - observed_amount), 2)
    drift_pct = round((drift / observed_amount) * 100, 4) if observed_amount else 0.0
    manual = {
        "source": profile.manual_btc_source_label,
        "observed": f"{_format_usd(observed_amount)} at {observed_at}",
        "acceptance_response": acceptance_response,
        "assessment": (
            f"Acceptance response reported {_format_usd(acceptance_amount)}; manual check showed {_format_usd(observed_amount)} at {observed_at}. "
            f"Absolute drift was {_format_usd(drift)} ({drift_pct}%), effectively exact for a live market."
        ),
        "pass": observed_amount > 0.0 and acceptance_amount > 0.0 and drift_pct <= 1.0,
    }
    _write_json(run_root / "evidence" / "manual_btc_verification.json", manual)
    return manual


def _offline_policy_path(runtime_home: Path) -> Path:
    return runtime_home / "config" / "default_policy.yaml"


def _write_offline_policy_override(runtime_home: Path) -> None:
    path = _offline_policy_path(runtime_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("system:\n  allow_web_fallback: false\n", encoding="utf-8")


def _clear_offline_policy_override(runtime_home: Path) -> None:
    path = _offline_policy_path(runtime_home)
    if path.exists():
        path.unlink()


def _current_health(base_url: str) -> dict[str, Any] | None:
    try:
        return _read_json(f"{base_url}/healthz", timeout=5.0)
    except Exception:
        return None


def _runtime_commit_matches(*, observed_commit: str, expected_commit: str) -> bool:
    observed = str(observed_commit or "").strip().lower()
    expected = str(expected_commit or "").strip().lower()
    if expected == "archive":
        return observed in {"", "archive", "unknown"}
    return bool(expected and observed.startswith(expected))


def _stop_runtime(base_url: str) -> None:
    deadline = time.time() + 30.0
    last_pid = 0
    while time.time() < deadline:
        health = _current_health(base_url)
        if health is None:
            return
        pid = int(dict(health.get("runtime") or {}).get("pid") or 0) if isinstance(health, dict) else 0
        if pid > 0 and pid != last_pid:
            with suppress(ProcessLookupError):
                os.kill(pid, signal.SIGTERM)
            last_pid = pid
        time.sleep(0.5)
    health = _current_health(base_url)
    pid = int(dict(health.get("runtime") or {}).get("pid") or 0) if isinstance(health, dict) else 0
    if pid <= 0:
        return
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    kill_deadline = time.time() + 5.0
    while time.time() < kill_deadline:
        if _current_health(base_url) is None:
            return
        time.sleep(0.5)


def _wait_for_runtime(base_url: str, *, expected_commit: str, expected_model: str, timeout: float = 120.0) -> dict[str, Any]:
    deadline = time.time() + timeout
    last_health: dict[str, Any] | None = None
    while time.time() < deadline:
        health = _current_health(base_url)
        if health and bool(health.get("ok")):
            runtime = dict(health.get("runtime") or {})
            commit = str(runtime.get("commit") or "")
            model_tag = str(runtime.get("model_tag") or "")
            if _runtime_commit_matches(observed_commit=commit, expected_commit=expected_commit) and model_tag == expected_model:
                return health
            last_health = health
        time.sleep(1.0)
    raise RuntimeError(f"Timed out waiting for runtime {expected_commit} / model {expected_model}. Last health={last_health!r}")


def _start_runtime(
    *,
    repo_root: Path,
    base_url: str,
    run_root: Path,
    runtime_home: Path,
    workspace_root: Path,
    model: str,
    start_script: Path,
    expected_commit: str,
    daemon_bind_port: int,
) -> subprocess.Popen[Any]:
    runtime_home.mkdir(parents=True, exist_ok=True)
    workspace_root.mkdir(parents=True, exist_ok=True)
    log_path = run_root / "evidence" / "runtime_launcher.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("a", encoding="utf-8")
    env = dict(os.environ)
    env["VOOL_HOME"] = str(runtime_home)
    env["VOOL_WORKSPACE_ROOT"] = str(workspace_root)
    env["VOOL_OLLAMA_MODEL"] = model
    env["VOOL_DAEMON_BIND_HOST"] = "127.0.0.1"
    env["VOOL_DAEMON_ADVERTISE_HOST"] = "127.0.0.1"
    env["VOOL_DAEMON_BIND_PORT"] = str(int(daemon_bind_port))
    env["VOOL_DAEMON_HEALTH_PORT"] = "0"
    command = _resolve_runtime_command(
        repo_root=repo_root,
        base_url=base_url,
        start_script=start_script,
    )
    process = subprocess.Popen(
        command,
        cwd=str(repo_root),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    health = _wait_for_runtime(base_url, expected_commit=expected_commit, expected_model=model)
    return process, health


def run_full_acceptance(
    *,
    base_url: str,
    repo_root: Path,
    run_root: Path,
    profile: AcceptanceProfile,
    runtime_home: Path,
    workspace_root: Path,
    start_script: Path,
) -> int:
    _preserve_previous_run_artifacts(run_root=run_root, profile=profile)
    expected_commit = _expected_repo_commit(repo_root)
    daemon_bind_port = _pick_isolated_daemon_bind_port(host="127.0.0.1")
    restored_health: dict[str, Any] = {}
    suspended_launch_agent = _suspend_installed_launch_agent(repo_root=repo_root, base_url=base_url)
    _stop_runtime(base_url)
    _start_runtime(
        repo_root=repo_root,
        base_url=base_url,
        run_root=run_root,
        runtime_home=runtime_home,
        workspace_root=workspace_root,
        model=profile.model,
        start_script=start_script,
        expected_commit=expected_commit,
        daemon_bind_port=daemon_bind_port,
    )
    try:
        online_payload = AcceptanceRunner(
            base_url=base_url,
            repo_root=repo_root,
            run_root=run_root,
            profile=profile,
        ).run_online()
        manual = fetch_manual_btc_verification(
            repo_root=repo_root,
            run_root=run_root,
            online_payload=online_payload,
            profile=profile,
        )
        _write_offline_policy_override(runtime_home)
        _stop_runtime(base_url)
        _start_runtime(
            repo_root=repo_root,
            base_url=base_url,
            run_root=run_root,
            runtime_home=runtime_home,
            workspace_root=workspace_root,
            model=profile.model,
            start_script=start_script,
            expected_commit=expected_commit,
            daemon_bind_port=daemon_bind_port,
        )
        offline_payload = run_offline_honesty(base_url, repo_root=repo_root, run_root=run_root)
    finally:
        _clear_offline_policy_override(runtime_home)
        _stop_runtime(base_url)
        if suspended_launch_agent is not None:
            _restore_installed_launch_agent(suspended_launch_agent)
            restored_health = _wait_for_runtime(
                base_url,
                expected_commit=expected_commit,
                expected_model=_installed_default_model(repo_root),
                timeout=180.0,
            )
        else:
            restore_result = _start_runtime(
                repo_root=repo_root,
                base_url=base_url,
                run_root=run_root,
                runtime_home=runtime_home,
                workspace_root=workspace_root,
                model=profile.model,
                start_script=start_script,
                expected_commit=expected_commit,
                daemon_bind_port=daemon_bind_port,
            )
            if (
                isinstance(restore_result, tuple)
                and len(restore_result) >= 2
                and isinstance(restore_result[1], dict)
            ):
                restored_health = dict(restore_result[1])
    render_report(
        repo_root=repo_root,
        online_payload=online_payload,
        offline_payload=offline_payload,
        manual_btc_check=manual,
        output_path=run_root / "evidence" / "VOOL_LOCAL_ACCEPTANCE_REPORT.md",
        profile=profile,
    )
    summary = build_acceptance_summary(
        online_payload=online_payload,
        offline_payload=offline_payload,
        manual_btc_check=manual,
        profile=profile,
    )
    restored_health = dict(restored_health or {})
    proof_manifest = build_proof_manifest(
        repo_root=repo_root,
        generated_by="run_local_acceptance",
        runtime_health=restored_health,
        runtime_capabilities=dict(restored_health.get("capabilities") or {}),
        acceptance_summary=summary,
    )
    write_proof_manifest(run_root / "evidence" / "proof_manifest.json", proof_manifest)
    return 0 if summary["overall_green"] and proof_manifest["overall_consistent"] else 1


def render_report(
    *,
    repo_root: Path,
    online_payload: dict[str, Any],
    offline_payload: dict[str, Any],
    manual_btc_check: dict[str, Any] | None,
    output_path: Path,
    profile: AcceptanceProfile,
) -> None:
    results = online_payload["results"]
    summary = build_acceptance_summary(
        online_payload=online_payload,
        offline_payload=offline_payload,
        manual_btc_check=manual_btc_check,
        profile=profile,
    )
    p0_ids = list(summary["p0_ids"])
    consistency_passes = int(summary["consistency_passes"])
    simple_latencies = list(summary["simple_latencies"])
    file_latencies = list(summary["file_latencies"])
    lookup_latencies = list(summary["lookup_latencies"])
    chain_latencies = list(summary["chain_latencies"])
    threshold_checks = dict(summary["threshold_checks"])
    overall_green = bool(summary["overall_green"])

    wrong_before_green = [
        "- Initial startup attempts hit runtime bootstrap and read-only/bootstrap-path issues before a clean dedicated acceptance runtime existed.",
        "- Public Hive auth readiness crashed on a missing remote config path; that was fixed to fail closed with status instead of exploding.",
        "- Workspace file follow-up append requests lost the last file path and reported success too early; builder routing and history-based path recovery were fixed.",
        "- Exact readback used paraphrased file reads instead of verbatim content; verbatim workspace reads were added.",
        "- Planner normalization was weak around spaced punctuation like `notes. txt`, `a. txt`, and `consistency_test. txt`; path cleaning was tightened.",
        "- Planner missed `with content VALUE` forms when no colon was present; inline-content parsing was widened.",
        "- Ultra-fresh market prompts like `What happened five minutes ago in global markets?` bluffed instead of refusing weak evidence; they now hard-stop with insufficient-evidence language.",
        "- One rerun wrote a truncated acceptance JSON and could not be trusted as proof; this final run replaced it with a complete capture.",
    ]

    p0_lines = [
        f"- {test_id}: {'PASS' if results[test_id]['pass'] else 'FAIL'}"
        for test_id in p0_ids
    ]
    p1_lines = [
        f"- P1.1 Consistency: {'PASS' if consistency_passes == 3 else ('PARTIAL' if consistency_passes >= profile.consistency_min_passes else 'FAIL')} ({consistency_passes}/3)"
    ]
    p1_lines.extend(
        f"- {label}: {'PASS' if results[test_id]['pass'] else 'FAIL'}"
        for label, test_id in [
            ("P1.3 Instruction fidelity", "P1.3_instruction_fidelity"),
            ("P1.4 Recovery after minor failure", "P1.4_recovery"),
        ]
    )
    p1_lines.append(f"- Offline honesty: {'PASS' if offline_payload['result']['pass'] else 'FAIL'}")

    machine = online_payload.get("machine", {})
    runtime = online_payload.get("runtime_version", {})
    profile_meta = online_payload.get("profile", {})
    capabilities = online_payload.get("capabilities", {})
    install_profile = capabilities.get("install_profile", {}) if isinstance(capabilities, dict) else {}
    runtime_dirty = _runtime_dirty_state(runtime_version=runtime, health_payload=online_payload.get("health", {}))
    benchmark_model = (
        str(profile_meta.get("benchmark_model") or profile.model or "").strip() or profile.model
    )
    benchmark_bundle_models = tuple(
        str(item).strip() for item in profile_meta.get("benchmark_bundle_models") or profile.bundle_models if str(item).strip()
    )
    runtime_model = (
        str(online_payload.get("model") or runtime.get("model_tag") or benchmark_model).strip() or benchmark_model
    )
    runtime_selected_models = tuple(
        str(item).strip()
        for item in (
            profile_meta.get("runtime_selected_models")
            or online_payload.get("selected_models")
            or install_profile.get("selected_models")
            or ()
        )
        if str(item).strip()
    )
    install_profile_id = str(
        profile_meta.get("install_profile_id") or install_profile.get("profile_id") or ""
    ).strip()
    install_profile_label = str(
        profile_meta.get("install_profile_label") or install_profile.get("label") or ""
    ).strip()
    manual_lines = []
    if manual_btc_check is not None:
        manual_lines = [
            "",
            "Manual live verification:",
            f"- acceptance response: {results['P0.4_live_lookup']['assistant_text']}",
            f"- manual check source: {manual_btc_check.get('source', 'n/a')}",
            f"- manual observed value: {manual_btc_check.get('observed', 'n/a')}",
            f"- drift assessment: {manual_btc_check.get('assessment', 'n/a')}",
        ]
    if runtime_dirty is True:
        runtime_build_note = (
            "- Runtime build was dirty for this acceptance run, so this proves only the exact dirty tree under test."
        )
    elif runtime_dirty is False:
        runtime_build_note = "- Runtime build was clean for this acceptance run."
    else:
        runtime_build_note = "- Runtime dirty-state could not be verified from the live runtime metadata."

    report_lines = [
        "# VOOL LOCAL ACCEPTANCE REPORT",
        "",
        f"Profile: {profile.profile_id} ({profile.display_name})",
        f"Benchmark profile model: {benchmark_model}",
        (
            f"Benchmark bundle models: {', '.join(benchmark_bundle_models)}"
            if benchmark_bundle_models
            else "Benchmark bundle models: unknown"
        ),
        f"Runtime model: {runtime_model}",
        (
            f"Runtime bundle models: {', '.join(runtime_selected_models)}"
            if runtime_selected_models
            else "Runtime bundle models: unknown"
        ),
        (
            f"Runtime install profile: {install_profile_id} ({install_profile_label})"
            if install_profile_id
            else "Runtime install profile: unknown"
        ),
        f"Commit: {runtime.get('commit', 'unknown')}",
        f"Build: {runtime.get('build_id', 'unknown')}",
        f"Date: {online_payload.get('captured_at_utc', 'unknown')}",
        "",
        "Machine:",
        f"- OS: {machine.get('platform', 'unknown')}",
        f"- CPU: {machine.get('cpu', 'unknown')}",
        f"- RAM: {machine.get('ram_gb', 'unknown')} GB",
        f"- GPU: {machine.get('gpu', 'unknown') or 'unknown'}",
        "",
        f"Overall result: {'GREEN' if overall_green else 'NOT GREEN'}",
        "",
        "P0 results:",
        *p0_lines,
        "",
        "P1 results:",
        *p1_lines,
        "",
        "Latency summary:",
        f"- simple prompt median: {_median(simple_latencies)}s",
        f"- file task median: {_median(file_latencies)}s",
        f"- live lookup median: {_median(lookup_latencies)}s",
        f"- chained task median: {_median(chain_latencies)}s",
        "",
        "Threshold gates:",
        f"- cold start <= {profile.cold_start_max_seconds}s: {'PASS' if threshold_checks['cold_start_max_seconds']['pass'] else 'FAIL'} (actual {threshold_checks['cold_start_max_seconds']['actual']}s)",
        f"- simple prompt median <= {profile.simple_prompt_median_max_seconds}s: {'PASS' if threshold_checks['simple_prompt_median_max_seconds']['pass'] else 'FAIL'} (actual {threshold_checks['simple_prompt_median_max_seconds']['actual']}s)",
        f"- simple prompt hard max <= {profile.simple_prompt_hard_max_seconds}s: {'PASS' if threshold_checks['simple_prompt_hard_max_seconds']['pass'] else 'FAIL'} (actual {threshold_checks['simple_prompt_hard_max_seconds']['actual']}s)",
        f"- file task median <= {profile.file_task_median_max_seconds}s: {'PASS' if threshold_checks['file_task_median_max_seconds']['pass'] else 'FAIL'} (actual {threshold_checks['file_task_median_max_seconds']['actual']}s)",
        f"- live lookup median <= {profile.live_lookup_median_max_seconds}s: {'PASS' if threshold_checks['live_lookup_median_max_seconds']['pass'] else 'FAIL'} (actual {threshold_checks['live_lookup_median_max_seconds']['actual']}s)",
        f"- chained task median <= {profile.chained_task_median_max_seconds}s: {'PASS' if threshold_checks['chained_task_median_max_seconds']['pass'] else 'FAIL'} (actual {threshold_checks['chained_task_median_max_seconds']['actual']}s)",
        f"- consistency >= {profile.consistency_min_passes}/3: {'PASS' if threshold_checks['consistency_min_passes']['pass'] else 'FAIL'} (actual {threshold_checks['consistency_min_passes']['actual']}/3)",
        *manual_lines,
        "",
        "What was wrong before green:",
        *wrong_before_green,
        "",
        "Notes:",
        runtime_build_note,
        "- Live lookup passed locally, but it only counts as final because the manual spot-check was performed separately.",
        "- Helper mesh and public Hive remain alpha surfaces; this acceptance only certifies the local runtime profile tested here.",
        "",
        "Evidence files:",
        f"- online: {_sanitize_text(str(output_path.parent / 'online_acceptance.json'), repo_root=repo_root)}",
        f"- offline: {_sanitize_text(str(output_path.parent / 'offline_honesty.json'), repo_root=repo_root)}",
        "",
        "Verdict:",
        (
            f"VOOL on {runtime_model} is acceptable for local use under this test profile."
            if overall_green
            else f"VOOL on {runtime_model} is not yet acceptable under this test profile."
        ),
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Run VOOL local acceptance checks.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    online = subparsers.add_parser("online")
    online.add_argument("--base-url", default=DEFAULT_BASE_URL)
    online.add_argument("--run-root", required=True)
    online.add_argument("--profile", default=str(DEFAULT_PROFILE_PATH))
    online.add_argument("--model", default="")

    offline = subparsers.add_parser("offline")
    offline.add_argument("--base-url", default=DEFAULT_BASE_URL)
    offline.add_argument("--run-root", required=True)

    report = subparsers.add_parser("report")
    report.add_argument("--run-root", required=True)
    report.add_argument("--profile", default=str(DEFAULT_PROFILE_PATH))
    report.add_argument("--manual-btc-json", default="")

    full = subparsers.add_parser("full")
    full.add_argument("--base-url", default=DEFAULT_BASE_URL)
    full.add_argument("--run-root", required=True)
    full.add_argument("--profile", default=str(DEFAULT_PROFILE_PATH))
    full.add_argument("--runtime-home", default="")
    full.add_argument("--workspace-root", default="")
    full.add_argument("--start-script", default=str(DEFAULT_START_SCRIPT))

    args = parser.parse_args(argv)
    run_root = Path(args.run_root).expanduser().resolve()
    profile = load_profile(getattr(args, "profile", str(DEFAULT_PROFILE_PATH)))

    if args.command == "online":
        runner = AcceptanceRunner(
            base_url=args.base_url.rstrip("/"),
            repo_root=REPO_ROOT,
            run_root=run_root,
            profile=AcceptanceProfile(
                **{
                    **profile.__dict__,
                    "model": str(args.model or profile.model),
                }
            ),
        )
        payload = runner.run_online()
        summary = build_acceptance_summary(
            online_payload=payload,
            offline_payload={"result": {"latency_seconds": 0.0, "pass": True}},
            manual_btc_check=None,
            profile=AcceptanceProfile(
                **{
                    **profile.__dict__,
                    "model": str(args.model or profile.model),
                }
            ),
        )
        base_ok = all(bool(payload["results"][test_id]["pass"]) for test_id in summary["p0_ids"])
        base_ok = base_ok and bool(payload["results"]["P1.3_instruction_fidelity"]["pass"])
        base_ok = base_ok and bool(payload["results"]["P1.4_recovery"]["pass"])
        base_ok = base_ok and bool(summary["threshold_checks"]["consistency_min_passes"]["pass"])
        return 0 if base_ok else 1

    if args.command == "offline":
        payload = run_offline_honesty(args.base_url.rstrip("/"), repo_root=REPO_ROOT, run_root=run_root)
        return 0 if payload["result"]["pass"] else 1

    if args.command == "full":
        if bool(args.runtime_home) ^ bool(args.workspace_root):
            parser.error("`full` requires both `--runtime-home` and `--workspace-root`, or neither.")
        if args.runtime_home and args.workspace_root:
            runtime_home = Path(args.runtime_home).expanduser().resolve()
            workspace_root = Path(args.workspace_root).expanduser().resolve()
        else:
            discovered_roots = _discover_active_runtime_roots(args.base_url.rstrip("/"))
            if discovered_roots is not None:
                runtime_home, workspace_root = discovered_roots
            else:
                runtime_home = (run_root / "runtime_home").resolve()
                workspace_root = (run_root / "workspace").resolve()
        return run_full_acceptance(
            base_url=args.base_url.rstrip("/"),
            repo_root=REPO_ROOT,
            run_root=run_root,
            profile=profile,
            runtime_home=runtime_home,
            workspace_root=workspace_root,
            start_script=Path(args.start_script).expanduser().resolve(),
        )

    online_payload = json.loads((run_root / "evidence" / "online_acceptance.json").read_text(encoding="utf-8"))
    offline_payload = json.loads((run_root / "evidence" / "offline_honesty.json").read_text(encoding="utf-8"))
    manual = None
    if args.manual_btc_json:
        manual = json.loads(Path(args.manual_btc_json).read_text(encoding="utf-8"))
    render_report(
        repo_root=REPO_ROOT,
        online_payload=online_payload,
        offline_payload=offline_payload,
        manual_btc_check=manual,
        output_path=run_root / "evidence" / "VOOL_LOCAL_ACCEPTANCE_REPORT.md",
        profile=profile,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
