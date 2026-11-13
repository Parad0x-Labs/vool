"""A FIDO2 security key (YubiKey, SoloKey, Nitrokey, Google Titan...) as a second thing a wallet needs to open.

VOOL asks the key for its hmac-secret: a 32-byte value the key computes from a secret that never leaves it and a salt
VOOL keeps. The wallet's sealing key is mixed with that value (:mod:`core.wallet.pilot_custody`), so the sealed key
opens only with BOTH the PIN (or Touch ID) AND this exact key plugged in and touched. A copy of VOOL's files, the
PIN and even a second key of the same model open nothing.

This module only talks to keys. It never sees the wallet's key, signs nothing and moves nothing. It runs in the
owner's own VOOL process on this computer, reached only from the owner-local wallet doors.

Losing the key: the wallet's backup key (written down, in the Keychain or on a USB stick) still brings the wallet
back under a new PIN, and that removes the security key from it. Backups are never locked to the key, so a lost key
can never lock the person out of their money.
"""
from __future__ import annotations

import base64
import contextlib
import os
import threading
from collections.abc import Callable, Iterable
from typing import Any

from core.wallet.security import wallet_fault

AUTHORITY = "core.wallet.security_key"
RP_ID = "vool.local"
RP_NAME = "VOOL wallet"
ORIGIN = "https://vool.local"
SALT_BYTES = 32
TOUCH_TIMEOUT_SECONDS = 30.0

#: Where keys come from: the real USB keys by default; a test puts a software key here.
_device_source: Callable[[], Iterable[Any]] | None = None
_LOCK = threading.Lock()


class KeyProblem(Exception):
    """A plain reason the key could not answer: no_security_key, not_this_key, touch_timeout, key_needs_pin,
    key_unsupported, key_error."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def set_device_source_for_tests(source: Callable[[], Iterable[Any]] | None) -> None:
    global _device_source
    _device_source = source


def _fault(reason: str, *, code: str = "wallet_setup_state_invalid", source_context: dict[str, Any] | None = None, **context: Any) -> Exception:
    return wallet_fault(code, authority=AUTHORITY, context={"reason": reason, **context}, source_context=source_context)


def library_available() -> bool:
    try:
        import fido2  # noqa: F401
    except Exception:
        return False
    return True


def _devices() -> list[Any]:
    if _device_source is not None:
        return list(_device_source())
    try:
        from fido2.hid import CtapHidDevice
    except Exception:
        return []
    try:
        return list(CtapHidDevice.list_devices())
    except Exception:
        return []


def _name_of(device: Any) -> str:
    descriptor = getattr(device, "descriptor", None)
    name = str(getattr(descriptor, "product_name", "") or "").strip()
    return name[:60] or "Security key"


def plugged_in() -> list[dict[str, str]]:
    """The security keys plugged in now, by product name only."""
    return [{"name": _name_of(device)} for device in _devices()]


def _client(device: Any, *, key_pin: str = ""):
    from fido2.client import DefaultClientDataCollector, Fido2Client, UserInteraction
    from fido2.ctap2.extensions import HmacSecretExtension

    class _Interaction(UserInteraction):
        def request_pin(self, permissions, rp_id):  # the key's own FIDO PIN, only when the person typed one
            return key_pin or None

        def request_uv(self, permissions, rp_id):
            return True

    collector = DefaultClientDataCollector(ORIGIN, verify=lambda rp_id, origin: rp_id == RP_ID)
    return Fido2Client(device, collector, user_interaction=_Interaction(), extensions=[HmacSecretExtension(allow_hmac_secret=True)])


def _timer_event(seconds: float) -> tuple[threading.Event, threading.Timer]:
    event = threading.Event()
    timer = threading.Timer(seconds, event.set)
    timer.daemon = True
    timer.start()
    return event, timer


def _problem_from(exc: Exception, *, cancelled: bool) -> KeyProblem:
    from fido2.client import ClientError, PinRequiredError
    from fido2.ctap import CtapError

    if cancelled:
        return KeyProblem("touch_timeout")
    if isinstance(exc, PinRequiredError):
        return KeyProblem("key_needs_pin")
    if isinstance(exc, ClientError):
        cause = exc.cause
        if isinstance(cause, CtapError):
            code = cause.code
            if code == CtapError.ERR.NO_CREDENTIALS:
                return KeyProblem("not_this_key")
            if code in (CtapError.ERR.KEEPALIVE_CANCEL, CtapError.ERR.USER_ACTION_TIMEOUT, CtapError.ERR.ACTION_TIMEOUT):
                return KeyProblem("touch_timeout")
            if code in (CtapError.ERR.PIN_INVALID, CtapError.ERR.PIN_BLOCKED, CtapError.ERR.PIN_AUTH_BLOCKED, CtapError.ERR.PIN_REQUIRED,
                        CtapError.ERR.PUAT_REQUIRED):
                return KeyProblem("key_needs_pin")
            if code == CtapError.ERR.UNSUPPORTED_EXTENSION:
                return KeyProblem("key_unsupported")
        if exc.code == ClientError.ERR.CONFIGURATION_UNSUPPORTED:
            return KeyProblem("key_needs_pin" if "verification" in str(exc).lower() else "key_unsupported")
        if exc.code == ClientError.ERR.DEVICE_INELIGIBLE:
            return KeyProblem("not_this_key")
        if exc.code == ClientError.ERR.TIMEOUT:
            return KeyProblem("touch_timeout")
    return KeyProblem("key_error")


def _hmac_output(results: Any) -> bytes | None:
    data = dict(results or {})
    got = data.get("hmacGetSecret")
    if got is None:
        return None
    output = got.get("output1") if isinstance(got, dict) else getattr(got, "output1", None)
    if isinstance(output, str):
        padded = output + "=" * (-len(output) % 4)
        output = base64.urlsafe_b64decode(padded)
    return bytes(output) if output and len(output) == 32 else None


def _assert_with(device: Any, credential_id: bytes, salt: bytes, *, timeout: float) -> bytes:
    from fido2.webauthn import (
        PublicKeyCredentialDescriptor,
        PublicKeyCredentialRequestOptions,
        PublicKeyCredentialType,
        UserVerificationRequirement,
    )

    options = PublicKeyCredentialRequestOptions(
        challenge=os.urandom(32), rp_id=RP_ID, user_verification=UserVerificationRequirement.DISCOURAGED,
        allow_credentials=[PublicKeyCredentialDescriptor(type=PublicKeyCredentialType.PUBLIC_KEY, id=credential_id)],
        extensions={"hmacGetSecret": {"salt1": salt}},
    )
    event, timer = _timer_event(timeout)
    try:
        selection = _client(device).get_assertion(options, event=event)
        output = _hmac_output(selection.get_response(0).client_extension_results)
    except KeyProblem:
        raise
    except Exception as exc:
        raise _problem_from(exc, cancelled=event.is_set()) from None
    finally:
        timer.cancel()
    if output is None:
        raise KeyProblem("key_unsupported")
    return output


def secret(credential_id_b64: str, salt_b64: str, *, timeout: float = TOUCH_TIMEOUT_SECONDS) -> bytes:
    """The key's 32-byte answer for this wallet. Blocks until the person touches the key (or ``timeout``)."""
    credential_id = base64.b64decode(credential_id_b64)
    salt = base64.b64decode(salt_b64)
    with _LOCK:
        devices = _devices()
        if not devices:
            raise KeyProblem("no_security_key")
        last = KeyProblem("not_this_key")
        for device in devices:
            try:
                return _assert_with(device, credential_id, salt, timeout=timeout)
            except KeyProblem as problem:
                last = problem
                if problem.reason != "not_this_key":
                    raise
            finally:
                with contextlib.suppress(Exception):
                    device.close()
        raise last


