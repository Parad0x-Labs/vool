"""A credential or signature belongs only to the exact endpoint that was requested."""
from __future__ import annotations

import urllib.request

from core.registry.codec import RegistryError


class RedirectRefused(RegistryError):
    """Redirect responses are source failures, never instructions to forward credentials."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, service: str):
        self.service = service

    def http_error_302(self, req, fp, code, msg, headers):
        fp.close()
        raise RedirectRefused(self.service + " redirect refused")

    http_error_301 = http_error_302
    http_error_303 = http_error_302
    http_error_307 = http_error_302
    http_error_308 = http_error_302


def open_request(request, *, service: str, timeout: int):
    return urllib.request.build_opener(_NoRedirect(service)).open(request, timeout=timeout)
