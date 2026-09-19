"""Device authentication for the wallet: Touch ID or the account password, through the Keychain, via a small
Swift helper -- the only thing that may unlock a private-key export.

Design (operator decision 2026-09-07): each pocket wallet's seed is sealed a second time under a random
"unlock secret" that lives ONLY in a Keychain item with user-presence access control, device-bound and never
synced. Reading that item is what makes macOS ask for Touch ID or the password, so the operating system --
not a prompt in front of a file -- releases the secret only after the person proves presence. The PIN never
unlocks this path; export has no PIN parameter at all.

The helper is ~120 lines of Swift (LocalAuthentication + Security), compiled once with `swiftc` and cached by
the hash of its source, or shipped pre-built inside the app bundle.

Wire contract (protocol 2, 2026-09-07): argv carries ONLY the command (`selftest`, `store`, `read`, `forget`); the
request -- service, account, the secret, the prompt reason -- travels as one JSON object on stdin, a private pipe;
the reply is one JSON line on stdout. No secret ever appears in argv (visible to every process on the machine), in
the helper's environment (the parent passes a fixed minimal one), in logs, or in exception text (only a whitelisted
error code and a sanitized detail survive). A bundle uses its shipped helper unconditionally; a source checkout
compiles into the data directory. There is NO environment switch to a fake: tests inject an authority in-process
(`set_device_authority_for_tests`) or place a controlled helper where the runtime looks.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Protocol

SERVICE = "com.vool.wallet.unlock"
SWIFTC = "/usr/bin/swiftc"
TOOL_NAME = "vool-devauth"
TOOL_VERSION = "2"


class DeviceAuthUnavailable(Exception):
    """No device authentication on this machine (no helper, no Swift toolchain, not macOS)."""


class DeviceAuthDenied(Exception):
    """The person cancelled or failed Touch ID / the password prompt."""


class DeviceAuthority(Protocol):
    def available(self) -> bool: ...
    def describe(self) -> str: ...
    def store_unlock_secret(self, wallet_id: str, secret: bytes, *, reason: str) -> None: ...
    def read_unlock_secret(self, wallet_id: str, *, reason: str) -> bytes: ...
    def forget(self, wallet_id: str) -> None: ...


_SWIFT_SOURCE = r'''
import Foundation
import Security
import LocalAuthentication

func out(_ obj: [String: Any]) {
    let data = try! JSONSerialization.data(withJSONObject: obj)
    FileHandle.standardOutput.write(data); FileHandle.standardOutput.write("\n".data(using: .utf8)!)
}
func fail(_ error: String, _ detail: String = "") -> Never { out(["ok": false, "error": error, "detail": detail]); exit(1) }

let args = CommandLine.arguments
guard args.count == 2 else { fail("usage") }
let cmd = args[1]

if cmd == "selftest" {
    let ctx = LAContext(); var err: NSError?
    let can = ctx.canEvaluatePolicy(.deviceOwnerAuthentication, error: &err)
    out(["ok": true, "device_owner_authentication": can, "protocol": 2, "detail": err?.localizedDescription ?? ""]); exit(0)
}
// Every other command reads ONE JSON object from stdin. Nothing secret is ever a command-line argument.
let input = FileHandle.standardInput.readDataToEndOfFile()
guard let parsed = try? JSONSerialization.jsonObject(with: input), let req = parsed as? [String: Any] else { fail("bad_request") }
guard let service = req["service"] as? String, let account = req["account"] as? String, !service.isEmpty, !account.isEmpty else { fail("bad_request") }
let reason = (req["reason"] as? String) ?? "VOOL wallet"

if cmd == "store" {
    guard let b64 = req["secret_b64"] as? String, let secret = Data(base64Encoded: b64) else { fail("bad_request") }
    var acErr: Unmanaged<CFError>?
    guard let access = SecAccessControlCreateWithFlags(nil, kSecAttrAccessibleWhenUnlockedThisDeviceOnly, .userPresence, &acErr) else {
        fail("access_control", String(describing: acErr?.takeRetainedValue()))
    }
    let del: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecAttrAccount as String: account]
    SecItemDelete(del as CFDictionary)
    let add: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecAttrAccount as String: account,
                              kSecAttrLabel as String: reason, kSecValueData as String: secret, kSecAttrAccessControl as String: access,
                              kSecUseDataProtectionKeychain as String: true]
    let status = SecItemAdd(add as CFDictionary, nil)
    if status != errSecSuccess { fail("store_failed", String(status)) }
    out(["ok": true]); exit(0)
}
if cmd == "read" {
    let ctx = LAContext(); ctx.localizedReason = reason
    let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecAttrAccount as String: account,
                                kSecReturnData as String: true, kSecMatchLimit as String: kSecMatchLimitOne,
                                kSecUseAuthenticationContext as String: ctx, kSecUseOperationPrompt as String: reason,
                                kSecUseDataProtectionKeychain as String: true]
    var item: CFTypeRef?
    let status = SecItemCopyMatching(query as CFDictionary, &item)
    if status == errSecUserCanceled || status == errSecAuthFailed { fail("denied", String(status)) }
    if status == errSecItemNotFound { fail("not_found", String(status)) }
    if status != errSecSuccess { fail("read_failed", String(status)) }
    guard let data = item as? Data else { fail("read_failed", "no data") }
    out(["ok": true, "secret_b64": data.base64EncodedString()]); exit(0)
}
if cmd == "forget" {
    let del: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecAttrAccount as String: account,
                              kSecUseDataProtectionKeychain as String: true]
    let status = SecItemDelete(del as CFDictionary)
    out(["ok": status == errSecSuccess || status == errSecItemNotFound]); exit(0)
}
fail("usage")
'''


def swift_source() -> str:
    return _SWIFT_SOURCE


def _source_digest() -> str:
    return hashlib.sha256(_SWIFT_SOURCE.encode("utf-8")).hexdigest()[:16]


def tools_dir() -> Path:
    """Where a source checkout keeps the compiled helper: the active data directory, never an environment override."""
    try:
        from core.runtime_paths import active_data_dir

        return Path(active_data_dir()) / "wallet_tools"
    except Exception:
        return Path(tempfile.gettempdir()) / "vool_wallet_tools"


def shipped_helper() -> Path | None:
    """The helper compiled at bundle build time (Contents/Resources/bin), when this interpreter is the bundle's.

    Located from the interpreter alone; no environment variable can point a bundle at another binary."""
    candidate = Path(sys.executable).resolve().parent.parent.parent / "bin" / TOOL_NAME
    return candidate if candidate.exists() and os.access(candidate, os.X_OK) else None


def toolchain_available() -> bool:
    return platform.system() == "Darwin" and (shipped_helper() is not None or os.access(SWIFTC, os.X_OK))


# Default deployment floor for the compiled helper. Without an explicit -target, swiftc stamps
# the BUILD HOST's macOS as the minimum: the 45a22cf9 bundle shipped a vool-devauth requiring
# macOS 26.0 inside an app advertising LSMinimumSystemVersion 12.0, so on any older Mac the
# helper is a dyld failure rather than a typed "unavailable". The target is explicit here and
# the bundle build passes its own arch/floor, so the artifact matches what it advertises.
HELPER_MIN_MACOS = "14.0"


def _helper_target(arch: str | None = None, min_macos: str | None = None) -> str:
    """The -target triple for the helper: explicit architecture and deployment floor, never the host's."""
    return f"{arch or platform.machine()}-apple-macos{min_macos or HELPER_MIN_MACOS}"