def enroll(*, wallet_label: str, key_pin: str = "", timeout: float = TOUCH_TIMEOUT_SECONDS) -> dict[str, Any]:
    """Make a new credential on the ONE key plugged in (touch 1), then ask it for this wallet's answer (touch 2).
    Returns the credential id and salt to keep, the key's name, and the 32-byte answer to seal with."""
    from fido2.webauthn import (
        AuthenticatorSelectionCriteria,
        PublicKeyCredentialCreationOptions,
        PublicKeyCredentialParameters,
        PublicKeyCredentialRpEntity,
        PublicKeyCredentialType,
        PublicKeyCredentialUserEntity,
        ResidentKeyRequirement,
        UserVerificationRequirement,
    )

    with _LOCK:
        devices = _devices()
        if not devices:
            raise KeyProblem("no_security_key")
        if len(devices) > 1:
            for device in devices:
                with contextlib.suppress(Exception):
                    device.close()
            raise KeyProblem("more_than_one_key")
        device = devices[0]
        name = _name_of(device)
        try:
            options = PublicKeyCredentialCreationOptions(
                rp=PublicKeyCredentialRpEntity(id=RP_ID, name=RP_NAME),
                user=PublicKeyCredentialUserEntity(id=os.urandom(16), name=(str(wallet_label or "Wallet")[:40] or "Wallet")),
                challenge=os.urandom(32),
                pub_key_cred_params=[PublicKeyCredentialParameters(type=PublicKeyCredentialType.PUBLIC_KEY, alg=-7),
                                     PublicKeyCredentialParameters(type=PublicKeyCredentialType.PUBLIC_KEY, alg=-8)],
                authenticator_selection=AuthenticatorSelectionCriteria(resident_key=ResidentKeyRequirement.DISCOURAGED,
                                                                       user_verification=UserVerificationRequirement.DISCOURAGED),
                extensions={"hmacCreateSecret": True},
            )
            event, timer = _timer_event(timeout)
            try:
                made = _client(device, key_pin=str(key_pin or "")).make_credential(options, event=event)
            except Exception as exc:
                raise _problem_from(exc, cancelled=event.is_set()) from None
            finally:
                timer.cancel()
            extension_results = dict(made.client_extension_results or {})
            if extension_results.get("hmacCreateSecret") is not True:
                raise KeyProblem("key_unsupported")
            credential_data = made.response.attestation_object.auth_data.credential_data
            if credential_data is None:
                raise KeyProblem("key_error")
            credential_id = bytes(credential_data.credential_id)
            salt = os.urandom(SALT_BYTES)
            answer = _assert_with(device, credential_id, salt, timeout=timeout)
        finally:
            with contextlib.suppress(Exception):
                device.close()
    return {"credential_id": base64.b64encode(credential_id).decode("ascii"), "salt": base64.b64encode(salt).decode("ascii"), "name": name,
            "answer": answer}


__all__ = ["RP_ID", "KeyProblem", "enroll", "library_available", "plugged_in", "secret", "set_device_source_for_tests"]
