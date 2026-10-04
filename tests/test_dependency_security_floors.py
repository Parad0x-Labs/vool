"""Dependency security floors for the open Python advisories against this repository.

Dependabot alerts on `uv.lock` (intake 2026-10-04, all OPEN at delivery time):

    #59/#60/#61  urllib3  GHSA-8988-9cw3-xx77 / GHSA-vxq7-64xx-v4gw / GHSA-gh4c-6fx4-qh6g
                 (proxy-TLS override, unbounded chunk-size line, chunked-Deflate loop)
                 first patched: 2.8.0
    #62          datasets GHSA-379c-qx7v-6h59 (folder-builder `file_name` path traversal)
                 first patched: 5.0.1
    #46          accelerate GHSA-4j2p-28q2-5m79 (weight_map traversal + FIFO DoS)
                 upstream lists NO first patched version; this repository holds the staged-base
                 checkpoint confinement (tests/test_trainable_base_manager.py,
                 tests/test_peft_lora_adapter_confinement.py) as CONTAINMENT, not a fix.

Three claim families, kept deliberately separate so none can stand in for another:

1. LOCK floors -- what `uv.lock` resolves for every synced environment, proven by parsing the
   committed lock bytes (tomllib, tomli fallback on 3.10 per the declared dev dependency).
2. INSTALLED floors -- what THIS interpreter actually imported. A lock claim says nothing about a
   stale virtualenv, and an installed claim says nothing about the lock. `urllib3` rides `requests`,
   a hard dependency, so it must always be present (its absence fails closed). `datasets` and
   `accelerate` live in the opt-in `models`/`runtime` extras: when they are absent the control
   SKIPs with a reason that names the gap -- a skip is an honest absence, never a pass.
3. REAL CONSUMER controls -- the product's own streaming transport
   (`core/provider_http.py`: `requests.request(stream=True)` + `iter_content(512)` in the
   transfer worker) driven against a local chunked HTTP server, and the demo planner's bounded
   fetch (`core/demo_source._bounded_get`) through the one outbound door. These prove the bump
   did not change observable transport behavior, and that the transport-layer bound -- not the
   product's wall-clock deadline -- is what refuses a malicious chunk-size line on urllib3 >= 2.8.0.

The version floors are discriminating controls: lowering the locked or installed version below
the advisory patch floor makes exactly these tests fail (verified RED/GREEN in the delivery
receipts; a fixture or row count alone was rejected as proof in the 2026-10-03 return review).
"""

from __future__ import annotations

import hashlib
import json
import struct
import threading
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import metadata
from pathlib import Path
from unittest import mock

import pytest
import requests

from core import provider_http

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10: declared dev dependency `tomli>=2.0`
    import tomli as tomllib

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import Version

REPO_ROOT = Path(__file__).resolve().parent.parent

#: (package, alert numbers, GHSA ids, first patched version) for the closable advisories.
PATCH_FLOORS = {
    "urllib3": ("#59/#60/#61", "GHSA-8988-9cw3-xx77 / GHSA-vxq7-64xx-v4gw / GHSA-gh4c-6fx4-qh6g", "2.8.0"),
    "datasets": ("#62", "GHSA-379c-qx7v-6h59", "5.0.1"),
}

#: Alert #46: accelerate has NO first patched version upstream. The lock holds the contained
#: release so any future bump is a deliberate, reviewed act against the open advisory -- never a
#: silent resolver drift that could be mistaken for a fix. Containment itself is proven by the
#: checkpoint admission/refusal suites, not here.
CONTAINED_RELEASES = {"accelerate": ("#46", "GHSA-4j2p-28q2-5m79", "1.14.0")}


def _lock_packages() -> dict[str, list[str]]:
    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))
    found: dict[str, list[str]] = {}
    for entry in lock.get("package", []):
        found.setdefault(str(entry.get("name")), []).append(str(entry.get("version")))
    return found


