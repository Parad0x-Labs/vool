"""The isolated helper this runtime uses for local speech-to-text.

Apple ships the **Speech** framework, whose ``SFSpeechRecognizer`` can transcribe a file
entirely ON DEVICE (``requiresOnDeviceRecognition``) at production quality. It has no Python
binding, so this module carries a Swift program, compiles it once with ``/usr/bin/swiftc``,
caches the binary by the hash of its own source, and runs it under the reader sandbox — the
same shape as ``vision_tool``, which this module deliberately imitates.

Two properties are load-bearing:

* **Local means local.** The helper sets ``requiresOnDeviceRecognition = true``, so a machine
  without an on-device model refuses rather than silently sending the operator's voice to
  Apple's servers. There is no cloud speech path in this runtime.
* **Authorization is a dependency, like a decoder.** macOS gates the Speech framework behind a
  per-app permission. The helper PROBES the authorization status only — it never triggers the
  system permission prompt from inside an extraction — and an unauthorized machine gets the
  typed ``speech_recognizer_unauthorized`` with the remediation an operator can act on. Typed
  absence is safe; a surprise dialog is not.

The helper speaks JSON on stdout and opens no network path.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from ._limits import SUBPROCESS_TIMEOUT_S
from ._sandbox import run_confined
from ._types import ReaderFailed, ReaderUnavailable

SWIFTC = "/usr/bin/swiftc"
TOOL_NAME = "vool_speech_tool"
#: Bumped whenever the Swift source changes meaning; recorded on every transcript produced.
TOOL_VERSION = "1.0.0"

_SWIFT_SOURCE = r"""
import AVFoundation
import Foundation
import Speech

struct Segment: Encodable {
    let start: Double
    let end: Double
    let text: String
    let confidence: Double
}

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(message.data(using: .utf8)!)
    exit(2)
}

func emit(_ object: Any) {
    guard let data = try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys]) else {
        fail("could not encode result")
    }
    FileHandle.standardOutput.write(data)
}

func statusName(_ status: SFSpeechRecognizerAuthorizationStatus) -> String {
    switch status {
    case .authorized: return "authorized"
    case .denied: return "denied"
    case .restricted: return "restricted"
    case .notDetermined: return "notDetermined"
    @unknown default: return "unknown"
    }
}

/// Probe everything the caller needs to decide whether transcription can run HERE, without
/// requesting authorization and without opening a file. A probe never triggers the system
/// permission prompt: that decision belongs to the operator, not to an attachment upload.
func probe(_ localeID: String) {
    let status = SFSpeechRecognizer.authorizationStatus()
    let recognizer = SFSpeechRecognizer(locale: Locale(identifier: localeID))
    var onDevice = false
    if let recognizer = recognizer {
        onDevice = recognizer.supportsOnDeviceRecognition
    }
    emit([
        "engine": "Speech.SFSpeechRecognizer",
        "authorization": statusName(status),
        "recognizer_available": recognizer != nil,
        "supports_on_device_recognition": onDevice,
        "locale": localeID,
    ])
}