def compile_helper(destination: Path, arch: str | None = None, min_macos: str | None = None) -> Path:
    """Compile the Swift helper to `destination` (used by the bundle build and by the on-demand cache)."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="vool-devauth-build-") as build_dir:
        source = Path(build_dir) / "tool.swift"
        source.write_text(_SWIFT_SOURCE, encoding="utf-8")
        staged = Path(build_dir) / "tool"
        completed = subprocess.run([SWIFTC, "-O", "-swift-version", "5", "-target", _helper_target(arch, min_macos),
                                    str(source), "-o", str(staged), "-framework", "LocalAuthentication", "-framework", "Security"],
                                   capture_output=True, timeout=600, check=False)
        if completed.returncode != 0 or not staged.exists():
            detail = (completed.stderr or b"").decode("utf-8", "replace").strip().splitlines()
            raise DeviceAuthUnavailable("the device authentication helper could not be compiled: " + ("; ".join(detail[-3:]) or "unknown compiler error"))
        pending = destination.with_name(destination.name + f".{os.getpid()}.tmp")
        shutil.copy2(staged, pending)
        os.chmod(pending, 0o700)
        os.replace(pending, destination)
    return destination


_ERROR_CODE = re.compile(r"^[a-z_]{1,40}$")
_NUMERIC_STATUS = re.compile(r"^-?\d{1,12}$")
_ENV_KEEP = ("HOME", "USER", "LOGNAME", "TMPDIR", "LANG", "LC_ALL", "__CF_USER_TEXT_ENCODING")


def _exception_text(payload: Any) -> str:
    """What a failed helper call may say: a whitelisted error code and, at most, a numeric OSStatus.

    Never the helper's free text -- a buggy or hostile helper could echo the request, and a secret is plain
    alphanumeric base64 that no character filter would catch."""
    error = payload.get("error") if isinstance(payload, dict) else None
    code = error if isinstance(error, str) and _ERROR_CODE.match(error) else "helper_failed"
    detail = payload.get("detail") if isinstance(payload, dict) else None
    status = detail.strip() if isinstance(detail, str) and _NUMERIC_STATUS.match(detail.strip()) else ""
    return f"{code} (status {status})" if status else code


def _helper_env() -> dict[str, str]:
    """The helper's environment: the few variables macOS needs to find the user's session, nothing else."""
    env = {key: os.environ[key] for key in _ENV_KEEP if key in os.environ}
    env["PATH"] = "/usr/bin:/bin"
    return env


class SwiftHelperAuthority:
    """The real thing: Keychain user-presence items through the compiled helper."""

    def __init__(self, *, binary: Path | None = None) -> None:
        # `binary` is an in-process test pin (a controlled helper); production never passes it
        self._pinned = Path(binary) if binary is not None else None

    def available(self) -> bool:
        return self._pinned is not None or toolchain_available()

    def describe(self) -> str:
        return "Touch ID or your Mac password (Keychain, this device only)"

    def _binary(self) -> Path:
        if self._pinned is not None:
            return self._pinned
        shipped = shipped_helper()
        if shipped is not None:
            return shipped
        if not toolchain_available():
            raise DeviceAuthUnavailable("this machine has no device authentication helper and no Swift toolchain to build one")
        binary = tools_dir() / f"{TOOL_NAME}-{TOOL_VERSION}-{_source_digest()}"
        if binary.exists() and os.access(binary, os.X_OK):
            return binary
        return compile_helper(binary)

    def _run(self, command: str, request: dict[str, Any] | None, *, timeout: float) -> dict[str, Any]:
        """One helper call: the command in argv, the request on stdin, the reply on stdout. Nothing else leaves."""
        binary = self._binary()
        body = json.dumps(request or {}).encode("utf-8")
        try:
            completed = subprocess.run([str(binary), command], input=body, capture_output=True, env=_helper_env(), timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            # `from None`: the TimeoutExpired object carries the partial stdout, which on `read` is the secret
            raise DeviceAuthDenied("the authentication prompt timed out") from None
        lines = (completed.stdout or b"").decode("utf-8", "replace").strip().splitlines()
        try:
            payload = json.loads(lines[-1]) if lines else {}
        except ValueError:
            payload = {}
        if not isinstance(payload, dict) or not payload.get("ok"):
            text = _exception_text(payload)
            if text.split(" ")[0] in ("denied", "not_found"):
                raise DeviceAuthDenied(text)
            raise DeviceAuthUnavailable(text)
        return payload

    def store_unlock_secret(self, wallet_id: str, secret: bytes, *, reason: str) -> None:
        self._run("store", {"service": SERVICE, "account": str(wallet_id), "secret_b64": base64.b64encode(bytes(secret)).decode("ascii"), "reason": str(reason)}, timeout=30)

    def read_unlock_secret(self, wallet_id: str, *, reason: str) -> bytes:
        payload = self._run("read", {"service": SERVICE, "account": str(wallet_id), "reason": str(reason)}, timeout=180)
        return base64.b64decode(str(payload.get("secret_b64") or ""))

    def forget(self, wallet_id: str) -> None:
        self._run("forget", {"service": SERVICE, "account": str(wallet_id)}, timeout=30)


_OVERRIDE: DeviceAuthority | None = None


def set_device_authority_for_tests(authority: DeviceAuthority | None) -> None:
    global _OVERRIDE
    _OVERRIDE = authority


def current_authority() -> DeviceAuthority:
    """The real helper, or the authority a test installed in this process. Nothing in the environment can change this."""
    if _OVERRIDE is not None:
        return _OVERRIDE
    return SwiftHelperAuthority()


def main(argv: list[str] | None = None) -> int:
    """`python -m core.wallet.device_auth --build <path> [--arch A] [--min-macos V]`: compile the helper for the bundle build.

    The bundle build passes its own target so the helper matches the artifact it ships in,
    instead of silently inheriting the build host's architecture and macOS version."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) >= 2 and args[0] == "--build":
        arch = args[args.index("--arch") + 1] if "--arch" in args else None
        min_macos = args[args.index("--min-macos") + 1] if "--min-macos" in args else None
        path = compile_helper(Path(args[1]), arch=arch, min_macos=min_macos)
        print(str(path))
        return 0
    print("usage: --build <destination> [--arch <arm64|x86_64>] [--min-macos <version>]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