def _pyproject_constraint_floors() -> dict[str, list[str]]:
    """The resolver-side floors from `[tool.uv] constraint-dependencies` in pyproject.toml.

    Lock floors alone only test the committed bytes: a future `uv lock` re-resolution could drift
    back below an advisory floor and nothing would notice until these tests ran. The declared
    constraints make the resolver itself hold the floor, so `uv lock --check` fails on a regressed
    resolution before any environment is built from it.
    """
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    floors: dict[str, list[str]] = {}
    for constraint in project.get("tool", {}).get("uv", {}).get("constraint-dependencies", []):
        requirement = Requirement(constraint.strip())
        floors.setdefault(requirement.name, []).append(str(requirement.specifier))
    return floors


# --------------------------------------------------------------------------------------
# 1. LOCK floors (committed uv.lock bytes)
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("package", sorted(PATCH_FLOORS))
def test_lock_resolves_each_advisory_package_at_or_above_its_patch_floor(package: str) -> None:
    alerts, ghsa, patched = PATCH_FLOORS[package]
    versions = _lock_packages().get(package, [])
    assert versions, f"{package} is absent from uv.lock; the {alerts} ({ghsa}) floor cannot be checked"
    for version in versions:
        assert Version(version) >= Version(patched), (
            f"uv.lock resolves {package} {version}, below the {alerts} ({ghsa}) first-patched "
            f"release {patched}; re-resolve with the constraint floors in pyproject.toml"
        )


@pytest.mark.parametrize("package", sorted(CONTAINED_RELEASES))
def test_lock_holds_each_contained_release_for_the_unpatched_advisory(package: str) -> None:
    alerts, ghsa, release = CONTAINED_RELEASES[package]
    versions = _lock_packages().get(package, [])
    assert versions, f"{package} is absent from uv.lock; the {alerts} ({ghsa}) containment cannot be checked"
    for version in versions:
        assert version == release, (
            f"uv.lock resolves {package} {version} instead of the contained release {release}. "
            f"{alerts} ({ghsa}) lists NO first patched version: a bump is not a fix and needs an "
            "explicit advisory-state review, not silent resolver drift"
        )


def test_resolver_constraints_declare_the_patch_floors() -> None:
    floors = _pyproject_constraint_floors()
    for package, (alerts, ghsa, patched) in PATCH_FLOORS.items():
        declared = floors.get(package, [])
        assert declared, f"pyproject.toml declares no [tool.uv] constraint floor for {package} ({alerts})"
        assert any(
            Version(specifier.version) >= Version(patched)
            for floor in declared
            for specifier in SpecifierSet(floor)
            if specifier.operator in {">=", "==", "~=", "==="}
        ), (
            f"the declared {package} constraint floors {declared} sit below the {alerts} ({ghsa}) "
            f"first-patched release {patched}"
        )


# --------------------------------------------------------------------------------------
# 2. INSTALLED floors (this interpreter; distinct evidence from the lock claims)
# --------------------------------------------------------------------------------------


def test_installed_urllib3_meets_the_patch_floor() -> None:
    """`requests` is a hard dependency, so its transport must be importable here; absence fails closed."""
    alerts, ghsa, patched = PATCH_FLOORS["urllib3"]
    try:
        installed = metadata.version("urllib3")
    except metadata.PackageNotFoundError:  # pragma: no cover - fail closed, never skip
        pytest.fail(
            "urllib3 is not installed in this environment although requests is a hard dependency; "
            "the installed-transport state is unknown and fails closed"
        )
    assert Version(installed) >= Version(patched), (
        f"installed urllib3 {installed} is below the {alerts} ({ghsa}) first-patched release "
        f"{patched}; this environment was built from a stale or hand-mutated resolution"
    )