/// Transcribe one audio file ON DEVICE, bounded in time. Segments carry their own offsets and
/// confidence, so the caller can quote "t=1.2-3.8s" and state how sure the engine was. A
/// recognition that had not finished when the budget expired is reported as complete=false --
/// a partial answer the caller will disclose, never a silent stop.
func transcribe(_ path: String, _ localeID: String, _ maxSeconds: Double) {
    guard SFSpeechRecognizer.authorizationStatus() == .authorized else {
        fail("unauthorized: \(statusName(SFSpeechRecognizer.authorizationStatus()))")
    }
    guard let recognizer = SFSpeechRecognizer(locale: Locale(identifier: localeID)), recognizer.isAvailable else {
        fail("recognizer-unavailable")
    }
    guard recognizer.supportsOnDeviceRecognition else {
        fail("on-device-recognition-unavailable")
    }
    let url = URL(fileURLWithPath: path)
    guard let file = try? AVAudioFile(forReading: url) else {
        fail("unreadable-audio")
    }
    let duration = Double(file.length) / file.processingFormat.sampleRate
    let request = SFSpeechURLRecognitionRequest(url: url)
    request.shouldReportPartialResults = false
    request.requiresOnDeviceRecognition = true
    if #available(macOS 13.0, *) {
        request.addsPunctuation = true
    }
    var segments: [Segment] = []
    let done = DispatchSemaphore(value: 0)
    var failure: String = ""
    var complete = true
    var task: SFSpeechRecognitionTask?
    task = recognizer.recognitionTask(with: request) { result, error in
        if let result = result, result.isFinal {
            let best = result.bestTranscription
            for segment in best.segments {
                let text = segment.substring.trimmingCharacters(in: .whitespacesAndNewlines)
                guard !text.isEmpty else { continue }
                segments.append(Segment(
                    start: segment.timestamp,
                    end: segment.timestamp + segment.duration,
                    text: text,
                    confidence: Double(segment.confidence)
                ))
            }
            done.signal()
            return
        }
        if error != nil {
            failure = error!.localizedDescription
            done.signal()
        }
    }
    // The bound is wall-clock: recognition that has not finished when the budget expires is cut
    // off gracefully (finish() asks for the best-so-far) and reported as partial.
    if done.wait(timeout: .now() + maxSeconds) == .timedOut {
        complete = false
        task?.finish()
        _ = done.wait(timeout: .now() + 5)
        if segments.isEmpty {
            fail("recognition-timed-out: no speech recognised within \(Int(maxSeconds))s")
        }
    }
    if segments.isEmpty && !failure.isEmpty {
        fail("recognition-failed: \(failure)")
    }
    emit([
        "engine": "Speech.SFSpeechRecognizer",
        "locale": localeID,
        "duration_s": duration,
        "on_device": true,
        "complete": complete && failure.isEmpty,
        "segments": segments.map { ["start": $0.start, "end": $0.end, "text": $0.text, "confidence": $0.confidence] },
    ])
}

