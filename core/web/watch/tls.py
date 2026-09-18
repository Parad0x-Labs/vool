from __future__ import annotations

import ssl
from urllib.parse import urlsplit, urlunsplit

from .config import BrainHiveWatchServerConfig


def normalize_base_url(url: str) -> str:
    parsed = urlsplit(str(url or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return str(url or "").rstrip("/")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), "", "", "")).rstrip("/")


def requires_public_tls(host: str) -> bool:
    return str(host or "").strip().lower() not in {"127.0.0.1", "localhost", "::1"}


def watch_tls_enabled(cfg: BrainHiveWatchServerConfig) -> bool:
    return bool(str(cfg.tls_certfile or "").strip() and str(cfg.tls_keyfile or "").strip())


def ssl_context_for_url(
    url: str,
    *,
    tls_ca_file: str | None = None,
    tls_insecure_skip_verify: bool = False,
) -> ssl.SSLContext | None:
    if not str(url).lower().startswith("https://"):
        return None
    if tls_insecure_skip_verify:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    if tls_ca_file:
        return ssl.create_default_context(cafile=str(tls_ca_file))
    return ssl.create_default_context()


def build_tls_context(cfg: BrainHiveWatchServerConfig) -> ssl.SSLContext | None:
    certfile = str(cfg.tls_certfile or "").strip()
    keyfile = str(cfg.tls_keyfile or "").strip()
    if not certfile and not keyfile:
        return None
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certfile=certfile, keyfile=keyfile)
    cafile = str(cfg.tls_ca_file or "").strip()
    if cafile:
        context.load_verify_locations(cafile=cafile)
    return context


def validate_tls_config(cfg: BrainHiveWatchServerConfig) -> None:
    certfile = str(cfg.tls_certfile or "").strip()
    keyfile = str(cfg.tls_keyfile or "").strip()
    if (certfile and not keyfile) or (keyfile and not certfile):
        raise ValueError("Both tls_certfile and tls_keyfile are required when Brain Hive watch TLS is enabled.")