def test_installed_datasets_meets_the_patch_floor_or_names_the_gap() -> None:
    alerts, ghsa, patched = PATCH_FLOORS["datasets"]
    try:
        import datasets  # presence probe; behavior controls live below
    except ModuleNotFoundError:
        pytest.skip(
            "datasets (models/runtime extra) is not installed in this environment: an explicit "
            "installed-extras gap. The uv.lock floor above still binds every synced environment; "
            "the behavioral traversal controls below are proven in the delivery receipts on "
            "datasets 5.0.1 (GREEN) and 5.0.0 (RED)"
        )
    installed = metadata.version("datasets")
    assert Version(installed) >= Version(patched), (
        f"installed datasets {installed} is below the {alerts} ({ghsa}) first-patched release {patched}"
    )


def test_installed_accelerate_stays_at_the_contained_release_or_names_the_gap() -> None:
    alerts, ghsa, release = CONTAINED_RELEASES["accelerate"]
    try:
        import accelerate  # presence probe
    except ModuleNotFoundError:
        pytest.skip(
            "accelerate (models/runtime extra) is not installed in this environment: an explicit "
            "installed-extras gap. Containment is the checkpoint admission/refusal suites, which "
            "do not need accelerate installed to prove refusal-before-load"
        )
    installed = metadata.version("accelerate")
    assert installed == release, (
        f"installed accelerate {installed} is not the contained release {release}. {alerts} ({ghsa}) "
        "lists NO first patched version: a bump claims nothing and needs explicit advisory review"
    )


# --------------------------------------------------------------------------------------
# 3. REAL CONSUMER controls (the product's own streaming transport and bounded fetch)
# --------------------------------------------------------------------------------------

#: The exact body the chunked server streams: 300 KB of non-repeating bytes so a truncated,
#: reordered or doubled transfer cannot match by accident.
_CHUNKED_BODY = bytes(range(256)) * 1200 + b"-final-marker-"
_DEFLATE_BODY = zlib.compress(b"deflate-me: " * 20000, 9)
#: GHSA-gh4c-6fx4-qh6g (#61): a COMPLETE deflate stream followed by trailing bytes. urllib3 < 2.8.0
#: re-feeds the decompressor its own unconsumed tail after zlib eof and never returns from read();
#: 2.8.0's eof guard makes post-eof data produce nothing, so the read completes with the exact
#: decompressed prefix.
_DEFLATE_LOOP_PLAINTEXT = b"deflate-loop-marker: " * 500
_DEFLATE_LOOP_BODY = zlib.compress(_DEFLATE_LOOP_PLAINTEXT) + b"\x00" * 64


class _ChunkedServer:
    """A local HTTP/1.1 server that answers with hand-framed chunked transfer responses.

    `keep_open` paths deliberately never terminate their chunk-size line, which is the exact
    attacker shape of GHSA-vxq7-64xx-v4gw (#60): an unterminated chunk-size line that a vulnerable
    urllib3 buffers without bound.
    """

    def __init__(self) -> None:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: object) -> None:
                pass

            def do_GET(self) -> None:
                self.server.requests.append(self.path)
                try:
                    if self.path == "/chunked":
                        self.send_response(200)
                        self.send_header("Content-Type", "application/octet-stream")
                        self.send_header("Transfer-Encoding", "chunked")
                        self.end_headers()
                        for start in range(0, len(_CHUNKED_BODY), 8192):
                            outer._write_chunk(self, _CHUNKED_BODY[start : start + 8192])
                        self.wfile.write(b"0\r\n\r\n")
                    elif self.path == "/deflate":
                        self.send_response(200)
                        self.send_header("Content-Type", "application/octet-stream")
                        self.send_header("Content-Encoding", "deflate")
                        self.send_header("Transfer-Encoding", "chunked")
                        self.end_headers()
                        for start in range(0, len(_DEFLATE_BODY), 7919):
                            outer._write_chunk(self, _DEFLATE_BODY[start : start + 7919])
                        self.wfile.write(b"0\r\n\r\n")
                    elif self.path == "/deflate-loop":
                        self.send_response(200)
                        self.send_header("Content-Type", "application/octet-stream")
                        self.send_header("Content-Encoding", "deflate")
                        self.send_header("Transfer-Encoding", "chunked")
                        self.end_headers()
                        outer._write_chunk(self, _DEFLATE_LOOP_BODY)
                        self.wfile.write(b"0\r\n\r\n")
                    elif self.path == "/oversize-line":
                        self.send_response(200)
                        self.send_header("Content-Type", "application/octet-stream")
                        self.send_header("Transfer-Encoding", "chunked")
                        self.end_headers()
                        # 64 KiB + 4 KiB of hex-digit line with NO terminator: urllib3 >= 2.8.0
                        # refuses the line at the 64 KiB bound; < 2.8.0 buffers it unbounded.
                        self.wfile.write(b"f" * (2**16 + 4096))
                        self.wfile.flush()
                        self.server.stopped.wait(30.0)
                    else:
                        self.send_response(404)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self._handler = Handler
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.http.requests: list[str] = []
        self.http.stopped = threading.Event()
        self.thread = threading.Thread(target=self.http.serve_forever)
        self.thread.start()

    @staticmethod
    def _write_chunk(handler: BaseHTTPRequestHandler, data: bytes) -> None:
        handler.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.http.server_port}"

    def close(self) -> None:
        self.http.stopped.set()
        self.http.shutdown()
        self.http.server_close()
        self.thread.join()