let arguments = CommandLine.arguments
guard arguments.count >= 3 else { fail("usage: tool <probe|transcribe> <path-or-locale> [locale] [maxSeconds]") }
switch arguments[1] {
case "probe":
    probe(arguments[2])
case "transcribe":
    guard arguments.count >= 5 else { fail("usage: tool transcribe <path> <locale> <maxSeconds>") }
    transcribe(arguments[2], arguments[3], Double(arguments[4]) ?? 600)
default:
    fail("unknown-subcommand")
}
"""


def _source_digest() -> str:
    return hashlib.sha256(_SWIFT_SOURCE.encode("utf-8")).hexdigest()[:16]


def tools_dir() -> Path:
    """Where the compiled helper is cached. Overridable so a test never touches operator state."""
    override = os.environ.get("VOOL_READER_TOOLS_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    try:
        from core.runtime_paths import active_data_dir

        return Path(active_data_dir()) / "reader_tools"
    except Exception:
        return Path(tempfile.gettempdir()) / "vool_reader_tools"


def toolchain_available() -> bool:
    """Buildable AND confinable. A helper this runtime could not sandbox is not available to it."""
    from . import _sandbox

    return platform.system() == "Darwin" and os.access(SWIFTC, os.X_OK) and _sandbox.confinement_available()


def _binary_path() -> Path:
    return tools_dir() / f"{TOOL_NAME}-{TOOL_VERSION}-{_source_digest()}"


def ensure_tool() -> Path:
    """The compiled helper, built once and cached by the hash of the source above."""
    if not toolchain_available():
        raise ReaderUnavailable(
            "This machine has no Swift toolchain, so the built-in speech recogniser cannot be built.",
            code="speech_toolchain_unavailable",
            remediation="Install the Xcode Command Line Tools (`xcode-select --install`) to enable local audio transcription and dictation.",
        )
    binary = _binary_path()
    if binary.exists() and os.access(binary, os.X_OK):
        return binary
    binary.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="vool-speech-build-") as build_dir:
        source = Path(build_dir) / "tool.swift"
        source.write_text(_SWIFT_SOURCE, encoding="utf-8")
        staged = Path(build_dir) / "tool"
        try:
            completed = subprocess.run(
                [SWIFTC, "-O", "-swift-version", "5", str(source), "-framework", "Speech",
                 "-framework", "AVFoundation", "-framework", "Foundation", "-o", str(staged)],
                capture_output=True,
                timeout=300,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ReaderUnavailable(
                f"The built-in speech helper could not be compiled ({exc}).",
                code="speech_build_failed",
                remediation="Check that the Xcode Command Line Tools are installed and working.",
            ) from exc
        if completed.returncode != 0 or not staged.exists():
            detail = (completed.stderr or b"").decode("utf-8", "replace").strip().splitlines()
            raise ReaderUnavailable(
                "The built-in speech helper could not be compiled: " + ("; ".join(detail[-3:]) or "unknown compiler error"),
                code="speech_build_failed",
                remediation="Check that the Xcode Command Line Tools are installed and working.",
            )
        # Atomic publish: a half-written binary is never visible to a concurrent extraction.
        pending = binary.with_name(binary.name + f".{os.getpid()}.tmp")
        shutil.copy2(staged, pending)
        os.chmod(pending, 0o700)
        os.replace(pending, binary)
    return binary


def _run(args: list[str], *, scratch: Path, timeout: int = SUBPROCESS_TIMEOUT_S) -> dict[str, Any]:
    binary = ensure_tool()
    result = run_confined(
        [str(binary), *args],
        scratch=scratch,
        timeout=timeout,
        fmt="audio",
        allow_gpu=True,
        extra_read=[str(binary.parent)],
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip() or f"exit {result.returncode}"
        if detail.startswith("unauthorized:"):
            state = detail.split(":", 1)[1].strip() or "not authorized"
            raise ReaderUnavailable(
                f"This machine has not granted Speech Recognition access, so audio was not transcribed ({state}).",
                code="speech_recognizer_unauthorized",
                remediation=(
                    "Enable Speech Recognition for this app in System Settings → Privacy & Security → "
                    "Speech Recognition. Until then audio attachments are refused with this reason and "
                    "dictation is unavailable."
                ),
                fmt="audio",
            )
        if "recognizer-unavailable" in detail:
            raise ReaderUnavailable(
                "No speech recogniser is installed for this language on this machine.",
                code="speech_recognizer_unavailable",
                remediation="Add the language in System Settings → General → Keyboard → Dictation languages, or attach a transcript as text.",
                fmt="audio",
            )
        if "on-device-recognition-unavailable" in detail:
            raise ReaderUnavailable(
                "This machine has no on-device speech model, and this runtime never sends voice to a cloud service.",
                code="speech_recognizer_on_device_unavailable",
                remediation="Enable on-device speech recognition, or attach a transcript as text.",
                fmt="audio",
            )
        if "unreadable-audio" in detail:
            raise ReaderFailed("The audio decoder failed: the file could not be opened as audio.", code="decoder_failed", fmt="audio")
        raise ReaderFailed(f"The audio decoder failed: {detail}", code="decoder_failed", fmt="audio")
    try:
        payload = json.loads(result.stdout.decode("utf-8", "replace") or "{}")
    except ValueError as exc:
        raise ReaderFailed("The audio decoder produced output this runtime could not read.", code="decoder_bad_output", fmt="audio") from exc
    return payload if isinstance(payload, dict) else {}


#: Default locale. The helper probes/transcribes one locale; more can be added per call.
DEFAULT_LOCALE = "en_US"


#: Probe results, briefly cached. Availability is still resolved through this module on every
#: call (a decoder can appear or disappear between one page load and the next), but a probe is
#: a subprocess and the door consults it on every upload: thirty seconds keeps that honest for
#: humans and keeps it from being a per-upload tax.
_PROBE_TTL_S = 30.0
_PROBE_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


def probe(*, locale: str = DEFAULT_LOCALE, scratch: Path | None = None) -> dict[str, Any]:
    """Authorization/recogniser facts for THIS machine, without ever requesting authorization."""
    import time as _time

    now = _time.monotonic()
    cached = _PROBE_CACHE.get(locale)
    if cached is not None and now - cached[0] < _PROBE_TTL_S:
        return dict(cached[1])
    if scratch is None:
        with tempfile.TemporaryDirectory(prefix="vool-speech-probe-") as tmp:
            facts = probe(locale=locale, scratch=Path(tmp))
    else:
        facts = _run(["probe", locale], scratch=scratch, timeout=30)
    _PROBE_CACHE[locale] = (now, dict(facts))
    return facts


def speech_available(*, locale: str = DEFAULT_LOCALE, probe_if_needed: bool = True) -> bool:
    """Check speech, or read cheap cached facts with ``probe_if_needed=False``.

    An explicit transcription/probe refreshes the facts. Unknown or expired
    authorization is unavailable until measured, never an optimistic grant.
    """
    import time

    if probe_if_needed:
        try:
            facts = probe(locale=locale)
        except Exception:
            return False
    else:
        cached = _PROBE_CACHE.get(locale)
        if cached is None or time.monotonic() - cached[0] >= _PROBE_TTL_S:
            return False
        facts = cached[1]
    return (
        str(facts.get("authorization") or "") == "authorized"
        and facts.get("recognizer_available") is True
        and facts.get("supports_on_device_recognition") is True
    )


def transcribe_unavailable(locale: str = DEFAULT_LOCALE) -> ReaderUnavailable | None:
    """THE typed reason transcription cannot run here, probed without side effects. None = it can."""
    if not toolchain_available():
        return ReaderUnavailable(
            "This machine has no Swift toolchain, so the built-in speech recogniser cannot be built.",
            code="speech_toolchain_unavailable",
            remediation="Install the Xcode Command Line Tools (`xcode-select --install`) to enable local audio transcription and dictation.",
            fmt="audio",
        )
    try:
        facts = probe(locale=locale)
    except ReaderUnavailable:
        raise
    except Exception as exc:
        return ReaderUnavailable(
            f"The speech recogniser could not be probed on this machine ({exc}).",
            code="speech_probe_failed",
            remediation="Check that the Xcode Command Line Tools are installed and working.",
            fmt="audio",
        )
    authorization = str(facts.get("authorization") or "")
    if authorization != "authorized":
        return ReaderUnavailable(
            f"This machine has not granted Speech Recognition access (status: {authorization}), so audio was not transcribed.",
            code="speech_recognizer_unauthorized",
            remediation=(
                "Enable Speech Recognition for this app in System Settings → Privacy & Security → "
                "Speech Recognition. Until then audio attachments are refused with this reason and "
                "dictation is unavailable."
            ),
            fmt="audio",
        )
    if facts.get("recognizer_available") is not True:
        return ReaderUnavailable(
            "No speech recogniser is installed for this language on this machine.",
            code="speech_recognizer_unavailable",
            remediation="Add the language in System Settings → General → Keyboard → Dictation languages, or attach a transcript as text.",
            fmt="audio",
        )
    if facts.get("supports_on_device_recognition") is not True:
        return ReaderUnavailable(
            "This machine has no on-device speech model, and this runtime never sends voice to a cloud service.",
            code="speech_recognizer_on_device_unavailable",
            remediation="Enable on-device speech recognition, or attach a transcript as text.",
            fmt="audio",
        )
    return None


def transcribe(audio_path: Path, *, locale: str = DEFAULT_LOCALE, max_seconds: int, scratch: Path) -> dict[str, Any]:
    """On-device transcription of one audio file: segments with offsets and confidence."""
    return _run(["transcribe", str(audio_path), locale, str(int(max_seconds))], scratch=scratch, timeout=max(SUBPROCESS_TIMEOUT_S, int(max_seconds) + 30))


__all__ = [
    "DEFAULT_LOCALE",
    "TOOL_VERSION",
    "ensure_tool",
    "probe",
    "speech_available",
    "toolchain_available",
    "tools_dir",
    "transcribe",
    "transcribe_unavailable",
]
