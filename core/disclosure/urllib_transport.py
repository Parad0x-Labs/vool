"""urllib-compatible provider I/O that consumes the reviewed bytes once.

This module exposes the small request/error surface used by legacy provider
helpers. It does not replace urllib globally or install a global opener.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from urllib import error as error
from urllib import request as _stdlib


class Request(_stdlib.Request):
    def __init__(self, url, data=None, *args, **kwargs):
        from core.disclosure.wire import WirePayload

        self.disclosure_payload = data if isinstance(data, WirePayload) else None
        if self.disclosure_payload is not None:
            data = self.disclosure_payload.wire_body
        elif isinstance(data, dict):
            from core.disclosure.wire import DisclosureRefused
            from core.provider_http import is_loopback_url
            if not is_loopback_url(url):
                raise DisclosureRefused("This helper has no sealed request. Nothing was sent.")
            data = json.dumps(data).encode("utf-8")
        super().__init__(url, data, *args, **kwargs)


def urlopen(req, *, timeout):
    from core.disclosure.wire import DisclosureRefused, note_sent
    from core.provider_http import urlopen as ordinary_urlopen

    wire = getattr(req, "disclosure_payload", None)
    if wire is None:
        data = getattr(req, "data", None)
        url = getattr(req,"full_url",req)
        from core.disclosure.direct import metadata_options
        options = metadata_options(url)
        if data and "proxies" not in options:
            raise DisclosureRefused("This helper has no sealed request. Nothing was sent.")
        return ordinary_urlopen(req, timeout=timeout, no_redirects=True, no_proxy="proxies" in options)
    # Validate at dispatch, after the caller has finished constructing Request.
    if req.data != wire.consume_wire(req.full_url):
        raise DisclosureRefused("The reviewed request changed. Nothing was sent.")
    req.add_header("Content-Type", "application/json")
    response = ordinary_urlopen(req, timeout=timeout, no_redirects=True, no_proxy=wire.cloud_destination is False)
    if wire.preview_id:
        note_sent(wire.preview_id)
    return response


request = SimpleNamespace(Request=Request, urlopen=urlopen)