@pytest.fixture
def chunked_server():
    server = _ChunkedServer()
    yield server
    server.close()


@pytest.fixture
def tracked_workers(monkeypatch):
    """Every transfer worker the suite spawns must be reaped before the test ends."""
    processes = []
    spawn = provider_http.subprocess.Popen

    def tracked(*args, **kwargs):
        process = spawn(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(provider_http.subprocess, "Popen", tracked)
    yield processes
    assert all(process.poll() is not None for process in processes)


def test_the_real_streaming_consumer_reads_a_chunked_body_byte_for_byte(chunked_server, tracked_workers) -> None:
    """The product's provider transport (`requests.request(stream=True)` + `iter_content(512)` in
    `core/provider_http.py`'s worker) must carry a chunked body with no byte lost, doubled or
    reordered on the patched urllib3 -- completion alone would not be proof."""
    response = provider_http.get(f"{chunked_server.url}/chunked", stream=True, timeout=20.0)
    body = response.content
    assert body == _CHUNKED_BODY
    assert hashlib.sha256(body).hexdigest() == hashlib.sha256(_CHUNKED_BODY).hexdigest()


def test_the_real_streaming_consumer_decodes_chunked_deflate_exactly(chunked_server, tracked_workers) -> None:
    """Chunked + Content-Encoding: deflate crosses urllib3's DeflateDecoder on every streamed
    provider response; the patched decoder must still yield the exact original bytes."""
    response = provider_http.get(f"{chunked_server.url}/deflate", stream=True, timeout=20.0)
    assert response.content == b"deflate-me: " * 20000


def test_a_completed_deflate_stream_with_trailing_data_returns_instead_of_looping(
    chunked_server, tracked_workers
) -> None:
    """GHSA-gh4c-6fx4-qh6g (#61) through the product transport: zlib-eof followed by trailing
    bytes. On urllib3 >= 2.8.0 the read completes with the exact decompressed prefix; on a
    vulnerable urllib3 the read never returns, so the only thing that ends the transfer is the
    product's wall-clock deadline (`requests.Timeout`) -- the exact outcome refused here."""
    started = time.monotonic()
    response = provider_http.get(f"{chunked_server.url}/deflate-loop", stream=True, timeout=6.0)
    assert response.content == _DEFLATE_LOOP_PLAINTEXT
    assert time.monotonic() - started < 5.0, (
        "the transfer must complete promptly on the patched decoder, not ride the deadline"
    )


def test_an_oversized_chunk_size_line_is_refused_by_the_transport_bound_not_the_deadline(
    chunked_server, tracked_workers
) -> None:
    """GHSA-vxq7-64xx-v4gw (#60) through the product's own transport: on urllib3 >= 2.8.0 the
    unterminated 68 KiB chunk-size line is refused by the transport's 64 KiB line bound (a fast
    `ChunkedEncodingError` surfaced by the worker). On a vulnerable urllib3 there is no bound: the
    only thing that ends the transfer is the product's wall-clock deadline (`requests.Timeout`
    after `timeout=` seconds), which is exactly the outcome this control refuses to accept."""
    started = time.monotonic()
    with pytest.raises(requests.exceptions.ConnectionError) as raised:
        response = provider_http.get(f"{chunked_server.url}/oversize-line", stream=True, timeout=8.0)
        _ = response.content   # stream=True returns at headers; the bound bites on the body read
    elapsed = time.monotonic() - started
    assert "ChunkedEncodingError" in str(raised.value), (
        f"the worker surfaced {raised.value!r}; the urllib3 2.8.0 chunk-line bound should reach "
        "the parent as a ChunkedEncodingError-class ConnectionError"
    )
    assert not isinstance(raised.value, requests.exceptions.Timeout)
    assert elapsed < 7.0, (
        f"the refusal took {elapsed:.1f}s: the transport bound should refuse in milliseconds, "
        "well before the 8s wall-clock deadline (a deadline-length refusal is the vulnerable shape)"
    )


def test_the_bounded_demo_fetch_refuses_fail_closed_when_the_turn_forbids_remote() -> None:
    """`core/demo_source._bounded_get` goes through the ONE outbound door; a turn that vetoed
    remote fetching must get the typed refusal BEFORE any socket opens (byte cap and deadline are
    pinned separately by test_demo_source/test_provider_http_lifetime)."""
    import core.demo_source as demo_source
    from core.remote_fetch_policy import RemoteFetchRefusedError, remote_fetch_policy_scope

    with remote_fetch_policy_scope({"allow_remote_fetch": False}), mock.patch(
        "urllib.request.urlopen",
        side_effect=AssertionError("the veto must refuse before any socket opens"),
    ) as urlopen:
        with pytest.raises(RemoteFetchRefusedError):
            demo_source._bounded_get("https://api.github.com/repos/example/example/readme", {})
    urlopen.assert_not_called()


# --------------------------------------------------------------------------------------
# 4. datasets folder-builder traversal (GHSA-379c-qx7v-6h59 / #62), behavioral on the extra
# --------------------------------------------------------------------------------------

_INSIDE_PIXEL = b"\x12\x34\x56"    # the in-bounds file's exact 1x1 RGB content
_OUTSIDE_PIXEL = b"\xab\xcd\xef"   # the outside-data_dir target's exact 1x1 RGB content


def _one_pixel_png(rgb: bytes) -> bytes:
    """A minimal valid 1x1 truecolor PNG, built (not fetched) so the fixture needs no assets."""

    def frame(tag: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", zlib.crc32(tag + payload))

    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + frame(b"IHDR", header)
        + frame(b"IDAT", zlib.compress(b"\x00" + rgb))
        + frame(b"IEND", b"")
    )


@pytest.fixture
def traversal_root(tmp_path, monkeypatch):
    """A folder-builder dataset whose metadata escapes data_dir itself.

    Layout (data_dir is `dataset/`; the escape target is a SIBLING of data_dir, outside it):

        root/dataset/train/metadata.jsonl   -> file_name "../../escape_target/outside_marker.png"
        root/dataset/train/inside_marker.png
        root/escape_target/outside_marker.png

    The prior rejected delivery pointed at a file outside the metadata directory but still inside
    data_dir and proved only a row count; this fixture's target is outside data_dir entirely and
    attribution is by exact content, below.
    """
    dataset_dir = tmp_path / "dataset"
    split_dir = dataset_dir / "train"
    escape_dir = tmp_path / "escape_target"
    split_dir.mkdir(parents=True)
    escape_dir.mkdir()
    (split_dir / "inside_marker.png").write_bytes(_one_pixel_png(_INSIDE_PIXEL))
    (escape_dir / "outside_marker.png").write_bytes(_one_pixel_png(_OUTSIDE_PIXEL))

    # The load-bearing property: the metadata's traversal target lands OUTSIDE data_dir itself.
    escape_file = escape_dir / "outside_marker.png"
    target = (split_dir / "../../escape_target/outside_marker.png").resolve()
    assert target == escape_file.resolve()
    assert dataset_dir.resolve() not in target.parents

    # Keep the builder entirely offline and inside the test's scratch: no hub reach, no shared cache.
    cache_dir = tmp_path / "hf-cache"
    monkeypatch.setenv("HF_HOME", str(cache_dir))
    monkeypatch.setenv("HF_DATASETS_CACHE", str(cache_dir / "datasets"))
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("HF_DATASETS_OFFLINE", "1")
    return dataset_dir, split_dir, escape_dir


def _row_rgb(row: dict) -> bytes:
    image = row["image"].convert("RGB")
    assert image.size == (1, 1), f"the fixture is 1x1; got {image.size}"
    return image.tobytes()


def test_folder_builder_metadata_file_name_cannot_escape_data_dir(traversal_root) -> None:
    datasets = pytest.importorskip(
        "datasets",
        reason="models/runtime extra absent: an explicit installed-extras gap (lock floor still binds; "
        "RED/GREEN behavioral receipts are in the delivery evidence)",
    )
    from datasets.exceptions import DatasetGenerationError

    dataset_dir, split_dir, escape_dir = traversal_root
    (split_dir / "metadata.jsonl").write_text(
        json.dumps({"file_name": "../../escape_target/outside_marker.png", "note": "escape"}) + "\n",
        encoding="utf-8",
    )
    # datasets 5.x wraps the folder-builder guard's ValueError in DatasetGenerationError, so the
    # refusal is asserted on the exact guard text carried by the exception chain, not on the
    # wrapper's generic message. A vulnerable 5.0.0 raises nothing here and builds rows from the
    # outside file instead (RED receipt).
    with pytest.raises((DatasetGenerationError, ValueError)) as raised:
        datasets.load_dataset("imagefolder", data_dir=str(dataset_dir))
    chain: list[str] = []
    exc: BaseException | None = raised.value
    while exc is not None:
        chain.append(str(exc))
        exc = exc.__cause__
    assert any("Invalid metadata file_name" in text and "escape" in text for text in chain), (
        f"the refusal must be the folder-builder file_name guard; exception chain was: {chain!r}"
    )
    outside = (escape_dir / "outside_marker.png").read_bytes()
    assert hashlib.sha256(outside).hexdigest() != hashlib.sha256(_one_pixel_png(_INSIDE_PIXEL)).hexdigest(), (
        "the two fixture files must differ in content for attribution to mean anything"
    )


def test_folder_builder_valid_metadata_builds_with_exact_content_attribution(traversal_root) -> None:
    datasets = pytest.importorskip(
        "datasets",
        reason="models/runtime extra absent: an explicit installed-extras gap (lock floor still binds; "
        "RED/GREEN behavioral receipts are in the delivery evidence)",
    )
    dataset_dir, split_dir, _escape_dir = traversal_root
    (split_dir / "metadata.jsonl").write_text(
        json.dumps({"file_name": "inside_marker.png", "note": "valid"}) + "\n", encoding="utf-8"
    )
    built = datasets.load_dataset("imagefolder", data_dir=str(dataset_dir))
    rows = list(built["train"])
    assert len(rows) == 1, "exactly one row: the valid metadata names exactly one in-bounds file"
    assert _row_rgb(rows[0]) == _INSIDE_PIXEL, (
        "the built row must carry the exact content of the in-bounds file it names (attribution "
        "by content, not row count)"
    )
